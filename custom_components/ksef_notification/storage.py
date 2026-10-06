"""TrackerStore: the tracker state in Home Assistant's Store, saved after every push.

What is written, and what never is: docs/ARCHITECTURE.md § What is persisted. The file holds
the cursor and the hashed `seen` map — no invoice content.
"""

from __future__ import annotations

import logging
from typing import Any

from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.storage import Store

from .const import STORAGE_KEY_PREFIX, STORAGE_VERSION
from .core.tracker import TrackerState

_LOGGER = logging.getLogger(__name__)


class TrackerStore:
    """`.storage/ksef_notification.<entry_id>`, one per config entry."""

    def __init__(self, hass: HomeAssistant, entry_id: str) -> None:
        self._store: Store[dict[str, Any]] = Store(
            hass, STORAGE_VERSION, f"{STORAGE_KEY_PREFIX}.{entry_id}"
        )

    async def async_load(self) -> TrackerState:
        """The stored state; missing or unreadable means a baseline (an empty state)."""
        try:
            data = await self._store.async_load()
        except (HomeAssistantError, NotImplementedError) as err:
            _LOGGER.warning(
                "Stored invoice state unreadable (%s); starting afresh", type(err).__name__
            )
            return TrackerState()
        return TrackerState.from_dict(data)

    async def async_save(self, state: TrackerState) -> None:
        """Written at once, not delayed: the write after a push is what prevents a repeat."""
        await self._store.async_save(state.to_dict())

    async def async_remove(self) -> None:
        await self._store.async_remove()
