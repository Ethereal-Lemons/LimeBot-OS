"""Current-chat media publication and duplicate-delivery tracking."""

from __future__ import annotations

import asyncio
import base64
import mimetypes
import os
import shutil
from pathlib import Path
from typing import Any, Awaitable, Callable, Dict, Optional, Union

from loguru import logger


class MediaDeliveryService:
    """Deliver local and fetched media using Toolbox-owned safety callbacks."""

    def __init__(
        self,
        *,
        get_bus: Callable[[], Any],
        get_project_root: Callable[[], Path],
        resolve_tool_path: Callable[[str], Path],
        is_path_shareable: Callable[[Union[str, Path]], tuple[bool, Optional[str], Optional[Path]]],
        to_display_path: Callable[[Path], str],
        fetch_url_to_temp: Callable[[str], Awaitable[str]],
    ) -> None:
        self.get_bus = get_bus
        self.get_project_root = get_project_root
        self.resolve_tool_path = resolve_tool_path
        self.is_path_shareable = is_path_shareable
        self.to_display_path = to_display_path
        self.fetch_url_to_temp = fetch_url_to_temp
        self._sent_media_by_turn: Dict[str, set[str]] = {}

    async def publish_generated_image_preview(
        self, paths: list[Path], caption: str
    ) -> None:
        """Publish the first generated image as the current chat preview."""
        if not paths:
            return

        from core.context import tool_context
        from core.events import OutboundMessage

        context = tool_context.get() or {}
        channel = (context.get("channel") or "").strip().lower()
        chat_id = (context.get("chat_id") or "").strip()
        if not channel or not chat_id:
            return

        first_path = paths[0]
        if channel in {"discord", "whatsapp"}:
            await self.get_bus().publish_outbound(
                OutboundMessage(
                    channel=channel,
                    chat_id=chat_id,
                    content="",
                    metadata={
                        "type": "file",
                        "file_path": str(first_path),
                        "caption": caption,
                    },
                )
            )
            return

        if channel == "web":
            try:
                await self._publish_web_media(first_path, caption)
            except Exception as exc:
                logger.warning(f"Failed to publish generated image preview: {exc}")

    async def _publish_web_media(self, file_path: Path, caption: str) -> None:
        """Publish a local file to the web chat as a servable attachment."""
        from core.context import tool_context
        from core.events import OutboundMessage

        ctx = tool_context.get() or {}
        chat_id = (ctx.get("chat_id") or "").strip()
        if not chat_id:
            return

        # Ensure the file lives under temp/ so the /temp static mount can serve it.
        temp_root = (self.get_project_root() / "temp").resolve()
        try:
            file_path.resolve().relative_to(temp_root)
            servable = file_path
        except Exception:
            dl_dir = self.get_project_root() / "temp" / "downloads"
            await asyncio.to_thread(
                lambda: dl_dir.mkdir(parents=True, exist_ok=True)
            )
            servable = dl_dir / file_path.name
            await asyncio.to_thread(shutil.copyfile, file_path, servable)

        mime_type = mimetypes.guess_type(servable.name)[0] or "application/octet-stream"
        is_image = mime_type.startswith("image/")
        url = ""
        try:
            rel = servable.resolve().relative_to(temp_root)
            url = f"/temp/{rel.as_posix()}"
        except Exception:
            if is_image:
                try:
                    blob = await asyncio.to_thread(servable.read_bytes)
                    url = (
                        f"data:{mime_type};base64,"
                        f"{base64.b64encode(blob).decode('ascii')}"
                    )
                except Exception:
                    url = ""

        attachment = {
            "name": servable.name,
            "kind": "image" if is_image else "document",
            "mime_type": mime_type,
            "mimeType": mime_type,
            "path": self.to_display_path(servable),
            "url": url,
        }
        metadata: Dict[str, Any] = {"attachments": [attachment]}
        if is_image and url:
            metadata["image"] = url
        turn_id = str(ctx.get("turn_id") or "").strip()
        message_id = str(ctx.get("message_id") or "").strip()
        if turn_id:
            metadata["turn_id"] = turn_id
        if message_id:
            metadata["message_id"] = message_id
        await self.get_bus().publish_outbound(
            OutboundMessage(
                channel="web", chat_id=chat_id, content=caption, metadata=metadata
            )
        )

    async def send_media(self, path: str, caption: str = "") -> str:
        """Share a local file OR a remote http(s) URL into the current chat.

        Works for web, Discord, and WhatsApp. Remote URLs are downloaded into
        temp/downloads first (SSRF-guarded), then delivered as a local file, so
        the agent can act on image URLs found via web_search.
        """
        from core.context import tool_context
        from core.events import OutboundMessage

        ctx = tool_context.get() or {}
        channel = (ctx.get("channel") or "").strip().lower()
        chat_id = (ctx.get("chat_id") or "").strip()

        if channel not in {"discord", "whatsapp", "web"}:
            return (
                "Error: send_media is only available in web, Discord, or WhatsApp "
                "conversations."
            )
        if not chat_id:
            return "Error: Missing current chat context for media delivery."

        source = str(path or "").strip()
        if not source:
            return "Error: A local file path or http(s) URL is required."

        turn_id = str(ctx.get("turn_id") or "").strip()
        if source.lower().startswith(("http://", "https://")):
            media_fingerprint = source
        else:
            try:
                media_fingerprint = os.path.normcase(
                    str(self.resolve_tool_path(source))
                )
            except Exception:
                media_fingerprint = os.path.normcase(os.path.normpath(source))

        delivered_this_turn = self._sent_media_by_turn.get(turn_id, set())
        if turn_id and media_fingerprint in delivered_this_turn:
            return (
                "ACTION BLOCKED: Duplicate send_media suppressed because this "
                "file was already delivered during the current turn. Continue "
                "with the task or reply in text instead of sending it again."
            )

        if source.lower().startswith(("http://", "https://")):
            downloaded = await self.fetch_url_to_temp(source)
            if downloaded.startswith("Error:"):
                return downloaded
            path = downloaded

        ok, error, resolved = self.is_path_shareable(path)
        if not ok or resolved is None:
            return f"Error: {error}"

        caption = str(caption or "").strip()

        outbound_meta: Dict[str, Any] = {
            "type": "file",
            "file_path": str(resolved),
            "caption": caption,
        }
        if turn_id:
            outbound_meta["turn_id"] = turn_id
        message_id = str(ctx.get("message_id") or "").strip()
        if message_id:
            outbound_meta["message_id"] = message_id

        if channel == "web":
            await self._publish_web_media(resolved, caption)
            if turn_id:
                self._remember_sent_media(turn_id, media_fingerprint)
            return f"Displayed '{self.to_display_path(resolved)}' in the web chat."

        await self.get_bus().publish_outbound(
            OutboundMessage(
                channel=channel,
                chat_id=chat_id,
                content="",
                metadata=outbound_meta,
            )
        )
        if turn_id:
            self._remember_sent_media(turn_id, media_fingerprint)
        display_path = self.to_display_path(resolved)
        return f"Sent '{display_path}' to the current {channel} chat."

    def _remember_sent_media(self, turn_id: str, fingerprint: str) -> None:
        """Remember successful media deliveries without growing forever."""
        bucket = self._sent_media_by_turn.setdefault(turn_id, set())
        bucket.add(fingerprint)
        while len(self._sent_media_by_turn) > 128:
            oldest_turn = next(iter(self._sent_media_by_turn))
            self._sent_media_by_turn.pop(oldest_turn, None)

    def media_delivered_this_turn(self, turn_id: str) -> bool:
        """True when host or send_media already delivered a file this turn."""
        tid = str(turn_id or "").strip()
        if not tid:
            return False
        return bool(self._sent_media_by_turn.get(tid))
