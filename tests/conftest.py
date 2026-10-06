"""Shared test fixtures.

Every test in this suite runs offline. Fixtures under `tests/fixtures/` are synthetic:
written by hand to the shape of a real KSeF response, never a recorded one.
"""

from __future__ import annotations

import pytest


@pytest.fixture(autouse=True)
def auto_enable_custom_integrations(enable_custom_integrations: None) -> None:
    """Let the HA test harness load custom_components/ in every test."""
