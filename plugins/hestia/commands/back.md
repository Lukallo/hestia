---
description: Lift the pause early and pick up where you left off
allowed-tools: Bash, Read
---

Lift the pause:

```
sh "${CLAUDE_PLUGIN_ROOT}/scripts/hestia" back
```

If the command prints a path to a list, read it and give the user back the one
thing they were in the middle of — the first item, in a sentence. Do not read
the whole list out, and do not start the work until they ask.
