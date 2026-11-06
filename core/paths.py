"""
Shared path constants for the persona/memory system.

Single source of truth — imported by prompt.py, tag_parser.py, loop.py, reflection.py.
"""

from dataclasses import dataclass
import re
from pathlib import Path

from core.runtime_paths import get_state_dir

_BASE_DIR = get_state_dir()

PERSONA_DIR = _BASE_DIR / "persona"
USERS_DIR = PERSONA_DIR / "users"
MEMORY_DIR = PERSONA_DIR / "memory"
LONG_TERM_MEMORY_FILE = PERSONA_DIR / "MEMORY.md"
USER_CONTEXT_FILE = PERSONA_DIR / "USER.md"
TOOLS_NOTES_FILE = PERSONA_DIR / "TOOLS.md"
HEARTBEAT_FILE = PERSONA_DIR / "HEARTBEAT.md"
SOUL_FILE = PERSONA_DIR / "SOUL.md"
IDENTITY_FILE = PERSONA_DIR / "IDENTITY.md"
MOOD_FILE = PERSONA_DIR / "MOOD.md"
RELATIONSHIPS_FILE = PERSONA_DIR / "RELATIONSHIPS.md"


@dataclass(frozen=True)
class PersonaPaths:
    """Paths belonging to one agent persona and its private memory."""

    root: Path

    @property
    def users(self) -> Path:
        return self.root / "users"

    @property
    def memory(self) -> Path:
        return self.root / "memory"

    @property
    def long_term_memory(self) -> Path:
        return self.root / "MEMORY.md"

    @property
    def user_context(self) -> Path:
        return self.root / "USER.md"

    @property
    def tools_notes(self) -> Path:
        return self.root / "TOOLS.md"

    @property
    def heartbeat(self) -> Path:
        return self.root / "HEARTBEAT.md"

    @property
    def soul(self) -> Path:
        return self.root / "SOUL.md"

    @property
    def identity(self) -> Path:
        return self.root / "IDENTITY.md"

    @property
    def mood(self) -> Path:
        return self.root / "MOOD.md"

    @property
    def relationships(self) -> Path:
        return self.root / "RELATIONSHIPS.md"


def get_persona_paths(profile_id: str = "default") -> PersonaPaths:
    """Return profile-scoped persona paths; ``default`` keeps legacy locations."""
    normalized = str(profile_id or "default").strip().lower()
    if normalized == "default":
        return PersonaPaths(PERSONA_DIR)
    if not re.fullmatch(r"[a-z0-9](?:[a-z0-9-]{0,38}[a-z0-9])?", normalized):
        raise ValueError("profile_id must be a safe lowercase slug")
    return PersonaPaths(PERSONA_DIR / "profiles" / normalized)
