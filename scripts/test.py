"""Run the test suite. Usage: python scripts/test.py [--docker] [pytest args...]

On Linux (and in CI) pytest runs from the project virtualenv. On Windows it cannot:
Home Assistant imports `fcntl`, a Unix-only module, so the test harness fails before
any test runs. There the suite runs in a local Linux container instead, built from
`scripts/test.Dockerfile` on first use and rebuilt whenever that file or
`requirements-dev.txt` changes. The container has no network, which also proves that
the suite runs offline. `--docker` forces the container route on Linux too.
"""

from __future__ import annotations

import hashlib
import os
import shlex
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

from _env import REPO_ROOT, run, run_tool

IMAGE = "ksef-notification-test"
DOCKERFILE = REPO_ROOT / "scripts" / "test.Dockerfile"
REQUIREMENTS = REPO_ROOT / "requirements-dev.txt"

#: Copied out of the read-only mount before pytest runs. Without the virtualenv the copy
#: takes seconds; with it, minutes (measured in Walk the dog).
COPY_EXCLUDES = (".venv", ".git", ".pytest_cache", ".ruff_cache")


def image_tag() -> str:
    """The image name for the current Dockerfile and dev requirements."""
    digest = hashlib.sha256()
    for path in (DOCKERFILE, REQUIREMENTS):
        digest.update(path.read_bytes().replace(b"\r\n", b"\n"))
    return f"{IMAGE}:{digest.hexdigest()[:12]}"


def ensure_image(docker: str, tag: str) -> int:
    """Build the test image unless one for these exact inputs already exists."""
    probe = subprocess.run([docker, "image", "inspect", tag], capture_output=True, check=False)
    if probe.returncode == 0:
        return 0
    print(f"building {tag} (needs network access once)")
    with tempfile.TemporaryDirectory() as context:
        shutil.copy2(REQUIREMENTS, Path(context) / REQUIREMENTS.name)
        return run([docker, "build", "--tag", tag, "--file", DOCKERFILE, context])


def run_in_docker(pytest_args: list[str]) -> int:
    """Run pytest in the test container against a copy of the working tree."""
    docker = shutil.which("docker")
    if docker is None:
        print(
            "error: docker not found. On Windows the tests run in a container — "
            "see docs/DEVELOPMENT.md § Tests on Windows.",
            file=sys.stderr,
        )
        return 1
    tag = image_tag()
    rc = ensure_image(docker, tag)
    if rc != 0:
        return rc
    excludes = " ".join(f"--exclude=./{name}" for name in COPY_EXCLUDES)
    script = (
        f"mkdir /repo && tar -C /src {excludes} -cf - . | tar -C /repo -xf - && "
        f"cd /repo && python -m pytest -p no:cacheprovider {shlex.join(pytest_args)}"
    )
    return run(
        [
            docker,
            "run",
            "--rm",
            "--network",
            "none",
            "--volume",
            f"{REPO_ROOT}:/src:ro",
            tag,
            "sh",
            "-c",
            script,
        ]
    )


def main() -> int:
    args = sys.argv[1:]
    if "--docker" in args:
        return run_in_docker([arg for arg in args if arg != "--docker"])
    if os.name == "nt":
        return run_in_docker(args)
    return run_tool("pytest", args)


if __name__ == "__main__":
    raise SystemExit(main())
