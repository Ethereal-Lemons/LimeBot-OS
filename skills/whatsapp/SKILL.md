---
name: whatsapp
description: Send files and media directly to WhatsApp conversations.
dependencies:
  python: []
  node: []
  binaries: []
---

# WhatsApp 📱
Bridge the gap between your local files and your mobile conversations.

### Usage:
`run_command("python skills/whatsapp/main.py 'file_path' 'jid' 'caption'")`

### Parameters:
- `file`: The absolute path to the local file you want to share.
- `jid`: The **exact `chat_id`** from the current conversation metadata (e.g., `123@s.whatsapp.net` or `123@lid`). 
  - *Note: Use the ID provided in your system context exactly. Do not ask the user for their phone number or ID.*
- `caption`: An optional text message to accompany the file.

### Security:
- Forbids sending sensitive system files (like `.env`, `IDENTITY.md`, or `SOUL.md`).
- Only works for approved contacts in the LimeBot whitelist.
