"""Setting the integration up in a test Home Assistant against the scripted KSeF.

Time is Home Assistant's frozen clock (`freezer`): the client, the scripted KSeF's tokens and
the coordinator's timer all read it, so moving it moves everything together. `World` is
KSeF's invoice store — a metadata query returns what was stored since its `from`, with a
high-water mark two minutes behind now, as observed on TEST.
"""

from __future__ import annotations

import json
from collections.abc import AsyncIterator
from datetime import datetime, timedelta
from typing import Any

from freezegun.api import FrozenDateTimeFactory
from homeassistant.config_entries import SOURCE_REAUTH
from homeassistant.core import HomeAssistant, State
from homeassistant.helpers import entity_registry as er
from homeassistant.util import dt as dt_util
from pytest_homeassistant_custom_component.common import (
    MockConfigEntry,
    async_fire_time_changed,
)

from custom_components.ksef_notification.const import (
    CONF_CHECK_INTERVAL_MIN,
    CONF_ENVIRONMENT,
    CONF_FIELDS,
    CONF_NIP,
    CONF_NOTIFY_SERVICE,
    CONF_TOKEN,
    DEFAULT_FIELDS,
    DOMAIN,
    ENV_TEST,
    KEY_CHECK_NOW,
    KEY_LAST_CHECK,
)

from .ksef_fake import (
    KSEF_TOKEN,
    NIP,
    Call,
    FakeClock,
    FakeKsef,
    Reply,
    invoice,
    metadata_reply,
    stamp,
)

PHONE = "mobile_app_phone"
INTERVAL = timedelta(minutes=15)
HWM_LAG = timedelta(minutes=2)
TITLE = f"KSeF {NIP} (TEST)"
DATA = {CONF_ENVIRONMENT: ENV_TEST, CONF_NIP: NIP, CONF_TOKEN: KSEF_TOKEN}
OPTIONS = {
    CONF_NOTIFY_SERVICE: PHONE,
    CONF_FIELDS: list(DEFAULT_FIELDS),
    CONF_CHECK_INTERVAL_MIN: 15,
}


class HaClock(FakeClock):
    """The fake clock, following Home Assistant's frozen time instead of its own."""

    def utcnow(self) -> datetime:
        return dt_util.utcnow()

    def monotonic(self) -> float:
        return dt_util.utcnow().timestamp()

    async def sleep(self, seconds: float) -> None:
        self.sleeps.append(seconds)


class World:
    """The invoices stored in KSeF; serves the metadata route."""

    def __init__(self, ksef: FakeKsef) -> None:
        self.records: list[dict[str, Any]] = []
        ksef.script("metadata", self.reply)

    def add(
        self, number: int, *, ago: timedelta = timedelta(seconds=30), **changes: Any
    ) -> dict[str, Any]:
        """An invoice stored `ago` before now."""
        record = invoice(number, dt_util.utcnow() - ago)
        record.update(changes)
        self.records.append(record)
        return record

    def reply(self, call: Call) -> Reply:
        date_from = datetime.fromisoformat(call.body["dateRange"]["from"])
        rows = sorted(
            (
                r
                for r in self.records
                if datetime.fromisoformat(r["permanentStorageDate"]) >= date_from
            ),
            key=lambda r: datetime.fromisoformat(r["permanentStorageDate"]),
        )
        return metadata_reply(rows, hwm=stamp(dt_util.utcnow() - HWM_LAG))


def make_entry(options: dict[str, Any] | None = None, **kwargs: Any) -> MockConfigEntry:
    return MockConfigEntry(
        domain=DOMAIN,
        title=TITLE,
        unique_id=f"{ENV_TEST}_{NIP}",
        data=dict(DATA),
        options={**OPTIONS, **(options or {})},
        version=1,
        **kwargs,
    )


async def setup(hass: HomeAssistant, entry: MockConfigEntry | None = None) -> MockConfigEntry:
    entry = entry or make_entry()
    if hass.config_entries.async_get_entry(entry.entry_id) is None:
        entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await settle(hass)
    return entry


async def settle(hass: HomeAssistant) -> None:
    await hass.async_block_till_done(wait_background_tasks=True)


async def advance(hass: HomeAssistant, freezer: FrozenDateTimeFactory, delta: timedelta) -> None:
    """Move time forward and let every timer that came due run to the end."""
    freezer.tick(delta)
    async_fire_time_changed(hass)
    await settle(hass)


async def minutes(
    hass: HomeAssistant, freezer: FrozenDateTimeFactory, count: int
) -> AsyncIterator[int]:
    """Move time forward one minute at a time, yielding the minute number before each step.

    `advance` lets one due timer fire per call, so a check scheduled too soon would hide inside
    a long jump; stepping by the minute lets every timer fire when it is due.
    """
    for minute in range(count):
        yield minute
        await advance(hass, freezer, timedelta(minutes=1))


def entity_id(hass: HomeAssistant, entry: MockConfigEntry, platform: str, key: str) -> str:
    found = er.async_get(hass).async_get_entity_id(platform, DOMAIN, f"{entry.entry_id}_{key}")
    assert found is not None, key
    return found


def number(n: int) -> str:
    """The KSeF number `invoice(n)` and `World.add(n)` give invoice n."""
    return f"3333333333-20261006-{n:012X}-{n % 256:02X}"


def state(hass: HomeAssistant, entry: MockConfigEntry, platform: str, key: str) -> State:
    found = hass.states.get(entity_id(hass, entry, platform, key))
    assert found is not None
    return found


def last_check(hass: HomeAssistant, entry: MockConfigEntry) -> dict[str, Any]:
    """The last-check sensor's attributes."""
    return dict(state(hass, entry, "sensor", KEY_LAST_CHECK).attributes)


def reauth_flows(hass: HomeAssistant) -> list[Any]:
    return [
        flow
        for flow in hass.config_entries.flow.async_progress_by_handler(DOMAIN)
        if flow["context"]["source"] == SOURCE_REAUTH
    ]


async def press(hass: HomeAssistant, entry: MockConfigEntry) -> None:
    """The Check now button."""
    await hass.services.async_call(
        "button",
        "press",
        {"entity_id": entity_id(hass, entry, "button", KEY_CHECK_NOW)},
        blocking=True,
    )
    await settle(hass)


def storage_key(entry: MockConfigEntry) -> str:
    return f"{DOMAIN}.{entry.entry_id}"


def stored(hass_storage: dict[str, Any], entry: MockConfigEntry) -> dict[str, Any]:
    """The persisted tracker state, as written."""
    return json.loads(json.dumps(hass_storage[storage_key(entry)]["data"]))
