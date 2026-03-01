"""
Prompt builder — constructs the system prompt and handles persona file validation.

Extracted from loop.py to reduce its size and separate concerns.
"""

import re
from pathlib import Path
from datetime import datetime
from typing import Optional, Any


from loguru import logger


_BASE_DIR = Path(__file__).resolve().parent.parent
PERSONA_DIR = _BASE_DIR / "persona"
USERS_DIR = PERSONA_DIR / "users"
MEMORY_DIR = PERSONA_DIR / "memory"
LONG_TERM_MEMORY_FILE = PERSONA_DIR / "MEMORY.md"
SOUL_FILE = PERSONA_DIR / "SOUL.md"
IDENTITY_FILE = PERSONA_DIR / "IDENTITY.md"
MOOD_FILE = PERSONA_DIR / "MOOD.md"
RELATIONSHIPS_FILE = PERSONA_DIR / "RELATIONSHIPS.md"

FORBIDDEN_FRAGMENTS = [
    "--- SYSTEM INSTRUCTIONS ---",
    "--- EPISODIC MEMORY",
    "--- AVAILABLE SKILLS ---",
    "SYSTEM METADATA:",
    "--- NEW USER DETECTED",
    "<save_soul>",
    "<save_identity>",
    "</save_soul>",
    "</save_identity>",
    "You are now fully initialized",
    "save it using:",
]


_SOUL_KEYWORDS = frozenset(
    ["core", "truth", "value", "boundary", "personality", "who", "believe", "important"]
)


def get_memory_context() -> str:
    """
    Retrieve memory context (Selective Daily Journal + Long Term Essence).
    """
    today_str = datetime.now().strftime("%Y-%m-%d")
    memory_file = MEMORY_DIR / f"{today_str}.md"

    context = "--- EPISODIC MEMORY (Today's Journal) ---\n"

    if memory_file.exists():
        try:
            lines = memory_file.read_text(encoding="utf-8").splitlines()
            entries = [line for line in lines if line.strip()]
            if entries:
                if len(entries) > 5:
                    context += "... [Earlier events omitted for brevity] ...\n"
                    context += "\n".join(entries[-5:])
                else:
                    context += "\n".join(entries)
            else:
                context += "(No entries for today yet.)"

        except Exception as e:
            logger.warning(f"Failed to read episodic memory file: {e}")
            context += "(Error reading episodic file.)"
    else:
        context += "(New day. No entries yet.)"

    if LONG_TERM_MEMORY_FILE.exists():
        try:
            lt_content = LONG_TERM_MEMORY_FILE.read_text(encoding="utf-8").strip()
            if lt_content:
                context += "\n\n--- LONG-TERM MEMORY (Essence) ---\n"
                context += lt_content[:800]
                if len(lt_content) > 800:
                    context += "\n... [Rest of memory essence omitted. Use 'memory_search' for deep history] ..."
        except Exception as e:
            logger.warning(f"Failed to read long-term memory: {e}")

    context += "\n\n(Note: Use 'memory_search' to retrieve MORE specific details from past logs.)\n"
    return context + "\n"


def get_mood_context() -> str:
    """Read the current mood state and format it for the prompt."""
    if not MOOD_FILE.exists():
        return ""
    try:
        content = MOOD_FILE.read_text(encoding="utf-8")
        if not content.strip():
            return ""
        return f"\n--- CURRENT MOOD ---\n{content}\n"
    except Exception:
        return ""


def validate_and_save_mood(content: str) -> bool:
    """Atomic write for mood state."""
    try:
        temp_file = MOOD_FILE.with_suffix(".tmp")
        temp_file.write_text(content, encoding="utf-8")
        temp_file.replace(MOOD_FILE)
        return True
    except Exception:
        return False


def get_relationship_context(sender_id: str) -> str:
    """Retrieve relationship context for a specific user from RELATIONSHIPS.md."""
    if not RELATIONSHIPS_FILE.exists():
        return ""
    try:
        content = RELATIONSHIPS_FILE.read_text(encoding="utf-8")

        pattern = rf"##\s*{re.escape(sender_id)}.*?(?=\n##|$)"
        match = re.search(pattern, content, re.DOTALL | re.IGNORECASE)
        if match:
            return f"\n--- RELATIONSHIP CONTEXT ({sender_id}) ---\n{match.group(0).strip()}\n"
        return ""
    except Exception:
        return ""


