---
name: browser-harness
description: Always use Browser Harness for web interaction that needs robust automation, downloads, uploads, screenshots, testing, or the user's live Chrome session.
dependencies:
  python: []
  node: []
  binaries:
    - browser-harness
---

# Browser Harness

Browser Harness controls Chrome directly through CDP. The default upstream checkout is at:

`$env:USERPROFILE\Developer\browser-harness`

The installed CLI is the normal entry point. The checkout is available for its current
`SKILL.md`, `install.md`, interaction guides, and source diagnostics.

## Usage from PowerShell

Pass Python helpers on standard input; `-c` and `--setup` are not valid commands:

```powershell
@'
new_tab("https://example.com")
wait_for_load()
print(page_info())
'@ | browser-harness
```

Helpers are pre-imported. The daemon starts automatically on the first command.
Use `new_tab(url)` for the first navigation and call `wait_for_load()` afterward.

## Health and connection

```powershell
browser-harness --doctor
browser-harness --reload
browser-harness recordings
```

If local Chrome cannot attach, open `chrome://inspect/#remote-debugging`, ask the user
to enable **Allow remote debugging for this browser instance**, and wait for the user
to approve Chrome's popup. Do not claim Browser Harness is unavailable until
`browser-harness --doctor` or an actual harness command reports the failure.

## Downloads, screenshots, and advanced interaction

Before inventing mechanics, read the relevant upstream guide under:

`$env:USERPROFILE\Developer\browser-harness\interaction-skills\`

Important guides include `downloads.md`, `screenshots.md`, `uploads.md`, `tabs.md`,
`dialogs.md`, and `print-as-pdf.md`.

For a site-specific flow, inspect the official examples under
`$env:USERPROFILE\Developer\browser-harness\agent-workspace\domain-skills\`.
Task-specific helpers belong in Browser Harness's configured agent workspace, not in
LimeBot core.

## Recording privacy

Recordings are disabled by default. Preserve the user's configured preference. Never
enable background recordings without explicit consent.
