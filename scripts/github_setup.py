"""Apply the recommended GitHub-side repository settings. Usage: python scripts/github_setup.py

Everything here is idempotent: run it again after changing a value and only that value moves.
Requires the GitHub CLI (`gh`) and `gh auth login`. Settings that the repository's plan or
visibility does not allow are reported as skipped, not as failures.

Options:
  --dry-run   print what would be sent, change nothing
"""

from __future__ import annotations

import json
import shutil
import subprocess
import sys

REPO = "zyndata/ksef-notification"

DESCRIPTION = (
    "Home Assistant integration that sends a push notification when a new cost invoice "
    "for your company arrives in KSeF, the Polish national e-invoice system."
)

TOPICS = [
    "home-assistant",
    "homeassistant",
    "hacs",
    "custom-integration",
    "ksef",
    "e-invoice",
    "invoices",
    "notifications",
    "poland",
    "python",
]

# Direct pushes to main stay allowed on purpose: single contributor, no PR review.
# The ruleset only prevents history loss.
MAIN_RULESET = {
    "name": "main-protection",
    "target": "branch",
    "enforcement": "active",
    "conditions": {"ref_name": {"include": ["~DEFAULT_BRANCH"], "exclude": []}},
    "rules": [{"type": "deletion"}, {"type": "non_fast_forward"}],
}

# Release tags are what HACS installs from — an overwritten tag silently changes a release.
TAG_RULESET = {
    "name": "release-tags",
    "target": "tag",
    "enforcement": "active",
    "conditions": {"ref_name": {"include": ["refs/tags/v*"], "exclude": []}},
    "rules": [{"type": "deletion"}, {"type": "update"}, {"type": "non_fast_forward"}],
}

# Free on public repositories; rejected with HTTP 422 while the repo is private.
SECURITY_ANALYSIS = {
    "security_and_analysis": {
        "secret_scanning": {"status": "enabled"},
        "secret_scanning_push_protection": {"status": "enabled"},
    }
}

dry_run = "--dry-run" in sys.argv
failures: list[str] = []
skipped: list[str] = []


def gh(args: list[str], *, stdin: str | None = None, label: str, optional: bool = False) -> str:
    """Run a gh command, recording the outcome instead of aborting the whole run."""
    print(f"\n== {label}")
    print(f"$ gh {' '.join(args)}")
    if dry_run:
        if stdin:
            print(stdin)
        return ""
    done = subprocess.run(["gh", *args], input=stdin, capture_output=True, text=True, check=False)
    if done.returncode == 0:
        print("   ok")
        return done.stdout
    message = (done.stderr or done.stdout).strip().splitlines()
    detail = message[-1] if message else f"exit {done.returncode}"
    if optional:
        print(f"   skipped — {detail}")
        skipped.append(f"{label}: {detail}")
    else:
        print(f"   FAILED — {detail}")
        failures.append(f"{label}: {detail}")
    return ""


def existing_ruleset_names() -> set[str]:
    out = gh(["api", f"repos/{REPO}/rulesets"], label="Read existing rulesets", optional=True)
    if not out:
        return set()
    return {item["name"] for item in json.loads(out)}


def main() -> int:
    # The messages carry dashes, and the Windows console is not UTF-8 by default.
    sys.stdout.reconfigure(encoding="utf-8")
    if not dry_run:
        if shutil.which("gh") is None:
            print("error: gh not found — install the GitHub CLI first", file=sys.stderr)
            return 1
        check = subprocess.run(["gh", "auth", "status"], capture_output=True, check=False)
        if check.returncode != 0:
            print("error: not logged in — run `gh auth login` first", file=sys.stderr)
            return 1

    topic_args = [arg for topic in TOPICS for arg in ("--add-topic", topic)]
    gh(
        [
            "repo",
            "edit",
            REPO,
            "--description",
            DESCRIPTION,
            *topic_args,
            "--enable-issues",
            "--enable-wiki=false",
            "--enable-projects=false",
            "--enable-merge-commit=false",
            "--enable-rebase-merge=false",
            "--enable-squash-merge",
            "--delete-branch-on-merge",
        ],
        label="Description, topics, features, merge behaviour",
    )

    gh(
        ["api", "--method", "PUT", f"repos/{REPO}/vulnerability-alerts"],
        label="Dependabot alerts",
    )
    gh(
        ["api", "--method", "PUT", f"repos/{REPO}/automated-security-fixes"],
        label="Dependabot security updates",
    )
    # SECURITY.md sends reporters to "Report a vulnerability", which exists only when this is on.
    gh(
        ["api", "--method", "PUT", f"repos/{REPO}/private-vulnerability-reporting"],
        label="Private vulnerability reporting",
        optional=True,
    )

    gh(
        [
            "api",
            "--method",
            "PUT",
            f"repos/{REPO}/actions/permissions/workflow",
            "-f",
            "default_workflow_permissions=read",
            "-F",
            "can_approve_pull_request_reviews=false",
        ],
        label="Workflow token is read-only by default",
    )

    # Needs a public repo or a paid plan; expected to skip while the repo is private.
    gh(
        ["api", "--method", "PATCH", f"repos/{REPO}", "--input", "-"],
        # Sent as nested JSON on stdin, not as `-f a[b][c]=v`: gh passes bracketed
        # names through literally, GitHub ignores the unknown key and still answers
        # 200, so the form version reported success while changing nothing.
        stdin=json.dumps(SECURITY_ANALYSIS),
        label="Secret scanning + push protection",
        optional=True,
    )

    present = existing_ruleset_names()
    for ruleset in (MAIN_RULESET, TAG_RULESET):
        name = ruleset["name"]
        if name in present:
            print(f"\n== Ruleset '{name}' already exists — leaving it alone")
            continue
        gh(
            ["api", "--method", "POST", f"repos/{REPO}/rulesets", "--input", "-"],
            stdin=json.dumps(ruleset),
            label=f"Ruleset '{name}'",
            optional=True,
        )

    print("\n" + "-" * 72)
    for line in skipped:
        print(f"skipped: {line}")
    for line in failures:
        print(f"FAILED:  {line}")
    if not failures:
        print("done — all required settings applied")
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
