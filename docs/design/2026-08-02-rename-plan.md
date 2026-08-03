# quarry rename Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add `quarry rename` — bulk/single session titling from outside a session, with a per-item confirm-or-edit UX and pluggable LLM-proposed titles.

**Architecture:** A new `cmd_rename.py` subcommand drives selection and the interactive loop; pure helpers in `claude_sessions.py` do template rendering, variable resolution, summary generation (shell out to `claude`), and the defensive `custom-title` transcript append. Titles persist exactly like `/rename`: one `custom-title` JSON line appended at the file tail.

**Tech Stack:** Python 3.8+ stdlib only (`argparse`, `configparser`, `subprocess`, `readline`, `re`, `json`). `$SUMMARY` shells out to the already-installed `claude` CLI — not a Python dependency.

Full design: `docs/design/2026-08-02-rename.md`.

## Global Constraints

- **Python 3.8+**, **stdlib only, no new Python dependencies** (the `claude` CLI is invoked as a subprocess, not imported).
- New `src/*.py` files open with a module **docstring** header (match `cmd_ls.py`/`cmd_rm.py`); new `tests/*.py` files open with a `# PURPOSE:` line (match existing tests).
- Titles persist **only** as an appended `{"type":"custom-title","customTitle":<title>,"sessionId":<uuid>}` record — never edit `~/.claude.json` or rewrite a transcript.
- Refuse to modify a session that is **currently open** in a live process (as `quarry rm` does).
- Config lives in `~/.config/quarry/config.ini` (honor `$XDG_CONFIG_HOME`); code holds authoritative defaults; `config.ini.example` mirrors them.
- Match surrounding style; `make test` (`python3 -m unittest discover -s tests -p 'test_*.py'`) must pass with **pristine output** (no warnings).

---

### Task 1: `[rename]` config

**Files:**
- Modify: `src/claude_sessions.py` (add after the `[search]` config block, ~line 230)
- Modify: `config.ini.example`
- Test: `tests/test_rename.py` (create)

**Interfaces:**
- Produces: `RenameConfig(default_template: str, summary_command: str, summary_context_chars: int, summary_timeout_secs: int)`; `load_rename_config() -> RenameConfig`; `DEFAULT_SUMMARY_COMMAND: str`.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_rename.py
# PURPOSE: Tests for `quarry rename` — config, template rendering, variable
# resolution, summary generation, the custom-title append, and selection.

from __future__ import annotations

import os
import sys
import unittest
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
                          "summary_command = printf %%s hi\n"
                          "summary_context_chars = 1234\n")
        cfg = cs.load_rename_config()
        self.assertEqual(cfg.default_template, "$START_DATE-$SUMMARY")
        self.assertEqual(cfg.summary_command, "printf %s hi")  # interpolation disabled
        self.assertEqual(cfg.summary_context_chars, 1234)

    def test_broken_file_falls_back(self):
        self.write_config("not ini {{{")
        self.assertEqual(cs.load_rename_config().default_template, "${AI_TITLE:-$SUMMARY}")
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python3 -m unittest tests.test_rename 2>&1 | tail -5` (or `python3 tests/test_rename.py`)
Expected: FAIL — `module 'claude_sessions' has no attribute 'load_rename_config'`

- [ ] **Step 3: Write minimal implementation**

In `src/claude_sessions.py`, after the `load_search_config()` definition:

```python
DEFAULT_SUMMARY_COMMAND = (
    'claude -p --no-session-persistence --model haiku --effort low --tools "" '
    '--system-prompt "You output ONLY a 3-8 word lowercase kebab-case slug '
    'summarizing the session topic. Do not include the project, repo, or tool '
    'name. No dates, no quotes, no punctuation, no explanation."'
)


@dataclass
class RenameConfig:
    default_template: str = "${AI_TITLE:-$SUMMARY}"
    summary_command: str = DEFAULT_SUMMARY_COMMAND
    summary_context_chars: int = 8000
    summary_timeout_secs: int = 60


def load_rename_config() -> RenameConfig:
    cfg = RenameConfig()
    # interpolation=None so a summary_command containing '%' is taken literally.
    parser = configparser.ConfigParser(interpolation=None)
    try:
        if not parser.read(config_ini_path(), encoding="utf-8"):
            return cfg
        return RenameConfig(
            default_template=parser.get("rename", "default_template",
                                        fallback=cfg.default_template),
            summary_command=parser.get("rename", "summary_command",
                                       fallback=cfg.summary_command),
            summary_context_chars=parser.getint("rename", "summary_context_chars",
                                                 fallback=cfg.summary_context_chars),
            summary_timeout_secs=parser.getint("rename", "summary_timeout_secs",
                                               fallback=cfg.summary_timeout_secs),
        )
    except (configparser.Error, OSError, ValueError):
        return cfg
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python3 -m unittest tests.test_rename -v 2>&1 | tail -6`
Expected: PASS (3 tests)

- [ ] **Step 5: Extend `config.ini.example` and add the sync test**

Append to `config.ini.example`:

```ini

