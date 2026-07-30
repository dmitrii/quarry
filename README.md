# quarry

Dig through your [Claude Code](https://claude.com/claude-code) session history from the
terminal, following `ls`/`rm` conventions. `quarry` inspects Claude's on-disk state under
`$CLAUDE_CONFIG_DIR` (default `~/.claude`) and `~/.claude.json` — listing, inspecting, and
removing sessions only ever read files, never launching Claude or any agent. (Resuming is
the one exception: `quarry fzf` and the detail view's `Resume` line hand off to
`claude --resume`.) One command, a few subcommands:

- **`quarry ls`** — list sessions, or show one in detail.
- **`quarry rm`** — remove a session's on-disk artifacts (with confirmation).
- **`quarry fzf`** — interactively find a session (fuzzy over names/prompts/replies) and
  resume it. Requires [`fzf`](https://github.com/junegunn/fzf).
- **`quarry completions`** — emit a shell completion script (fish/zsh/bash).

> Independent project, not affiliated with or endorsed by Anthropic. Claude and Claude
> Code are trademarks of Anthropic, PBC. "for Claude Code" is descriptive/nominative use.

## Install

Dependency-free: Python 3.8+ and, for `quarry fzf`, [`fzf`](https://github.com/junegunn/fzf)
on your `PATH`. The tool runs straight from the checkout; `make install` just symlinks the
entry point onto your `PATH` (it resolves the symlink to find `src/`, so the repo can stay
put).

```sh
git clone https://github.com/dmitrii/quarry.git && cd quarry
make install               # symlink bin/quarry -> ~/.local/bin/quarry
make install-completions   # fish autoloads; zsh/bash print the one rc line to add
```

Override locations with `PREFIX=` / `BINDIR=` etc. (`make help` lists them). Make sure the
bin dir is on your `PATH` — fish: `fish_add_path ~/.local/bin`. Undo with `make uninstall`.

## `quarry ls`

```
quarry ls [-l] [-a] [-c] [-r] [-t] [-S] [--size {log}] [--color {auto,always,never}] [query]
```

### Listing

- `quarry ls` — one session per line: the session's name, or its UUID when it has no
  custom name. Sorted by **last interaction**, most recent first (like `ls -t`).
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
live `claude` process, determined from `$CLAUDE_CONFIG_DIR/sessions/<pid>.json` (the PID
must still be alive; stale entries are ignored).

```
$ quarry ls -lS
*   1.2M  Jan 15 09:42  my-app                2026-01-15-refactor-auth-module
    486K  Jan 14 16:20  my-app                2026-01-14-add-dark-mode-toggle
     72K  Jan 12 11:03  scratch               2026-01-12-debug-flaky-test
     18K  Jan 10 14:55  scratch               8f3c1a92-4b7e-4c1d-9a2f-1e6d0b5c7a34
```

### Detail view

Pass a UUID (or unique prefix) or a name to inspect one session. Matching precedence:
exact UUID → exact title (custom or AI) → UUID prefix → case-insensitive title
substring. Ambiguous queries list the candidates.

```
$ quarry ls debug-flaky
Track down and fix the intermittent test failure
  Session ID     8f3c1a92-4b7e-4c1d-9a2f-1e6d0b5c7a34
  Name           2026-01-12-debug-flaky-test
  AI title       Track down and fix the intermittent test failure
  Directory      /Users/you/code/scratch
  Started        2026-01-12T11:03:54-08:00
  Last update    2026-01-12T11:41:12-08:00
  Duration       37m 18s
  Process        NOT OPEN
  Resume         cd /Users/you/code/scratch && claude --resume 8f3c1a92-…
  User prompts   9
  Agent replies  14
  Tool calls     22
  Tokens         ↓ 8.1k generated · ↑ 96.4k peak context
  Log size       71.6 KiB (73,301 B)
  Latest context 42.0k tokens (41,984)
  Model          claude-opus-4-8
  Git branch     main
  CLI version    2.1.197
  First prompt   …
  Last prompt    …
```

Notes:
- **Name vs AI title.** `Name` is the user-set title (`/rename`); `AI title` is Claude's
  auto-generated one. Both are always shown so it's clear which is which.
- **Resume.** `claude --resume` is scoped to the *current directory's* project, so the
  command prepends `cd <launch dir> &&` unless you're already there. (Running it from the
  wrong directory is why `claude --resume <id>` reports "No conversation found".)
- **Agent replies** counts distinct model responses (unique message IDs), not raw log
  records. **Tokens** are summed generated output and peak input context from usage data.
- **Latest context** is how full the window was on the *last* request (input + both cache
  buckets of the final main-thread `assistant` record) — distinct from the *peak* on the
  Tokens line. Useful for gauging how heavy a session is to resume.
- Prompt previews grow to fill a wider terminal.
- **Removed sessions.** The detail view works for a removed session too (resolvable by
  UUID/name even without `-a`): log-only fields show `-`, while directory, duration,
  tokens, cost, and model are recovered from `~/.claude.json`'s last-run record.

## `quarry rm`

```
quarry rm [-f] [-n] [--color {auto,always,never}] [query]
```

Removes a session's on-disk artifacts, resolving `query` exactly as the detail view does
(ambiguous queries are **refused**, not guessed). It deletes only the files/dirs named
after the UUID:

- `projects/<encoded-cwd>/<uuid>.jsonl` and its `<uuid>/` sidecar dir
- `session-env/<uuid>/`
- `file-history/<uuid>/`

Centralized files (`history.jsonl`, `~/.claude.json`) are **left untouched** — the
orphaned references there are harmless, and this keeps `quarry rm` from ever rewriting
shared state. (A session you remove therefore lingers as a grey `-a` entry until Claude
next overwrites that directory's `lastSessionId`.)

**No argument** — offers the session Claude **last ran in the current directory**
(`~/.claude.json`'s `lastSessionId` for `$PWD`), so right after quitting a session you can
just type `quarry rm`. It considers *only* that one session — never falling back to older
ones — and declines cleanly if it's already removed or still running. `-f` is refused with
no argument (an inferred target must be confirmed interactively).

Safety:
- **Refuses to remove a session that is currently open** in a live `claude` process.
- Prompts before deleting (`rm -i` style). `-f` skips the prompt; `-n` previews the exact
  file list and total size and deletes nothing. Without a TTY and without `-f`, it refuses
  rather than delete unprompted.

```
$ quarry rm -n debug-flaky
Would remove session 8f3c1a92-4b7e-4c1d-9a2f-1e6d0b5c7a34  "Track down and fix the intermittent…"
    72K  ~/.claude/projects/-Users-you-code-scratch/8f3c1a92-….jsonl
      0  ~/.claude/session-env/8f3c1a92-…/
  2 item(s), 71.6 KiB (73,301 B)
```

## `quarry fzf`

Interactive picker. It's a thin launcher — `quarry ls --tsv` piped into `fzf` — so `fzf`
provides the whole UI and the keybindings decide what happens to the session you pick:

| key | action |
|-----|--------|
| `enter` | **resume** it (`cd <dir> && claude --resume <uuid>`) |
| `ctrl-x` | **delete** it (`quarry rm`), then refresh the list |
| `ctrl-y` | copy its UUID to the clipboard |
| `tab` | **cycle the search scope**: `names` → `prompts` → `replies` (looping) |

The header shows the active scope in **CAPS** so the current search breadth is always
visible. `enter` **refuses to resume a session that's currently open** in a live process
(checked at resume time) — it prints a message and bails rather than risk a second attach.
Deleting an open session via `ctrl-x` is likewise refused by `quarry rm`.

The preview pane is `quarry ls <uuid>` (the detail view), live as you scroll. Matching is
**exact-substring** (`fzf --exact`) — fuzzy subsequence matching is useless once the
searchable text includes whole transcripts (any short query's letters appear *somewhere*
in a 100k-token blob).

**Scope** (cycled with `tab`) controls how much text is searchable, via
`quarry ls --tsv --scope`:

- `names` — session title + AI title (cheap; the default)
- `prompts` — the above + everything you typed
- `replies` — the above + the agent's text responses

`prompts`/`replies` parse each log, so they're heavier than `names` — but only the picker
uses them, on demand.

> **Selecting text from the fzf screen** (e.g. to copy a path out of the preview): fzf
> captures the mouse, so use your terminal's bypass modifier — in **iTerm2**, hold
> **⌥ Option** and drag, then ⌘C.

`quarry ls --tsv` emits `UUID⇥label⇥dir⇥date⇥searchable` and is the machine-readable
counterpart to the normal listing; you can pipe it into your own tools too.

Resume is directory-scoped, so on `enter` the wrapper `cd`s into the session's launch
directory before `exec claude --resume`. The resume happens in the wrapper *after* fzf
exits (fzf prints the selection; the script execs Claude) rather than via fzf's `become` —
with `become` the resumed session renders but never receives keyboard input, because it
doesn't inherit the terminal on stdin. Removed sessions aren't listed (they can't be
resumed).

## Completions

`quarry completions {fish,zsh,bash}` prints a completion script to stdout;
`make install-completions` installs all three. Reliability differs by shell:

- **fish** — installed to `~/.config/fish/completions/quarry.fish`, which fish autoloads.
  Includes **dynamic** completion of session names/UUIDs (via `quarry ls --tsv`).
- **zsh / bash** — static completion of subcommands and flags. The Makefile installs the
  file and prints the one rc line to add (`fpath+=…` before `compinit`, or a `source …`).

## Size metrics

Size is pluggable via the `SIZE_METRICS` registry in `src/claude_sessions.py`; `--size`
and `-S` both read from it. Today the only enabled metric is `log` (log-file bytes on
disk), which is cheap — a `stat()` per file.

A metric declares a `unit` (`bytes`/`tokens`) and whether it's `expensive` (needs the log
parsed). Cheap metrics read a field off `Session`; expensive ones are filled by
`ensure_sizes()` before listing. A `context` metric (latest post-prompt context size) is
wired up and commented out in the registry — uncommenting it enables `--size context` for
the column and `-S` sorting. It's left off by default because, unlike `log`, it must parse
every log just to list.

## Layout

- `bin/quarry` — the entry point: a dispatcher that routes each subcommand to its own
  argument parser. Puts `src/` on `sys.path`.
- `bin/quarry-fzf` — thin shell launcher around `fzf` + `quarry ls`/`quarry rm`.
- `src/claude_sessions.py` — shared core: discovery, matching, log analysis, artifact
  listing, and formatting.
- `src/cmd_ls.py`, `src/cmd_rm.py` — the `ls` and `rm` command implementations.

## Data source

Everything comes from `$CLAUDE_CONFIG_DIR` (default `~/.claude`): `projects/*/*.jsonl` for
the logs, `sessions/<pid>.json` for liveness, and `~/.claude.json` for per-directory
`lastSessionId` pointers (used to surface removed sessions). These are undocumented Claude
Code internals (developed against CLI v2.1.x) and may change; the tool degrades gracefully
when files are missing or unparseable.

## License

BSD 2-Clause. See [LICENSE](LICENSE).
