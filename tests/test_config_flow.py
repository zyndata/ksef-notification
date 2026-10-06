"""The setup wizard, the options flow and the re-authentication flow (docs/CONFIG.md).

The flow talks to the scripted KSeF of `ksef_fake.py` through Home Assistant's own client
session, so every error below travels the real path: KSeF reply → client error → form error.
"""

from __future__ import annotations

import logging
from collections.abc import Iterator
from functools import partial
from typing import Any
from unittest.mock import patch

import pytest
from homeassistant.config_entries import SOURCE_USER, ConfigEntryState
from homeassistant.core import HomeAssistant, ServiceCall
from homeassistant.data_entry_flow import FlowResult, FlowResultType, InvalidData
from pytest_homeassistant_custom_component.common import MockConfigEntry, start_reauth_flow

from custom_components.ksef_notification import config_flow
from custom_components.ksef_notification.client import Clock, KsefClient
from custom_components.ksef_notification.const import (
    CONF_CHECK_INTERVAL_MIN,
    CONF_ENVIRONMENT,
    CONF_FIELDS,
    CONF_NIP,
    CONF_NOTIFY_SERVICE,
    CONF_TOKEN,
    DEFAULT_FIELDS,
    DOMAIN,
    ENV_PROD,
    ENV_TEST,
)
from custom_components.ksef_notification.core.fields import FIELD_KEYS

from .ksef_fake import (
    AUTH_ROUTES,
    KSEF_TOKEN,
    NIP,
    FakeClock,
    FakeKsef,
    Reply,
    fixture,
    metadata_reply,
    status_reply,
)

NEW_TOKEN = "synthetic-ksef-token-1111"
ACCESS = {CONF_ENVIRONMENT: ENV_TEST, CONF_NIP: NIP, CONF_TOKEN: KSEF_TOKEN}
NOTIFICATION = {CONF_NOTIFY_SERVICE: "mobile_app_phone", CONF_FIELDS: list(DEFAULT_FIELDS)}
BEHAVIOUR = {CONF_CHECK_INTERVAL_MIN: 15}


@pytest.fixture(autouse=True)
def fake_time_client(clock: FakeClock) -> Iterator[None]:
    """The flow's client runs on the fake clock, so the auth status poll does not sleep."""
    factory = partial(KsefClient, clock=Clock(clock.utcnow, clock.monotonic, clock.sleep))
    with patch.object(config_flow, "KsefClient", factory):
        yield


@pytest.fixture(autouse=True)
def phones(hass: HomeAssistant) -> None:
    """Two registered phones and one notify service that is not a phone."""

    async def _noop(_: ServiceCall) -> None:
        return None

    for service in ("mobile_app_phone", "mobile_app_tablet", "persistent_notification"):
        hass.services.async_register("notify", service, _noop)


@pytest.fixture
def accept_any() -> Iterator[list[str]]:
    """Every token is accepted; records the environment validated. The scripted KSeF
    serves only the TEST base URL, so tests about another environment use this."""
    environments: list[str] = []

    async def _accept(_: HomeAssistant, environment: str, nip: str, token: str) -> None:
        environments.append(environment)

    with patch.object(config_flow, "validate_access", _accept):
        yield environments


async def _start(hass: HomeAssistant) -> FlowResult:
    return await hass.config_entries.flow.async_init(DOMAIN, context={"source": SOURCE_USER})


async def _configure(hass: HomeAssistant, result: FlowResult, data: dict[str, Any]) -> FlowResult:
    return await hass.config_entries.flow.async_configure(result["flow_id"], data)


async def _through_access(hass: HomeAssistant, access: dict[str, Any] = ACCESS) -> FlowResult:
    result = await _configure(hass, await _start(hass), access)
    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "notification"
    return result


def _entry(**data: Any) -> MockConfigEntry:
    return MockConfigEntry(
        domain=DOMAIN,
        title=f"KSeF {NIP} (TEST)",
        unique_id=f"{ENV_TEST}_{NIP}",
        data={**ACCESS, **data},
        options={**NOTIFICATION, **BEHAVIOUR},
        version=1,
    )


def _select_options(result: FlowResult, key: str) -> list[str]:
    for marker, validator in result["data_schema"].schema.items():
        if marker == key:
            return list(validator.config["options"])
    raise KeyError(key)


