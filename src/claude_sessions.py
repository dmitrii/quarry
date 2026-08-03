"""Shared core for the ls-claude / rm-claude tools.

Reads Claude Code's on-disk session state under $CLAUDE_CONFIG_DIR (default
~/.claude) and the top-level ~/.claude.json. No Claude/agent invocations.

Session state lives in a few places, all keyed by the session UUID:
  projects/<encoded-cwd>/<uuid>.jsonl   the conversation log (source of truth)
  projects/<encoded-cwd>/<uuid>/        sidecar dir: subagents/, tool-results/
  session-env/<uuid>/                   per-session environment
  file-history/<uuid>/                  file-edit snapshots
  projects/<encoded-cwd>/agent-*.jsonl  subagent transcripts, each tagged with
                                        the sessionId that spawned it
The live registry sessions/<pid>.json is keyed by PID, exists only while a
session runs, and is what tells us a session is "open". ~/.claude.json holds a
per-directory `lastSessionId` pointer plus that session's last-run metrics.
"""

from __future__ import annotations

import configparser
import json
import os
import re
import subprocess
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

# ── model ──────────────────────────────────────────────────────────────────


@dataclass
class Session:
    uuid: str
    title: str | None              # user-set custom title
    cwd: str | None
    started: datetime | None       # first timestamp seen in the log
    last: datetime | None          # last timestamp seen in the log
    ai_title: str | None = None    # Claude's auto-generated title
    path: Path | None = None       # the .jsonl log
    log_bytes: int = 0             # size of the log on disk
    open: bool = False             # currently open in a live `claude` process
    pid: int | None = None         # PID of the live process, when open
    status: str | None = None      # 'busy'/'idle' reported by a live process
    deleted: bool = False          # referenced in .claude.json but log is gone
    last_meta: dict | None = None  # .claude.json last-run metrics, for deleted
    latest_context: int | None = None  # tokens in the last request; None until scanned
    is_sidechain: bool = False     # a subagent transcript, not a top-level session
    parent: str | None = None      # for a sidechain, the session it was spawned from
    sidechains: list[Path] = field(default_factory=list)  # child sidechain logs

    @property
    def display_name(self) -> str:
        return self.title if self.title else self.uuid

    @property
    def named(self) -> bool:
        return bool(self.title)


def config_dir() -> Path:
    env = os.environ.get("CLAUDE_CONFIG_DIR")
    return Path(env) if env else Path.home() / ".claude"


def claude_json_path(root: Path) -> Path | None:
    # ~/.claude.json normally sits beside ~/.claude; be forgiving about layout.
    for cand in (root / ".claude.json", root.parent / ".claude.json",
                 Path.home() / ".claude.json"):
        if cand.exists():
            return cand
    return None


def load_claude_json(root: Path) -> dict:
    cj = claude_json_path(root)
    if cj is None:
        return {}
    try:
        return json.loads(cj.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}


def project_last_session(root: Path, cwd: str) -> str | None:
    """The `lastSessionId` .claude.json records for a directory — i.e. the
    session Claude most recently ran there. Used as rm-claude's no-arg default."""
    projects = load_claude_json(root).get("projects") or {}
    pv = projects.get(cwd)                       # fast path: exact key
    if isinstance(pv, dict) and pv.get("lastSessionId"):
        return pv["lastSessionId"]
    target = os.path.realpath(cwd)               # resolve symlinks (e.g. /tmp)
    for key, pv in projects.items():
        if (isinstance(pv, dict) and pv.get("lastSessionId")
                and os.path.realpath(key) == target):
            return pv["lastSessionId"]
    return None


def parse_ts(raw: str) -> datetime | None:
    # Timestamps look like 2026-07-10T14:05:08.451Z (UTC).
    try:
        return datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except (ValueError, AttributeError):
        return None


