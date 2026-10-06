"""Shared test fixtures.

Every test in this suite runs offline. Fixtures under `tests/fixtures/` are synthetic:
written by hand to the shape of a real KSeF response, never a recorded one.
"""

from __future__ import annotations

from collections.abc import AsyncIterator, Iterator
from functools import partial
from unittest.mock import patch

import aiohttp
import pytest
from freezegun.api import FrozenDateTimeFactory
from homeassistant.core import Event, HomeAssistant, ServiceCall
from pytest_homeassistant_custom_component.common import async_capture_events, async_mock_service
from pytest_homeassistant_custom_component.test_util.aiohttp import AiohttpClientMocker

from custom_components.ksef_notification.client import Clock, KsefClient
from custom_components.ksef_notification.const import ENV_TEST, EVENT_INVOICE

from .ha_setup import PHONE, HaClock, World
from .ksef_fake import KSEF_TOKEN, NIP, START, FakeClock, FakeKsef, signing_material

# Built now, before any test freezes time: cryptography's certificate builder refuses
# freezegun's datetime class.
signing_material()


@pytest.fixture(autouse=True)
def auto_enable_custom_integrations(enable_custom_integrations: None) -> None:
    """Let the HA test harness load custom_components/ in every test."""


@pytest.fixture
def clock() -> FakeClock:
    return FakeClock()


@pytest.fixture
async def session(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker
) -> AsyncIterator[aiohttp.ClientSession]:
    session = aioclient_mock.create_session(hass.loop)
    yield session
    await session.close()


@pytest.fixture
def ksef(aioclient_mock: AiohttpClientMocker, clock: FakeClock) -> FakeKsef:
    return FakeKsef(aioclient_mock, clock)


@pytest.fixture
def ha_ksef(
    aioclient_mock: AiohttpClientMocker, freezer: FrozenDateTimeFactory
) -> Iterator[FakeKsef]:
    """The scripted KSeF for the integration as Home Assistant sets it up, on HA's frozen clock."""
    freezer.move_to(START)
    clock = HaClock()
    factory = partial(KsefClient, clock=Clock(clock.utcnow, clock.monotonic, clock.sleep))
    with patch("custom_components.ksef_notification.KsefClient", factory):
        yield FakeKsef(aioclient_mock, clock)


@pytest.fixture
def world(ha_ksef: FakeKsef) -> World:
    """KSeF's invoice store behind the metadata route."""
    return World(ha_ksef)


@pytest.fixture
def pushes(hass: HomeAssistant) -> list[ServiceCall]:
    """Every call of the configured phone's notify service."""
    return async_mock_service(hass, "notify", PHONE)


@pytest.fixture
def events(hass: HomeAssistant) -> list[Event]:
    return async_capture_events(hass, EVENT_INVOICE)


@pytest.fixture
def client(session: aiohttp.ClientSession, ksef: FakeKsef, clock: FakeClock) -> KsefClient:
    return KsefClient(
        session, ENV_TEST, NIP, KSEF_TOKEN, clock=Clock(clock.utcnow, clock.monotonic, clock.sleep)
    )
