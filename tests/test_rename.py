# PURPOSE: Tests for `quarry rename` — config, template rendering, variable
# resolution, summary generation, the custom-title append, and selection.

from __future__ import annotations

import configparser
import json
import os
import sys
import unittest
from datetime import datetime, timezone
from pathlib import Path
from tempfile import TemporaryDirectory

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))
import claude_sessions as cs  # noqa: E402
import cmd_rename  # noqa: E402


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
        self.assertEqual(p.getint("rename", "summary_max_words"),
                         d.summary_max_words)


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

    def test_caps_runaway_output_to_max_words(self):
        with TemporaryDirectory() as td:
            s = self._session(td)
            cfg = cs.RenameConfig(
                summary_command="printf 'one two three four five six seven eight nine ten'",
                summary_max_words=8)
            self.assertEqual(cs.generate_summary(s, cfg),
                             "one-two-three-four-five-six-seven-eight")


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


class ResolveTargetsTests(unittest.TestCase):
    def _mk(self, uuid, title=None, ai_title=None):
        return cs.Session(uuid=uuid, title=title, cwd="/x", started=None, last=None,
                          ai_title=ai_title)

    def test_glob_skips_titled_unless_retitle(self):
        sess = [self._mk("aaaa1111"), self._mk("aaaa2222", title="already")]
        got = cmd_rename.resolve_targets(sess, "aaaa*", retitle=False, root=None, cwd="/x")
        self.assertEqual([s.uuid for s in got], ["aaaa1111"])
        got = cmd_rename.resolve_targets(sess, "aaaa*", retitle=True, root=None, cwd="/x")
        self.assertEqual(sorted(s.uuid for s in got), ["aaaa1111", "aaaa2222"])

    def test_single_explicit_titled_is_kept(self):
        sess = [self._mk("aaaa2222", title="already")]
        got = cmd_rename.resolve_targets(sess, "aaaa2222", retitle=False, root=None, cwd="/x")
        self.assertEqual([s.uuid for s in got], ["aaaa2222"])

    def test_uuid_prefix_matches(self):
        sess = [self._mk("d0809692-f479-402f-b302-4c880634577a")]
        got = cmd_rename.resolve_targets(sess, "d0809692", retitle=False, root=None, cwd="/x")
        self.assertEqual([s.uuid for s in got], ["d0809692-f479-402f-b302-4c880634577a"])

    def test_matches_by_name(self):
        sess = [self._mk("uuuu1", title="my-proj")]
        got = cmd_rename.resolve_targets(sess, "my-*", retitle=True, root=None, cwd="/x")
        self.assertEqual([s.uuid for s in got], ["uuuu1"])


class DryRunTests(unittest.TestCase):
    def test_skeleton_leaves_summary_literal(self):
        s = cs.Session(uuid="u", title=None, cwd="/x/quarry", started=None, last=None,
                       ai_title="")
        skeleton = cs.render_template("$LAUNCH_DIR-${AI_TITLE:-$SUMMARY}",
                                      cmd_rename._skeleton_resolver(s))
        self.assertEqual(skeleton, "quarry-$SUMMARY")


class PromptTitleTests(unittest.TestCase):
    def test_replacement_text_used(self):
        self.assertEqual(cmd_rename.prompt_title("proposed", read=lambda p: "edited"),
                         "edited")

    def test_empty_accepts_proposed(self):
        self.assertEqual(cmd_rename.prompt_title("proposed", read=lambda p: "   "),
                         "proposed")

    def test_dash_skips(self):
        self.assertIsNone(cmd_rename.prompt_title("proposed", read=lambda p: "-"))

    def test_empty_with_no_proposal_skips(self):
        self.assertIsNone(cmd_rename.prompt_title("", read=lambda p: ""))

    def test_ctrl_c_propagates(self):
        def boom(p):
            raise KeyboardInterrupt
        with self.assertRaises(KeyboardInterrupt):
            cmd_rename.prompt_title("proposed", read=boom)


class EndToEndApplyTests(unittest.TestCase):
    def test_generate_render_and_apply(self):
        with TemporaryDirectory() as td:
            d = Path(td) / "projects" / "-Users-x-Code-quarry"
            d.mkdir(parents=True)
            p = d / "d0809692-f479-402f-b302-4c880634577a.jsonl"
            p.write_text('{"type":"user","cwd":"/Users/x/Code/quarry",'
                         '"timestamp":"2025-08-12T10:00:00.000Z",'
                         '"message":{"role":"user","content":"Fix flaky test"}}\n',
                         encoding="utf-8")
            s = cs.load_session(p)
            cfg = cs.RenameConfig(default_template="$LAUNCH_DIR-$SUMMARY",
                                  summary_command="printf fixed-flaky-test")
            proposed = cs.render_template(cfg.default_template,
                                          cmd_rename._resolver(s, cfg, None))
            self.assertEqual(proposed, "quarry-fixed-flaky-test")
            chosen = cmd_rename.prompt_title(proposed, read=lambda pr: proposed)
            cs.set_custom_title(s, chosen)
            self.assertEqual(cs.load_session(p).title, "quarry-fixed-flaky-test")