def _suggested(result: FlowResult, key: str) -> Any:
    for marker in result["data_schema"].schema:
        if marker == key:
            return (marker.description or {}).get("suggested_value")
    raise KeyError(key)


# --- the wizard ------------------------------------------------------------------------------


async def test_happy_path_creates_the_documented_entry(hass: HomeAssistant, ksef: FakeKsef) -> None:
    result = await _start(hass)
    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "user"
    assert result["errors"] == {}

    result = await _configure(hass, result, ACCESS)
    assert result["step_id"] == "notification"
    result = await _configure(hass, result, NOTIFICATION)
    assert result["step_id"] == "behaviour"
    result = await _configure(hass, result, BEHAVIOUR)

    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["title"] == f"KSeF {NIP} (TEST)"
    assert result["data"] == {"environment": "test", "nip": NIP, "token": KSEF_TOKEN}
    assert result["options"] == {
        "notify_service": "mobile_app_phone",
        "fields": ["seller_name", "invoice_number", "gross_amount", "due_date"],
        "check_interval_min": 15,
    }
    entry = result["result"]
    assert entry.unique_id == f"test_{NIP}"
    assert entry.version == 1
    await hass.async_block_till_done()
    assert entry.state is ConfigEntryState.LOADED


async def test_validation_is_one_authentication_one_query_and_a_revocation(
    hass: HomeAssistant, ksef: FakeKsef
) -> None:
    await _through_access(hass)

    assert ksef.routes() == [*AUTH_ROUTES, "metadata", "revoke"]
    metadata = ksef.last("metadata")
    assert metadata.query["pageSize"] == "10"
    assert metadata.body["subjectType"] == "Subject2"


async def test_production_title_has_no_environment_suffix(
    hass: HomeAssistant, accept_any: list[str]
) -> None:
    result = await _through_access(hass, {**ACCESS, CONF_ENVIRONMENT: ENV_PROD})
    assert accept_any == [ENV_PROD]
    result = await _configure(hass, result, NOTIFICATION)
    result = await _configure(hass, result, BEHAVIOUR)

    assert result["title"] == f"KSeF {NIP}"
    assert result["result"].unique_id == f"prod_{NIP}"


async def test_environment_is_offered_with_production_as_default(hass: HomeAssistant) -> None:
    result = await _start(hass)

    assert _select_options(result, CONF_ENVIRONMENT) == ["prod", "demo", "test"]
    marker = next(m for m in result["data_schema"].schema if m == CONF_ENVIRONMENT)
    assert marker.default() == "prod"


@pytest.mark.parametrize("typed", ["111 111 11 11", "111-111-11-11", " 1111111111 "])
async def test_nip_accepts_spaces_and_dashes(
    hass: HomeAssistant, ksef: FakeKsef, typed: str
) -> None:
    result = await _through_access(hass, {**ACCESS, CONF_NIP: typed})
    result = await _configure(hass, result, NOTIFICATION)
    result = await _configure(hass, result, BEHAVIOUR)

    assert result["data"][CONF_NIP] == NIP


@pytest.mark.parametrize(
    "typed",
    [
        "1111111112",  # checksum
        "9000000000",  # remainder 10 is never a valid check digit
        "111111111",  # 9 digits
        "11111111111",  # 11 digits
        "PL1111111111",
        "11111a1111",
        chr(0xFF11) * 10,  # full-width digits are digits to str.isdigit
        "",
    ],
)
async def test_invalid_nip_is_refused_without_asking_ksef(
    hass: HomeAssistant, ksef: FakeKsef, typed: str
) -> None:
    result = await _configure(hass, await _start(hass), {**ACCESS, CONF_NIP: typed})

    assert result["type"] is FlowResultType.FORM
    assert result["errors"] == {CONF_NIP: "invalid_nip"}
    assert ksef.calls == []


async def test_blank_token_is_refused_without_asking_ksef(
    hass: HomeAssistant, ksef: FakeKsef
) -> None:
    result = await _configure(hass, await _start(hass), {**ACCESS, CONF_TOKEN: "   "})

    assert result["errors"] == {CONF_TOKEN: "invalid_token"}
    assert ksef.calls == []


async def test_token_is_stored_without_surrounding_whitespace(
    hass: HomeAssistant, ksef: FakeKsef
) -> None:
    result = await _through_access(hass, {**ACCESS, CONF_TOKEN: f"  {KSEF_TOKEN}\n"})
    result = await _configure(hass, result, NOTIFICATION)
    result = await _configure(hass, result, BEHAVIOUR)

    assert result["data"][CONF_TOKEN] == KSEF_TOKEN


