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
- **Send Direct Message**: use the native `send_discord_message` tool with `user_id` and `message`.
- **Send Embed**: `python {baseDir}/main.py embed <channel_id> "<title>" "<description>" [color]`
- **List Channels**: `python {baseDir}/main.py list`: See which servers and channels LimeBot has access to.
- **Leave Guild**: `python {baseDir}/main.py leave <guild_id>`
- **Fetch History**: `python {baseDir}/main.py history <channel_id> [limit]`

### Instructions:
- Use the **channel_id** (Enable Developer Mode in Discord to copy these).
- For DMs, use a Discord **user_id** with the native `send_discord_message` tool. Discord privacy settings may still block delivery if the bot cannot DM that user.
- Embeds are great for structured info, logs, or status updates.
