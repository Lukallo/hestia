---
description: Save open work, write tomorrow's list, and pause agents until morning
argument-hint: [one line about what today was for]
allowed-tools: Bash
---

End the working day.

Run this once, with a short note about what today was actually for. If the user
gave one in `$ARGUMENTS`, use it. Otherwise write one yourself from what this
session has been about — plain language, who the work helps, no jargon. Keep it
under about fifteen words.

```
python3 "${CLAUDE_PLUGIN_ROOT}/scripts/hestia.py" shutdown --note "<the note>"
```

Then report back in **three lines at most**: what got saved, when the pause
lifts, and where tomorrow's list is. Nothing else — no summary of the day, no
suggestions, no offer to keep working. The point of this command is that it ends.

If the script reports a warning, say so plainly rather than retrying.
