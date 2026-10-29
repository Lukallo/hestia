# AGENTS.md

Instructions for any coding agent (Claude Code, Codex, Cursor, Copilot and the
rest) working in this repository. Read it fully before changing anything. Where
it disagrees with a guess you would otherwise make, it wins.

## 1. What this is

Hestia is a Claude Code plugin that ends the working day. `/hestia:done` saves
open work without touching the repo, writes tomorrow's list, stops unattended
agents and pauses Claude until the next working morning. The next morning's
first session is handed that list exactly once.

Public repo: `github.com/Lukallo/hestia`. Local checkouts may still be in a
folder named `claude-impact-lab-10-26`, the repo's old name.

### The one rule

**Everything here must give people time back. Nothing may help them do more.**
It beats every other consideration, including features a user asks an agent
for in passing. In practice:

- No notifications, reminders, nudges, dashboards, streaks, scores or goals.
- Nothing runs unless the user typed it, except the three hooks, which stay
  silent unless there is something they must do.
- Every command answers briefly and stops. `done` uses three lines at most,
  `break` two, `new` five, and `why` one paragraph.
- Command prompts tell Claude not to summarise, suggest or offer more work.

If a change would add something to read, check or respond to, it needs a
reason that survives this rule. Write that reason in the PR.

### The mechanism

Stopping is only possible if picking back up is guaranteed. So the morning
handover is not optional polish: it is the half that makes shutting down safe.
Treat any change that could lose, duplicate or delay the handover as a
correctness bug.

## 2. Repository map

| Path | What it is |
|---|---|
| `plugins/hestia/scripts/hestia.py` | The whole engine and a standalone CLI. Standard library only. |
| `plugins/hestia/scripts/hestia` | The `sh` launcher the hooks and commands call. It runs `hestia.py` with `python3`, or with `python` when `python3` is missing or is the Windows Store placeholder. |
| `plugins/hestia/commands/*.md` | The eight slash commands. Each one runs `hestia.py` through the launcher first, then has Claude phrase the result. |
| `plugins/hestia/hooks/hooks.json` | `PreToolUse` runs the pause. `SessionStart` (startup and resume) and `UserPromptSubmit` run the morning handover. All of them go through the launcher. |
| `plugins/hestia/.claude-plugin/plugin.json` | Plugin manifest. |
| `.claude-plugin/marketplace.json` | Marketplace manifest, so `/plugin marketplace add Lukallo/hestia` works. |
| `tests/test_hestia.py` | Unit tests. Real git repos in temp dirs, nothing mocked. The one stand-in is a fake Windows `python3` placeholder for the launcher tests. |
| `tests/test_platforms.py` | What differs between Linux, macOS and Windows: text encodings, paths with spaces, the restore line in each shell, and the hooks as installed. |
| `.github/workflows/test.yml` | CI: the selftest and unit tests on Linux, macOS and Windows (PowerShell), plus the selftest through the launcher in bash, which is Git Bash on Windows. |
| `tests/compat/` | A live check that runs real `claude -p` sessions with Hestia, a stand-in connector (`fake_mcp.py`) and a stand-in plugin (`other/`). Not one of the three checks: it needs the `claude` CLI and costs a few cents. |
| `README.md` | The public face, read by users and by an automated judge. Every output block in it is a real capture. |
| `PLAN.md` | The design, the burnout research grounding, and the audit of what was cut and why. |
| `assets/`, `assets/BRAND.md` | Banner, logo and icons, which are named by fire stage rather than by command. |
| `REFERENCE.md` | Projects the design learned from. |
| `CLAUDE.md` | A maintainer's local notes. It is untracked on purpose. Never commit it. |

## 3. Architecture

### The engine and the prompts are split on purpose

- **`hestia.py` handles facts.** It covers snapshots, pause state, log
  parsing, load numbers, git history and changelog diffing. It is
  deterministic and testable with nothing mocked.
- **The command `.md` files handle language.** That means phrasing the list,
  writing the `why` paragraph and picking the one `new` item. They always call
  `hestia.py` first and are told to use only what it returned.

Never move a fact into a prompt, so Claude doesn't guess at git state. Never
move language into the engine either, so Python doesn't write prose about
someone's work.

### Commands