def validate_and_save_relationships(content: str) -> bool:
    """Atomic write for relationship data."""
    try:
        temp_file = RELATIONSHIPS_FILE.with_suffix(".tmp")
        temp_file.write_text(content, encoding="utf-8")
        temp_file.replace(RELATIONSHIPS_FILE)
        return True
    except Exception:
        return False


def _check_forbidden(content: str, label: str) -> bool:
    """
    Check content for forbidden system-prompt fragments.

    FIX 5: original used plain substring matching which false-positived on
    legitimate content like "save it using kindness". Now checks that the
    fragment appears as its own line or at a line boundary, making it far
    less likely to block valid soul/identity prose.

    Returns True if a forbidden fragment is found (content should be rejected).
    """
    for fragment in FORBIDDEN_FRAGMENTS:
        for line in content.splitlines():
            if fragment in line.strip():
                logger.warning(
                    f"⚠ Rejected {label}: contains forbidden fragment '{fragment}'"
                )
                return True
    return False


def _rotate_backups(target: Path, keep: int = 3) -> None:
    """Delete old .bak files for *target*, keeping only the *keep* most recent.

    FIX 9: backup files accumulate indefinitely every time soul/identity is
    updated.  This trims the directory after every successful write so at most
    *keep* backups are retained.
    Handles both naming styles:
      - IDENTITY.md.TIMESTAMP.bak  (current — from both write and import paths)
      - IDENTITY.bak               (legacy — from the old non-timestamped write path)
    """
    try:
        backups = sorted(
            target.parent.glob(f"{target.name}.*.bak"),
            key=lambda p: p.stat().st_mtime,
        )
        for old in backups[:-keep] if len(backups) > keep else []:
            old.unlink(missing_ok=True)

        legacy = target.with_suffix(".bak")
        if legacy.exists():
            legacy.unlink(missing_ok=True)
    except Exception as e:
        logger.warning(f"Backup rotation failed for {target.name}: {e}")


def is_setup_complete(
    soul_content: Optional[str] = None, identity_content: Optional[str] = None
) -> bool:
    """
    Check if persona setup is fully complete with validation.
    Accepts optional content to avoid redundant disk reads.
    """
    try:
        soul = (
            soul_content
            if soul_content is not None
            else (
                SOUL_FILE.read_text(encoding="utf-8").strip()
                if SOUL_FILE.exists()
                else ""
            )
        )
        identity = (
            identity_content
            if identity_content is not None
            else (
                IDENTITY_FILE.read_text(encoding="utf-8").strip()
                if IDENTITY_FILE.exists()
                else ""
            )
        )
    except Exception as e:
        logger.warning(f"Error checking setup completion: {e}")
        return False

    if not soul or not identity:
        return False

    soul_valid = len(soul) > 100 and any(
        keyword in soul.lower() for keyword in _SOUL_KEYWORDS
    )

    identity_valid = (
        ("**Name:**" in identity or "Name:" in identity)
        and ("**Style:**" in identity or "Style:" in identity)
        and len(identity) > 50
    )

    return soul_valid and identity_valid