class CommandHelperTests(unittest.TestCase):
    def test_model_override_replaces_existing(self):
        self.assertEqual(
            cmd_rename._apply_model_override("claude -p --model sonnet --tools ''", "opus"),
            "claude -p --model opus --tools ''")

    def test_model_override_appends_when_absent(self):
        self.assertEqual(cmd_rename._apply_model_override("mytool --flag", "opus"),
                         "mytool --flag --model opus")

    def test_model_override_noop_without_model(self):
        self.assertEqual(cmd_rename._apply_model_override("x --model sonnet", None),
                         "x --model sonnet")

    def test_command_name(self):
        self.assertEqual(cmd_rename._command_name("claude -p --model sonnet"), "claude")
        self.assertEqual(cmd_rename._command_name("/usr/local/bin/mytool --x"), "mytool")


class GenerateProgressTests(unittest.TestCase):
    def test_wrapper_delegates_without_tty(self):
        with TemporaryDirectory() as td:
            d = Path(td) / "projects" / "-Users-x-Code-real"
            d.mkdir(parents=True)
            p = d / "s.jsonl"
            p.write_text('{"type":"user","cwd":"/x",'
                         '"timestamp":"2025-08-12T10:00:00.000Z",'
                         '"message":{"role":"user","content":"hello world"}}\n',
                         encoding="utf-8")
            s = cs.load_session(p)
            cfg = cs.RenameConfig(summary_command="printf my-title")
            self.assertEqual(cmd_rename._generate_summary(s, cfg), "my-title")


