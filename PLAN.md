# hearth: keep up with your AI tools without losing the reason you do the work

> Status: idea and plan only. Nothing is built yet. "hearth" is a working name.

## The problem

Compression burnout: AI tools squeeze more work into the same hours. Agents keep running, there's always another
release to keep up with, and the work gets more and more technical. Somewhere along the way you lose touch
with the *romantic* part: who the work is for, why you started it, and the bit you actually enjoy.

## Who it's for

People who build with Claude Code every day, run several sessions or repos at once, and feel the work has
become all mechanics and no meaning.

## Which brief ideas it combines

| Brief idea | Where it lives in hearth |
|---|---|
| A shutdown command | `/hearth:shutdown` + the pause hooks |
| A workload tracker | `/hearth:load` |
| Learning AI together | `/hearth:digest` |
| *(our own)* Staying in touch with the why | `/hearth:why`, `/hearth:plain` |

**The one rule, applied to every command:** each one replaces something you would otherwise do by hand. None of
them adds alerts, nags or daily check-ins. Every command is run on demand, and the only automatic behaviour is the overnight pause
and the morning recap.

## The suite

### `/hearth:shutdown` (end the day)
One command, no questions:
1. **Saves open work safely.** Every repo you touched today (found from Claude Code logs plus the current
   folder) with uncommitted changes gets snapshotted to a private ref `refs/hearth/<date>` through a
   temporary git index. Your working tree, staging area and branch are untouched.
2. **Writes tomorrow's list** to `~/hearth/<date>.md`. For each project it records the branch, files still
   changed, the last few things you asked Claude, and any new TODOs. Add your own line with
   `/hearth:shutdown finish the auth bug first`.
3. **Pauses agents until morning.** The next working day at 08:00 by default (Friday → Monday). While
   paused, the plugin's hooks block agent tool use and new prompts with one line: "Clocked out until
   Mon 08:00, `/hearth:resume` if it's truly urgent."

### `/hearth:morning` (start the day)
This runs automatically in the first session after the pause ends (`SessionStart` hook), or you can run it by hand. It
loads tomorrow's list into context so you and Claude start exactly where you stopped, with no 20 minutes of
re-reading.

### `/hearth:why` (the romantic part)
Reads today's commits and Claude Code prompts and retells the day in human terms: who it helps, what
changed for them, and what you made that you can be proud of. For example: *"You made it so a volunteer no longer
types the same address three times."* It's two or three sentences with no jargon. It's run when you want it and never
pushed at you.

### `/hearth:plain` (explain it to a person)
Turns the current change, branch or PR into one plain paragraph for someone non-technical (a partner,
manager or client). It saves the time of writing updates and keeps you practised at saying what the work is
*for*.

### `/hearth:load` (how much am I juggling?)
A quiet snapshot from real data: active sessions and projects today, hours from your first to last prompt,
your last real break (the longest gap of 30 minutes or more), and uncommitted repos. It's a mirror you open
yourself, not a dashboard that pings you.

### `/hearth:digest` (keep up without chasing)
Once a week, it picks **one** thing from the Claude Code changelog that's relevant to how *you* actually work
(matched against your own logs) and explains it in five lines. It replaces trying to read every release. The
output is a short card you can share with a group (the "learning AI together" idea).

### `/hearth:resume`, `/hearth:status`
Lift the pause early, or see whether you're paused and where today's snapshots are.

## How it's packaged: a plugin in this repo, plus a CLI

```
.claude-plugin/marketplace.json        # lets anyone install from this repo
plugins/hearth/
  .claude-plugin/plugin.json
  commands/      shutdown.md morning.md why.md plain.md load.md digest.md resume.md status.md
  hooks/hooks.json                     # PreToolUse + UserPromptSubmit (pause), SessionStart (morning)
  scripts/hearth.py                    # deterministic engine, also a standalone CLI
tests/
README.md
```

Install inside Claude Code:
```
/plugin marketplace add Lukallo/claude-impact-lab-10-26
/plugin install hearth
```
You can also use it with no Claude Code session at all: `python plugins/hearth/scripts/hearth.py shutdown`.

**The work is split in two:**
- **`hearth.py` does the deterministic work:** git snapshots, the pause file, log parsing and load numbers. It uses only the
  standard library and is fully testable, with nothing mocked.
- **Claude, through the command markdown files, does the human-language work:** the why, the plain paragraph, the
  digest choice and phrasing the list. Each command calls `hearth.py` for real data first, so the writing is
  grounded in what actually happened.

## Technical notes

| Piece | Approach |
|---|---|
| Repo discovery | `cwd` of today's entries in `~/.claude/projects/*/*.jsonl` + the current folder + `repos` in config |
| Snapshot | `GIT_INDEX_FILE=tmp git add -A` → `write-tree` → `commit-tree -p HEAD` → `update-ref refs/hearth/<date>` |
| Prompt history | `type == "user"` entries with plain-text content (skip `tool_result` blocks and `<command…>` wrappers) |
| Pause | `~/.hearth/pause.json` `{until}`; the hook exits 0 immediately when there's no pause, so there's zero cost on normal days |
| Hook paths | `${CLAUDE_PLUGIN_ROOT}`, so nothing is written to the user's `settings.json` |
| Python on Windows vs macOS/Linux | A tiny launcher that tries `python3` then `python` |
| Config | `~/.hearth/config.json`: resume hour, working days, extra repos, notes folder |
| Tests | Snapshot leaves the tree byte-identical, log parsing, Fri→Mon resume, hook block/allow, load numbers |

## Build order (when we build)

1. Plugin skeleton + marketplace file, so it can be installed.
2. `hearth.py`: pause / resume / status + hooks (the core loop).
3. Safe snapshot + tests.
4. Log parsing → `shutdown` list, `load`, `morning`.
5. Language commands: `why`, `plain`, `digest`.
6. README for a stranger: install, first run, what it touches, how to undo.

## Judging checklist

- **Problem (30):** compression burnout and losing the meaning of the work; clear audience; combines three brief
  ideas plus one of our own.
- **Works (30):** real git refs, real hook blocking, real log data; every command works end to end.
- **Usable (20):** two-line install, zero config, everything reversible.
- **Time back (20):** on-demand only, no alerts; removes the morning catch-up, the overnight agent creep and
  release-chasing.
