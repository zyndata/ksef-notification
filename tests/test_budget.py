"""The request budget, measured: the real integration over a simulated day and week.

Every request the integration sends is counted per KSeF limit group and checked in sliding
windows of 1 s, 60 s and 1 h against the limits in docs/KSEF_API.md § Rate limits, and against
the budget docs/ARCHITECTURE.md § Resource and request budget promises. The numbers these tests
assert are the measured numbers the documents quote.
"""

from __future__ import annotations

import gc
import tracemalloc
from collections import Counter
from collections.abc import Iterable
from dataclasses import replace
from datetime import datetime, timedelta
from pathlib import Path

from freezegun.api import FrozenDateTimeFactory
from homeassistant.core import HomeAssistant, ServiceCall
from homeassistant.helpers import storage
from homeassistant.util import dt as dt_util
from pytest_homeassistant_custom_component.test_util.aiohttp import AiohttpClientMocker

from custom_components.ksef_notification.const import COMBINE_THRESHOLD, CONF_FIELDS
from custom_components.ksef_notification.core.fields import FIELD_KEYS

from .ha_setup import (
    INTERVAL,
    PHONE,
    World,
    advance,
    last_check,
    make_entry,
    minutes,
    press,
    setup,
)
from .ksef_fake import START, FakeKsef

#: Route of the scripted KSeF → KSeF's limit group.
GROUPS = {
    "metadata": "invoiceMetadata",
    "xml": "invoiceDownload",
    "keys": "auth",
    "challenge": "auth",
    "init": "auth",
    "status": "auth",
    "redeem": "auth",
    "refresh": "auth",
    "revoke": "other",
}
SECOND, MINUTE, HOUR = timedelta(seconds=1), timedelta(minutes=1), timedelta(hours=1)
#: KSeF's limits per (NIP, IP), docs/KSEF_API.md § Rate limits.
LIMITS = {
    "invoiceMetadata": {SECOND: 8, MINUTE: 16, HOUR: 20},
    "invoiceDownload": {SECOND: 8, MINUTE: 16, HOUR: 64},
    "auth": {SECOND: 60},
    "other": {SECOND: 10, MINUTE: 30, HOUR: 120},
}
ALL_FIELDS = {CONF_FIELDS: list(FIELD_KEYS)}
COMPONENT = Path(__file__).parent.parent / "custom_components" / "ksef_notification"


def peak(times: Iterable[datetime], window: timedelta) -> int:
    """The most requests inside any window of this length (KSeF's windows slide)."""
    ordered = sorted(times)
    most = start = 0
    for end, at in enumerate(ordered):
        while ordered[start] <= at - window:
            start += 1
        most = max(most, end - start + 1)
    return most


def peaks(ksef: FakeKsef) -> dict[str, dict[timedelta, int]]:
    times: dict[str, list[datetime]] = {group: [] for group in LIMITS}
    for call in ksef.calls:
        assert call.at is not None
        times[GROUPS[call.route]].append(call.at)
    return {
        group: {window: peak(times[group], window) for window in LIMITS[group]} for group in LIMITS
    }


def assert_within_limits(measured: dict[str, dict[timedelta, int]]) -> None:
    for group, windows in LIMITS.items():
        for window, limit in windows.items():
            assert measured[group][window] <= limit, (group, window, measured[group][window])


async def test_a_day_at_the_minimum_interval_with_every_field_and_three_invoices_per_check(
    hass: HomeAssistant, freezer: FrozenDateTimeFactory, ha_ksef: FakeKsef, world: World
) -> None:
    """The worst case for downloads without pressing anything: as many new FA(3) invoices
    before every check as are still notified one by one (one more would be combined, which
    downloads nothing)."""
    entry = await setup(hass, make_entry(ALL_FIELDS))
    n = 2
    async for minute in minutes(hass, freezer, 24 * 60):
        if minute % 15 == 14:
            for _ in range(COMBINE_THRESHOLD):
                world.add(n, ago=timedelta(seconds=40 - n % 3))
                n += 1

    counts = Counter(call.route for call in ha_ksef.calls)
    assert counts["metadata"] == 97  # the baseline and 96 checks
    assert counts["xml"] == 288
    assert counts["redeem"] == 1  # one full authentication, at setup
    assert counts["refresh"] == 96  # an access token lasts exactly one interval
    measured = peaks(ha_ksef)
    assert measured["invoiceMetadata"] == {SECOND: 1, MINUTE: 1, HOUR: 4}
    assert measured["invoiceDownload"] == {SECOND: 3, MINUTE: 3, HOUR: 12}
    assert measured["auth"][SECOND] == 5  # the setup's authentication
    assert_within_limits(measured)
    check = last_check(hass, entry)
    assert check["metadata_requests_last_hour"] == 4
    assert check["download_requests_last_hour"] == 12


