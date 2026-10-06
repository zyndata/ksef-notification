"""Quiet hours end to end: no request to KSeF inside them, the night caught up after them.

Home Assistant runs in Europe/Warsaw, where `START` (08:00 UTC) is 10:00 local time.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from freezegun.api import FrozenDateTimeFactory
from homeassistant.const import STATE_ON
from homeassistant.core import HomeAssistant
from homeassistant.util import dt as dt_util

from custom_components.ksef_notification.const import (
    CONF_FIELDS,
    CONF_QUIET_END,
    CONF_QUIET_START,
    KEY_NOTIFICATIONS,
)

from .ha_setup import (
    INTERVAL,
    World,
    advance,
    last_check,
    make_entry,
    minutes,
    press,
    setup,
    state,
)
from .ksef_fake import AUTH_ROUTES, START, FakeKsef, Reply, fixture


@pytest.fixture(autouse=True)
async def warsaw(hass: HomeAssistant) -> None:
    await hass.config.async_set_time_zone("Europe/Warsaw")


def _quiet(start: str, end: str) -> dict[str, str]:
    return {CONF_QUIET_START: start, CONF_QUIET_END: end}


async def test_no_request_during_quiet_hours_and_the_invoices_come_after_them(
    hass: HomeAssistant,
    freezer: FrozenDateTimeFactory,
    ha_ksef: FakeKsef,
    world: World,
    pushes: list,
) -> None:
    """Checks at 10:00 and 10:15; quiet 10:30 to 12:00; the next check at 12:00 sharp."""
    entry = await setup(hass, make_entry(_quiet("10:30", "12:00")))
    await advance(hass, freezer, INTERVAL)
    assert ha_ksef.count("metadata") == 2
    quiet_end = START + timedelta(hours=2)
    assert last_check(hass, entry)["next_check"] == quiet_end.isoformat()

    calls = len(ha_ksef.calls)
    async for minute in minutes(hass, freezer, 104):  # 10:15 → 11:59
        if minute in (20, 60):
            world.add(minute, ago=timedelta(0))
    assert len(ha_ksef.calls) == calls  # not even a token refresh
    assert pushes == []

    await advance(hass, freezer, timedelta(minutes=1))
    assert dt_util.utcnow() == quiet_end
    assert ha_ksef.count("metadata") == 3
    assert len(pushes) == 2
    assert last_check(hass, entry)["next_check"] == (quiet_end + INTERVAL).isoformat()


async def test_a_night_across_midnight_ends_in_the_morning(
    hass: HomeAssistant, freezer: FrozenDateTimeFactory, ha_ksef: FakeKsef, world: World
) -> None:
    freezer.move_to(datetime(2026, 10, 6, 19, 50, tzinfo=UTC))  # 21:50 local
    entry = await setup(hass, make_entry(_quiet("22:00", "06:00")))
    morning = datetime(2026, 10, 7, 4, 0, tzinfo=UTC)  # 06:00 local
    assert last_check(hass, entry)["next_check"] == morning.isoformat()

    for _ in range(8):  # to 03:50 UTC
        await advance(hass, freezer, timedelta(hours=1))
        assert ha_ksef.count("metadata") == 1
    await advance(hass, freezer, timedelta(minutes=10))
    assert dt_util.utcnow() == morning
    assert ha_ksef.count("metadata") == 2


async def test_home_assistant_started_in_quiet_hours_asks_nothing_until_they_end(
    hass: HomeAssistant, freezer: FrozenDateTimeFactory, ha_ksef: FakeKsef, world: World
) -> None:
    entry = await setup(hass, make_entry(_quiet("09:00", "11:00")))

    assert ha_ksef.calls == []
    assert state(hass, entry, "switch", KEY_NOTIFICATIONS).state == STATE_ON
    assert last_check(hass, entry)["next_check"] == (START + timedelta(hours=1)).isoformat()

    await advance(hass, freezer, timedelta(hours=1))
    assert ha_ksef.routes() == [*AUTH_ROUTES, "metadata"]


async def test_check_now_works_during_quiet_hours(
    hass: HomeAssistant, freezer: FrozenDateTimeFactory, ha_ksef: FakeKsef, world: World
) -> None:
    entry = await setup(hass, make_entry(_quiet("09:00", "11:00")))

    await press(hass, entry)

    assert ha_ksef.count("metadata") == 1
    assert last_check(hass, entry)["next_check"] == (START + timedelta(hours=1)).isoformat()


async def test_a_rate_limit_back_off_ending_in_quiet_hours_waits_for_their_end(
    hass: HomeAssistant, freezer: FrozenDateTimeFactory, ha_ksef: FakeKsef, world: World
) -> None:
    entry = await setup(hass, make_entry(_quiet("11:00", "12:00")))
    ha_ksef.script(
        "metadata",
        Reply(status=429, json=fixture("error_429.json"), headers={"Retry-After": "3600"}),
        world.reply,
    )
    await advance(hass, freezer, INTERVAL)

    assert last_check(hass, entry)["outcome"] == "rate_limited"
    assert last_check(hass, entry)["next_check"] == (START + timedelta(hours=2)).isoformat()


async def test_without_quiet_hours_the_night_is_checked_as_usual(
    hass: HomeAssistant, freezer: FrozenDateTimeFactory, ha_ksef: FakeKsef, world: World
) -> None:
    freezer.move_to(datetime(2026, 10, 6, 19, 50, tzinfo=UTC))
    await setup(hass, make_entry({CONF_FIELDS: ["seller_name"]}))

    for _ in range(4):
        await advance(hass, freezer, INTERVAL)
    assert ha_ksef.count("metadata") == 5
