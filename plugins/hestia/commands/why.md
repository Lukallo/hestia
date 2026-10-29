---
description: One plain paragraph on who today's work helps and what changed for them
argument-hint: [who it's for, e.g. "my manager" | a repo path]
allowed-tools: Bash
---

Gather the facts first. Never write this from memory alone:

```
python3 "${CLAUDE_PLUGIN_ROOT}/scripts/hestia.py" why --json
```

If `$ARGUMENTS` is a path to a folder, add `--repo "<that path>"`. If it names a
person or audience instead, write for them. With no argument, write it for the
user themselves.

Then write **one paragraph, three to five sentences**:

- who this work helps, and what is different for them now;
- in plain language a non-technical person could follow: no commit hashes, no
  file names, no jargon;
- built only from the commits, open changes, prompts and note in the output.
  Do not invent outcomes, users or numbers that aren't there. If the work is
  unfinished, say what it is on its way to.

If the output shows no commits, no open changes and no prompts, say in one line
that there's nothing to go on yet, and stop. Print only the paragraph: no
heading, no bullet list, no offer to send or post it anywhere.
