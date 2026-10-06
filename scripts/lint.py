"""Check lint and formatting (no changes). Usage: python scripts/lint.py"""

from __future__ import annotations

from _env import run_tool


def main() -> int:
    rc = run_tool("ruff", ["check", "."])
    rc_format = run_tool("ruff", ["format", "--check", "."])
    return rc or rc_format


if __name__ == "__main__":
    raise SystemExit(main())
