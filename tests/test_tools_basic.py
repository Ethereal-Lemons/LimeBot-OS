import unittest
import shutil
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

    async def test_tool_call_read_file_line_range(self):
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
        tmp_file = tmp_dir / "tool_range_test.txt"
        tmp_file.write_text("line1\nline2\nline3\nline4\n", encoding="utf-8")

        try:
            result = await agent._execute_tool(
                "read_file",
                {"path": str(tmp_file), "start_line": 2, "end_line": 3},
                session_key="test:web",
            )
        finally:
            tmp_file.unlink(missing_ok=True)

        self.assertIn("line2", str(result))
        self.assertIn("line3", str(result))
        self.assertNotIn("line1", str(result))
        self.assertNotIn("line4", str(result))

    async def test_tool_call_list_dir_pagination(self):
        from core.bus import MessageBus
        from core.loop import AgentLoop

        class _TestAgentLoop(AgentLoop):
            async def _init_skills_and_tools(self) -> None:
                self._tool_definitions = []
                self._warmed = True

        bus = MessageBus()
        agent = _TestAgentLoop(bus=bus)

        tmp_dir = Path("temp") / "list_dir_test"
        tmp_dir.mkdir(parents=True, exist_ok=True)
        created = []
        for i in range(5):
            f = tmp_dir / f"item_{i}.txt"
            f.write_text(f"file {i}", encoding="utf-8")
            created.append(f)

        try:
            result = await agent._execute_tool(
                "list_dir",
                {"path": str(tmp_dir), "limit": 2, "offset": 1, "sort_by": "name"},
                session_key="test:web",
            )
        finally:
            for f in created:
                f.unlink(missing_ok=True)
            shutil.rmtree(tmp_dir, ignore_errors=True)

        # Expect a paged response and exactly two file rows in the selected window.
        file_rows = [ln for ln in str(result).splitlines() if ln.startswith("[FILE]")]
        self.assertEqual(len(file_rows), 2)
