"""
Shared path constants for the persona/memory system.

Single source of truth — imported by prompt.py, tag_parser.py, loop.py, reflection.py.
"""

from pathlib import Path

_BASE_DIR = Path(__file__).resolve().parent.parent

PERSONA_DIR = _BASE_DIR / "persona"
USERS_DIR = PERSONA_DIR / "users"
MEMORY_DIR = PERSONA_DIR / "memory"
LONG_TERM_MEMORY_FILE = PERSONA_DIR / "MEMORY.md"
SOUL_FILE = PERSONA_DIR / "SOUL.md"
IDENTITY_FILE = PERSONA_DIR / "IDENTITY.md"
MOOD_FILE = PERSONA_DIR / "MOOD.md"
RELATIONSHIPS_FILE = PERSONA_DIR / "RELATIONSHIPS.md"
