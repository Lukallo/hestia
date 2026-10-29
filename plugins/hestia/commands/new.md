---
description: The one thing in Claude Code's recent changes worth knowing, in five lines
allowed-tools: Bash
---

```
python3 "${CLAUDE_PLUGIN_ROOT}/scripts/hestia.py" new --json
```

- If `ok` is false, say in one line that the changelog couldn't be reached, so
  there's nothing to do. Stop.
- If `releases` is empty, say in one line that nothing has changed since the
  version shown in `since`. Stop.

Otherwise pick **exactly one** change: the one most relevant to how this user
works, judged from this session, the project in front of you and their
settings. Prefer something that saves them time over something that adds a
new thing to do. Then answer in **five lines at most**:

1. what it is, in a sentence;
2. why it matters for the way they work;
3. how to use it, as one command or setting if there is one;
4. if its release is marked `installed: false`, that it needs an update first.

Do not list the other changes, do not count them, and do not update, install or
configure anything. Showing one thing is the whole point.