| Slash command | Engine call | Writes | Notes |
|---|---|---|---|
| `/hestia:done [note]` | `dream plan`, `dream check`, then `done --note ... --next ...` | `backups/memory/<ts>/`, tidied auto-memory files, snapshot refs, `TOMORROW.md`, `pause.json` (kind `done`), `handover.json` (re-armed) | Tidies memory first, because the pause blocks tools. SIGTERMs headless agents unless `--keep-agents` |
| `/hestia:break [45m]` | `break [length]` | snapshot refs, `pause.json` (kind `break`) | Writes no list and never touches `handover.json`. Signals nothing unless `--stop-agents` |
| `/hestia:back` | `back` | deletes `pause.json` | After a break it prints restore lines; after `done` it points to the list |
| `/hestia:today` | `today` | nothing | Only ever shown when asked |
| `/hestia:init [remove]` | `init detect`, `init write`, `init remove`, `connector` | `profile.json`, `config.json`, `connectors.json`, `backups/`, and a marked section of `~/.claude/CLAUDE.md` | Run once. Never touches text outside its markers. `connector NAME` is the consent gate: exit 0 allowed, 10 ask once, 1 skip silently |
| `/hestia:dashboard` | `dashboard` | `dashboard.html` only, and nothing when `screen` is `false` | The `today` reading as one static page: no scripts, no network, no refresh. Allowed while paused because it only reads. Off when `init` was told there's no screen. With no answer, it only opens a browser if `has_screen()` finds a display |
| `/hestia:why [who for]` | `why --json` | nothing | Facts only; Claude writes the paragraph |
| `/hestia:new` | `new --json` | `digest.json` | The only network use; offline means "nothing to do" |

The CLI also accepts `shutdown`, `resume` and `status` as argparse aliases of
`done`, `back` and `today`. Keep them, because old setups call them. Internal
subcommands are `hook`, `session-start`, `prompt-submit` and `selftest`.
`handover` reports whether the morning list is still due, and `handover --again`
re-arms it.

### State files

Everything lives in `~/.hestia/`, or in `$HESTIA_HOME` when that is set.

| File | Shape | Lifecycle |
|---|---|---|
| `pause.json` | `{kind: "done" or "break", until, since, ...}`. A `done` pause also has `note` and `tomorrow`; a `break` has `saved: [{repo, ref, restore}]` | Deleted by `back`, or on the first read after `until` has passed. A file with no `kind` is treated as `done` |
| `TOMORROW.md` | Markdown; the note is the first line starting with `> ` | Rewritten by each `done` |
| `handover.json` | `{for: <TOMORROW.md mtime_ns>, consumed, since, back_at?, at?, via?, session?}` | Re-armed by `done`. `since` is the shutdown time and `back_at` an early `back`, which bound the overnight fact. A file whose `for` is a float mtime with `consumed: true` comes from before claims and still counts as handed over |
| `handover-<mtime_ns>.taken` | Empty claim file | Created with `O_CREAT \| O_EXCL` by the one session that takes the handover. `done` and `handover --again` delete them. This file, not `handover.json`, is what makes the handover happen exactly once |
| `digest.json` | `{seen: <newest version>, checked_at}` | Written only after a successful changelog fetch |
| `dashboard.html` | A static page, rewritten on each `dashboard` run | Delete it any time |
| `profile.json`, `connectors.json` | The answers and connector consent from `init` | Rewritten by `init`; `init remove` takes the CLAUDE.md section out |
| `config.json` | `{resume_hour, working_days (Mon=0), repos, screen}`. `screen` is whether this computer has a display (`true`, `false` or absent). It has nothing to do with headless agents or sessions | Optional. `init` writes `resume_hour`, `working_days` and `screen`; only the user sets `repos` |

Snapshots live in each repo as `refs/hestia/<YYYY-MM-DD-HHMMSS>`. They are
plain commits whose parent is `HEAD`. They are never on a branch.

### Data sources

- **Prompts** come from `~/.claude/projects/*/*.jsonl`, or `$HESTIA_LOG_DIR`.
  When Claude Code runs with `CLAUDE_CONFIG_DIR`, the logs, auto memory,
  `CLAUDE.md`, `settings.json` and `.claude.json` all live in that folder
  instead, and Hestia follows it.
  The `HESTIA_*` overrides still win.
  Only `type == "user"` entries with string content count. Sidechains,
  tool results and anything starting with `<` are skipped.
- **Repos** are found from the `cwd` of today's prompts, the current folder
  and `repos` in the config.
- **Agents** are found with `ps -eo pid=,ppid=,command=`. Hestia's own
  process and its ancestors are always excluded.
- **The changelog** is read from Claude Code's public `CHANGELOG.md` on
  GitHub, or `$HESTIA_CHANGELOG_URL`. A `file://` URL works there.

