# Hestia: keep up with your AI tools without losing the reason you do the work

> Status: all eight commands (the six planned plus `init` and `dashboard`) and the two hooks (`PreToolUse` pause, `SessionStart` handover) are built and tested.
> Not built yet: the lunch break, the recap on the first prompt after a break, the finish line, catch-up and park.
> The README's Scope section says why.

## Why "Hestia"

Hestia is the Greek goddess of the hearth. Her one job was to **keep the fire going**: every home and city kept
her flame alight, and it was never allowed to go out. That's the opposite of burnout. A fire that burns too hot
burns out; a fire that's tended, banked at night and relit in the morning lasts.

That's what this tool does. It doesn't help you burn brighter. It tends the fire: it banks it at the end of the day
(`/hestia:done`), lets it rest at midday (`/hestia:break`), keeps it safe overnight (agents paused), and relights it
in the morning (yesterday's list, ready). It also keeps you close to the warm part of the work, who it's for and why
you started (`/hestia:why`).

## The one rule

**Every piece must give you time back. Nothing in Hestia may help you do more.** Each piece is checked against this in the [audit](#audit-does-each-piece-strictly-help-and-could-it-hurt).

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
| The waits disappear (builds, searches, first drafts), and so do the breaks they gave for free | Recovery | Exhaustion | `break` and lunch; catch-up in `today` so you can step away during a long run |
| The day has no natural end: stopping becomes a choice against a next task that costs nothing | Recovery (detaching) | Exhaustion | `done`, the pause, the finish line |
| What's left is judgment, with constant switching between sessions | Workload | Exhaustion | `today`, the recap |
| New Claude Code features every week | Workload | Exhaustion | `new`, one item only |
| Being twice as fast becomes the new normal, and the gain is never cashed | Reward, fairness | Cynicism | Finish line and overnight fact make it visible. A tool can't fix this alone |
| You ask Claude instead of a colleague | Community | Cynicism | Mostly not covered. `why` makes it easy to send a person an update |
| The craft part got automated; you review output you didn't make | Values | Cynicism, feeling ineffective | `why`, "what today was for" |
| The bar keeps moving, so you never feel done | Reward | Feeling ineffective | Finish line ("3 of 5 done") |

What we can honestly claim tonight: not that burnout goes down, which takes weeks to show, but that the early signs change. Agents stop over lunch and overnight, the day has an end, and nothing ran between `done` and morning.

## Who it's for

People who build with Claude Code every day, run several sessions or repos at once, and find it hard to stop.

## Brief ideas it covers

**Shutdown command** (the core), plus **workload tracker** and **learning AI together** as supporting
pieces, and one of our own: **staying in touch with what the work is for**.

---

## The suite: 8 commands, 3 automatic behaviours

| | What it does | Replaces (the time it gives back) |
|---|---|---|
| `/hestia:done [note]` | Saves open work safely, writes tomorrow's list, pauses agents until morning | Manual stashing/WIP commits, "where was I?" notes, agents running overnight |
| `/hestia:break [45m]` | Saves where you are and pauses agents for that long, then lifts itself. No list for tomorrow, because the day isn't over | The daytime breaks that disappeared along with the waits |
| `/hestia:back` | Lifts a pause or break early | n/a (the escape hatch) |
| `/hestia:today` | Only when asked: whether you're paused; today's load (projects, hours from first to last prompt, last real break, unsaved repos); the finish line ("3 of 5 done"); and catch-up on what agents did since your last prompt | Guessing how much you've taken on; watching agents work |
| `/hestia:init [remove]` | Once: finds what's already set up, asks four skippable questions, records which connectors Hestia may use, and writes a short about-me section to `~/.claude/CLAUDE.md` between markers. Added after the original plan (PR #9) | Re-explaining who you are and why you build to every new session |
| `/hestia:dashboard` | Only when asked: the `today` reading as one static page, with today's timeline, saved snapshots and tomorrow's list. Added after the original plan (PR #11) | Piecing the day together from `today`, `git` and `TOMORROW.md` |
| `/hestia:why [target]` | One plain paragraph: who this work helps and what changed for them. Use it for yourself or to paste into an update for a non-technical person | Writing status updates by hand; losing the "why" |
| `/hestia:new` | Shows only what changed in Claude Code since your version or the last check, then picks the **one** thing relevant to how you work, in five lines | Reading every release note to keep up |
| *auto* pause | While paused, a `PreToolUse` hook blocks agent tool use: "Clocked out until Mon 08:00, `/hestia:back` if urgent" or "On a break until 13:50" | Overnight agent creep |
| *auto* recap | After any pause ends, your first prompt gets where you left off as context, **once**: yesterday's list in the morning, opening with the overnight fact ("Shut down 17:30, next agent activity 09:05, nothing ran overnight"), or "before the break you were on X" | 20 minutes of re-orientation each morning |
| *auto* lunch (opt-in) | Set a lunch time once, and a break starts at the first natural stopping point after it (when Claude finishes its turn), with one line: "Lunch until 13:50, your work is saved" | Skipped lunches |
| *auto* park (**proposed**) | While paused, a prompt you type goes onto tomorrow's list instead of starting a conversation about work | The evening "just one thing" |

### Audit: does each piece strictly help, and could it hurt?

| Piece | Helps | Could it hurt? | Decision |
|---|---|---|---|
| `done` | Core of the brief; removes the fear of stopping | Snapshot could touch files, so it uses a temp git index: tree, index and branch stay **byte-identical** (tested) | **Keep** (the centre of the project) |
| `break` + lunch | Brings back the daytime break, the first thing compression takes | A break forced on you mid-task is irritating, and breaks people choose do more for recovery than imposed ones. So you set the time, it only starts between turns, and `back` ends it early. Agents pause too: if they kept running, you'd check on them from your phone | **Keep**, lunch opt-in |
| pause hook | Agents actually stop | Blocking *your* prompts would add a prompt, so we **only block agent tools**. You can still type, and Claude just can't act | **Keep**, agent tools only |
| recap | Saves re-orientation time | Injecting context every session would be noise, so it fires **once**, after a pause | **Keep**, once only |
| overnight fact | Evidence for the time-back criterion, from logs we already read | Could turn into a score, so it's a plain fact: no totals, no comparisons, no streaks | **Keep**, fact only |
| "what today was for" | Restores the romantic part | As a separate command it's one more thing to read, so it **merges into tomorrow's list** as its first line | **Merged** into `done` |
| finish line | Gives the day an end without an alert. `done` answers *how* to stop; this answers *when* | Could feel like a quota, so it's only the list you wrote yourself, shown only when asked | **Merged** into `done` + `today` |
| load tracker | Lets you see your workload | As a dashboard or alert it would add attention cost, so it lives inside `today` and is **only shown when asked** | **Merged** into `today` |
| `init` | Every session explains changes by what they do for people first, with no extra prompts | Writes to a file the user owns, so it only touches text between its own markers, keeps a backup, and `init remove` restores the file byte for byte. A declined connector stays declined | **Kept**, run once |
| `dashboard` (static page) | One look at the day when you ask for it | A dashboard you keep open becomes a monitor, so it is a static file: no scripts, no network, no refresh, no totals across days, and it only appears when asked | **Kept**, on request only |
| catch-up | Step away during a long agent run and catch up in seconds, instead of watching it work | Could encourage running more agents at once, so it's only shown when asked and has no alerts | **Merged** into `today` |
| `why` | Replaces writing updates; keeps the why | Could be fluff, so it's grounded in real commits and prompts from `hestia.py` | **Keep** |
| `new` | Keeping up without chasing | Network-dependent and could push *more* consumption, so it shows **one** item only, degrades cleanly offline, and shows nothing when there's nothing new | **Keep**, tightly scoped |
| park | Nothing typed in the evening is lost, and Claude doesn't start a conversation about work | Changes the pause hook's "you can still type" decision. Must always let `/hestia:` commands through | **Proposed**, needs agreement |
| separate `morning` / `load` / "what today was for" / `catchup` / `overtime` / `week` commands, or a manual `park` | n/a | Sprawl: each one is covered above | **Cut** |
| notifications, reminders, streaks, scores, an always-visible progress strip | n/a | Adds alerts and pressure, which breaks the one rule | **Never** |

---

## How it's packaged: a Claude Code plugin in this repo, plus a CLI

```
.claude-plugin/marketplace.json
plugins/hestia/
  .claude-plugin/plugin.json
  commands/   done.md break.md back.md today.md why.md new.md
  hooks/hooks.json            # PreToolUse (pause), SessionStart + UserPromptSubmit (recap), Stop (lunch)
  scripts/hestia.py           # deterministic engine + standalone CLI, stdlib only
tests/                        # python -m unittest
.github/workflows/test.yml    # tests on Windows, macOS and Linux
README.md
```

Install inside Claude Code:
```
/plugin marketplace add Lukallo/hestia
/plugin install hestia@hestia
```
Without Claude Code: `python plugins/hestia/scripts/hestia.py done`.

- **`hestia.py`** handles the facts: snapshots, pause and break state, log parsing, load numbers and changelog diffing. It's testable with nothing mocked.
- **The command `.md` files** have Claude do the language: phrasing the list, `why` and the `new` pick. Each one calls `hestia.py` for real data first.

> **Format note:** the current Claude Code docs call `commands/*.md` the older form and recommend `skills/<name>/SKILL.md` for new plugins. You still run them as `/hestia:done`.

## Technical notes

| Piece | Approach |
|---|---|
| Repo discovery | `cwd` of today's entries in `~/.claude/projects/*/*.jsonl` + current folder + `repos` in config |
| Prompts | `type == "user"` entries with plain-text content; skip `tool_result` blocks and `<command…>` wrappers |
| Snapshot | `GIT_INDEX_FILE=tmp git add -A` → `write-tree` → `commit-tree -p HEAD` → `update-ref refs/hestia/<date>`; restore with `git checkout refs/hestia/<date> -- .` |
| Pause and break | `~/.hestia/pause.json` `{until, kind}`, where `kind` is `done` or `break`. The hook exits 0 immediately when there's no pause, so it adds no cost on normal days. A break lifts itself once `until` has passed |
| Lunch | `lunch` (e.g. `"13:00"`) and optional `lunch_minutes` (default 45) in config. A `Stop` hook checks: past lunch time, no break taken today, not paused. If so it starts a break and shows one line; otherwise it exits immediately. Other sessions that are mid-turn stop at their next tool call |
| Recap | `done` and `break` write a short note next to `pause.json`. `SessionStart` adds it to a new session; `UserPromptSubmit` adds it (as `additionalContext`) to the first prompt in a session left open over lunch or overnight, which `SessionStart` alone would miss. It's cleared once it has fired, and the hook exits immediately when there's nothing to add |
| Overnight fact | Pause start from `pause.json`, then the first `tool_use` timestamp after it in `~/.claude/projects/*/*.jsonl` |
| Finish line | `done` writes the list as a `- [ ]` checklist. `today` gives Claude the list plus today's commits and prompts, and Claude says which items look done. The count is Claude's judgement and is labelled as such |
| Catch-up | Per session, since your last prompt: tool calls, files changed, and whether the last assistant message asks you something ("waiting on you") |
| What's new | Claude Code `CHANGELOG.md` from GitHub, compared against `claude --version` and `~/.hestia/digest.json`; offline means "couldn't check, nothing to do" |
| Park (proposed) | In the same `UserPromptSubmit` hook: if paused and the prompt doesn't start with `/hestia:`, append it to tomorrow's list and return `decision: "block"` with a reason shown to you |
| Paths | `${CLAUDE_PLUGIN_ROOT}`, so the user's `settings.json` is never edited |
| Python | A launcher that tries `python3` then `python` (Windows only has `python`) |
| Config | Optional `~/.hestia/config.json`: resume hour, working days, lunch time, extra repos |

## Making it easy for a reviewer (human or AI) to verify

These are plain facts, not persuasion. Each one maps to a judging line:

- **README, first screen:** who it's for → the problem → which brief idea → install in two lines → what you'll see.
- **Real sample output** in the README, captured from an actual run and labelled as such, including the overnight fact ("nothing ran between 17:30 and 09:05"), the most direct evidence for the time-back criterion.
- **`hestia.py selftest`**: builds a throwaway repo, runs `done` → checks the snapshot → checks that the hook blocks → runs `back`, then takes a short break and checks that it lifts itself. It proves the main flow end to end in one command.
- **Tests + CI badge** on three operating systems.
- **"What it touches and how to undo it"** and **"What it deliberately doesn't do"** (no alerts, no streaks), which speak directly to the time-back criterion.
- **Honest limitations** (e.g. `new` needs network; the pause only blocks agents started through Claude Code).

## Build order

In priority order. If time runs short, stop wherever the clock says: everything above that point still works.

**Must**

1. Plugin skeleton + marketplace, so it can be installed.
2. `hestia.py` pause / `back` / `today` + `PreToolUse` hook, and `break` (the same pause with an end time). Park goes here if agreed.
3. Safe snapshot + tests.
4. Log parsing → `done` list (with the "what today was for" line), load numbers, recap, overnight fact.

**Next**

5. Finish line, catch-up, lunch.
6. `why`.
7. `selftest` and README with real output.

**Last**

8. `new` (needs network, and it's the piece most likely to add reading).
9. CI on three operating systems.