@pytest.mark.parametrize(
    ("route", "reply", "error"),
    [
        ("status", status_reply(450, "Token unieważniony"), "invalid_token"),
        (
            "status",
            status_reply(450, f"Token nie może być użyty w kontekście {NIP}"),
            "invalid_token",
        ),
        ("status", status_reply(415, "Brak przypisanych uprawnień"), "no_permission"),
        (
            "metadata",
            Reply(status=403, json=fixture("error_403_missing_permissions.json")),
            "no_permission",
        ),
        ("status", status_reply(470), "account_blocked"),
        ("status", status_reply(480), "account_blocked"),
        ("metadata", Reply(status=429, headers={"Retry-After": "120"}), "rate_limited"),
        ("challenge", Reply(status=429, headers={"Retry-After": "120"}), "rate_limited"),
        ("challenge", Reply(status=503), "cannot_connect"),
        ("challenge", Reply(exc=TimeoutError()), "cannot_connect"),
        ("status", status_reply(550), "cannot_connect"),
        ("metadata", Reply(status=500), "cannot_connect"),
        ("metadata", Reply(json={"unexpected": True}), "cannot_connect"),
    ],
)
async def test_each_ksef_failure_is_its_own_error(
    hass: HomeAssistant, ksef: FakeKsef, route: str, reply: Reply, error: str
) -> None:
    ksef.script(route, reply)

    result = await _configure(hass, await _start(hass), ACCESS)

    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "user"
    assert result["errors"] == {"base": error}
    assert not hass.config_entries.async_entries(DOMAIN)


async def test_403_security_block_is_account_blocked(hass: HomeAssistant, ksef: FakeKsef) -> None:
    body = fixture("error_403_missing_permissions.json")
    body["reasonCode"] = "security-service-blocked"
    ksef.script("metadata", Reply(status=403, json=body))

    result = await _configure(hass, await _start(hass), ACCESS)

    assert result["errors"] == {"base": "account_blocked"}


async def test_unexpected_error_is_unknown_and_logs_no_message(
    hass: HomeAssistant, ksef: FakeKsef, caplog: pytest.LogCaptureFixture
) -> None:
    with patch.object(KsefClient, "validate", side_effect=RuntimeError(KSEF_TOKEN)):
        result = await _configure(hass, await _start(hass), ACCESS)

    assert result["errors"] == {"base": "unknown"}
    assert "Unexpected RuntimeError while validating the KSeF token" in caplog.text
    assert KSEF_TOKEN not in caplog.text


async def test_a_failed_attempt_keeps_nip_and_environment_but_not_the_token(
    hass: HomeAssistant, ksef: FakeKsef
) -> None:
    ksef.script("status", status_reply(450), status_reply(200))

    result = await _configure(hass, await _start(hass), ACCESS)
    assert result["errors"] == {"base": "invalid_token"}
    assert _suggested(result, CONF_NIP) == NIP
    assert _suggested(result, CONF_ENVIRONMENT) == ENV_TEST
    assert _suggested(result, CONF_TOKEN) is None

    result = await _configure(hass, result, ACCESS)
    assert result["step_id"] == "notification"


async def test_same_company_and_environment_aborts_before_any_request(
    hass: HomeAssistant, ksef: FakeKsef
) -> None:
    _entry().add_to_hass(hass)

    result = await _configure(hass, await _start(hass), {**ACCESS, CONF_NIP: "111-111-11-11"})

    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "already_configured"
    assert ksef.calls == []


async def test_same_company_in_another_environment_is_allowed(
    hass: HomeAssistant, accept_any: list[str]
) -> None:
    _entry().add_to_hass(hass)

    await _through_access(hass, {**ACCESS, CONF_ENVIRONMENT: ENV_PROD})
    assert accept_any == [ENV_PROD]


async def test_another_company_is_allowed(hass: HomeAssistant, ksef: FakeKsef) -> None:
    _entry().add_to_hass(hass)

    await _through_access(hass, {**ACCESS, CONF_NIP: "2222222222"})


# --- step 2: notification ------------------------------------------------------------------