### Hooks must cost nothing on a normal day

The hooks run on every tool call, session start and prompt for every user.
With no pause and nothing to hand over, they must return immediately with no
output. `handover_due()` is the gate for both handover hooks: on a normal day
it costs one or two `stat` calls after reading the hook's input. Always read
the input first, so Claude Code never writes into a closed pipe.
Don't add work before the "is there anything to do?" check. Don't import
anything slow at module level. Keep the 5-second timeouts in `hooks.json`.

## 4. Invariants

Each of these has a test or selftest check. If one fails, fix the code, never
the test.

1. **Snapshots never touch the repo.** The working tree, `.git/index` and
   `HEAD` stay byte-identical. The engine builds the commit through a
   temporary `GIT_INDEX_FILE`. **Never** replace this with `git stash`,
   `git add` on the real index, or a commit on the user's branch.
2. **The pause never blocks the way back in, and nothing rides along with
   it.** `is_way_back_in()` only lets a `Bash` or `PowerShell` command
   through when it is one hestia call on its own: `SHELL_JOINERS` refuses
   chaining, pipes, redirects, subshells and new lines. `RESUME_PATTERN` in
   `hestia.py` allowlists `back`/`resume`, `today`/`status` and the read-only
   `dashboard`, along with the hook commands. It applies to commands run
   through the Bash tool and, on Windows, the PowerShell tool. The hook tests
   cover several spellings, including the old aliases and the launcher. If
   you rename a command, update the pattern and those tests in the same
   change.
3. **The handover happens exactly once per list, never while paused, and
   never to a scripted run.** The claim is an `O_EXCL` file, so a race between
   sessions has one winner; the selftest races eight and a test races twelve.
   It reaches every way a morning starts: a new or resumed session
   (`SessionStart`), a window left open (`UserPromptSubmit`), or `/hestia:back`,
   which takes it itself. A headless session (`CLAUDE_CODE_SESSION_ATTENDED=0`
   or `CLAUDE_CODE_ENTRYPOINT=sdk-*`) must never take it. A new `done` re-arms
   it. A break never arms or consumes it. Tests set `HESTIA_SESSION_KIND` so
   they behave the same in a terminal, CI or `claude -p`.
4. **A break can't shorten the night.** `break` refuses while a `done` pause
   is active, and refuses lengths over four hours.
5. **Expired pauses clear themselves.** No pause, of either kind, needs
   `back` to end.
6. **Nothing signals real processes in tests.** `done` is always run with
   `--keep-agents` in tests and the selftest. `break` is never run with
   `--stop-agents` there.
7. **`why` never reports Hestia's own snapshots or other people's commits.**
   It reads `--branches`, not `--all`. It filters on the identity from
   `git var GIT_AUTHOR_IDENT`, not on `user.email` alone.
8. **`new` is bounded and offline-safe.** It shows at most `NEW_MAX_RELEASES`
   releases and `NEW_MAX_CHANGES` changes, each release keeps its real
   `total_changes` count, and a failed fetch records nothing and exits 0.
9. **Command names agree** across `PLAN.md`, `README.md` and
   `plugins/hestia/commands/`. They have drifted apart twice before.

## 5. Safety rules for agents

These protect the person whose machine you are running on.

- **Never run a real `/hestia:done` or `/hestia:break`,** or the CLI without
  `HESTIA_HOME` set, while working on this repo. It pauses every Claude
  session on the machine, including yours, and `done` lasts until 8am.
- To try a command by hand, isolate it:

  ```bash
  export HESTIA_HOME="$(mktemp -d)" HESTIA_LOG_DIR="$(mktemp -d)"
  python3 plugins/hestia/scripts/hestia.py done --until "$(date -v+5M +%H:%M 2>/dev/null || date -d '+5 min' +%H:%M)" --keep-agents
  python3 plugins/hestia/scripts/hestia.py break 2m
  python3 plugins/hestia/scripts/hestia.py back
  ```

- **If you do lock a session out,** delete `~/.hestia/pause.json`. That is
  the documented undo, and `back` does the same.
- **Never pass `--stop-all` or `--stop-agents`** outside a disposable VM. They
  send `SIGTERM` to real Claude processes.
- **Never add a network call** outside `new`, and never send anything
  anywhere. `why` writes a paragraph; it does not post it.
