<p align="center">
  <img src="assets/banner.svg" alt="Hestia: keep the fire going, don't burn out. A Claude Code plugin." width="900">
</p>

<h1 align="center">Hestia</h1>

<p align="center">
  <img src="https://img.shields.io/badge/Claude%20Code-plugin-E8743B?style=flat" alt="Claude Code plugin">
  <img src="https://img.shields.io/badge/Python%203-stdlib%20only-F4B740?style=flat" alt="Python 3, standard library only">
  <img src="https://img.shields.io/badge/brief-a%20shutdown%20command-A4472B?style=flat" alt="Brief: a shutdown command">
</p>

**One command, `/hestia:done`, ends your working day in Claude Code: it saves your open work, writes tomorrow's first step, and stops your agents until morning.**

## Who it's for

People who build with Claude Code every day and find it hard to stop. AI removed the natural end of the workday: each task finishes faster, agents keep running, and the day thins out instead of ending (compression burnout). Hestia answers the Claude Code Impact Lab brief, **a shutdown command**, by making stopping safe: picking back up is guaranteed.

<p align="center">
  <img src="assets/cycle.svg" alt="The day as a fire: tend it during the day, bank it with /hestia:done, keep it overnight while agents are paused, relight it in the morning with yesterday's list ready." width="900">
</p>
<p align="center"><em>Tend the fire by day, bank it with <code>/hestia:done</code>, keep it overnight, relight it in the morning.</em></p>

<p align="center"><img src="assets/meander.svg" alt="" width="900"></p>

## Install

In Claude Code:

```text
/plugin marketplace add Lukallo/hestia
/plugin install hestia@hestia
```

Needs `git` and `python3`, nothing else.

<p align="center"><img src="assets/meander.svg" alt="" width="900"></p>

## Use it

| | Command | What it does |
|---|---|---|
| <img src="assets/icons/kindling.svg" width="40" alt="Kindling icon: one new thing, not the woodpile"> | `/hestia:new` | Shows one thing worth knowing from what changed in Claude Code since your last check |
| <img src="assets/icons/embers.svg" width="40" alt="Embers icon: reading how warm it's running"> | `/hestia:today` | Whether you're paused, and today's load, only when you ask |
| <img src="assets/icons/rest.svg" width="40" alt="Rest icon: the fire turned low at midday"> | `/hestia:break [45m]` | Saves where you are and pauses agents for that long, then lifts itself |
| <img src="assets/icons/home.svg" width="40" alt="Home icon: the home the fire warms"> | `/hestia:why [who for]` | One plain paragraph on who today's work helps, from your real commits and prompts |
| <img src="assets/icons/bank.svg" width="40" alt="Bank icon: embers covered for the night"> | `/hestia:done [note]` | Saves open work, writes tomorrow's list, pauses Claude until the next working morning |
| <img src="assets/icons/spark.svg" width="40" alt="Spark icon: striking a light early"> | `/hestia:back` | Lifts a pause or break early and reminds you where you were |

On their own: while you're paused, a hook blocks every tool call except `/hestia:back`, and the first session next morning is handed yesterday's list once, then Hestia goes quiet.

<p align="center"><img src="assets/meander.svg" alt="" width="900"></p>

## What you'll see

`/hestia:done unblocking the CSV import for the ops team`, from a real run on a throwaway repo (temp path shortened):

```text
Clocked out until Fri 30 Oct 08:00.
  saved 2 files in demo -> refs/hestia/2026-10-29-185346
  tomorrow's list: ~/.hestia/TOMORROW.md
```

<p align="center"><img src="assets/meander.svg" alt="" width="900"></p>

## What it touches, and how to undo it

