"""No invoice value reaches Home Assistant's database through the last-invoice sensor.

The sensor's attributes are the selected invoice fields; `_unrecorded_attributes` must keep
every one of them out of the recorder (docs/ARCHITECTURE.md § What Home Assistant may record
on its own). Checked against a real recorder, not against the class attribute.
"""

from __future__ import annotations

from datetime import timedelta
from functools import partial

import pytest
from freezegun.api import FrozenDateTimeFactory
from homeassistant.components.recorder import Recorder, get_instance
from homeassistant.components.recorder.history import get_significant_states
from homeassistant.core import HomeAssistant
from pytest_homeassistant_custom_component.common import async_mock_service
from pytest_homeassistant_custom_component.components.recorder.common import (
    async_wait_recording_done,
)

from custom_components.ksef_notification.const import CONF_FIELDS, KEY_LAST_INVOICE
from custom_components.ksef_notification.core.fields import FIELD_KEYS

from .ha_setup import INTERVAL, PHONE, World, advance, entity_id, make_entry, setup
from .ksef_fake import START, FakeKsef


@pytest.fixture(autouse=True)
def auto_enable_custom_integrations(
    recorder_mock: Recorder, enable_custom_integrations: None
) -> None:
    """The recorder has to exist before `hass` does; the suite-wide version creates `hass` first."""


async def test_last_invoice_attributes_are_not_recorded(
    recorder_mock: Recorder, hass: HomeAssistant, freezer: FrozenDateTimeFactory, ha_ksef: FakeKsef
) -> None:
    world = World(ha_ksef)
    pushes = async_mock_service(hass, "notify", PHONE)
    entry = await setup(hass, make_entry({CONF_FIELDS: list(FIELD_KEYS)}))
    world.add(2)
    await advance(hass, freezer, INTERVAL)
    assert len(pushes) == 1
    sensor = entity_id(hass, entry, "sensor", KEY_LAST_INVOICE)
    live = hass.states.get(sensor)
    assert live is not None
    assert set(FIELD_KEYS) <= set(live.attributes)  # they are there, live

    await async_wait_recording_done(hass)
    history = await get_instance(hass).async_add_executor_job(
        partial(
            get_significant_states,
            hass,
            START - timedelta(hours=1),
            None,
            [sensor],
            significant_changes_only=False,
        )
    )

    recorded = history[sensor]
    assert recorded[-1].state == live.state  # the sensor itself is recorded…
    for state in recorded:
        assert set(state.attributes) <= {"device_class", "friendly_name"}  # …its fields are not
