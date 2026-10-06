"""Check the release metadata, and optionally tag the release.

Usage:
  python scripts/release.py            # verify manifest.json and CHANGELOG.md agree
  python scripts/release.py --notes    # print the changelog section for that version
  python scripts/release.py --print-version   # print the manifest version, nothing else
  python scripts/release.py --tag      # verify, then create and push the `vX.Y.Z` tag

The version lives in `custom_components/ksef_notification/manifest.json` — that is the
number Home Assistant and HACS show. The tag only mirrors it, so this script never
invents a version: it reads the manifest and refuses to tag until `CHANGELOG.md`
has a matching, dated section.
"""

from __future__ import annotations

import json
import re
import subprocess
import sys

from _env import REPO_ROOT, run

MANIFEST = REPO_ROOT / "custom_components" / "ksef_notification" / "manifest.json"
CHANGELOG = REPO_ROOT / "CHANGELOG.md"

#: `## [1.2.3] - 2026-08-27` — Keep a Changelog's release heading. `[Unreleased]`
#: carries no date and is deliberately not matched.
RELEASE_HEADING = re.compile(r"^## \[(\d+\.\d+\.\d+)\] - (\d{4}-\d{2}-\d{2})\s*$", re.MULTILINE)
SEMVER = re.compile(r"^\d+\.\d+\.\d+$")
#: `[1.2.3]: https://…` — a Markdown link reference, not release content.
LINK_DEFINITION = re.compile(r"\[[^\]]+\]: \S+")


def manifest_version() -> str:
    """The version Home Assistant reports for the installed integration."""
    return str(json.loads(MANIFEST.read_text(encoding="utf-8"))["version"])


def changelog_notes(version: str) -> str | None:
    """The changelog body for `version`, or None when it has no dated section yet."""
    text = CHANGELOG.read_text(encoding="utf-8")
    for match in RELEASE_HEADING.finditer(text):
        if match.group(1) != version:
            continue
        rest = text[match.end() :]
        next_section = re.search(r"^## \[", rest, re.MULTILINE)
        body = rest[: next_section.start()] if next_section else rest
        return _without_link_definitions(body)
    return None


def _without_link_definitions(body: str) -> str:
    """Drop the link-reference block the file's last section carries after its content."""
    lines = body.rstrip().splitlines()
    while lines and (not lines[-1].strip() or LINK_DEFINITION.fullmatch(lines[-1])):
        lines.pop()
    return "\n".join(lines).strip()


def check() -> tuple[str, str] | None:
    """Return (version, notes) when the release metadata is consistent, else None."""
    version = manifest_version()
    if not SEMVER.fullmatch(version):
        print(
            f"error: manifest version {version!r} is not a MAJOR.MINOR.PATCH number",
            file=sys.stderr,
        )
        return None
    notes = changelog_notes(version)
    if notes is None:
        print(
            f"error: CHANGELOG.md has no dated `## [{version}] - YYYY-MM-DD` section.\n"
            f"       Rename the `## [Unreleased]` heading, or bump the manifest version.",
            file=sys.stderr,
        )
        return None
    return version, notes


def _git(*args: str) -> str:
    result = subprocess.run(
        ["git", *args], cwd=REPO_ROOT, capture_output=True, text=True, check=False
    )
    return result.stdout.strip() if result.returncode == 0 else ""


def tag(version: str) -> int:
    """Create and push `vX.Y.Z`, refusing what the release workflow would reject."""
    name = f"v{version}"
    if _git("tag", "-l", name):
        print(f"error: tag {name} already exists — bump the version first", file=sys.stderr)
        return 1
    if _git("status", "--porcelain"):
        print(
            "error: the working tree is dirty — commit the release changes first",
            file=sys.stderr,
        )
        return 1
    rc = run(["git", "tag", "-a", name, "-m", f"KSeF Notification {version}"])
    if rc:
        return rc
    return run(["git", "push", "origin", name])


def main() -> int:
    # The changelog is full of dashes and Polish letters, and the Windows console is
    # not UTF-8 by default — printing the notes would fail without this.
    sys.stdout.reconfigure(encoding="utf-8")
    args = sys.argv[1:]
    if args and args[0] not in {"--notes", "--tag", "--print-version"}:
        print(__doc__, file=sys.stderr)
        return 2

    if args and args[0] == "--print-version":
        print(manifest_version())
        return 0

    checked = check()
    if checked is None:
        return 1
    version, notes = checked

    if args and args[0] == "--notes":
        print(notes)
        return 0
    if args and args[0] == "--tag":
        return tag(version)

    print(f"Version {version} — manifest and CHANGELOG agree.")
    print(f"Run `python scripts/release.py --tag` to publish it as v{version}.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