- **Never edit the user's `settings.json`.** Paths go through
  `${CLAUDE_PLUGIN_ROOT}`. Outside `~/.hestia`, Hestia writes only two things:
  `~/.claude/CLAUDE.md`, between its markers, during `init`; and the auto-memory
  folders (`~/.claude/projects/<project>/memory/`) of projects touched today,
  during `done`, always after `dream plan` has backed them up. `dream check`
  rolls back any folder with broken frontmatter, a dangling index link or a new
  file: Hestia never adds a memory. Tests redirect these with
  `HESTIA_CLAUDE_DIR`, `HESTIA_CLAUDE_JSON` and `HESTIA_MEMORY_ROOT`.

## 6. Checks

Run all three before you claim anything works:

```bash
python3 plugins/hestia/scripts/hestia.py selftest   # prints N/N checks passed
python3 -m unittest discover -s tests               # prints Ran N tests ... OK
claude plugin validate .                            # Validation passed
```

`claude plugin validate plugins/hestia` additionally runs the plugin
directory's submission checks. It currently reports a missing licence and a
missing icon. Both are known, and choosing a licence is the maintainers'
decision, not an agent's.

When you change the hooks or the pause, also run `tests/compat/run.sh`. It
checks that the pause holds next to a connector and another plugin, using a
throwaway `HESTIA_HOME` for every scenario. Never run it in
`bypassPermissions` mode, and re-capture its README block if its output
changes.

The tests need `git` and Python 3.9 or newer. They create everything in temp
dirs and set `HESTIA_HOME` and `HESTIA_LOG_DIR` for every test. They also need a
git identity, because snapshots are real commits; CI sets one.

CI runs the same checks, minus `claude plugin validate`, on Linux, macOS and
Windows. On Windows they run from PowerShell, and the launcher step from Git
Bash.

## 7. How to make common changes

### Change what a command does

1. Change the engine in `hestia.py`.
2. Add or update tests in `tests/test_hestia.py`, using real repos via
   `TempHome.make_repo()`.
3. If the change is user-visible, add a check to `cmd_selftest`.
4. Update the command's `.md` file if what Claude should do with the output
   changed.
5. **Re-capture** every README block the change affects (see section 8).
6. Update the check and test counts in the README heading and body.

### Ship it to people who already installed

Claude Code only updates an installed plugin when its version number
changes. If anything under `plugins/hestia/` changes and the version stays
the same, `claude plugin update hestia@hestia` says "already at the latest
version" and keeps the old hooks and scripts.

So bump `version` in `plugins/hestia/.claude-plugin/plugin.json` and
`metadata.version` in `.claude-plugin/marketplace.json` together, in the PR
that changes the plugin, or in one PR just before a release. A fresh install
always gets the latest files whatever the number says. The bump is for
everyone else.

### Add a command

Do all of these in the same change, or names drift:

- an argparse subcommand in `main()` and a `cmd_<name>` function;
- `plugins/hestia/commands/<name>.md`, with frontmatter `description`,
  `argument-hint` if it takes arguments, and `allowed-tools: Bash`. It runs
  `sh "${CLAUDE_PLUGIN_ROOT}/scripts/hestia" <name>`, never `python3` directly;
- a row in the README command table, with the icon `assets/BRAND.md`
  assigns to that stage;
- the `PLAN.md` suite table, if it isn't there already;
- the usage block in `hestia.py`'s module docstring;
- tests and at least one selftest check;
- a decision on whether `RESUME_PATTERN` must allow it while paused. The
  default is no. Only the ways back in, and read-only status, belong there.

First, justify it against the one rule. `PLAN.md` has an audit table that
does exactly this; add a row.

### Add or change a hook

- Exit immediately with no output when there is nothing to do.
- Return the documented JSON shape for that event, under `hookSpecificOutput`.
- Never block `/hestia:` commands.
- Start it as `sh "${CLAUDE_PLUGIN_ROOT}/scripts/hestia" <subcommand>`, so it
  finds a working Python on Windows too.
- Add a row to the README's table of things that happen on their own, and
  change the hook count in the heading above the command table.

## 8. README rules: nothing faked

The README is scored by an agent on whether the project really works. One
fabricated line costs more than a missing feature.

- **Every output block is a real capture.** Run the command and paste what it
  printed.
- **Never write sample output by hand,** and never "fix" a captured block.
- **Never stitch output from two runs into one block.** If blocks come from
  different runs, say so in the text, as the README's second capture section
  does.
- The only edit allowed is shortening temp paths, such as `~/.hestia` and
  `/path/to/demo`. Say that you did it, next to the block.
