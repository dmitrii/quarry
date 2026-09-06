# PURPOSE: Shared test helpers for building throwaway git repositories, so
# tests that commit, tag or release can do so without touching the real
# checkout, and for reading the version those tests must not hardcode. Not a
# test module itself.

from __future__ import annotations

import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
QUARRY = ROOT / "bin" / "quarry"

sys.path.insert(0, str(ROOT / "tools"))
import bump_version  # noqa: E402

IGNORED = shutil.ignore_patterns(".git", "__pycache__", "build_stamp.py")


def shipped_version() -> str:
    """The version bin/quarry currently declares.

    Tests must derive their expectations from this rather than naming a
    literal, because `make release` moves it.
    """
    return bump_version.read_version(QUARRY)


def git(repo: Path, *args: str) -> str:
    out = subprocess.run(["git", "-C", str(repo), *args],
                         capture_output=True, text=True, check=True)
    return out.stdout.strip()


def scratch_repo(dest: Path) -> Path:
    """A throwaway copy of the working tree, safe to commit and tag in."""
    shutil.copytree(ROOT, dest, ignore=IGNORED)
    git(dest, "init", "-q", "-b", "main")
    git(dest, "config", "user.email", "test@example.com")
    git(dest, "config", "user.name", "Test")
    git(dest, "add", "-A")
    git(dest, "commit", "-q", "-m", "initial")
    return dest
