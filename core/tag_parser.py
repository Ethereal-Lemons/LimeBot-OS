"""
Tag parser — extracts and processes XML-style tags from LLM responses.

Handles: <save_soul>, <save_identity>, <save_user>, <log_memory>,
         <save_memory>, <discord_send>, <discord_embed>
"""

import re
import asyncio
from datetime import datetime
from pathlib import Path

from loguru import logger


from core.events import OutboundMessage


_BASE_DIR = Path(__file__).resolve().parent.parent
PERSONA_DIR = _BASE_DIR / "persona"
USERS_DIR = PERSONA_DIR / "users"
MEMORY_DIR = PERSONA_DIR / "memory"
LONG_TERM_MEMORY_FILE = PERSONA_DIR / "MEMORY.md"

_ANY_TAG = r"save_soul|save_identity|save_mood|save_relationship|save_user|log_memory|save_memory|discord_send|discord_embed"


class TagResult:
    """Result of processing tags from LLM reply."""

    __slots__ = (
        "clean_reply",
        "soul_updated",
        "identity_updated",
        "mood_updated",
        "relationship_updated",
    )

    def __init__(
        self,
        clean_reply: str,
        soul_updated: bool = False,
        identity_updated: bool = False,
        mood_updated: bool = False,
        relationship_updated: bool = False,
    ):
        self.clean_reply = clean_reply
        self.soul_updated = soul_updated
        self.identity_updated = identity_updated
        self.mood_updated = mood_updated
        self.relationship_updated = relationship_updated


