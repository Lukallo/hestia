# Hestia

**One command that ends the working day in Claude Code.** It banks whatever you
have open, writes tomorrow's first move, and stops your agents until morning.

Built for the Claude Code Impact Lab, Dublin — brief: **a shutdown command**.

Hestia is the Greek goddess of the hearth, and her one job was to keep the fire
going. A fire that burns too hot burns out; a fire that's tended, banked at
night and relit in the morning lasts. This tool doesn't help you burn brighter.

---

## The problem

AI removed the natural end of the workday.

Each task finishes faster, so there's always one more that fits. And with agents
running, the work keeps going whether you're there or not. Stopping has become a
deliberate act — and nobody has built the act. Starting work has infinite
tooling. Ending it has none.

So the day doesn't end. It just thins out.

The reason people don't stop isn't discipline. It's that stopping feels like
dropping something: the fear is losing the thread. So shutting down is only
possible if picking back up is guaranteed. That's the whole mechanism here.

Burnout research splits recovery into breaks during the day and *detaching* in
the evening — not doing the work, and not thinking about it either. AI
compression erodes both: the waits that used to give breaks for free are gone,
and the day has no finish line. [PLAN.md](PLAN.md) maps each cause onto the
piece that answers it.

## Who it's for

People who build with Claude Code every day, run several sessions or repos at
once, and find it hard to stop.

## Install

```
/plugin marketplace add Lukallo/hestia
/plugin install hestia@hestia
```

Needs `git` and `python3`. No other dependencies — the engine is standard
library only.

## What it does

| Command | What happens |
|---|---|
| `/hestia:shutdown [note]` | Saves open work in every repo you touched today, writes tomorrow's list, and pauses Claude until the next working morning |
| `/hestia:resume` | Lifts the pause early, and reminds you of the one thing you were mid-way through |
| `/hestia:status` | Whether you're paused, and today's load — only ever when you ask |

While paused, a `PreToolUse` hook stops Claude from taking actions and says when
you're back. That's the part that actually ends the day: the agents stop, not
just you.

## What you'll see

All output below is captured from real runs, not written by hand. The shutdown
example runs against a throwaway repo in a temp folder, so it doesn't pause a
live session.

**Ending the day:**

```
$ /hestia:shutdown unblocking the CSV import for the ops team

Clocked out until Fri 30 Oct 08:00.
  saved 2 files in demo -> refs/hestia/2026-10-29-180204
  tomorrow's list: ~/.hestia/TOMORROW.md
```

Three lines, and it stops. That's the whole answer.

**What it leaves for the morning** (`~/.hestia/TOMORROW.md`):

```markdown
# Tomorrow - Thu 29 Oct

> unblocking the CSV import for the ops team

## Where you left off

- **demo** (main) - 2 files saved
  `git -C /path/to/demo checkout refs/hestia/2026-10-29-180204 -- .`

## Today

- no prompts recorded today
```

The throwaway repo has no session history, so `Today` is empty here. On a real
machine it fills in with the same numbers `status` reports, below.

**Checking in, when you ask** (`/hestia:status`):

```
Working.
  13 prompts across 3 projects, 4.6 hours from first to last
  last real break: 247 min at 13:27
```

**The proof** (`python3 plugins/hestia/scripts/hestia.py selftest`):

```
PASS shutdown runs
PASS snapshot saved the work
PASS snapshot reported untouched
PASS branch unchanged
PASS working tree unchanged
PASS index byte-identical
PASS uncommitted file is recoverable
PASS tomorrow's list written
PASS hook blocks tool use while paused
PASS hook blocks edits while paused
PASS hook always allows resume
PASS resume runs
PASS hook is silent after resume
PASS expired pause clears itself

14/14 checks passed
```

## How your work is saved

The snapshot never touches your repo. Hestia builds a commit through a
**temporary git index**, so your working tree, your real index and your current
branch are left exactly as they were:

```
GIT_INDEX_FILE=<temp> git read-tree HEAD
GIT_INDEX_FILE=<temp> git add -A
git commit-tree <tree> -p HEAD    ->  git update-ref refs/hestia/<timestamp>
```

Nothing is stashed, nothing is committed to your branch, nothing moves. The test
suite asserts that `.git/index` is **byte-identical** before and after.

**To get your work back:**

```
git -C <repo> checkout refs/hestia/<timestamp> -- .
```

The exact line, with the real timestamp, is written into tomorrow's list.

## What it touches, and how to undo it

| Thing | Where | Undo |
|---|---|---|
| Snapshots | `refs/hestia/*` in each repo | `git update-ref -d refs/hestia/<ts>` |
| Pause state | `~/.hestia/pause.json` | `/hestia:resume`, or delete the file |
| Tomorrow's list | `~/.hestia/TOMORROW.md` | delete it |
| Config | `~/.hestia/config.json` (optional) | delete it |

It never edits your `settings.json`, never writes into your repos' working
trees, and never pushes anything.

## Prove it works

One command builds a throwaway repo, runs the whole flow and checks every claim
on this page:

```
python3 plugins/hestia/scripts/hestia.py selftest
```

It verifies that the snapshot recovers an uncommitted file, that the branch,
working tree and index are unchanged, that the hook blocks tool use while
paused, that it *never* blocks `resume`, and that an expired pause lets you back
in on its own.

The unit tests run against real git repositories in temp directories — nothing
is mocked:

```
python3 -m unittest discover -s tests -v
```

## What it deliberately doesn't do

The brief's one rule is that it has to give time back, not help you do more. So:

- **No notifications, reminders or nudges.** Nothing ever interrupts you. Every
  command runs because you typed it.
- **No dashboard.** The workload numbers live inside `/hestia:status` and are
  shown only when asked. A thing you have to check is a thing that costs
  attention.
- **No streaks, scores or goals.** Measuring rest turns rest into work.
- **No summary at shutdown.** `/hestia:shutdown` answers in three lines and
  stops. A command that ends the day shouldn't give you more to read.

The time it saves is the stashing, the "where was I?" note, the morning
re-orientation, and the agents that would otherwise have run all night.

## Honest limitations

- The pause blocks tool use **inside Claude Code**. Agents you started by other
  means (a cron job, a CI runner, a separate terminal) keep going.
- While paused, Claude won't act on anything — including for you. That's
  deliberate, but it means `/hestia:resume` is the only way back in tonight. The
  hook allowlists that command explicitly so you can never lock yourself out.
- Repo discovery reads the `cwd` of today's prompts from `~/.claude/projects`.
  If you worked somewhere Claude Code never saw, add it to `repos` in the
  config.
- We can't claim burnout goes down — that takes weeks to show. What we can claim
  is that the day has an end, the agents stopped overnight, and nothing ran
  between shutdown and morning.
- Developed and tested on macOS and Linux. The hook invokes `python3`; on
  Windows that is usually `python`, so edit `hooks/hooks.json` accordingly. Not
  tested on Windows.

## Without Claude Code

The engine is a plain CLI and works on its own:

```
python3 plugins/hestia/scripts/hestia.py shutdown --note "shipped the import fix"
python3 plugins/hestia/scripts/hestia.py status
python3 plugins/hestia/scripts/hestia.py resume
```

## Config

Optional, at `~/.hestia/config.json`:

```json
{
  "resume_hour": 8,
  "working_days": [0, 1, 2, 3, 4],
  "repos": ["/Users/you/code/side-project"]
}
```

`working_days` is Monday = 0. Shutting down on a Friday evening resumes Monday
morning.

---

The full design, the burnout grounding, and the audit of what was cut and why
are in [PLAN.md](PLAN.md).
