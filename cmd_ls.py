#!/usr/bin/env python3
"""quarry ls — list sessions, ls-style.

Lists sessions by custom title, or by UUID when untitled. Sorted by last
interaction, most recent first (like `ls -t`); -c sorts by start time, -S by
size. In the long view a leading '*' (bold green) marks a session open in a
live `claude` process. Pass a UUID/prefix/name to show one session in detail.

With -a, sessions whose logs were removed but are still referenced in
~/.claude.json are also listed (in grey), like `ls -a` surfacing hidden entries.
"""

from __future__ import annotations

import argparse
import os
import shlex
import shutil
import sys
from datetime import datetime, timezone
from pathlib import Path

# Resolve the symlink so imports work when installed via a bin/ symlink.
sys.path.insert(0, os.path.dirname(os.path.realpath(__file__)))
import claude_sessions as cs  # noqa: E402


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="quarry ls",
        description="List sessions, sorted by time (ls-style). "
                    "Give a UUID (or prefix) or a name to show one session in detail.",
    )
    p.add_argument("query", nargs="?",
                   help="show details for the session matching this UUID/prefix/name")
    p.add_argument("-l", dest="long", action="store_true",
                   help="long view: timestamp, launch directory, and name/UUID")
    p.add_argument("-a", dest="all", action="store_true",
                   help="also list removed sessions still referenced in .claude.json")
    p.add_argument("-c", dest="by_started", action="store_true",
                   help="sort by session start time (default: last interaction)")
    p.add_argument("-r", dest="reverse", action="store_true",
                   help="reverse order (oldest first, or smallest first with -S)")
    p.add_argument("-t", dest="_t", action="store_true",
                   help="sort by time (default; accepted for ls familiarity)")
    p.add_argument("-S", dest="by_size", action="store_true",
                   help="sort by size, largest first")
    p.add_argument("--size", choices=tuple(cs.SIZE_METRICS),
                   default=cs.DEFAULT_SIZE_METRIC,
                   help=f"which size to show/sort by (default: {cs.DEFAULT_SIZE_METRIC})")
    p.add_argument("--tsv", action="store_true",
                   help="machine-readable listing (UUID<TAB>label<TAB>dir<TAB>date"
                        "<TAB>searchable) for piping into `quarry fzf`")
    p.add_argument("--scope", choices=("names", "prompts", "replies"), default="names",
                   help="how much text --tsv puts in the searchable column "
                        "(names=title+AI title; prompts=+your prompts; replies=+agent text)")
    p.add_argument("--color", choices=("auto", "always", "never"), default="auto",
                   help="when to colorize output (default: auto)")
    # Internal predicate used by `quarry fzf` before resuming: exit 0 if the given
    # session is open in a live process, 1 otherwise.
    p.add_argument("--isopen", metavar="UUID", help=argparse.SUPPRESS)
    return p


def emit_tsv(listing, time_key, scope):
    for s in listing:
        label = cs.clean_line(s.title or s.ai_title or s.uuid)
        dirp = (s.cwd or "").replace("\t", " ").replace("\n", " ")
        dt = time_key(s)
        date = dt.astimezone().strftime("%Y-%m-%d %H:%M") if dt else ""
        content = s.ai_title or ""
        if scope in ("prompts", "replies") and s.path:
            content = f"{content} {cs.scan_text(s.path, scope == 'replies')}"
        print("\t".join((s.uuid, label, dirp, date, cs.clean_line(content))))


def _row(pal, lbl, val):
    print(f"  {pal.label(lbl.ljust(cs.LABEL_W))}  {val}")