[rename]
# What `quarry rename` writes as a title. $VARS are substituted; ${VAR:-fallback}
# uses the fallback when VAR is empty. Free vars: $START_DATE $LAST_ACTIVITY_DATE
# $LAUNCH_DIR $GIT_PROJECT $BRANCH $UUID $UUID8 $AI_TITLE. $SUMMARY is generated.
default_template = ${AI_TITLE:-$SUMMARY}

# $SUMMARY generator. Contract: quarry pipes session context to this command's
# stdin; the command prints the title to stdout. Edit freely (model, effort,
# wording). Default reuses your Claude Code auth and leaves no session on disk.
summary_command = claude -p --no-session-persistence --model haiku --effort low --tools "" --system-prompt "You output ONLY a 3-8 word lowercase kebab-case slug summarizing the session topic. Do not include the project, repo, or tool name. No dates, no quotes, no punctuation, no explanation."

# Max characters of session context fed to summary_command (a cost knob).
summary_context_chars = 8000
# Seconds before a summary_command call is aborted.
summary_timeout_secs = 60
```

Add to `tests/test_rename.py`:

```python
class ConfigExampleSyncTests(unittest.TestCase):
    def test_example_matches_code_defaults(self):
        import configparser
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
```

Run: `python3 -m unittest tests.test_rename -v 2>&1 | tail -6` → PASS (4 tests)

- [ ] **Step 6: Commit**

```bash
git add src/claude_sessions.py config.ini.example tests/test_rename.py
git commit -m "rename: add [rename] config with authoritative defaults"
```

---

### Task 2: Variable resolvers and title normalization

**Files:**
- Modify: `src/claude_sessions.py`
- Test: `tests/test_rename.py`

**Interfaces:**
- Produces: `slugify(text: str) -> str` (lowercase kebab); `normalize_title(text: str) -> str` (case-preserving, collapses separators); `date_ymd(dt: datetime | None) -> str`; `git_project(cwd: str | None) -> str`; `session_branch(session: Session) -> str`; `free_variable(session: Session, name: str) -> str | None` (returns value for instant vars incl. `AI_TITLE`, `BRANCH`; `None` for `SUMMARY` or unknown names).

- [ ] **Step 1: Write the failing test**

```python
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
        from datetime import datetime, timezone
        s = cs.Session(uuid="d0809692-f479-402f-b302-4c880634577a", title=None,
                       cwd="/Users/x/Code/quarry",
                       started=datetime(2025, 8, 12, tzinfo=timezone.utc),
                       last=datetime(2025, 8, 19, tzinfo=timezone.utc),
                       ai_title="Fix the Flaky Test")
        self.assertEqual(cs.free_variable(s, "LAUNCH_DIR"), "quarry")
        self.assertEqual(cs.free_variable(s, "UUID8"), "d0809692")
        self.assertEqual(cs.free_variable(s, "AI_TITLE"), "fix-the-flaky-test")
        self.assertEqual(cs.free_variable(s, "START_DATE"), "2025-08-12")
        self.assertIsNone(cs.free_variable(s, "SUMMARY"))
```

- [ ] **Step 2: Run to verify it fails**

Run: `python3 -m unittest tests.test_rename.HelperTests -v 2>&1 | tail -5`
Expected: FAIL — `has no attribute 'slugify'`

- [ ] **Step 3: Implement**

In `src/claude_sessions.py` (near the other formatting helpers), add:

```python
def slugify(text: str) -> str:
    """Lowercase kebab slug: alphanumeric runs joined by single hyphens."""
    out, gap = [], False
    for ch in text.lower():
        if ch.isalnum():
            out.append(ch); gap = False
        elif not gap:
            out.append("-"); gap = True
    return "".join(out).strip("-")


def normalize_title(text: str) -> str:
    """Final title cleanup: strip surrounding quotes/space, whitespace -> '-',
    collapse repeated/edge separators. Case is preserved (no convention forced)."""
    t = " ".join(text.split()).strip().strip("'\"")
    t = "-".join(t.split(" "))
    while "--" in t:
        t = t.replace("--", "-")
    return t.strip("-")


