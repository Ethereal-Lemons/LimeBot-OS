"""Event definitions for the message bus."""

from dataclasses import dataclass, field
from typing import Any, List, Dict


@dataclass
class InboundMessage:
    """Message received from a channel."""

    channel: str
    sender_id: str
    chat_id: str
    content: str
    media: List[str] = field(default_factory=list)
    metadata: Dict[str, Any] = field(default_factory=dict)

    @property
    def session_key(self) -> str:
        """Unique key for the conversation session. Sanitized for filesystem safety."""

        key = f"{self.channel}_{self.chat_id}"

        for char in ["/", "\\", ":", "*", "?", '"', "<", ">", "|"]:
            key = key.replace(char, "_")
        return key


@dataclass
class OutboundMessage:
    """Message sent to a channel."""

    channel: str
    chat_id: str
    content: str
    media: List[str] = field(default_factory=list)
    metadata: Dict[str, Any] = field(default_factory=dict)
