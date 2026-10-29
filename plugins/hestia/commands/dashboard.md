---
description: Open today's reading as one static page - the pause, the load, saved work and tomorrow's list
allowed-tools: Bash
---

```
python3 "${CLAUDE_PLUGIN_ROOT}/scripts/hestia.py" dashboard
```

Print the path it gives, and stop there. Do not summarise the page, do not
interpret the numbers, and do not suggest taking a break. The user asked to
look, not for advice - this command is only ever run when they ask for it.
