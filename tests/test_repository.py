"""The repository stays readable by someone who has only the repository.

A few working files used during development are deliberately git-ignored and exist only
on the author's machine. A tracked document that links to one of them sends a reader of
the public repository to a page that does not exist, so links are checked here, and the
ignore rules that keep those files out are checked with them.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).parents[1]

#: Local, git-ignored working files. Never a link target in a tracked document.
LOCAL_ONLY = (
    "CLAUDE.md",
    "CLAUDE.local.md",
    ".claude/",
    "PLAN.md",
    "STATE.md",
    "docs/REFERENCE.md",
)

#: `[text](target)` — the target up to an optional `#anchor` or title.
LINK = re.compile(r"\]\(([^)\s#]+)")


def _tracked_documents() -> list[Path]:
    """Every Markdown file that ships with the repository."""
    candidates = [*REPO_ROOT.glob("*.md"), *REPO_ROOT.joinpath("docs").rglob("*.md")]
    local = {(REPO_ROOT / name).resolve() for name in LOCAL_ONLY}
    return sorted(path for path in candidates if path.resolve() not in local)


DOCUMENTS = _tracked_documents()


def test_the_document_scan_found_something() -> None:
    """Guard against a glob silently finding nothing and passing every check."""
    names = {path.name for path in DOCUMENTS}
    assert {"README.md", "CHANGELOG.md", "ARCHITECTURE.md", "DEVELOPMENT.md"} <= names


@pytest.mark.parametrize("path", DOCUMENTS, ids=lambda path: path.relative_to(REPO_ROOT).as_posix())
def test_no_link_to_a_local_working_file(path: Path) -> None:
    """A reader of the public repository cannot open these."""
    local = {(REPO_ROOT / name).resolve() for name in LOCAL_ONLY}
    targets = LINK.findall(path.read_text(encoding="utf-8"))
    broken = [
        target
        for target in targets
        if "://" not in target
        and any((path.parent / target).resolve().is_relative_to(item) for item in local)
    ]

    assert broken == []


def test_local_working_files_are_ignored() -> None:
    """The ignore rules are what keep the working files out of a commit."""
    ignored = set((REPO_ROOT / ".gitignore").read_text(encoding="utf-8").splitlines())

    assert set(LOCAL_ONLY) <= ignored
    assert ".env" in ignored
