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

It also tidies Claude's [auto memory](https://code.claude.com/docs/en/memory) when you clock out, its own take on "dreaming".

## Who it's for

People who build with Claude Code every day and find it hard to stop. AI removed the natural end of the workday: each task finishes faster, agents keep running, and the day thins out instead of ending (compression burnout). Hestia answers the Claude Code Impact Lab brief, **a shutdown command**, by making stopping safe: picking back up is guaranteed.

<p align="center">
  <img src="assets/cycle.svg" alt="The day as a fire: start the day with /hestia:back and tend it, bank it with /hestia:done, keep it overnight while agents are paused, relight it in the morning with yesterday's list ready." width="900">
</p>
<p align="center"><em>Tend the fire by day, bank it with <code>/hestia:done</code>, keep it overnight, relight it in the morning.</em></p>

<p align="center"><img src="assets/meander.svg" alt="" width="900"></p>

## Install

Needs `git` and Python 3 (`python3` or `python`), nothing else; on Windows, Git for Windows.

**1. Install**, in Claude Code:

```text
/plugin marketplace add Lukallo/hestia
/plugin install hestia@hestia
```

**2. At the end of the day**, type:

```text
/hestia:done
```

That's all you need. The first session next morning picks up where you left off.

**3. Optional, once:** `/hestia:init` takes about a minute. Every question is skippable and has a default. It tells Hestia your working days and hours, and what your work is for.

```text
/hestia:init
```

<p align="center"><img src="assets/meander.svg" alt="" width="900"></p>

## Use it

| | Command | What it does |
|---|---|---|
| <img src="assets/icons/tend.svg" width="40" alt="Tend icon: lighting the fire for the first time"> | `/hestia:init` | Once: learns your hours and why you build, asks about connectors (and a screen, if it finds none), adds a short section to `~/.claude/CLAUDE.md` |
| <img src="assets/icons/kindling.svg" width="40" alt="Kindling icon: one new thing, not the woodpile"> | `/hestia:new` | Shows one thing worth knowing from what changed in Claude Code since your last check |
| <img src="assets/icons/embers.svg" width="40" alt="Embers icon: reading how warm it's running"> | `/hestia:today` | Whether you're paused, and today's load, only when you ask |
| <img src="assets/icons/embers.svg" width="40" alt="Embers icon: the same reading on one page"> | `/hestia:dashboard` | The same reading, plus saved work and tomorrow's list, on one static page that never refreshes; off if you told `/hestia:init` there's no screen |
| <img src="assets/icons/rest.svg" width="40" alt="Rest icon: the fire turned low at midday"> | `/hestia:break [45m]` | Saves where you are and pauses agents for that long, then lifts itself |
| <img src="assets/icons/home.svg" width="40" alt="Home icon: the home the fire warms"> | `/hestia:why [who for]` | One plain paragraph on who today's work helps, from your real commits and prompts |
| <img src="assets/icons/bank.svg" width="40" alt="Bank icon: embers covered for the night"> | `/hestia:done [note]` | Saves open work, writes tomorrow's list with a "Start here" step, pauses Claude until the next working morning |
| <img src="assets/icons/spark.svg" width="40" alt="Spark icon: striking a light early"> | `/hestia:back` | Lifts a pause or break early and tells you where to start |

On their own: while you're paused, a hook blocks every tool call, including connectors, subagents and other plugins, except `/hestia:back` on its own. Next morning the first session you're at (new, resumed, or a window left open, on its next prompt) gets yesterday's list exactly once, with where to start and whether anything ran overnight; scripted runs never take it. Then Hestia goes quiet.

<p align="center"><img src="assets/meander.svg" alt="" width="900"></p>

## What you'll see

`/hestia:done unblocking the CSV import for the ops team`, from a real run on a throwaway repo (temp path shortened):