Snapshots are built through a temporary git index, so your branch, working tree and index stay exactly as they were. Get work back with `git -C <repo> checkout refs/hestia/<timestamp> -- .` (the exact line is in tomorrow's list).

| Thing | Where | Undo |
|---|---|---|
| Snapshots | `refs/hestia/*` in each repo | `git update-ref -d refs/hestia/<ts>` |
| Pause or break state | `~/.hestia/pause.json` | `/hestia:back`, or delete the file |
| Last version `new` showed | `~/.hestia/digest.json` | delete it to see the same changes again |
| Handover marker | `~/.hestia/handover.json` | delete it to get the list again |
| Tomorrow's list | `~/.hestia/TOMORROW.md` | delete it |
| Config | `~/.hestia/config.json` (optional) | delete it |

It never edits your `settings.json`, never commits to your branch, and never pushes. Only `/hestia:new` uses the network, to read Claude Code's public changelog.

<p align="center"><img src="assets/meander.svg" alt="" width="900"></p>

## What it never does

- No notifications, reminders or nudges: every command runs because you typed it.
- No dashboard, streaks or scores: numbers appear only when you run `/hestia:today`.
- No summary at shutdown: `/hestia:done` answers in three lines and stops.
- Nothing runs overnight: unattended agents are stopped and open sessions are paused until morning.

<p align="center"><img src="assets/meander.svg" alt="" width="900"></p>

## Prove it works

```text
python3 plugins/hestia/scripts/hestia.py selftest
python3 -m unittest discover -s tests
```

<details>
<summary><b>Real output: 29 checks, 40 tests</b></summary>

```text
PASS done runs
PASS snapshot saved the work
PASS snapshot reported untouched
PASS branch unchanged
PASS working tree unchanged
PASS index byte-identical
PASS uncommitted file is recoverable
PASS tomorrow's list written
PASS hook blocks tool use while paused
PASS hook blocks edits while paused
PASS hook always allows back
PASS no handover while still paused
PASS back runs
PASS old names still work as aliases
PASS morning gets yesterday's list
PASS handover carries the note
PASS handover happens only once
PASS hook is silent after back
PASS expired pause clears itself
PASS break runs
PASS break saves where you are
PASS hook pauses agents during a break
PASS break writes no list
PASS back ends a break early
PASS break lifts itself
PASS why reads today's commits
PASS new shows only what changed
PASS new is quiet when nothing changed
PASS new degrades cleanly offline

29/29 checks passed
```

The unittest report (the commands under test also print their own output, left out here):

```text
........................................
----------------------------------------------------------------------
Ran 40 tests in 7.987s

OK
```

The selftest never touches real processes and reads a local changelog, so it runs offline. The tests use real git repos in temp folders; nothing is mocked.

</details>

<details>
<summary><b>More: limitations, config, CLI, how agents are stopped</b></summary>

### Limitations

- Agent stopping only reaches Claude Code processes on this machine, found via `ps`. On Windows the pause works but nothing is signalled.
- The hooks call `python3`; on Windows that is usually `python`, so edit `hooks/hooks.json`.
- Two sessions starting at the same moment can both get the morning handover.
- `/hestia:new` needs the network; offline it says it couldn't check.
- We can't claim burnout goes down. We can claim the day has an end and nothing ran between shutdown and morning.

### Config

Optional, at `~/.hestia/config.json` (`working_days`: Monday = 0; `repos` adds repos Claude Code never saw):

```json
{ "resume_hour": 8, "working_days": [0, 1, 2, 3, 4], "repos": ["/Users/you/code/side-project"] }
```

### Without Claude Code

The engine is a plain CLI: `python3 plugins/hestia/scripts/hestia.py done --note "..."`, and likewise `break 45m`, `today`, `back`, `why`, `new`. `done` takes `--until`, `--keep-agents` and `--stop-all`; `break` takes `--stop-agents`; most take `--json`. The old names `shutdown`, `resume` and `status` still work.

### How agents are stopped

At `/hestia:done`, unattended runs (`claude --print`) get `SIGTERM`, never `SIGKILL`. Interactive sessions are left alone and paused by the hook at their next step. `--keep-agents` stops nothing; `--stop-all` includes interactive sessions. A break signals nothing unless you pass `--stop-agents`.

</details>

<p align="center"><img src="assets/meander.svg" alt="" width="900"></p>

## Project links

- [PLAN.md](PLAN.md): the full design, the burnout research, and what was cut and why.
- [assets/BRAND.md](assets/BRAND.md): the visual identity, palette and icon rules.
- [REFERENCE.md](REFERENCE.md): projects we learned from.
- [connectors/README.md](connectors/README.md): what Hestia could work with.