async def test_phones_are_offered_and_the_default_fields_preselected(
    hass: HomeAssistant, ksef: FakeKsef
) -> None:
    result = await _through_access(hass)

    assert _select_options(result, CONF_NOTIFY_SERVICE) == ["mobile_app_phone", "mobile_app_tablet"]
    assert _select_options(result, CONF_FIELDS) == list(FIELD_KEYS)
    marker = next(m for m in result["data_schema"].schema if m == CONF_FIELDS)
    assert marker.default() == list(DEFAULT_FIELDS)


async def test_fields_are_stored_in_message_order(hass: HomeAssistant, ksef: FakeKsef) -> None:
    result = await _through_access(hass)
    picked = ["ksef_number", "due_date", "seller_nip", "line_items", "due_date"]

    result = await _configure(hass, result, {**NOTIFICATION, CONF_FIELDS: picked})
    result = await _configure(hass, result, BEHAVIOUR)

    assert result["options"][CONF_FIELDS] == ["seller_nip", "due_date", "line_items", "ksef_number"]


async def test_every_field_can_be_selected(hass: HomeAssistant, ksef: FakeKsef) -> None:
    result = await _through_access(hass)

    result = await _configure(hass, result, {**NOTIFICATION, CONF_FIELDS: list(FIELD_KEYS)[::-1]})
    result = await _configure(hass, result, BEHAVIOUR)

    assert result["options"][CONF_FIELDS] == list(FIELD_KEYS)


async def test_no_field_selected_is_an_error(hass: HomeAssistant, ksef: FakeKsef) -> None:
    result = await _through_access(hass)

    result = await _configure(hass, result, {**NOTIFICATION, CONF_FIELDS: []})

    assert result["step_id"] == "notification"
    assert result["errors"] == {CONF_FIELDS: "no_fields"}


async def test_unknown_field_is_refused_by_the_schema(hass: HomeAssistant, ksef: FakeKsef) -> None:
    result = await _through_access(hass)

    with pytest.raises(InvalidData):
        await _configure(hass, result, {**NOTIFICATION, CONF_FIELDS: ["buyer_name"]})


@pytest.mark.parametrize(
    ("typed", "stored"),
    [
        ("mobile_app_phone", "mobile_app_phone"),
        ("notify.mobile_app_phone", "mobile_app_phone"),
        ("  mobile_app_new_phone ", "mobile_app_new_phone"),  # not registered yet
    ],
)
async def test_phone_service_custom_values(
    hass: HomeAssistant, ksef: FakeKsef, typed: str, stored: str
) -> None:
    result = await _through_access(hass)

    result = await _configure(hass, result, {**NOTIFICATION, CONF_NOTIFY_SERVICE: typed})
    result = await _configure(hass, result, BEHAVIOUR)

    assert result["options"][CONF_NOTIFY_SERVICE] == stored


@pytest.mark.parametrize(
    "typed",
    [
        "persistent_notification",
        "notify.notify",
        "light.kitchen",
        "mobile_app_",
        "mobile_app_Phone",
        "",
    ],
)
async def test_not_a_phone_service_is_an_error(
    hass: HomeAssistant, ksef: FakeKsef, typed: str
) -> None:
    result = await _through_access(hass)

    result = await _configure(hass, result, {**NOTIFICATION, CONF_NOTIFY_SERVICE: typed})

    assert result["step_id"] == "notification"
    assert result["errors"] == {CONF_NOTIFY_SERVICE: "invalid_notify_service"}


# --- step 3: behaviour ---------------------------------------------------------------------


@pytest.mark.parametrize(("typed", "stored"), [(15, 15), (20.0, 20), (1440, 1440)])
async def test_interval_bounds_and_storage(
    hass: HomeAssistant, ksef: FakeKsef, typed: float, stored: int
) -> None:
    result = await _configure(hass, await _through_access(hass), NOTIFICATION)

    result = await _configure(hass, result, {CONF_CHECK_INTERVAL_MIN: typed})

    assert result["options"][CONF_CHECK_INTERVAL_MIN] == stored
    assert type(result["options"][CONF_CHECK_INTERVAL_MIN]) is int


async def test_interval_default_is_15(hass: HomeAssistant, ksef: FakeKsef) -> None:
    result = await _configure(hass, await _through_access(hass), NOTIFICATION)

    marker = next(m for m in result["data_schema"].schema if m == CONF_CHECK_INTERVAL_MIN)
    assert marker.default() == 15
    selector = result["data_schema"].schema[marker].config
    assert (selector["min"], selector["max"], selector["step"]) == (15, 1440, 5)


