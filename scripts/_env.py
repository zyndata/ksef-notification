"""Shared helpers for the task-runner scripts. Same behaviour on Windows and Linux."""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
VENV_DIR = REPO_ROOT / ".venv"
PYTHON_VERSION = "3.14"

#: Where uv is told to keep the Python it downloads, when the environment would
#: otherwise hand it a directory that moves. Only ever used as a fallback for
#: `uv_environ()` — see there for why.
STABLE_PYTHON_DIR = Path.home() / ".local" / "share" / "uv" / "python"


def venv_bin(tool: str) -> Path:
    """Path of a tool inside the project virtualenv."""
    if os.name == "nt":
        return VENV_DIR / "Scripts" / f"{tool}.exe"
    return VENV_DIR / "bin" / tool


def find_uv() -> Path | None:
    """Locate uv on PATH or in its default install locations."""
    found = shutil.which("uv")
    if found:
        return Path(found)
    suffix = ".exe" if os.name == "nt" else ""
    for candidate in (
        Path.home() / ".local" / "bin" / f"uv{suffix}",
        Path.home() / ".cargo" / "bin" / f"uv{suffix}",
    ):
        if candidate.is_file():
            return candidate
    return None


def uv_environ() -> dict[str, str]:
    """The environment uv is run with — the parent's, plus a stable Python location.

    uv downloads the interpreter under `XDG_DATA_HOME`, and a snap sets that variable
    to a *per-revision* directory: the VS Code snap gives
    `~/snap/code/<revision>/.local/share`. The venv's `python` is a symlink into it,
    so the next snap update prunes the old revision and every script in this
    directory stops working with "required file not found". Pointing uv at a path
    that does not carry a revision number fixes it for good.

    Only a snap is redirected. Windows and a plain Linux shell already give uv a
    stable directory, and moving them would orphan an interpreter that is fine where
    it is. An explicit `UV_PYTHON_INSTALL_DIR` always wins.
    """
    env = dict(os.environ)
    data_home = env.get("XDG_DATA_HOME")
    if data_home and "snap" in Path(data_home).parts:
        env.setdefault("UV_PYTHON_INSTALL_DIR", str(STABLE_PYTHON_DIR))
    return env


def venv_python_works() -> bool:
    """True when the venv has an interpreter that still runs.

    Not the same question as "does the file exist": a venv whose Python was removed
    from under it (see `uv_environ`) leaves a `python` that cannot start.
    """
    python = venv_bin("python")
    if not python.is_file():
        return False
    result = subprocess.run([str(python), "-c", ""], capture_output=True, check=False)
    return result.returncode == 0


def run(cmd: list[str | Path], env: dict[str, str] | None = None) -> int:
    """Run a command from the repo root, echoing it first."""
    printable = " ".join(str(part) for part in cmd)
    print(f"$ {printable}")
    return subprocess.run(
        [str(part) for part in cmd], cwd=REPO_ROOT, env=env, check=False
    ).returncode


def run_tool(tool: str, args: list[str]) -> int:
    """Run a tool from the virtualenv, failing clearly if setup has not been run."""
    exe = venv_bin(tool)
    if not exe.is_file():
        print(f"error: {exe} not found — run `python scripts/setup.py` first", file=sys.stderr)
        return 1
    return run([exe, *args])


def load_dotenv() -> dict[str, str]:
    """Parse the repo-root .env file (simple KEY=VALUE lines, # comments)."""
    env_file = REPO_ROOT / ".env"
    values: dict[str, str] = {}
    if not env_file.is_file():
        return values
    for raw_line in env_file.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        values[key.strip()] = value.strip().strip("'\"")
    return values
