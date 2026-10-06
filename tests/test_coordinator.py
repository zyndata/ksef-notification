"""The integration end to end: coordinator, entities, notification, event, store, diagnostics.

Home Assistant runs the real integration against the scripted KSeF (`World` serves the
metadata route); time is frozen and moved by the tests, so every request is counted. These
are the contracts of docs/ARCHITECTURE.md and docs/CONFIG.md.
"""

from __future__ import annotations

import json
import re
from datetime import timedelta

import pytest
from freezegun.api import FrozenDateTimeFactory
from homeassistant.config_entries import ConfigEntryDisabler, ConfigEntryState
from homeassistant.const import STATE_OFF, STATE_ON, STATE_UNKNOWN
from homeassistant.core import HomeAssistant, ServiceCall, State
from homeassistant.exceptions import HomeAssistantError, ServiceValidationError
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers import issue_registry as ir
from homeassistant.setup import async_setup_component
from homeassistant.util import dt as dt_util
from pytest_homeassistant_custom_component.common import (
    MockConfigEntry,
    async_mock_service,
    mock_restore_cache,
)

from custom_components.ksef_notification.const import (
    CONF_FIELDS,
    DOMAIN,
    ISSUE_ACCOUNT_BLOCKED,
    ISSUE_NOTIFY_SERVICE_MISSING,
    KEY_CHECK_NOW,
    KEY_LAST_CHECK,
    KEY_LAST_INVOICE,
    KEY_NOTIFICATIONS,
)
from custom_components.ksef_notification.core.tracker import ksef_hash
from custom_components.ksef_notification.diagnostics import async_get_config_entry_diagnostics

from .ha_setup import (
    INTERVAL,
    PHONE,
    TITLE,
    World,
    advance,
    entity_id,
    last_check,
    make_entry,
    number,
    press,
    reauth_flows,
    settle,
    setup,
    state,
    storage_key,
    stored,
)
from .ksef_fake import (
    AUTH_ROUTES,
    FIXTURES,
    KSEF_TOKEN,
    NIP,
    START,
    FakeKsef,
    Reply,
    error_reply,
    fixture,
    status_reply,
)

SELLER = "Fikcyjny Dostawca B"
INVOICE_NUMBER = "FV/2026/10/0042"
BODY = f"{SELLER}\nInvoice number: {INVOICE_NUMBER}\nGross: 123.00 PLN\nDue date: 2026-10-20"
BODY_NO_XML = f"{SELLER}\nInvoice number: {INVOICE_NUMBER}\nGross: 123.00 PLN\nDue date: —"
XML_OK = Reply(body=(FIXTURES / "invoice_fa3.xml").read_bytes())
#: A check's window, written as the client writes it.
FROM_FORMAT = "microseconds"


def _messages(pushes: list[ServiceCall]) -> list[str]:
    return [call.data["message"] for call in pushes]


async def _switch(hass: HomeAssistant, entry: MockConfigEntry, on: bool) -> None:
    await hass.services.async_call(
        "switch",
        "turn_on" if on else "turn_off",
        {"entity_id": entity_id(hass, entry, "switch", KEY_NOTIFICATIONS)},
        blocking=True,
    )
    await settle(hass)


def _restore_switch(hass: HomeAssistant, entry: MockConfigEntry, position: str) -> None:
    """Register the switch under a known id with a last state, as after a restart."""
    entry.add_to_hass(hass)
    switch = er.async_get(hass).async_get_or_create(
        "switch", DOMAIN, f"{entry.entry_id}_{KEY_NOTIFICATIONS}", config_entry=entry
    )
    mock_restore_cache(hass, [State(switch.entity_id, position)])


# --- setup and the first check ----------------------------------------------------------------


