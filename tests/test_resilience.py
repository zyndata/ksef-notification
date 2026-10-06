"""Failure paths end to end: the integration recovers on its own, notifies each invoice once,
and asks for a new token only when the KSeF token itself is the problem.

Where KSeF TEST could be made to fail for real (2026-10-06), the scripted replies repeat what it
answered, KSeF's own texts included — see docs/KSEF_API.md § Failure behaviour, observed. The
rest (expiry after 7 days, a multi-hour outage, a restart in the middle of a check) cannot be
provoked on TEST and is simulated here only.
"""

from __future__ import annotations

import asyncio
import copy
import logging
import threading
from datetime import timedelta
from typing import Any

import aiohttp
import pytest
from freezegun.api import FrozenDateTimeFactory
from homeassistant import block_async_io
from homeassistant.core import HomeAssistant, ServiceCall
from homeassistant.helpers import issue_registry as ir
from homeassistant.util import dt as dt_util
from pytest_homeassistant_custom_component.common import async_fire_time_changed

from custom_components.ksef_notification.const import (
    CONF_FIELDS,
    CONF_TOKEN,
    DOMAIN,
    ISSUE_ACCOUNT_BLOCKED,
    KEY_CHECK_NOW,
    KEY_LAST_INVOICE,
    KEY_NOTIFICATIONS,
)
from custom_components.ksef_notification.core import xml_parser
from custom_components.ksef_notification.core.fields import FIELD_KEYS
from custom_components.ksef_notification.core.tracker import ksef_hash

from .ha_setup import (
    INTERVAL,
    PHONE,
    World,
    advance,
    entity_id,
    last_check,
    make_entry,
    minutes,
    number,
    press,
    reauth_flows,
    settle,
    setup,
    state,
    storage_key,
)
from .ksef_fake import (
    START,
    Call,
    FakeKsef,
    Reply,
    error_reply,
    fixture,
    status_reply,
)

# KSeF's texts as TEST returned them on 2026-10-06.
REFRESH_TOKEN_REVOKED = "Token KSeF został unieważniony."
STATUS_TOKEN_REVOKED = "Token unieważniony."
REFRESH_SESSION_REVOKED = (
    "Status uwierzytelnienia (425) nie pozwala na odświeżenie tokenu dostępowego."
)
REFRESH_BLOCKED = "Status uwierzytelnienia (480) nie pozwala na odświeżenie tokenu dostępowego."
STATUS_BLOCKED = (
    "Podejrzenie incydentu bezpieczeństwa. Skontaktuj się z Ministerstwem Finansów przez "
    "formularz zgłoszeniowy."
)
FULL_AUTH = ["challenge", "init", "status", "redeem"]


def _tags(pushes: list[ServiceCall]) -> list[str]:
    return [call.data["data"]["tag"] for call in pushes]


def _tag(n: int) -> str:
    return f"ksef_{ksef_hash(number(n))}"


def _since(ksef: FakeKsef, mark: int) -> list[str]:
    return [call.route for call in ksef.calls[mark:]]


def _assert_available(hass: HomeAssistant, entry: Any) -> None:
    for platform, key in (
        ("switch", KEY_NOTIFICATIONS),
        ("sensor", KEY_LAST_INVOICE),
        ("button", KEY_CHECK_NOW),
    ):
        assert state(hass, entry, platform, key).state != "unavailable"


# --- access and refresh tokens ----------------------------------------------------------------


async def test_an_access_token_refused_as_expired_is_refreshed_and_the_query_repeated(
    hass: HomeAssistant,
    freezer: FrozenDateTimeFactory,
    ha_ksef: FakeKsef,
    world: World,
    pushes: list,
) -> None:
    """KSeF refuses an access token the client still believed valid (a clock behind): one new
    token, the query once more. Observed live: HTTP 401, then refresh and retry succeed."""
    entry = await setup(hass)
    world.add(2)
    ha_ksef.script("metadata", Reply(status=401), world.reply)
    mark = len(ha_ksef.calls)
    await advance(hass, freezer, INTERVAL)

    assert _since(ha_ksef, mark) == ["refresh", "metadata", "refresh", "metadata", "xml"]
    assert _tags(pushes) == [_tag(2)]
    assert last_check(hass, entry)["outcome"] == "ok"
    assert reauth_flows(hass) == []


