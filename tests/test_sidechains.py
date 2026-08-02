# PURPOSE: Tests that subagent sidechain transcripts (agent-*.jsonl) are kept
# out of the listing, counted against their parent session, and removed with it.

from __future__ import annotations

import os
import subprocess
import sys
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

QUARRY = os.path.join(os.path.dirname(__file__), "..", "bin", "quarry")

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))
import claude_sessions as cs  # noqa: E402

PARENT = "d0809692-f479-402f-b302-4c880634577a"


def write_log(root: Path, project: str, name: str, lines: list[str]) -> Path:
    d = root / "projects" / project
    d.mkdir(parents=True, exist_ok=True)
    p = d / f"{name}.jsonl"
    p.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return p


def real_log(uuid: str) -> list[str]:
    return [
        '{"type":"user","sessionId":"%s","cwd":"/Users/x/Code/real",'
        '"timestamp":"2025-08-12T10:00:00.000Z",'
        '"message":{"role":"user","content":"hello"}}' % uuid,
        '{"type":"assistant","sessionId":"%s","isSidechain":false,'
        '"timestamp":"2025-08-12T10:00:05.000Z",'
        '"message":{"role":"assistant","content":[{"type":"text","text":"hi"}]}}' % uuid,
    ]


def sidechain_log(parent: str) -> list[str]:
    # A subagent transcript: every turn is isSidechain, and sessionId points at
    # the parent rather than at this file's own name.
    return [
        '{"type":"user","isSidechain":true,"sessionId":"%s","cwd":"/Users/x/Code/real",'
        '"timestamp":"2025-08-12T10:01:00.000Z",'
        '"message":{"role":"user","content":"do subtask"}}' % parent,
        '{"type":"assistant","isSidechain":true,"sessionId":"%s",'
        '"timestamp":"2025-08-12T10:01:03.000Z",'
        '"message":{"role":"assistant","content":[{"type":"text","text":"done"}]}}' % parent,
    ]


class Base(unittest.TestCase):
    def setUp(self):
        self._td = TemporaryDirectory()
        self.root = Path(self._td.name)
        self._cache = TemporaryDirectory()
        self._prev = os.environ.get("XDG_CACHE_HOME")
        os.environ["XDG_CACHE_HOME"] = self._cache.name

    def tearDown(self):
        if self._prev is None:
            os.environ.pop("XDG_CACHE_HOME", None)
        else:
            os.environ["XDG_CACHE_HOME"] = self._prev
        self._td.cleanup()
        self._cache.cleanup()


class Sidechains(Base):
    def _fixture(self):
        proj = "-Users-x-Code-real"
        write_log(self.root, proj, PARENT, real_log(PARENT))
        a = write_log(self.root, proj, "agent-11111111", sidechain_log(PARENT))
        b = write_log(self.root, proj, "agent-22222222", sidechain_log(PARENT))
        return proj, a, b

    def test_load_session_marks_sidechain_and_parent(self):
        _, a, _ = self._fixture()
        s = cs.load_session(a)
        self.assertIsNotNone(s)
        self.assertTrue(s.is_sidechain)
        self.assertEqual(s.parent, PARENT)

    def test_sidechains_are_not_listed(self):
        self._fixture()
        sessions = cs.discover(self.root)
        self.assertEqual([s.uuid for s in sessions], [PARENT])

    def test_parent_counts_its_sidechains(self):
        _, a, b = self._fixture()
        (parent,) = cs.discover(self.root)
        self.assertEqual(sorted(parent.sidechains), sorted([a, b]))

    def test_artifacts_include_sidechains(self):
        _, a, b = self._fixture()
        (parent,) = cs.discover(self.root)
        arts = cs.artifacts(self.root, parent)
        self.assertIn(a, arts)
        self.assertIn(b, arts)

    def _run(self, *args):
        env = dict(os.environ, CLAUDE_CONFIG_DIR=str(self.root),
                   XDG_CACHE_HOME=self._cache.name)
        return subprocess.run([sys.executable, QUARRY, *args],
                              capture_output=True, text=True, env=env, check=True)

    def test_long_listing_shows_sidechain_count(self):
        self._fixture()
        out = self._run("ls", "-l", "--color", "never").stdout
        self.assertIn("↳2", out)

    def test_detail_shows_sidechain_count(self):
        self._fixture()
        out = self._run("ls", PARENT, "--color", "never").stdout
        self.assertRegex(out, r"Sidechains\s+2")


if __name__ == "__main__":
    unittest.main()
