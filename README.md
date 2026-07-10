# ls-claude

List your [Claude Code](https://claude.com/claude-code) sessions from the terminal,
following `ls` conventions. Reads the JSONL session logs under
`$CLAUDE_CONFIG_DIR/projects/` (default `~/.claude/projects/`) — no Claude/agent
invocations, just the on-disk logs.

## Install

`ls-claude` is a single, dependency-free Python 3 script. Symlink it onto your `PATH`:

```sh
mkdir -p ~/.local/bin
ln -s "$PWD/ls-claude" ~/.local/bin/ls-claude
# ensure ~/.local/bin is on PATH (fish):
fish_add_path ~/.local/bin
```

## Usage

```
ls-claude [-l] [-c] [-r] [-t] [-S] [--size {log}] [--color {auto,always,never}] [query]
```

### Listing

- `ls-claude` — one session per line: the session's name, or its UUID when it has
  no custom name. Sorted by **last interaction**, most recent first (like `ls -t`).
- `-l` — long view. Columns: open marker, size, timestamp, launch directory
  (fixed 20 chars), and name/UUID.
- `-c` — sort by **start** time instead of last interaction.
- `-S` — sort by **size**, largest first.
- `-r` — reverse the sort (oldest/smallest first).
- `-t` — accepted for `ls` familiarity (time sort is already the default).
- `--size {log}` — which notion of size to show and sort by (see below).
- `--color {auto,always,never}` — `auto` colorizes only on a TTY and respects `NO_COLOR`.

In `-l`, a leading **`*`** (bold green) marks a session that is **currently open** in a
live `claude` process, determined from `$CLAUDE_CONFIG_DIR/sessions/<pid>.json` (the
PID must still be alive; stale entries are ignored).

```
$ ls-claude -lS
*   1.0M  Jul  9 23:00  repos                 2026-01-15-refactor-auth-module
*   751K  Jul 10 07:50  you               2026-01-14-add-dark-mode-toggle
    370K  Jul  9 23:03  you               2026-01-12-debug-flaky-test
     26K  Jul  9 17:09  you               8f3c1a92-4b7e-4c1d-9a2f-1e6d0b5c7a34
```

### Detail view

Pass a UUID (or unique prefix) or a name to inspect one session. Matching precedence:
exact UUID → exact title (custom or AI) → UUID prefix → case-insensitive title
substring. Ambiguous queries list the candidates.

```
$ ls-claude debug-flaky
Track down and fix the intermittent test failure
  Session ID     8f3c1a92-4b7e-4c1d-9a2f-1e6d0b5c7a34
  Name           (unset — use /rename to set)
  AI title       Track down and fix the intermittent test failure
  Directory      /Users/you
  Started        2026-01-12T11:03:54-08:00
  Last update    2026-01-12T11:41:12-08:00
  Duration       5m 17s
  Process        NOT OPEN
  Resume         cd /Users/you && claude --resume 8f3c1a92-…
  User prompts   2
  Agent replies  2
  Tool calls     0
  Tokens         ↓ 1.2k generated · ↑ 23.5k peak context
  Log size       25.5 KiB (26,143 B)
  Model          claude-opus-4-8
  Git branch     HEAD
  CLI version    2.1.197
  First prompt   …
  Last prompt    …
```

Notes:
- **Name vs AI title.** `Name` is the user-set title (`/rename`); `AI title` is Claude's
  auto-generated one. Both are always shown so it's clear which is which.
- **Resume.** `claude --resume` is scoped to the *current directory's* project, so the
  command prepends `cd <launch dir> &&` unless you're already there. (Running it from
  the wrong directory is why `claude --resume <id>` reports "No conversation found".)
- **Agent replies** counts distinct model responses (unique message IDs), not raw log
  records. **Tokens** are summed generated output and peak input context from usage data.
- Prompt previews grow to fill a wider terminal.

## Size metrics

Size is pluggable via the `SIZE_METRICS` registry in the script; `--size` and `-S` both
read from it. Today the only metric is `log` (log-file bytes on disk), which is cheap —
a `stat()` per file. Adding a metric is a one-line registry entry.

## Data source

Everything comes from `$CLAUDE_CONFIG_DIR` (default `~/.claude`):
`projects/*/*.jsonl` for the logs and `sessions/<pid>.json` for liveness. These are
undocumented Claude Code internals (developed against CLI v2.1.x) and may change; the
script degrades gracefully when files are missing or unparseable.

## Possible improvements

- **`-R` / `--resume <query>`** — resolve a query like the detail view, then either
  *print* the exact `cd … && claude --resume …` (composable: `eval "$(ls-claude -R hawk)"`)
  or *exec* it to jump straight back into the session. Print would be the safe default,
  exec behind a second flag. Orthogonal to `-l`: `-l` is for browsing, `-R` for acting.
- **AI-title fallback in the listing** — for sessions with no custom name, show the
  (dimmed) AI title instead of the bare UUID, so unnamed sessions are readable at a glance.
- **More size metrics** — e.g. `tokens` or `messages`, selectable via `--size`. Unlike
  `log`, these require fully parsing every log just to list, so they'd be opt-in only.
- **Directory filter** — restrict the listing to sessions launched under a given path
  (mirroring how `claude --resume` is scoped), e.g. `ls-claude --cwd .`.
