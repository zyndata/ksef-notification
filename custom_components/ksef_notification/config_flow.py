"""Config flow for the KSeF Notification integration.

The three-step wizard (KSeF access → notification → behaviour), the options flow (the last
two steps again) and the re-authentication flow, as documented in docs/CONFIG.md. The KSeF
token is checked against KSeF with `KsefClient.validate()` before it is accepted.
"""

from __future__ import annotations

import logging
import re
import traceback
from collections.abc import Mapping
from pathlib import Path
from typing import Any

import voluptuous as vol
from homeassistant.config_entries import (
    ConfigEntry,
    ConfigFlow,
    ConfigFlowResult,
    OptionsFlowWithReload,
)
from homeassistant.const import UnitOfTime
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.selector import (
    NumberSelector,
    NumberSelectorConfig,
    NumberSelectorMode,
    SelectSelector,
    SelectSelectorConfig,
    SelectSelectorMode,
    TextSelector,
    TextSelectorConfig,
    TextSelectorType,
)

from .client import AuthErrorReason, KsefAuthError, KsefClient, KsefError, KsefRateLimitError
from .const import (
    CHECK_INTERVAL_STEP_MIN,
    CONF_CHECK_INTERVAL_MIN,
    CONF_ENVIRONMENT,
    CONF_FIELDS,
    CONF_NIP,
    CONF_NOTIFY_SERVICE,
    CONF_TOKEN,
    DEFAULT_CHECK_INTERVAL_MIN,
    DEFAULT_ENVIRONMENT,
    DEFAULT_FIELDS,
    DOMAIN,
    ENV_PROD,
    ENVIRONMENTS,
    MAX_CHECK_INTERVAL_MIN,
    MIN_CHECK_INTERVAL_MIN,
)
from .core.fields import FIELD_KEYS, ordered

_LOGGER = logging.getLogger(__name__)

NOTIFY_DOMAIN = "notify"
PHONE_SERVICE_PREFIX = "mobile_app_"
_PHONE_SERVICE = re.compile(rf"{PHONE_SERVICE_PREFIX}[a-z0-9_]+")

_NIP_WEIGHTS = (6, 5, 7, 2, 3, 4, 5, 6, 7)
_NIP_SEPARATORS = re.compile(r"[\s-]")
_NIP = re.compile(r"[0-9]{10}")

_AUTH_ERRORS: Mapping[AuthErrorReason, str] = {
    AuthErrorReason.TOKEN_INVALID: "invalid_token",
    AuthErrorReason.NO_PERMISSION: "no_permission",
    AuthErrorReason.BLOCKED: "account_blocked",
}


def normalize_nip(raw: str) -> str | None:
    """The NIP as 10 digits, or None when it is not a valid NIP.

    Spaces and dashes are accepted; the checksum is the weighted sum of the first nine
    digits mod 11, which must equal the tenth (a remainder of 10 is never valid).
    """
    nip = _NIP_SEPARATORS.sub("", raw)
    if not _NIP.fullmatch(nip):
        return None
    *body, check = (int(digit) for digit in nip)
    checksum = sum(digit * weight for digit, weight in zip(body, _NIP_WEIGHTS, strict=True)) % 11
    return nip if checksum == check else None


def normalize_notify_service(raw: str) -> str | None:
    """A `mobile_app_*` notify service without the `notify.` prefix, or None."""
    service = raw.strip().removeprefix(f"{NOTIFY_DOMAIN}.")
    return service if _PHONE_SERVICE.fullmatch(service) else None


def entry_title(environment: str, nip: str) -> str:
    """`KSeF <nip>`, with the environment added outside production."""
    title = f"KSeF {nip}"
    return title if environment == ENV_PROD else f"{title} ({environment.upper()})"


async def validate_access(
    hass: HomeAssistant, environment: str, nip: str, token: str
) -> str | None:
    """Authenticate and run one metadata query; the error key, or None on success."""
    client = KsefClient(async_get_clientsession(hass), environment, nip, token)
    try:
        await client.validate()
    except KsefAuthError as err:
        return _AUTH_ERRORS.get(err.reason, "invalid_token")
    except KsefRateLimitError:
        return "rate_limited"
    except KsefError as err:
        _LOGGER.debug("KSeF could not be reached while validating: %s", err)
        return "cannot_connect"
    except Exception as err:
        # Any failure must end in a form error. The message of an unexpected exception could
        # quote what it was given, so only its type and where it was raised are logged.
        frame = traceback.extract_tb(err.__traceback__)[-1]
        _LOGGER.error(
            "Unexpected %s while validating the KSeF token (%s:%s)",
            type(err).__name__,
            Path(frame.filename).name,
            frame.lineno,
        )
        return "unknown"
    return None