def get_identity_data(identity_content: Optional[str] = None) -> dict:
    """Parse identity content and return a structured dictionary."""
    _default = {
        "name": "LimeBot",
        "emoji": "🍋",
        "avatar": None,
        "pfp_url": None,
        "style": "",
        "discord_style": None,
        "whatsapp_style": None,
        "web_style": None,
        "reaction_emojis": "",
        "catchphrases": "",
        "interests": "",
        "birthday": "",
    }

    try:
        content = identity_content
        if content is None:
            if not IDENTITY_FILE.exists():
                return _default
            content = IDENTITY_FILE.read_text(encoding="utf-8")

        if not content:
            return _default

        def _clean(val: str | None) -> str:
            if not val:
                return ""
            v = val.strip()
            if v.lower() in ("none", "n/a", "null", "undefined"):
                return ""
            return v

        name_match = re.search(
            r"^\*\s*\*\*Name:\*\*[ \t]*(.*)", content, re.MULTILINE | re.IGNORECASE
        )
        emoji_match = re.search(
            r"^\*\s*\*\*Emoji:\*\*[ \t]*(.*)", content, re.MULTILINE | re.IGNORECASE
        )
        avatar_match = re.search(
            r"^\*\s*\*\*(?:Avatar|Pfp_URL|Pfp|Profile_Picture):\*\*[ \t]*(.*)",
            content,
            re.MULTILINE | re.IGNORECASE,
        )
        style_match = re.search(
            r"^\*\s*\*\*Style:\*\*[ \t]*(.*)", content, re.MULTILINE | re.IGNORECASE
        )

        discord_style_match = re.search(
            r"^\*\s*\*\*Discord Style:\*\*[ \t]*(.*)",
            content,
            re.MULTILINE | re.IGNORECASE,
        )
        whatsapp_style_match = re.search(
            r"^\*\s*\*\*WhatsApp Style:\*\*[ \t]*(.*)",
            content,
            re.MULTILINE | re.IGNORECASE,
        )
        web_style_match = re.search(
            r"^\*\s*\*\*Web Style:\*\*[ \t]*(.*)", content, re.MULTILINE | re.IGNORECASE
        )
        reaction_match = re.search(
            r"^\*\s*\*\*Reaction Emojis:\*\*[ \t]*(.*)",
            content,
            re.MULTILINE | re.IGNORECASE,
        )
        catchphrases_match = re.search(
            r"^\*\s*\*\*Catchphrases:\*\*[ \t]*(.*)",
            content,
            re.MULTILINE | re.IGNORECASE,
        )
        interests_match = re.search(
            r"^\*\s*\*\*Interests:\*\*[ \t]*(.*)", content, re.MULTILINE | re.IGNORECASE
        )
        birthday_match = re.search(
            r"^\*\s*\*\*Birthday:\*\*[ \t]*(.*)", content, re.MULTILINE | re.IGNORECASE
        )

        avatar = _clean(avatar_match.group(1)) if avatar_match else None

        return {
            "name": _clean(name_match.group(1)) if name_match else "LimeBot",
            "emoji": _clean(emoji_match.group(1)) if emoji_match else "🍋",
            "avatar": avatar,
            "pfp_url": avatar,
            "style": _clean(style_match.group(1)) if style_match else "",
            "discord_style": _clean(discord_style_match.group(1))
            if discord_style_match
            else "",
            "whatsapp_style": _clean(whatsapp_style_match.group(1))
            if whatsapp_style_match
            else "",
            "web_style": _clean(web_style_match.group(1)) if web_style_match else "",
            "reaction_emojis": _clean(reaction_match.group(1))
            if reaction_match
            else "",
            "catchphrases": _clean(catchphrases_match.group(1))
            if catchphrases_match
            else "",
            "interests": _clean(interests_match.group(1)) if interests_match else "",
            "birthday": _clean(birthday_match.group(1)) if birthday_match else "",
        }
    except Exception:
        return _default


