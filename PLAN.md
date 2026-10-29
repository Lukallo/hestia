# Hestia: keep up with your AI tools without losing the reason you do the work

> Status: plan only. Nothing is built yet.

## Why "Hestia"

Hestia is the Greek goddess of the hearth. Her one job was to **keep the fire going**: every home and city kept
her flame alight, and it was never allowed to go out. That's the opposite of burnout. A fire that burns too hot
burns out; a fire that's tended, banked at night and relit in the morning lasts.

That's what this tool does. It doesn't help you burn brighter. It tends the fire: it banks it at the end of the day
(`/hestia:done`), keeps it safe overnight (agents paused), and relights it in the morning (yesterday's list,
ready). It also keeps you close to the warm part of the work, who it's for and why you started (`/hestia:why`).

## The problem

Compression burnout: AI tools squeeze more work into the same hours. Agents keep running, there's always another
release to keep up with, and the work gets so technical you lose touch with the *romantic* part: who it's
for and why you started.

### What burnout is, and which part each piece of Hestia answers

The WHO (ICD-11) describes burnout as the result of **chronic workplace stress that hasn't been successfully managed**. It's a work problem, not a medical diagnosis, and it has three parts: **exhaustion**, **cynicism** (pulling away from the work) and **feeling ineffective**. A hard day you sleep off is fatigue. Burnout is when recovery stops keeping up, for weeks or months.

Two research models explain most of it:
- **Demands vs. recovery:** effort is fine if you recover from it. Recovery needs breaks during the day and *detaching* in the evening: not doing work, and not thinking about it either.
- **Six mismatches (Maslach):** burnout grows when the job is off on any of workload, control, reward, community, fairness or values. Workload is only one of the six.

The root change with AI is that **starting and finishing a task became nearly free**. This is how that maps onto the causes, and which piece answers each one. The mapping is our reasoning, not settled research.

| What AI compression does | Cause | Part of burnout | Hestia piece |
|---|---|---|---|
| The waits disappear (builds, searches, first drafts), and so do the breaks they gave for free | Recovery | Exhaustion | Catch-up in `today`: step away during a long run instead of watching it |
| The day has no natural end: stopping becomes a choice against a next task that costs nothing | Recovery (detaching) | Exhaustion | `done`, the pause, the finish line |
| What's left is judgment, with constant switching between sessions | Workload | Exhaustion | `today`, morning recap |
| New Claude Code features every week | Workload | Exhaustion | `new`, one item only |
| Being twice as fast becomes the new normal, and the gain is never cashed | Reward, fairness | Cynicism | Finish line and overnight fact make it visible. A tool can't fix this alone |
| You ask Claude instead of a colleague | Community | Cynicism | Mostly not covered. `why` makes it easy to send a person an update |
| The craft part got automated; you review output you didn't make | Values | Cynicism, feeling ineffective | `why`, "what today was for" |
| The bar keeps moving, so you never feel done | Reward | Feeling ineffective | Finish line ("3 of 5 done") |

What we can honestly claim tonight: not that burnout goes down, which takes weeks to show, but that the early signs change. Agents stop overnight, the day has an end, and nothing ran between shutdown and morning.

## Who it's for

People who build with Claude Code every day, run several sessions or repos at once, and find it hard to stop.

## Brief ideas it covers

**Shutdown command** (the core), plus **workload tracker** and **learning AI together** as supporting
pieces, and one of our own: **staying in touch with what the work is for**.

---

## The suite: 5 commands, 2 automatic behaviours

| | What it does | Replaces (the time it gives back) |
|---|---|---|
| `/hestia:done [note]` | Saves open work safely, writes tomorrow's list, pauses agents until morning | Manual stashing/WIP commits, "where was I?" notes, agents running overnight |
| `/hestia:back` | Lifts the pause early | n/a (the escape hatch) |
| `/hestia:today` | Shows whether you're paused, plus today's load: projects, hours from first to last prompt, last real break, unsaved repos | Guessing how much you've taken on |
| `/hestia:why [target]` | One plain paragraph: who this work helps and what changed for them. Use it for yourself or to paste into an update for a non-technical person | Writing status updates by hand; losing the "why" |
| `/hestia:new` | Shows only what changed in Claude Code since your version or the last check, then picks the **one** thing relevant to how you work, in five lines | Reading every release note to keep up |
| *auto* pause | While paused, a `PreToolUse` hook blocks agent tool use: "Clocked out until Mon 08:00, `/hestia:back` if urgent" | Overnight agent creep |
| *auto* morning | The first session after the pause ends gets yesterday's list as context, **once** | 20 minutes of re-orientation each morning |