async def test_setup_creates_the_entities_and_takes_a_baseline(
    hass: HomeAssistant,
    ha_ksef: FakeKsef,
    world: World,
    pushes: list,
    events: list,
    hass_storage: dict,
) -> None:
    """The first check takes note of what is already there and notifies nothing."""
    world.add(1, ago=timedelta(minutes=30))
    entry = await setup(hass)

    assert entry.state is ConfigEntryState.LOADED
    assert state(hass, entry, "switch", KEY_NOTIFICATIONS).state == STATE_ON
    assert state(hass, entry, "sensor", KEY_LAST_INVOICE).state == STATE_UNKNOWN
    assert state(hass, entry, "button", KEY_CHECK_NOW)
    check = state(hass, entry, "sensor", KEY_LAST_CHECK)
    assert dt_util.parse_datetime(check.state) == START
    assert check.attributes["outcome"] == "ok"
    assert check.attributes["new_invoices"] == 0
    assert check.attributes["last_attempt"] == START.isoformat()
    assert check.attributes["next_check"] == (START + INTERVAL).isoformat()
    assert check.attributes["metadata_requests_last_hour"] == 1
    assert check.attributes["download_requests_last_hour"] == 0

    assert ha_ksef.routes() == [*AUTH_ROUTES, "metadata"]
    assert ha_ksef.last("metadata").body["dateRange"]["from"] == (
        START - timedelta(hours=2)
    ).isoformat(timespec=FROM_FORMAT)
    assert pushes == []
    assert events == []
    assert stored(hass_storage, entry) == {
        "cursor": (START - timedelta(minutes=2)).isoformat(),
        "seen": {},
    }


async def test_the_device_is_named_after_the_entry(hass: HomeAssistant, world: World) -> None:
    entry = await setup(hass)

    switch = er.async_get(hass).async_get(entity_id(hass, entry, "switch", KEY_NOTIFICATIONS))
    assert switch is not None
    assert switch.device_id is not None
    device = dr.async_get(hass).async_get(switch.device_id)
    assert device is not None
    assert device.name == TITLE
    assert device.entry_type is dr.DeviceEntryType.SERVICE


# --- a new invoice ----------------------------------------------------------------------------


async def test_a_new_invoice_is_notified_with_the_selected_fields(
    hass: HomeAssistant,
    freezer: FrozenDateTimeFactory,
    ha_ksef: FakeKsef,
    world: World,
    pushes: list,
    events: list,
) -> None:
    entry = await setup(hass)
    freezer.tick(timedelta(minutes=14))
    world.add(2, ago=timedelta(0))
    await advance(hass, freezer, timedelta(minutes=1))

    assert ha_ksef.last("metadata").body["dateRange"]["from"] == (
        START - timedelta(minutes=3)
    ).isoformat(timespec=FROM_FORMAT)
    assert ha_ksef.count("xml") == 1
    sensor_id = entity_id(hass, entry, "sensor", KEY_LAST_INVOICE)
    assert [call.data for call in pushes] == [
        {
            "title": "New cost invoice",
            "message": BODY,
            "data": {
                "tag": f"ksef_{ksef_hash(number(2))}",
                "group": "ksef_notification",
                "channel": "KSeF",
                "clickAction": f"entityId:{sensor_id}",
                "url": f"entityId:{sensor_id}",
            },
        }
    ]
    fields = {
        "seller_name": SELLER,
        "invoice_number": INVOICE_NUMBER,
        "gross_amount": 123.0,
        "due_date": "2026-10-20",
        "currency": "PLN",
    }
    assert [event.data for event in events] == [
        {
            "entry_id": entry.entry_id,
            "environment": "test",
            "ksef_number": number(2),
            "acquisition_date": "2026-10-06T07:40:02.200000+00:00",
            "combined": False,
            "notified": True,
            "details": "fetched",
            "fields": fields,
        }
    ]
    last = state(hass, entry, "sensor", KEY_LAST_INVOICE)
    assert dt_util.parse_datetime(last.state) == dt_util.parse_datetime("2026-10-06T07:40:02+00:00")
    assert {key: last.attributes[key] for key in fields} == fields
    assert last.attributes["ksef_number"] == number(2)
    assert last_check(hass, entry)["new_invoices"] == 1
    assert last_check(hass, entry)["download_requests_last_hour"] == 1