@pytest.mark.parametrize("typed", [10, 14.9, 1445, 2000])
async def test_interval_outside_bounds_is_refused(
    hass: HomeAssistant, ksef: FakeKsef, typed: float
) -> None:
    result = await _configure(hass, await _through_access(hass), NOTIFICATION)

    with pytest.raises(InvalidData):
        await _configure(hass, result, {CONF_CHECK_INTERVAL_MIN: typed})


@pytest.mark.parametrize("typed", [17, 15.5])
async def test_interval_off_step_is_an_error(
    hass: HomeAssistant, ksef: FakeKsef, typed: float
) -> None:
    result = await _configure(hass, await _through_access(hass), NOTIFICATION)

    result = await _configure(hass, result, {CONF_CHECK_INTERVAL_MIN: typed})

    assert result["step_id"] == "behaviour"
    assert result["errors"] == {CONF_CHECK_INTERVAL_MIN: "invalid_interval"}


# --- options flow --------------------------------------------------------------------------


async def test_options_round_trip_keeps_identity_and_reloads(
    hass: HomeAssistant, ksef: FakeKsef
) -> None:
    entry = _entry()
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)

    result = await hass.config_entries.options.async_init(entry.entry_id)
    assert result["step_id"] == "notification"
    assert _suggested(result, CONF_NOTIFY_SERVICE) == "mobile_app_phone"
    assert _suggested(result, CONF_FIELDS) == list(DEFAULT_FIELDS)

    result = await hass.config_entries.options.async_configure(
        result["flow_id"],
        {CONF_NOTIFY_SERVICE: "mobile_app_tablet", CONF_FIELDS: ["vat_amount", "seller_name"]},
    )
    assert result["step_id"] == "behaviour"
    assert _suggested(result, CONF_CHECK_INTERVAL_MIN) == 15

    with patch.object(hass.config_entries, "async_schedule_reload") as reload:
        result = await hass.config_entries.options.async_configure(
            result["flow_id"], {CONF_CHECK_INTERVAL_MIN: 60}
        )
        await hass.async_block_till_done()

    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert dict(entry.options) == {
        "notify_service": "mobile_app_tablet",
        "fields": ["seller_name", "vat_amount"],
        "check_interval_min": 60,
    }
    assert dict(entry.data) == ACCESS
    assert entry.unique_id == f"test_{NIP}"
    reload.assert_called_once_with(entry.entry_id)
    assert ksef.calls == []  # the options flow never talks to KSeF


async def test_options_flow_does_not_offer_identity_fields(hass: HomeAssistant) -> None:
    entry = _entry()
    entry.add_to_hass(hass)

    result = await hass.config_entries.options.async_init(entry.entry_id)
    keys = {str(marker) for marker in result["data_schema"].schema}
    result = await hass.config_entries.options.async_configure(result["flow_id"], NOTIFICATION)
    keys |= {str(marker) for marker in result["data_schema"].schema}

    assert keys == {CONF_NOTIFY_SERVICE, CONF_FIELDS, CONF_CHECK_INTERVAL_MIN}


async def test_options_flow_validates_like_the_wizard(hass: HomeAssistant) -> None:
    entry = _entry()
    entry.add_to_hass(hass)

    result = await hass.config_entries.options.async_init(entry.entry_id)
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {CONF_NOTIFY_SERVICE: "light.kitchen", CONF_FIELDS: []}
    )
    assert result["errors"] == {
        CONF_NOTIFY_SERVICE: "invalid_notify_service",
        CONF_FIELDS: "no_fields",
    }
    assert _suggested(result, CONF_NOTIFY_SERVICE) == "light.kitchen"

    result = await hass.config_entries.options.async_configure(result["flow_id"], NOTIFICATION)
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {CONF_CHECK_INTERVAL_MIN: 17}
    )
    assert result["errors"] == {CONF_CHECK_INTERVAL_MIN: "invalid_interval"}


async def test_unchanged_options_do_not_reload(hass: HomeAssistant) -> None:
    entry = _entry()
    entry.add_to_hass(hass)

    result = await hass.config_entries.options.async_init(entry.entry_id)
    result = await hass.config_entries.options.async_configure(result["flow_id"], NOTIFICATION)
    with patch.object(hass.config_entries, "async_schedule_reload") as reload:
        result = await hass.config_entries.options.async_configure(result["flow_id"], BEHAVIOUR)
        await hass.async_block_till_done()

    assert result["type"] is FlowResultType.CREATE_ENTRY
    reload.assert_not_called()