### Audit: does each piece strictly help, and could it hurt?

| Piece | Helps | Could it hurt? | Decision |
|---|---|---|---|
| `done` | Core of the brief; removes the fear of stopping | Snapshot could touch files, so it uses a temp git index: tree, index and branch stay **byte-identical** (tested) | **Keep** (the centre of the project) |
| pause hook | Agents actually stop | Blocking *your* prompts would add a prompt, so we **only block agent tools**. You can still type, and Claude just can't act | **Keep**, agent tools only |
| morning recap | Saves re-orientation time | Injecting context every session would be noise, so it fires **once**, after a pause | **Keep**, once only |
| "what today was for" | Restores the romantic part | As a separate command it's one more thing to read, so it **merges into tomorrow's list** as its first line | **Merged** into `done` |
| `why` | Replaces writing updates; keeps the why | Could be fluff, so it's grounded in real commits and prompts from `hestia.py` | **Keep** |
| load tracker | Lets you see your workload | As a dashboard or alert it would add attention cost, so it lives inside `today` and is **only shown when asked** | **Merged** into `today` |
| `new` | Keeping up without chasing | Network-dependent and could push *more* consumption, so it shows **one** item only, degrades cleanly offline, and shows nothing when there's nothing new | **Keep**, tightly scoped |
| separate `morning` / `load` / "what today was for" commands | n/a | Sprawl | **Cut** |
| notifications, reminders, streaks, scores | n/a | Adds alerts and pressure, which breaks the one rule | **Never** |

### Additions, through the same audit

No new commands: each addition lives inside a piece above. Nothing above changes. Where an addition would change a decision above, it's marked **Proposed** and needs agreement first.

| Piece | Plugs into | Helps | Could it hurt? | Decision |
|---|---|---|---|---|
| overnight fact: "Shut down 17:30, next agent activity 09:05, nothing ran overnight" | morning recap, `today` | Evidence for the time-back criterion, from logs we already read | Could turn into a score, so it's a plain fact: no totals, no comparisons, no streaks | **Keep**, fact only |
| finish line | tomorrow's list (from `done`), `today` | Gives the day an end without an alert. `done` answers *how* to stop; this answers *when* | Could feel like a quota, so it's only the list you wrote yourself, shown only when asked | **Merged** into `done` + `today` |
| catch-up | `today` | Step away during a long agent run and catch up in seconds, instead of watching it work | Could encourage running more agents at once, so it's only shown when asked and has no alerts | **Merged** into `today` |
| park | pause | A prompt typed while paused goes onto tomorrow's list instead of starting a conversation about work. Nothing is lost | Changes the "you can still type" decision above. Must always let `/hestia:` commands through | **Proposed**, needs agreement |
| separate `catchup` / `break` / `overtime` / `week` commands, or a manual `park` | n/a | Already covered by `today`, the list and `back` | Sprawl | **Cut** |
| strip above the prompt box showing progress (a mod) | n/a | Always-visible progress | A permanent dashboard, which breaks the one rule | **Never** |

---

## How it's packaged: a Claude Code plugin in this repo, plus a CLI

```
.claude-plugin/marketplace.json
plugins/hestia/
  .claude-plugin/plugin.json
  commands/   done.md back.md today.md why.md new.md
  hooks/hooks.json            # PreToolUse (pause), SessionStart (morning, once)
  scripts/hestia.py           # deterministic engine + standalone CLI, stdlib only
tests/                        # python -m unittest
.github/workflows/test.yml    # tests on Windows, macOS and Linux
README.md
```

Install inside Claude Code:
```
/plugin marketplace add Lukallo/claude-impact-lab-10-26
/plugin install hestia@hestia
```
Without Claude Code: `python plugins/hestia/scripts/hestia.py done`.

