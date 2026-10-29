<p align="center">
  <img src="assets/banner.svg" alt="Hestia: keep the fire going, don't burn out. A Claude Code plugin." width="900">
</p>

<h1 align="center">Hestia</h1>

<p align="center">
  <img src="https://img.shields.io/badge/Claude%20Code-plugin-E8743B?style=flat" alt="Claude Code plugin">
  <img src="https://img.shields.io/badge/Python%203-stdlib%20only-F4B740?style=flat" alt="Python 3, standard library only">
  <img src="https://img.shields.io/badge/brief-a%20shutdown%20command-A4472B?style=flat" alt="Brief: a shutdown command">
</p>

**One command that ends the working day in Claude Code.** `/hestia:shutdown` banks whatever you have open, writes tomorrow's first move, and stops your agents until morning.

- **Your work is saved without being touched.** Uncommitted changes in every repo you worked in today go into a `refs/hestia/<timestamp>` snapshot. Your branch, working tree and index stay exactly as they were.
- **The agents stop when you do.** Unattended Claude Code runs get `SIGTERM`. Open sessions are paused by a hook until the next working morning.
- **Picking back up is guaranteed, once.** The first session next morning is handed yesterday's list automatically, a single time, then Hestia goes quiet. No alerts, no prompts, no dashboard.

<p align="center">
  <img src="assets/cycle.svg" alt="The day as a fire: tend it during the day, bank it with /hestia:shutdown, keep it overnight while agents are paused, relight it in the morning with yesterday's list ready." width="900">
</p>
<p align="center"><em>Tend the fire by day, bank it with <code>/hestia:shutdown</code>, keep it overnight, relight it in the morning.</em></p>

## Get started

In Claude Code:

```text
/plugin marketplace add Lukallo/hestia
/plugin install hestia@hestia
```

Needs `git` and `python3`. No other dependencies: the engine is standard library only. At the end of the day, type:

```text
/hestia:shutdown
```

That's it. You get:

```text
~/.hestia/
├── TOMORROW.md     tomorrow's list: your note, where you left off, the exact restore line
├── pause.json      when the pause lifts (gone after /hestia:resume)
├── handover.json   makes sure the morning handover happens only once
└── config.json     optional, only if you create it

<each repo you touched today>
└── refs/hestia/<timestamp>   a snapshot of your uncommitted work
```

<p align="center"><img src="assets/meander.svg" alt="" width="900"></p>

## AI removed the natural end of the workday

<img src="assets/logo.svg" width="72" align="right" alt="Hestia logo: a single flame on a coal-coloured tile">

Hestia is the Greek goddess of the hearth, and her one job was to keep the fire going. A fire that burns too hot burns out. A fire that's tended, banked at night and relit in the morning lasts. This tool doesn't help you burn brighter.

Each task finishes faster, so there's always one more that fits. With agents running, the work keeps going whether you're there or not. Stopping has become a deliberate act, and nobody has built the act. Starting work has infinite tooling. Ending it has none.

So the day doesn't end. It just thins out.

The reason people don't stop isn't discipline. Stopping feels like dropping something: the fear is losing the thread. So shutting down is only possible if picking back up is guaranteed. That's the whole mechanism here.

Burnout research splits recovery into breaks during the day and *detaching* in the evening: not doing the work, and not thinking about it either. AI compression erodes both. The waits that used to give breaks for free are gone, and the day has no finish line. [PLAN.md](PLAN.md) maps each cause onto the piece that answers it.

### Who it's for

People who build with Claude Code every day, run several sessions or repos at once, and find it hard to stop.

### The brief

Built for the Claude Code Impact Lab, Dublin. The brief: **a shutdown command**. Its one rule is that it has to give time back, not help you do more.

<p align="center"><img src="assets/meander.svg" alt="" width="900"></p>

## Three commands, two hooks, nothing else

| | Command | What happens |
|---|---|---|
| <img src="assets/icons/bank.svg" width="40" alt="Bank: embers covered for the night"> | `/hestia:shutdown [note]` | Saves open work in every repo you touched today, writes tomorrow's list, and pauses Claude until the next working morning |
| <img src="assets/icons/spark.svg" width="40" alt="Spark: striking a light early"> | `/hestia:resume` | Lifts the pause early, and reminds you of the one thing you were mid-way through |
| <img src="assets/icons/embers.svg" width="40" alt="Embers: reading how warm it's running"> | `/hestia:status` | Whether you're paused, and today's load, only ever when you ask |

Plus two things that happen on their own, and nothing else:

