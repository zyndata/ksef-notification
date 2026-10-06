"""The throttled Check now button.

Contract in docs/CONFIG.md § Entities: refused, with the earliest time, within
MIN_QUERY_GAP of the previous check.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from homeassistant.components.button import ButtonEntity

from .const import KEY_CHECK_NOW
from .entity import KsefEntity

if TYPE_CHECKING:
    from homeassistant.config_entries import ConfigEntry
    from homeassistant.core import HomeAssistant
    from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

    from .coordinator import KsefCoordinator


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    coordinator: KsefCoordinator = entry.runtime_data
    async_add_entities([KsefCheckNowButton(coordinator, entry)])


class KsefCheckNowButton(KsefEntity, ButtonEntity):
    """Asks KSeF for new invoices now."""

    def __init__(self, coordinator: KsefCoordinator, entry: ConfigEntry) -> None:
        super().__init__(coordinator, entry, KEY_CHECK_NOW)

    async def async_press(self) -> None:
        await self.coordinator.async_check_now()