async def test_metadata_only_selection_downloads_nothing(
    hass: HomeAssistant,
    freezer: FrozenDateTimeFactory,
    ha_ksef: FakeKsef,
    world: World,
    pushes: list,
    events: list,
) -> None:
    await setup(hass, make_entry({CONF_FIELDS: ["seller_name", "gross_amount"]}))
    world.add(2)
    await advance(hass, freezer, INTERVAL)

    assert ha_ksef.count("xml") == 0
    assert _messages(pushes) == [f"{SELLER}\nGross: 123.00 PLN"]
    assert events[0].data["details"] == "not_needed"
    assert events[0].data["fields"] == {
        "seller_name": SELLER,
        "gross_amount": 123.0,
        "currency": "PLN",
    }


async def test_a_pef_invoice_is_never_downloaded(
    hass: HomeAssistant,
    freezer: FrozenDateTimeFactory,
    ha_ksef: FakeKsef,
    world: World,
    pushes: list,
    events: list,
) -> None:
    await setup(hass)
    world.add(2, formCode={"systemCode": "PEF (3)", "schemaVersion": "2-1", "value": "FA_PEF"})
    await advance(hass, freezer, INTERVAL)

    assert ha_ksef.count("xml") == 0
    assert _messages(pushes) == [BODY_NO_XML]
    assert events[0].data["details"] == "unavailable"
    assert events[0].data["fields"]["due_date"] is None


async def test_three_invoices_are_notified_one_by_one_oldest_first(
    hass: HomeAssistant,
    freezer: FrozenDateTimeFactory,
    ha_ksef: FakeKsef,
    world: World,
    pushes: list,
    events: list,
) -> None:
    await setup(hass)
    for n, ago in ((3, 1), (2, 3), (4, 2)):
        world.add(n, ago=timedelta(minutes=ago))
    freezer.tick(timedelta(minutes=10))
    await advance(hass, freezer, timedelta(minutes=5))

    assert ha_ksef.count("xml") == 3
    assert len(pushes) == 3
    assert [event.data["ksef_number"] for event in events] == [number(2), number(4), number(3)]
    assert [call.data["data"]["tag"] for call in pushes] == [
        f"ksef_{ksef_hash(number(n))}" for n in (2, 4, 3)
    ]


async def test_four_invoices_make_one_combined_notification_without_downloads(
    hass: HomeAssistant,
    freezer: FrozenDateTimeFactory,
    ha_ksef: FakeKsef,
    world: World,
    pushes: list,
    events: list,
) -> None:
    entry = await setup(hass)
    for n in range(2, 6):
        world.add(n, ago=timedelta(seconds=60 - n))
    await advance(hass, freezer, INTERVAL)

    assert ha_ksef.count("xml") == 0
    assert len(pushes) == 1
    assert pushes[0].data["title"] == "4 new cost invoices"
    assert pushes[0].data["message"] == "\n".join([f"{SELLER} — 123.00 PLN"] * 4)
    assert pushes[0].data["data"]["tag"] == f"ksef_combined_{entry.entry_id}"
    assert [event.data["combined"] for event in events] == [True] * 4
    assert {event.data["details"] for event in events} == {"unavailable"}
    assert last_check(hass, entry)["new_invoices"] == 4
    assert state(hass, entry, "sensor", KEY_LAST_INVOICE).attributes["ksef_number"] == number(5)


async def test_nothing_is_notified_twice_across_checks(
    hass: HomeAssistant, freezer: FrozenDateTimeFactory, world: World, pushes: list, events: list
) -> None:
    await setup(hass)
    world.add(2, ago=timedelta(seconds=10))
    for _ in range(4):
        await advance(hass, freezer, INTERVAL)

    assert len(pushes) == 1
    assert len(events) == 1


async def test_an_invoice_arriving_late_behind_the_hwm_is_still_notified(
    hass: HomeAssistant, freezer: FrozenDateTimeFactory, world: World, pushes: list
) -> None:
    """Stored before the previous check's HWM but visible only now: inside the overlap."""
    await setup(hass)
    await advance(hass, freezer, INTERVAL)
    world.add(2, ago=timedelta(minutes=2, seconds=30))  # 30 s before the HWM of that check
    await advance(hass, freezer, INTERVAL)

    assert _messages(pushes) == [BODY]


