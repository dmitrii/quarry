#!/usr/bin/env python3
"""quarry rm — remove a session's on-disk artifacts, ls/rm-style.

Deletes only the files/dirs named after the session UUID (the log, its sidecar
subagents/tool-results dir, session-env, and file-history). Centralized files
(history.jsonl, ~/.claude.json) are intentionally left untouched — orphaned
references there are harmless. Refuses to touch a session that is currently
open in a live `claude` process. Prompts before deleting (rm -i style).
"""

from __future__ import annotations

import argparse
import os
import shutil
import sys

sys.path.insert(0, os.path.dirname(os.path.realpath(__file__)))
import claude_sessions as cs  # noqa: E402


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="quarry rm",
        description="Remove a session's artifacts by UUID/prefix/name. "
                    "Only UUID-named files are deleted; no centralized files are edited.",
    )
    p.add_argument("query", nargs="?",
                   help="the session to remove (UUID, prefix, or name). "
                        "If omitted, offers the last session run in the current directory.")
    p.add_argument("-f", "--force", action="store_true",
                   help="do not prompt for confirmation")
    p.add_argument("-n", "--dry-run", action="store_true",
                   help="show what would be removed, but delete nothing")
    p.add_argument("--color", choices=("auto", "always", "never"), default="auto",
                   help="when to colorize output (default: auto)")
    return p


def main(argv: list[str]) -> int:
    args = build_parser().parse_args(argv)
    root = cs.config_dir()
    pal = cs.Palette(cs.color_enabled(args.color, sys.stderr))

    sessions = cs.discover(root)
    deleted = cs.deleted_sessions(root)

    note = None
    if args.query is None:
        # No-arg: offer the session Claude last ran in this directory — and ONLY
        # that one. Never fall back to older sessions ("don't dig deeper").
        cwd = os.getcwd()
        sid = cs.project_last_session(root, cwd)
        if not sid:
            print(f"no session given and none recorded for {cwd}; "
                  f"pass a UUID or name.", file=sys.stderr)
            return 2
        if args.force:
            print("refusing to force-remove an inferred session; omit -f to "
                  "confirm interactively, or name it explicitly.", file=sys.stderr)
            return 2
        match = next((s for s in sessions + deleted if s.uuid == sid), None)
        if match is None:
            print(f"last session for {cwd} ({sid}) is unknown; pass a UUID or name.",
                  file=sys.stderr)
            return 1
        note = f"No session given; offering the last one run in {cwd}:"
    else:
        match, candidates = cs.find_session(sessions + deleted, args.query)
        if match is None:
            if candidates:
                print(f"'{args.query}' is ambiguous — refusing to guess. Matches:",
                      file=sys.stderr)
                for c in candidates:
                    print(f"  {c.uuid}  {c.title or c.ai_title or ''}", file=sys.stderr)
            else:
                print(f"no session matches '{args.query}'", file=sys.stderr)
            return 1

    label = match.title or match.ai_title or match.uuid
    if note:
        print(pal.dim(note))

    # Never delete artifacts for a session that's currently running.
    if match.open:
        print(pal.warn(f"refusing: session {match.uuid} is open in a live process "
                       f"(PID {match.pid}). Quit it first."), file=sys.stderr)
        return 1

    items = cs.artifacts(root, match)
    if not items:
        print(f"nothing to remove for {match.uuid} "
              f"(already gone).", file=sys.stderr)
        return 0

    total = sum(cs.path_size(p) for p in items)
    verb = "Would remove" if args.dry_run else "About to remove"
    print(f"{verb} session {pal.head(match.uuid)}"
          + (f'  "{label}"' if label != match.uuid else ""))
    for p in items:
        suffix = "/" if p.is_dir() else ""
        size = cs.human_size_short(cs.path_size(p)).rjust(cs.SIZE_COL_W)
        print(f"  {pal.dim(size)}  {p}{suffix}")
    print(f"  {len(items)} item(s), {cs.human_bytes(total)}")

    if args.dry_run:
        return 0

    if not args.force:
        if not sys.stdin.isatty():
            print("refusing to delete without a prompt; pass -f to force or -n to preview.",
                  file=sys.stderr)
            return 1
        try:
            answer = input("Remove these? [y/N] ").strip().lower()
        except (EOFError, KeyboardInterrupt):
            print()
            return 1
        if answer not in ("y", "yes"):
            print("aborted.", file=sys.stderr)
            return 1

    errors = 0
    for p in items:
        try:
            if p.is_dir() and not p.is_symlink():
                shutil.rmtree(p)
            else:
                p.unlink()
        except OSError as e:
            print(pal.warn(f"failed to remove {p}: {e}"), file=sys.stderr)
            errors += 1
    if errors:
        return 1
    print(f"removed {len(items)} item(s) for {match.uuid}.")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
