"""Deploy the integration into a local Home Assistant config folder.

Usage: python scripts/install.py
Target: $HA_CONFIG_DIR from .env (copy .env.example to .env and fill it in).
"""

from __future__ import annotations

import shutil
import sys
from pathlib import Path

from _env import REPO_ROOT, load_dotenv

COMPONENT = "ksef_notification"


def main() -> int:
    target_raw = load_dotenv().get("HA_CONFIG_DIR", "")
    if not target_raw:
        print(
            "error: HA_CONFIG_DIR is not set. Copy .env.example to .env and set it "
            "to your Home Assistant config directory.",
            file=sys.stderr,
        )
        return 1

    ha_config = Path(target_raw)
    if not ha_config.is_dir():
        print(f"error: HA_CONFIG_DIR does not exist: {ha_config}", file=sys.stderr)
        return 1
    if not (ha_config / "configuration.yaml").is_file():
        print(
            f"error: {ha_config} does not look like a HA config dir (no configuration.yaml found)",
            file=sys.stderr,
        )
        return 1

    src = REPO_ROOT / "custom_components" / COMPONENT
    dst = ha_config / "custom_components" / COMPONENT
    if dst.is_symlink():
        print(f"error: {dst} is a symlink (already linked to a working copy?) — not touching it")
        return 1
    if dst.exists():
        shutil.rmtree(dst)
    shutil.copytree(src, dst, ignore=shutil.ignore_patterns("__pycache__"))
    print(f"Deployed {src.name} -> {dst}")
    print("Restart Home Assistant to load the new version.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