def date_ymd(dt: datetime | None) -> str:
    return dt.astimezone().strftime("%Y-%m-%d") if dt else ""


def git_project(cwd: str | None) -> str:
    """Basename of the nearest ancestor containing a .git, else ''."""
    if not cwd:
        return ""
    p = Path(cwd)
    for d in (p, *p.parents):
        if (d / ".git").exists():
            return d.name
    return ""


def session_branch(session: Session) -> str:
    """First gitBranch recorded in the log (lazy scan; '' if none)."""
    if not session.path:
        return ""
    try:
        with session.path.open(encoding="utf-8", errors="replace") as fh:
            for line in fh:
                if '"gitBranch"' not in line:
                    continue
                try:
                    b = json.loads(line).get("gitBranch")
                except json.JSONDecodeError:
                    continue
                if b:
                    return b
    except OSError:
        pass
    return ""


def free_variable(session: Session, name: str) -> str | None:
    """Value for an instant/offline template variable, or None if `name` is not
    one (e.g. SUMMARY, which requires generation)."""
    if name == "START_DATE":
        return date_ymd(session.started)
    if name == "LAST_ACTIVITY_DATE":
        return date_ymd(session.last)
    if name == "LAUNCH_DIR":
        return os.path.basename(os.path.normpath(session.cwd)) if session.cwd else ""
    if name == "GIT_PROJECT":
        return git_project(session.cwd)
    if name == "BRANCH":
        return slugify(session_branch(session))
    if name == "UUID":
        return session.uuid
    if name == "UUID8":
        return session.uuid[:8]
    if name == "AI_TITLE":
        return slugify(session.ai_title or "")
    return None
```

- [ ] **Step 4: Run to verify it passes**

Run: `python3 -m unittest tests.test_rename.HelperTests -v 2>&1 | tail -6` → PASS

- [ ] **Step 5: Commit**

```bash
git add src/claude_sessions.py tests/test_rename.py
git commit -m "rename: variable resolvers and title normalization"
```

---

### Task 3: `render_template` with fallback + lazy resolution

**Files:**
- Modify: `src/claude_sessions.py`
- Test: `tests/test_rename.py`

**Interfaces:**
- Produces: `render_template(template: str, resolve: Callable[[str], str]) -> str`. `resolve(name)` returns the variable's value (possibly `""`); it is called **lazily** — a `${VAR:-fallback}` fallback is rendered only when `VAR` resolves empty, so an expensive resolver (e.g. for `SUMMARY`) is not invoked when short-circuited. Output is passed through `normalize_title`.

- [ ] **Step 1: Write the failing test**

```python
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
        self.assertEqual(cs.render_template("${START_DATE}_x", r), "2025-08-12-x")

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
```

- [ ] **Step 2: Run to verify it fails**

Run: `python3 -m unittest tests.test_rename.RenderTests -v 2>&1 | tail -5`
Expected: FAIL — `has no attribute 'render_template'`

- [ ] **Step 3: Implement**

Add to `src/claude_sessions.py` (ensure `from typing import Callable` or use `"callable"` per the file's existing style — the file already uses `"callable"` as a string annotation in `SizeMetric`, so match that):

```python
def render_template(template: str, resolve) -> str:
    """Render a title template. Supports $VAR, ${VAR}, and ${VAR:-fallback}
    (fallback rendered only when VAR is empty). `resolve(name)` supplies values
    and is called lazily. Result is normalized (see normalize_title)."""

    def render(s: str) -> str:
        out = []
        i, n = 0, len(s)
        while i < n:
            ch = s[i]
            if ch != "$":
                out.append(ch); i += 1
                continue
            if i + 1 < n and s[i + 1] == "{":
                depth, j = 1, i + 2
                while j < n and depth:
                    if s[j] == "{":
                        depth += 1
                    elif s[j] == "}":
                        depth -= 1
                    if depth:
                        j += 1
                inner = s[i + 2:j]           # between the outer braces
                i = j + 1                     # past the closing '}'
                name, sep, fallback = inner.partition(":-")
                value = resolve(name.strip())
                out.append(value if value or not sep else render(fallback))
            else:                             # bare $NAME
                j = i + 1
                while j < n and (s[j].isalnum() or s[j] == "_"):
                    j += 1
                out.append(resolve(s[i + 1:j]))
                i = j
        return "".join(out)

    return normalize_title(render(template))
