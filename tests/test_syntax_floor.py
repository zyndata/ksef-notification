"""Every shipped module has to parse on the oldest Python the integration claims.

A syntax error is not a bug that degrades a feature — Home Assistant cannot import
the integration at all, and the user gets a traceback instead of a config flow. The
manual install route documented in the README has no version gate whatsoever, so
nothing but this test stands between a newer grammar and that failure.

`ast.parse(feature_version=...)` applies the older grammar without needing the older
interpreter, so this runs on the same 3.14 as everything else.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

COMPONENT = Path(__file__).parents[1] / "custom_components" / "ksef_notification"

#: Home Assistant 2026.9 (the `hacs.json` minimum) runs 3.14; 3.13 is one release below
#: it, and what a manual install on an older box may still land on.
FLOOR = (3, 13)

MODULES = sorted(COMPONENT.rglob("*.py"))


def test_every_shipped_module_is_covered() -> None:
    """Guard against the glob silently finding nothing and passing every check."""
    assert len(MODULES) >= 20


@pytest.mark.parametrize("path", MODULES, ids=lambda path: path.relative_to(COMPONENT).as_posix())
def test_module_parses_on_the_oldest_supported_python(path: Path) -> None:
    floor = ".".join(str(part) for part in FLOOR)
    try:
        ast.parse(path.read_text(encoding="utf-8"), feature_version=FLOOR)
    except SyntaxError as error:
        pytest.fail(f"{path.relative_to(COMPONENT)} does not parse on Python {floor}: {error}")