def _phone_services(hass: HomeAssistant) -> list[str]:
    return sorted(
        service
        for service in hass.services.async_services_for_domain(NOTIFY_DOMAIN)
        if service.startswith(PHONE_SERVICE_PREFIX)
    )


def _notification_schema(hass: HomeAssistant) -> vol.Schema:
    return vol.Schema(
        {
            vol.Required(CONF_NOTIFY_SERVICE): SelectSelector(
                SelectSelectorConfig(
                    options=_phone_services(hass),
                    custom_value=True,
                    mode=SelectSelectorMode.DROPDOWN,
                )
            ),
            vol.Required(CONF_FIELDS, default=list(DEFAULT_FIELDS)): SelectSelector(
                SelectSelectorConfig(
                    options=list(FIELD_KEYS),
                    multiple=True,
                    mode=SelectSelectorMode.LIST,
                    translation_key=CONF_FIELDS,
                )
            ),
        }
    )


def _behaviour_schema() -> vol.Schema:
    return vol.Schema(
        {
            vol.Required(
                CONF_CHECK_INTERVAL_MIN, default=DEFAULT_CHECK_INTERVAL_MIN
            ): NumberSelector(
                NumberSelectorConfig(
                    min=MIN_CHECK_INTERVAL_MIN,
                    max=MAX_CHECK_INTERVAL_MIN,
                    step=CHECK_INTERVAL_STEP_MIN,
                    mode=NumberSelectorMode.BOX,
                    unit_of_measurement=UnitOfTime.MINUTES,
                )
            ),
        }
    )


def _validate_notification(user_input: dict[str, Any]) -> tuple[dict[str, Any], dict[str, str]]:
    """The notification options as stored, and the form errors."""
    errors: dict[str, str] = {}
    service = normalize_notify_service(user_input[CONF_NOTIFY_SERVICE])
    if service is None:
        errors[CONF_NOTIFY_SERVICE] = "invalid_notify_service"
    fields = ordered(user_input[CONF_FIELDS])
    if not fields:
        errors[CONF_FIELDS] = "no_fields"
    return {CONF_NOTIFY_SERVICE: service, CONF_FIELDS: list(fields)}, errors


def _validate_behaviour(user_input: dict[str, Any]) -> tuple[dict[str, Any], dict[str, str]]:
    """The behaviour options as stored, and the form errors."""
    errors: dict[str, str] = {}
    interval = user_input[CONF_CHECK_INTERVAL_MIN]
    if interval != int(interval) or int(interval) % CHECK_INTERVAL_STEP_MIN:
        errors[CONF_CHECK_INTERVAL_MIN] = "invalid_interval"
    return {CONF_CHECK_INTERVAL_MIN: int(interval)}, errors