# --- restarts ---------------------------------------------------------------------------------


async def test_no_duplicate_after_a_restart(
    hass: HomeAssistant,
    freezer: FrozenDateTimeFactory,
    ha_ksef: FakeKsef,
    world: World,
    pushes: list,
) -> None:
    entry = await setup(hass)
    world.add(2)
    await advance(hass, freezer, INTERVAL)
    assert len(pushes) == 1

    freezer.tick(timedelta(minutes=1))
    assert await hass.config_entries.async_reload(entry.entry_id)
    await settle(hass)

    assert ha_ksef.count("revoke") == 1
    assert ha_ksef.routes()[-1] == "metadata"  # the reloaded entry checked at once…
    assert len(pushes) == 1  # …and recognised the invoice


async def test_invoices_that_arrived_while_home_assistant_was_down_are_notified(
    hass: HomeAssistant,
    hass_storage: dict,
    ha_ksef: FakeKsef,
    world: World,
    pushes: list,
) -> None:
    """A restart with notifications on is not a baseline: it catches up from the cursor."""
    entry = make_entry()
    _restore_switch(hass, entry, STATE_ON)
    hass_storage[storage_key(entry)] = {
        "version": 1,
        "minor_version": 1,
        "key": storage_key(entry),
        "data": {"cursor": (START - timedelta(hours=3)).isoformat(), "seen": {}},
    }
    world.add(2, ago=timedelta(hours=2))
    await setup(hass, entry)

    assert ha_ksef.last("metadata").body["dateRange"]["from"] == (
        START - timedelta(hours=3, minutes=1)
    ).isoformat(timespec=FROM_FORMAT)
    assert _messages(pushes) == [BODY]


async def test_a_restart_with_notifications_off_makes_no_request(
    hass: HomeAssistant, freezer: FrozenDateTimeFactory, ha_ksef: FakeKsef, world: World
) -> None:
    entry = make_entry()
    _restore_switch(hass, entry, STATE_OFF)
    await setup(hass, entry)
    await advance(hass, freezer, timedelta(hours=2))

    assert state(hass, entry, "switch", KEY_NOTIFICATIONS).state == STATE_OFF
    assert ha_ksef.calls == []
    assert last_check(hass, entry)["outcome"] == "disabled"
    assert last_check(hass, entry)["next_check"] is None


# --- the switch -------------------------------------------------------------------------------


async def test_switch_off_stops_every_request_and_on_takes_a_fresh_baseline(
    hass: HomeAssistant,
    freezer: FrozenDateTimeFactory,
    ha_ksef: FakeKsef,
    world: World,
    pushes: list,
    hass_storage: dict,
) -> None:
    entry = await setup(hass)
    await _switch(hass, entry, on=False)
    requests = len(ha_ksef.calls)

    world.add(2)
    for _ in range(12):
        await advance(hass, freezer, INTERVAL)

    assert len(ha_ksef.calls) == requests
    assert stored(hass_storage, entry) == {"cursor": None, "seen": {}}
    assert last_check(hass, entry)["outcome"] == "disabled"

    world.add(3)
    await _switch(hass, entry, on=True)
    assert ha_ksef.routes()[-1] == "metadata"
    assert pushes == []  # what arrived while off is not delivered

    world.add(4)
    await advance(hass, freezer, INTERVAL)
    assert [call.data["data"]["tag"] for call in pushes] == [f"ksef_{ksef_hash(number(4))}"]


async def test_switch_on_soon_after_a_check_waits_for_the_gap(
    hass: HomeAssistant, freezer: FrozenDateTimeFactory, ha_ksef: FakeKsef, world: World
) -> None:
    """Off and on again must not be a way around MIN_QUERY_GAP."""
    entry = await setup(hass)
    freezer.tick(timedelta(minutes=1))
    await _switch(hass, entry, on=False)
    await _switch(hass, entry, on=True)

    assert ha_ksef.count("metadata") == 1
    assert last_check(hass, entry)["next_check"] == (START + timedelta(minutes=10)).isoformat()
    await advance(hass, freezer, timedelta(minutes=9))
    assert ha_ksef.count("metadata") == 2


