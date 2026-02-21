"""WhatsApp channel implementation using Node.js bridge."""

import asyncio
import json
from pathlib import Path
from typing import Any
from loguru import logger

from channels.base import BaseChannel
from core.events import OutboundMessage
from core.bus import MessageBus


_FORBIDDEN_FILENAMES = frozenset(
    {
        "IDENTITY.md",
        "SOUL.md",
        "MEMORY.md",
        ".env",
        "limebot.json",
        "id_rsa",
        "AGENTS.md",
    }
)

CONTACTS_PATH = Path.cwd() / "data" / "contacts.json"
_EMPTY_CONTACTS: dict = {"allowed": [], "pending": [], "blocked": [], "identities": {}}


class WhatsAppChannel(BaseChannel):
    """
    WhatsApp channel that connects to a Node.js bridge.
    """

    name = "whatsapp"

    def __init__(self, config: Any, bus: MessageBus):
        super().__init__(config, bus)
        self._ws = None
        self._connected = False
        self._self_id: str | None = None

    async def start(self) -> None:
        """Start the WhatsApp channel by connecting to the bridge."""
        import websockets

        bridge_url = getattr(self.config, "bridge_url", "ws://127.0.0.1:3000")
        logger.info(f"Connecting to WhatsApp bridge at {bridge_url}...")

        self._running = True

        while self._running:
            try:
                async with websockets.connect(bridge_url) as ws:
                    self._ws = ws
                    self._connected = True
                    logger.info("Connected to WhatsApp bridge")

                    async for message in ws:
                        try:
                            await self._handle_bridge_message(message)
                        except Exception as e:
                            logger.error(f"Error handling bridge message: {e}")

            except asyncio.CancelledError:
                break
            except Exception as e:
                self._connected = False
                self._ws = None
                logger.error(f"WhatsApp bridge connection error: {e}")

                if self._running:
                    logger.info("Reconnecting in 5 seconds...")
                    await asyncio.sleep(5)

    async def stop(self) -> None:
        """Stop the WhatsApp channel."""
        self._running = False
        self._connected = False

        if self._ws:
            await self._ws.close()
            self._ws = None

    async def send(self, msg: OutboundMessage) -> None:
        """Send a text message through WhatsApp."""
        if not self._is_connected():
            return

        try:
            content = msg.content
            metadata = msg.metadata or {}

            if "embed" in metadata:
                embed = metadata["embed"]
                parts = []
                if title := embed.get("title"):
                    parts.append(f"*{title}*")
                if desc := embed.get("description"):
                    parts.append(desc)

                for field in embed.get("fields", []):
                    name = field.get("name", "")
                    val = field.get("value", "")
                    # WhatsApp doesn't support language tags in code blocks
                    val = val.replace("```bash\n", "```\n").replace(
                        "```json\n", "```\n"
                    )
                    # If it's just an inline code block, keep it on same line. If block, use newline.
                    if val.startswith("```"):
                        parts.append(f"*{name}:*\n{val}")
                    else:
                        parts.append(f"*{name}:* {val}")

                if footer := embed.get("footer"):
                    parts.append(f"_{footer}_")

                if parts:
                    content = "\n\n".join(parts)

            if not content:
                logger.warning("[WhatsApp] Attempted to send empty message, skipping.")
                return

            payload = {"type": "send", "to": msg.chat_id, "text": content}
            await self._ws.send(json.dumps(payload))
        except Exception as e:
            logger.error(f"Error sending WhatsApp message: {e}")

    async def send_file(
        self, to: str, file_path: str, caption: str | None = None
    ) -> bool:
        """Send a file through WhatsApp.

        Args:
            to: Recipient JID (e.g., "1234567890@s.whatsapp.net")
            file_path: Absolute path to the file to send
            caption: Optional caption for the file

        Returns:
            True if sent successfully, False otherwise
        """
        if not self._is_connected():
            return False

        p = Path(file_path).resolve()

        if p.name in _FORBIDDEN_FILENAMES or p.name.endswith(".env"):
            logger.warning(
                f"[WhatsApp Security] Blocked attempt to send sensitive file by name: {file_path}"
            )
            return False

        if "persona" in p.parts:
            logger.warning(
                f"[WhatsApp Security] Blocked attempt to send file inside persona dir: {file_path}"
            )
            return False

        if not p.exists():
            logger.warning(f"[WhatsApp] File not found, aborting send: {file_path}")
            return False

        try:
            payload = {
                "type": "sendFile",
                "to": to,
                "filePath": str(p),
                "caption": caption,
            }
            await self._ws.send(json.dumps(payload))
            logger.info(f"[WhatsApp] File sent: {p} to {to}")
            return True
        except Exception as e:
            logger.error(f"Error sending WhatsApp file: {e}")
            return False

    async def reset_session(self) -> bool:
        """Reset WhatsApp session via bridge."""
        if not self._is_connected():
            return False

        try:
            await self._ws.send(json.dumps({"type": "reset"}))
            logger.info("[WhatsApp] Session reset command sent")
            return True
        except Exception as e:
            logger.error(f"Error resetting WhatsApp session: {e}")
            return False

    async def _handle_bridge_message(self, raw: str) -> None:
        """Handle a message from the bridge."""
        try:
            data = json.loads(raw)
            logger.debug(f"[WhatsApp] DEBUG: Data received: {data}")
        except json.JSONDecodeError as e:
            logger.warning(f"[WhatsApp] Received malformed JSON from bridge: {e}")
            return

        msg_type = data.get("type")

        if msg_type == "message":
            await self._handle_incoming_message(data)

        elif msg_type == "status":
            await self._handle_status(data)

        elif msg_type == "qr":
            await self._handle_qr(data)

        else:
            logger.debug(f"[WhatsApp] Unknown bridge message type: {msg_type!r}")

    async def _handle_incoming_message(self, data: dict) -> None:
        sender = data.get("sender", "")
        sender_alt = data.get("senderAlt")
        content = data.get("content", "")

        if not content:
            logger.debug("[WhatsApp] Received empty message, skipping.")
            return

        phone_source = sender_alt if sender_alt and sender.endswith("@lid") else sender
        phone_number = (
            phone_source.split("@")[0] if "@" in phone_source else phone_source
        )

        logger.info(
            f"[WhatsApp] Message from: {phone_number} (Original: {sender}), content: {content[:50]}..."
        )

        push_name = data.get("pushName")
        verified_name = data.get("verifiedName")

        try:
            if not self._check_contact_allowed(
                phone_number, push_name, verified_name, alt_id=sender_alt
            ):
                return
        except Exception as e:
            logger.error(f"[WhatsApp] Error checking contact: {e}")
            return

        logger.debug(
            f"[WhatsApp] Processing message from {phone_number} ({push_name or 'No Name'})"
        )

        await self._handle_message(
            sender_id=phone_number,
            chat_id=sender_alt if sender_alt and sender.endswith("@lid") else sender,
            content=content,
            metadata={
                "message_id": data.get("id"),
                "timestamp": data.get("timestamp"),
                "is_group": data.get("isGroup", False),
                "push_name": push_name,
                "verified_name": verified_name,
            },
        )

    async def _handle_status(self, data: dict) -> None:
        status = data.get("status")
        logger.info(f"WhatsApp status: {status}")

        if status == "connected":
            self._connected = True
            self_id = data.get("selfId")
            if self_id:
                self._self_id = self_id
                logger.info(f"[WhatsApp] Auto-allowed self: {self_id}")
        elif status == "disconnected":
            self._connected = False

        await self.bus.publish_outbound(
            OutboundMessage(
                content=f"WhatsApp {status}",
                channel="web",
                chat_id="system",
                metadata={"type": "whatsapp_status", "status": status},
            )
        )

    async def _handle_qr(self, data: dict) -> None:
        # Baileys fires QR events mid-reconnect even when a valid saved session
        # exists. If we're already authenticated there's nothing to scan, so
        # don't spam the web UI with stale QR frames.
        if self._connected:
            logger.debug("[WhatsApp] Ignoring QR event — already connected.")
            return

        qr_data = data.get("qr")
        logger.info("Received WhatsApp QR code from bridge. Broadcasting to Web UI...")

        await self.bus.publish_outbound(
            OutboundMessage(
                content="WhatsApp QR Code Update",
                channel="web",
                chat_id="system",
                metadata={"type": "whatsapp_qr", "qr": qr_data},
            )
        )

    def _load_contacts(self) -> dict:
        """Load contacts from JSON file."""
        if not CONTACTS_PATH.exists():
            return dict(_EMPTY_CONTACTS)

        try:
            return json.loads(CONTACTS_PATH.read_text(encoding="utf-8"))
        except Exception as e:
            logger.error(f"Error loading contacts.json: {e}")
            return dict(_EMPTY_CONTACTS)

    def _save_contacts(self, contacts: dict) -> None:
        """Save contacts to JSON file."""
        CONTACTS_PATH.parent.mkdir(parents=True, exist_ok=True)

        try:
            CONTACTS_PATH.write_text(json.dumps(contacts, indent=2), encoding="utf-8")
        except Exception as e:
            logger.error(f"Error saving contacts.json: {e}")

    def _check_contact_allowed(
        self,
        chat_id: str,
        push_name: str | None = None,
        verified_name: str | None = None,
        alt_id: str | None = None,
    ) -> bool:
        """
        Check if a contact is allowed.
        If unknown, add to pending list.
        Stores name metadata in the identities section.
        """

        if self._self_id and chat_id == self._self_id:
            return True

        contacts = self._load_contacts()
        allowed: list = contacts.setdefault("allowed", [])
        pending: list = contacts.setdefault("pending", [])
        blocked: list = contacts.setdefault("blocked", [])
        identities: dict = contacts.setdefault("identities", {})

        if push_name or verified_name or alt_id:
            current = identities.get(chat_id, {})
            updated = {
                "push_name": push_name or current.get("push_name"),
                "verified_name": verified_name or current.get("verified_name"),
                "alt_id": alt_id or current.get("alt_id"),
            }
            if updated != current:
                identities[chat_id] = updated
                contacts["identities"] = identities
                self._save_contacts(contacts)

        if chat_id in allowed:
            return True

        if chat_id in blocked:
            logger.warning(f"[WhatsApp] Contact {chat_id} is BLOCKED.")
            return False

        if chat_id in pending:
            logger.info(f"[WhatsApp] Contact {chat_id} is PENDING approval.")
            return False

        name_str = f" ({push_name})" if push_name else ""
        logger.info(
            f"[WhatsApp] New contact {chat_id}{name_str} added to PENDING list."
        )
        pending.append(chat_id)
        contacts["pending"] = pending
        self._save_contacts(contacts)
        return False

    def _is_connected(self) -> bool:
        """Return True if the bridge WebSocket is ready; log a warning otherwise."""
        if not self._ws or not self._connected:
            logger.warning("[WhatsApp] Bridge not connected, skipping operation.")
            return False
        return True
