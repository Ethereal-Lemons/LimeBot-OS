---
name: browser-harness
description: Use Browser Harness for direct control of the user's real Chrome via CDP when a task needs robust browser automation, live navigation, scraping, testing, uploads, or site-specific browser workflows. Prefer this skill over the basic browser skill when the task may need editable browser helpers, domain-specific browser skills, or persistent connection to the user's existing logged-in browser.
dependencies:
  python: []
  node: []
  binaries:
    - browser-harness
---

# Browser Harness

Use the installed Browser Harness checkout at:

`C:\Users\brite\Developer\browser-harness`

This is the real upstream repo checkout. Do not copy helpers into LimeBot. For task-specific browser work, edit:

- `C:\Users\brite\Developer\browser-harness\agent-workspace\agent_helpers.py`
- `C:\Users\brite\Developer\browser-harness\agent-workspace\domain-skills\`

For setup, reconnect, or browser attach failures, read:

- `C:\Users\brite\Developer\browser-harness\install.md`

For normal usage patterns, read:

- `C:\Users\brite\Developer\browser-harness\SKILL.md`

## Core Commands

Check health:

```powershell
browser-harness --doctor
```

Run the interactive attach flow:

```powershell
browser-harness --setup
```

Reload the daemon after helper edits:

```powershell
browser-harness --reload
```

Run a browser task:

```powershell
browser-harness -c '
new_tab("https://example.com")
wait_for_load()
print(page_info())
'
```

## Rules

- First navigation should be `new_tab(url)`, not `goto_url(url)`, unless you intentionally want to reuse the active tab.
- Prefer Browser Harness when the user wants interaction with their real logged-in browser.
- If the task becomes domain-specific, search `agent-workspace/domain-skills/` before inventing a new flow.
- If a useful site-specific pattern is discovered, save it under `agent-workspace/domain-skills/` instead of bloating LimeBot's own skill docs.
- After editing `agent_helpers.py` or any domain skill, run `browser-harness --reload` before the next task.

## Quick Workflow

1. Run `browser-harness --doctor`.
2. If not attached, run `browser-harness --setup`.
3. Read the upstream `SKILL.md` for normal task flow.
4. Use `browser-harness -c '...'` for the task.
5. If Browser Harness learned a reusable site mechanic, store it in `agent-workspace/domain-skills/`.