def print_detail(s: cs.Session, pal: cs.Palette, now: datetime, width: int) -> None:
    if s.deleted:
        print_deleted_detail(s, pal, width)
        return

    stats = cs.analyze(s)
    print(pal.head(s.title or s.ai_title or s.uuid))
    _row(pal, "Session ID", s.uuid)
    # Name and AI title are always shown, side by side, so it's clear which is
    # user-set and which Claude generated — and that an unset name can be set.
    _row(pal, "Name", s.title if s.title else pal.dim("(unset — use /rename to set)"))
    _row(pal, "AI title", pal.dim(s.ai_title) if s.ai_title else pal.dim("(none yet)"))
    _row(pal, "Directory", s.cwd or "?")
    _row(pal, "Started", cs.iso(s.started))
    _row(pal, "Last update", cs.iso(s.last))
    if s.started and s.last:
        _row(pal, "Duration", cs.human_duration(s.last - s.started))
    if s.open:
        state = f"{s.pid}" + (f" ({s.status})" if s.status else "")
        _row(pal, "Process", pal.open(state))
    else:
        _row(pal, "Process", pal.dim("NOT OPEN"))

    # `claude --resume` is scoped to the current directory's project, so prefix
    # a `cd` unless we're already in the session's launch directory.
    cmd = f"claude --resume {s.uuid}"
    if s.cwd and os.path.normpath(s.cwd) != os.path.normpath(os.getcwd()):
        cmd = f"cd {shlex.quote(s.cwd)} && {cmd}"
    _row(pal, "Resume", pal.dim(cmd))

    _row(pal, "User prompts", str(stats["user_prompts"]))
    _row(pal, "Agent replies", str(stats["assistant_msgs"]))
    _row(pal, "Tool calls", str(stats["tool_calls"]))
    tok = (f"↓ {cs.human_tokens(stats['output_tokens'])} generated · "
           f"↑ {cs.human_tokens(stats['context_peak'])} peak context")
    _row(pal, "Tokens", tok)
    _row(pal, "Log size", cs.human_bytes(stats["log_bytes"]))
    lc = stats["latest_context"]
    _row(pal, "Latest context",
         f"{cs.human_tokens(lc)} tokens ({lc:,})" if lc else pal.dim("-"))
    models = stats["models"]
    if models:
        _row(pal, "Model" if len(models) == 1 else "Models", ", ".join(models))
    if stats["branch"]:
        _row(pal, "Git branch", stats["branch"])
    if stats["version"]:
        _row(pal, "CLI version", stats["version"])

    # Fit the prompt preview to the terminal: wider terminal -> more text.
    avail = max(20, width - cs.ROW_PREFIX)

    def preview(text):
        if not text:
            return pal.dim("-")
        return text if len(text) <= avail else text[: avail - 1] + "…"

    if stats["first_prompt"]:
        _row(pal, "First prompt", pal.dim(preview(stats["first_prompt"])))
    if stats["last_prompt"] and stats["last_prompt"] != stats["first_prompt"]:
        _row(pal, "Last prompt", pal.dim(preview(stats["last_prompt"])))


def print_deleted_detail(s: cs.Session, pal: cs.Palette, width: int) -> None:
    """Detail for a removed session: show what .claude.json still knows, and a
    dash for everything that lived only in the (now-gone) log."""
    dash = pal.dim("-")
    m = s.last_meta or {}
    print(pal.gray(s.uuid) + pal.warn("  (deleted)"))
    _row(pal, "Session ID", s.uuid)
    _row(pal, "Name", dash)
    _row(pal, "AI title", dash)
    _row(pal, "Directory", s.cwd or "?")
    _row(pal, "Started", dash)
    _row(pal, "Last update", dash)
    dur = m.get("lastDuration")
    _row(pal, "Duration", cs.human_duration(dur / 1000) if dur else dash)
    _row(pal, "Process", pal.gray("deleted — log no longer present"))
    _row(pal, "Resume", pal.dim("(unavailable — log deleted)"))
    _row(pal, "User prompts", dash)
    _row(pal, "Agent replies", dash)
    _row(pal, "Tool calls", dash)

    usage = m.get("lastModelUsage") or {}
    out_tok = (sum(v.get("outputTokens", 0) for v in usage.values())
               or m.get("lastTotalOutputTokens") or 0)
    in_tok = (sum(v.get("inputTokens", 0) + v.get("cacheReadInputTokens", 0)
                  + v.get("cacheCreationInputTokens", 0) for v in usage.values())
              or m.get("lastTotalInputTokens") or 0)
    if out_tok or in_tok:
        _row(pal, "Tokens", f"↓ {cs.human_tokens(out_tok)} generated · "
                            f"↑ {cs.human_tokens(in_tok)} last input")
    else:
        _row(pal, "Tokens", dash)
    if m.get("lastCost") is not None:
        _row(pal, "Cost", f"${m['lastCost']:.4f}")
    _row(pal, "Log size", dash)
    if usage:
        _row(pal, "Model" if len(usage) == 1 else "Models", ", ".join(usage))
    print()
    print(pal.dim("  Log deleted; the figures above are from Claude's last-run "
                  "record for this directory (.claude.json)."))


