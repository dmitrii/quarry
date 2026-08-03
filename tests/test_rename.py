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


class RenderTests(unittest.TestCase):
    def _resolver(self, values, log=None):
        def resolve(name):
            if log is not None:
                log.append(name)
            return values.get(name, "")
        return resolve

    def test_plain_and_braced(self):
        r = self._resolver({"START_DATE": "2025-08-12", "SUMMARY": "fix-bug"})
        self.assertEqual(cs.render_template("$START_DATE-$SUMMARY", r), "2025-08-12-fix-bug")
        self.assertEqual(cs.render_template("${START_DATE}_x", r), "2025-08-12_x")

    def test_empty_segment_collapses(self):
        r = self._resolver({"START_DATE": "2025-08-12", "GIT_PROJECT": "", "SUMMARY": "fix"})
        self.assertEqual(cs.render_template("$START_DATE-$GIT_PROJECT-$SUMMARY", r),
                         "2025-08-12-fix")

    def test_fallback_used_when_empty(self):
        r = self._resolver({"AI_TITLE": "", "SUMMARY": "generated-title"})
        self.assertEqual(cs.render_template("${AI_TITLE:-$SUMMARY}", r), "generated-title")

    def test_fallback_is_lazy(self):
        log = []
        r = self._resolver({"AI_TITLE": "existing-title", "SUMMARY": "should-not-run"}, log)
        self.assertEqual(cs.render_template("${AI_TITLE:-$SUMMARY}", r), "existing-title")
        self.assertNotIn("SUMMARY", log)  # generator never consulted


class GenerateSummaryTests(unittest.TestCase):
    def _session(self, td):
        d = Path(td) / "projects" / "-Users-x-Code-real"
        d.mkdir(parents=True)
        p = d / "s.jsonl"
        p.write_text(
            '{"type":"user","cwd":"/x","timestamp":"2025-08-12T10:00:00.000Z",'
            '"message":{"role":"user","content":"Fix the Flaky Test please"}}\n',
            encoding="utf-8")
        return cs.Session(uuid="u", title=None, cwd="/x", started=None, last=None, path=p)

    def test_pipes_context_and_slugifies_output(self):
        with TemporaryDirectory() as td:
            s = self._session(td)
            cfg = cs.RenameConfig(summary_command="head -c 8")  # -> "Fix the "
            self.assertEqual(cs.generate_summary(s, cfg), "fix-the")

    def test_empty_output_raises(self):
        with TemporaryDirectory() as td:
            s = self._session(td)
            cfg = cs.RenameConfig(summary_command="true")  # no output
            with self.assertRaises(RuntimeError):
                cs.generate_summary(s, cfg)

    def test_nonzero_exit_raises(self):
        with TemporaryDirectory() as td:
            s = self._session(td)
            cfg = cs.RenameConfig(summary_command="false")
            with self.assertRaises(RuntimeError):
                cs.generate_summary(s, cfg)


class SetCustomTitleTests(unittest.TestCase):
    REAL = ['{"type":"user","cwd":"/x","timestamp":"2025-08-12T10:00:00.000Z",'
            '"message":{"role":"user","content":"hi"}}']

    def _session(self, td):
        d = Path(td) / "projects" / "-Users-x-Code-real"
        d.mkdir(parents=True)
        p = d / "d0809692-f479-402f-b302-4c880634577a.jsonl"
        p.write_text("\n".join(self.REAL) + "\n", encoding="utf-8")
        return cs.load_session(p)

    def test_appends_and_reads_back(self):
        with TemporaryDirectory() as td:
            s = self._session(td)
            cs.set_custom_title(s, "my-title")
            self.assertEqual(cs.load_session(s.path).title, "my-title")

    def test_rejects_empty_title(self):
        with TemporaryDirectory() as td:
            s = self._session(td)
            with self.assertRaises(ValueError):
                cs.set_custom_title(s, "   ")

    def test_rejects_bad_uuid(self):
        with TemporaryDirectory() as td:
            s = self._session(td)
            s.uuid = "not-a-uuid"
            with self.assertRaises(ValueError):
                cs.set_custom_title(s, "x")

    def test_refuses_open_session(self):
        with TemporaryDirectory() as td:
            s = self._session(td)
            s.open = True
            with self.assertRaises(RuntimeError):
                cs.set_custom_title(s, "x")


if __name__ == "__main__":
    unittest.main()