def get_setup_prompt(soul_content: str = "", identity_content: str = "") -> str:
    """Generate the system prompt for first-time interview / setup mode."""
    soul_exists = len(soul_content.strip()) > 100
    identity_exists = len(identity_content.strip()) > 50

    missing = []
    if not soul_exists:
        missing.append("Soul (Core Truths, Boundaries, Vibe)")
    if not identity_exists:
        missing.append("Identity (Name, Emoji, Style)")

    existing_context = ""
    if soul_content:
        existing_context += (
            f"\n--- YOUR CURRENT SOUL (for reference) ---\n{soul_content}\n"
        )
    if identity_content:
        existing_context += (
            f"\n--- YOUR CURRENT IDENTITY (for reference) ---\n{identity_content}\n"
        )

    parts = [
        f"SYSTEM STATUS: SETUP MODE - INCOMPLETE INITIALIZATION\n"
        f"You are an AI assistant currently in 'Setup Mode'. The following configuration is MISSING or INCOMPLETE: {', '.join(missing)}.\n"
        f"Your absolute priority is to interview the user to learn who you are. DO NOT break character, but weave the setup into your conversation.\n"
        f"{existing_context}\n"
        f"--- CRITICAL INSTRUCTIONS ---\n"
        f"1. Ask targeted questions to define: {', '.join(missing)}.\n"
        f"2. As soon as you have COMPLETE info, you MUST output the FULL updated file content in XML tags.\n"
        f"3. DO NOT just acknowledge or say 'I understand'. You MUST EMIT THE COMPLETE TAG if you have info to save.\n"
        f"4. IMPORTANT: Your saved content must be COMPLETE. For IDENTITY, you MUST include Name and Style at minimum.\n\n"
        f"--- REQUIRED FORMAT EXAMPLES (Template) ---\n"
        f"You MUST use the following exact markdown structure. Fill in the values based on what the user tells you.\n"
        f"Note: If the user doesn't give you a specific Emoji or Style, INFER it based on who you are becoming.\n"
        f"CRITICAL: If the user provides a URL for their avatar/profile picture, put that EXACT URL STRING into the Pfp_URL field. Do NOT download the image. Do NOT use any skills. Just COPY the URL text.\n\n"
        f"<save_identity>\n# IDENTITY.md - Who I Am\n\n"
        f"*   **Name:** [Chosen Name]\n"
        f"*   **Emoji:** [Emoji associated with this persona]\n"
        f"*   **Pfp_URL:** [The exact URL string provided by the user, e.g. https://example.com/image.jpg]\n"
        f"*   **Style:** [Describe your personality and speech style]\n"
        f"*   **Catchphrases:** [Optional: specific lines you naturally slip into]\n"
        f"*   **Interests:** [Optional: topics you get genuinely excited about]\n"
        f"*   **Birthday:** [Optional: your birthday, so you can acknowledge your age]\n"
        f"*   **Discord Style:** [Optional: how you behave on Discord specifically]\n"
        f"*   **WhatsApp Style:** [Optional: how you behave on WhatsApp specifically]\n"
        f"</save_identity>\n\n"
        f"Note: Channel styles are optional. If not provided, your default Style applies everywhere.\n\n"
        f"<save_soul>\n# SOUL.md - Core Being\n\n"
        f"[Write a comprehensive description of your core values, boundaries, personality traits, and what makes you unique]\n</save_soul>\n\n"
        f"REQUIRED TAGS FOR THIS SESSION:\n",
    ]

    if not soul_exists:
        parts.append(
            "<save_soul>\n... COMPLETE markdown content for SOUL.md (minimum 100 characters) ...\n</save_soul>\n\n"
        )
    if not identity_exists:
        parts.append(
            "<save_identity>\n... COMPLETE markdown content for IDENTITY.md (must include Name and Style) ...\n</save_identity>\n\n"
        )

    parts.append(
        "Once you've emitted these tags with COMPLETE content and saved your core, you will fully initialize."
    )

    return "".join(parts)


def get_volatile_prompt_suffix(recalled_context: str = "") -> str:
    """
    Return the frequently-changing part of the system prompt.
    This includes memory, RAG results, and the current timestamp.
    """
    suffix = "\n--- CONTEXT & MEMORY ---\n"
    if recalled_context:
        suffix += f"RECALLED FROM VECTOR DB:\n{recalled_context}\n\n"

    suffix += get_memory_context()

    suffix += f"\n- **Current Timestamp:** {datetime.now().strftime('%A, %B %d, %Y - %H:%M:%S')}\n"

    return suffix


