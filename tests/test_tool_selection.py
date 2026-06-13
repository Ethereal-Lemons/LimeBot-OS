import sys
import types
import inspect
import os
import unittest
from unittest import mock
from types import SimpleNamespace

if "dotenv" not in sys.modules:
    dotenv = types.ModuleType("dotenv")
    dotenv.load_dotenv = lambda *args, **kwargs: None
    sys.modules["dotenv"] = dotenv

if "loguru" not in sys.modules:
    loguru = types.ModuleType("loguru")

    class _DummyLogger:
        def __getattr__(self, name):
            return lambda *args, **kwargs: None

    loguru.logger = _DummyLogger()
    sys.modules["loguru"] = loguru


class TestToolSelection(unittest.TestCase):
    def _require_fast_harness_loop_integration(self):
        from core.loop import AgentLoop

        has_turn_method = hasattr(AgentLoop, "_should_include_tools_for_turn")
        try:
            shortlist_source = inspect.getsource(AgentLoop._get_tool_definitions_for_turn)
        except (OSError, TypeError):
            shortlist_source = ""

        if not has_turn_method or "ai_harness" not in shortlist_source:
            self.skipTest(
                "Requires core/loop.py integration for fast AI harness tool routing."
            )
        return AgentLoop

    def test_should_include_tools_for_plain_directory_request(self):
        from core.loop import AgentLoop

        self.assertTrue(AgentLoop._should_include_tools("list your current dir"))
        self.assertTrue(
            AgentLoop._should_include_tools("show me the working directory")
        )

    def test_should_include_tools_for_plain_smalltalk(self):
        from core.loop import AgentLoop

        self.assertTrue(AgentLoop._should_include_tools("how are you today"))

    def test_shortlist_prefers_filesystem_cluster_for_code_search(self):
        from core.tool_defs import build_tool_definitions, shortlist_tool_definitions

        tools = build_tool_definitions(enabled_skills=["browser"])
        shortlisted = shortlist_tool_definitions(
            tools, "find verify_auth in the codebase and read the file"
        )
        names = {tool["function"]["name"] for tool in shortlisted}

        self.assertIn("search_files", names)
        self.assertIn("read_file", names)
        self.assertIn("list_dir", names)
        self.assertNotIn("google_search", names)

    def test_shortlist_prefers_browser_cluster_for_urls(self):
        from core.tool_defs import build_tool_definitions, shortlist_tool_definitions

        tools = build_tool_definitions(enabled_skills=["browser"])
        shortlisted = shortlist_tool_definitions(
            tools, "open https://example.com and inspect the page"
        )
        names = {tool["function"]["name"] for tool in shortlisted}

        self.assertIn("browser_navigate", names)
        self.assertIn("browser_snapshot", names)
        self.assertIn("browser_click", names)
        self.assertNotIn("read_file", names)

    def test_agent_normalizes_common_tool_aliases(self):
        from core.loop import AgentLoop
        from core.metrics import MetricsCollector

        agent = object.__new__(AgentLoop)
        agent.metrics = MetricsCollector()
        agent._filesystem_alias_actions = {
            "list": "list_dir",
            "read": "read_file",
            "write": "write_file",
            "delete": "delete_file",
            "find": "search_files",
            "search": "search_files",
        }
        agent._tool_name_aliases = {
            "ls": "list_dir",
            "dir": "list_dir",
            "list_files": "list_dir",
            "cat": "read_file",
            "open_file": "read_file",
            "show_file": "read_file",
            "grep": "search_files",
            "rg": "search_files",
            "ripgrep": "search_files",
            "find_files": "search_files",
            "shell": "run_command",
            "terminal": "run_command",
            "exec": "run_command",
            "bash": "run_command",
            "powershell": "run_command",
            "cmd": "run_command",
        }

        name, args = agent._normalize_tool_alias("grep", {"pattern": "TODO"}, "web:test")
        self.assertEqual(name, "search_files")
        self.assertEqual(args["query"], "TODO")

        name, args = agent._normalize_tool_alias("cat", {"file": "README.md"}, "web:test")
        self.assertEqual(name, "read_file")
        self.assertEqual(args["path"], "README.md")

        name, args = agent._normalize_tool_alias(
            "powershell", {"script": "git status"}, "web:test"
        )
        self.assertEqual(name, "run_command")
        self.assertEqual(args["command"], "git status")

    def test_agent_normalizes_json_suffixed_read_aliases(self):
        from core.loop import AgentLoop
        from core.metrics import MetricsCollector

        agent = object.__new__(AgentLoop)
        agent.metrics = MetricsCollector()

        name, args = agent._normalize_tool_alias(
            "read_filejson",
            {"path": "core/asyncio_compat.py", "line_start": 1, "line_end": 40},
            "web:test",
        )
        self.assertEqual(name, "read_file")
        self.assertEqual(args["path"], "core/asyncio_compat.py")
        self.assertEqual(args["start_line"], 1)
        self.assertEqual(args["end_line"], 40)

    def test_agent_uses_full_tool_schema_by_default(self):
        from core.loop import AgentLoop

        agent = object.__new__(AgentLoop)
        all_tools = [
            {"function": {"name": "read_file"}},
            {"function": {"name": "browser_navigate"}},
            {"function": {"name": "list_dir"}},
        ]
        agent._get_tool_definitions = lambda: all_tools

        selected = agent._get_tool_definitions_for_turn(
            "open https://example.com and inspect the page"
        )

        self.assertEqual(selected, all_tools)

    def test_agent_can_opt_in_to_tool_shortlisting_via_env(self):
        from core.loop import AgentLoop
        from core.tool_defs import shortlist_tool_definitions

        agent = object.__new__(AgentLoop)
        all_tools = [
            {"function": {"name": "read_file"}},
            {"function": {"name": "browser_navigate"}},
            {"function": {"name": "browser_snapshot"}},
            {"function": {"name": "browser_click"}},
            {"function": {"name": "list_dir"}},
        ]
        agent._get_tool_definitions = lambda: all_tools

        with mock.patch.dict(os.environ, {"LIMEBOT_ENABLE_TOOL_SHORTLIST": "1"}):
            selected = agent._get_tool_definitions_for_turn(
                "open https://example.com and inspect the page"
            )

        self.assertEqual(
            selected,
            shortlist_tool_definitions(
                all_tools, "open https://example.com and inspect the page"
            ),
        )

    def test_fast_harness_can_disable_tools_for_casual_smalltalk(self):
        AgentLoop = self._require_fast_harness_loop_integration()

        agent = object.__new__(AgentLoop)
        agent.config = SimpleNamespace(
            ai_harness=SimpleNamespace(
                mode="fast", fast_disable_tools_for_casual=True
            )
        )

        self.assertFalse(agent._should_include_tools_for_turn("how are you today"))
        self.assertFalse(agent._should_include_tools_for_turn("ok"))

    def test_fast_harness_keeps_tools_for_clear_action_requests(self):
        AgentLoop = self._require_fast_harness_loop_integration()

        agent = object.__new__(AgentLoop)
        agent.config = SimpleNamespace(
            ai_harness=SimpleNamespace(
                mode="fast", fast_disable_tools_for_casual=True
            )
        )

        self.assertTrue(agent._should_include_tools_for_turn("list your current dir"))
        self.assertTrue(
            agent._should_include_tools_for_turn(
                "open https://example.com and inspect the page"
            )
        )
        self.assertTrue(agent._should_include_tools_for_turn("read ./README.md"))

    def test_fast_harness_shortlists_without_env_flag(self):
        AgentLoop = self._require_fast_harness_loop_integration()
        from core.tool_defs import shortlist_tool_definitions

        agent = object.__new__(AgentLoop)
        agent.config = SimpleNamespace(
            ai_harness=SimpleNamespace(
                mode="fast", fast_disable_tools_for_casual=True
            )
        )
        agent._get_tool_definitions = lambda: [
            {"function": {"name": "read_file"}},
            {"function": {"name": "browser_navigate"}},
            {"function": {"name": "browser_snapshot"}},
            {"function": {"name": "browser_click"}},
            {"function": {"name": "list_dir"}},
        ]
        agent._log_tool_debug = lambda *args, **kwargs: None

        with mock.patch.dict(os.environ, {"LIMEBOT_ENABLE_TOOL_SHORTLIST": ""}):
            selected = agent._get_tool_definitions_for_turn(
                "open https://example.com and inspect the page"
            )

        self.assertEqual(
            selected,
            shortlist_tool_definitions(
                agent._get_tool_definitions(),
                "open https://example.com and inspect the page",
            ),
        )
