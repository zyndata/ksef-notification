"""The push via notify.mobile_app_* and the ksef_notification_invoice event.

Not `notify.py`: a module named after a platform is that platform to Home Assistant.
Contracts in docs/CONFIG.md § Notification and § Event payload.
"""

from __future__ import annotations

import logging
from collections.abc import Sequence
from typing import Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import Platform
from homeassistant.core import HomeAssistant
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers import issue_registry as ir
from homeassistant.helpers.translation import async_get_translations

from .const import (
    CLICK_ENTITY_PREFIX,
    CONF_ENVIRONMENT,
    CONF_NOTIFY_SERVICE,
    DOMAIN,
    EVENT_INVOICE,
    ISSUE_NOTIFY_SERVICE_MISSING,
    KEY_LAST_INVOICE,
    NOTIFICATION_CHANNEL,
    NOTIFICATION_GROUP,
    NOTIFICATION_TAG_PREFIX,
    NOTIFY_DOMAIN,
)
from .core.formatter import Message, field_values, format_combined, format_invoice, render
from .core.model import Invoice
from .core.tracker import ksef_hash

_LOGGER = logging.getLogger(__name__)

_CATEGORY = "common"
_FALLBACK_LANGUAGE = "en"


def issue_id(key: str, entry_id: str) -> str:
    return f"{key}_{entry_id}"


class Notifier:
    """Sends one entry's notifications and fires its events."""

    def __init__(self, hass: HomeAssistant, entry: ConfigEntry, selection: Sequence[str]) -> None:
        self._hass = hass
        self._entry = entry
        self._service: str = entry.options[CONF_NOTIFY_SERVICE]
        self._selection = tuple(selection)

    async def async_send_invoice(self, invoice: Invoice) -> bool:
        """One invoice; True when the push went out."""
        language = self._hass.config.language
        message = format_invoice(invoice, self._selection, language)
        tag = f"{NOTIFICATION_TAG_PREFIX}{ksef_hash(invoice.ksef_number)}"
        return await self._async_push(message, tag)

    async def async_send_combined(self, invoices: Sequence[Invoice]) -> bool:
        """Several invoices in one message; True when the push went out."""
        message = format_combined(invoices, self._hass.config.language)
        tag = f"{NOTIFICATION_TAG_PREFIX}combined_{self._entry.entry_id}"
        return await self._async_push(message, tag)

    def fire_event(self, invoice: Invoice, *, combined: bool, notified: bool) -> None:
        """`ksef_notification_invoice`: the selected fields only, after the push attempt."""
        acquired = invoice.acquisition_date
        self._hass.bus.async_fire(
            EVENT_INVOICE,
            {
                "entry_id": self._entry.entry_id,
                "environment": self._entry.data[CONF_ENVIRONMENT],
                "ksef_number": invoice.ksef_number,
                "acquisition_date": acquired.isoformat() if acquired else None,
                "combined": combined,
                "notified": notified,
                "details": invoice.details_status.value,
                "fields": field_values(invoice, self._selection),
            },
        )

    async def _async_push(self, message: Message, tag: str) -> bool:
        strings = await self._async_strings()
        title, body = render(message, strings, self._entry_title_suffix())
        data: dict[str, Any] = {
            "tag": tag,
            "group": NOTIFICATION_GROUP,
            "channel": NOTIFICATION_CHANNEL,
        }
        if entity_id := self._last_invoice_entity_id():
            # The two companion apps spell the same thing differently.
            data["clickAction"] = data["url"] = f"{CLICK_ENTITY_PREFIX}{entity_id}"

        if not self._hass.services.has_service(NOTIFY_DOMAIN, self._service):
            _LOGGER.warning("%s.%s is not registered; no push sent", NOTIFY_DOMAIN, self._service)
            self._raise_issue()
            return False
        try:
            await self._hass.services.async_call(
                NOTIFY_DOMAIN,
                self._service,
                {"title": title, "message": body, "data": data},
                blocking=True,
            )
        except Exception as err:
            # The message of a foreign exception may quote the notification; its type is enough.
            _LOGGER.warning(
                "%s.%s failed (%s); no push sent", NOTIFY_DOMAIN, self._service, type(err).__name__
            )
            self._raise_issue()
            return False
        ir.async_delete_issue(
            self._hass, DOMAIN, issue_id(ISSUE_NOTIFY_SERVICE_MISSING, self._entry.entry_id)
        )
        return True

    def _raise_issue(self) -> None:
        ir.async_create_issue(
            self._hass,
            DOMAIN,
            issue_id(ISSUE_NOTIFY_SERVICE_MISSING, self._entry.entry_id),
            is_fixable=False,
            severity=ir.IssueSeverity.WARNING,
            translation_key=ISSUE_NOTIFY_SERVICE_MISSING,
            translation_placeholders={
                "service": f"{NOTIFY_DOMAIN}.{self._service}",
                "entry_title": self._entry.title,
            },
        )

    def _entry_title_suffix(self) -> str | None:
        """Only when another entry exists does the title need telling apart."""
        if len(self._hass.config_entries.async_entries(DOMAIN)) > 1:
            return self._entry.title
        return None

    def _last_invoice_entity_id(self) -> str | None:
        """Looked up each time: the user may rename the entity."""
        return er.async_get(self._hass).async_get_entity_id(
            Platform.SENSOR, DOMAIN, f"{self._entry.entry_id}_{KEY_LAST_INVOICE}"
        )

    async def _async_strings(self) -> dict[str, str]:
        """The `common` texts in Home Assistant's language, English where one is missing."""
        prefix = f"component.{DOMAIN}.{_CATEGORY}."
        strings: dict[str, str] = {}
        for language in dict.fromkeys((_FALLBACK_LANGUAGE, self._hass.config.language)):
            texts = await async_get_translations(self._hass, language, _CATEGORY, {DOMAIN})
            strings.update(
                (key.removeprefix(prefix), text)
                for key, text in texts.items()
                if key.startswith(prefix)
            )
        return strings