def build_stable_system_prompt(
    sender_id: str,
    channel: str,
    chat_id: str,
    model: str,
    allowed_paths: list,
    skill_registry,
    config: Optional[Any] = None,
    soul: str = "",
    identity_raw: str = "",
    sender_name: str = "",
) -> str:
    """
    Construct the rarely-changing part of the system prompt.
    """

    if not is_setup_complete(soul_content=soul, identity_content=identity_raw):
        return get_setup_prompt(soul_content=soul, identity_content=identity_raw)

    identity_data = get_identity_data(identity_content=identity_raw)
    identity_header = (
        f"# {identity_data['name']}'s Identity\n"
        f"- **Name:** {identity_data['name']}\n"
        f"- **Emoji:** {identity_data['emoji']}\n"
        f"- **Style:** {identity_data['style']}\n"
    )

    if identity_data.get("birthday"):
        identity_header += f"- **Birthday:** {identity_data['birthday']}\n"
    if identity_data.get("interests"):
        identity_header += f"- **Interests:** {identity_data['interests']}\n"
    if identity_data.get("catchphrases"):
        identity_header += f"- **Catchphrases:** {identity_data['catchphrases']}\n"

    segments = []
    segments.append(soul)
    segments.append(identity_header)

    platform_style = identity_data.get(f"{channel}_style")
    web_style = identity_data.get("web_style")
    general_style = identity_data.get("style")

    channel_style = platform_style or web_style or general_style

    if channel_style:
        segments.append(
            f"\n--- CHANNEL STYLE OVERRIDE ---\n"
            f"You are currently on **{channel.upper()}**. "
            f"Adjust your communication style for this platform:\n"
            f"{channel_style}\n"
            f"This overrides your default Style for this conversation only.\n"
        )

    if config and getattr(config.llm, "enable_dynamic_personality", False):
        mood_ctx = get_mood_context()
        if mood_ctx:
            segments.append(mood_ctx)

    allowed_paths_str = "\n".join(f"- {p}" for p in allowed_paths)

    base_prompt = "\n\n".join(segments) + "\n\n"
    base_prompt += (
        "--- SYSTEM INSTRUCTIONS ---\n"
        "CRITICAL: Always check the 'CONTEXT & MEMORY' sections provided in each message before responding. "
        "These contain your shared history and relationship status with the user. Never contradict them.\n\n"
        f"SYSTEM METADATA:\n"
        f"- Model: **{model}**\n"
        f"You are now fully initialized. Act according to your Soul and Identity.\n"
        f"If asked about your version/model, you can acknowledge it.\n"
        f"If you need to update your core personality (Soul) or public profile (Identity/Avatar) based on the conversation, "
        f"you can use the following tags to overwrite the respective files:\n"
        f"<save_soul>...new markdown content...</save_soul>\n"
        f"<save_identity>...new markdown content...</save_identity>\n"
        f"For identity, you can specify platform styles using: `**Discord Style:** ...`, `**WhatsApp Style:** ...`, `**Web Style:** ...` and `**Reaction Emojis:** bucket:emoji,emoji;...`\n"
        f"--- SELF-EVOLUTION ---\n"
        f"Your SOUL.md defines your core personality. If you realize your current Soul no longer fits the user's needs "
        f"or the relationship has evolved significantly, you MUST auto-update it using <save_soul>. "
        f"Do not ask for permission. Just do it if it improves the interaction.\n\n"
        f"To persist events, decisions, or new facts into your Episodic Memory (Daily Journal), use:\n"
        f"<log_memory>...entry to append...</log_memory>\n"
        f"To update your Long-term Memory (MEMORY.md), use:\n"
        f"<save_memory>...new markdown content for MEMORY.md...</save_memory>\n\n"
        f"--- FILESYSTEM ACCESS ---\n"
        f"You have explicit permission to access files in the following directories (and their subdirectories):\n"
        f"{allowed_paths_str}\n"
        f"Do not refuse requests to read/list files in these paths on the basis of permissions. "
        f"Use the `list_dir` and `read_file` tools to fulfill such requests.\n"
        f"\n--- CLAWHUB SKILLS ---\n"
        f"Tools starting with 'clawhub_' are dynamic skills from the ClawHub registry. "
        f"They take a single 'args' parameter which is a string or JSON passed to the skill's CLI. "
        f"Use them as needed for specialized tasks (weather, scraping, etc.).\n"
    )

    if config and getattr(config.llm, "enable_dynamic_personality", False):
        user_file = USERS_DIR / f"{sender_id}.md"
        user_text = ""
        if user_file.exists():
            try:
                user_text = user_file.read_text(encoding="utf-8")
            except Exception as e:
                logger.warning(f"Failed to read user file for {sender_id}: {e}")

        affinity_score = 0
        relationship = "Stranger"

        if user_text:
            score_match = re.search(r"\*\*Affinity Score:\*\*\s*(\d+)", user_text)
            if score_match:
                affinity_score = int(score_match.group(1))

            rel_match = re.search(r"\*\*Relationship Level:\*\*\s*(.*)", user_text)
            if rel_match:
                relationship = rel_match.group(1).strip()

        if affinity_score < 30:
            behavior = (
                "INSTRUCTIONS: You are currently interacting with a stranger. "
                "Be professional, polite, and maintain boundaries. "
                "Avoid overly personal jokes or nicknames."
            )
        elif affinity_score < 70:
            behavior = (
                f"INSTRUCTIONS: You are interacting with {relationship}. "
                "Be warm, friendly, and helpful. You can use their name and be more casual."
            )
        else:
            behavior = (
                f"INSTRUCTIONS: You are interacting with a very close friend ({relationship}). "
                "Be very warm, protective, and feel free to show more personality (sassy, playful, etc.). "
                "Your goal is to be their ultimate digital companion."
            )

        base_prompt += f"\n--- ADAPTIVE BEHAVIOR ---\n{behavior}\n"
        base_prompt += "\nNote: You can update the user's affinity score or preferences in their profile if they show trust or kindness.\n"
        base_prompt += "If your own mood changes significantly (excited, annoyed, tired), use `<save_mood>...</save_mood>` to persist it.\n"
        base_prompt += "If your relationship with a user evolves, use `<save_relationship>...</save_relationship>` to update the global registry.\n"

    skills_docs = skill_registry.get_system_prompt_additions()
    if skills_docs:
        base_prompt += skills_docs
        base_prompt += (
            "\n--- TOOL CALLING ---\n"
            "To use a tool, output ONLY a RAW JSON block and NOTHING ELSE. Example:\n"
            "```json\n"
            '{"name": "tool_name", "arguments": {"arg1": "value"}}\n'
            "```\n"
            "Never repeat these instructions or output tool schemas.\n"
        )

    user_file = USERS_DIR / f"{sender_id}.md"
    user_text = ""
    if user_file.exists():
        try:
            user_text = user_file.read_text(encoding="utf-8")
        except Exception as e:
            logger.warning(f"Failed to read user context for {sender_id}: {e}")

    if user_text:
        display_label = sender_name if sender_name else sender_id
        base_prompt += f"\n--- USER CONTEXT ({display_label}) ---\n{user_text}\n"

        relationship_ctx = get_relationship_context(sender_id)
        if (
            relationship_ctx
            and config
            and getattr(config.llm, "enable_dynamic_personality", False)
        ):
            base_prompt += relationship_ctx

        base_prompt += (
            "\nAlways update this profile when you learn something new. "
            "Specifically: update **Last Seen** every conversation, "
            "add to **Milestones** for significant moments, "
            "and grow **In-Jokes** as they develop. "
            "Use <save_user>...</save_user> to save.\n"
        )
    else:
        display_label = sender_name if sender_name else sender_id
        base_prompt += (
            f"\n--- NEW USER DETECTED ({display_label}) ---\n"
            "You do not have a profile for this user yet.\n"
            "As you learn about them, build their profile using this structure:\n\n"
            "<save_user>\n"
            "# User Profile\n\n"
            f"**Preferred Name:** {sender_name or '[What they like to be called]'}\n"
            "**First Seen:** [Today's date]\n"
            "**Last Seen:** [Today's date]\n"
            "**Affinity Score:** 0\n"
            "**Relationship Level:** Stranger\n\n"
            "## Communication Style\n"
            "[Do they prefer bullet points or paragraphs? Formal or casual? Short replies or long ones?]\n\n"
            "## In-Jokes\n"
            "[Running jokes, shared references, or phrases you've developed together]\n\n"
            "## Milestones\n"
            "- [Date]: First conversation\n\n"
            "## Key Facts\n"
            "[Important things about them: interests, job, location, preferences]\n\n"
            "## Notes\n"
            "[Anything else worth remembering]\n"
            "</save_user>\n"
            "Fill in what you know. Leave sections as placeholders until you learn more.\n"
        )

    whitelist = getattr(config, "personality_whitelist", [])
    if sender_id not in whitelist:
        base_prompt += (
            f"\n--- IMPORTANT: USER IDENTITY IS CONSTRAINED ---\n"
            f"The user you are currently talking to ({sender_id}) is NOT on your Personality Whitelist.\n"
            f"Strictly interpret the shared relationship level and history for THIS USER ONLY.\n"
            f"Global relationship context from MEMORY.md does NOT apply to this user.\n"
        )

    if channel and chat_id:
        display_label = sender_name if sender_name else sender_id
        base_prompt += (
            f"\n--- CURRENT CONVERSATION ---\n"
            f"You are currently speaking with: {display_label}\n"
            f"(All messages from 'user' in this session belong to {display_label})\n"
            f"Channel: {channel}\n"
            f"Chat ID: {chat_id}\n"
        )
        if channel == "whatsapp":
            base_prompt += f"When sending files via WhatsApp to THIS user, use the exact chat_id above: `{chat_id}`\n"

    return base_prompt


