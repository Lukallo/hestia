---
description: Save open work, write tomorrow's list, and pause agents until morning
argument-hint: [one line about what today was for]
allowed-tools: Bash, Read, Edit
---

End the working day.

## 1. Tidy Claude's memory first

The pause blocks every tool once `done` runs, so do this before it. Run:

```
sh "${CLAUDE_PLUGIN_ROOT}/scripts/hestia" dream plan --json
```

If `skipped` is set, or `projects` is empty, skip straight to step 2 and say
nothing about memory. Otherwise it has already backed up each memory folder.
For each project, tidy that folder using only what the output and this session
show. Make **at most ten changes** per project, and skip anything you're unsure of:

- **Merge duplicates.** For each pair in `duplicates`, fold the content of one
  into the other, delete the emptied file, and drop its line from `MEMORY.md`.
- **Remove what today contradicted.** Delete an entry only when
  `prompts_today` or this session clearly shows it is no longer true.
- **Fix the index.** Remove `MEMORY.md` lines listed in
  `index_points_to_missing`, and add a one-line entry for each file in
  `missing_from_index`. If `over_load_limit` is true, shorten index lines,
  because only the first 200 lines load.

Only edit files inside the listed `folder`s. **Never create a new memory file,
and never add a fact that wasn't already there.** Then check the result. A
failed check rolls that folder back on its own:

```
sh "${CLAUDE_PLUGIN_ROOT}/scripts/hestia" dream check --json --backup "<backup>"
```

## 2. Clock out

Run this once, with two short lines written from what this session has been
about. Use plain language and no jargon.

- **The note:** what today was actually for and who the work helps, in under
  about fifteen words. If the user gave one in `$ARGUMENTS`, use theirs.
- **The next step:** the single first thing to do when work starts again,
  concrete enough to act on without reading anything else. Name the file,
  test or decision, for example "rerun the importer test, then fix the
  semicolon case in parse_row". If the session ended mid-task, it's the next
  move in that task. If everything was finished, it's what the user said comes
  next, or leave `--next` out rather than invent one.

```
sh "${CLAUDE_PLUGIN_ROOT}/scripts/hestia" done --note "<the note>" --next "<the next step>"
```

Then report back in **three lines at most**: what got saved, when the pause
lifts, and where tomorrow's list is. If step 1 changed anything, add it to the
first line in a few words, for example "memory tidied: 2 merged, 1 removed
(undo: hestia dream restore)". Nothing else: no summary of the day, no
suggestions, no offer to keep working. The point of this command is that it ends.

If the script reports a warning, say so plainly rather than retrying.
