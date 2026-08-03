# PURPOSE: Tests for `quarry rename` — config, template rendering, variable
# resolution, summary generation, the custom-title append, and selection.

from __future__ import annotations

import configparser
import os
import sys
import unittest
from datetime import datetime, timezone
from pathlib import Path
from tempfile import TemporaryDirectory

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))
import claude_sessions as cs  # noqa: E402


class ConfigBase(unittest.TestCase):
    def setUp(self):
        self._cfg = TemporaryDirectory()
        self._prev = os.environ.get("XDG_CONFIG_HOME")
        os.environ["XDG_CONFIG_HOME"] = self._cfg.name

    def tearDown(self):
        if self._prev is None:
            os.environ.pop("XDG_CONFIG_HOME", None)
        else:
            os.environ["XDG_CONFIG_HOME"] = self._prev
        self._cfg.cleanup()

    def write_config(self, body: str):
        d = Path(self._cfg.name) / "quarry"
        d.mkdir(parents=True, exist_ok=True)
        (d / "config.ini").write_text(body, encoding="utf-8")


class RenameConfigTests(ConfigBase):
    def test_defaults_when_absent(self):
        cfg = cs.load_rename_config()
        self.assertEqual(cfg.default_template, "${AI_TITLE:-$SUMMARY}")
        self.assertEqual(cfg.summary_context_chars, 8000)
        self.assertEqual(cfg.summary_timeout_secs, 60)
        self.assertIn("claude -p", cfg.summary_command)

    def test_overrides_and_percent_signs_are_safe(self):
        self.write_config("[rename]\n"
                          "default_template = $START_DATE-$SUMMARY\n"
                          "summary_command = printf %s hi\n"
                          "summary_context_chars = 1234\n")
        cfg = cs.load_rename_config()
        self.assertEqual(cfg.default_template, "$START_DATE-$SUMMARY")
        self.assertEqual(cfg.summary_command, "printf %s hi")  # '%' kept literal
        self.assertEqual(cfg.summary_context_chars, 1234)

    def test_broken_file_falls_back(self):
        self.write_config("not ini {{{")
        self.assertEqual(cs.load_rename_config().default_template, "${AI_TITLE:-$SUMMARY}")


class ConfigExampleSyncTests(unittest.TestCase):
    def test_example_matches_code_defaults(self):
        example = Path(__file__).resolve().parents[1] / "config.ini.example"
        p = configparser.ConfigParser(interpolation=None)
        p.read(example, encoding="utf-8")
        d = cs.RenameConfig()
        self.assertEqual(p.get("rename", "default_template"), d.default_template)
        self.assertEqual(p.get("rename", "summary_command"), d.summary_command)
        self.assertEqual(p.getint("rename", "summary_context_chars"),
                         d.summary_context_chars)
        self.assertEqual(p.getint("rename", "summary_timeout_secs"),
                         d.summary_timeout_secs)


class HelperTests(unittest.TestCase):
    def test_slugify(self):
        self.assertEqual(cs.slugify("Clean up: Worktrees, demo!"), "clean-up-worktrees-demo")
        self.assertEqual(cs.slugify(""), "")

    def test_normalize_title_collapses_and_preserves_case(self):
        self.assertEqual(cs.normalize_title("2026-08-02--Fix  thing"), "2026-08-02-Fix-thing")
        self.assertEqual(cs.normalize_title('  "quoted"  '), "quoted")

    def test_git_project(self):
        with TemporaryDirectory() as td:
            repo = Path(td) / "MyRepo"
            (repo / "sub").mkdir(parents=True)
            (repo / ".git").mkdir()
            self.assertEqual(cs.git_project(str(repo / "sub")), "MyRepo")
            self.assertEqual(cs.git_project(str(Path(td))), "")

    def test_free_variable(self):
        s = cs.Session(uuid="d0809692-f479-402f-b302-4c880634577a", title=None,
                       cwd="/Users/x/Code/quarry",
                       started=datetime(2025, 8, 12, 12, 0, tzinfo=timezone.utc),
                       last=datetime(2025, 8, 19, 12, 0, tzinfo=timezone.utc),
                       ai_title="Fix the Flaky Test")
        self.assertEqual(cs.free_variable(s, "LAUNCH_DIR"), "quarry")
        self.assertEqual(cs.free_variable(s, "UUID8"), "d0809692")
        self.assertEqual(cs.free_variable(s, "AI_TITLE"), "fix-the-flaky-test")
        self.assertEqual(cs.free_variable(s, "START_DATE"), "2025-08-12")
        self.assertIsNone(cs.free_variable(s, "SUMMARY"))


if __name__ == "__main__":
    unittest.main()