```

- [ ] **Step 4: Run to verify it passes**

Run: `python3 -m unittest tests.test_rename.RenderTests -v 2>&1 | tail -6` → PASS

- [ ] **Step 5: Commit**

```bash
git add src/claude_sessions.py tests/test_rename.py
git commit -m "rename: template rendering with lazy \${VAR:-fallback}"
```

---

### Task 4: `generate_summary` (shell out to the generator)

**Files:**
- Modify: `src/claude_sessions.py`
- Test: `tests/test_rename.py`

**Interfaces:**
- Consumes: `RenameConfig`, `scan_text` (Task uses `scan_text(path, replies=False)`).
- Produces: `generate_summary(session: Session, cfg: RenameConfig) -> str`. Builds context from all user prompts truncated to `cfg.summary_context_chars`, pipes it to `cfg.summary_command` (via the shell) on stdin, returns `slugify(stdout)`. Raises `RuntimeError` on non-zero exit, timeout, or empty output.

- [ ] **Step 1: Write the failing test** (uses a real deterministic command — no network, no mocks)

```python
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
            # generator: uppercase the first two words of stdin -> slugified back down
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
```

- [ ] **Step 2: Run to verify it fails**

Run: `python3 -m unittest tests.test_rename.GenerateSummaryTests -v 2>&1 | tail -5`
Expected: FAIL — `has no attribute 'generate_summary'`

- [ ] **Step 3: Implement**

Add `import subprocess` to the imports of `src/claude_sessions.py`, then:

```python
def generate_summary(session: Session, cfg: RenameConfig) -> str:
    """Produce $SUMMARY by piping the session's user-prompt context to
    cfg.summary_command on stdin and slugifying its stdout. Raises RuntimeError
    on failure so the caller can fall back to manual entry."""
    assert session.path is not None
    context = scan_text(session.path, replies=False)[: cfg.summary_context_chars]
    try:
        proc = subprocess.run(
            cfg.summary_command, shell=True, input=context,
            capture_output=True, text=True, timeout=cfg.summary_timeout_secs)
    except subprocess.TimeoutExpired as e:
        raise RuntimeError(f"summary_command timed out after "
                           f"{cfg.summary_timeout_secs}s") from e
    if proc.returncode != 0:
        raise RuntimeError(f"summary_command exited {proc.returncode}: "
                           f"{proc.stderr.strip()[:200]}")
    title = slugify(proc.stdout)
    if not title:
        raise RuntimeError("summary_command produced no usable title")
    return title
```

- [ ] **Step 4: Run to verify it passes**

Run: `python3 -m unittest tests.test_rename.GenerateSummaryTests -v 2>&1 | tail -6` → PASS

- [ ] **Step 5: Commit**

```bash
git add src/claude_sessions.py tests/test_rename.py
git commit -m "rename: generate \$SUMMARY via pluggable stdin/stdout command"
```

---

### Task 5: `set_custom_title` (defensive tail-append + read-back)

**Files:**
- Modify: `src/claude_sessions.py`
- Test: `tests/test_rename.py`

**Interfaces:**
- Produces: `set_custom_title(session: Session, title: str) -> None`. Validates (UUID well-formed, non-empty title, session not open), appends the `custom-title` record at EOF (ensuring a preceding newline), then re-parses with `load_session` and asserts the title resolves; raises `ValueError` (bad input) or `RuntimeError` (open session / read-back mismatch).

- [ ] **Step 1: Write the failing test**

```python
class SetCustomTitleTests(unittest.TestCase):
    def _session(self, td, lines):
        d = Path(td) / "projects" / "-Users-x-Code-real"
        d.mkdir(parents=True)
        p = d / "d0809692-f479-402f-b302-4c880634577a.jsonl"
        p.write_text("\n".join(lines) + "\n", encoding="utf-8")
        return cs.load_session(p)

    REAL = ['{"type":"user","cwd":"/x","timestamp":"2025-08-12T10:00:00.000Z",'
            '"message":{"role":"user","content":"hi"}}']

    def test_appends_and_reads_back(self):
        with TemporaryDirectory() as td:
            s = self._session(td, self.REAL)
            cs.set_custom_title(s, "my-title")
            self.assertEqual(cs.load_session(s.path).title, "my-title")

    def test_rejects_empty_title(self):
        with TemporaryDirectory() as td:
            s = self._session(td, self.REAL)
            with self.assertRaises(ValueError):
                cs.set_custom_title(s, "   ")

    def test_rejects_bad_uuid(self):
        with TemporaryDirectory() as td:
            s = self._session(td, self.REAL)
            s.uuid = "not-a-uuid"
            with self.assertRaises(ValueError):
                cs.set_custom_title(s, "x")

    def test_refuses_open_session(self):
        with TemporaryDirectory() as td:
            s = self._session(td, self.REAL)
            s.open = True
            with self.assertRaises(RuntimeError):
                cs.set_custom_title(s, "x")