def main(argv: list[str]) -> int:
    args = build_parser().parse_args(argv)
    root = cs.config_dir()

    if args.isopen:
        return 0 if args.isopen in cs.live_sessions(root) else 1

    sessions = cs.discover(root)
    deleted = cs.deleted_sessions(root)

    now = datetime.now(timezone.utc)
    time_key = (lambda s: s.started) if args.by_started else (lambda s: s.last)
    metric = cs.SIZE_METRICS[args.size]
    size_of = metric.get
    size_fmt = cs.human_tokens if metric.unit == "tokens" else cs.human_size_short
    epoch = datetime.min.replace(tzinfo=timezone.utc)

    pal = cs.Palette(cs.color_enabled(args.color, sys.stdout))

    # Detail view can resolve a removed session too, so it never "breaks".
    if args.query is not None:
        match, candidates = cs.find_session(sessions + deleted, args.query)
        if match is None:
            if candidates:
                print(f"'{args.query}' is ambiguous; matches:", file=sys.stderr)
                for c in candidates:
                    print(f"  {c.uuid}  {c.title or c.ai_title or ''}", file=sys.stderr)
            else:
                print(f"no session matches '{args.query}'", file=sys.stderr)
            return 1
        width = shutil.get_terminal_size(fallback=(80, 24)).columns
        print_detail(match, pal, now, width)
        return 0

    listing = sessions + deleted if args.all else sessions
    cs.ensure_sizes(listing, args.size)   # no-op for cheap metrics (e.g. log)
    if args.by_size:
        listing.sort(key=size_of, reverse=not args.reverse)   # largest first (ls -S)
    else:
        listing.sort(key=lambda s: time_key(s) or epoch, reverse=not args.reverse)

    if args.tsv:
        emit_tsv(listing, time_key, args.scope)
        return 0

    if not listing:
        print("no sessions found", file=sys.stderr)
        return 0

    for s in listing:
        if s.deleted:
            name = pal.gray(s.display_name)
        elif s.open:
            name = pal.open(s.display_name)
        else:
            name = pal.name(s.display_name) if s.named else pal.uuid(s.uuid)
        if args.long:
            # Leading one-char column, ls -F style: '*' marks a live session.
            mark = pal.open("*") if s.open else " "
            size = pal.dim("-".rjust(cs.SIZE_COL_W)) if s.deleted else \
                pal.dim(size_fmt(size_of(s)).rjust(cs.SIZE_COL_W))
            ts = pal.dim(cs.format_time(time_key(s), now))
            print(f"{mark} {size}  {ts}  {pal.dir(cs.dirname(s.cwd))}  {name}")
        else:
            print(name)
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main(sys.argv[1:]))
    except BrokenPipeError:
        # e.g. piped into `head`; exit quietly like ls does.
        try:
            sys.stdout.close()
        finally:
            os._exit(0)
