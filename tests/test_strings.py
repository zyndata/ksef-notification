"""Every string the integration can show has to exist, in every language file.

A missing key is invisible in Python and shows up in the frontend as a raw
`ksef_notification::config::…` placeholder, so the checks are structural: step ids, error
keys, abort reasons and selector translation keys are read out of the source, not restated
here.

hassfest validates only `strings.json` and `translations/en.json` of a custom integration;
the parity checks at the end of this file are the only check `translations/pl.json` gets.
"""

from __future__ import annotations

import ast
import json
import string
from pathlib import Path

import pytest

from custom_components.ksef_notification import const
from custom_components.ksef_notification.const import ENVIRONMENTS, INTEGRATION_NAME
from custom_components.ksef_notification.coordinator import Outcome
from custom_components.ksef_notification.core.fields import FIELD_KEYS

COMPONENT = Path(__file__).parents[1] / "custom_components" / "ksef_notification"
CONFIG_FLOW = COMPONENT / "config_flow.py"
STRINGS = json.loads((COMPONENT / "strings.json").read_text(encoding="utf-8"))
TREE = ast.parse(CONFIG_FLOW.read_text(encoding="utf-8"))

CONFIG_FLOW_CLASS = "KsefNotificationConfigFlow"
OPTIONS_FLOW_CLASS = "KsefNotificationOptionsFlow"
#: Module-level functions shared by both flows; their errors must exist in both sections.
SHARED_VALIDATORS = ("_validate_notification", "_validate_behaviour")


def _node(name: str) -> ast.AST:
    return next(
        node
        for node in ast.walk(TREE)
        if isinstance(node, ast.ClassDef | ast.FunctionDef | ast.AsyncFunctionDef)
        and node.name == name
    )


def _keyword_literals(keyword: str, root: ast.AST = TREE) -> set[str]:
    """Every literal passed as `keyword=` under `root`."""
    return {
        node.value.value
        for call in ast.walk(root)
        if isinstance(call, ast.Call)
        for node in call.keywords
        if node.arg == keyword and isinstance(node.value, ast.Constant)
    }


def _error_literals(root: ast.AST) -> set[str]:
    """Literals assigned as `errors[...] = "key"` under `root`."""
    return {
        node.value.value
        for node in ast.walk(root)
        if isinstance(node, ast.Assign)
        and isinstance(node.value, ast.Constant)
        and any(
            isinstance(target, ast.Subscript)
            and isinstance(target.value, ast.Name)
            and target.value.id == "errors"
            for target in node.targets
        )
    }


def _validation_errors() -> set[str]:
    """What validate_access returns, including the auth-reason mapping."""
    returned = {
        node.value.value
        for node in ast.walk(_node("validate_access"))
        if isinstance(node, ast.Return) and isinstance(node.value, ast.Constant)
        if isinstance(node.value.value, str)
    }
    mapping = next(
        node.value
        for node in TREE.body
        if isinstance(node, ast.AnnAssign)
        and isinstance(node.target, ast.Name)
        and node.target.id == "_AUTH_ERRORS"
    )
    assert isinstance(mapping, ast.Dict)
    returned |= {value.value for value in mapping.values if isinstance(value, ast.Constant)}
    default = next(
        call.args[1].value
        for call in ast.walk(_node("validate_access"))
        if isinstance(call, ast.Call)
        and isinstance(call.func, ast.Attribute)
        and call.func.attr == "get"
    )
    return returned | {default}


SHARED_ERRORS = set().union(*(_error_literals(_node(name)) for name in SHARED_VALIDATORS))
CONFIG_ERRORS = _error_literals(_node(CONFIG_FLOW_CLASS)) | _validation_errors() | SHARED_ERRORS
CONFIG_STEPS = _keyword_literals("step_id", _node(CONFIG_FLOW_CLASS))
OPTIONS_STEPS = _keyword_literals("step_id", _node(OPTIONS_FLOW_CLASS))
ABORT_REASONS = _keyword_literals("reason") | {"already_configured", "reauth_successful"}


def test_the_source_scan_found_something() -> None:
    """Guard against an AST walk that silently matches nothing."""
    assert {"user", "notification", "behaviour", "reauth_confirm"} == CONFIG_STEPS
    assert {"notification", "behaviour"} == OPTIONS_STEPS
    assert {"no_fields", "invalid_notify_service", "invalid_interval"} == SHARED_ERRORS
    assert {"invalid_nip", "invalid_token", "no_permission", "account_blocked"} <= CONFIG_ERRORS
    assert {"rate_limited", "cannot_connect", "unknown"} <= CONFIG_ERRORS


def test_translations_match_strings() -> None:
    """`translations/en.json` is the base language file — it must not drift."""
    english = json.loads((COMPONENT / "translations" / "en.json").read_text(encoding="utf-8"))

    assert english == STRINGS


def test_every_step_has_a_title_and_labels() -> None:
    for section, steps in (("config", CONFIG_STEPS), ("options", OPTIONS_STEPS)):
        for step in steps:
            text = STRINGS[section]["step"][step]
            assert text["title"], (section, step)
            assert text["data"], (section, step)
            assert set(text["data_description"]) <= set(text["data"]), (section, step)


def test_no_step_text_is_left_without_a_step() -> None:
    assert set(STRINGS["config"]["step"]) == CONFIG_STEPS
    assert set(STRINGS["options"]["step"]) == OPTIONS_STEPS


def test_every_error_has_a_message() -> None:
    assert set(STRINGS["config"]["error"]) == CONFIG_ERRORS
    assert set(STRINGS["options"]["error"]) == SHARED_ERRORS