async def process_tags(
    raw_reply: str,
    sender_id: str,
    validate_soul,
    validate_identity,
    validate_mood=None,
    validate_relationship=None,
    vector_service=None,
    bus=None,
    msg=None,
    config=None,
) -> TagResult:
    """
    Parse XML-style tags from an LLM reply, execute side effects,
    and return the cleaned reply text.

    Args:
        raw_reply: The full assistant response (may contain tags).
        sender_id: ID of the user who sent the original message.
        validate_soul: Callable(content) -> bool, saves SOUL.md if valid.
        validate_identity: Callable(content) -> bool, saves IDENTITY.md if valid.
        vector_service: VectorService for semantic indexing.
        bus: MessageBus for publishing outbound messages.
        msg: The original InboundMessage (for channel routing).

    Returns:
        TagResult with cleaned reply and update flags.
    """
    reply = raw_reply or "(Task completed silently.)"
    soul_updated = False
    identity_updated = False
    mood_updated = False
    relationship_updated = False

    while True:
        soul_match = re.search(
            f"<save_soul>(.*?)(?:</save_soul>|(?=<({_ANY_TAG})>)|\\Z)", reply, re.DOTALL
        )
        if not soul_match:
            break
        content = soul_match.group(1).strip()
        if validate_soul(content):
            soul_updated = True
        reply = reply.replace(soul_match.group(0), "", 1).strip()

    while True:
        id_match = re.search(
            f"<save_identity>(.*?)(?:</save_identity>|(?=<({_ANY_TAG})>)|\\Z)",
            reply,
            re.DOTALL,
        )
        if not id_match:
            break
        content = id_match.group(1).strip()
        if validate_identity(content):
            identity_updated = True
        reply = reply.replace(id_match.group(0), "", 1).strip()

    while True:
        mood_match = re.search(
            f"<save_mood>(.*?)(?:</save_mood>|(?=<({_ANY_TAG})>)|\\Z)", reply, re.DOTALL
        )
        if not mood_match:
            break
        content = mood_match.group(1).strip()
        if validate_mood:
            saved = validate_mood(content)
            if saved:
                mood_updated = True
                logger.info("🎭 Mood updated via <save_mood>.")
        reply = reply.replace(mood_match.group(0), "", 1).strip()

    while True:
        rel_match = re.search(
            f"<save_relationship>(.*?)(?:</save_relationship>|(?=<({_ANY_TAG})>)|\\Z)",
            reply,
            re.DOTALL,
        )
        if not rel_match:
            break
        content = rel_match.group(1).strip()
        if (
            validate_relationship
            and config
            and getattr(config.llm, "enable_dynamic_personality", False)
        ):
            saved = validate_relationship(content)
            if saved:
                relationship_updated = True
                logger.info("🤝 Relationship updated via <save_relationship>.")
        reply = reply.replace(rel_match.group(0), "", 1).strip()

    while True:
        user_match = re.search(
            f"<save_user>(.*?)(?:</save_user>|(?=<({_ANY_TAG})>)|\\Z)", reply, re.DOTALL
        )
        if not user_match:
            break
        content = user_match.group(1).strip()
        try:
            _FORBIDDEN = [
                "--- SYSTEM INSTRUCTIONS ---",
                "SYSTEM METADATA:",
                "<save_soul>",
                "<save_identity>",
                "</save_soul>",
                "</save_identity>",
                "You are now fully initialized",
            ]
            injected = any(
                frag in line.strip()
                for frag in _FORBIDDEN
                for line in content.splitlines()
            )
            if injected:
                logger.warning(
                    f"⚠ Rejected <save_user> for {sender_id}: forbidden fragment detected"
                )

            elif len(content) < 20:
                logger.warning(
                    f"⚠ Rejected <save_user> for {sender_id}: content too short ({len(content)} chars)"
                )
            else:
                # Sanitize sender_id to prevent path traversal
                safe_sender_id = "".join(
                    c for c in sender_id if c.isalnum() or c in ("-", "_")
                ).strip()
                if not safe_sender_id:
                    safe_sender_id = "unknown"

                user_file = USERS_DIR / f"{safe_sender_id}.md"
                user_file.parent.mkdir(exist_ok=True, parents=True)
                tmp = user_file.with_suffix(".tmp")
                tmp.write_text(content, encoding="utf-8")
                tmp.replace(user_file)
                logger.info(
                    f"✓ Saved user profile for {safe_sender_id} (original: {sender_id})"
                )
        except Exception as e:
            logger.error(f"Error saving user profile: {e}")
        reply = reply.replace(user_match.group(0), "", 1).strip()

    while True:
        mem_match = re.search(
            f"<log_memory>(.*?)(?:</log_memory>|(?=<({_ANY_TAG})>)|\\Z)",
            reply,
            re.DOTALL,
        )
        if not mem_match:
            break
        entry = mem_match.group(1).strip()
        today_str = datetime.now().strftime("%Y-%m-%d")
        memory_file = MEMORY_DIR / f"{today_str}.md"
        log_entry = f"\n- **[{datetime.now().strftime('%H:%M')}]** {entry}"

        try:
            with open(memory_file, "a", encoding="utf-8") as f:
                f.write(log_entry)

            if vector_service is not None:
                asyncio.create_task(vector_service.add_entry(entry, category="journal"))
        except Exception as e:
            logger.error(f"Error writing to memory: {e}")
        reply = reply.replace(mem_match.group(0), "", 1).strip()

    while True:
        save_mem_match = re.search(
            f"<save_memory>(.*?)(?:</save_memory>|(?=<({_ANY_TAG})>)|\\Z)",
            reply,
            re.DOTALL,
        )
        if not save_mem_match:
            break
        content = save_mem_match.group(1).strip()
        try:
            existing_content = ""
            if LONG_TERM_MEMORY_FILE.exists():
                existing_content = LONG_TERM_MEMORY_FILE.read_text(
                    encoding="utf-8"
                ).strip()

            LONG_TERM_MEMORY_FILE.write_text(content, encoding="utf-8")

            if vector_service is not None:
                is_template = (
                    "No significant events or user data recorded yet" in content
                )
                if content != existing_content and not is_template:
                    asyncio.create_task(
                        vector_service.add_entry(content, category="long_term")
                    )
        except Exception as e:
            logger.error(f"Error saving long-term memory: {e}")
        reply = reply.replace(save_mem_match.group(0), "", 1).strip()

    while True:
        discord_send_match = re.search(
            f"<discord_send>(.*?)(?:</discord_send>|(?=<({_ANY_TAG})>)|\\Z)",
            reply,
            re.DOTALL,
        )
        if not discord_send_match:
            break
        content = discord_send_match.group(1).strip()

        channel_match = re.search(r"^channel_id:\s*(.+)$", content, re.MULTILINE)
        channel_id = channel_match.group(1).strip() if channel_match else None

        if channel_match:
            content = content.replace(channel_match.group(0), "", 1).strip()

        content = re.sub(r"^message:\s*", "", content, flags=re.IGNORECASE).strip()
        message = content

        if channel_id and message:
            asyncio.create_task(
                bus.publish_outbound(
                    OutboundMessage(
                        content=message,
                        channel="discord",
                        chat_id=channel_id,
                        metadata={"from_skill": True},
                    )
                )
            )
        elif not channel_id:
            logger.warning("<discord_send> tag missing channel_id — message dropped")
        reply = reply.replace(discord_send_match.group(0), "", 1).strip()

    while True:
        discord_embed_match = re.search(
            f"<discord_embed>(.*?)(?:</discord_embed>|(?=<({_ANY_TAG})>)|\\Z)",
            reply,
            re.DOTALL,
        )
        if not discord_embed_match:
            break
        content = discord_embed_match.group(1).strip()

        channel_match = re.search(r"^channel_id:\s*(.+)$", content, re.MULTILINE)
        title_match = re.search(r"^title:\s*(.+)$", content, re.MULTILINE)
        color_match = re.search(r"^color:\s*(.+)$", content, re.MULTILINE)

        channel_id = channel_match.group(1).strip() if channel_match else None
        title = title_match.group(1).strip() if title_match else None
        color = color_match.group(1).strip() if color_match else "#5865F2"

        for match in [channel_match, title_match, color_match]:
            if match:
                content = content.replace(match.group(0), "", 1)

        content = re.sub(
            r"^description:\s*", "", content.strip(), flags=re.IGNORECASE
        ).strip()
        description = content

        if channel_id and title:
            asyncio.create_task(
                bus.publish_outbound(
                    OutboundMessage(
                        content=f"**{title}**\n{description or ''}",
                        channel="discord",
                        chat_id=channel_id,
                        metadata={
                            "from_skill": True,
                            "embed": {
                                "title": title,
                                "description": description,
                                "color": color,
                            },
                        },
                    )
                )
            )
        elif not channel_id:
            logger.warning("<discord_embed> tag missing channel_id — embed dropped")
        reply = reply.replace(discord_embed_match.group(0), "", 1).strip()

    _ORPHAN_CLOSING = re.compile(
        r"</(?:save_user|save_soul|save_identity|save_mood|save_relationship"
        r"|log_memory|save_memory|discord_send|discord_embed)>",
        re.IGNORECASE,
    )
    reply = _ORPHAN_CLOSING.sub("", reply)

    reply = re.sub(r"\n{3,}", "\n\n", reply).strip()

    if not reply and raw_reply:
        if soul_updated or identity_updated:
            reply = "(Persona configuration updated.)"
        else:
            reply = "(System updated configuration/memory files.)"

    return TagResult(
        reply, soul_updated, identity_updated, mood_updated, relationship_updated
    )
