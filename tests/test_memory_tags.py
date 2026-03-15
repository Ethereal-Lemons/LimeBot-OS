import unittest
from datetime import datetime
from pathlib import Path


MEMORY_DIR = Path("persona") / "memory"
LONG_TERM_MEMORY_FILE = Path("persona") / "MEMORY.md"


class TestMemoryTags(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        try:
            import loguru  # noqa: F401
        except Exception:
            raise unittest.SkipTest("Missing dependencies (loguru).")

        self.today_file = MEMORY_DIR / f"{datetime.now().strftime('%Y-%m-%d')}.md"
        self._today_exists = self.today_file.exists()
        self._today_content = (
            self.today_file.read_text(encoding="utf-8") if self._today_exists else None
        )

        self._lt_exists = LONG_TERM_MEMORY_FILE.exists()
        self._lt_content = (
            LONG_TERM_MEMORY_FILE.read_text(encoding="utf-8")
            if self._lt_exists
            else None
        )

    async def asyncTearDown(self):
        if self._today_exists:
            self.today_file.write_text(self._today_content or "", encoding="utf-8")
        else:
            if self.today_file.exists():
                self.today_file.unlink()

        if self._lt_exists:
            LONG_TERM_MEMORY_FILE.write_text(self._lt_content or "", encoding="utf-8")
        else:
            if LONG_TERM_MEMORY_FILE.exists():
                LONG_TERM_MEMORY_FILE.unlink()

    async def test_log_and_save_memory_tags(self):
        from core.tag_parser import process_tags
        from core import prompt as prompt_module

        raw = (
            "Hello "
            "<log_memory>user said hello</log_memory> "
            "<save_memory>Long term memory snapshot.</save_memory>"
        )

        result = await process_tags(
            raw_reply=raw,
            sender_id="tester",
            validate_soul=prompt_module.validate_and_save_soul,
            validate_identity=prompt_module.validate_and_save_identity,
            vector_service=None,
            bus=None,
            msg=None,
            config=None,
        )

        self.assertIn("Hello", result.clean_reply)
        self.assertTrue(self.today_file.exists())
        self.assertIn("user said hello", self.today_file.read_text(encoding="utf-8"))
        self.assertTrue(LONG_TERM_MEMORY_FILE.exists())
        self.assertIn(
            "Long term memory snapshot.",
            LONG_TERM_MEMORY_FILE.read_text(encoding="utf-8"),
        )

    async def test_process_tags_keeps_empty_reply_empty(self):
        from core.tag_parser import process_tags
        from core import prompt as prompt_module

        result = await process_tags(
            raw_reply="",
            sender_id="tester",
            validate_soul=prompt_module.validate_and_save_soul,
            validate_identity=prompt_module.validate_and_save_identity,
            vector_service=None,
            bus=None,
            msg=None,
            config=None,
        )

        self.assertEqual(result.clean_reply, "")

    async def test_save_memory_tool_call_is_routed_through_tag_parser(self):
        from core.bus import MessageBus
        from core.loop import AgentLoop

        class _TestAgentLoop(AgentLoop):
            async def _init_skills_and_tools(self) -> None:
                self._tool_definitions = []
                self._warmed = True

        agent = _TestAgentLoop(bus=MessageBus())
        agent.vector_service = None

        result = await agent._execute_tool(
            "save_memory",
            {"content": "Compat long-term memory snapshot."},
            session_key="web:test",
        )

        self.assertEqual(result, "Long-term memory saved.")
        self.assertTrue(LONG_TERM_MEMORY_FILE.exists())
        self.assertIn(
            "Compat long-term memory snapshot.",
            LONG_TERM_MEMORY_FILE.read_text(encoding="utf-8"),
        )

    async def test_log_memory_tool_call_is_routed_through_tag_parser(self):
        from core.bus import MessageBus
        from core.loop import AgentLoop

        class _TestAgentLoop(AgentLoop):
            async def _init_skills_and_tools(self) -> None:
                self._tool_definitions = []
                self._warmed = True

        agent = _TestAgentLoop(bus=MessageBus())
        agent.vector_service = None

        result = await agent._execute_tool(
            "log_memory",
            {"content": "Compat episodic memory entry."},
            session_key="web:test",
        )

        self.assertEqual(result, "Memory logged.")
        self.assertTrue(self.today_file.exists())
        self.assertIn(
            "Compat episodic memory entry.",
            self.today_file.read_text(encoding="utf-8"),
        )
