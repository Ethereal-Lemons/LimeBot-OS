import unittest
from types import SimpleNamespace

from core.paths import HEARTBEAT_FILE, LONG_TERM_MEMORY_FILE, TOOLS_NOTES_FILE, USER_CONTEXT_FILE


class TestPromptContextScoping(unittest.TestCase):
    def setUp(self):
        self._files = {}
        for path in (
            LONG_TERM_MEMORY_FILE,
            USER_CONTEXT_FILE,
            TOOLS_NOTES_FILE,
            HEARTBEAT_FILE,
        ):
            self._files[path] = (
                path.exists(),
                path.read_text(encoding="utf-8") if path.exists() else None,
            )

        LONG_TERM_MEMORY_FILE.write_text("Trusted long-term memory.", encoding="utf-8")
        USER_CONTEXT_FILE.write_text("Trusted operator profile.", encoding="utf-8")
        TOOLS_NOTES_FILE.write_text("Trusted local notes.", encoding="utf-8")
        HEARTBEAT_FILE.write_text("Return HEARTBEAT_OK when idle.", encoding="utf-8")

        self.config = SimpleNamespace(
            llm=SimpleNamespace(enable_dynamic_personality=False),
            personality_whitelist=["owner"],
        )
        self.identity = (
            "# IDENTITY.md - Who I Am\n\n"
            "*   **Name:** LimeBot\n"
            "*   **Emoji:** 🍋\n"
            "*   **Style:** Clear and direct\n"
        )
        self.soul = (
            "Core values matter. Truth, boundaries, and personality are important. "
            "This soul description is long enough to pass validation and explain who I am."
        )

    def tearDown(self):
        for path, (existed, content) in self._files.items():
            if existed:
                path.write_text(content or "", encoding="utf-8")
            elif path.exists():
                path.unlink()

    def test_trusted_sessions_load_private_context(self):
        from core.prompt import build_stable_system_prompt, get_volatile_prompt_suffix

        prompt = build_stable_system_prompt(
            sender_id="owner",
            channel="discord",
            chat_id="123",
            model="test-model",
            allowed_paths=[],
            skill_registry=None,
            config=self.config,
            soul=self.soul,
            identity_raw=self.identity,
            sender_name="Owner",
        )
        volatile = get_volatile_prompt_suffix(
            include_private_memory=True,
            current_message="@heartbeat",
        )

        self.assertIn("PRIMARY USER CONTEXT", prompt)
        self.assertIn("Trusted operator profile.", prompt)
        self.assertIn("LOCAL OPERATOR NOTES", prompt)
        self.assertIn("Trusted local notes.", prompt)
        self.assertIn("Trusted long-term memory.", volatile)
        self.assertIn("HEARTBEAT MODE", volatile)

    def test_untrusted_sessions_hide_private_context(self):
        from core.prompt import build_stable_system_prompt, get_volatile_prompt_suffix

        prompt = build_stable_system_prompt(
            sender_id="stranger",
            channel="discord",
            chat_id="123",
            model="test-model",
            allowed_paths=[],
            skill_registry=None,
            config=self.config,
            soul=self.soul,
            identity_raw=self.identity,
            sender_name="Stranger",
        )
        volatile = get_volatile_prompt_suffix(
            include_private_memory=False,
            current_message="hello",
        )

        self.assertNotIn("Trusted operator profile.", prompt)
        self.assertNotIn("Trusted local notes.", prompt)
        self.assertIn("PRIVATE CONTEXT POLICY", prompt)
        self.assertIn("Shared memory files are intentionally hidden", volatile)
        self.assertNotIn("Trusted long-term memory.", volatile)