```

- [ ] **Step 2: Run to verify it fails**

Run: `python3 -m unittest tests.test_rename.SetCustomTitleTests -v 2>&1 | tail -5`
Expected: FAIL — `has no attribute 'set_custom_title'`

- [ ] **Step 3: Implement**

Add near `artifacts()` in `src/claude_sessions.py`:

```python
import re as _re  # if `re` is not already imported at module top; prefer a top import

_UUID_RE = _re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-"
                       r"[0-9a-f]{4}-[0-9a-f]{12}$")


def set_custom_title(session: Session, title: str) -> None:
    """Append a custom-title record at the transcript tail (matches /rename),
    then verify our parser reads it back. Raises on bad input or format drift."""
    title = title.strip()
    if not title:
        raise ValueError("title is empty")
    if not _UUID_RE.match(session.uuid):
        raise ValueError(f"not a session UUID: {session.uuid}")
    if session.open:
        raise RuntimeError(f"session {session.uuid} is open in a live process")
    if session.path is None or not session.path.exists():
        raise RuntimeError(f"transcript not found for {session.uuid}")

    record = json.dumps({"type": "custom-title", "customTitle": title,
                         "sessionId": session.uuid})
    data = session.path.read_bytes()
    prefix = b"" if (not data or data.endswith(b"\n")) else b"\n"
    with session.path.open("ab") as fh:
        fh.write(prefix + record.encode("utf-8") + b"\n")

    reloaded = load_session(session.path)
    if reloaded is None or reloaded.title != title:
        raise RuntimeError(
            "wrote the title but could not read it back — quarry's understanding "
            "of the transcript format may be out of date")
    session.title = title
```

(If `re` is not yet imported at the top of the file, add `import re` to the top imports and use `re` instead of `_re`.)

- [ ] **Step 4: Run to verify it passes**

Run: `python3 -m unittest tests.test_rename.SetCustomTitleTests -v 2>&1 | tail -6` → PASS

- [ ] **Step 5: Commit**

```bash
git add src/claude_sessions.py tests/test_rename.py
git commit -m "rename: defensive custom-title append with read-back"
```

---

### Task 6: `cmd_rename` — parser, target resolution, dry-run

**Files:**
- Create: `src/cmd_rename.py`
- Test: `tests/test_rename.py`

**Interfaces:**
- Consumes: `cs.discover`, `cs.project_last_session`, `cs.load_rename_config`, `cs.Palette`, `cs.color_enabled`.
- Produces: `build_parser() -> argparse.ArgumentParser`; `resolve_targets(sessions, selector, retitle, root, cwd) -> list[Session]`; `main(argv) -> int`. Selection: no `selector` → last session in `cwd` (via `project_last_session`); a `selector` is an `fnmatch` glob over `uuid` and current name; already-titled sessions are skipped unless `retitle`, **except** a single explicitly-named target (bare selector matching exactly one, or the last-session default).

- [ ] **Step 1: Write the failing test**

```python
import cmd_rename  # add near the top imports of tests/test_rename.py


class ResolveTargetsTests(unittest.TestCase):
    def _mk(self, uuid, title=None):
        return cs.Session(uuid=uuid, title=title, cwd="/x", started=None, last=None)

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

    def test_matches_by_name(self):
        sess = [self._mk("uuuu1", title="my-proj")]
        got = cmd_rename.resolve_targets(sess, "my-*", retitle=True, root=None, cwd="/x")
        self.assertEqual([s.uuid for s in got], ["uuuu1"])
```

- [ ] **Step 2: Run to verify it fails**

Run: `python3 -m unittest tests.test_rename.ResolveTargetsTests -v 2>&1 | tail -5`
Expected: FAIL — `No module named 'cmd_rename'`

- [ ] **Step 3: Implement**

Create `src/cmd_rename.py`:

```python
#!/usr/bin/env python3
"""quarry rename — set session titles in bulk or one at a time.

Proposes a title per session from a template (default: reuse the AI title, else
generate one), shows the session's detail, and lets you accept, edit, or skip
before writing. With no selector it targets the last session run in the current
directory (like `quarry rm`). Titles are written the way `/rename` writes them:
a custom-title record appended to the session's transcript.
"""

from __future__ import annotations

import argparse
import fnmatch
import os
import sys
from datetime import datetime, timezone