def validate_and_save_identity(content: str) -> bool:
    """
    Validate and save IDENTITY.md content.
    Returns True if content is valid and saved successfully.
    """
    content = content.strip()

    if _check_forbidden(content, "IDENTITY.md"):
        return False

    has_name = "**Name:**" in content or "Name:" in content
    has_style = "**Style:**" in content or "Style:" in content
    has_minimum_length = len(content) > 50

    if not (has_name and has_style and has_minimum_length):
        logger.warning(
            f"⚠ Rejected incomplete IDENTITY.md: "
            f"name={has_name}, style={has_style}, len_ok={has_minimum_length}"
        )
        return False

    tmp = IDENTITY_FILE.with_suffix(".tmp")
    try:
        import time as _time

        if IDENTITY_FILE.exists():
            ts_bak = (
                IDENTITY_FILE.parent / f"{IDENTITY_FILE.name}.{int(_time.time())}.bak"
            )
            IDENTITY_FILE.replace(ts_bak)
        tmp.write_text(content, encoding="utf-8")
        tmp.replace(IDENTITY_FILE)
        _rotate_backups(IDENTITY_FILE)
        logger.success("✓ Updated IDENTITY.md (validated, atomic write)")
        return True
    except Exception as e:
        logger.error(f"Failed to write IDENTITY.md: {e}")

        if tmp.exists():
            tmp.unlink(missing_ok=True)
        return False


