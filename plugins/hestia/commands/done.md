---
description: Save open work, write tomorrow's list, and pause agents until morning
argument-hint: [one line about what today was for]
allowed-tools: Bash
---

End the working day.

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
lifts, and where tomorrow's list is. Nothing else: no summary of the day, no
suggestions, no offer to keep working. The point of this command is that it ends.

If the script reports a warning, say so plainly rather than retrying.
