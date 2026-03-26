import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch


class TestPersonaBootstrap(unittest.TestCase):
    def test_bootstrap_creates_missing_runtime_files_from_templates(self):
        try:
            import loguru  # noqa: F401
        except Exception:
            raise unittest.SkipTest("Missing dependencies (loguru).")

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            persona_dir = root / "persona"
            persona_dir.mkdir(parents=True, exist_ok=True)

            (persona_dir / "SOUL.md.example").write_text(
                "Core values: truth and boundaries. "
                "Personality and values are important. "
                "This soul description is long enough to pass validation. "
                "It includes who I am and what I believe. "
                "Boundaries matter, and this text exceeds one hundred characters.",
                encoding="utf-8",
            )
            (persona_dir / "IDENTITY.md.example").write_text(
                "# IDENTITY.md - Who I Am\n\n"
                "*   **Name:** LimeBot\n"
                "*   **Emoji:** 🍋\n"
                "*   **Pfp_URL:** \n"
                "*   **Style:** Helpful, concise, and friendly.\n",
                encoding="utf-8",
            )
            (persona_dir / "MEMORY.md.example").write_text(
                "# Long-Term Memory\n",
                encoding="utf-8",
            )

            import core.paths as paths
            import core.persona_bootstrap as persona_bootstrap

            with patch.object(paths, "_BASE_DIR", root), patch.object(
                paths, "PERSONA_DIR", persona_dir
            ), patch.object(paths, "SOUL_FILE", persona_dir / "SOUL.md"), patch.object(
                paths, "IDENTITY_FILE", persona_dir / "IDENTITY.md"
            ), patch.object(
                paths, "LONG_TERM_MEMORY_FILE", persona_dir / "MEMORY.md"
            ), patch.object(
                persona_bootstrap, "PERSONA_DIR", persona_dir
            ), patch.object(
                persona_bootstrap, "SOUL_FILE", persona_dir / "SOUL.md"
            ), patch.object(
                persona_bootstrap, "IDENTITY_FILE", persona_dir / "IDENTITY.md"
            ), patch.object(
                persona_bootstrap, "LONG_TERM_MEMORY_FILE", persona_dir / "MEMORY.md"
            ), patch.object(
                persona_bootstrap,
                "_BOOTSTRAP_TEMPLATES",
                (
                    (persona_dir / "SOUL.md", persona_dir / "SOUL.md.example"),
                    (persona_dir / "IDENTITY.md", persona_dir / "IDENTITY.md.example"),
                    (persona_dir / "MEMORY.md", persona_dir / "MEMORY.md.example"),
                ),
            ):
                persona_bootstrap.ensure_persona_bootstrap_files()

            self.assertEqual(
                (persona_dir / "SOUL.md").read_text(encoding="utf-8"),
                (persona_dir / "SOUL.md.example").read_text(encoding="utf-8"),
            )
            self.assertEqual(
                (persona_dir / "IDENTITY.md").read_text(encoding="utf-8"),
                (persona_dir / "IDENTITY.md.example").read_text(encoding="utf-8"),
            )
            self.assertEqual(
                (persona_dir / "MEMORY.md").read_text(encoding="utf-8"),
                (persona_dir / "MEMORY.md.example").read_text(encoding="utf-8"),
            )

    def test_bootstrap_does_not_overwrite_existing_runtime_files(self):
        try:
            import loguru  # noqa: F401
        except Exception:
            raise unittest.SkipTest("Missing dependencies (loguru).")

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            persona_dir = root / "persona"
            persona_dir.mkdir(parents=True, exist_ok=True)

            (persona_dir / "SOUL.md.example").write_text("example soul", encoding="utf-8")
            (persona_dir / "IDENTITY.md.example").write_text(
                "example identity", encoding="utf-8"
            )
            (persona_dir / "MEMORY.md.example").write_text(
                "example memory", encoding="utf-8"
            )

            (persona_dir / "SOUL.md").write_text("custom soul", encoding="utf-8")
            (persona_dir / "IDENTITY.md").write_text(
                "custom identity", encoding="utf-8"
            )
            (persona_dir / "MEMORY.md").write_text("custom memory", encoding="utf-8")

            import core.persona_bootstrap as persona_bootstrap

            with patch.object(
                persona_bootstrap,
                "_BOOTSTRAP_TEMPLATES",
                (
                    (persona_dir / "SOUL.md", persona_dir / "SOUL.md.example"),
                    (persona_dir / "IDENTITY.md", persona_dir / "IDENTITY.md.example"),
                    (persona_dir / "MEMORY.md", persona_dir / "MEMORY.md.example"),
                ),
            ):
                persona_bootstrap.ensure_persona_bootstrap_files()

            self.assertEqual(
                (persona_dir / "SOUL.md").read_text(encoding="utf-8"), "custom soul"
            )
            self.assertEqual(
                (persona_dir / "IDENTITY.md").read_text(encoding="utf-8"),
                "custom identity",
            )
            self.assertEqual(
                (persona_dir / "MEMORY.md").read_text(encoding="utf-8"),
                "custom memory",
            )
