"""KsefCoordinator, the DataUpdateCoordinator that runs one check per cycle.

Wires client → core → storage → notifier; see docs/ARCHITECTURE.md § Data flow and
§ Coordinator scheduling.

Scheduling is the coordinator's own single point-in-time timer, not `update_interval`: the
switch, the 429 back-off and a manual check all move that one timer. A check never raises
`UpdateFailed` — its result is the last-check sensor's `outcome`, and every entity stays
available through an outage.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta
from enum import StrEnum
from typing import TYPE_CHECKING

from homeassistant.core import CALLBACK_TYPE, HomeAssistant, callback
from homeassistant.exceptions import ServiceValidationError
from homeassistant.helpers import issue_registry as ir
from homeassistant.helpers.event import async_track_point_in_utc_time
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator
from homeassistant.util import dt as dt_util

from .client import (
    AuthErrorReason,
    KsefAuthError,
    KsefError,
    KsefInvoiceNotFoundError,
    KsefInvoiceNotReadyError,
    KsefMalformedResponseError,
    KsefRateLimitError,
    KsefTemporaryError,
    RateLimitGroup,
)
from .const import (
    CONF_CHECK_INTERVAL_MIN,
    CONF_FIELDS,
    CONF_QUIET_END,
    CONF_QUIET_START,
    DEFAULT_CHECK_INTERVAL_MIN,
    DEFAULT_FIELDS,
    DOMAIN,
    ISSUE_ACCOUNT_BLOCKED,
    MIN_QUERY_GAP,
    RETRY_AFTER_MARGIN,
)
from .core import quiet_hours, xml_parser
from .core.fields import needs_xml, ordered
from .core.formatter import is_combined
from .core.model import Invoice
from .core.tracker import (
    TrackerState,
    finish_baseline,
    finish_cycle,
    may_defer,
    plan_cycle,
    record_deferred,
    record_handled,
    select_new,
)
from .notifier import Notifier, issue_id

if TYPE_CHECKING:
    from homeassistant.config_entries import ConfigEntry

    from .client import KsefClient
    from .storage import TrackerStore

_LOGGER = logging.getLogger(__name__)


class Outcome(StrEnum):
    """The result of the most recent check — the last-check sensor's `outcome`."""

    OK = "ok"
    RATE_LIMITED = "rate_limited"
    UNAVAILABLE = "unavailable"
    AUTH_FAILED = "auth_failed"
    BLOCKED = "blocked"
    DISABLED = "disabled"


@dataclass(frozen=True)
class KsefData:
    """What the entities show. The one `Invoice` lives here, in memory only."""

    enabled: bool
    #: None until the first check of this session.
    outcome: Outcome | None
    last_success: datetime | None
    last_attempt: datetime | None
    next_check: datetime | None
    new_invoices: int
    metadata_requests_last_hour: int
    download_requests_last_hour: int
    last_invoice: Invoice | None


