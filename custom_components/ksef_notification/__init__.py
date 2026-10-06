"""The KSeF Notification integration.

Watches KSeF, the Polish national e-invoice system, for new cost invoices issued to the
configured company and pushes the fields the user selected to one phone. See
docs/ARCHITECTURE.md.
"""

from __future__ import annotations

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import EVENT_HOMEASSISTANT_STOP, Platform
from homeassistant.core import Event, HomeAssistant
from homeassistant.helpers import issue_registry as ir
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from .client import KsefClient
from .const import (
    CONF_ENVIRONMENT,
    CONF_NIP,
    CONF_TOKEN,
    DOMAIN,
    ISSUE_ACCOUNT_BLOCKED,
    ISSUE_NOTIFY_SERVICE_MISSING,
)
from .coordinator import KsefCoordinator
from .notifier import issue_id
from .storage import TrackerStore

PLATFORMS: list[Platform] = [Platform.BUTTON, Platform.SENSOR, Platform.SWITCH]


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Set up one company's checks.

    The first refresh makes no request: the coordinator starts with checks off and the
    switch, set up right after, restores the real position. No update listener — the
    options flow is an `OptionsFlowWithReload`, which reloads the entry itself.
    """
    client = KsefClient(
        async_get_clientsession(hass),
        entry.data[CONF_ENVIRONMENT],
        entry.data[CONF_NIP],
        entry.data[CONF_TOKEN],
    )
    store = TrackerStore(hass, entry.entry_id)
    coordinator = KsefCoordinator(hass, entry, client, store, await store.async_load())
    await coordinator.async_config_entry_first_refresh()
    entry.runtime_data = coordinator

    async def _close_on_stop(_: Event) -> None:
        await client.async_close()

    # Home Assistant does not unload entries when it stops; the session is revoked anyway.
    entry.async_on_unload(hass.bus.async_listen_once(EVENT_HOMEASSISTANT_STOP, _close_on_stop))
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    return True


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Stop checking, revoke the KSeF session, drop this entry's repair issues."""
    if not await hass.config_entries.async_unload_platforms(entry, PLATFORMS):
        return False
    coordinator: KsefCoordinator = entry.runtime_data
    await coordinator.async_shutdown()
    await coordinator.client.async_close()
    _delete_issues(hass, entry.entry_id)
    return True


async def async_remove_entry(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """The stored invoice state goes with the entry."""
    await TrackerStore(hass, entry.entry_id).async_remove()
    _delete_issues(hass, entry.entry_id)


def _delete_issues(hass: HomeAssistant, entry_id: str) -> None:
    for key in (ISSUE_ACCOUNT_BLOCKED, ISSUE_NOTIFY_SERVICE_MISSING):
        ir.async_delete_issue(hass, DOMAIN, issue_id(key, entry_id))
