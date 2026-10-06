"""Shared entity base: the one service device per config entry.

The device is named after the entry title, which is what tells two companies (or the same
company in two environments) apart. Every entity stays available through a failed check:
only the last-check sensor reports it, as its `outcome` (docs/ARCHITECTURE.md § Coordinator
scheduling).
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from homeassistant.helpers.device_registry import DeviceEntryType, DeviceInfo
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN, INTEGRATION_NAME

if TYPE_CHECKING:
    from homeassistant.config_entries import ConfigEntry

    from .coordinator import KsefCoordinator


class KsefEntity(CoordinatorEntity["KsefCoordinator"]):
    """Common identity and device for every KSeF Notification entity."""

    _attr_has_entity_name = True

    def __init__(self, coordinator: KsefCoordinator, entry: ConfigEntry, key: str) -> None:
        super().__init__(coordinator)
        self._attr_translation_key = key
        self._attr_unique_id = f"{entry.entry_id}_{key}"
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, entry.entry_id)},
            entry_type=DeviceEntryType.SERVICE,
            manufacturer=INTEGRATION_NAME,
            name=entry.title,
        )

    @property
    def available(self) -> bool:
        return True