class KsefCoordinator(DataUpdateCoordinator[KsefData]):
    """One config entry's checks: when they run, and what one check does."""

    def __init__(
        self,
        hass: HomeAssistant,
        entry: ConfigEntry,
        client: KsefClient,
        store: TrackerStore,
        state: TrackerState,
    ) -> None:
        super().__init__(hass, _LOGGER, name=DOMAIN, config_entry=entry, update_interval=None)
        self.client = client
        self._store = store
        self._state = state
        self._interval = timedelta(
            minutes=int(entry.options.get(CONF_CHECK_INTERVAL_MIN, DEFAULT_CHECK_INTERVAL_MIN))
        )
        self._quiet = (
            quiet_hours.parse(entry.options[CONF_QUIET_START], entry.options[CONF_QUIET_END])
            if CONF_QUIET_START in entry.options and CONF_QUIET_END in entry.options
            else None
        )
        self.selection: tuple[str, ...] = ordered(entry.options.get(CONF_FIELDS, DEFAULT_FIELDS))
        self.notifier = Notifier(hass, entry, self.selection)

        # Starts off: the switch restores the real position, so a Home Assistant started
        # with notifications off makes no request at all.
        self._enabled = False
        #: Set by a refused token or a blocked account: no more checks until a reload.
        self._halted: Outcome | None = None
        self._outcome: Outcome | None = None
        self._last_attempt: datetime | None = None
        self._last_success: datetime | None = None
        self._last_query: datetime | None = None
        self._next_check: datetime | None = None
        self._new_invoices = 0
        self._new_invoices_found = 0
        self._last_invoice: Invoice | None = None
        self._unsub_timer: CALLBACK_TYPE | None = None
        self._task: asyncio.Task[None] | None = None

    # --- state for entities and diagnostics -------------------------------------------------

    @property
    def enabled(self) -> bool:
        return self._enabled

    @property
    def tracker_state(self) -> TrackerState:
        return self._state

    @property
    def halted(self) -> Outcome | None:
        return self._halted

    async def _async_update_data(self) -> KsefData:
        """Only the first refresh comes through here, and it makes no request."""
        return self._snapshot()

    def _snapshot(self) -> KsefData:
        return KsefData(
            enabled=self._enabled,
            outcome=self._outcome if self._enabled else Outcome.DISABLED,
            last_success=self._last_success,
            last_attempt=self._last_attempt,
            next_check=self._next_check,
            new_invoices=self._new_invoices,
            metadata_requests_last_hour=self.client.requests_last_hour(RateLimitGroup.METADATA),
            download_requests_last_hour=self.client.requests_last_hour(RateLimitGroup.DOWNLOAD),
            last_invoice=self._last_invoice,
        )

    @callback
    def _publish(self) -> None:
        self.async_set_updated_data(self._snapshot())

    # --- control ----------------------------------------------------------------------------

    async def async_set_enabled(self, enabled: bool) -> None:
        """The switch. Off: no timer, no request, and the cursor is cleared, so turning it
        on again starts with a baseline instead of delivering what arrived meanwhile."""
        if enabled:
            if self._enabled:
                return
            self._enabled = True
            if self._halted is None:
                now = dt_util.utcnow()
                when = self._outside_quiet_hours(self._earliest_manual_check() or now)
                if when > now:
                    self._arm_timer(when)
                else:
                    self._start_check()
            self._publish()
            return

        self._enabled = False
        self._cancel_timer()
        await self._async_cancel_check()
        if self._state.to_dict() != TrackerState().to_dict():
            self._state = TrackerState()
            await self._store.async_save(self._state)
        self._publish()

    async def async_check_now(self) -> None:
        """The Check now button: refused with a reason, otherwise run and awaited."""
        if not self._enabled:
            raise ServiceValidationError(translation_domain=DOMAIN, translation_key="disabled")
        if self._halted is not None:
            raise ServiceValidationError(translation_domain=DOMAIN, translation_key="halted")
        if earliest := self._earliest_manual_check():
            raise ServiceValidationError(
                translation_domain=DOMAIN,
                translation_key="too_soon",
                translation_placeholders={"time": dt_util.as_local(earliest).strftime("%H:%M")},
            )
        if self._running():
            return
        self._start_check()
        if self._task is not None:
            await asyncio.wait({self._task})

    async def async_request_refresh(self) -> None:
        """`homeassistant.update_entity` on any entity: a check, within MIN_QUERY_GAP."""
        if (
            not self._enabled
            or self._halted is not None
            or self._running()
            or self._earliest_manual_check()
        ):
            _LOGGER.debug("Update request ignored: checks off, stopped, running or too soon")
            return
        self._start_check()

    async def async_shutdown(self) -> None:
        self._cancel_timer()
        await self._async_cancel_check()
        await super().async_shutdown()

    def _earliest_manual_check(self) -> datetime | None:
        """When a check not started by the timer is allowed again, if not now."""
        if self._last_query is None:
            return None
        earliest = self._last_query + MIN_QUERY_GAP
        return earliest if dt_util.utcnow() < earliest else None

    # --- scheduling -------------------------------------------------------------------------

    def _outside_quiet_hours(self, when: datetime) -> datetime:
        """`when`, or the end of the quiet hours it falls in. Only scheduled checks wait;
        Check now and `update_entity` are asked for and run at any hour."""
        if self._quiet is None:
            return when
        return self._quiet.postpone(when, dt_util.get_default_time_zone())

    def _running(self) -> bool:
        return self._task is not None and not self._task.done()

    @callback
    def _start_check(self) -> None:
        """Run a check in the background, unless one is running: checks never overlap."""
        # While Home Assistant stops, the session is being revoked; a check would log in again.
        if self._running() or self.hass.is_stopping:
            return
        self._task = self.config_entry.async_create_background_task(
            self.hass, self._async_check(), f"{DOMAIN} check"
        )

    async def _async_cancel_check(self) -> None:
        task, self._task = self._task, None
        if task is None or task.done():
            return
        task.cancel()
        await asyncio.wait({task})

    @callback
    def _arm_timer(self, when: datetime) -> None:
        self._cancel_timer()
        if self.config_entry.pref_disable_polling:
            self._next_check = None
            return
        self._next_check = when
        self._unsub_timer = async_track_point_in_utc_time(self.hass, self._on_timer, when)

    @callback
    def _cancel_timer(self) -> None:
        self._next_check = None
        if self._unsub_timer is not None:
            self._unsub_timer()
            self._unsub_timer = None

    @callback
    def _on_timer(self, _now: datetime) -> None:
        self._unsub_timer = None
        # The time zone may have changed since the timer was armed.
        now = dt_util.utcnow()
        if (when := self._outside_quiet_hours(now)) > now:
            self._arm_timer(when)
            self._publish()
            return
        self._start_check()

    # --- one check --------------------------------------------------------------------------

    async def _async_check(self) -> None:
        """One check; whatever happens is recorded as its outcome, then the next is armed."""
        self._cancel_timer()
        now = dt_util.utcnow()
        self._last_attempt = now
        delay = self._interval
        self._new_invoices_found = 0
        try:
            await self._async_cycle(now)
        except KsefAuthError as err:
            self._fail_auth(err)
        except KsefRateLimitError as err:
            self._outcome = Outcome.RATE_LIMITED
            delay = max(self._interval, timedelta(seconds=err.retry_after_s) + RETRY_AFTER_MARGIN)
        except KsefError as err:
            if self._outcome is not Outcome.UNAVAILABLE:
                _LOGGER.warning("KSeF check failed: %s", err)
            self._outcome = Outcome.UNAVAILABLE
        except Exception:
            _LOGGER.exception("Unexpected error during the KSeF check")
            self._outcome = Outcome.UNAVAILABLE
        else:
            if self._outcome not in (None, Outcome.OK):
                _LOGGER.info("KSeF check succeeded again")
            self._outcome = Outcome.OK
            self._last_success = now
            self._new_invoices = self._new_invoices_found
            ir.async_delete_issue(
                self.hass, DOMAIN, issue_id(ISSUE_ACCOUNT_BLOCKED, self.config_entry.entry_id)
            )
        if self._enabled and self._halted is None:
            self._arm_timer(self._outside_quiet_hours(dt_util.utcnow() + delay))
        self._publish()

    def _fail_auth(self, err: KsefAuthError) -> None:
        if err.reason is AuthErrorReason.BLOCKED:
            _LOGGER.error("KSeF refuses access for this company (%s); checks stopped", err)
            self._outcome = self._halted = Outcome.BLOCKED
            ir.async_create_issue(
                self.hass,
                DOMAIN,
                issue_id(ISSUE_ACCOUNT_BLOCKED, self.config_entry.entry_id),
                is_fixable=False,
                severity=ir.IssueSeverity.ERROR,
                translation_key=ISSUE_ACCOUNT_BLOCKED,
                translation_placeholders={"entry_title": self.config_entry.title},
            )
            return
        _LOGGER.warning("KSeF refused the KSeF token (%s); re-authentication needed", err)
        self._outcome = self._halted = Outcome.AUTH_FAILED
        self.config_entry.async_start_reauth(self.hass)

    async def _async_cycle(self, now: datetime) -> None:
        """The data flow of docs/ARCHITECTURE.md: query, select, notify, record, advance."""
        plan = plan_cycle(self._state, now)
        if plan.catch_up_expired:
            _LOGGER.warning("Last check is older than KSeF's date range allows; starting afresh")
        self._last_query = now
        result = await self.client.query_metadata(plan.date_from)
        invoices = [Invoice.from_metadata(record) for record in result.invoices]

        if plan.baseline:
            self._state = finish_baseline(invoices, result.hwm, now)
            await self._store.async_save(self._state)
            return

        persisted = self._state.to_dict()
        new = select_new(self._state, invoices)
        if is_combined(len(new)):
            await self._async_notify_combined(new)
        else:
            for invoice in new:
                await self._async_notify_one(invoice)

        self._state, dropped = finish_cycle(self._state, invoices, result.hwm, result.complete, now)
        if dropped:
            _LOGGER.warning("Invoice state over its cap; %d oldest entries dropped", dropped)
        if self._state.to_dict() != persisted:
            await self._store.async_save(self._state)

    async def _async_notify_one(self, invoice: Invoice) -> None:
        enriched = await self._async_with_details(invoice)
        if enriched is None:
            return
        notified = await self.notifier.async_send_invoice(enriched)
        self.notifier.fire_event(enriched, combined=False, notified=notified)
        await self._async_handled(enriched)

    async def _async_notify_combined(self, invoices: Sequence[Invoice]) -> None:
        """Metadata only: a combined check downloads nothing."""
        if needs_xml(self.selection):
            invoices = [invoice.without_details() for invoice in invoices]
        notified = await self.notifier.async_send_combined(invoices)
        for invoice in invoices:
            self.notifier.fire_event(invoice, combined=True, notified=notified)
            self._state = record_handled(self._state, invoice)
        await self._store.async_save(self._state)
        self._last_invoice = invoices[-1]
        self._new_invoices_found += len(invoices)

    async def _async_handled(self, invoice: Invoice) -> None:
        """Never again: saved before the next push."""
        self._state = record_handled(self._state, invoice)
        await self._store.async_save(self._state)
        self._last_invoice = invoice
        self._new_invoices_found += 1

    async def _async_with_details(self, invoice: Invoice) -> Invoice | None:  # noqa: PLR0911
        """The invoice with its XML fields when the selection needs them; None = deferred."""
        if not needs_xml(self.selection):
            return invoice
        if not invoice.xml_supported:
            return invoice.without_details()
        try:
            body = await self.client.fetch_invoice_xml(invoice.ksef_number)
        except (KsefInvoiceNotReadyError, KsefTemporaryError, KsefRateLimitError) as err:
            if may_defer(self._state, invoice):
                _LOGGER.debug("Invoice XML not available yet (%s); deferred", type(err).__name__)
                self._state = record_deferred(self._state, invoice)
                return None
            _LOGGER.info("Invoice XML still not available; notifying without its fields")
            return invoice.without_details()
        except (KsefInvoiceNotFoundError, KsefMalformedResponseError) as err:
            _LOGGER.info("Invoice XML unusable (%s); notifying without its fields", err)
            return invoice.without_details()
        try:
            details = await self.hass.async_add_executor_job(xml_parser.parse, body)
        except xml_parser.InvoiceXmlError as err:
            _LOGGER.info("Invoice XML unreadable (%s); notifying without its fields", err)
            return invoice.without_details()
        return invoice.with_details(details)
