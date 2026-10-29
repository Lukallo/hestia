---
description: Show whether you're paused, and what today's load has looked like
allowed-tools: Bash
---

```
python3 "${CLAUDE_PLUGIN_ROOT}/scripts/hestia.py" today
```

Print what it says, and stop there. Do not interpret the numbers, do not
suggest taking a break, and do not compare today to other days. The user asked
for a reading, not advice — this command is only ever run when they ask for it.