| | When | What happens |
|---|---|---|
| <img src="assets/icons/keep.svg" width="40" alt="Keep: one ember guarded overnight"> | *at shutdown* | Unattended agents are sent `SIGTERM`. Interactive sessions are left alone and reported; a `PreToolUse` hook stops them at their next step instead of killing a window you're sitting in front of |
| <img src="assets/icons/relight.svg" width="40" alt="Relight: sunrise over the hearth"> | *next morning* | A `SessionStart` hook hands yesterday's list to the first session after the pause, **once**, then goes quiet |

The second one is the whole trick. The context you left with is given back automatically, and exactly once, because a recap on every session is just noise.

> [!TIP]
> While paused, the hook blocks every tool call except the way back in. `/hestia:resume` is explicitly allowlisted, so you can never lock yourself out.

### Stopping agents, carefully

A headless run (`claude --print`, `--output-format`) has nobody watching it, so it's the kind that quietly burns all night. Those get `SIGTERM`: graceful, never `SIGKILL`.

An interactive session has a person at it, so killing it would take work away rather than give time back. It's reported and paused instead.

Hestia never signals its own process or anything that spawned it, so the command can't stop itself. `--keep-agents` reports without stopping anything; `--stop-all` includes interactive sessions.

<p align="center"><img src="assets/meander.svg" alt="" width="900"></p>

## What you'll see: real output, one run

Everything below was captured from a run of the engine against a throwaway repo, with `HESTIA_HOME` pointed at a temp folder so it didn't pause a live session.

> [!NOTE]
> The only edit is shortening the temp paths to `~/.hestia` and `/path/to/demo`. The run used `--keep-agents`, so nothing was signalled.

**Ending the day** (`/hestia:shutdown unblocking the CSV import for the ops team`):

```text
Clocked out until Fri 30 Oct 08:00.
  saved 2 files in demo -> refs/hestia/2026-10-29-182042
  tomorrow's list: ~/.hestia/TOMORROW.md
```

Three lines, and it stops. When Claude sessions are still open, a fourth line reports them: `1 Claude session still open - paused, they'll stop at their next step`.

**What it leaves for the morning** (`~/.hestia/TOMORROW.md`):

```markdown
# Tomorrow - Thu 29 Oct

> unblocking the CSV import for the ops team

## Where you left off

- **demo** (main) - 2 files saved
  `git -C /path/to/demo checkout refs/hestia/2026-10-29-182042 -- .`

## Today

- no prompts recorded today

## Overnight

- nothing was running
```

The throwaway setup has no session history, so `Today` is empty. On a real machine it fills in with the same numbers `/hestia:status` reports.

**If a session tries to work while you're clocked out** (the `PreToolUse` hook's answer to an `Edit`):

```text
{"hookSpecificOutput": {"hookEventName": "PreToolUse", "permissionDecision": "deny", "permissionDecisionReason": "Clocked out until Fri 30 Oct 08:00. Run /hestia:resume if tonight can't wait."}}
```

**Coming back early** (`/hestia:resume`):

```text
Back on. Paused since Thu 29 Oct 18:20.
Yesterday's list: ~/.hestia/TOMORROW.md
```

The first new session after that receives `TOMORROW.md` as context. A second session start prints nothing.

**Checking in, when you ask** (`/hestia:status`, on a real machine):

```text
Working.
  14 prompts across 1 project, 0.8 hours from first to last
  no break longer than 20 minutes today
```

<p align="center"><img src="assets/meander.svg" alt="" width="900"></p>

## Your work is saved without touching your repo

Hestia builds the snapshot commit through a **temporary git index**, so your working tree, your real index and your current branch are left exactly as they were:

```text
GIT_INDEX_FILE=<temp> git read-tree HEAD
GIT_INDEX_FILE=<temp> git add -A
git commit-tree <tree> -p HEAD    ->  git update-ref refs/hestia/<timestamp>
```

Nothing is stashed, nothing is committed to your branch, nothing moves. The test suite asserts that `.git/index` is **byte-identical** before and after.

**To get your work back:**

```text
git -C <repo> checkout refs/hestia/<timestamp> -- .
```

The exact line, with the real timestamp, is written into tomorrow's list.

### What it touches, and how to undo it

| Thing | Where | Undo |
|---|---|---|
| Snapshots | `refs/hestia/*` in each repo | `git update-ref -d refs/hestia/<ts>` |
| Pause state | `~/.hestia/pause.json` | `/hestia:resume`, or delete the file |
| Handover marker | `~/.hestia/handover.json` | delete it to get the list again |
| Tomorrow's list | `~/.hestia/TOMORROW.md` | delete it |
| Config | `~/.hestia/config.json` (optional) | delete it |

