# PURPOSE: Tests that `quarry --version` reports the commit pinned by the build
# stamp without consulting git, degrades to the bare version number when no
# stamp is present, and that `make stamp` pins HEAD.

from __future__ import annotations

import shutil
import subprocess
import sys
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

ROOT = Path(__file__).resolve().parent.parent
STAMP = "build_stamp.py"


def copy_tree(dest: Path) -> Path:
    """A runnable quarry (bin/ + src/) outside any git checkout."""
    shutil.copytree(ROOT / "bin", dest / "bin",
                    ignore=shutil.ignore_patterns("__pycache__"))
    shutil.copytree(ROOT / "src", dest / "src",
                    ignore=shutil.ignore_patterns("__pycache__", STAMP))
    return dest / "bin" / "quarry"


def run_version(quarry: Path) -> str:
    """The first line of --version output."""
    out = subprocess.run(
        [sys.executable, str(quarry), "--version"],
        capture_output=True, text=True, check=True,
    )
    assert not out.stderr.strip(), f"unexpected stderr: {out.stderr}"
    return out.stdout.splitlines()[0]


def head_of_repo() -> tuple[str, str]:
    out = subprocess.run(
        ["git", "-C", str(ROOT), "log", "-1", "--format=%cs %h"],
        capture_output=True, text=True, check=True,
    )
    date, short_hash = out.stdout.split()
    return date, short_hash


def write_stamp(quarry: Path, body: str) -> None:
    (quarry.parent.parent / "src" / STAMP).write_text(body, encoding="utf-8")


class VersionTest(unittest.TestCase):
    def test_reports_the_pinned_commit(self):
        with TemporaryDirectory() as tmp:
            quarry = copy_tree(Path(tmp))
            write_stamp(quarry, '# PURPOSE: test fixture.\n'
                                'DATE = "2019-04-02"\nCOMMIT = "abc1234"\n')

            line = run_version(quarry)

        self.assertEqual(line, "quarry 0.1.0 (2019-04-02, abc1234)")

    def test_falls_back_to_bare_version_without_a_stamp(self):
        with TemporaryDirectory() as tmp:
            line = run_version(copy_tree(Path(tmp)))

        self.assertRegex(line, r"^quarry \d+\.\d+\.\d+$")

    def test_does_not_consult_git_at_run_time(self):
        """A stamped copy must report its pin even with git off the PATH."""
        with TemporaryDirectory() as tmp:
            quarry = copy_tree(Path(tmp))
            write_stamp(quarry, 'DATE = "2019-04-02"\nCOMMIT = "abc1234"\n')

            out = subprocess.run(
                [sys.executable, str(quarry), "--version"],
                capture_output=True, text=True, check=True,
                env={"PATH": "/nonexistent", "HOME": tmp},
            )

        self.assertEqual(out.stdout.splitlines()[0],
                         "quarry 0.1.0 (2019-04-02, abc1234)")
        self.assertEqual(out.stderr.strip(), "")

    def test_make_stamp_pins_the_newest_commit(self):
        date, short_hash = head_of_repo()
        with TemporaryDirectory() as tmp:
            target = Path(tmp) / STAMP
            subprocess.run(["make", "stamp", f"STAMP={target}"],
                           cwd=ROOT, capture_output=True, text=True, check=True)
            written = target.read_text(encoding="utf-8")

        self.assertTrue(written.startswith("# PURPOSE:"), written)
        self.assertIn(f'DATE = "{date}"', written)
        self.assertIn(f'COMMIT = "{short_hash}"', written)


if __name__ == "__main__":
    unittest.main()
