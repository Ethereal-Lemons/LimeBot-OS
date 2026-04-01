"""Telegram channel implementation using the Bot API long-polling interface."""

import asyncio
from typing import Any

import aiohttp
from loguru import logger

from channels.base import BaseChannel
from core.events import OutboundMessage


_TELEGRAM_MAX_MESSAGE_LEN = 4096


class TelegramChannel(BaseChannel):
    """Telegram channel implementation."""

    name = "telegram"

    def __init__(self, config: Any, bus):
        super().__init__(config, bus)
        self.token = getattr(self.config, "token", None)
        self.api_base = (
            getattr(self.config, "api_base", "https://api.telegram.org") or ""
        ).rstrip("/")
        self.poll_timeout = max(1, int(getattr(self.config, "poll_timeout", 30) or 30))
        self.allow_chats = {
            str(chat_id)
            for chat_id in getattr(self.config, "allow_chats", []) or []
            if str(chat_id).strip()
        }
        self._session: aiohttp.ClientSession | None = None
        self._offset: int | None = None
        self._bot_profile: dict[str, Any] | None = None
        self._status = "disconnected"
        self._last_error = ""

    async def start(self) -> None:
        """Start polling Telegram for updates."""
        if not self.token:
            logger.warning("[Telegram] Channel enabled without TELEGRAM_BOT_TOKEN.")
            self._status = "error"
            self._last_error = "Missing TELEGRAM_BOT_TOKEN"
            return

        self._running = True
        self._status = "connecting"
        self._last_error = ""
        timeout = aiohttp.ClientTimeout(total=self.poll_timeout + 15)
        self._session = aiohttp.ClientSession(timeout=timeout)
        backoff = 2

        while self._running:
            try:
                if self._bot_profile is None:
                    await self._refresh_bot_profile()
                updates = await self._get_updates()
                self._status = "connected"
                self._last_error = ""
                backoff = 2
                for update in updates:
                    if not self._running:
                        break
                    await self._handle_update(update)
            except asyncio.CancelledError:
                raise
            except Exception as e:
                self._status = "error"
                self._last_error = str(e)
                logger.error(f"[Telegram] Polling error: {e}")
                if self._running:
                    await asyncio.sleep(backoff)
                    backoff = min(backoff * 2, 30)

    async def stop(self) -> None:
        """Stop polling and close the HTTP session."""
        self._running = False
        self._status = "disconnected"
        if self._session:
            await self._session.close()
            self._session = None

    async def send(self, msg: OutboundMessage) -> None:
        """Send a message to a Telegram chat."""
        if not self.token:
            logger.warning("[Telegram] Missing TELEGRAM_BOT_TOKEN; send skipped.")
            return

        content = (msg.content or "").strip()
        metadata = msg.metadata or {}
        msg_type = metadata.get("type")

        if msg_type == "typing":
            await self._api_call(
                "sendChatAction",
                {"chat_id": str(msg.chat_id), "action": "typing"},
            )
            return
        if msg_type == "stop_typing":
            return

        if "embed" in metadata:
            content = self._flatten_embed(metadata["embed"])

        if not content:
            logger.debug("[Telegram] Ignoring empty outbound message.")
            return

        for chunk in self._split_message(content):
            await self._api_call(
                "sendMessage",
                {
                    "chat_id": str(msg.chat_id),
                    "text": chunk,
                    "disable_web_page_preview": True,
                },
            )

    async def _get_updates(self) -> list[dict[str, Any]]:
        payload: dict[str, Any] = {
            "timeout": self.poll_timeout,
            "allowed_updates": ["message", "edited_message"],
        }
        if self._offset is not None:
            payload["offset"] = self._offset

        result = await self._api_call("getUpdates", payload)
        return result if isinstance(result, list) else []

    async def _handle_update(self, update: dict[str, Any]) -> None:
        update_id = update.get("update_id")
        if isinstance(update_id, int):
            self._offset = update_id + 1

        message = update.get("message") or update.get("edited_message")
        if not isinstance(message, dict):
            return

        text = (message.get("text") or message.get("caption") or "").strip()
        if not text:
            return

        chat = message.get("chat") or {}
        sender = message.get("from") or {}
        chat_id = str(chat.get("id") or "")
        sender_id = str(sender.get("id") or "")

        if not sender_id or not chat_id:
            return
        if not self.is_allowed(sender_id):
            logger.info(f"[Telegram] Sender {sender_id} blocked by allow list.")
            return
        if self.allow_chats and chat_id not in self.allow_chats:
            logger.info(f"[Telegram] Chat {chat_id} blocked by allow list.")
            return

        metadata = {
            "source": "telegram",
            "message_id": message.get("message_id"),
            "chat_type": chat.get("type"),
            "sender_username": sender.get("username"),
            "sender_name": " ".join(
                part
                for part in [sender.get("first_name"), sender.get("last_name")]
                if part
            ).strip(),
        }
        await self._handle_message(
            sender_id=sender_id,
            chat_id=chat_id,
            content=text,
            metadata=metadata,
        )

    async def _api_call(
        self, method: str, payload: dict[str, Any] | None = None
    ) -> Any:
        if not self.token:
            raise RuntimeError("Telegram channel is missing TELEGRAM_BOT_TOKEN.")
        if not self._session:
            timeout = aiohttp.ClientTimeout(total=self.poll_timeout + 15)
            self._session = aiohttp.ClientSession(timeout=timeout)

        url = f"{self.api_base}/bot{self.token}/{method}"
        async with self._session.post(url, json=payload or {}) as response:
            data = await response.json(content_type=None)

        if response.status != 200 or not data.get("ok", False):
            description = data.get("description") or f"HTTP {response.status}"
            raise RuntimeError(f"Telegram API {method} failed: {description}")
        return data.get("result")

    async def _refresh_bot_profile(self) -> dict[str, Any]:
        profile = await self._api_call("getMe")
        self._bot_profile = profile if isinstance(profile, dict) else None
        self._status = "connected"
        self._last_error = ""
        return self._bot_profile or {}

    def get_status(self) -> dict[str, Any]:
        profile = self._bot_profile or {}
        return {
            "enabled": True,
            "status": self._status,
            "connected": self._status == "connected",
            "username": profile.get("username"),
            "bot_id": profile.get("id"),
            "display_name": profile.get("first_name"),
            "can_join_groups": profile.get("can_join_groups"),
            "can_read_all_group_messages": profile.get(
                "can_read_all_group_messages"
            ),
            "supports_inline_queries": profile.get("supports_inline_queries"),
            "last_error": self._last_error,
        }

    @staticmethod
    def _flatten_embed(embed: dict[str, Any]) -> str:
        parts: list[str] = []
        if title := embed.get("title"):
            parts.append(str(title))
        if description := embed.get("description"):
            parts.append(str(description))
        for field in embed.get("fields", []) or []:
            name = str(field.get("name") or "").strip()
            value = str(field.get("value") or "").strip()
            if name and value:
                parts.append(f"{name}: {value}")
            elif value:
                parts.append(value)
        if footer := embed.get("footer"):
            parts.append(str(footer))
        return "\n\n".join(part for part in parts if part).strip()

    @staticmethod
    def _split_message(content: str) -> list[str]:
        if len(content) <= _TELEGRAM_MAX_MESSAGE_LEN:
            return [content]

        chunks: list[str] = []
        remaining = content
        while remaining:
            if len(remaining) <= _TELEGRAM_MAX_MESSAGE_LEN:
                chunks.append(remaining)
                break

            split_at = remaining.rfind("\n", 0, _TELEGRAM_MAX_MESSAGE_LEN)
            if split_at == -1:
                split_at = remaining.rfind(" ", 0, _TELEGRAM_MAX_MESSAGE_LEN)
            if split_at == -1:
                split_at = _TELEGRAM_MAX_MESSAGE_LEN

            chunks.append(remaining[:split_at].rstrip())
            remaining = remaining[split_at:].lstrip()

        return [chunk for chunk in chunks if chunk]