sys.path.insert(0, os.path.dirname(os.path.realpath(__file__)))
import claude_sessions as cs  # noqa: E402


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="quarry rename",
        description="Title sessions from a template, with per-item confirm/edit. "
                    "No selector = the last session run in the current directory.")
    p.add_argument("selector", nargs="?",
                   help="UUID/prefix or shell glob over UUID-or-name; omit for the "
                        "last session in this directory")
    p.add_argument("-t", "--template",
                   help="title template (default: config's default_template)")
    p.add_argument("--retitle", action="store_true",
                   help="also (re)title sessions that already have a custom title")
    p.add_argument("-n", "--dry-run", action="store_true",
                   help="show matched sessions and the rendered template; write nothing")
    p.add_argument("--model", help="override the model in the default summary command")
    p.add_argument("--color", choices=("auto", "always", "never"), default="auto")
    return p


def resolve_targets(sessions, selector, retitle, root, cwd):
    """Select sessions to rename (see module/Task interface for semantics)."""
    if not selector:
        sid = cs.project_last_session(root, cwd) if root else None
        return [s for s in sessions if s.uuid == sid] if sid else []
    matched = [s for s in sessions
               if fnmatch.fnmatch(s.uuid, selector)
               or (s.title and fnmatch.fnmatch(s.title, selector))
               or (s.ai_title and fnmatch.fnmatch(s.ai_title, selector))]
    explicit_single = len(matched) == 1 and selector == matched[0].uuid
    if retitle or explicit_single:
        return matched
    return [s for s in matched if not s.named]
```

- [ ] **Step 4: Run to verify it passes**

Run: `python3 -m unittest tests.test_rename.ResolveTargetsTests -v 2>&1 | tail -6` → PASS

- [ ] **Step 5: Add `main()` with dry-run, and a dry-run test**

Append to `src/cmd_rename.py`:

```python
def _resolver(session, cfg, model):
    cache = {}
    cmd = cfg.summary_command
    if model:
        cmd = cmd.replace("--model haiku", f"--model {model}")
    live = cs.RenameConfig(cfg.default_template, cmd,
                           cfg.summary_context_chars, cfg.summary_timeout_secs)

    def resolve(name):
        if name in cache:
            return cache[name]
        v = cs.free_variable(session, name)
        if v is None and name == "SUMMARY":
            v = cs.generate_summary(session, live)
        cache[name] = v or ""
        return cache[name]
    return resolve


def main(argv: list[str]) -> int:
    args = build_parser().parse_args(argv)
    root = cs.config_dir()
    cfg = cs.load_rename_config()
    template = args.template or cfg.default_template
    pal = cs.Palette(cs.color_enabled(args.color, sys.stdout))

    sessions = cs.discover(root)
    targets = resolve_targets(sessions, args.selector, args.retitle, root, os.getcwd())
    if not targets:
        print("no sessions to rename", file=sys.stderr)
        return 0

    if args.dry_run:
        for s in targets:
            skeleton = cs.render_template(
                template, lambda n, s=s: cs.free_variable(s, n) or (
                    "$SUMMARY" if n == "SUMMARY" else ""))
            print(f"{s.uuid}  {s.title or s.ai_title or ''}  ->  {skeleton}")
        return 0

    return _run_interactive(targets, template, cfg, args.model, pal)  # Task 7
```

Add the dry-run test:

```python
class DryRunTests(unittest.TestCase):
    def test_dry_run_leaves_summary_literal(self):
        s = cs.Session(uuid="u", title=None, cwd="/x/quarry", started=None, last=None,
                       ai_title="")
        skeleton = cs.render_template(
            "$LAUNCH_DIR-${AI_TITLE:-$SUMMARY}",
            lambda n: cs.free_variable(s, n) or ("$SUMMARY" if n == "SUMMARY" else ""))
        self.assertEqual(skeleton, "quarry-$SUMMARY")
