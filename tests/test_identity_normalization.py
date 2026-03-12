import unittest
from pathlib import Path
import shutil

from core import prompt
from core.loop import AgentLoop


class TestIdentityNormalization(unittest.TestCase):
    def test_get_identity_data_parses_bulletless_identity_fields(self):
        content = """
**Name:** Lisa
**Emoji:** 👑
**Style:** Charismatic, playful, confident.
**Web Style:** Soft but sharp.
**Reaction Emojis:** 👑, ✨
""".strip()

        identity = prompt.get_identity_data(content)

        self.assertEqual(identity["name"], "Lisa")
        self.assertEqual(identity["emoji"], "👑")
        self.assertEqual(identity["style"], "Charismatic, playful, confident.")
        self.assertEqual(identity["web_style"], "Soft but sharp.")
        self.assertEqual(identity["reaction_emojis"], "👑, ✨")

    def test_validate_and_save_identity_normalizes_to_canonical_template(self):
        original_identity_file = prompt.IDENTITY_FILE
        test_dir = Path("temp") / "test_identity_normalization"
        shutil.rmtree(test_dir, ignore_errors=True)
        test_dir.mkdir(parents=True, exist_ok=True)
        prompt.IDENTITY_FILE = test_dir / "IDENTITY.md"
        try:
            ok = prompt.validate_and_save_identity(
                """
**Name:** Lisa
**Emoji:** 👑
**Style:** Charismatic, playful, confident.
**Birthday:** February 22nd
**Interests:** Coding, K-pop
""".strip()
            )
            self.assertTrue(ok)
            saved = prompt.IDENTITY_FILE.read_text(encoding="utf-8")
            self.assertIn("# IDENTITY.md - Who I Am", saved)
            self.assertIn("*   **Name:** Lisa", saved)
            self.assertIn("*   **Style:** Charismatic, playful, confident.", saved)
            self.assertIn("*   **Birthday:** February 22nd", saved)
        finally:
            prompt.IDENTITY_FILE = original_identity_file
            shutil.rmtree(test_dir, ignore_errors=True)


class TestWebFinalReplySuppression(unittest.TestCase):
    def test_suppression_disabled_when_tag_processing_changes_reply(self):
        suppressed = AgentLoop._should_suppress_web_final_reply(
            channel="web",
            any_tool_calls_in_turn=True,
            iterations_limit_reached=False,
            web_streamed_reply=True,
            force_direct_reply=False,
            reply_to_user="Persona updated.",
            raw_reply="<save_identity>...</save_identity>\nPersona updated.",
        )

        self.assertFalse(suppressed)

    def test_suppression_kept_when_streamed_and_final_reply_match(self):
        suppressed = AgentLoop._should_suppress_web_final_reply(
            channel="web",
            any_tool_calls_in_turn=True,
            iterations_limit_reached=False,
            web_streamed_reply=True,
            force_direct_reply=False,
            reply_to_user="Persona updated.",
            raw_reply="Persona updated.",
        )

        self.assertTrue(suppressed)
