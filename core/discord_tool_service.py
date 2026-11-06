"""Discord-specific tool behavior, routed through LimeBot's outbound bus."""

from __future__ import annotations

import json
import re
from typing import Any, Callable, Dict, List, Optional

from core.context import tool_context
from core.events import OutboundMessage


class DiscordToolService:
    """Validate and publish Discord tool requests through the channel bus."""

    def __init__(self, bus: Any, get_channel_by_name: Callable[[str], Any]) -> None:
        self.bus = bus
        self._get_channel_by_name = get_channel_by_name

    async def send_message(
        self,
        message: str = "",
        channel_id: str = "",
        user_id: str = "",
    ) -> str:
        """Send a plain Discord message to a channel or user DM."""
        ctx = tool_context.get() or {}
        active_channel = (ctx.get("channel") or "").strip().lower()
        origin_chat_id = str(ctx.get("chat_id") or "").strip()
        target_channel = str(channel_id or "").strip()
        target_user = str(user_id or "").strip()

        if target_channel and target_user:
            return "Error: Pass either channel_id or user_id, not both."

        target_id = target_user or target_channel
        target_kind = "user DM" if target_user else "channel"

        if not target_id:
            if active_channel != "discord":
                return (
                    "Error: channel_id or user_id is required when sending a Discord message outside a Discord conversation."
                )
            target_id = str(ctx.get("chat_id") or "").strip()
            target_kind = "current Discord chat"

        if not target_id or not target_id.isdigit():
            return "Error: Discord channel_id or user_id must be numeric."

        message = str(message or "").strip()
        if not message:
            return "Error: message is required."

        await self.bus.publish_outbound(
            OutboundMessage(
                channel="discord",
                chat_id=target_id,
                content=message,
                metadata={
                    "target_type": "dm" if target_user else "channel",
                    "from_tool": "send_discord_message",
                    "origin_channel": active_channel or None,
                    "origin_chat_id": origin_chat_id or None,
                },
            )
        )
        return f"Sent Discord message to {target_kind} {target_id}."

    async def send_embed(
        self,
        title: str = "",
        description: str = "",
        color: str = "#5865F2",
        footer: str = "",
        image: str = "",
        thumbnail: str = "",
        fields: Optional[List[Dict[str, Any]]] = None,
        channel_id: str = "",
        user_id: str = "",
    ) -> str:
        """Validate and send a native Discord embed."""
        ctx = tool_context.get() or {}
        active_channel = (ctx.get("channel") or "").strip().lower()
        origin_chat_id = str(ctx.get("chat_id") or "").strip()
        target_channel = str(channel_id or "").strip()
        target_user = str(user_id or "").strip()

        if target_channel and target_user:
            return "Error: Pass either channel_id or user_id, not both."

        target_id = target_user or target_channel
        target_kind = "user DM" if target_user else "channel"

        if not target_id:
            if active_channel != "discord":
                return (
                    "Error: channel_id or user_id is required when sending a Discord embed outside a Discord conversation."
                )
            target_id = str(ctx.get("chat_id") or "").strip()
            target_kind = "current Discord chat"

        if not target_id or not target_id.isdigit():
            return "Error: Discord channel_id or user_id must be numeric."

        title = str(title or "").strip()
        description = str(description or "").strip()
        if not title and not description:
            return "Error: title or description is required for a Discord embed."
        if title and len(title) > 256:
            return f"Error: Embed title exceeds 256 characters ({len(title)})."
        if description and len(description) > 4096:
            return f"Error: Embed description exceeds 4096 characters ({len(description)})."

        color = str(color or "#5865F2").strip()
        if not re.fullmatch(r"#[0-9A-Fa-f]{6}", color):
            return f"Error: Invalid color '{color}'. Expected #RRGGBB."

        normalized_fields: List[Dict[str, Any]] = []
        for field in fields or []:
            if not isinstance(field, dict):
                return "Error: Each embed field must be an object."
            name = str(field.get("name") or "").strip()
            value = str(field.get("value") or "").strip()
            if not name or not value:
                return "Error: Each embed field requires both name and value."
            normalized_fields.append(
                {
                    "name": name[:256],
                    "value": value[:1024],
                    "inline": bool(field.get("inline", False)),
                }
            )

        embed_data = {
            "title": title,
            "description": description,
            "color": color,
            "footer": str(footer or "").strip() or None,
            "image": str(image or "").strip() or None,
            "thumbnail": str(thumbnail or "").strip() or None,
            "fields": normalized_fields,
        }
        fallback = title or description[:100]
        await self.bus.publish_outbound(
            OutboundMessage(
                channel="discord",
                chat_id=target_id,
                content=fallback,
                metadata={
                    "embed": embed_data,
                    "target_type": "dm" if target_user else "channel",
                    "from_tool": "send_discord_embed",
                    "origin_channel": active_channel or None,
                    "origin_chat_id": origin_chat_id or None,
                },
            )
        )
        return f"Sent native Discord embed to {target_kind} {target_id}."

    async def list_channels(self) -> str:
        """List guild text channels available to the running Discord bot."""
        discord_channel = self._get_channel_by_name("discord")
        if not discord_channel:
            return "Error: Discord channel is not active."

        client = getattr(discord_channel, "client", None)
        if not client or not client.is_ready():
            return "Error: Discord client is not ready."

        guilds_payload: List[Dict[str, Any]] = []
        for guild in getattr(client, "guilds", []) or []:
            text_channels = []
            for channel in getattr(guild, "channels", []) or []:
                if str(getattr(channel, "type", "")) != "text":
                    continue
                text_channels.append(
                    {
                        "id": str(getattr(channel, "id", "")),
                        "name": getattr(channel, "name", ""),
                        "type": str(getattr(channel, "type", "")),
                    }
                )
            guilds_payload.append(
                {
                    "id": str(getattr(guild, "id", "")),
                    "name": getattr(guild, "name", ""),
                    "channels": text_channels,
                }
            )

        return json.dumps({"guilds": guilds_payload}, ensure_ascii=False)