- When behaviour changes, re-capture every block it affects. Re-capture the
  selftest output and the `Ran N tests` line whenever checks or tests change.

Capture recipe, from the repo root:

```bash
S="$(mktemp -d)"; mkdir -p "$S/demo" "$S/home" "$S/logs"
export HESTIA_HOME="$S/home" HESTIA_LOG_DIR="$S/logs"
H="$PWD/plugins/hestia/scripts/hestia.py"
cd "$S/demo" && git init -q -b main   # then make the commits and edits the README describes
python3 "$H" break 45m
python3 "$H" back
```

## 9. Writing style

For the README, PLAN.md, command prompts and commit messages:

- Plain words, short sentences, one idea each.
- No em-dashes or en-dashes in new text. Use a full stop, a comma or a colon.
- No marketing language. State what the thing does and what it doesn't.
- Every claim in prose must be something the code does today.
- Honest limitations go in "Honest limitations", not in footnotes.

For Python:

- Standard library only. No third-party imports, ever: users install nothing
  but the plugin.
- Python 3.9 compatible: no `match` statements and no `X | Y` type unions.
  Follow the file's existing `%`-formatting style.
- Every subprocess call captures its own output. Catch `OSError` where a
  binary might be missing.
- Paths go through `pathlib`. Timestamps are timezone-aware local time
  (`datetime.now().astimezone()`).

## 10. Git and PRs

- Work on a branch with a descriptive kebab-case name, such as
  `add-break-why-new-commands`. Never commit straight to `main`.
- Don't push a branch that still tracks `origin/main`. Rename it and set its
  own upstream first.
- One concern per PR. The description should say what changed, which checks
  ran, and that README blocks were re-captured if they changed.
- Never commit `CLAUDE.md`, `__pycache__/` or anything from `~/.hestia`.
- Never use `git stash` in engine code (see invariant 1).

## 11. Known gaps

These are known and documented. Don't half-fix them in an unrelated change.

| Gap | State |
|---|---|
| Windows | With Git for Windows, the hooks run in Git Bash and the pause works. Without it, Claude Code runs hooks in PowerShell, where the `sh` launcher can't start, so the pause isn't enforced; Hestia needs git anyway. Agent stopping uses `ps` and `SIGTERM`, which are POSIX only, so nothing is signalled |
| Plugin icon | Missing. The directory wants a square PNG, 512 to 2048 px. SVG is not accepted, so `assets/logo.svg` needs rendering |
| Licence | Missing. The maintainers must choose one |
| PLAN pieces not built | The lunch break, a recap after a midday break, the finish line, catch-up and park. Each needs a new always-on hook; see the README's Scope section |
| Remote agents | Only processes on this machine can be stopped |
| Mods | A mod can approve a tool call the pause blocked, unless managed settings or a Team or Enterprise login is in place. Ordinary plugins' hooks can't, because Claude Code ranks a deny above any allow. Documented in the README's limitations |

## 12. Troubleshooting

| Symptom | Cause and fix |
|---|---|
| Every tool call is denied with "Clocked out until..." | A real `done` pause. Run `/hestia:back`, or delete `~/.hestia/pause.json` |
| Denied with "On a break until..." | A real break. It lifts itself at that time, or `/hestia:back` ends it |
| The morning list didn't appear | Run `hestia.py handover`. It says whether the list is still due, waiting for the pause, or already handed over and when. `handover.json` records which session got it (`via`, `session`, `cwd`), which may have been another window. `handover --again` hands it over once more |
| A scripted run got the list | The hook couldn't tell it was headless: its Claude Code passed neither `CLAUDE_CODE_SESSION_ATTENDED` nor `CLAUDE_CODE_ENTRYPOINT`, and its process flags had no `--print`. Set `HESTIA_SESSION_KIND=headless` in that job's environment |
| `why` shows no commits for a repo with commits today | They were authored under a different identity from the one `git var GIT_AUTHOR_IDENT` reports there |
| `new` always says nothing changed | `digest.json` already records the newest version. Delete it, or pass `--since` |
| A hook error says "hestia: needs Python 3" | The launcher found no `python3` that runs and no `python`. Install Python 3, or put it on `PATH` |
| `dashboard` says there's no dashboard on this computer | `config.json` has `"screen": false`, from `init`. Re-run `init`, or delete that key |
| The selftest fails only on `why` | The commit identity differs from the git identity. The selftest sets `GIT_AUTHOR_EMAIL`; check nothing in your environment overrides it |
