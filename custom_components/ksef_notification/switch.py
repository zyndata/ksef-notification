"""The notifications switch, restored across restarts.

Contract in docs/CONFIG.md § Entities. Off: no requests to KSeF at all. Default on.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from homeassistant.components.switch import SwitchEntity
from homeassistant.const import STATE_ON, EntityCategory
from homeassistant.helpers.restore_state import RestoreEntity

from .const import KEY_NOTIFICATIONS
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
    async_add_entities([KsefNotificationsSwitch(coordinator, entry)])


class KsefNotificationsSwitch(KsefEntity, SwitchEntity, RestoreEntity):
    """Turns checking — and with it notifying — on and off."""

    _attr_entity_category = EntityCategory.CONFIG

    def __init__(self, coordinator: KsefCoordinator, entry: ConfigEntry) -> None:
        super().__init__(coordinator, entry, KEY_NOTIFICATIONS)

    async def async_added_to_hass(self) -> None:
        """Restore the previous position and hand it to the coordinator."""
        await super().async_added_to_hass()
        last_state = await self.async_get_last_state()
        enabled = last_state is None or last_state.state == STATE_ON
        await self.coordinator.async_set_enabled(enabled)

    @property
    def is_on(self) -> bool:
        return self.coordinator.enabled

    async def async_turn_on(self, **kwargs: Any) -> None:
        await self.coordinator.async_set_enabled(True)

    async def async_turn_off(self, **kwargs: Any) -> None:
        await self.coordinator.async_set_enabled(False)
