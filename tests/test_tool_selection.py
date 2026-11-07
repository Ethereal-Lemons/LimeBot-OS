import sys
import types
import unittest
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


class TestModelRoutedToolSurface(unittest.TestCase):
    @staticmethod
    def _agent(tool_definitions):
        from core.loop import AgentLoop

        agent = object.__new__(AgentLoop)
        agent.config = SimpleNamespace()
        agent.skill_registry = None
        agent.subagent_registry = None
        agent._session_capability_state = {}
        agent._get_tool_definitions = lambda: tool_definitions
        agent._log_tool_debug = lambda *args, **kwargs: None
        return agent

    def test_every_nonempty_turn_can_choose_from_tools(self):
        from core.loop import AgentLoop

        for message in ("hi", "ok", "hazlo", "find Jennie photos", "open a URL"):
            with self.subTest(message=message):
                self.assertTrue(AgentLoop._should_include_tools(message))
                agent = object.__new__(AgentLoop)
                agent.config = SimpleNamespace()
                self.assertTrue(agent._should_include_tools_for_turn(message))

        agent = object.__new__(AgentLoop)
        agent.config = SimpleNamespace()
        self.assertFalse(agent._should_include_tools_for_turn(""))

    def test_model_receives_full_registry_for_each_kind_of_request(self):
        from core.tool_defs import build_tool_definitions

        all_tools = build_tool_definitions(enabled_skills=["browser"])
        all_names = {tool["function"]["name"] for tool in all_tools}
        agent = self._agent(all_tools)

        for message in (
            "download a picture of Jennie and send it here",
            "generate a new picture of a lime robot",
            "run the project tests",
            "search the web and then open this page",
            "hola",
        ):
            with self.subTest(message=message):
                selected = agent._get_tool_definitions_for_turn(
                    message, session_key="web:chat"
                )
                self.assertEqual(
                    {tool["function"]["name"] for tool in selected}, all_names
                )

    def test_web_search_uses_structured_image_delivery_parameter(self):
        from core.tool_defs import build_tool_definitions

        web_search = next(
            tool["function"]
            for tool in build_tool_definitions(enabled_skills=[])
            if tool["function"]["name"] == "web_search"
        )
        attach = web_search["parameters"]["properties"]["attach_to_chat"]
        self.assertEqual(attach["type"], "boolean")
        self.assertIn("user expects an image", attach["description"])

    def test_registered_browser_tools_are_available_without_browser_skill(self):
        from core.tool_defs import build_tool_definitions

        names = {
            tool["function"]["name"]
            for tool in build_tool_definitions(enabled_skills=[])
        }
        self.assertTrue(
            {"browser_navigate", "browser_act", "browser_extract"} <= names
        )

    def test_generate_image_schema_supports_chat_references(self):
        from core.tool_defs import build_tool_definitions

        generate = next(
            tool["function"]
            for tool in build_tool_definitions(enabled_skills=[])
            if tool["function"]["name"] == "generate_image"
        )
        properties = generate["parameters"]["properties"]
        self.assertEqual(properties["reference_images"]["type"], "array")
        self.assertEqual(properties["use_attached_images"]["type"], "boolean")

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

        name, args = agent._normalize_tool_alias(
            "grep", {"pattern": "TODO"}, "web:test"
        )
        self.assertEqual(name, "search_files")
        self.assertEqual(args["query"], "TODO")

        name, args = agent._normalize_tool_alias(
            "cat", {"file": "README.md"}, "web:test"
        )
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


if __name__ == "__main__":
    unittest.main()
