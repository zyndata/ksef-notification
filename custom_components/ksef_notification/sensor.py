"""The last-invoice sensor and the last-check diagnostic sensor.

Contract in docs/CONFIG.md § Entities. The last-invoice sensor's attributes are invoice data:
they are kept out of the recorder (`_unrecorded_attributes`) and the sensor is not restored,
so no invoice value reaches Home Assistant's database through it.
"""

from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING, Any

from homeassistant.components.sensor import SensorDeviceClass, SensorEntity
from homeassistant.const import EntityCategory

from .const import KEY_LAST_CHECK, KEY_LAST_INVOICE
from .core.fields import FIELD_KEYS
from .core.formatter import field_values
from .entity import KsefEntity

if TYPE_CHECKING:
    from homeassistant.config_entries import ConfigEntry
    from homeassistant.core import HomeAssistant
    from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

    from .coordinator import KsefCoordinator

ATTR_KSEF_NUMBER = "ksef_number"
ATTR_CURRENCY = "currency"


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    coordinator: KsefCoordinator = entry.runtime_data
    async_add_entities(
        [KsefLastInvoiceSensor(coordinator, entry), KsefLastCheckSensor(coordinator, entry)]
    )


class KsefLastInvoiceSensor(KsefEntity, SensorEntity):
    """When the most recently notified invoice was received in KSeF; its fields as attributes."""

    _attr_device_class = SensorDeviceClass.TIMESTAMP
    _unrecorded_attributes = frozenset({ATTR_KSEF_NUMBER, ATTR_CURRENCY, *FIELD_KEYS})

    def __init__(self, coordinator: KsefCoordinator, entry: ConfigEntry) -> None:
        super().__init__(coordinator, entry, KEY_LAST_INVOICE)

    @property
    def native_value(self) -> datetime | None:
        invoice = self.coordinator.data.last_invoice
        if invoice is None:
            return None
        # acquisitionDate is required by the API; the storage date is the nearest stand-in.
        return invoice.acquisition_date or invoice.storage_date

    @property
    def extra_state_attributes(self) -> dict[str, Any] | None:
        invoice = self.coordinator.data.last_invoice
        if invoice is None:
            return None
        return {
            ATTR_KSEF_NUMBER: invoice.ksef_number,
            **field_values(invoice, self.coordinator.selection),
        }


class KsefLastCheckSensor(KsefEntity, SensorEntity):
    """When KSeF last answered a check; how the latest attempt went and what it cost."""

    _attr_device_class = SensorDeviceClass.TIMESTAMP
    _attr_entity_category = EntityCategory.DIAGNOSTIC
    # They change every check without meaning anything on their own.
    _unrecorded_attributes = frozenset(
        {"last_attempt", "next_check", "metadata_requests_last_hour", "download_requests_last_hour"}
    )

    def __init__(self, coordinator: KsefCoordinator, entry: ConfigEntry) -> None:
        super().__init__(coordinator, entry, KEY_LAST_CHECK)

    @property
    def native_value(self) -> datetime | None:
        return self.coordinator.data.last_success

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        data = self.coordinator.data
        return {
            "outcome": data.outcome.value if data.outcome else None,
            "last_attempt": _iso(data.last_attempt),
            "next_check": _iso(data.next_check),
            "new_invoices": data.new_invoices,
            "metadata_requests_last_hour": data.metadata_requests_last_hour,
            "download_requests_last_hour": data.download_requests_last_hour,
        }


def _iso(moment: datetime | None) -> str | None:
    return moment.isoformat() if moment else None
