---
name: discord
description: Manage and interact with Discord servers, channels, and members.
requires_backend: true
dependencies:
  python: []
  node: []
  binaries: []
---

# Discord 🎧
Full integration with Discord for real-time messaging and server administration.

### Commands:
- **Send Message**: `python {baseDir}/main.py send <channel_id> "<message>"`
- **Send Embed**: `python {baseDir}/main.py embed <channel_id> "<title>" "<description>" [color]`
- **List Channels**: `python {baseDir}/main.py list`: See which servers and channels LimeBot has access to.

### Instructions:
- Use the **channel_id** (Enable Developer Mode in Discord to copy these).
- Embeds are great for structured info, logs, or status updates.
