"""Manifest sanity. Setup, unload and removal are exercised in test_coordinator.py."""

from __future__ import annotations

import json
from pathlib import Path

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