@pytest.mark.parametrize(
    "refusal",
    [
        pytest.param(Reply(status=401), id="refresh token expired"),
        pytest.param(error_reply(21301, REFRESH_SESSION_REVOKED), id="session revoked elsewhere"),
    ],
)
async def test_a_refused_refresh_token_falls_back_to_a_full_authentication(
    hass: HomeAssistant,
    freezer: FrozenDateTimeFactory,
    ha_ksef: FakeKsef,
    world: World,
    pushes: list,
    refusal: Reply,
) -> None:
    entry = await setup(hass)
    world.add(2)
    ha_ksef.script("refresh", refusal, ha_ksef.default("refresh"))
    mark = len(ha_ksef.calls)
    await advance(hass, freezer, INTERVAL)

    # The public key is still cached: four authentication requests, not five.
    assert _since(ha_ksef, mark) == ["refresh", *FULL_AUTH, "metadata", "xml"]
    assert _tags(pushes) == [_tag(2)]
    assert last_check(hass, entry)["outcome"] == "ok"
    assert reauth_flows(hass) == []

    mark = len(ha_ksef.calls)
    await advance(hass, freezer, INTERVAL)
    assert _since(ha_ksef, mark) == ["refresh", "metadata"]


async def test_the_refresh_token_expiring_after_seven_days_costs_one_full_authentication(
    hass: HomeAssistant, freezer: FrozenDateTimeFactory, ha_ksef: FakeKsef, world: World
) -> None:
    entry = await setup(hass)
    redeemed = ha_ksef.count("redeem")
    while dt_util.utcnow() < START + timedelta(days=7) - INTERVAL:
        await advance(hass, freezer, INTERVAL)
    assert ha_ksef.count("redeem") == redeemed

    mark = len(ha_ksef.calls)
    await advance(hass, freezer, INTERVAL)

    # Less than TOKEN_MARGIN left on the refresh token: no refresh is even attempted.
    assert _since(ha_ksef, mark) == [*FULL_AUTH, "metadata"]
    assert last_check(hass, entry)["outcome"] == "ok"
    assert reauth_flows(hass) == []


# --- the KSeF token itself ----------------------------------------------------------------------


async def test_a_revoked_ksef_token_asks_for_a_new_one_and_nothing_is_lost(
    hass: HomeAssistant,
    freezer: FrozenDateTimeFactory,
    ha_ksef: FakeKsef,
    world: World,
    pushes: list,
) -> None:
    """The sequence TEST showed for a KSeF token revoked while the integration runs: the held
    access token keeps working until it expires, then the refresh and the full
    authentication are refused. A new token resumes from the cursor, not from a baseline."""
    entry = await setup(hass)
    ha_ksef.script("refresh", error_reply(21301, REFRESH_TOKEN_REVOKED))
    ha_ksef.script("status", status_reply(450, STATUS_TOKEN_REVOKED))

    freezer.tick(timedelta(minutes=9))
    world.add(2)
    await advance(hass, freezer, timedelta(minutes=1))
    await press(hass, entry)
    assert _tags(pushes) == [_tag(2)]  # the access token still worked

    mark = len(ha_ksef.calls)
    await advance(hass, freezer, INTERVAL)
    assert _since(ha_ksef, mark) == ["refresh", "challenge", "init", "status"]
    assert len(reauth_flows(hass)) == 1
    assert last_check(hass, entry)["outcome"] == "auth_failed"
    _assert_available(hass, entry)

    world.add(3)
    mark = len(ha_ksef.calls)
    for _ in range(12):
        await advance(hass, freezer, INTERVAL)
    assert _since(ha_ksef, mark) == []

    # The user enters a new token (the reauth step updates the entry and reloads it).
    ha_ksef.heal("refresh", "status")
    hass.config_entries.async_update_entry(
        entry, data={**entry.data, CONF_TOKEN: "synthetic-ksef-token-0001"}
    )
    assert await hass.config_entries.async_reload(entry.entry_id)
    await settle(hass)

    assert _tags(pushes) == [_tag(2), _tag(3)]
    assert last_check(hass, entry)["outcome"] == "ok"


