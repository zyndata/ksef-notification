"""Every string the integration can show has to exist, in every language file.

A missing key is invisible in Python and shows up in the frontend as a raw
`ksef_notification::config::…` placeholder, so the checks are structural: step ids and
abort reasons are read out of the source, not restated here. Phase 5 adds the wizard's
steps and errors, phase 7 the Polish file and its parity checks.
"""

from __future__ import annotations

import ast
import json
from pathlib import Path

COMPONENT = Path(__file__).parents[1] / "custom_components" / "ksef_notification"
CONFIG_FLOW = COMPONENT / "config_flow.py"
STRINGS = json.loads((COMPONENT / "strings.json").read_text(encoding="utf-8"))


def _string_literals(keyword: str) -> set[str]:
    """Every literal passed as `keyword=` anywhere in config_flow.py."""
    tree = ast.parse(CONFIG_FLOW.read_text(encoding="utf-8"))
    return {
        node.value.value
        for call in ast.walk(tree)
        if isinstance(call, ast.Call)
        for node in call.keywords
        if node.arg == keyword and isinstance(node.value, ast.Constant)
    }


ABORT_REASONS = _string_literals("reason")


def test_the_source_scan_found_something() -> None:
    """Guard against an AST walk that silently matches nothing."""
    assert ABORT_REASONS


def test_translations_match_strings() -> None:
    """`translations/en.json` is the base language file — it must not drift."""
    english = json.loads((COMPONENT / "translations" / "en.json").read_text(encoding="utf-8"))

    assert english == STRINGS


def test_every_abort_reason_has_a_message() -> None:
    """An abort the flow can raise but cannot render shows the user a raw key."""
    assert set(STRINGS["config"]["abort"]) >= ABORT_REASONS


def test_the_user_step_is_labelled() -> None:
    """hassfest rejects a strings file without `config.step`; the user step needs a title."""
    assert STRINGS["config"]["step"]["user"]["title"]
