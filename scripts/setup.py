"""Set up the dev environment: Python 3.14 venv + pinned dev deps + pre-commit hooks.

Usage: python scripts/setup.py [--no-pre-commit]

Requires uv (https://docs.astral.sh/uv/) — it provisions the right Python on both
Windows and Linux regardless of what the system Python is. Idempotent.
"""

from __future__ import annotations

import sys

from _env import (
    PYTHON_VERSION,
    REPO_ROOT,
    VENV_DIR,
    find_uv,
    run,
    run_tool,
    uv_environ,
    venv_bin,
    venv_python_works,
)


def main() -> int:
    uv = find_uv()
    if uv is None:
        print(
            "error: uv not found. Install it first:\n"
            "  Linux:   curl -LsSf https://astral.sh/uv/install.sh | sh\n"
            '  Windows: powershell -c "irm https://astral.sh/uv/install.ps1 | iex"\n'
            "then re-run this script (open a new terminal so PATH picks it up).",
            file=sys.stderr,
        )
        return 1

    env = uv_environ()

    if not venv_python_works():
        # Covers a venv built before UV_PYTHON_INSTALL_DIR was pinned, whose
        # interpreter a snap update has since taken away: `uv venv` replaces it.
        if VENV_DIR.exists():
            print("existing venv has no working interpreter — rebuilding it")
        rc = run([uv, "venv", "--python", PYTHON_VERSION, VENV_DIR], env=env)
        if rc != 0:
            return rc

    rc = run(
        [
            uv,
            "pip",
            "install",
            "--python",
            venv_bin("python"),
            "-r",
            REPO_ROOT / "requirements-dev.txt",
        ],
        env=env,
    )
    if rc != 0:
        return rc

    if "--no-pre-commit" not in sys.argv[1:] and (REPO_ROOT / ".git").exists():
        rc = run_tool("pre-commit", ["install"])
        if rc != 0:
            return rc

    print("\nDev environment ready. Next: python scripts/lint.py | test.py | install.py")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