It never edits your `settings.json`, never writes into your repos' working trees, and never pushes anything.

<p align="center"><img src="assets/meander.svg" alt="" width="900"></p>

## Prove it: 18 checks, 22 tests

One command builds a throwaway repo, runs the whole flow, and checks every claim on this page:

```text
python3 plugins/hestia/scripts/hestia.py selftest
```

Real output:

```text
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
PASS no handover while still paused
PASS resume runs
PASS morning gets yesterday's list
PASS handover carries the note
PASS handover happens only once
PASS hook is silent after resume
PASS expired pause clears itself

18/18 checks passed
```

The selftest runs with `--keep-agents`, so it never touches real processes.

The 22 unit tests run against real git repositories in temp directories. Nothing is mocked:

```text
python3 -m unittest discover -s tests -v
```

```text
Ran 22 tests in 3.153s

OK
```

<p align="center"><img src="assets/meander.svg" alt="" width="900"></p>

## What it deliberately doesn't do

The brief's one rule is that it has to give time back, not help you do more. So:

- **No notifications, reminders or nudges.** Nothing ever interrupts you. Every command runs because you typed it.
- **No dashboard.** The workload numbers live inside `/hestia:status` and are shown only when asked. A thing you have to check is a thing that costs attention.
- **No streaks, scores or goals.** Measuring rest turns rest into work.
- **No summary at shutdown.** `/hestia:shutdown` answers in three lines and stops. A command that ends the day shouldn't give you more to read.

The time it saves is the stashing, the "where was I?" note, the morning re-orientation, and the agents that would otherwise have run all night.

## Honest limitations

- Agent stopping only reaches Claude Code processes on **this machine**, found via `ps`.
- On Windows the pause works, but nothing is signalled, and the hooks call `python3`.
- We can't claim burnout goes down. We can claim the day has an end, the agents stopped, and nothing ran between shutdown and morning.

<details>
<summary><b>Limitations in detail</b></summary>

- Agent stopping finds Claude Code processes on **this machine**, via `ps`. A run on a CI box, a remote host or inside a container keeps going; so does anything not launched as `claude`.
- Process detection is POSIX (`ps`, `SIGTERM`). On Windows the pause still works (the hook blocks the next step), but nothing is signalled.
- The handover fires on `SessionStart` with the `startup` matcher, so resuming an existing session with `--continue` won't re-deliver the list. That's deliberate: it's already in that session's context.
- While paused, Claude won't act on anything, including for you. That's deliberate, but it means `/hestia:resume` is the only way back in tonight. The hook allowlists that command explicitly so you can never lock yourself out.
- Repo discovery reads the `cwd` of today's prompts from `~/.claude/projects`. If you worked somewhere Claude Code never saw, add it to `repos` in the config.
- We can't claim burnout goes down; that takes weeks to show. What we can claim is that the day has an end, the agents stopped overnight, and nothing ran between shutdown and morning.
- Developed on macOS and Linux. The selftest and unit tests also pass on Windows 11 with Python 3.13, but the plugin hasn't been run end to end inside Claude Code on Windows. The hooks invoke `python3`; on Windows that is usually `python`, so edit `hooks/hooks.json` accordingly.

</details>

<p align="center"><img src="assets/meander.svg" alt="" width="900"></p>

## Use it your way

<details>
<summary><b>Without Claude Code: the engine is a plain CLI</b></summary>

```text
python3 plugins/hestia/scripts/hestia.py shutdown --note "shipped the import fix"
python3 plugins/hestia/scripts/hestia.py status
python3 plugins/hestia/scripts/hestia.py resume
```

`shutdown` also takes `--until HH:MM` (or an ISO timestamp), `--keep-agents`, `--stop-all` and `--json`. `status` takes `--json`.

</details>

<details>
<summary><b>Config: resume hour, working days, extra repos</b></summary>

Optional, at `~/.hestia/config.json`:

```json
{
  "resume_hour": 8,
  "working_days": [0, 1, 2, 3, 4],
  "repos": ["/Users/you/code/side-project"]
}
```

`working_days` is Monday = 0. Shutting down on a Friday evening resumes Monday morning.

</details>

<p align="center"><img src="assets/meander.svg" alt="" width="900"></p>

## Project links

- [PLAN.md](PLAN.md): the full design, the burnout grounding, and the audit of what was cut and why.
- [assets/BRAND.md](assets/BRAND.md): the visual identity, palette and icon rules.
- [REFERENCE.md](REFERENCE.md): projects we learned from, and what we took from each.
