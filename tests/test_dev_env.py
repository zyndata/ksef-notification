"""The dev virtualenv has to survive an editor update.

`uv` downloads its interpreter under `XDG_DATA_HOME`, and a snap points that at a
per-revision directory — so the venv built inside VS Code stopped working the moment
the snap updated and the old revision was pruned. `uv_environ()` is what prevents
that, and only this test says what it may and may not redirect: moving a Windows or
plain-Linux install would orphan an interpreter that was never at risk.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).parents[1]
sys.path.insert(0, str(REPO_ROOT / "scripts"))

from _env import STABLE_PYTHON_DIR, uv_environ  # noqa: E402

SNAP_DATA_HOME = "/home/user/snap/code/260/.local/share"


def test_snap_data_home_is_redirected_off_the_revision(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The case that broke: a path carrying a snap revision number."""
    monkeypatch.setenv("XDG_DATA_HOME", SNAP_DATA_HOME)
    monkeypatch.delenv("UV_PYTHON_INSTALL_DIR", raising=False)
    assert uv_environ()["UV_PYTHON_INSTALL_DIR"] == str(STABLE_PYTHON_DIR)


def test_stable_target_carries_no_revision() -> None:
    """A fix that pointed at another moving directory would fix nothing."""
    assert "snap" not in STABLE_PYTHON_DIR.parts


@pytest.mark.parametrize(
    "data_home",
    [
        pytest.param("/home/user/.local/share", id="plain-linux"),
        pytest.param(None, id="unset-as-on-windows"),
    ],
)
def test_stable_environments_keep_uv_defaults(
    monkeypatch: pytest.MonkeyPatch, data_home: str | None
) -> None:
    """Windows and a normal shell are already safe; leave their download alone."""
    monkeypatch.delenv("UV_PYTHON_INSTALL_DIR", raising=False)
    if data_home is None:
        monkeypatch.delenv("XDG_DATA_HOME", raising=False)
    else:
        monkeypatch.setenv("XDG_DATA_HOME", data_home)
    assert "UV_PYTHON_INSTALL_DIR" not in uv_environ()


def test_an_explicit_choice_is_never_overridden(monkeypatch: pytest.MonkeyPatch) -> None:
    """Someone who set the variable themselves meant it, snap or not."""
    monkeypatch.setenv("XDG_DATA_HOME", SNAP_DATA_HOME)
    monkeypatch.setenv("UV_PYTHON_INSTALL_DIR", "/opt/pythons")
    assert uv_environ()["UV_PYTHON_INSTALL_DIR"] == "/opt/pythons"
