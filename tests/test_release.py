"""The version the user sees has to be the version the release says it is.

`manifest.json` is what Home Assistant and HACS display; `CHANGELOG.md` is what the
release page shows. Nothing in the code reads either, so only this test stops them
from drifting apart — and drift is exactly what shows up in the frontend as an
install whose version means nothing to the person reading it.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).parents[1]
sys.path.insert(0, str(REPO_ROOT / "scripts"))

from release import (  # noqa: E402
    LINK_DEFINITION,
    RELEASE_HEADING,
    SEMVER,
    changelog_notes,
    manifest_version,
)

CHANGELOG = (REPO_ROOT / "CHANGELOG.md").read_text(encoding="utf-8")
MANIFEST = json.loads(
    (REPO_ROOT / "custom_components" / "ksef_notification" / "manifest.json").read_text(
        encoding="utf-8"
    )
)


def test_manifest_declares_a_semver_version() -> None:
    assert SEMVER.fullmatch(str(MANIFEST["version"]))


def test_manifest_version_has_a_dated_changelog_section() -> None:
    version = manifest_version()
    assert changelog_notes(version), f"CHANGELOG.md has no `## [{version}] - YYYY-MM-DD` section"


def test_manifest_version_is_the_newest_changelog_entry() -> None:
    """A released version below the top of the file means a bump was forgotten."""
    released = [match.group(1) for match in RELEASE_HEADING.finditer(CHANGELOG)]
    assert released, "CHANGELOG.md has no released section at all"
    assert released[0] == manifest_version()


def test_changelog_releases_descend() -> None:
    versions = [
        tuple(int(part) for part in match.group(1).split("."))
        for match in RELEASE_HEADING.finditer(CHANGELOG)
    ]
    assert versions == sorted(versions, reverse=True)


def test_release_notes_carry_no_link_definitions() -> None:
    """The trailing `[0.8.0]: …` block is Markdown plumbing, not release content."""
    notes = changelog_notes(manifest_version())
    assert notes is not None
    assert not any(LINK_DEFINITION.fullmatch(line) for line in notes.splitlines())


@pytest.mark.parametrize("version", ["0.0.1", "99.0.0"])
def test_unknown_versions_have_no_notes(version: str) -> None:
    assert changelog_notes(version) is None