def load_session(path: Path) -> Session | None:
    title = ai_title = None
    cwd = None
    lo = hi = None
    session_id = None
    saw_turn = saw_primary_turn = False
    with path.open(encoding="utf-8", errors="replace") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            try:
                rec = json.loads(line)
            except json.JSONDecodeError:
                continue
            t = rec.get("type")
            if t == "custom-title":
                title = rec.get("customTitle") or title
            elif t == "ai-title":
                ai_title = rec.get("aiTitle") or ai_title
            elif t in ("user", "assistant"):
                saw_turn = True
                if not rec.get("isSidechain"):
                    saw_primary_turn = True
            if session_id is None and rec.get("sessionId"):
                session_id = rec["sessionId"]
            # First cwd seen = the launch directory; later records drift as tools cd.
            if cwd is None and rec.get("cwd"):
                cwd = rec["cwd"]
            ts_raw = rec.get("timestamp")
            if ts_raw:
                ts = parse_ts(ts_raw)
                if ts is not None:
                    if lo is None or ts < lo:
                        lo = ts
                    if hi is None or ts > hi:
                        hi = ts
    # A real conversation log always carries a cwd and timestamps. Files with
    # neither are metadata-only sidecars Claude writes beside real logs —
    # summary/ai-title/agent-name records keyed to another session — not
    # sessions in their own right.
    if cwd is None and lo is None and hi is None:
        return None
    # A transcript with only isSidechain turns is a subagent spawned from
    # another session (its sessionId), not a resumable conversation itself.
    is_sidechain = saw_turn and not saw_primary_turn
    try:
        size = path.stat().st_size
    except OSError:
        size = 0
    return Session(uuid=path.stem, title=title, cwd=cwd, started=lo, last=hi,
                   ai_title=ai_title, path=path, log_bytes=size,
                   is_sidechain=is_sidechain,
                   parent=session_id if is_sidechain else None)