class KsefNotificationConfigFlow(ConfigFlow, domain=DOMAIN):
    """The setup wizard and the re-authentication flow."""

    VERSION = 1

    def __init__(self) -> None:
        self._data: dict[str, Any] = {}
        self._options: dict[str, Any] = {}

    @staticmethod
    @callback
    def async_get_options_flow(config_entry: ConfigEntry) -> KsefNotificationOptionsFlow:
        return KsefNotificationOptionsFlow()

    async def async_step_user(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        """Step 1: KSeF access — environment, NIP and token, checked against KSeF."""
        errors: dict[str, str] = {}
        if user_input is not None:
            environment = user_input[CONF_ENVIRONMENT]
            nip = normalize_nip(user_input[CONF_NIP])
            token = user_input[CONF_TOKEN].strip()
            if nip is None:
                errors[CONF_NIP] = "invalid_nip"
            elif not token:
                errors[CONF_TOKEN] = "invalid_token"
            else:
                # Before any request to KSeF: a duplicate must not cost an authentication.
                await self.async_set_unique_id(f"{environment}_{nip}")
                self._abort_if_unique_id_configured()
                if error := await validate_access(self.hass, environment, nip, token):
                    errors["base"] = error
                else:
                    self._data = {CONF_ENVIRONMENT: environment, CONF_NIP: nip, CONF_TOKEN: token}
                    return await self.async_step_notification()

        schema = vol.Schema(
            {
                vol.Required(CONF_ENVIRONMENT, default=DEFAULT_ENVIRONMENT): SelectSelector(
                    SelectSelectorConfig(
                        options=list(ENVIRONMENTS),
                        mode=SelectSelectorMode.LIST,
                        translation_key=CONF_ENVIRONMENT,
                    )
                ),
                vol.Required(CONF_NIP): TextSelector(),
                vol.Required(CONF_TOKEN): TextSelector(
                    TextSelectorConfig(type=TextSelectorType.PASSWORD)
                ),
            }
        )
        if user_input is not None:
            # Keep what was typed, except the token.
            schema = self.add_suggested_values_to_schema(
                schema, {key: user_input[key] for key in (CONF_ENVIRONMENT, CONF_NIP)}
            )
        return self.async_show_form(step_id="user", data_schema=schema, errors=errors)

    async def async_step_notification(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Step 2: the phone and the invoice fields."""
        errors: dict[str, str] = {}
        schema = _notification_schema(self.hass)
        if user_input is not None:
            options, errors = _validate_notification(user_input)
            if not errors:
                self._options.update(options)
                return await self.async_step_behaviour()
            schema = self.add_suggested_values_to_schema(schema, user_input)
        return self.async_show_form(step_id="notification", data_schema=schema, errors=errors)

    async def async_step_behaviour(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Step 3: how often to check; creates the entry."""
        errors: dict[str, str] = {}
        schema = _behaviour_schema()
        if user_input is not None:
            options, errors = _validate_behaviour(user_input)
            if not errors:
                self._options.update(options)
                return self.async_create_entry(
                    title=entry_title(self._data[CONF_ENVIRONMENT], self._data[CONF_NIP]),
                    data=self._data,
                    options=self._options,
                )
            schema = self.add_suggested_values_to_schema(schema, user_input)
        return self.async_show_form(step_id="behaviour", data_schema=schema, errors=errors)

    async def async_step_reauth(self, entry_data: Mapping[str, Any]) -> ConfigFlowResult:
        """KSeF refused the stored token."""
        return await self.async_step_reauth_confirm()

    async def async_step_reauth_confirm(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """A new KSeF token for the same environment and NIP; the invoice state is kept."""
        entry = self._get_reauth_entry()
        errors: dict[str, str] = {}
        if user_input is not None:
            token = user_input[CONF_TOKEN].strip()
            if not token:
                errors[CONF_TOKEN] = "invalid_token"
            elif error := await validate_access(
                self.hass, entry.data[CONF_ENVIRONMENT], entry.data[CONF_NIP], token
            ):
                errors["base"] = error
            else:
                return self.async_update_reload_and_abort(entry, data_updates={CONF_TOKEN: token})

        return self.async_show_form(
            step_id="reauth_confirm",
            data_schema=vol.Schema(
                {
                    vol.Required(CONF_TOKEN): TextSelector(
                        TextSelectorConfig(type=TextSelectorType.PASSWORD)
                    )
                }
            ),
            description_placeholders={"entry_title": entry.title},
            errors=errors,
        )


class KsefNotificationOptionsFlow(OptionsFlowWithReload):
    """Steps 2 and 3 of the wizard again; the company's identity is not editable here."""

    def __init__(self) -> None:
        self._options: dict[str, Any] = {}

    async def async_step_init(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        return await self.async_step_notification()

    async def async_step_notification(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        errors: dict[str, str] = {}
        if user_input is not None:
            options, errors = _validate_notification(user_input)
            if not errors:
                self._options.update(options)
                return await self.async_step_behaviour()
        schema = self.add_suggested_values_to_schema(
            _notification_schema(self.hass), user_input or self.config_entry.options
        )
        return self.async_show_form(step_id="notification", data_schema=schema, errors=errors)

    async def async_step_behaviour(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        errors: dict[str, str] = {}
        if user_input is not None:
            options, errors = _validate_behaviour(user_input)
            if not errors:
                self._options.update(options)
                return self.async_create_entry(data={**self.config_entry.options, **self._options})
        schema = self.add_suggested_values_to_schema(
            _behaviour_schema(), user_input or self.config_entry.options
        )
        return self.async_show_form(step_id="behaviour", data_schema=schema, errors=errors)
