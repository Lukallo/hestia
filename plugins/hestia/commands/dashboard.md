---
description: Open today's reading as one static page - the pause, the load, saved work and tomorrow's list. Off on a computer with no screen
allowed-tools: Bash
---

```
sh "${CLAUDE_PLUGIN_ROOT}/scripts/hestia" dashboard
```

Print the path it gives, and stop there. Do not summarise the page, do not
interpret the numbers, and do not suggest taking a break. The user asked to
look, not for advice - this command is only ever run when they ask for it.

If it says there is no dashboard on this computer, print its two lines and
stop. Don't write or open the page some other way, and don't run
`/hestia:today` for them.
