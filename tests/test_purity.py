"""Purity is a structural property, so it is checked structurally.

docs/ARCHITECTURE.md makes `core/*` pure — no I/O, no Home Assistant imports, no clock
reads, `now` always a parameter — because that is what makes new-invoice detection and
message formatting testable over every sequence of cycles and restarts. A stray
`datetime.now()` would quietly undo that without failing a single behavioural test, so
this module reads the source instead.

The same document keeps `client/*` free of the core: the client does KSeF I/O and
nothing else.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

COMPONENT = Path(__file__).parents[1] / "custom_components" / "ksef_notification"
CORE = sorted(COMPONENT.joinpath("core").glob("*.py"))
CLIENT = sorted(COMPONENT.joinpath("client").glob("*.py"))

#: Absolute imports a pure module may use: parts of the standard library that do no I/O.
ALLOWED_ROOTS = {
    "__future__",
    "collections",
    "dataclasses",
    "datetime",
    "decimal",
    "enum",
    "hashlib",
    "itertools",
    "math",
    "re",
    "typing",
    "unicodedata",
    "xml",
}

#: Reading a clock, or anything that touches the outside world.
FORBIDDEN_CALLS = {"now", "utcnow", "today", "time", "monotonic", "sleep", "open"}


def _imports(tree: ast.AST) -> tuple[set[str], set[str]]:
    """(absolute module roots, relative module names) imported by a parsed module."""
    roots: set[str] = set()
    relative: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            roots.update(alias.name.split(".")[0] for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
            roots.add(node.module.split(".")[0])
        elif isinstance(node, ast.ImportFrom):
            relative.add(node.module or "")
    return roots, relative


def _parse(path: Path) -> ast.AST:
    return ast.parse(path.read_text(encoding="utf-8"))


def test_every_layer_is_covered() -> None:
    """Guard against a glob silently finding nothing and passing every check."""
    assert {path.name for path in CORE} == {
        "__init__.py",
        "fields.py",
        "formatter.py",
        "model.py",
        "tracker.py",
        "xml_parser.py",
    }
    assert {path.name for path in CLIENT} == {
        "__init__.py",
        "api.py",
        "auth.py",
        "errors.py",
        "http.py",
    }


@pytest.mark.parametrize("path", CORE, ids=lambda path: path.name)
def test_core_imports_only_the_standard_library(path: Path) -> None:
    """No homeassistant, no aiohttp: plain Python over dataclasses."""
    roots, _ = _imports(_parse(path))

    assert roots <= ALLOWED_ROOTS, f"{path.name} imports {sorted(roots - ALLOWED_ROOTS)}"


@pytest.mark.parametrize("path", CORE, ids=lambda path: path.name)
def test_core_never_reads_a_clock_or_does_io(path: Path) -> None:
    """`now` is a parameter everywhere — nothing in here may ask the system for it."""
    called = {
        node.func.attr if isinstance(node.func, ast.Attribute) else getattr(node.func, "id", "")
        for node in ast.walk(_parse(path))
        if isinstance(node, ast.Call)
    }

    assert not called & FORBIDDEN_CALLS, f"{path.name} calls {sorted(called & FORBIDDEN_CALLS)}"


@pytest.mark.parametrize("path", CORE, ids=lambda path: path.name)
def test_core_defines_nothing_async(path: Path) -> None:
    """Async is where I/O lives; a pure decision layer has no use for it."""
    assert not [node for node in ast.walk(_parse(path)) if isinstance(node, ast.AsyncFunctionDef)]


@pytest.mark.parametrize("path", CLIENT, ids=lambda path: path.name)
def test_client_never_imports_the_core(path: Path) -> None:
    """The client talks to KSeF and raises typed errors; deciding is the core's job."""
    roots, relative = _imports(_parse(path))

    assert "core" not in {name.split(".")[0] for name in relative}
    assert "custom_components" not in roots
