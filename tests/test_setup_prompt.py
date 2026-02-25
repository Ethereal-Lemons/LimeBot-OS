import asyncio
import unittest
from pathlib import Path


SOUL_FILE = Path("persona") / "SOUL.md"
IDENTITY_FILE = Path("persona") / "IDENTITY.md"


class TestSetupPrompt(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        try:
            import loguru  # noqa: F401
        except Exception:
            raise unittest.SkipTest("Missing dependencies (loguru).")

        from core.bus import MessageBus
        from core.loop import AgentLoop

        class _TestAgentLoop(AgentLoop):
            async def _init_skills_and_tools(self) -> None:
                self._tool_definitions = []
                self._warmed = True

        self.bus = MessageBus()
        self.agent = _TestAgentLoop(bus=self.bus)

        self._soul_exists = SOUL_FILE.exists()
        self._identity_exists = IDENTITY_FILE.exists()
        self._soul_content = SOUL_FILE.read_text(encoding="utf-8") if self._soul_exists else None
        self._identity_content = (
            IDENTITY_FILE.read_text(encoding="utf-8") if self._identity_exists else None
        )

    async def asyncTearDown(self):
        if self._soul_exists:
            SOUL_FILE.write_text(self._soul_content or "", encoding="utf-8")
        else:
            if SOUL_FILE.exists():
                SOUL_FILE.unlink()

        if self._identity_exists:
            IDENTITY_FILE.write_text(self._identity_content or "", encoding="utf-8")
        else:
            if IDENTITY_FILE.exists():
                IDENTITY_FILE.unlink()

    async def _get_prompt(self) -> str:
        return await self.agent._get_stable_prompt(
            sender_id="tester",
            channel="web",
            chat_id="chat",
            sender_name="Tester",
        )

    async def test_missing_persona_files_triggers_setup_prompt(self):
        if SOUL_FILE.exists():
            SOUL_FILE.unlink()
        if IDENTITY_FILE.exists():
            IDENTITY_FILE.unlink()

        prompt = await self._get_prompt()
        self.assertIn("SYSTEM STATUS: SETUP MODE", prompt)

    async def test_invalid_persona_content_triggers_setup_prompt(self):
        SOUL_FILE.write_text("too short", encoding="utf-8")
        IDENTITY_FILE.write_text("Name: A", encoding="utf-8")

        prompt = await self._get_prompt()
        self.assertIn("SYSTEM STATUS: SETUP MODE", prompt)

    async def test_valid_persona_content_exits_setup_prompt(self):
        SOUL_FILE.write_text(
            "Core values: truth and boundaries. "
            "Personality and values are important. "
            "This soul description is long enough to pass validation. "
            "It includes who I am and what I believe. "
            "Boundaries matter, and this text exceeds one hundred characters.",
            encoding="utf-8",
        )
        IDENTITY_FILE.write_text(
            "# IDENTITY.md - Who I Am\n\n"
            "*   **Name:** LimeBot\n"
            "*   **Emoji:** 🍋\n"
            "*   **Pfp_URL:** \n"
            "*   **Style:** Clear, concise, and helpful\n"
            "*   **Catchphrases:** \n",
            encoding="utf-8",
        )

        prompt = await self._get_prompt()
        self.assertNotIn("SYSTEM STATUS: SETUP MODE", prompt)
