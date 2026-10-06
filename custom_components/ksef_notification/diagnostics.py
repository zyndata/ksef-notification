"""Config entry diagnostics with every secret and invoice value redacted.

What is shown: options, environment, the cursor, how many invoices are remembered, the last
check's outcome and the request counters. What never is: the KSeF token, the NIP, the entry
title (it defaults to `KSeF <nip>`), the phone's service name, any hash, any invoice value.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from homeassistant.components.diagnostics import async_redact_data

from .const import CONF_NIP, CONF_NOTIFY_SERVICE, CONF_TOKEN

if TYPE_CHECKING:
    from datetime import datetime

    from homeassistant.config_entries import ConfigEntry
    from homeassistant.core import HomeAssistant

    from .coordinator import KsefCoordinator

TO_REDACT = {CONF_TOKEN, CONF_NIP, CONF_NOTIFY_SERVICE, "title", "unique_id"}


async def async_get_config_entry_diagnostics(
    hass: HomeAssistant, entry: ConfigEntry
) -> dict[str, Any]:
    coordinator: KsefCoordinator = entry.runtime_data
    data = coordinator.data
    state = coordinator.tracker_state
    invoice = data.last_invoice
    return {
        "entry": async_redact_data(
            {
                "title": entry.title,
                "unique_id": entry.unique_id,
                "data": dict(entry.data),
                "options": dict(entry.options),
                "pref_disable_polling": entry.pref_disable_polling,
            },
            TO_REDACT,
        ),
        "tracker": {
            "cursor": _iso(state.cursor),
            "seen_entries": len(state.seen),
            "deferred_invoices": len(state.deferrals),
        },
        "status": {
            "enabled": data.enabled,
            "halted": coordinator.halted,
            "outcome": data.outcome,
            "last_success": _iso(data.last_success),
            "last_attempt": _iso(data.last_attempt),
            "next_check": _iso(data.next_check),
            "new_invoices": data.new_invoices,
            "metadata_requests_last_hour": data.metadata_requests_last_hour,
            "download_requests_last_hour": data.download_requests_last_hour,
        },
        "last_invoice": None
        if invoice is None
        else {"details": invoice.details_status.value, "form_code": invoice.form_code},
    }


def _iso(moment: datetime | None) -> str | None:
    return moment.isoformat() if moment else None