# --- check now and update_entity --------------------------------------------------------------


async def test_check_now_is_refused_within_ten_minutes_and_resets_the_timer(
    hass: HomeAssistant, freezer: FrozenDateTimeFactory, ha_ksef: FakeKsef, world: World
) -> None:
    entry = await setup(hass)
    freezer.tick(timedelta(minutes=5))

    with pytest.raises(ServiceValidationError) as refused:
        await press(hass, entry)
    assert refused.value.translation_key == "too_soon"
    assert refused.value.translation_placeholders == {
        "time": dt_util.as_local(START + timedelta(minutes=10)).strftime("%H:%M")
    }
    assert ha_ksef.count("metadata") == 1

    freezer.tick(timedelta(minutes=5))
    await press(hass, entry)
    assert ha_ksef.count("metadata") == 2
    assert last_check(hass, entry)["next_check"] == (START + timedelta(minutes=25)).isoformat()

    await advance(hass, freezer, timedelta(minutes=5))  # the old 15-minute mark
    assert ha_ksef.count("metadata") == 2


async def test_check_now_is_refused_while_notifications_are_off(
    hass: HomeAssistant, freezer: FrozenDateTimeFactory, ha_ksef: FakeKsef, world: World
) -> None:
    entry = await setup(hass)
    await _switch(hass, entry, on=False)
    freezer.tick(timedelta(hours=1))

    with pytest.raises(ServiceValidationError) as refused:
        await press(hass, entry)
    assert refused.value.translation_key == "disabled"
    assert ha_ksef.count("metadata") == 1


async def test_update_entity_is_throttled_like_the_button(
    hass: HomeAssistant, freezer: FrozenDateTimeFactory, ha_ksef: FakeKsef, world: World
) -> None:
    assert await async_setup_component(hass, "homeassistant", {})
    entry = await setup(hass)
    target = {"entity_id": entity_id(hass, entry, "sensor", KEY_LAST_CHECK)}

    freezer.tick(timedelta(minutes=5))
    await hass.services.async_call("homeassistant", "update_entity", target, blocking=True)
    await settle(hass)
    assert ha_ksef.count("metadata") == 1

    freezer.tick(timedelta(minutes=6))
    await hass.services.async_call("homeassistant", "update_entity", target, blocking=True)
    await settle(hass)
    assert ha_ksef.count("metadata") == 2


# --- failures ---------------------------------------------------------------------------------


async def test_rate_limit_moves_the_next_check_past_retry_after(
    hass: HomeAssistant, freezer: FrozenDateTimeFactory, ha_ksef: FakeKsef, world: World
) -> None:
    entry = await setup(hass)
    ha_ksef.script(
        "metadata",
        Reply(status=429, json=fixture("error_429.json"), headers={"Retry-After": "3600"}),
        world.reply,
    )
    await advance(hass, freezer, INTERVAL)

    check = last_check(hass, entry)
    assert check["outcome"] == "rate_limited"
    assert check["next_check"] == (START + INTERVAL + timedelta(seconds=3605)).isoformat()
    assert state(hass, entry, "switch", KEY_NOTIFICATIONS).state == STATE_ON

    await advance(hass, freezer, timedelta(minutes=45))
    assert ha_ksef.count("metadata") == 2
    await advance(hass, freezer, timedelta(minutes=15, seconds=5))
    assert ha_ksef.count("metadata") == 3
    check = last_check(hass, entry)
    assert check["outcome"] == "ok"
    assert dt_util.parse_datetime(check["next_check"]) - dt_util.utcnow() == INTERVAL