async def test_a_blocked_context_raises_an_issue_not_a_reauthentication(
    hass: HomeAssistant,
    freezer: FrozenDateTimeFactory,
    ha_ksef: FakeKsef,
    world: World,
    pushes: list,
) -> None:
    """TEST's /testdata/context/block: refresh refused with 21301 "(480)", then status 480."""
    entry = await setup(hass)
    ha_ksef.script("refresh", error_reply(21301, REFRESH_BLOCKED))
    ha_ksef.script("status", status_reply(480, STATUS_BLOCKED))
    await advance(hass, freezer, INTERVAL)

    issue = f"{ISSUE_ACCOUNT_BLOCKED}_{entry.entry_id}"
    assert ir.async_get(hass).async_get_issue(DOMAIN, issue) is not None
    assert reauth_flows(hass) == []
    assert last_check(hass, entry)["outcome"] == "blocked"

    world.add(2)
    mark = len(ha_ksef.calls)
    for _ in range(8):
        await advance(hass, freezer, INTERVAL)
    assert _since(ha_ksef, mark) == []

    ha_ksef.heal("refresh", "status")
    assert await hass.config_entries.async_reload(entry.entry_id)
    await settle(hass)
    assert _tags(pushes) == [_tag(2)]
    assert ir.async_get(hass).async_get_issue(DOMAIN, issue) is None


# --- rate limits --------------------------------------------------------------------------------


@pytest.mark.parametrize("route", ["metadata", "refresh"])
async def test_after_a_rate_limit_nothing_is_sent_until_retry_after_and_nothing_is_lost(
    hass: HomeAssistant,
    freezer: FrozenDateTimeFactory,
    ha_ksef: FakeKsef,
    world: World,
    pushes: list,
    route: str,
) -> None:
    """TEST's /testdata/rate-limits: 429 with Retry-After 3565 s (a sliding hour)."""
    entry = await setup(hass)
    limited = Reply(status=429, json=fixture("error_429.json"), headers={"Retry-After": "3565"})
    ha_ksef.script(route, limited, world.reply if route == "metadata" else ha_ksef.default(route))
    await advance(hass, freezer, INTERVAL)
    blocked_at = dt_util.utcnow()
    assert last_check(hass, entry)["outcome"] == "rate_limited"

    mark = len(ha_ksef.calls)
    for n in range(2, 5):
        world.add(n)
        await advance(hass, freezer, INTERVAL)
    assert ha_ksef.calls[mark:] == []

    await advance(hass, freezer, INTERVAL)
    assert all(call.at >= blocked_at + timedelta(seconds=3565) for call in ha_ksef.calls[mark:])
    assert last_check(hass, entry)["outcome"] == "ok"
    assert _tags(pushes) == [_tag(n) for n in range(2, 5)]
    for _ in range(4):
        await advance(hass, freezer, INTERVAL)
    assert len(pushes) == 3
    assert reauth_flows(hass) == []


# --- outage -------------------------------------------------------------------------------------


