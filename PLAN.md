# hearth: keep up with your AI tools without losing the reason you do the work

> Status: plan only. Nothing is built yet. "hearth" is a working name.

## The problem

Compression burnout: AI tools squeeze more work into the same hours. Agents keep running, there's always another
release to keep up with, and the work gets so technical you lose touch with the *romantic* part: who it's
for and why you started.

## Who it's for

People who build with Claude Code every day, run several sessions or repos at once, and find it hard to stop.

## Brief ideas it covers

**Shutdown command** (the core), plus **workload tracker** and **learning AI together** as supporting
pieces, and one of our own: **staying in touch with what the work is for**.

---

## The suite: 5 commands, 2 automatic behaviours

| | What it does | Replaces (the time it gives back) |
|---|---|---|
| `/hearth:shutdown [note]` | Saves open work safely, writes tomorrow's list, pauses agents until morning | Manual stashing/WIP commits, "where was I?" notes, agents running overnight |
| `/hearth:resume` | Lifts the pause early | n/a (the escape hatch) |
| `/hearth:status` | Shows whether you're paused, plus today's load: projects, hours from first to last prompt, last real break, unsaved repos | Guessing how much you've taken on |
| `/hearth:plain [target]` | One plain paragraph: who this work helps and what changed for them. Use it for yourself or to paste into an update for a non-technical person | Writing status updates by hand; losing the "why" |
| `/hearth:digest` | Shows only what changed in Claude Code since your version or the last digest, then picks the **one** thing relevant to how you work, in five lines | Reading every release note to keep up |
| *auto* pause | While paused, a `PreToolUse` hook blocks agent tool use: "Clocked out until Mon 08:00, `/hearth:resume` if urgent" | Overnight agent creep |
| *auto* morning | The first session after the pause ends gets yesterday's list as context, **once** | 20 minutes of re-orientation each morning |

### Audit: does each piece strictly help, and could it hurt?

| Piece | Helps | Could it hurt? | Decision |
|---|---|---|---|
| shutdown | Core of the brief; removes the fear of stopping | Snapshot could touch files, so it uses a temp git index: tree, index and branch stay **byte-identical** (tested) | **Keep** (the centre of the project) |
| pause hook | Agents actually stop | Blocking *your* prompts would add a prompt, so we **only block agent tools**. You can still type, and Claude just can't act | **Keep**, agent tools only |
| morning recap | Saves re-orientation time | Injecting context every session would be noise, so it fires **once**, after a pause | **Keep**, once only |
| "what today was for" | Restores the romantic part | As a separate command it's one more thing to read, so it **merges into tomorrow's list** as its first line | **Merged** into shutdown |
| plain | Replaces writing updates; keeps the why | Could be fluff, so it's grounded in real commits and prompts from `hearth.py` | **Keep** |
| load tracker | Lets you see your workload | As a dashboard or alert it would add attention cost, so it lives inside `status` and is **only shown when asked** | **Merged** into status |
| digest | Keeping up without chasing | Network-dependent and could push *more* consumption, so it shows **one** item only, degrades cleanly offline, and shows nothing when there's nothing new | **Keep**, tightly scoped |
| separate `morning` / `why` / `load` commands | n/a | Sprawl | **Cut** |
| notifications, reminders, streaks, scores | n/a | Adds alerts and pressure, which breaks the one rule | **Never** |

---

## How it's packaged: a Claude Code plugin in this repo, plus a CLI

```
.claude-plugin/marketplace.json
plugins/hearth/
  .claude-plugin/plugin.json
  commands/   shutdown.md resume.md status.md plain.md digest.md
  hooks/hooks.json            # PreToolUse (pause), SessionStart (morning, once)
  scripts/hearth.py           # deterministic engine + standalone CLI, stdlib only
tests/                        # python -m unittest
.github/workflows/test.yml    # tests on Windows, macOS and Linux
README.md
```

Install inside Claude Code:
```
/plugin marketplace add Lukallo/claude-impact-lab-10-26
/plugin install hearth@hearth
```
Without Claude Code: `python plugins/hearth/scripts/hearth.py shutdown`.

- **`hearth.py`** handles the facts: snapshots, pause state, log parsing, load numbers and changelog diffing. It's testable with nothing mocked.
- **The command `.md` files** have Claude do the language: phrasing the list, `plain` and the digest pick. Each one calls `hearth.py` for real data first.

## Technical notes

| Piece | Approach |
|---|---|
| Repo discovery | `cwd` of today's entries in `~/.claude/projects/*/*.jsonl` + current folder + `repos` in config |
| Snapshot | `GIT_INDEX_FILE=tmp git add -A` → `write-tree` → `commit-tree -p HEAD` → `update-ref refs/hearth/<date>`; restore with `git checkout refs/hearth/<date> -- .` |
| Prompts | `type == "user"` entries with plain-text content; skip `tool_result` blocks and `<command…>` wrappers |
| Pause | `~/.hearth/pause.json` `{until}`; the hook exits 0 immediately when there's no pause, so it adds no cost on normal days |
| Paths | `${CLAUDE_PLUGIN_ROOT}`, so the user's `settings.json` is never edited |
| Python | A launcher that tries `python3` then `python` (Windows only has `python`) |
| Digest | Claude Code `CHANGELOG.md` from GitHub, compared against `claude --version` and `~/.hearth/digest.json`; offline means "couldn't check, nothing to do" |
| Config | Optional `~/.hearth/config.json`: resume hour, working days, extra repos |

## Making it easy for a reviewer (human or AI) to verify

These are plain facts, not persuasion. Each one maps to a judging line:

- **README, first screen:** who it's for → the problem → which brief idea → install in two lines → what you'll see.
- **Real sample output** in the README, captured from an actual run and labelled as such.
- **`hearth.py selftest`**: builds a throwaway repo, runs shutdown → checks the snapshot → checks that the hook blocks → resumes. It proves the main flow end to end in one command.
- **Tests + CI badge** on three operating systems.
- **"What it touches and how to undo it"** and **"What it deliberately doesn't do"** (no alerts, no streaks), which speak directly to the time-back criterion.
- **Honest limitations** (e.g. digest needs network; the pause only blocks agents started through Claude Code).

## Build order

1. Plugin skeleton + marketplace, so it can be installed.
2. `hearth.py` pause / resume / status + `PreToolUse` hook.
3. Safe snapshot + tests.
4. Log parsing → shutdown list (with the "what today was for" line), load numbers, morning recap.
5. `plain`, then `digest`.
6. `selftest`, CI, README with real output.
