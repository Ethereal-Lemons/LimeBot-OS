import os
import unittest
from types import SimpleNamespace
from unittest.mock import patch


class TestDefaultToolSelectionRegressions(unittest.TestCase):
    @staticmethod
    def _tool_names(tool_definitions):
        return {
            tool.get("function", {}).get("name", "")
            for tool in tool_definitions
        }

    def _load_default_harness_config(self):
        import config as config_module

        previous_cache = config_module._cached_config
        self.addCleanup(setattr, config_module, "_cached_config", previous_cache)
        config_module._cached_config = None
        with patch.dict(
            os.environ,
            {
                "LIMEBOT_AI_HARNESS_MODE": "",
            },
            clear=False,
        ):
            return config_module.load_config(force_reload=True)

    @staticmethod
    def _make_agent(config, tool_definitions):
        from core.loop import AgentLoop

        agent = object.__new__(AgentLoop)
        agent.config = config
        agent.skill_registry = SimpleNamespace(
            get_required_tool_names=lambda _name: []
        )
        agent._get_tool_definitions = lambda: tool_definitions
        agent._log_tool_debug = lambda *args, **kwargs: None
        return agent

    def test_forced_skill_keeps_the_complete_available_schema(self):
        from core.tool_defs import build_tool_definitions

        all_tools = build_tool_definitions(enabled_skills=["browser"])
        agent = self._make_agent(
            SimpleNamespace(),
            all_tools,
        )

        selected = agent._get_tool_definitions_for_turn(
            "summarize the README and list remaining tasks",
            forced_skill_name="docx-creator",
        )

        self.assertEqual(selected, all_tools)
        self.assertEqual(self._tool_names(selected), self._tool_names(all_tools))

    def test_default_config_keeps_full_schema_for_exact_spanish_docx_correction(self):
        from core.tool_defs import build_tool_definitions

        default_config = self._load_default_harness_config()
        all_tools = build_tool_definitions(enabled_skills=["browser"])
        agent = self._make_agent(default_config, all_tools)
        prompt = (
            "ese docx esta feo, revisa bien el que te mandé, "
            "tiene imagenes structura y todo"
        )

        selected = agent._get_tool_definitions_for_turn(prompt)
        names = self._tool_names(selected)

        self.assertEqual(selected, all_tools)
        self.assertTrue(
            {"read_file", "write_file", "run_command", "send_media"} <= names
        )
        self.assertNotEqual(names, {"send_media", "send_voice", "generate_image"})

    def test_default_config_keeps_tools_for_short_spanish_action_followup(self):
        from core.tool_defs import build_tool_definitions

        default_config = self._load_default_harness_config()
        all_tools = build_tool_definitions(enabled_skills=["browser"])
        agent = self._make_agent(default_config, all_tools)

        self.assertTrue(agent._should_include_tools_for_turn("hazlo"))
        self.assertEqual(
            agent._get_tool_definitions_for_turn("hazlo"),
            all_tools,
        )

    def test_default_config_exposes_tools_for_casual_smalltalk(self):
        from core.tool_defs import build_tool_definitions

        default_config = self._load_default_harness_config()
        all_tools = build_tool_definitions(enabled_skills=["browser"])
        agent = self._make_agent(default_config, all_tools)

        self.assertTrue(agent._should_include_tools_for_turn("hola"))
        self.assertTrue(agent._should_include_tools_for_turn("how are you?"))
        self.assertEqual(
            agent._get_tool_definitions_for_turn("hola"),
            all_tools,
        )


if __name__ == "__main__":
    unittest.main()