# --- re-authentication ---------------------------------------------------------------------


async def test_reauth_replaces_only_the_token(hass: HomeAssistant, ksef: FakeKsef) -> None:
    entry = _entry()
    entry.add_to_hass(hass)

    result = await entry.start_reauth_flow(hass)
    assert result["step_id"] == "reauth_confirm"
    assert result["description_placeholders"]["entry_title"] == entry.title

    result = await _configure(hass, result, {CONF_TOKEN: f" {NEW_TOKEN} "})
    await hass.async_block_till_done()

    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "reauth_successful"
    assert dict(entry.data) == {**ACCESS, CONF_TOKEN: NEW_TOKEN}
    assert dict(entry.options) == {**NOTIFICATION, **BEHAVIOUR}
    assert ksef.routes() == [*AUTH_ROUTES, "metadata", "revoke"]


async def test_reauth_validates_against_the_entry_environment_and_nip(
    hass: HomeAssistant, ksef: FakeKsef
) -> None:
    entry = _entry()
    entry.add_to_hass(hass)
    seen: list[dict[str, Any]] = []
    real = config_flow.validate_access

    async def spy(hass: HomeAssistant, environment: str, nip: str, token: str) -> str | None:
        seen.append({"environment": environment, "nip": nip, "token": token})
        return await real(hass, environment, nip, token)

    with patch.object(config_flow, "validate_access", spy):
        result = await entry.start_reauth_flow(hass)
        await _configure(hass, result, {CONF_TOKEN: NEW_TOKEN})

    assert seen == [{"environment": ENV_TEST, "nip": NIP, "token": NEW_TOKEN}]


@pytest.mark.parametrize(
    ("reply", "error"),
    [
        (status_reply(450, "Token unieważniony"), "invalid_token"),
        (status_reply(415), "no_permission"),
        (status_reply(470), "account_blocked"),
        (Reply(status=503), "cannot_connect"),
    ],
)
async def test_reauth_failure_keeps_the_old_token(
    hass: HomeAssistant, ksef: FakeKsef, reply: Reply, error: str
) -> None:
    entry = _entry()
    entry.add_to_hass(hass)
    ksef.script("challenge" if reply.status == 503 else "status", reply)

    result = await entry.start_reauth_flow(hass)
    result = await _configure(hass, result, {CONF_TOKEN: NEW_TOKEN})

    assert result["type"] is FlowResultType.FORM
    assert result["errors"] == {"base": error}
    assert entry.data[CONF_TOKEN] == KSEF_TOKEN


async def test_reauth_blank_token_is_refused_without_asking_ksef(
    hass: HomeAssistant, ksef: FakeKsef
) -> None:
    entry = _entry()
    entry.add_to_hass(hass)

    result = await entry.start_reauth_flow(hass)
    result = await _configure(hass, result, {CONF_TOKEN: ""})

    assert result["errors"] == {CONF_TOKEN: "invalid_token"}
    assert ksef.calls == []


# --- the token stays secret ----------------------------------------------------------------


async def test_token_never_reaches_the_log_or_a_form(
    hass: HomeAssistant, ksef: FakeKsef, caplog: pytest.LogCaptureFixture
) -> None:
    caplog.set_level(logging.DEBUG)
    ksef.script("status", status_reply(450), status_reply(200))
    ksef.script("metadata", Reply(status=500), Reply(status=429), metadata_reply([]))
    shown: list[FlowResult] = []

    result = await _start(hass)
    for _ in range(4):
        result = await _configure(hass, result, ACCESS)
        shown.append(result)
    result = await _configure(hass, result, NOTIFICATION)
    result = await _configure(hass, result, BEHAVIOUR)
    reauth = await start_reauth_flow(hass, result["result"])
    shown.append(await _configure(hass, reauth, {CONF_TOKEN: NEW_TOKEN}))

    assert [r.get("errors") for r in shown] == [
        {"base": "invalid_token"},
        {"base": "cannot_connect"},
        {"base": "rate_limited"},
        {},
        None,
    ]
    assert shown[-1]["reason"] == "reauth_successful"
    visible = repr([{k: v for k, v in r.items() if k != "data_schema"} for r in shown])
    for secret in (KSEF_TOKEN, NEW_TOKEN):
        assert secret not in caplog.text
        assert secret not in visible