```

Run: `python3 -m unittest tests.test_rename.DryRunTests -v 2>&1 | tail -6` → PASS
(`_run_interactive` doesn't exist yet; that's Task 7. `main` isn't called by this test.)

- [ ] **Step 6: Commit**

```bash
git add src/cmd_rename.py tests/test_rename.py
git commit -m "rename: cmd_rename parser, target selection, dry-run"
```

---

### Task 7: Interactive confirm-or-edit loop

**Files:**
- Modify: `src/cmd_rename.py`
- Modify: `src/cmd_ls.py` (export the detail printer if needed — it already exposes `print_detail(s, pal, now, width)`)
- Test: `tests/test_rename.py`

**Interfaces:**
- Consumes: `cmd_ls.print_detail(session, pal, now, width)`; `cs.render_template`; `cs.set_custom_title`; the `_resolver` from Task 6.
- Produces: `prompt_title(proposed: str, read=input) -> str | None` (returns the edited/accepted title, or `None` to skip; empty input → skip); `_run_interactive(targets, template, cfg, model, pal) -> int`.

- [ ] **Step 1: Write the failing test** (the keystroke layer is thin; test the decision helper with an injected reader)

```python
class PromptTitleTests(unittest.TestCase):
    def test_accept_returns_edited(self):
        self.assertEqual(cmd_rename.prompt_title("proposed", read=lambda p: "edited"), "edited")

    def test_empty_input_skips(self):
        self.assertIsNone(cmd_rename.prompt_title("proposed", read=lambda p: "   "))

    def test_ctrl_c_propagates(self):
        def boom(p):
            raise KeyboardInterrupt
        with self.assertRaises(KeyboardInterrupt):
            cmd_rename.prompt_title("proposed", read=boom)
```

- [ ] **Step 2: Run to verify it fails**

Run: `python3 -m unittest tests.test_rename.PromptTitleTests -v 2>&1 | tail -5`
Expected: FAIL — `module 'cmd_rename' has no attribute 'prompt_title'`

- [ ] **Step 3: Implement**

Append to `src/cmd_rename.py`:

```python
import readline  # noqa: E402  (enables line editing + prefill on input())
import shutil  # noqa: E402
import cmd_ls  # noqa: E402


def prompt_title(proposed: str, read=input) -> str | None:
    """Show `proposed` in an editable line; return the accepted/edited title, or
    None to skip (empty submission). KeyboardInterrupt propagates (abort batch)."""
    def prefill():
        readline.insert_text(proposed)
        readline.redisplay()
    hook = getattr(readline, "set_pre_input_hook", None)
    if hook and read is input:
        hook(prefill)
    try:
        answer = read("  title (Enter=accept, empty=skip, Ctrl-C=abort): ")
    finally:
        if hook and read is input:
            hook(None)
    answer = (answer or "").strip()
    return answer or None


def _run_interactive(targets, template, cfg, model, pal) -> int:
    now = datetime.now(timezone.utc)
    width = shutil.get_terminal_size(fallback=(80, 24)).columns
    renamed = skipped = 0
    for s in targets:
        print()
        cmd_ls.print_detail(s, pal, now, width)
        try:
            proposed = cs.render_template(template, _resolver(s, cfg, model))
        except RuntimeError as e:
            print(pal.warn(f"  summary generation failed: {e}"), file=sys.stderr)
            proposed = ""
        try:
            chosen = prompt_title(proposed)
        except KeyboardInterrupt:
            print("\naborted.", file=sys.stderr)
            break
        if chosen is None:
            skipped += 1
            continue
        try:
            cs.set_custom_title(s, chosen)
            print(pal.head(f"  renamed -> {chosen}"))
            renamed += 1
        except (ValueError, RuntimeError) as e:
            print(pal.warn(f"  failed: {e}"), file=sys.stderr)
    print(f"\nrenamed {renamed}, skipped {skipped}.", file=sys.stderr)
    return 0
```

- [ ] **Step 4: Run to verify it passes**

Run: `python3 -m unittest tests.test_rename.PromptTitleTests -v 2>&1 | tail -6` → PASS

- [ ] **Step 5: End-to-end apply test** (fake generator, temp session, injected input)

```python
class EndToEndApplyTests(ConfigBase):
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
            proposed = cs.render_template(cfg.default_template, cmd_rename._resolver(s, cfg, None))
            self.assertEqual(proposed, "quarry-fixed-flaky-test")
            chosen = cmd_rename.prompt_title(proposed, read=lambda pr: "")  # accept as-is? no: empty=skip
            # accept unchanged by returning the proposed text:
            chosen = cmd_rename.prompt_title(proposed, read=lambda pr: proposed)
            cs.set_custom_title(s, chosen)
            self.assertEqual(cs.load_session(p).title, "quarry-fixed-flaky-test")
```

Run: `python3 -m unittest tests.test_rename.EndToEndApplyTests -v 2>&1 | tail -6` → PASS

- [ ] **Step 6: Run the whole suite (pristine) and commit**

Run: `python3 -W error::ResourceWarning -m unittest discover -s tests -p 'test_*.py' 2>&1 | tail -4` → OK

```bash
git add src/cmd_rename.py tests/test_rename.py
git commit -m "rename: interactive confirm-or-edit loop"
```

---

### Task 8: Wire `rename` into the dispatcher, completions, and docs

**Files:**
- Modify: `bin/quarry`
- Modify: `README.md`
- Test: manual smoke + `make test`

**Interfaces:**
- Consumes: `cmd_rename.main`.

- [ ] **Step 1: Register the subcommand**

In `bin/quarry`: add `import cmd_rename  # noqa: E402` beside the other imports; add a `rename` line to `USAGE`; and in `main()` add:

