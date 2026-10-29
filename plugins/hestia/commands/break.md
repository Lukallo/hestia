---
description: Take a break - save where you are and pause agents until it ends
argument-hint: [45m | 1h30m | 13:50]
allowed-tools: Bash
---

Start a break. If the user gave a length in `$ARGUMENTS` (like `45m`, `1h30m`,
`90` or an end time like `13:50`), pass it through. Otherwise leave it out and
the break lasts 45 minutes.

```
python3 "${CLAUDE_PLUGIN_ROOT}/scripts/hestia.py" break <length if given>
```

Report back in **two lines at most**: when the break ends, and what got saved.
Nothing else. Do not suggest what to do on the break, do not summarise the
session, and do not offer to keep working. The break lifts itself when it ends.
`/hestia:back` ends it early.

If the script says the length can't be read or is too long, repeat its one-line
reason and stop. If it says the user is already clocked out, say so and stop.