def test_every_abort_reason_has_a_message() -> None:
    """An abort the flow can raise but cannot render shows the user a raw key. The two
    reasons Home Assistant's helpers raise for the flow are added by hand."""
    assert set(STRINGS["config"]["abort"]) == ABORT_REASONS


def test_every_selector_option_has_a_label() -> None:
    names = {
        node.value.id
        for call in ast.walk(TREE)
        if isinstance(call, ast.Call)
        for node in call.keywords
        if node.arg == "translation_key" and isinstance(node.value, ast.Name)
    }
    keys = _keyword_literals("translation_key") | {getattr(const, name) for name in names}

    assert keys == set(STRINGS["selector"])
    assert set(STRINGS["selector"]["environment"]["options"]) == set(ENVIRONMENTS)
    assert set(STRINGS["selector"]["fields"]["options"]) == set(FIELD_KEYS)


def test_reauth_description_uses_only_the_placeholders_the_flow_passes() -> None:
    assert "{entry_title}" in STRINGS["config"]["step"]["reauth_confirm"]["description"]


# --- entities, exceptions and repair issues (phase 6) -----------------------------------------

PLATFORM_MODULES = ("switch", "sensor", "button")


def _constants_used(module: Path, prefix: str) -> set[str]:
    """Values of the `const` names starting with `prefix` that a module refers to."""
    tree = ast.parse(module.read_text(encoding="utf-8"))
    names = {
        node.id
        for node in ast.walk(tree)
        if isinstance(node, ast.Name) and node.id.startswith(prefix)
    }
    return {getattr(const, name) for name in names}


def test_every_entity_has_a_name() -> None:
    for platform in PLATFORM_MODULES:
        keys = _constants_used(COMPONENT / f"{platform}.py", "KEY_")
        assert keys, platform
        assert set(STRINGS["entity"][platform]) == keys, platform
        for key in keys:
            assert STRINGS["entity"][platform][key]["name"], (platform, key)
    assert set(STRINGS["entity"]) == set(PLATFORM_MODULES)


def test_every_outcome_has_a_label() -> None:
    states = STRINGS["entity"]["sensor"]["last_check"]["state_attributes"]["outcome"]["state"]
    assert set(states) == {outcome.value for outcome in Outcome}


def test_every_exception_raised_has_a_message() -> None:
    raised = _keyword_literals(
        "translation_key", ast.parse((COMPONENT / "coordinator.py").read_text(encoding="utf-8"))
    )
    assert raised == {"disabled", "halted", "too_soon"}
    assert set(STRINGS["exceptions"]) == raised
    assert "{time}" in STRINGS["exceptions"]["too_soon"]["message"]


def test_every_repair_issue_has_a_title_and_description() -> None:
    issues = {value for name, value in vars(const).items() if name.startswith("ISSUE_")}
    assert set(STRINGS["issues"]) == issues
    for key in issues:
        assert STRINGS["issues"][key]["title"]
        assert STRINGS["issues"][key]["description"]
    assert "{service}" in STRINGS["issues"]["notify_service_missing"]["description"]
    assert "{entry_title}" in STRINGS["issues"]["account_blocked"]["title"]


# --- translations (phase 7) -------------------------------------------------------------------

#: Every language shipped beyond the base file.
TRANSLATED = ("pl",)

POLISH_TITLE = "Powiadomienia KSeF"

#: Texts that are the same word in Polish: an abbreviation, a code, or the environment's name.
SAME_IN_POLISH = {
    "selector.environment.options.test",
    "entity.sensor.last_invoice.state_attributes.vat_amount.name",
    "entity.sensor.last_check.state_attributes.outcome.state.ok",
    "common.notification_seller_nip",
    "common.field_vat_amount",
}


def _leaves(node: dict, prefix: str = "") -> dict[str, str]:
    """Dotted path → text, so a missing key is reported by its path."""
    leaves: dict[str, str] = {}
    for key, value in node.items():
        path = f"{prefix}.{key}" if prefix else key
        if isinstance(value, dict):
            leaves.update(_leaves(value, path))
        else:
            leaves[path] = value
    return leaves


def _placeholders(text: str) -> set[str]:
    return {field for _, field, _, _ in string.Formatter().parse(text) if field}


def _language(code: str) -> dict[str, str]:
    path = COMPONENT / "translations" / f"{code}.json"
    return _leaves(json.loads(path.read_text(encoding="utf-8")))


ENGLISH = _leaves(STRINGS)


def test_the_base_file_names_the_integration() -> None:
    """The root `title` is what a translation can override; the base repeats the manifest."""
    assert STRINGS["title"] == INTEGRATION_NAME


@pytest.mark.parametrize("code", TRANSLATED)
def test_a_translation_has_every_key(code: str) -> None:
    """A key missing from a translation shows the user a raw identifier or English."""
    assert set(_language(code)) == set(ENGLISH)


@pytest.mark.parametrize("code", TRANSLATED)
def test_a_translation_fills_every_key(code: str) -> None:
    assert [path for path, text in _language(code).items() if not text.strip()] == []


@pytest.mark.parametrize("code", TRANSLATED)
def test_a_translation_keeps_every_placeholder(code: str) -> None:
    """A dropped `{slot}` leaves the value out; a renamed one reaches the phone as `{slot}`."""
    mismatched = {
        path: (_placeholders(ENGLISH[path]), _placeholders(text))
        for path, text in _language(code).items()
        if path in ENGLISH and _placeholders(ENGLISH[path]) != _placeholders(text)
    }
    assert mismatched == {}


def test_polish_is_not_a_copy_of_english() -> None:
    copied = {path for path, text in _language("pl").items() if ENGLISH.get(path) == text}
    assert copied == SAME_IN_POLISH


def test_polish_uses_the_localized_title() -> None:
    assert _language("pl")["title"] == POLISH_TITLE
