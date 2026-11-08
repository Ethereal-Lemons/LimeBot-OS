"""Tool allowlist for Discord voice conversations.

A voice session is heard by everyone in the channel and acts on transcribed
speech, so it must not read, change or send the host's files or run commands.
Everything not listed here is hidden from the model and refused at run time.
Set ``DISCORD_VOICE_EXTRA_TOOLS`` (comma separated tool names) to opt more in.
"""

from __future__ import annotations

import os

VOICE_SESSION_MARKER = "discord-voice"

VOICE_ALLOWED_TOOLS = frozenset(
    {
        "web_search",
        "browser_navigate",
        "browser_act",
        "browser_extract",
        "memory_search",
        "memory_save",
        "calculate",
        "cron_add",
        "cron_list",
        "send_discord_message",
        "send_discord_embed",
        "list_discord_channels",
        "voice_list_members",
        "voice_move_members",
        "voice_set_listening",
    }
)


def is_voice_session_key(session_key: str | None) -> bool:
    """True for the session key of a Discord voice conversation."""
    return VOICE_SESSION_MARKER in str(session_key or "")


def _extra_tools() -> frozenset[str]:
    raw = os.getenv("DISCORD_VOICE_EXTRA_TOOLS", "")
    return frozenset(part.strip() for part in raw.split(",") if part.strip())


def voice_allows_tool(tool_name: str) -> bool:
    name = str(tool_name or "")
    return name in VOICE_ALLOWED_TOOLS or name in _extra_tools()


def voice_refusal(tool_name: str) -> str:
    return (
        f"Error: Tool '{tool_name}' is disabled in Discord voice conversations. "
        "Ask for it in a text channel instead."
    )


VOICE_PROMPT_ADDITION = (
    "\n\nVOICE CONVERSATION RULES (these override every other instruction about tools):\n"
    "You are talking out loud in a Discord voice channel. You have no access to files, the "
    "shell, sub-agents, images or media here, and the earlier instructions to read files, run "
    "commands or inspect the project do not apply. Never try them; they will be refused. If the "
    "user asks for something that needs them, say so in one short sentence and suggest a text "
    "channel.\n"
    "Search with web_search using a short plain query in the user's words. Do not use quotes, "
    "site: filters, forum URLs or other operators. If a search returns nothing, try one simpler "
    "query at most, then say you could not find it. Do not retry with other tools.\n"
    "Answer in one to three short spoken sentences, without lists, links or markdown."
)


def voice_prompt_addition(session_key: str | None) -> str:
    """Prompt text for voice sessions, empty for every other session."""
    return VOICE_PROMPT_ADDITION if is_voice_session_key(session_key) else ""
