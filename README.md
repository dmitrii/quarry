# ls-claude

Browse and manage your [Claude Code](https://claude.com/claude-code) sessions from the
terminal, following `ls`/`rm` conventions. Reads Claude's on-disk state under
`$CLAUDE_CONFIG_DIR` (default `~/.claude`) and `~/.claude.json` — no Claude/agent
invocations. Two commands:

- **`ls-claude`** — list sessions, or show one in detail.
- **`rm-claude`** — remove a session's on-disk artifacts (with confirmation).
- **`fzf-claude`** — interactively find a session (fuzzy over names/prompts/replies) and
  resume it. Requires [`fzf`](https://github.com/junegunn/fzf).

## Install

Dependency-free Python 3. The two commands are thin front-ends over a shared module
(`claude_sessions.py`); symlink the commands onto your `PATH` (they resolve the symlink
to find the module, so it can stay in the repo):

```sh
mkdir -p ~/.local/bin
ln -s "$PWD/ls-claude"  ~/.local/bin/ls-claude
ln -s "$PWD/rm-claude"  ~/.local/bin/rm-claude
ln -s "$PWD/fzf-claude" ~/.local/bin/fzf-claude   # needs `fzf` on PATH
# ensure ~/.local/bin is on PATH (fish):
fish_add_path ~/.local/bin
```

## `ls-claude`

```
ls-claude [-l] [-a] [-c] [-r] [-t] [-S] [--size {log}] [--color {auto,always,never}] [query]
```

### Listing

- `ls-claude` — one session per line: the session's name, or its UUID when it has
  no custom name. Sorted by **last interaction**, most recent first (like `ls -t`).
- `-l` — long view. Columns: open marker, size, timestamp, launch directory
  (fixed 20 chars), and name/UUID.
- `-a` — also list **removed** sessions still referenced in `~/.claude.json` (in grey),
  like `ls -a` surfacing otherwise-hidden entries.
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
  Latest context 23.5k tokens (23,472)
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
- **Latest context** is how full the window was on the *last* request (input + both
  cache buckets of the final main-thread `assistant` record) — distinct from the *peak*
  on the Tokens line. Useful for gauging how heavy a session is to resume.
- Prompt previews grow to fill a wider terminal.
- **Removed sessions.** The detail view works for a removed session too (resolvable by
  UUID/name even without `-a`): log-only fields show `-`, while directory, duration,
  tokens, cost, and model are recovered from `~/.claude.json`'s last-run record.

## `rm-claude`

```
rm-claude [-f] [-n] [--color {auto,always,never}] [query]
```

Removes a session's on-disk artifacts, resolving `query` exactly as the detail view
does (ambiguous queries are **refused**, not guessed). It deletes only the files/dirs
named after the UUID:

- `projects/<encoded-cwd>/<uuid>.jsonl` and its `<uuid>/` sidecar dir
- `session-env/<uuid>/`
- `file-history/<uuid>/`

Centralized files (`history.jsonl`, `~/.claude.json`) are **left untouched** — the
orphaned references there are harmless, and this keeps `rm-claude` from ever rewriting
shared state. (A session you remove therefore lingers as a grey `-a` entry until Claude
next overwrites that directory's `lastSessionId`.)

**No argument** — offers the session Claude **last ran in the current directory**
(`~/.claude.json`'s `lastSessionId` for `$PWD`), so right after quitting a session you can
just type `rm-claude`. It considers *only* that one session — never falling back to older
ones — and declines cleanly if it's already removed or still running. `-f` is refused with
no argument (an inferred target must be confirmed interactively).

Safety:
- **Refuses to remove a session that is currently open** in a live `claude` process.
- Prompts before deleting (`rm -i` style). `-f` skips the prompt; `-n` previews the
  exact file list and total size and deletes nothing. Without a TTY and without `-f`,
  it refuses rather than delete unprompted.

```
$ rm-claude -n debug-flaky
Would remove session 8f3c1a92-4b7e-4c1d-9a2f-1e6d0b5c7a34  "Track down and fix the intermittent test failure"
    26K  ~/.claude/projects/-Users-you/8f3c1a92-….jsonl
      0  ~/.claude/session-env/8f3c1a92-…/
  2 item(s), 25.5 KiB (26,143 B)
```

## `fzf-claude`

Interactive picker. It's a thin launcher — `ls-claude --tsv` piped into `fzf` — so `fzf`
provides the whole UI and the keybindings decide what happens to the session you pick:

| key | action |
|-----|--------|
| `enter` | **resume** it (`cd <dir> && claude --resume <uuid>`) |
| `ctrl-x` | **delete** it (`rm-claude`), then refresh the list |
| `ctrl-y` | copy its UUID to the clipboard |
| `tab` | **widen the search scope**: `names` → `prompts` → `replies` |

`enter` **refuses to resume a session that's currently open** in a live process (checked
at resume time) — it prints a message and bails rather than risk a second attach. Deleting
an open session via `ctrl-x` is likewise refused by `rm-claude`.

The preview pane is `ls-claude <uuid>` (the detail view), live as you scroll. Matching is
**exact-substring** (`fzf --exact`) — fuzzy subsequence matching is useless once the
searchable text includes whole transcripts (any short query's letters appear *somewhere*
in a 100k-token blob).

**Scope** (cycled with `tab`) controls how much text is searchable, via
`ls-claude --tsv --scope`:

- `names` — session title + AI title (cheap; the default)
- `prompts` — the above + everything you typed
- `replies` — the above + the agent's text responses

`prompts`/`replies` parse each log, so they're heavier than `names` — but only the picker
uses them, on demand.

`ls-claude --tsv` emits `UUID⇥label⇥dir⇥date⇥searchable` and is the machine-readable
counterpart to the normal listing; you can pipe it into your own tools too.

Resume is directory-scoped, so on `enter` the wrapper `cd`s into the session's launch
directory before `exec claude --resume`. The resume happens in the wrapper *after* fzf
exits (fzf prints the selection; the script execs Claude) rather than via fzf's `become` —
with `become` the resumed session renders but never receives keyboard input, because it
doesn't inherit the terminal on stdin. Removed sessions aren't listed (they can't be
resumed).

## Size metrics

Size is pluggable via the `SIZE_METRICS` registry in `claude_sessions.py`; `--size` and
`-S` both read from it. Today the only enabled metric is `log` (log-file bytes on disk),
which is cheap — a `stat()` per file.

A metric declares a `unit` (`bytes`/`tokens`) and whether it's `expensive` (needs the log
parsed). Cheap metrics read a field off `Session`; expensive ones are filled by
`ensure_sizes()` before listing. A `context` metric (latest post-prompt context size) is
wired up and commented out in the registry — uncommenting it enables `--size context` for
the column and `-S` sorting. It's left off by default because, unlike `log`, it must parse
every log just to list.

## Layout

- `claude_sessions.py` — shared core: discovery, matching, log analysis, artifact listing,
  and formatting. The Python commands import it.
- `ls-claude`, `rm-claude` — thin Python CLI front-ends.
- `fzf-claude` — thin shell launcher around `fzf` + `ls-claude --tsv`.

## Data source

Everything comes from `$CLAUDE_CONFIG_DIR` (default `~/.claude`): `projects/*/*.jsonl` for
the logs, `sessions/<pid>.json` for liveness, and `~/.claude.json` for per-directory
`lastSessionId` pointers (used to surface removed sessions). These are undocumented Claude
Code internals (developed against CLI v2.1.x) and may change; the tools degrade gracefully
when files are missing or unparseable.

## Possible improvements

- **`full-log` scope for `fzf-claude`** — a fourth scope that greps entire logs (tool
  output included) via `rg`. Too big to preload like the others, so it'd use fzf's
  reload-per-keystroke ripgrep pattern rather than preloaded exact matching.
- **AI-title fallback in the listing** — for sessions with no custom name, show the
  (dimmed) AI title instead of the bare UUID, so unnamed sessions are readable at a glance.
- **More size metrics** — e.g. `tokens` (latest context is already wired, commented out in
  `SIZE_METRICS`) or `messages`, selectable via `--size`. These parse every log to list.
- **Directory filter** — restrict the listing to sessions launched under a given path
  (mirroring how `claude --resume` is scoped), e.g. `ls-claude --cwd .`.
- **Standalone `resume-claude <query>`** — resume by name/UUID without the picker (the old
  `-R` idea, as its own command rather than a flag on `ls-claude`, which shouldn't launch
  things). `fzf-claude`'s `enter` binding already covers the interactive case.
