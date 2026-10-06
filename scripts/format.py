"""Auto-format and auto-fix lint findings. Usage: python scripts/format.py"""

from __future__ import annotations

from _env import run_tool


def main() -> int:
    rc = run_tool("ruff", ["format", "."])
    rc_fix = run_tool("ruff", ["check", "--fix", "."])
    return rc or rc_fix


if __name__ == "__main__":
    raise SystemExit(main())