def live_sessions(root: Path) -> dict[str, dict]:
    # A session is "open" if sessions/<pid>.json exists and that PID is alive
    # (files can go stale if Claude was killed uncleanly). -> sessionId -> info.
    live: dict[str, dict] = {}
    sdir = root / "sessions"
    if not sdir.is_dir():
        return live
    for f in sdir.glob("*.json"):
        try:
            pid = int(f.stem)
        except ValueError:
            continue
        try:
            os.kill(pid, 0)  # signal 0 = liveness probe, sends nothing
        except ProcessLookupError:
            continue         # stale registry entry; process is gone
        except PermissionError:
            pass             # alive but not ours to signal — still counts
        try:
            info = json.loads(f.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        sid = info.get("sessionId")
        if sid:
            live[sid] = {"pid": pid, "status": info.get("status")}
    return live


# ── discovery cache ──────────────────────────────────────────────────────
#
# Parsing every log on every listing is the dominant cost (each is read and
# JSON-decoded line by line). Logs are append-only and mostly dormant, so we
# memoize load_session()'s result keyed by (size, mtime): an unchanged file is
# served from the cache without being reopened. The cache is a pure
# optimization — any read/write error just falls back to a live parse.

_CACHE_VERSION = 2


def cache_file() -> Path:
    base = os.environ.get("XDG_CACHE_HOME")
    root = Path(base) if base else Path.home() / ".cache"
    return root / "quarry" / "index.json"


def _stat_key(path: Path) -> tuple[int, int]:
    st = path.stat()
    return st.st_size, st.st_mtime_ns


def load_cache() -> dict:
    try:
        data = json.loads(cache_file().read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    if data.get("version") != _CACHE_VERSION:
        return {}
    entries = data.get("entries")
    return entries if isinstance(entries, dict) else {}


def save_cache(entries: dict) -> None:
    f = cache_file()
    try:
        f.parent.mkdir(parents=True, exist_ok=True)
        tmp = f.with_name(f.name + ".tmp")
        tmp.write_text(json.dumps({"version": _CACHE_VERSION, "entries": entries}),
                       encoding="utf-8")
        tmp.replace(f)  # atomic swap so a concurrent reader never sees a partial file
    except OSError:
        pass


def _entry_from_session(s: Session | None, size: int, mtime: int) -> dict:
    e = {"size": size, "mtime": mtime, "session": s is not None}
    if s is not None:
        e.update(title=s.title, ai_title=s.ai_title, cwd=s.cwd,
                 started=s.started.isoformat() if s.started else None,
                 last=s.last.isoformat() if s.last else None,
                 log_bytes=s.log_bytes,
                 is_sidechain=s.is_sidechain, parent=s.parent)
    return e


def _session_from_entry(path: Path, e: dict) -> Session | None:
    if not e.get("session"):
        return None
    return Session(
        uuid=path.stem, title=e.get("title"), cwd=e.get("cwd"),
        started=parse_ts(e["started"]) if e.get("started") else None,
        last=parse_ts(e["last"]) if e.get("last") else None,
        ai_title=e.get("ai_title"), path=path, log_bytes=e.get("log_bytes", 0),
        is_sidechain=e.get("is_sidechain", False), parent=e.get("parent"))


def discover(root: Path) -> list[Session]:
    """All sessions with an on-disk log, annotated with live-process state."""
    projects = root / "projects"
    if not projects.is_dir():
        return []
    live = live_sessions(root)
    cache = load_cache()
    seen: set[str] = set()
    dirty = False
    sessions = []
    children: dict[str, list[Path]] = {}   # parent uuid -> its sidechain logs
    for jsonl in projects.glob("*/*.jsonl"):
        if not jsonl.is_file():
            continue
        key = str(jsonl)
        seen.add(key)
        try:
            size, mtime = _stat_key(jsonl)
        except OSError:
            continue
        e = cache.get(key)
        if not (isinstance(e, dict) and e.get("size") == size and e.get("mtime") == mtime):
            e = _entry_from_session(load_session(jsonl), size, mtime)
            cache[key] = e
            dirty = True
        s = _session_from_entry(jsonl, e)
        if s is None:
            continue
        if s.is_sidechain:
            # Not a session of its own; tallied to the parent it was spawned from.
            if s.parent:
                children.setdefault(s.parent, []).append(jsonl)
            continue
        if s.uuid in live:
            s.open = True
            s.pid = live[s.uuid]["pid"]
            s.status = live[s.uuid]["status"]
        sessions.append(s)
    for s in sessions:
        s.sidechains = sorted(children.get(s.uuid, []))
    # Drop entries for logs that no longer exist (checking the path, so caches
    # shared across config roots keep each other's still-present entries).
    for key in [k for k in cache if k not in seen and not os.path.exists(k)]:
        del cache[key]
        dirty = True
    if dirty:
        save_cache(cache)
    return sessions


_LAST_META_KEYS = ("lastDuration", "lastCost", "lastTotalInputTokens",
                   "lastTotalOutputTokens", "lastModelUsage",
                   "lastLinesAdded", "lastLinesRemoved")


def deleted_sessions(root: Path) -> list[Session]:
    """Sessions referenced by .claude.json's per-directory `lastSessionId` whose
    log no longer exists — i.e. removed sessions Claude still points at."""
    data = load_claude_json(root)
    if not data:
        return []
    projects = root / "projects"
    existing = {p.stem for p in projects.glob("*/*.jsonl")} if projects.is_dir() else set()
    out: list[Session] = []
    seen: set[str] = set()
    for cwd, pv in (data.get("projects") or {}).items():
        if not isinstance(pv, dict):
            continue
        sid = pv.get("lastSessionId")
        if not sid or sid in existing or sid in seen:
            continue
        seen.add(sid)
        out.append(Session(
            uuid=sid, title=None, cwd=cwd, started=None, last=None,
            deleted=True, last_meta={k: pv.get(k) for k in _LAST_META_KEYS},
        ))
    return out


def find_session(sessions: list[Session], query: str) -> tuple[Session | None, list[Session]]:
    """Resolve a query to one session. Returns (match, ambiguous_candidates).

    Match precedence: exact UUID > exact title (custom or AI) > UUID prefix >
    case-insensitive substring of a title. Ties beyond an exact hit are reported
    as candidates so the caller can disambiguate.
    """
    q = query.strip()
    ql = q.lower()

    def names(s: Session) -> list[str]:
        return [n for n in (s.title, s.ai_title) if n]

    for s in sessions:                                   # exact UUID
        if s.uuid == q:
            return s, []
    exact = [s for s in sessions if any(n == q for n in names(s))]
    if len(exact) == 1:
        return exact[0], []
    if len(exact) > 1:
        return None, exact

    prefix = [s for s in sessions if s.uuid.startswith(ql)]
    if len(prefix) == 1:
        return prefix[0], []
    if len(prefix) > 1:
        return None, prefix

    sub = [s for s in sessions if any(ql in n.lower() for n in names(s))]
    if len(sub) == 1:
        return sub[0], []
    return None, sub


def context_total(usage: dict) -> int:
    """Total tokens fed to the model on one request = regular input + both cache
    buckets. (Cache tokens usually dominate, since context is cached per turn.)"""
    return (usage.get("input_tokens", 0)
            + usage.get("cache_read_input_tokens", 0)
            + usage.get("cache_creation_input_tokens", 0))


def scan_latest_context(path: Path) -> int:
    """Focused pass: the context size of the last main-thread request in a log.
    Cheaper than analyze() — for populating the size column when listing."""
    latest = 0
    for line in path.open(encoding="utf-8", errors="replace"):
        line = line.strip()
        if not line:
            continue
        try:
            rec = json.loads(line)
        except json.JSONDecodeError:
            continue
        if rec.get("type") == "assistant" and not rec.get("isSidechain"):
            u = (rec.get("message") or {}).get("usage")
            if u:
                latest = context_total(u)
    return latest


# ── search config ──────────────────────────────────────────────────────────
#
# ~/.config/quarry/config.ini tunes what the widest fzf scope (replies)
# searches. Missing or malformed config falls back to the defaults below.


@dataclass
class SearchConfig:
    sidechains: bool = True   # also search the session's subagent transcripts
    thinking: bool = True     # include the agent's chain-of-thought, not just replies


def config_ini_path() -> Path:
    base = os.environ.get("XDG_CONFIG_HOME")
    root = Path(base) if base else Path.home() / ".config"
    return root / "quarry" / "config.ini"


def load_search_config() -> SearchConfig:
    cfg = SearchConfig()
    parser = configparser.ConfigParser()
    try:
        if not parser.read(config_ini_path(), encoding="utf-8"):
            return cfg
        return SearchConfig(
            sidechains=parser.getboolean("search", "sidechains", fallback=cfg.sidechains),
            thinking=parser.getboolean("search", "thinking", fallback=cfg.thinking),
        )
    except (configparser.Error, OSError, ValueError):
        return cfg


# ── rename config ────────────────────────────────────────────────────────────
#
# The [rename] section of ~/.config/quarry/config.ini tunes `quarry rename`.
# Defaults here are authoritative (quarry works with no config file);
# config.ini.example mirrors them.

DEFAULT_SUMMARY_COMMAND = (
    'claude -p --no-session-persistence --model sonnet --effort medium --tools "" '
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
    summary_max_words: int = 8   # cap $SUMMARY so a runaway model reply can't sprawl


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
            summary_max_words=parser.getint("rename", "summary_max_words",
                                            fallback=cfg.summary_max_words),
        )
    except (configparser.Error, OSError, ValueError):
        return cfg


def slugify(text: str) -> str:
    """Lowercase kebab slug: alphanumeric runs joined by single hyphens."""
    out, gap = [], False
    for ch in text.lower():
        if ch.isalnum():
            out.append(ch)
            gap = False
        elif not gap:
            out.append("-")
            gap = True
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
                out.append(ch)
                i += 1
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
    if cfg.summary_max_words > 0:
        # A model that ignores the length instruction can spill a whole reply;
        # keep only the leading words (the useful title is at the front).
        title = "-".join(title.split("-")[: cfg.summary_max_words])
    return title


_UUID_RE = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-"
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


def scan_text(path: Path, *, replies: bool, thinking: bool = False,
              include_sidechain: bool = False) -> str:
    """Concatenate a log's searchable text into one line — the content behind
    --scope prompts/replies. Human prompt text is always included; `replies`
    adds the agent's visible text and `thinking` its chain-of-thought. Records
    flagged isSidechain are skipped unless `include_sidechain`."""
    parts: list[str] = []
    with path.open(encoding="utf-8", errors="replace") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            try:
                rec = json.loads(line)
            except json.JSONDecodeError:
                continue
            if rec.get("isSidechain") and not include_sidechain:
                continue
            t = rec.get("type")
            if t == "user" and not rec.get("isMeta"):
                c = (rec.get("message") or {}).get("content")
                if isinstance(c, str):
                    parts.append(c)
                elif isinstance(c, list):
                    parts += [b.get("text", "") for b in c
                              if isinstance(b, dict) and b.get("type") == "text"]
            elif t == "assistant" and replies:
                for b in (rec.get("message") or {}).get("content", []):
                    if not isinstance(b, dict):
                        continue
                    if b.get("type") == "text":
                        parts.append(b.get("text", ""))
                    elif thinking and b.get("type") == "thinking":
                        parts.append(b.get("thinking", ""))
    return " ".join(" ".join(parts).split())


def analyze(session: Session) -> dict:
    """Second, deep pass over one log: counts, tokens, models, prompt previews."""
    user_prompts = 0
    tool_calls = 0
    asst_ids: set[str] = set()
    out_tokens: dict[str, int] = {}   # message.id -> max output_tokens seen
    context_peak = 0
    latest_context = 0                # context of the last main-thread request
    models: dict[str, None] = {}      # first-seen order preserved
    branch = version = None
    first_prompt = last_prompt = None

    assert session.path is not None
    for line in session.path.open(encoding="utf-8", errors="replace"):
        line = line.strip()
        if not line:
            continue
        try:
            rec = json.loads(line)
        except json.JSONDecodeError:
            continue
        if rec.get("gitBranch"):
            branch = rec["gitBranch"]
        if rec.get("version"):
            version = rec["version"]
        t = rec.get("type")
        if t == "user" and not rec.get("isMeta"):
            content = rec.get("message", {}).get("content")
            # A real human prompt is text; tool results are lists of tool_result.
            is_prompt = isinstance(content, str) or (
                isinstance(content, list)
                and any(isinstance(b, dict) and b.get("type") != "tool_result"
                        for b in content))
            if is_prompt:
                user_prompts += 1
                text = content if isinstance(content, str) else next(
                    (b.get("text", "") for b in content
                     if isinstance(b, dict) and b.get("type") == "text"), "")
                text = " ".join(text.split())
                if text:
                    first_prompt = first_prompt or text
                    last_prompt = text
        elif t == "assistant":
            msg = rec.get("message", {})
            mid = msg.get("id")
            if mid:
                asst_ids.add(mid)
            if msg.get("model"):
                models.setdefault(msg["model"], None)
            u = msg.get("usage") or {}
            if mid and "output_tokens" in u:
                out_tokens[mid] = max(out_tokens.get(mid, 0), u["output_tokens"])
            if u:
                ctx = context_total(u)
                context_peak = max(context_peak, ctx)
                if not rec.get("isSidechain"):
                    latest_context = ctx   # overwritten in order -> last wins
            for b in msg.get("content", []):
                if isinstance(b, dict) and b.get("type") == "tool_use":
                    tool_calls += 1

    return {
        "user_prompts": user_prompts,
        "assistant_msgs": len(asst_ids),
        "tool_calls": tool_calls,
        "output_tokens": sum(out_tokens.values()),
        "context_peak": context_peak,
        "latest_context": latest_context,
        "models": list(models),
        "branch": branch,
        "version": version,
        "first_prompt": first_prompt,
        "last_prompt": last_prompt,
        "log_bytes": session.path.stat().st_size,
    }


def artifacts(root: Path, session: Session) -> list[Path]:
    """Every on-disk artifact belonging to a session (existing paths only).

    The session's own files are named by its UUID; its subagent transcripts
    (agent-*.jsonl) are named independently but belong to it, so they go too.
    Removal is a straight delete — no centralized file is edited. history.jsonl
    / .claude.json references are left untouched.
    """
    paths: list[Path] = []
    if session.path:
        paths.append(session.path)                     # <uuid>.jsonl
        paths.append(session.path.parent / session.uuid)  # sidecar dir
    paths.append(root / "session-env" / session.uuid)
    paths.append(root / "file-history" / session.uuid)
    paths += session.sidechains                        # subagent transcripts
    return [p for p in paths if p.exists()]


def path_size(p: Path) -> int:
    """Total bytes of a file or a directory tree."""
    try:
        if p.is_file() or p.is_symlink():
            return p.stat().st_size
        return sum(f.stat().st_size for f in p.rglob("*") if f.is_file())
    except OSError:
        return 0


# ── formatting ─────────────────────────────────────────────────────────────

DIR_WIDTH = 20  # fixed width so the dir column lines up in the long view
LABEL_W = 14    # widest label ("Latest context")
ROW_PREFIX = 2 + LABEL_W + 2   # visible columns before a detail row's value
SIZE_COL_W = 6                 # right-aligned width of the size column

# Pluggable notions of a session's "size". `get` reads a value off a Session;
# `expensive` marks metrics whose value requires parsing the log (populated by
# ensure_sizes() before listing). `unit` picks the column/detail formatter
# ("bytes" or "tokens"). Adding a metric = one registry entry + (for expensive
# ones) a field on Session and a branch in ensure_sizes().
@dataclass
class SizeMetric:
    label: str
    get: "callable"
    unit: str = "bytes"
    expensive: bool = False


SIZE_METRICS: dict[str, SizeMetric] = {
    "log": SizeMetric("Log size", lambda s: s.log_bytes, unit="bytes"),
    # Ready to enable once the -l cost is acceptable — the machinery below
    # (ensure_sizes, Session.latest_context, scan_latest_context) already exists:
    # "context": SizeMetric("Latest context", lambda s: s.latest_context or 0,
    #                       unit="tokens", expensive=True),
}
DEFAULT_SIZE_METRIC = "log"


def ensure_sizes(sessions: list[Session], metric: str) -> None:
    """Populate whatever a size metric needs before it can be read. Cheap metrics
    (log) are no-ops; expensive ones parse each log once. Call before sorting or
    reading the size column."""
    m = SIZE_METRICS[metric]
    if not m.expensive:
        return
    if metric == "context":
        for s in sessions:
            if s.latest_context is None and s.path:
                s.latest_context = scan_latest_context(s.path)


def clean_line(s: str) -> str:
    """Collapse all whitespace (incl. tabs/newlines) so a value is safe in one
    tab-separated field."""
    return " ".join(str(s).split())


def dirname(cwd: str | None) -> str:
    if not cwd:
        name = "?"
    else:
        name = os.path.basename(os.path.normpath(cwd)) or cwd
    if len(name) > DIR_WIDTH:
        name = name[: DIR_WIDTH - 1] + "…"  # ellipsis
    return name.ljust(DIR_WIDTH)


def format_time(dt: datetime | None, now: datetime) -> str:
    # Mirror `ls -l`: "Mon DD HH:MM" for recent, "Mon DD  YYYY" for >6 months.
    if dt is None:
        return "%12s" % "-"
    local = dt.astimezone()
    six_months = 182 * 24 * 3600
    if abs((now - dt).total_seconds()) > six_months:
        return local.strftime("%b %e  %Y")
    return local.strftime("%b %e %H:%M")


def iso(dt: datetime | None) -> str:
    if dt is None:
        return "-"
    return dt.astimezone().isoformat(timespec="seconds")


def human_bytes(n: int) -> str:
    step = 1024.0
    unit = "B"
    val = float(n)
    for u in ("B", "KiB", "MiB", "GiB"):
        unit = u
        if val < step:
            break
        val /= step
    if unit == "B":
        return f"{n} B"
    return f"{val:.1f} {unit} ({n:,} B)"


def human_duration(delta_or_secs) -> str:
    secs = (int(delta_or_secs.total_seconds())
            if hasattr(delta_or_secs, "total_seconds") else int(delta_or_secs))
    if secs < 0:
        secs = 0
    d, rem = divmod(secs, 86400)
    h, rem = divmod(rem, 3600)
    m, s = divmod(rem, 60)
    parts = []
    if d:
        parts.append(f"{d}d")
    if h or d:
        parts.append(f"{h}h")
    if m or h or d:
        parts.append(f"{m}m")
    parts.append(f"{s}s")
    return " ".join(parts)


def human_tokens(n: int) -> str:
    if n >= 1_000_000:
        return f"{n / 1e6:.1f}M"
    if n >= 1_000:
        return f"{n / 1e3:.1f}k"
    return str(n)


def human_size_short(n: int) -> str:
    # Compact, column-friendly, `ls -h` style: 1024-based, one decimal below 10.
    val = float(n)
    for unit in ("", "K", "M", "G", "T"):
        if unit == "":
            if val < 1024:
                return str(n)
        elif val < 1024:
            return (f"{val:.1f}{unit}" if val < 10 else f"{val:.0f}{unit}")
        val /= 1024
    return f"{val:.0f}P"


class Palette:
    def __init__(self, enabled: bool):
        self.on = enabled

    def _w(self, code: str, text: str) -> str:
        return f"\033[{code}m{text}\033[0m" if self.on else text

    def dim(self, t): return self._w("2", t)
    def gray(self, t): return self._w("90", t)     # grey, for deleted sessions
    def dir(self, t): return self._w("34", t)      # blue, like ls dirs
    def name(self, t): return self._w("0", t)
    def uuid(self, t): return self._w("33", t)     # yellow for unnamed
    def open(self, t): return self._w("1;32", t)   # bold green for live sessions
    def label(self, t): return self._w("1", t)     # bold field labels
    def head(self, t): return self._w("1;36", t)   # bold cyan session heading
    def warn(self, t): return self._w("1;31", t)   # bold red for warnings


def color_enabled(mode: str, stream) -> bool:
    if mode == "always":
        return True
    if mode == "never":
        return False
    return stream.isatty() and not os.environ.get("NO_COLOR")