- **`hestia.py`** handles the facts: snapshots, pause state, log parsing, load numbers and changelog diffing. It's testable with nothing mocked.
- **The command `.md` files** have Claude do the language: phrasing the list, `why` and the `new` pick. Each one calls `hestia.py` for real data first.

> **Format note:** the current Claude Code docs call `commands/*.md` the older form and recommend `skills/<name>/SKILL.md` for new plugins. You still run them as `/hestia:done`. If park is agreed, `hooks.json` also gets a `UserPromptSubmit` hook.

## Technical notes

| Piece | Approach |
|---|---|
| Repo discovery | `cwd` of today's entries in `~/.claude/projects/*/*.jsonl` + current folder + `repos` in config |
| Snapshot | `GIT_INDEX_FILE=tmp git add -A` → `write-tree` → `commit-tree -p HEAD` → `update-ref refs/hestia/<date>`; restore with `git checkout refs/hestia/<date> -- .` |
| Prompts | `type == "user"` entries with plain-text content; skip `tool_result` blocks and `<command…>` wrappers |
| Pause | `~/.hestia/pause.json` `{until}`; the hook exits 0 immediately when there's no pause, so it adds no cost on normal days |
| Paths | `${CLAUDE_PLUGIN_ROOT}`, so the user's `settings.json` is never edited |
| Python | A launcher that tries `python3` then `python` (Windows only has `python`) |
| What's new | Claude Code `CHANGELOG.md` from GitHub, compared against `claude --version` and `~/.hestia/digest.json`; offline means "couldn't check, nothing to do" |
| Config | Optional `~/.hestia/config.json`: resume hour, working days, extra repos |
| Overnight fact | Pause start from `~/.hestia/pause.json`, then the first `tool_use` timestamp after it in `~/.claude/projects/*/*.jsonl` |
| Finish line | `done` writes the list as a `- [ ]` checklist. `today` gives Claude the list plus today's commits and prompts, and Claude says which items look done. The count is Claude's judgement and is labelled as such |
| Catch-up | Per session, since your last prompt: tool calls, files changed, and whether the last assistant message asks you something ("waiting on you") |
| Park (proposed) | `UserPromptSubmit` hook: if paused and the prompt doesn't start with `/hestia:`, append it to tomorrow's list and return `decision: "block"` with a reason shown to you. Exits immediately when there's no pause |

## Making it easy for a reviewer (human or AI) to verify

These are plain facts, not persuasion. Each one maps to a judging line:

- **README, first screen:** who it's for → the problem → which brief idea → install in two lines → what you'll see.
- **Real sample output** in the README, captured from an actual run and labelled as such.
- **`hestia.py selftest`**: builds a throwaway repo, runs `done` → checks the snapshot → checks that the hook blocks → runs `back`. It proves the main flow end to end in one command.
- **Tests + CI badge** on three operating systems.
- **"What it touches and how to undo it"** and **"What it deliberately doesn't do"** (no alerts, no streaks), which speak directly to the time-back criterion.
- **Honest limitations** (e.g. `new` needs network; the pause only blocks agents started through Claude Code).
- **The overnight fact in the sample output** ("nothing ran between 17:30 and 09:05"), the most direct evidence for the time-back criterion.

## Build order

1. Plugin skeleton + marketplace, so it can be installed.
2. `hestia.py` pause / `back` / `today` + `PreToolUse` hook.
3. Safe snapshot + tests.
4. Log parsing → `done` list (with the "what today was for" line), load numbers, morning recap.
5. `why`, then `new`.
6. `selftest`, CI, README with real output.

The additions slot into step 4, since the overnight fact, the finish line and catch-up all come from the same log parsing. Park, if agreed, goes with step 2.

### Cut line for tonight

If time runs short, stop wherever the clock says, in this order:
- **Must:** steps 1–4 (skeleton, pause and `back`, safe snapshot, `done` list and morning recap), plus the overnight fact.
- **Next:** `why`, `selftest`, README with real output.
- **Last:** `new` (needs network, and it's the piece most likely to add reading), then CI on three operating systems.
