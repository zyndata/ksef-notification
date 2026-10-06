"""Shared test fixtures.

Every test in this suite runs offline. Fixtures under `tests/fixtures/` are synthetic:
written by hand to the shape of a real KSeF response, never a recorded one.
"""

from __future__ import annotations

from collections.abc import AsyncIterator

import aiohttp
import pytest
from homeassistant.core import HomeAssistant
from pytest_homeassistant_custom_component.test_util.aiohttp import AiohttpClientMocker

from custom_components.ksef_notification.client import Clock, KsefClient
from custom_components.ksef_notification.const import ENV_TEST

from .ksef_fake import KSEF_TOKEN, NIP, FakeClock, FakeKsef


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
def client(session: aiohttp.ClientSession, ksef: FakeKsef, clock: FakeClock) -> KsefClient:
    return KsefClient(
        session, ENV_TEST, NIP, KSEF_TOKEN, clock=Clock(clock.utcnow, clock.monotonic, clock.sleep)
    )
