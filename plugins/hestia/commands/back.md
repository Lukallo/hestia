---
description: Lift the pause early and pick up where you left off
allowed-tools: Bash, Read
---

Lift the pause:

```
sh "${CLAUDE_PLUGIN_ROOT}/scripts/hestia" back
```

- If it prints a `Start here:` line, give the user that one thing back in a
  sentence, then the `Overnight:` line if there is one. Read the list it points
  to only if the user asks for more.
- If there is no `Start here:` line but it prints a path to a list, read the
  list and give back the one thing they were in the middle of, in a sentence.
- After a break it prints `Before the break:` lines instead. Name the repo they
  were in, in a sentence.

Do not read the whole list out, and do not start the work until they ask.
