"""
Discord Skill API Handler.

Called when requests arrive at:
  POST /api/skill/discord/<action>

Supported actions:
  send   — send a plain text message
  embed  — send a rich embed
  file   — send a file attachment
  list   — list guilds and text channels
"""

from __future__ import annotations

import re
from typing import Any

from loguru import logger


_MAX_MESSAGE_LEN = 2000
_MAX_EMBED_TITLE_LEN = 256
_MAX_EMBED_DESC_LEN = 4096
_VALID_HEX_COLOR = re.compile(r"^#[0-9A-Fa-f]{6}$")


async def handle(
    action: str, data: dict[str, Any], context: dict[str, Any]
) -> dict[str, Any]:
    """
    Route an incoming skill API request to the appropriate handler.

    Args:
        action:  The action name from the URL (e.g. "send", "embed").
        data:    JSON body parsed by the caller.
        context: Runtime context — must contain 'bus'; optionally 'channels'.
    """
    bus = context.get("bus")
    if not bus:
        return {"error": "Message bus not available"}

    if action == "send":
        return await _send_message(data, bus)
    elif action == "embed":
        return await _send_embed(data, bus)
    elif action == "file":
        return await _send_file(data, bus)
    elif action == "list":
        return await _list_channels(context)
    else:
        return {
            "error": f"Unknown action '{action}'. Supported: send, embed, file, list"
        }


def _require(data: dict, *keys: str) -> str | None:
    """Return an error string if any required key is missing/empty, else None."""
    for key in keys:
        if not data.get(key):
            return f"'{key}' is required"
    return None


def _get_discord_client(context: dict):
    """
    Return the ready discord.py Client, or None with a logged warning.
    Avoids duplicating this lookup in every handler.
    """
    channels = context.get("channels", [])
    discord_channel = next((c for c in channels if c.name == "discord"), None)

    if not discord_channel:
        logger.warning("[Discord API] Discord channel not active")
        return None, "Discord channel not active"

    if not hasattr(discord_channel, "client") or not discord_channel.client.is_ready():
        logger.warning("[Discord API] Discord client not ready")
        return None, "Discord client not ready"

    return discord_channel.client, None


def _publish_outbound(bus, channel_id: str, content: str, metadata: dict):
    """Build and publish an OutboundMessage."""
    from core.events import OutboundMessage

    return bus.publish_outbound(
        OutboundMessage(
            content=content,
            channel="discord",
            chat_id=str(channel_id),
            metadata={"from_skill": True, **metadata},
        )
    )


async def _list_channels(context: dict) -> dict[str, Any]:
    """Return all guilds and their text channels."""
    client, err = _get_discord_client(context)
    if err:
        return {"error": err}

    result = []
    logger.info(f"[Discord API] Listing channels across {len(client.guilds)} guild(s)")

    for guild in client.guilds:
        text_channels = [
            {"id": str(ch.id), "name": ch.name, "type": str(ch.type)}
            for ch in guild.channels
            if str(ch.type) == "text"
        ]
        result.append(
            {"id": str(guild.id), "name": guild.name, "channels": text_channels}
        )

    return {"guilds": result}


async def _send_message(data: dict, bus) -> dict[str, Any]:
    """Send a plain text message to a Discord channel."""
    if err := _require(data, "channel_id", "message"):
        return {"error": err}

    channel_id = data["channel_id"]
    message = data["message"]

    if len(message) > _MAX_MESSAGE_LEN:
        return {
            "error": f"Message exceeds Discord's {_MAX_MESSAGE_LEN}-character limit ({len(message)} chars). "
            f"Consider splitting it into multiple messages."
        }

    await _publish_outbound(bus, channel_id, message, {})
    logger.info(f"[Discord API] Text message queued → channel {channel_id}")

    return {
        "status": "sent",
        "channel_id": str(channel_id),
        "preview": message[:80] + ("…" if len(message) > 80 else ""),
    }


async def _send_embed(data: dict, bus) -> dict[str, Any]:
    """Send a rich embed to a Discord channel."""
    if err := _require(data, "channel_id"):
        return {"error": err}

    channel_id = data["channel_id"]
    title = data.get("title", "")
    description = data.get("description", "")
    color = data.get("color", "#5865F2")

    if not title and not description:
        return {"error": "'title' or 'description' is required"}

    if len(title) > _MAX_EMBED_TITLE_LEN:
        return {
            "error": f"Embed title exceeds {_MAX_EMBED_TITLE_LEN} characters ({len(title)})."
        }
    if len(description) > _MAX_EMBED_DESC_LEN:
        return {
            "error": f"Embed description exceeds {_MAX_EMBED_DESC_LEN} characters ({len(description)})."
        }

    if not _VALID_HEX_COLOR.match(color):
        return {"error": f"Invalid color '{color}'. Expected format: #RRGGBB"}

    embed_data = {
        "title": title,
        "description": description,
        "color": color,
        "footer": data.get("footer"),
        "image": data.get("image"),
        "thumbnail": data.get("thumbnail"),
        "fields": data.get("fields", []),
    }

    fallback = title or description[:100]
    await _publish_outbound(bus, channel_id, fallback, {"embed": embed_data})
    logger.info(f"[Discord API] Embed queued → channel {channel_id} (title={title!r})")

    return {"status": "sent", "channel_id": str(channel_id), "embed": embed_data}


async def _send_file(data: dict, bus) -> dict[str, Any]:
    """Send a file attachment to a Discord channel."""
    if err := _require(data, "channel_id", "file_path"):
        return {"error": err}

    from pathlib import Path

    channel_id = data["channel_id"]
    file_path = data["file_path"]
    caption = data.get("caption", "")

    p = Path(file_path).resolve()
    if not p.exists():
        return {"error": f"File not found: {file_path}"}
    if not p.is_file():
        return {"error": f"Path is not a file: {file_path}"}

    max_bytes = 25 * 1024 * 1024
    size = p.stat().st_size
    if size > max_bytes:
        return {
            "error": f"File too large: {size / 1024 / 1024:.1f} MB. Discord limit is 25 MB."
        }

    await _publish_outbound(
        bus,
        channel_id,
        caption,
        {"type": "file", "file_path": str(p), "caption": caption},
    )
    logger.info(
        f"[Discord API] File queued → channel {channel_id}: {p.name} ({size / 1024:.1f} KB)"
    )

    return {
        "status": "sent",
        "channel_id": str(channel_id),
        "file": p.name,
        "size_kb": round(size / 1024, 1),
    }