class RefExtractionTests(unittest.TestCase):
    def _session(self, td, content, *, extra_lines=()):
        d = Path(td) / "projects" / "-Users-x-Code-real"
        d.mkdir(parents=True, exist_ok=True)
        p = d / "s.jsonl"
        head = ('{"type":"user","cwd":"/x","timestamp":"2025-08-12T10:00:00.000Z",'
                '"message":{"role":"user","content":%s}}' % json.dumps(content))
        p.write_text("\n".join([head, *extra_lines]) + "\n", encoding="utf-8")
        return cs.Session(uuid="u", title=None, cwd="/x", started=None, last=None, path=p)

    def _asst(self, text):
        return ('{"type":"assistant","message":{"role":"assistant","content":'
                '[{"type":"text","text":%s}]}}' % json.dumps(text))

    # ── Linear: NAME on every link, KEY on issues, HEX on reviews ─────────
    def test_linear_issue_key_name_no_hex(self):
        self.assertEqual(
            cs._extract_linear("see https://linear.app/acme/issue/ENG-42/fix-the-thing"),
            {"LINEAR_KEY": "eng-42", "LINEAR_NAME": "fix-the-thing", "LINEAR_HEX": ""})

    def test_linear_review_keyed(self):
        got = cs._extract_linear("https://linear.app/acme/review/eng-1234-add-retry-to"
                                 "-webhook-consumer-85a54f16c809")
        self.assertEqual(got["LINEAR_KEY"], "eng-1234")
        self.assertEqual(got["LINEAR_NAME"], "add-retry-to-webhook-consumer")
        self.assertEqual(got["LINEAR_HEX"], "85a54f")

    def test_linear_review_keyless(self):
        got = cs._extract_linear("https://linear.app/acme/review/docs-add-tuning-guide"
                                 "-for-large-eval-sets-5ab17a82f382")
        self.assertEqual(got["LINEAR_KEY"], "")
        self.assertEqual(got["LINEAR_NAME"], "docs-add-tuning-guide-for-large-eval-sets")
        self.assertEqual(got["LINEAR_HEX"], "5ab17a")

    def test_linear_project_keyless(self):
        got = cs._extract_linear("https://linear.app/acme/project/q3-reliability-72575f7e716e")
        self.assertEqual(got["LINEAR_KEY"], "")
        self.assertEqual(got["LINEAR_NAME"], "q3-reliability")
        self.assertEqual(got["LINEAR_HEX"], "72575f")
        self.assertEqual(len(got["LINEAR_HEX"]), cs.LINEAR_HEX_LEN)

    def test_linear_url_wrapped_in_markup_or_parens(self):
        # a slash command stores the URL inside <command-args>…</command-args>;
        # the slug must not swallow the trailing tag (regression).
        u = "https://linear.app/acme/review/some-title-5ab17a82f382"
        for wrapped in (f"<command-args>{u}</command-args>", f"(see {u})", f"<{u}>"):
            got = cs._extract_linear(wrapped)
            self.assertEqual(got["LINEAR_HEX"], "5ab17a", wrapped)
            self.assertEqual(got["LINEAR_NAME"], "some-title", wrapped)

    def test_hex_only_when_it_has_a_digit(self):
        self.assertEqual(
            cs._extract_linear("linear.app/w/review/some-title-85a54f16c809")["LINEAR_HEX"],
            "85a54f")
        # trailing all-letter hex word (no digit) is a slug word, not an id
        got = cs._extract_linear("linear.app/w/review/cafe-deadbeef")
        self.assertEqual(got["LINEAR_HEX"], "")
        self.assertEqual(got["LINEAR_NAME"], "cafe-deadbeef")

    def test_linear_is_first_prompt_only(self):
        with TemporaryDirectory() as td:
            later = self._asst("https://linear.app/acme/issue/ENG-9/late-link")
            s = self._session(td, "first prompt, no linear link", extra_lines=[later])
            self.assertEqual(cs.free_variable(s, "LINEAR_KEY"), "")
            self.assertEqual(cs.free_variable(s, "LINEAR_NAME"), "")

    def test_first_prompt_skips_tool_result_turn(self):
        with TemporaryDirectory() as td:
            d = Path(td) / "projects" / "-Users-x-Code-real"
            d.mkdir(parents=True)
            p = d / "s.jsonl"
            tool_result = ('{"type":"user","cwd":"/x","timestamp":"2025-08-12T10:00:00.000Z",'
                           '"message":{"role":"user","content":'
                           '[{"type":"tool_result","content":"x"}]}}')
            real = ('{"type":"user","cwd":"/x","timestamp":"2025-08-12T10:00:01.000Z",'
                    '"message":{"role":"user","content":'
                    '"https://linear.app/acme/issue/ENG-3/the-real-one"}}')
            p.write_text(tool_result + "\n" + real + "\n", encoding="utf-8")
            s = cs.Session(uuid="u", title=None, cwd="/x", started=None, last=None, path=p)
            self.assertEqual(cs.free_variable(s, "LINEAR_KEY"), "eng-3")

    # ── GitHub: first prompt preferred, whole-transcript PR fallback ──────
    def test_github_pr_in_first_prompt(self):
        with TemporaryDirectory() as td:
            s = self._session(td, "review https://github.com/acme/webapp/pull/1234 please")
            self.assertEqual(cs.free_variable(s, "GH_REPO"), "webapp")
            self.assertEqual(cs.free_variable(s, "GH_PR"), "1234")
            self.assertEqual(cs.free_variable(s, "GH_REPO_PR"), "webapp-pr1234")

    def test_github_scp_repo_only_degrades(self):
        with TemporaryDirectory() as td:
            s = self._session(td, "clone git@github.com:acme/platform-infra.git")
            self.assertEqual(cs.free_variable(s, "GH_REPO"), "platform-infra")
            self.assertEqual(cs.free_variable(s, "GH_PR"), "")
            self.assertEqual(cs.free_variable(s, "GH_REPO_PR"), "platform-infra")

    def test_github_repo_trailing_punctuation(self):
        with TemporaryDirectory() as td:
            s = self._session(td, "(see https://github.com/acme/scoring-tool)")
            self.assertEqual(cs.free_variable(s, "GH_REPO"), "scoring-tool")

    def test_github_pr_from_transcript_fallback(self):
        # first prompt is a keyless Linear review link; the PR appears later
        with TemporaryDirectory() as td:
            later = self._asst("opened https://github.com/acme/platform-infra/pull/977")
            s = self._session(td, "reviewing https://linear.app/acme/review/some-title-5ab17a82f382",
                              extra_lines=[later])
            self.assertEqual(cs.free_variable(s, "GH_PR"), "977")
            self.assertEqual(cs.free_variable(s, "GH_REPO"), "platform-infra")
            self.assertEqual(cs.free_variable(s, "GH_REPO_PR"), "platform-infra-pr977")

    def test_github_first_prompt_pr_preferred_over_later(self):
        with TemporaryDirectory() as td:
            later = self._asst("also https://github.com/acme/other/pull/2")
            s = self._session(td, "https://github.com/acme/webapp/pull/1", extra_lines=[later])
            self.assertEqual(cs.free_variable(s, "GH_REPO_PR"), "webapp-pr1")

    def test_github_absent_is_empty_and_template_falls_back(self):
        with TemporaryDirectory() as td:
            s = self._session(td, "no links in this one")
            self.assertEqual(cs.free_variable(s, "GH_REPO_PR"), "")
            r = cmd_rename._resolver(s, cs.RenameConfig(), None)
            self.assertEqual(cs.render_template("${GH_REPO_PR:-none}", r), "none")


if __name__ == "__main__":
    unittest.main()
