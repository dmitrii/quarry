# PURPOSE: Tests that `quarry --version` reports the commit pinned by the build
# stamp on a single line without consulting git, degrades to the bare version
# number when no stamp is present, and that `make stamp` pins HEAD.

from __future__ import annotations

import shutil
import subprocess
import sys
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from support import ROOT, git, scratch_repo

STAMP = "build_stamp.py"

PINNED = 'DATE = "2019-04-02"\nCOMMIT = "abc1234"\nCOMMITS_SINCE_RELEASE = 0\n'


def copy_tree(dest: Path) -> Path:
    """A runnable quarry (bin/ + src/) outside any git checkout."""
    shutil.copytree(ROOT / "bin", dest / "bin",
                    ignore=shutil.ignore_patterns("__pycache__"))
    shutil.copytree(ROOT / "src", dest / "src",
                    ignore=shutil.ignore_patterns("__pycache__", STAMP))
    return dest / "bin" / "quarry"


def run_version(quarry: Path) -> str:
    """Everything --version writes to stdout — it should be a single line."""
    out = subprocess.run(
        [sys.executable, str(quarry), "--version"],
        capture_output=True, text=True, check=True,
    )
    assert not out.stderr.strip(), f"unexpected stderr: {out.stderr}"
    return out.stdout


def write_stamp(quarry: Path, body: str) -> None:
    (quarry.parent.parent / "src" / STAMP).write_text(body, encoding="utf-8")


def head_of_repo() -> tuple[str, str]:
    out = subprocess.run(
        ["git", "-C", str(ROOT), "log", "-1", "--format=%cs %h"],
        capture_output=True, text=True, check=True,
    )
    date, short_hash = out.stdout.split()
    return date, short_hash


class VersionTest(unittest.TestCase):
    def test_reports_the_pinned_commit(self):
        with TemporaryDirectory() as tmp:
            quarry = copy_tree(Path(tmp))
            write_stamp(quarry, "# PURPOSE: test fixture.\n" + PINNED)

            stdout = run_version(quarry)

        self.assertEqual(stdout, "quarry 0.1.0 (2019-04-02, abc1234)\n")

    def test_counts_commits_made_since_the_release(self):
        with TemporaryDirectory() as tmp:
            quarry = copy_tree(Path(tmp))
            write_stamp(quarry, PINNED.replace("RELEASE = 0", "RELEASE = 7"))

            stdout = run_version(quarry)

        self.assertEqual(stdout, "quarry 0.1.0+7 (2019-04-02, abc1234)\n")

    def test_omits_the_count_on_a_release_commit(self):
        with TemporaryDirectory() as tmp:
            quarry = copy_tree(Path(tmp))
            write_stamp(quarry, PINNED)

            stdout = run_version(quarry)

        self.assertNotIn("+", stdout)

    def test_treats_an_incomplete_stamp_as_no_stamp(self):
        """A stamp from an older install must degrade, not crash."""
        with TemporaryDirectory() as tmp:
            quarry = copy_tree(Path(tmp))
            write_stamp(quarry, 'DATE = "2019-04-02"\n')

            stdout = run_version(quarry)

        self.assertRegex(stdout, r"^quarry \d+\.\d+\.\d+\n$")

    def test_make_stamp_counts_commits_since_the_latest_tag(self):
        with TemporaryDirectory() as tmp:
            repo = scratch_repo(Path(tmp) / "quarry")
            git(repo, "tag", "-a", "v0.1.0", "-m", "release")
            git(repo, "commit", "-q", "--allow-empty", "-m", "after one")
            git(repo, "commit", "-q", "--allow-empty", "-m", "after two")
            target = Path(tmp) / STAMP

            subprocess.run(["make", "stamp", f"STAMP={target}"], cwd=repo,
                           capture_output=True, text=True, check=True)

            self.assertIn("COMMITS_SINCE_RELEASE = 2",
                          target.read_text(encoding="utf-8"))

    def test_falls_back_to_bare_version_without_a_stamp(self):
        with TemporaryDirectory() as tmp:
            stdout = run_version(copy_tree(Path(tmp)))

        self.assertRegex(stdout, r"^quarry \d+\.\d+\.\d+\n$")

    def test_does_not_consult_git_at_run_time(self):
        """A stamped copy must report its pin even with git off the PATH."""
        with TemporaryDirectory() as tmp:
            quarry = copy_tree(Path(tmp))
            write_stamp(quarry, PINNED)

            out = subprocess.run(
                [sys.executable, str(quarry), "--version"],
                capture_output=True, text=True, check=True,
                env={"PATH": "/nonexistent", "HOME": tmp},
            )

        self.assertEqual(out.stdout, "quarry 0.1.0 (2019-04-02, abc1234)\n")
        self.assertEqual(out.stderr.strip(), "")

    def test_rejects_a_short_version_flag(self):
        """--version is the only spelling; -V falls through to the usage error."""
        out = subprocess.run(
            [sys.executable, str(ROOT / "bin" / "quarry"), "-V"],
            capture_output=True, text=True,
        )

        self.assertEqual(out.returncode, 2)
        self.assertEqual(out.stdout, "")
        self.assertIn("unknown command '-V'", out.stderr)

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