async def test_an_outage_keeps_the_interval_and_the_entities_available(
    hass: HomeAssistant, freezer: FrozenDateTimeFactory, ha_ksef: FakeKsef, world: World
) -> None:
    entry = await setup(hass)
    ha_ksef.script("metadata", Reply(status=503), world.reply)
    await advance(hass, freezer, INTERVAL)

    check = state(hass, entry, "sensor", KEY_LAST_CHECK)
    assert check.attributes["outcome"] == "unavailable"
    assert dt_util.parse_datetime(check.state) == START  # the last success
    assert check.attributes["next_check"] == (START + 2 * INTERVAL).isoformat()
    for platform, key in (
        ("switch", KEY_NOTIFICATIONS),
        ("sensor", KEY_LAST_INVOICE),
        ("button", KEY_CHECK_NOW),
    ):
        assert state(hass, entry, platform, key).state != "unavailable"

    await advance(hass, freezer, INTERVAL)
    assert last_check(hass, entry)["outcome"] == "ok"
    assert ha_ksef.count("metadata") == 3


async def test_a_token_without_permission_starts_reauthentication_and_stops_checks(
    hass: HomeAssistant, freezer: FrozenDateTimeFactory, ha_ksef: FakeKsef, world: World
) -> None:
    entry = await setup(hass)
    ha_ksef.script(
        "metadata", Reply(status=403, json=fixture("error_403_missing_permissions.json"))
    )
    await advance(hass, freezer, INTERVAL)

    assert len(reauth_flows(hass)) == 1
    assert last_check(hass, entry)["outcome"] == "auth_failed"
    assert last_check(hass, entry)["next_check"] is None
    await advance(hass, freezer, timedelta(hours=2))
    assert ha_ksef.count("metadata") == 2
    with pytest.raises(ServiceValidationError):
        await press(hass, entry)


async def test_a_revoked_token_at_startup_starts_reauthentication(
    hass: HomeAssistant, ha_ksef: FakeKsef, world: World
) -> None:
    ha_ksef.script("status", status_reply(450, "Token KSeF został unieważniony"))
    entry = await setup(hass)

    assert entry.state is ConfigEntryState.LOADED
    assert len(reauth_flows(hass)) == 1
    assert last_check(hass, entry)["outcome"] == "auth_failed"
    assert ha_ksef.count("metadata") == 0


async def test_a_blocked_account_raises_a_repair_issue_and_stops_checks(
    hass: HomeAssistant, freezer: FrozenDateTimeFactory, ha_ksef: FakeKsef, world: World
) -> None:
    entry = await setup(hass)
    body = fixture("error_403_missing_permissions.json")
    body["reasonCode"] = "security-service-blocked"
    ha_ksef.script("metadata", Reply(status=403, json=body))
    await advance(hass, freezer, INTERVAL)

    issue_id = f"{ISSUE_ACCOUNT_BLOCKED}_{entry.entry_id}"
    assert ir.async_get(hass).async_get_issue(DOMAIN, issue_id) is not None
    assert reauth_flows(hass) == []
    assert last_check(hass, entry)["outcome"] == "blocked"
    await advance(hass, freezer, timedelta(hours=2))
    assert ha_ksef.count("metadata") == 2

    ha_ksef.script("metadata", world.reply)
    assert await hass.config_entries.async_reload(entry.entry_id)
    await settle(hass)
    assert ir.async_get(hass).async_get_issue(DOMAIN, issue_id) is None
    assert last_check(hass, entry)["outcome"] == "ok"


async def test_a_missing_phone_raises_an_issue_and_the_invoice_is_not_retried(
    hass: HomeAssistant, freezer: FrozenDateTimeFactory, world: World, events: list
) -> None:
    entry = await setup(hass)
    world.add(2)
    await advance(hass, freezer, INTERVAL)

    issue_id = f"{ISSUE_NOTIFY_SERVICE_MISSING}_{entry.entry_id}"
    issue = ir.async_get(hass).async_get_issue(DOMAIN, issue_id)
    assert issue is not None
    assert issue.translation_placeholders == {"service": f"notify.{PHONE}", "entry_title": TITLE}
    assert [event.data["notified"] for event in events] == [False]

    pushes = async_mock_service(hass, "notify", PHONE)
    await advance(hass, freezer, INTERVAL)
    assert pushes == []  # handled, not queued
    assert len(events) == 1

    world.add(3)
    await advance(hass, freezer, INTERVAL)
    assert len(pushes) == 1
    assert ir.async_get(hass).async_get_issue(DOMAIN, issue_id) is None


