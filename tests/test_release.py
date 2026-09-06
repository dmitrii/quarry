# PURPOSE: Tests the version arithmetic behind `make release` and the target
# itself — that it bumps the constant, commits, tags, refuses to run on a dirty
# tree or over an existing tag, and never pushes.

from __future__ import annotations

import subprocess
import sys
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from support import ROOT, git, scratch_repo, shipped_version

sys.path.insert(0, str(ROOT / "tools"))
import bump_version  # noqa: E402

# What the target should produce from wherever the version currently stands.
# Computed rather than named, so a release does not break these tests; the
# arithmetic itself is pinned against literals by ArithmeticTest below.
CURRENT = shipped_version()
NEXT_MINOR = bump_version.next_version(CURRENT, "minor")
NEXT_MAJOR = bump_version.next_version(CURRENT, "major")


def make_release(repo: Path, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run(["make", "release", *args],
                          cwd=repo, capture_output=True, text=True)


def version_in(repo: Path) -> str:
    return bump_version.read_version(repo / "bin" / "quarry")


class ArithmeticTest(unittest.TestCase):
    def test_minor_is_the_default_and_resets_patch(self):
        self.assertEqual(bump_version.next_version("0.1.0", "minor"), "0.2.0")
        self.assertEqual(bump_version.next_version("0.1.4", "minor"), "0.2.0")

    def test_major_resets_minor_and_patch(self):
        self.assertEqual(bump_version.next_version("0.1.0", "major"), "1.0.0")
        self.assertEqual(bump_version.next_version("1.2.3", "major"), "2.0.0")

    def test_patch_increments_only_the_last_field(self):
        self.assertEqual(bump_version.next_version("0.1.0", "patch"), "0.1.1")
        self.assertEqual(bump_version.next_version("1.2.9", "patch"), "1.2.10")

    def test_rejects_an_unknown_bump(self):
        with self.assertRaises(ValueError):
            bump_version.next_version("0.1.0", "sideways")

    def test_rejects_an_unparseable_version(self):
        with self.assertRaises(ValueError):
            bump_version.next_version("0.1", "minor")


class ReleaseTargetTest(unittest.TestCase):
    def test_bumps_commits_and_tags(self):
        with TemporaryDirectory() as tmp:
            repo = scratch_repo(Path(tmp) / "quarry")
            out = make_release(repo)

            self.assertEqual(out.returncode, 0, out.stderr)
            self.assertEqual(version_in(repo), NEXT_MINOR)
            self.assertEqual(git(repo, "tag", "--list"), f"v{NEXT_MINOR}")
            self.assertEqual(git(repo, "log", "-1", "--format=%s"),
                             f"version: {NEXT_MINOR}")
            # The bump is the only thing in the commit.
            self.assertEqual(git(repo, "show", "--name-only", "--format=", "HEAD"),
                             "bin/quarry")

        self.assertIn("git push --follow-tags", out.stdout)

    def test_honors_an_explicit_bump(self):
        with TemporaryDirectory() as tmp:
            repo = scratch_repo(Path(tmp) / "quarry")
            out = make_release(repo, "BUMP=major")

            self.assertEqual(out.returncode, 0, out.stderr)
            self.assertEqual(version_in(repo), NEXT_MAJOR)
            self.assertEqual(git(repo, "tag", "--list"), f"v{NEXT_MAJOR}")

    def test_restamps_so_the_release_reports_no_distance(self):
        """Otherwise --version keeps counting from the previous tag."""
        with TemporaryDirectory() as tmp:
            repo = scratch_repo(Path(tmp) / "quarry")
            git(repo, "tag", "-a", "v0.0.1", "-m", "an earlier release")
            git(repo, "commit", "-q", "--allow-empty", "-m", "after one")
            git(repo, "commit", "-q", "--allow-empty", "-m", "after two")

            out = make_release(repo)

            self.assertEqual(out.returncode, 0, out.stderr)
            stamp = (repo / "src" / "build_stamp.py").read_text(encoding="utf-8")
            self.assertIn("COMMITS_SINCE_RELEASE = 0", stamp)

            version = subprocess.run([sys.executable, str(repo / "bin" / "quarry"),
                                      "--version"],
                                     capture_output=True, text=True, check=True)

        self.assertTrue(version.stdout.startswith(f"quarry {NEXT_MINOR} ("),
                        version.stdout)

    def test_refuses_a_dirty_tree(self):
        with TemporaryDirectory() as tmp:
            repo = scratch_repo(Path(tmp) / "quarry")
            (repo / "README.md").write_text("scribble\n", encoding="utf-8")

            out = make_release(repo)

            self.assertNotEqual(out.returncode, 0)
            self.assertIn("dirty", out.stderr)
            self.assertEqual(version_in(repo), CURRENT)
            self.assertEqual(git(repo, "tag", "--list"), "")

    def test_refuses_when_the_tag_already_exists(self):
        with TemporaryDirectory() as tmp:
            repo = scratch_repo(Path(tmp) / "quarry")
            git(repo, "tag", f"v{NEXT_MINOR}")

            out = make_release(repo)

            self.assertNotEqual(out.returncode, 0)
            self.assertIn(f"v{NEXT_MINOR}", out.stderr)
            # Refused before touching the file.
            self.assertEqual(version_in(repo), CURRENT)

    def test_rejects_a_bad_bump_without_changing_anything(self):
        with TemporaryDirectory() as tmp:
            repo = scratch_repo(Path(tmp) / "quarry")

            out = make_release(repo, "BUMP=sideways")

            self.assertNotEqual(out.returncode, 0)
            self.assertEqual(version_in(repo), CURRENT)
            self.assertEqual(git(repo, "tag", "--list"), "")


if __name__ == "__main__":
    unittest.main()
