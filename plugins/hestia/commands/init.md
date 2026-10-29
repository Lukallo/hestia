---
description: Set Hestia up once: learn your day and why you build, and which connectors it may use
argument-hint: [remove]
allowed-tools: Bash, AskUserQuestion
---

Set Hestia up for this person. It should take about a minute and never come back
to nag. Find out everything the machine already knows, then ask only the rest.

If `$ARGUMENTS` is `remove`, run this, print what it says, and stop:

```
python3 "${CLAUDE_PLUGIN_ROOT}/scripts/hestia.py" init remove
```

## 1. Look before asking

List the MCP servers you can see in your own tools: every tool named
`mcp__<server>__<tool>` gives a `<server>` (for example `claude_ai_Slack`). Pass
them, comma separated, as `<servers>` (empty if there are none). Then run:

```
python3 "${CLAUDE_PLUGIN_ROOT}/scripts/hestia.py" init detect --json --seen "<servers>"
```

It returns their git name, GitHub login, usual hours from recent sessions, the
current config, any earlier answers (`profile`), the connectors found, and
whether this computer seems to have a screen (`has_screen`).

## 2. One screen of questions

Ask these four in **one** `AskUserQuestion` call. Each one
can be skipped, and the first option is always the default. If `profile` or
`config` already has an answer, make that the first option, labelled "Keep: …".

1. **Your day** (header "Your day"). The default is the current config (normally Mon–Fri,
   back at 08:00). If `hours.usual_start` is known, offer "Mon–Fri, back at <that
   hour>" too, plus one other sensible choice.
2. **Why you build** (header "Why"). Options: "Making things people use",
   "The craft itself", "A cause or a group of people". The user can type their own.
3. **Who it helps right now** (header "Helps"). Offer up to two short guesses
   based on the projects in this session, plus "Skip for now". The user can type their own.
4. **How Hestia talks to you** (header "Tone"). Options: "Short and plain", "Warm
   but brief", "More detail when I ask how".

Keep the wording plain. This person may not be technical, so don't say "MCP" or "config".

## 3. A second screen, only if needed

At most two more questions, in **one** `AskUserQuestion` call. If neither
applies, skip this step completely and say nothing about it.

**A screen** (header "Screen"). Ask only when `has_screen` is false, or when
`config.screen` is already `true` or `false`. Otherwise leave it out. Ask "Does
this computer have a screen you can see?" with these options, and make "Keep: …"
first if `config.screen` is set:
- "No screen here": a server or a remote login. Hestia won't open pages here, so
  the dashboard is off. `/hestia:today` gives the same reading.
- "It has a screen": `/hestia:dashboard` opens its page in the browser.

**Connectors.** Only ask about connectors whose `kind` is calendar, chat, tasks
or email, because those are the ones Hestia has a use for. If there are none,
leave this question out and say nothing about connectors. Never suggest
installing one.

Otherwise ask one multi-select question: "Hestia found these. Which may it
use? It only acts when you run a Hestia command." Each option is the connector's
name, with a description of what Hestia would do with it:
- calendar: resume at your first meeting and keep lunch free
- chat: set your status when you clock out
- tasks: start tomorrow's list from your open tasks
- email: keep emails you ask for after `done` as drafts for the morning

The ones they pick are `allowed`. The ones they leave unpicked are `declined`.

## 4. Save

Build the answers as JSON and pass it on stdin. Leave out any field the user skipped:

```
python3 "${CLAUDE_PLUGIN_ROOT}/scripts/hestia.py" init write --seen "<servers>" --answers - <<'JSON'
{"why": "...", "helps": "...", "tone": "plain|warm|detail",
 "resume_hour": 8, "working_days": [0, 1, 2, 3, 4], "screen": true,
 "connectors": {"<connector key>": "allowed|declined"}}
JSON
```

`working_days` uses 0 for Monday. `screen` is `false` for "No screen here", and
left out if it wasn't asked. Then print the script's three lines and stop: no
summary, no tips, no offer to do more.