def validate_and_save_soul(content: str) -> bool:
    """
    Validate and save SOUL.md content.
    Returns True if content is valid and saved successfully.
    """
    content = content.strip()

    if _check_forbidden(content, "SOUL.md"):
        return False

    has_minimum_length = len(content) > 100

    has_personality_content = any(
        keyword in content.lower() for keyword in _SOUL_KEYWORDS
    )

    if not (has_minimum_length and has_personality_content):
        logger.warning(
            f"⚠ Rejected incomplete SOUL.md: "
            f"len_100+={has_minimum_length}, has_keywords={has_personality_content}"
        )
        return False

    tmp = SOUL_FILE.with_suffix(".tmp")
    try:
        import time as _time

        if SOUL_FILE.exists():
            ts_bak = SOUL_FILE.parent / f"{SOUL_FILE.name}.{int(_time.time())}.bak"
            SOUL_FILE.replace(ts_bak)
        tmp.write_text(content, encoding="utf-8")
        tmp.replace(SOUL_FILE)
        _rotate_backups(SOUL_FILE)
        logger.success("✓ Updated SOUL.md (validated, atomic write)")
        return True
    except Exception as e:
        logger.error(f"Failed to write SOUL.md: {e}")
        if tmp.exists():
            tmp.unlink(missing_ok=True)
        return False