async def test_a_day_of_check_now_every_ten_minutes(
    hass: HomeAssistant, freezer: FrozenDateTimeFactory, ha_ksef: FakeKsef, world: World
) -> None:
    """The most a user can make the integration ask: the button at every chance, three new
    invoices each time. A manual check resets the timer, so the timer never fires."""
    entry = await setup(hass, make_entry(ALL_FIELDS))
    n = 2
    async for minute in minutes(hass, freezer, 24 * 60):
        if minute % 10 == 9:
            for _ in range(COMBINE_THRESHOLD):
                world.add(n, ago=timedelta(seconds=40 - n % 3))
                n += 1
        if minute % 10 == 0 and minute:
            await press(hass, entry)

    counts = Counter(call.route for call in ha_ksef.calls)
    assert counts["metadata"] == 144  # the baseline and 143 presses
    assert counts["xml"] == 429
    measured = peaks(ha_ksef)
    assert measured["invoiceMetadata"] == {SECOND: 1, MINUTE: 1, HOUR: 6}
    assert measured["invoiceDownload"] == {SECOND: 3, MINUTE: 3, HOUR: 18}
    assert_within_limits(measured)


async def test_a_burst_of_invoices_is_combined_and_downloads_nothing(
    hass: HomeAssistant, freezer: FrozenDateTimeFactory, ha_ksef: FakeKsef, world: World
) -> None:
    await setup(hass, make_entry(ALL_FIELDS))
    for n in range(2, 302):
        world.add(n, ago=timedelta(seconds=50))
    await advance(hass, freezer, INTERVAL)

    counts = Counter(call.route for call in ha_ksef.calls)
    assert counts["xml"] == 0
    assert counts["metadata"] == 2  # the baseline and one check (paging: test_client_api.py)
    assert_within_limits(peaks(ha_ksef))


async def test_a_week_costs_two_full_authentications_and_memory_stays_flat(
    hass: HomeAssistant,
    freezer: FrozenDateTimeFactory,
    ha_ksef: FakeKsef,
    world: World,
    aioclient_mock: AiohttpClientMocker,
) -> None:
    """Seven days and an hour at the default interval, three invoices an hour. The refresh
    token lasts exactly seven days, so a week costs the setup's authentication and one more.

    Memory is what the integration's own code allocated and still holds, compared between day
    two and day seven. The harness keeps things production does not, so they are dropped
    before each measurement: the recorded requests, the storage mock's call history, and log
    records (a no-op phone, so no push logs a warning; every record holds its task's name).
    Day one is warm-up: aiohttp's URL parser keeps the last 128 URLs (yarl's lru_cache), and
    each invoice download has its own URL.
    """

    async def phone(_: ServiceCall) -> None:
        return None

    hass.services.async_register("notify", PHONE, phone)
    entry = await setup(hass)
    coordinator = entry.runtime_data
    n = 2

    async def run(hours: int) -> None:
        nonlocal n
        for _ in range(hours):
            for check in range(4):
                if check < 3:
                    world.add(n, ago=timedelta(seconds=30))
                    n += 1
                await advance(hass, freezer, INTERVAL)

    def held_bytes() -> int:
        aioclient_mock.mock_calls.clear()
        ha_ksef.calls[:] = [replace(call, body=None, headers={}) for call in ha_ksef.calls]
        storage.Store._async_write_data.reset_mock()  # type: ignore[attr-defined]
        storage.Store._async_load.reset_mock()  # type: ignore[attr-defined]
        gc.collect()
        snapshot = tracemalloc.take_snapshot().filter_traces(
            [tracemalloc.Filter(inclusive=True, filename_pattern=f"{COMPONENT}/*")]
        )
        return sum(stat.size for stat in snapshot.statistics("filename"))

    tracemalloc.start()
    try:
        await run(48)
        day_two = held_bytes()
        timers_day_two = len(hass.loop._scheduled)
        await run(24 * 5 + 1)
        day_seven = held_bytes()
        timers_day_seven = len(hass.loop._scheduled)
    finally:
        tracemalloc.stop()

    assert dt_util.utcnow() >= START + timedelta(days=7)
    counts = Counter(call.route for call in ha_ksef.calls)
    assert counts["redeem"] == 2
    assert counts["keys"] == 1  # the fake's certificate is valid for 30 days
    assert counts["metadata"] == 1 + 4 * (24 * 7 + 1)
    assert counts["xml"] == 3 * (24 * 7 + 1)
    assert_within_limits(peaks(ha_ksef))

    assert day_seven - day_two < 2048, (day_two, day_seven)
    assert timers_day_seven == timers_day_two
    assert len(coordinator.tracker_state.seen) <= 3
    assert coordinator.tracker_state.deferrals == {}
    # The request counters hold one hour: 4 queries, 4 refreshes, 3 downloads.
    sent = coordinator.client._http._sent
    assert sum(len(times) for times in sent.values()) == 11
