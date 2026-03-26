"""Helpers for bootstrapping local persona runtime files from shipped templates."""

from pathlib import Path

from loguru import logger

from core.paths import IDENTITY_FILE, LONG_TERM_MEMORY_FILE, PERSONA_DIR, SOUL_FILE


_BOOTSTRAP_TEMPLATES: tuple[tuple[Path, Path], ...] = (
    (SOUL_FILE, PERSONA_DIR / "SOUL.md.example"),
    (IDENTITY_FILE, PERSONA_DIR / "IDENTITY.md.example"),
    (LONG_TERM_MEMORY_FILE, PERSONA_DIR / "MEMORY.md.example"),
)


def ensure_persona_bootstrap_files() -> None:
    """Create local runtime persona files from templates when missing.

    The repository ships example templates for first boot, but the live runtime
    persona should remain local state. This function only fills gaps and never
    overwrites an existing user/persona file.
    """

    for target, template in _BOOTSTRAP_TEMPLATES:
        if target.exists() or not template.exists():
            continue
        try:
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(template.read_text(encoding="utf-8"), encoding="utf-8")
            logger.info(f"[persona] Bootstrapped {target.name} from {template.name}")
        except Exception as e:
            logger.warning(
                f"[persona] Failed to bootstrap {target.name} from {template.name}: {e}"
            )
