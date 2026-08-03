# PURPOSE: Tests the search config (~/.config/quarry/config.ini) and what each
# fzf scope searches — visible replies, chain-of-thought, and sidechains.

from __future__ import annotations

import os
import subprocess
import sys
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))
import claude_sessions as cs  # noqa: E402

QUARRY = os.path.join(os.path.dirname(__file__), "..", "bin", "quarry")
PARENT = "d0809692-f479-402f-b302-4c880634577a"

PROMPT_TOKEN = "PLEASEFIXTHEBUG"
REPLY_TOKEN = "VISIBLEREPLYTOKEN"
THINK_TOKEN = "CHAINOFTHOUGHTTOKEN"
SIDE_TOKEN = "SUBAGENTREPLYTOKEN"


def real_log(uuid: str) -> list[str]:
    return [
        '{"type":"user","sessionId":"%s","cwd":"/Users/x/Code/real",'
        '"timestamp":"2025-08-12T10:00:00.000Z",'
        '"message":{"role":"user","content":"%s"}}' % (uuid, PROMPT_TOKEN),
        '{"type":"assistant","sessionId":"%s","timestamp":"2025-08-12T10:00:05.000Z",'
        '"message":{"role":"assistant","content":['
        '{"type":"thinking","thinking":"%s"},'
        '{"type":"text","text":"%s"}]}}' % (uuid, THINK_TOKEN, REPLY_TOKEN),
    ]


def sidechain_log(parent: str) -> list[str]:
    return [
        '{"type":"assistant","isSidechain":true,"sessionId":"%s",'
        '"cwd":"/Users/x/Code/real","timestamp":"2025-08-12T10:01:00.000Z",'
        '"message":{"role":"assistant","content":[{"type":"text","text":"%s"}]}}'
        % (parent, SIDE_TOKEN),
    ]


def write_log(root: Path, project: str, name: str, lines: list[str]) -> Path:
    d = root / "projects" / project
    d.mkdir(parents=True, exist_ok=True)
    p = d / f"{name}.jsonl"
    p.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return p


class Base(unittest.TestCase):
    def setUp(self):
        self._td = TemporaryDirectory()
        self.root = Path(self._td.name)
        self._cfg = TemporaryDirectory()
        self._cache = TemporaryDirectory()
        self._env = {}
        for k, v in (("XDG_CONFIG_HOME", self._cfg.name),
                     ("XDG_CACHE_HOME", self._cache.name)):
            self._env[k] = os.environ.get(k)
            os.environ[k] = v

    def tearDown(self):
        for k, v in self._env.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v
        self._td.cleanup()
        self._cfg.cleanup()
        self._cache.cleanup()

    def write_config(self, body: str):
        d = Path(self._cfg.name) / "quarry"
        d.mkdir(parents=True, exist_ok=True)
        (d / "config.ini").write_text(body, encoding="utf-8")


class SearchConfig(Base):
    def test_defaults_are_true(self):
        cfg = cs.load_search_config()
        self.assertTrue(cfg.sidechains)
        self.assertTrue(cfg.thinking)

    def test_reads_false_values(self):
        self.write_config("[search]\nsidechains = false\nthinking = false\n")
        cfg = cs.load_search_config()
        self.assertFalse(cfg.sidechains)
        self.assertFalse(cfg.thinking)

    def test_broken_file_falls_back_to_defaults(self):
        self.write_config("this is not ini {{{")
        cfg = cs.load_search_config()
        self.assertTrue(cfg.sidechains)
        self.assertTrue(cfg.thinking)


class ScanText(Base):
    def _log(self):
        return write_log(self.root, "-Users-x-Code-real", PARENT, real_log(PARENT))

    def test_prompts_scope_has_prompt_not_reply(self):
        p = self._log()
        blob = cs.scan_text(p, replies=False, thinking=False)
        self.assertIn(PROMPT_TOKEN, blob)
        self.assertNotIn(REPLY_TOKEN, blob)

    def test_replies_includes_visible_reply(self):
        p = self._log()
        blob = cs.scan_text(p, replies=True, thinking=False)
        self.assertIn(REPLY_TOKEN, blob)
        self.assertNotIn(THINK_TOKEN, blob)

    def test_thinking_flag_includes_chain_of_thought(self):
        p = self._log()
        blob = cs.scan_text(p, replies=True, thinking=True)
        self.assertIn(THINK_TOKEN, blob)

    def test_sidechain_records_gated_by_include_sidechain(self):
        p = write_log(self.root, "-Users-x-Code-real", "agent-1", sidechain_log(PARENT))
        self.assertNotIn(SIDE_TOKEN, cs.scan_text(p, replies=True, thinking=False))
        self.assertIn(SIDE_TOKEN, cs.scan_text(p, replies=True, thinking=False,
                                               include_sidechain=True))


class TsvEndToEnd(Base):
    def _fixture(self):
        proj = "-Users-x-Code-real"
        write_log(self.root, proj, PARENT, real_log(PARENT))
        write_log(self.root, proj, "agent-11111111", sidechain_log(PARENT))

    def _parent_row(self):
        env = dict(os.environ, CLAUDE_CONFIG_DIR=str(self.root))
        out = subprocess.run([sys.executable, QUARRY, "ls", "--tsv", "--scope", "replies"],
                             capture_output=True, text=True, env=env, check=True).stdout
        for line in out.splitlines():
            if line.startswith(PARENT):
                return line
        return ""

    def test_defaults_include_sidechain_and_thinking(self):
        self._fixture()
        row = self._parent_row()
        self.assertIn(SIDE_TOKEN, row)
        self.assertIn(THINK_TOKEN, row)

    def test_config_can_disable_both(self):
        self._fixture()
        self.write_config("[search]\nsidechains = false\nthinking = false\n")
        row = self._parent_row()
        self.assertNotIn(SIDE_TOKEN, row)
        self.assertNotIn(THINK_TOKEN, row)
        self.assertIn(REPLY_TOKEN, row)   # visible replies still searched


if __name__ == "__main__":
    unittest.main()
