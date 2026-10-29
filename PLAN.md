# clockout: one command to end the day

> Status: idea and plan only. Nothing is built yet.

## The problem

Compression burnout comes from AI tools squeezing more work into the same hours. Agents keep running, the
work never reaches a natural stopping point, and stopping feels risky: *"If I close this now I'll lose my
place / forget what I was doing / the agent will sit idle."* So people keep going.

## Who it's for

Developers who use Claude Code (and git) every day, especially people running several sessions or repos at
once who find it hard to stop at the end of the day.

## Which brief idea it fits

**"A shutdown command"**: one command that saves your open work, writes tomorrow's list and pauses your
agents until morning.

## What it does

Run `clockout` once at the end of the day. It then does three things, with no further questions:

1. **Saves your open work, safely.** It finds every repo you touched today (from Claude Code session logs plus
   the current folder). For each repo with uncommitted changes, it snapshots everything (including untracked
   files, respecting `.gitignore`) to a private git ref `refs/clockout/<date>`. It does this through a
   temporary index, so your working tree, staging area and branch are **not touched**. Nothing is lost and
   nothing is rewritten.
2. **Writes tomorrow's list.** It creates a short markdown note for the next working day (`~/clockout/<date>.md`)
   with, per project:
   - branch, files still changed, last commit message
   - the last few things you asked Claude in that project (from `~/.claude/projects/*/*.jsonl`)
   - `TODO`/`FIXME` lines added in today's diff
   - an optional line of your own: `clockout "finish the auth bug first"`

   Plus one quiet fact line, e.g. "Today: 9h 10m from first to last prompt, 4 projects". That's a mirror, not an alert.
3. **Pauses your agents until morning.** It writes a pause file with a resume time (next working day,
   08:00 by default, and Friday resumes on Monday). A Claude Code hook (installed once with `clockout install`) reads it:
   - `PreToolUse`: any agent, including background or headless ones, is blocked from running tools while
     paused (exit code 2, with the message "Clocked out until Mon 08:00").
   - `UserPromptSubmit`: a new prompt gets one line saying you're clocked out and how to override
     (`clockout resume`) if it's truly urgent.
   - `SessionStart` (morning): the first session after the pause ends gets tomorrow's list as context, so
     you and Claude pick up exactly where you left off without spending 20 minutes re-reading.

Other commands: `clockout status`, `clockout resume`, `clockout uninstall`.

## Why it gives time back (and doesn't add work)

- It's one command, with no forms or daily check-ins. It never sends notifications or nags.
- It takes away the main reason people don't stop (fear of losing context), and it makes "just one more agent
  run" impossible overnight.
- The morning recap replaces the re-orientation time people spend each morning. That saved time goes back to
  the evening, not into more tasks.

## Technical plan

| Piece | Approach |
|---|---|
| Language | Python 3.9+, standard library only (no dependencies), one file `clockout.py` + `pyproject.toml` for `pipx install` |
| Repo discovery | `cwd` field of today's entries in `~/.claude/projects/*/*.jsonl`, plus the current folder, plus optional `repos` in `~/.clockout/config.json` |
| Snapshot | `GIT_INDEX_FILE=tmp git add -A` → `git write-tree` → `git commit-tree -p HEAD` → `git update-ref refs/clockout/<date>` |
| Prompt history | Log entries with `type == "user"` whose content is plain text (skip `tool_result` blocks and `<command…>` wrappers) |
| Pause state | `~/.clockout/pause.json` `{ "until": ISO time }`; the hook exits 0 instantly when there's no pause file or it has expired |
| Hook install | Merge into `~/.claude/settings.json` (backup first), using absolute forward-slash paths to the Python interpreter and script so it works on Windows, macOS and Linux |
| Config | `~/.clockout/config.json`: resume hour, working days, extra repos, note folder |
| Tests | `python -m unittest`: snapshot leaves the working tree identical, log parsing, resume-time calculation (Fri→Mon), hook block/allow |

## Build order (for when we build it)

1. Pause file + `hook` + `install`/`uninstall` + `resume`/`status`. This is the core loop and can be tested on its own.
2. Safe git snapshot + tests.
3. Claude Code log parsing → tomorrow's list.
4. `SessionStart` morning recap.
5. README written for a stranger: install, first run, how to undo, what it touches.

## Judging checklist

- **Problem:** compression burnout; for Claude Code developers; brief idea "shutdown command".
- **Works:** real git refs, real hook blocking, real log parsing, with nothing mocked.
- **Usable:** one install line, zero config, fully reversible (`uninstall`, `resume`).
- **Time back:** no alerts or prompts added; it removes the morning catch-up and the overnight agent creep.
