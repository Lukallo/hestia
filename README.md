<p align="center">
  <img src="assets/banner.svg" alt="Hestia: keep the fire going, don't burn out. A Claude Code plugin." width="900">
</p>

<p align="center">
  <img src="https://img.shields.io/badge/Claude%20Code-plugin-E8743B?style=flat" alt="Claude Code plugin">
  <img src="https://img.shields.io/badge/Python-3.9%2B%20%C2%B7%20stdlib%20only-F4B740?style=flat" alt="Python 3.9+, standard library only">
  <img src="https://img.shields.io/badge/status-in%20development-6B615B?style=flat" alt="Status: in development">
</p>

<p align="center">
  <b>One command ends your day.</b> Your work is saved, tomorrow's list is written, and your agents stay quiet until morning.
</p>

> [!NOTE]
> Hestia is being built right now for the Claude Impact Lab hackathon. This README describes the design in
> [`PLAN.md`](PLAN.md); anything not in the code yet is marked *(planned)*.

AI tools made starting and finishing a task almost free, so the day no longer ends on its own. Agents keep running,
there's always one more release to keep up with, and the work gets so technical you lose touch with who it's for.
That's **compression burnout**.

Hestia is for people who build with **Claude Code** every day, and especially for people running several sessions or repos
at once who find it hard to stop.

<p align="center">
  <img src="assets/cycle.svg" alt="The day as a fire: tend it during the day, bank it with /hestia:shutdown, keep it overnight while agents are paused, relight it in the morning with yesterday's list ready." width="900">
</p>

<p align="center"><img src="assets/meander.svg" alt="" width="900"></p>

## Commands

|  | Command | What it does | Time it gives back |
|---|---|---|---|
| <img src="assets/icons/bank.svg" width="40" alt=""> | `/hestia:shutdown [note]` | Saves open work safely, writes tomorrow's list, pauses agents until morning | Manual WIP commits, "where was I?" notes, agents running overnight |
| <img src="assets/icons/resume.svg" width="40" alt=""> | `/hestia:resume` | Lifts the pause early, if it's truly urgent | n/a (the escape hatch) |
| <img src="assets/icons/status.svg" width="40" alt=""> | `/hestia:status` | Whether you're paused, plus today's load: projects, hours, last real break | Guessing how much you've taken on |
| <img src="assets/icons/plain.svg" width="40" alt=""> | `/hestia:plain [target]` | One plain paragraph: who this work helps and what changed for them | Writing updates by hand; losing the why |
| <img src="assets/icons/digest.svg" width="40" alt=""> | `/hestia:digest` | The **one** new Claude Code feature that matters to how you work | Reading every release note |

Two things happen without you asking, and nothing else does:

| | | |
|---|---|---|
| <img src="assets/icons/keep.svg" width="40" alt=""> | **Overnight pause** | While paused, agents can't run tools. You can still type, but Claude can't act. |
| <img src="assets/icons/relight.svg" width="40" alt=""> | **Morning recap** | The first session after the pause starts with yesterday's list, once. |

<p align="center"><img src="assets/meander.svg" alt="" width="900"></p>

## Get started *(planned)*

Inside Claude Code:

```
/plugin marketplace add Lukallo/claude-impact-lab-10-26
/plugin install hestia@hestia
```

Then, at the end of the day:

```
/hestia:shutdown finish the auth bug first
```

There's no config file to write and no account to create. Without Claude Code, the same engine runs as a CLI:
`python plugins/hestia/scripts/hestia.py shutdown`.

## What it touches, and how to undo it

| Hestia writes | Undo |
|---|---|
| A snapshot ref `refs/hestia/<date>` in each repo with unsaved work. Your files, staging area and branch are not changed | `git update-ref -d refs/hestia/<date>` |
| Tomorrow's list in `~/hestia/<date>.md` | Delete the file |
| A pause file in `~/.hestia/pause.json` | `/hestia:resume` |

It never edits your `settings.json`: the plugin's hooks come and go with the plugin.

## What it deliberately doesn't do

No notifications, reminders, streaks, scores or dashboards. Every command runs only when you ask, and the
pause and the morning recap are the only automatic behaviour. A tool that's meant to give time back shouldn't take
attention to use.

<p align="center"><img src="assets/meander.svg" alt="" width="900"></p>

## Why "Hestia"

<img src="assets/logo.svg" width="72" align="right" alt="Hestia logo">

Hestia is the Greek goddess of the hearth. Her one job was to **keep the fire going**: every home and city kept her
flame alight, and it was never allowed to go out. A fire that burns too hot burns out; a fire that's tended,
banked at night and relit in the morning lasts. Hestia doesn't help you burn brighter. It tends the fire.

## Project

- [`PLAN.md`](PLAN.md): the design, the research behind it, and the audit of every feature against the "give time back" rule.
- [`assets/BRAND.md`](assets/BRAND.md): the visual identity, palette and icon rules.
- [`REFERENCE.md`](REFERENCE.md): projects we learned from, and what we took from each.

Built for the Claude Impact Lab hackathon, against the brief's "shutdown command" idea, with a workload tracker and
"learning AI together" folded in.
