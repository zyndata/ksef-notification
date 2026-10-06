"""Skeleton tests: manifest sanity, config-entry setup/unload."""

from __future__ import annotations

import json
from pathlib import Path

from homeassistant.config_entries import ConfigEntryState
from homeassistant.core import HomeAssistant
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.ksef_notification.const import DOMAIN, INTEGRATION_NAME

MANIFEST = json.loads(
    (Path(__file__).parents[1] / "custom_components" / DOMAIN / "manifest.json").read_text(
        encoding="utf-8"
    )
)


def test_manifest_matches_const() -> None:
    """The manifest and const.py must agree on the domain and the name."""
    assert MANIFEST["domain"] == DOMAIN
    assert MANIFEST["name"] == INTEGRATION_NAME
    assert MANIFEST["config_flow"] is True
    assert MANIFEST["iot_class"] == "cloud_polling"


def test_manifest_requires_nothing_beyond_home_assistant() -> None:
    """Runtime dependencies are limited to what Home Assistant core already ships."""
    assert MANIFEST["requirements"] == []


async def test_setup_and_unload_entry(hass: HomeAssistant) -> None:
    """The skeleton config entry sets up and unloads cleanly."""
    entry = MockConfigEntry(domain=DOMAIN, title="KSeF 0000000000")
    entry.add_to_hass(hass)

    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    assert entry.state is ConfigEntryState.LOADED

    assert await hass.config_entries.async_unload(entry.entry_id)
    await hass.async_block_till_done()
    assert entry.state is ConfigEntryState.NOT_LOADED
