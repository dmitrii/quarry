# PURPOSE: Tests that scripted (non-interactive) sessions — whose opening prompt
# was enqueued before the first turn — are detected, hidden from `quarry ls` by
# default, and revealed by -a. A mid-session type-ahead must NOT count as scripted.

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

MANUAL = "aaaaaaaa-0000-0000-0000-000000000000"
SCRIPTED = "bbbbbbbb-1111-1111-1111-111111111111"


def write_log(root: Path, name: str, lines: list[str]) -> Path:
    d = root / "projects" / "-Users-x-Code-real"
    d.mkdir(parents=True, exist_ok=True)
    p = d / f"{name}.jsonl"
    p.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return p


def _user(uuid, ts="10:00:01"):
    return ('{"type":"user","sessionId":"%s","cwd":"/Users/x/Code/real",'
            '"timestamp":"2025-08-12T%s.000Z",'
            '"message":{"role":"user","content":"go"}}' % (uuid, ts))


def _asst(uuid, ts="10:00:05"):
    return ('{"type":"assistant","sessionId":"%s","isSidechain":false,'
            '"timestamp":"2025-08-12T%s.000Z",'
            '"message":{"role":"assistant","content":[{"type":"text","text":"ok"}]}}'
            % (uuid, ts))


def _enqueue(uuid, ts="10:00:00"):
    return ('{"type":"queue-operation","operation":"enqueue","sessionId":"%s",'
            '"timestamp":"2025-08-12T%s.000Z","content":"do the probe"}' % (uuid, ts))


def manual_log():
    return [_user(MANUAL), _asst(MANUAL)]


def scripted_log():
    # opening prompt enqueued *before* any turn = programmatic launch
    return [_enqueue(SCRIPTED), _user(SCRIPTED), _asst(SCRIPTED)]


def typeahead_log():
    # a queue-operation *after* a turn is an interactive type-ahead, not scripting
    return [_user(MANUAL), _asst(MANUAL), _enqueue(MANUAL, "10:00:06")]


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


class Detection(Base):
    def test_enqueue_before_first_turn_is_scripted(self):
        p = write_log(self.root, SCRIPTED, scripted_log())
        s = cs.load_session(p)
        self.assertTrue(s.is_scripted)
        self.assertFalse(s.is_sidechain)

    def test_typeahead_after_turn_is_not_scripted(self):
        p = write_log(self.root, MANUAL, typeahead_log())
        self.assertFalse(cs.load_session(p).is_scripted)

    def test_plain_session_is_not_scripted(self):
        p = write_log(self.root, MANUAL, manual_log())
        self.assertFalse(cs.load_session(p).is_scripted)


class Listing(Base):
    def _run(self, *args):
        env = dict(os.environ, CLAUDE_CONFIG_DIR=str(self.root),
                   XDG_CACHE_HOME=self._cache.name)
        return subprocess.run([sys.executable, QUARRY, *args],
                              capture_output=True, text=True, env=env, check=True)

    def _fixture(self):
        write_log(self.root, MANUAL, manual_log())
        write_log(self.root, SCRIPTED, scripted_log())

    def test_default_ls_hides_scripted(self):
        self._fixture()
        uuids = self._run("ls", "--tsv").stdout
        self.assertIn(MANUAL, uuids)
        self.assertNotIn(SCRIPTED, uuids)

    def test_dash_a_reveals_scripted(self):
        self._fixture()
        uuids = self._run("ls", "-a", "--tsv").stdout
        self.assertIn(MANUAL, uuids)
        self.assertIn(SCRIPTED, uuids)


if __name__ == "__main__":
    unittest.main()
