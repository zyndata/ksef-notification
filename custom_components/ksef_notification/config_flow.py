"""Config flow for the KSeF Notification integration.

The three-step wizard, the options flow and the re-authentication flow (docs/CONFIG.md)
are implemented in phase 5. Until then the flow aborts, so the integration cannot be
half-configured.
"""

from __future__ import annotations

from typing import Any

from homeassistant.config_entries import ConfigFlow, ConfigFlowResult

from .const import DOMAIN


class KsefNotificationConfigFlow(ConfigFlow, domain=DOMAIN):
    """Handle the KSeF Notification config flow."""

    VERSION = 1

    async def async_step_user(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        """Abort until the wizard is implemented (phase 5)."""
        return self.async_abort(reason="not_implemented")