async def test_a_failing_phone_service_counts_as_not_notified(
    hass: HomeAssistant, freezer: FrozenDateTimeFactory, world: World, events: list
) -> None:
    async def _broken(_: ServiceCall) -> None:
        raise HomeAssistantError("synthetic failure")

    hass.services.async_register("notify", PHONE, _broken)
    entry = await setup(hass)
    world.add(2)
    await advance(hass, freezer, INTERVAL)

    assert [event.data["notified"] for event in events] == [False]
    issue_id = f"{ISSUE_NOTIFY_SERVICE_MISSING}_{entry.entry_id}"
    assert ir.async_get(hass).async_get_issue(DOMAIN, issue_id) is not None
    assert last_check(hass, entry)["outcome"] == "ok"


# --- invoice XML ------------------------------------------------------------------------------


async def test_xml_not_ready_defers_twice_then_notifies_without_its_fields(
    hass: HomeAssistant,
    freezer: FrozenDateTimeFactory,
    ha_ksef: FakeKsef,
    world: World,
    pushes: list,
    events: list,
) -> None:
    await setup(hass)
    ha_ksef.script("xml", error_reply(21165))
    world.add(2)
    await advance(hass, freezer, INTERVAL)
    await advance(hass, freezer, INTERVAL)
    assert pushes == []
    assert events == []

    await advance(hass, freezer, INTERVAL)
    assert ha_ksef.count("xml") == 3
    assert _messages(pushes) == [BODY_NO_XML]
    assert events[0].data["details"] == "unavailable"


async def test_xml_ready_on_the_second_check_is_notified_in_full(
    hass: HomeAssistant,
    freezer: FrozenDateTimeFactory,
    ha_ksef: FakeKsef,
    world: World,
    pushes: list,
) -> None:
    await setup(hass)
    ha_ksef.script("xml", error_reply(21165), XML_OK)
    world.add(2)
    await advance(hass, freezer, INTERVAL)
    await advance(hass, freezer, INTERVAL)

    assert _messages(pushes) == [BODY]


async def test_xml_not_found_or_unreadable_is_notified_at_once_without_its_fields(
    hass: HomeAssistant,
    freezer: FrozenDateTimeFactory,
    ha_ksef: FakeKsef,
    world: World,
    pushes: list,
) -> None:
    await setup(hass)
    ha_ksef.script("xml", error_reply(21164), Reply(body=b"<Faktura><unclosed></Faktura>"))
    world.add(2)
    world.add(3, ago=timedelta(seconds=20))
    await advance(hass, freezer, INTERVAL)

    assert _messages(pushes) == [BODY_NO_XML, BODY_NO_XML]


async def test_a_download_rate_limit_defers_the_rest_of_the_check_without_requests(
    hass: HomeAssistant,
    freezer: FrozenDateTimeFactory,
    ha_ksef: FakeKsef,
    world: World,
    pushes: list,
) -> None:
    await setup(hass)
    ha_ksef.script(
        "xml",
        Reply(status=429, json=fixture("error_429.json"), headers={"Retry-After": "60"}),
        XML_OK,
    )
    world.add(2)
    world.add(3, ago=timedelta(seconds=20))
    await advance(hass, freezer, INTERVAL)
    assert ha_ksef.count("xml") == 1
    assert pushes == []

    await advance(hass, freezer, INTERVAL)
    assert ha_ksef.count("xml") == 3
    assert _messages(pushes) == [BODY, BODY]


# --- language and several entries -------------------------------------------------------------


async def test_a_polish_home_assistant_sends_a_polish_notification(
    hass: HomeAssistant, freezer: FrozenDateTimeFactory, world: World, pushes: list
) -> None:
    """The notification follows Home Assistant's language, through `translations/pl.json`."""
    hass.config.language = "pl"
    await setup(hass)
    world.add(2)
    await advance(hass, freezer, INTERVAL)

    assert pushes[0].data["title"] == "Nowa faktura kosztowa"
    assert "Brutto: 123,00 PLN" in pushes[0].data["message"]
    assert "Termin płatności: 20.10.2026" in pushes[0].data["message"]


