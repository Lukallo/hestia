# References

Projects we looked at while designing Hestia, and what we took from each.

## [Graphify-Labs/graphify](https://github.com/Graphify-Labs/graphify)

A `/graphify` skill for Claude Code (and other assistants) that turns a codebase into a queryable knowledge graph.

**What we took: the README.** A centred logo, a short row of badges, one bold sentence saying what it does, a hero
image, a 30-second install, then "see it in action" with real output and a capability table. Hestia's README
follows the same order, so a stranger knows what it is, who it's for and how to start within one screen.

## [gsd-build/gsd-2 › gsd-orchestrator](https://github.com/gsd-build/gsd-2/tree/main/gsd-orchestrator)

A skill that drives GSD, a headless CLI that plans, codes, tests and commits from a spec, as a subprocess, so an
agent can build software autonomously for long stretches.

**What we took:**
- **Skill layout.** A short `SKILL.md` that routes to `workflows/`, `references/` and `templates/` instead of one
  long prompt. Hestia's commands follow the same pattern: a short instruction file that calls a script for the
  facts.
- **A plain contract between agent and script.** It uses exit codes (`0` success, `1` error, `10` blocked) and JSON
  on stdout with progress on stderr. Hestia's hook and `hestia.py` use the same rules, so Claude Code and humans can
  both read the result.
- **Cheap status checks.** GSD's `query` answers in about 50 ms with no LLM call. Hestia's pause hook is built
  the same way: it exits instantly when there's no pause, so it costs nothing on a normal day.

**Why it matters to Hestia:** this is exactly the kind of long-running autonomous agent that keeps working after
you've stopped. Hestia's overnight pause is the counterweight. It lets tools like this run all day and makes sure
they stop when you do.
