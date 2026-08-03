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
    """Select sessions to rename. No selector -> the last session run in `cwd`.
    A selector is a shell glob over UUID or current name; already-titled sessions
    are skipped unless `retitle` or the selector names exactly one session."""
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


def _resolver(session, cfg, model):
    """Build a lazy variable resolver for one session: free vars are instant,
    $SUMMARY shells out (only when actually reached during rendering)."""
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


def _skeleton_resolver(session):
    """Resolver for --dry-run: free vars filled, $SUMMARY left literal."""
    def resolve(name):
        v = cs.free_variable(session, name)
        if v is None:
            return "$SUMMARY" if name == "SUMMARY" else ""
        return v
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
            skeleton = cs.render_template(template, _skeleton_resolver(s))
            print(f"{s.uuid}  {s.title or s.ai_title or ''}  ->  {skeleton}")
        return 0

    return _run_interactive(targets, template, cfg, args.model, pal)  # Task 7


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