```python
    if cmd == "rename":
        return cmd_rename.main(rest)
```

- [ ] **Step 2: Add completions**

- fish: after the `rm` block, add
  ```
  complete -c quarry -n __quarry_needs_command -a rename -d 'Title sessions'
  complete -c quarry -n '__quarry_using_command rename' -s t -l template -d 'title template'
  complete -c quarry -n '__quarry_using_command rename' -l retitle -d 'include already-titled'
  complete -c quarry -n '__quarry_using_command rename' -s n -l dry-run -d 'preview only'
  complete -c quarry -n '__quarry_using_command rename' -l model -d 'summary model override'
  complete -c quarry -n '__quarry_using_command rename' -l color -x -a 'auto always never' -d 'when to colorize'
  complete -c quarry -n '__quarry_using_command rename' -a '(__quarry_sessions)' -d session
  ```
- zsh: add `'rename:Title sessions'` to `cmds`, and a `rename)` case mirroring `rm)` with `-t/--template`, `--retitle`, `-n/--dry-run`, `--model`, `--color`, `'*:session:_default'`.
- bash: add `rename` to the top-level `compgen -W`, and a `rename)` case: `COMPREPLY=( $(compgen -W "-t --template --retitle -n --dry-run --model --color" -- "$cur") )`.

- [ ] **Step 3: Smoke test**

Run:
```bash
python3 bin/quarry rename -h >/dev/null && echo OK
python3 bin/quarry rename --dry-run '________-____-____-____-____________' 2>&1 | head -1  # no match -> "no sessions to rename"
for sh in fish zsh bash; do python3 bin/quarry completions $sh >/dev/null && echo "$sh ok"; done
```
Expected: `OK`, the no-match message, and three `ok` lines.

- [ ] **Step 4: Update the README**

Add a `## quarry rename` section (mirror the `quarry rm` section): synopsis, selector/`-t`/`--retitle`/`-n`/`--model`, the variable list, the `${VAR:-fallback}` operator, the pluggable `summary_command` (context→stdin, title→stdout; no dependency / no API key / no disk state), and the per-item confirm keys (Enter/empty/Ctrl-C). Add `rename` to the command list near the top and to the `## Layout` note.

- [ ] **Step 5: Full suite + commit**

Run: `make test` → OK

```bash
git add bin/quarry README.md
git commit -m "rename: register subcommand, completions, and docs"
```

---

## Self-Review

**Spec coverage:** CLI/selector/flags (Tasks 6, 8); no-selector last-session (Task 6); skip-titled/`--retitle`/single-explicit (Task 6); variables incl. `$AI_TITLE`/`$BRANCH` (Task 2); `${VAR:-fallback}` + laziness (Task 3); minimal case-preserving normalization (Task 2); `$SUMMARY` via stdin→stdout generator, no-persistence/no-dep (Task 4); config `[rename]` incl. default `${AI_TITLE:-$SUMMARY}` + example-sync test (Task 1); defensive tail-append + read-back (Task 5); per-item confirm UX (Task 7); dispatcher/completions/README (Task 8). ✓

**Placeholder scan:** none — every code step carries complete code. The only cross-task forward reference is `_run_interactive` (introduced in Task 6's `main`, implemented in Task 7); noted inline.

**Type consistency:** `RenameConfig` field order `(default_template, summary_command, summary_context_chars, summary_timeout_secs)` is consistent across Tasks 1, 6, 7. `resolve(name) -> str`, `free_variable(...) -> str | None`, `generate_summary(session, cfg) -> str`, `set_custom_title(session, title) -> None`, `prompt_title(proposed, read=input) -> str | None` are used consistently.

## Notes for the implementer

- `$BRANCH` reads the transcript lazily (only when used); all other free vars come from the already-parsed (cached) `Session`.
- `readline` prefill uses `set_pre_input_hook`, which is a no-op fallback under macOS libedit builds lacking it — acceptable; line editing still works, prefill may not. Verify on the target machine during Task 7; if prefill is unavailable, print the proposed title above the prompt as a fallback.
- Keep `make test` output pristine — reuse the `with path.open(...)` pattern (no bare `open()` iteration) in any new file reads.