async def test_a_language_without_a_translation_falls_back_to_english(
    hass: HomeAssistant, freezer: FrozenDateTimeFactory, world: World, pushes: list
) -> None:
    hass.config.language = "de"
    await setup(hass)
    world.add(2)
    await advance(hass, freezer, INTERVAL)

    assert pushes[0].data["title"] == "New cost invoice"
    assert "Due date: 2026-10-20" in pushes[0].data["message"]


async def test_the_title_names_the_entry_when_there_are_several(
    hass: HomeAssistant, freezer: FrozenDateTimeFactory, world: World, pushes: list
) -> None:
    MockConfigEntry(
        domain=DOMAIN,
        title="KSeF 0000000000",
        unique_id="prod_0000000000",
        data={"environment": "prod", "nip": "0000000000", "token": "synthetic-other"},
        disabled_by=ConfigEntryDisabler.USER,
    ).add_to_hass(hass)
    await setup(hass)
    world.add(2)
    await advance(hass, freezer, INTERVAL)

    assert pushes[0].data["title"] == f"New cost invoice · {TITLE}"


# --- storage, unload, removal, diagnostics ----------------------------------------------------


async def test_the_store_holds_no_invoice_content(
    hass: HomeAssistant,
    freezer: FrozenDateTimeFactory,
    world: World,
    pushes: list,
    hass_storage: dict,
) -> None:
    entry = await setup(hass)
    freezer.tick(timedelta(minutes=14))
    world.add(2)  # recent enough to stay in `seen` after the check
    await advance(hass, freezer, timedelta(minutes=1))
    assert len(pushes) == 1

    data = stored(hass_storage, entry)
    assert set(data) == {"cursor", "seen"}
    assert list(data["seen"]) == [ksef_hash(number(2))]
    assert all(re.fullmatch(r"[0-9a-f]{16}", key) for key in data["seen"])
    text = json.dumps(hass_storage[storage_key(entry)])
    for value in (
        number(2),
        "3333333333",
        SELLER,
        INVOICE_NUMBER,
        "123",
        "2026-10-20",
        NIP,
        KSEF_TOKEN,
    ):
        assert value not in text, value


async def test_unload_revokes_the_session_and_removal_deletes_the_store(
    hass: HomeAssistant, ha_ksef: FakeKsef, world: World, hass_storage: dict
) -> None:
    entry = await setup(hass)
    assert storage_key(entry) in hass_storage

    assert await hass.config_entries.async_unload(entry.entry_id)
    await settle(hass)
    assert entry.state is ConfigEntryState.NOT_LOADED
    assert ha_ksef.count("revoke") == 1

    assert await hass.config_entries.async_remove(entry.entry_id)
    await settle(hass)
    assert storage_key(entry) not in hass_storage


async def test_diagnostics_redact_every_secret_and_invoice_value(
    hass: HomeAssistant, freezer: FrozenDateTimeFactory, world: World, pushes: list
) -> None:
    entry = await setup(hass)
    freezer.tick(timedelta(minutes=14))
    world.add(2)
    await advance(hass, freezer, timedelta(minutes=1))

    diagnostics = await async_get_config_entry_diagnostics(hass, entry)
    text = json.dumps(diagnostics, default=str)

    for secret in (
        KSEF_TOKEN,
        NIP,
        TITLE,
        PHONE,
        "synthetic-access-token",
        "synthetic-refresh-token",
        number(2),
        ksef_hash(number(2)),
        SELLER,
        INVOICE_NUMBER,
        "123.0",
        "2026-10-20",
    ):
        assert secret not in text, secret
    assert diagnostics["entry"]["data"]["environment"] == "test"
    assert diagnostics["entry"]["options"]["fields"] == entry.options[CONF_FIELDS]
    assert diagnostics["tracker"]["seen_entries"] == 1
    assert diagnostics["status"]["outcome"] == "ok"
    assert diagnostics["last_invoice"] == {"details": "fetched", "form_code": "FA (3)"}
