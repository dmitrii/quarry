# PURPOSE: Shared test helpers for building throwaway git repositories, so
# tests that commit, tag or release can do so without touching the real
# checkout. Not a test module itself.

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

IGNORED = shutil.ignore_patterns(".git", "__pycache__", "build_stamp.py")


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