```text
Clocked out until Fri 30 Oct 08:00.
  saved 2 files in demo -> refs/hestia/2026-10-29-195708
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
| Handover marker | `~/.hestia/handover.json`, `~/.hestia/handover-*.taken` | `hestia.py handover --again` to get the list again |
| Tomorrow's list | `~/.hestia/TOMORROW.md` | delete it |
| Config | `~/.hestia/config.json` | delete it |
| Claude's memory, tidied at `/hestia:done` | the memory folders of today's projects, backed up first to `~/.hestia/backups/memory/` | `hestia dream restore` |
| Your answers and connector choices | `~/.hestia/profile.json`, `~/.hestia/connectors.json` | delete them, or re-run `/hestia:init` |
| About-me section | `~/.claude/CLAUDE.md`, between `<!-- hestia:start -->` and `<!-- hestia:end -->` | `/hestia:init remove` restores the file byte for byte |
| Dashboard page | `~/.hestia/dashboard.html` | delete it |

It never edits your `settings.json`, never commits to your branch, and never pushes. It touches `CLAUDE.md` only inside its own markers, only when you run `/hestia:init`. Only `/hestia:new` uses the network, to read Claude Code's public changelog.

<p align="center"><img src="assets/meander.svg" alt="" width="900"></p>

## What it never does

- No notifications, reminders or nudges: every command runs because you typed it.
- No always-on dashboard, streaks or scores: `/hestia:dashboard` only opens when you type it, and nothing pings you.
- No summary at shutdown: `/hestia:done` answers in three lines and stops.
- Nothing runs overnight: unattended agents are stopped and open sessions are paused until morning.

<p align="center"><img src="assets/meander.svg" alt="" width="900"></p>

## Prove it works

```text
python3 plugins/hestia/scripts/hestia.py selftest
python3 -m unittest discover -s tests
```

<details>
<summary><b>Real output: 47 checks, 110 tests</b></summary>

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
PASS hook allows back from PowerShell
PASS hook allows back through the launcher
PASS hook blocks work chained after back
PASS hook blocks connector tools while paused
PASS no handover while still paused
PASS scripted runs never take the list
PASS a window left open gets it on its next prompt
PASS handover carries the note
PASS non-ASCII text survives the night
PASS handover leads with where to start
PASS handover says what ran overnight
PASS handover happens only once
PASS sessions starting together get it once
PASS morning gets yesterday's list
PASS back runs
PASS old names still work as aliases
PASS hook is silent after back
PASS expired pause clears itself
PASS break runs
PASS break saves where you are
PASS hook pauses agents during a break
PASS break writes no list
PASS back ends a break early
PASS restore line brings the work back
PASS break lifts itself
PASS why reads today's commits
PASS new shows only what changed
PASS new is quiet when nothing changed
PASS new degrades cleanly offline
PASS init writes profile, config and connectors
PASS init keeps CLAUDE.md outside its markers
PASS no dashboard on a computer with no screen
PASS init remove restores CLAUDE.md exactly
PASS dream plan backs up today's memory
PASS dream check rolls back a broken tidy
PASS dream restore puts memory back exactly

47/47 checks passed
```

The unittest result (the commands under test print their own output in between, left out here):

```text
Ran 110 tests in 33.009s

OK (skipped=6)
```

That run was on Windows, where 6 tests skip: 4 launcher tests need a POSIX `sh` with symlinks, and 2 dashboard tests use a shell-script stand-in browser. CI runs them on Linux and macOS. `tests/test_platforms.py` covers what differs between Linux, macOS and Windows: non-English text, folders with spaces, and hooks run the way Claude Code runs them.

The selftest never touches real processes and reads a local changelog, so it runs offline. The tests use real git repos in temp folders; nothing is mocked.

</details>

<details>
<summary><b>More: limitations, config, CLI, how agents are stopped</b></summary>

### Limitations

- Agent stopping only reaches Claude Code processes on this machine, found via `ps`. On Windows the pause works but nothing is signalled.
- Hooks and commands start Hestia through `scripts/hestia`, a small `sh` launcher that uses `python3`, or `python` when `python3` is missing or the Microsoft Store placeholder. On Windows it needs Git for Windows (Claude Code then runs hooks in Git Bash); without it the pause isn't enforced. CI runs the selftest and tests on Windows, but it hasn't been run end to end inside Claude Code on Windows.
- If a scripted run ever gets the morning list, set `HESTIA_SESSION_KIND=headless` in that job's environment.
- `/hestia:new` needs the network; offline it says it couldn't check.
- We can't claim burnout goes down. We can claim the day has an end and nothing ran between shutdown and morning.

### Config

At `~/.hestia/config.json` (written by `/hestia:init`, or by hand) (`working_days`: Monday = 0; `repos` adds repos Claude Code never saw):

```json
{ "resume_hour": 8, "working_days": [0, 1, 2, 3, 4], "repos": ["/Users/you/code/side-project"] }
```

`"screen": false` turns the dashboard off on a computer with no screen.

### Without Claude Code

The engine is a plain CLI: `python3 plugins/hestia/scripts/hestia.py done --note "..." --next "..."`, and likewise `init`, `break 45m`, `today`, `dashboard`, `back`, `why`, `new`. `handover` says whether the morning list is still due (`--again` re-arms it). `done` takes `--until`, `--keep-agents` and `--stop-all`; `break` takes `--stop-agents`; `dashboard` takes `--out` and `--no-open`; most take `--json`. The old names `shutdown`, `resume` and `status` still work.

### How agents are stopped

At `/hestia:done`, unattended runs (`claude --print`) get `SIGTERM`, never `SIGKILL`. Interactive sessions are left alone and paused by the hook at their next step. `--keep-agents` stops nothing; `--stop-all` includes interactive sessions. A break signals nothing unless you pass `--stop-agents`. The pause is a `PreToolUse` hook, and Claude Code ranks a deny above any other hook's allow, so another plugin can't approve its way past it; `tests/compat/run.sh` checks this with real `claude -p` sessions.

</details>

<p align="center"><img src="assets/meander.svg" alt="" width="900"></p>

A nod to the Dublin Claude Code community: no endless agent loops here, since `/hestia:done` gives every one an end.

## Project links

- [PLAN.md](PLAN.md): the full design, the burnout research, and what was cut and why.
- [assets/BRAND.md](assets/BRAND.md): the visual identity, palette and icon rules.
- [REFERENCE.md](REFERENCE.md): projects we learned from.
- [connectors/README.md](connectors/README.md): what Hestia could work with.
