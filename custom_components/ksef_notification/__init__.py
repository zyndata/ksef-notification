"""The KSeF Notification integration.

Watches KSeF, the Polish national e-invoice system, for new cost invoices issued to the
configured company and pushes the fields the user selected to one phone. See
docs/ARCHITECTURE.md.
"""

from __future__ import annotations

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import Platform
from homeassistant.core import HomeAssistant

# switch, sensor and button platforms are added in phase 6
PLATFORMS: list[Platform] = []


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Set up KSeF Notification from a config entry (coordinator wiring lands in phase 6)."""
    return True


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Unload a config entry."""
    return True
