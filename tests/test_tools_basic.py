import unittest
from pathlib import Path


class TestToolsBasic(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        try:
            import loguru  # noqa: F401
        except Exception:
            raise unittest.SkipTest("Missing dependencies (loguru).")

    async def test_tool_call_read_file(self):
        from core.bus import MessageBus
        from core.loop import AgentLoop

        class _TestAgentLoop(AgentLoop):
            async def _init_skills_and_tools(self) -> None:
                self._tool_definitions = []
                self._warmed = True

        bus = MessageBus()
        agent = _TestAgentLoop(bus=bus)

        tmp_dir = Path("temp")
        tmp_dir.mkdir(exist_ok=True)
        tmp_file = tmp_dir / "tool_test.txt"
        tmp_file.write_text("hello tool", encoding="utf-8")

        try:
            result = await agent._execute_tool(
                "read_file", {"path": str(tmp_file)}, session_key="test:web"
            )
        finally:
            tmp_file.unlink(missing_ok=True)

        self.assertIn("hello tool", str(result))

    async def test_tool_call_search_files(self):
        from core.bus import MessageBus
        from core.loop import AgentLoop

        class _TestAgentLoop(AgentLoop):
            async def _init_skills_and_tools(self) -> None:
                self._tool_definitions = []
                self._warmed = True

        bus = MessageBus()
        agent = _TestAgentLoop(bus=bus)

        tmp_dir = Path("temp")
        tmp_dir.mkdir(exist_ok=True)
        tmp_file = tmp_dir / "search_tool_test.txt"
        tmp_file.write_text("alpha beta gamma", encoding="utf-8")

        try:
            by_content = await agent._execute_tool(
                "search_files",
                {"query": "beta", "path": str(tmp_dir), "mode": "content"},
                session_key="test:web",
            )
            by_name = await agent._execute_tool(
                "search_files",
                {"query": "search_tool_test", "path": str(tmp_dir), "mode": "name"},
                session_key="test:web",
            )
        finally:
            tmp_file.unlink(missing_ok=True)

        self.assertIn("search_tool_test.txt", str(by_content))
        self.assertIn("search_tool_test.txt", str(by_name))