async def test_a_six_hour_outage_costs_one_request_per_check_and_loses_nothing(
    hass: HomeAssistant,
    freezer: FrozenDateTimeFactory,
    ha_ksef: FakeKsef,
    world: World,
    pushes: list,
    events: list,
) -> None:
    entry = await setup(hass)
    failures = [
        Reply(status=503),
        Reply(exc=TimeoutError()),
        Reply(exc=aiohttp.ClientConnectionError()),
    ]

    def down(_: Call) -> Reply:
        failures.append(failures.pop(0))
        return failures[-1]

    outage = ("refresh", "metadata", "challenge", "keys", "xml")
    for route in outage:
        ha_ksef.script(route, down)
    mark = len(ha_ksef.calls)
    async for minute in minutes(hass, freezer, 6 * 60):
        if minute % 75 == 20:
            world.add(minute // 75 + 2)
    assert last_check(hass, entry)["outcome"] == "unavailable"

    assert len(ha_ksef.calls) - mark == 24  # one request per check, nothing more
    check = state(hass, entry, "sensor", "last_check")
    assert dt_util.parse_datetime(check.state) == START  # the last success
    _assert_available(hass, entry)
    assert reauth_flows(hass) == []
    assert ir.async_get(hass).issues == {}
    assert pushes == []

    ha_ksef.heal(*outage)
    ha_ksef.script("metadata", world.reply)
    mark = len(ha_ksef.calls)
    await advance(hass, freezer, INTERVAL)

    # One query covers the whole gap, from the cursor of the last good check.
    assert _since(ha_ksef, mark) == ["refresh", "metadata"]
    assert ha_ksef.last("metadata").body["dateRange"]["from"] == (
        START - timedelta(minutes=3)
    ).isoformat(timespec="microseconds")
    assert len(pushes) == 1
    assert pushes[0].data["title"] == "5 new cost invoices"
    assert sorted(event.data["ksef_number"] for event in events) == sorted(
        number(n) for n in range(2, 7)
    )
    for _ in range(4):
        await advance(hass, freezer, INTERVAL)
    assert len(pushes) == 1
    assert len(events) == 5


async def test_an_outage_in_the_middle_of_authentication_is_temporary(
    hass: HomeAssistant, freezer: FrozenDateTimeFactory, ha_ksef: FakeKsef, world: World
) -> None:
    """Authentication status 550 or 500, or the challenge itself failing, is KSeF's problem,
    not the token's. (A status poll that never finishes is covered in test_client_auth.py:
    this harness's clock does not move during a check.)"""
    entry = await setup(hass)
    ha_ksef.script("refresh", Reply(status=401))
    for route, reply in (
        ("status", status_reply(550)),
        ("status", status_reply(500)),
        ("challenge", Reply(status=503)),
    ):
        ha_ksef.script(route, reply)
        await advance(hass, freezer, INTERVAL)
        ha_ksef.heal(route)
        assert last_check(hass, entry)["outcome"] == "unavailable"
    assert reauth_flows(hass) == []

    await advance(hass, freezer, INTERVAL)
    assert last_check(hass, entry)["outcome"] == "ok"


# --- restarts -----------------------------------------------------------------------------------


async def test_a_restart_in_the_middle_of_a_check_repeats_nothing_and_misses_nothing(
    hass: HomeAssistant,
    freezer: FrozenDateTimeFactory,
    ha_ksef: FakeKsef,
    world: World,
) -> None:
    """Home Assistant restarts (here: the entry reloads) while the second of three pushes is on
    its way. Every push that completed is in the store; the reloaded entry sends the rest."""
    started: list[str] = []
    delivered: list[str] = []
    hold = asyncio.Event()

    async def phone(call: ServiceCall) -> None:
        tag = call.data["data"]["tag"]
        started.append(tag)
        if len(started) == 2:
            await hold.wait()  # never set: the restart interrupts this push
        delivered.append(tag)

    hass.services.async_register("notify", PHONE, phone)
    entry = await setup(hass)
    for n, ago in ((2, 50), (3, 40), (4, 30)):
        world.add(n, ago=timedelta(seconds=ago))
    freezer.tick(INTERVAL)
    async_fire_time_changed(hass)
    for _ in range(50):
        if len(started) == 2:
            break
        await hass.async_block_till_done()
    assert started == [_tag(2), _tag(3)]

    assert await hass.config_entries.async_reload(entry.entry_id)
    await settle(hass)

    assert delivered == [_tag(2), _tag(3), _tag(4)]
    await advance(hass, freezer, INTERVAL)
    assert delivered == [_tag(2), _tag(3), _tag(4)]


async def test_a_hard_crash_right_after_a_push_repeats_at_most_that_one_push(
    hass: HomeAssistant,
    freezer: FrozenDateTimeFactory,
    ha_ksef: FakeKsef,
    world: World,
    hass_storage: dict,
) -> None:
    """The documented at-least-once corner: power lost after a push left Home Assistant but
    before its store write. The disk then holds the state from just before that push."""
    entry = await setup(hass)
    disk_at_push: list[dict[str, Any]] = []
    delivered: list[str] = []

    async def phone(call: ServiceCall) -> None:
        disk_at_push.append(copy.deepcopy(hass_storage[storage_key(entry)]))
        delivered.append(call.data["data"]["tag"])

    hass.services.async_register("notify", PHONE, phone)
    for n, ago in ((2, 50), (3, 40), (4, 30)):
        world.add(n, ago=timedelta(seconds=ago))
    await advance(hass, freezer, INTERVAL)
    assert delivered == [_tag(2), _tag(3), _tag(4)]

    assert await hass.config_entries.async_unload(entry.entry_id)
    await settle(hass)
    hass_storage[storage_key(entry)] = disk_at_push[1]  # crashed just after push 2 of 3
    freezer.tick(timedelta(minutes=5))
    assert await hass.config_entries.async_setup(entry.entry_id)
    await settle(hass)

    # Had the crash really come after push 2 of 3, the phone would have shown 2, 3, then 3, 4:
    # the one push in flight is repeated, nothing before it, nothing missed.
    assert delivered[3:] == [_tag(3), _tag(4)]


# --- the event loop -----------------------------------------------------------------------------


async def test_nothing_blocks_the_event_loop(
    hass: HomeAssistant,
    freezer: FrozenDateTimeFactory,
    ha_ksef: FakeKsef,
    world: World,
    pushes: list,
    caplog: pytest.LogCaptureFixture,
    disable_block_async_io: None,
) -> None:
    """Home Assistant's own blocking-call detection, switched on for a full life cycle: setup,
    individual and combined checks with every field (XML parsed), switch, reload, removal.

    Under tests the detection normally skips file access (`open`, `Path.read_*`, `os.listdir`
    …), because the harness itself reads files; this test watches the full production set.
    """
    parsed_on: list[bool] = []
    parse = xml_parser.parse

    def tracked_parse(body: bytes) -> Any:
        parsed_on.append(threading.current_thread() is threading.main_thread())
        return parse(body)

    caplog.set_level(logging.WARNING)
    with pytest.MonkeyPatch.context() as patch:
        patch.setattr(block_async_io, "_IN_TESTS", False)
        block_async_io.enable()
        patch.setattr(xml_parser, "parse", tracked_parse)
        entry = await setup(hass, make_entry({CONF_FIELDS: list(FIELD_KEYS)}))
        for n in (2, 3):
            world.add(n, ago=timedelta(seconds=40 - n))
        await advance(hass, freezer, INTERVAL)
        for n in range(4, 9):
            world.add(n, ago=timedelta(seconds=40 - n))
        await advance(hass, freezer, INTERVAL)
        await hass.services.async_call(
            "switch",
            "turn_off",
            {"entity_id": entity_id(hass, entry, "switch", KEY_NOTIFICATIONS)},
            blocking=True,
        )
        await settle(hass)
        assert await hass.config_entries.async_reload(entry.entry_id)
        await settle(hass)
        assert await hass.config_entries.async_remove(entry.entry_id)
        await settle(hass)

    assert parsed_on == [False, False]
    assert len(pushes) == 3
    assert "blocking call" not in caplog.text.lower()
    assert "Unexpected error" not in caplog.text
