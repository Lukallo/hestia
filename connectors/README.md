# Connectors and plugins: what Hestia could work with

> Research, not built. Checked on 29 Oct 2026 against Anthropic's connector directory (1,040 connectors, 528 plugins), the claude.ai connector registry and the Claude Code docs. Sources are at the bottom.

Every idea here has to pass [the one rule](../PLAN.md#the-one-rule): **it gives you time back, and never helps you do more.** A connector that only adds reading (an inbox count, a feed) fails it, however popular it is.

## How Hestia would use them

- **No bundling needed.** Claude Code already sees your claude.ai connectors, listed as `claude.ai Slack` and so on. Hestia's commands can use a connector when you have it and skip it when you don't, so none of this is ever required.
- **One part already works.** The pause hook matches every tool (`"matcher": "*"`), connector tools included. While you're clocked out, an agent can't send a Slack message or an email at 23:00. That's worth a line in the README and the pitch.
- **Plugins can bundle their own servers** in `.mcp.json`, for people who don't use claude.ai connectors. Hestia shouldn't need to.

## Default connectors

Anthropic's own Google Workspace connectors, available to every Claude user. On Team and Enterprise plans, an owner has to enable them first.

| Connector | What Hestia could do | Fit |
|---|---|---|
| **Google Calendar** | Resume at your first meeting instead of a fixed 08:00. Put a "Lunch" block in your calendar so nobody books over it. Mention tomorrow's first meeting in the recap | **High** |
| Gmail | Already covered, since paused agents can't send. Could also save an email you ask for after `done` as a draft for the morning (part of park) | Medium |
| Google Drive, Docs, Sheets, Slides | Save tomorrow's list to a Doc so it's on your phone. Not needed, since the list is already a file | Low |

## Most popular

The directory's "Top connectors", in its order: Google Drive, Gmail, Google Calendar, Canva, Microsoft 365, Notion, Figma, Slack, Atlassian, HubSpot, Linear, monday.com. The Google ones are covered above.

| Connector | What Hestia could do | Fit |
|---|---|---|
| **Slack** | Set your status to "Clocked out, back 08:00" on `done` and clear it on `back`. **Check first:** its listed tools cover sending, searching, reading and canvases, and none of them obviously sets a status or do-not-disturb (3 tools aren't listed) | High, if possible |
| **Linear** | Build tomorrow's list from your in-progress issues, and count issues closed today toward the finish line | High |
| **Atlassian (Jira)** | Same as Linear, for Jira | High |
| Microsoft 365 | Read your Outlook calendar for tomorrow's first meeting. Its tools are **search-only**, so it can't block lunch or set a Teams status | Medium |
| Notion | Write tomorrow's list to a Notion page | Medium |
| monday.com | Same as Linear | Medium |
| Canva, Figma, HubSpot | Nothing that gives time back | None |

Further down the full listing come Asana (same as Linear, Medium), and then Box, Supabase, Shopify, Vercel, Zoom, Granola and Adobe, none of which has a real use here.

## Not top, but a good fit

| Connector | What Hestia could do | Fit |
|---|---|---|
| **Reclaim.ai** | A scheduling tool with "find open time" and event tools. The best way to actually protect lunch and the end of the day on your calendar | **High** |
| Todoist, ClickUp, TickTick | Tomorrow's list from your tasks | Medium |

## Plugins

- **Defaults:** Claude Code adds Anthropic's official marketplace (`claude-plugins-official`) automatically, but you still choose and install plugins yourself.
- **Most popular:** frontend-design, Superpowers, code-review, context7, code-simplifier and playwright. None of them has a feature to connect to. What matters is that Hestia **works alongside them**: the pause blocks their tools like any other. Superpowers also hooks into session start, as Hestia's recap does, so test the two together.
- **Ones that matter for Hestia:**
  - **linear, atlassian, asana and github** in the official marketplace bring the same services into Claude Code for people who don't use claude.ai connectors.
  - **discord and imessage** let people message your Claude session. While you're paused, those messages hit the pause like everything else.
  - **Cowork's `productivity` plugin** handles tasks, calendars and daily workflows across Slack, Notion, Asana, Linear, Jira and Microsoft 365. It's Hestia's closest neighbour: it helps you run the day, and Hestia helps you end it. That's a useful contrast for the pitch.

## Recommendation

1. **Google Calendar.** Everyone has it, and "resume at your first meeting" plus a lunch block are clear time-back wins.
2. **Slack status,** but only if the connector can set one. Check that first.
3. **Linear or Jira** to fill tomorrow's list.

And state in the README that the pause already stops connector sends overnight.

## Open questions

- Can the Slack connector set a status or do-not-disturb? Its 3 unlisted tools might.
- Can a Hestia command name a connector's tools directly, or only ask Claude to use one if it's there? The Claude Code docs don't say how connector tools are named for `allowed-tools`.
- Calendar changes write to other people's view of your day. A lunch block is visible to colleagues, so it should be opt-in, like lunch itself.

## Sources

- [Claude connectors directory](https://claude.com/connectors): top connectors, top plugins, totals
- [Use Google Workspace connectors (Claude Help Center)](https://support.claude.com/en/articles/10166901-use-google-workspace-connectors): the default connectors and who gets them
- [Claude Code MCP docs](https://code.claude.com/docs/en/mcp): claude.ai connectors in Claude Code, plugin `.mcp.json`
- [anthropics/claude-plugins-official](https://github.com/anthropics/claude-plugins-official): the official plugin marketplace
- [anthropics/knowledge-work-plugins](https://github.com/anthropics/knowledge-work-plugins): Cowork plugins, including `productivity`
- Tool lists for Slack, Microsoft 365, Linear, Reclaim.ai and others come from the claude.ai connector registry
