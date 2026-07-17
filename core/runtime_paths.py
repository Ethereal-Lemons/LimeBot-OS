"""Runtime-owned paths that should not need to live in the Git checkout.

The default remains the project root for backwards compatibility.  Set
``LIMEBOT_STATE_DIR`` to move mutable configuration, persona data, and custom
skills to a user-owned directory; the updater can then replace the checkout
without touching that state.
"""

import os
from pathlib import Path


PROJECT_DIR = Path(__file__).resolve().parent.parent


def get_state_dir() -> Path:
    """Return the configured mutable-state directory.

    The value is resolved at call time so tests and embedders can change the
    environment before loading configuration without re-importing modules.
    """

    raw = str(os.getenv("LIMEBOT_STATE_DIR") or "").strip()
    if not raw:
        return PROJECT_DIR
    return Path(raw).expanduser().resolve()


def get_config_file() -> Path:
    return get_state_dir() / "limebot.json"


def get_env_file() -> Path:
    return get_state_dir() / ".env"


def get_allowed_paths_file() -> Path:
    return get_state_dir() / "allowed_paths.txt"


def get_skills_dir() -> Path:
    return get_state_dir() / "skills"


def get_skill_dirs() -> list[Path]:
    """Return shipped skills plus the optional user-owned skills directory."""

    project_skills = PROJECT_DIR / "skills"
    state_skills = get_skills_dir()
    return [project_skills] if project_skills == state_skills else [project_skills, state_skills]


def ensure_state_dir() -> Path:
    state_dir = get_state_dir()
    state_dir.mkdir(parents=True, exist_ok=True)
    return state_dir
