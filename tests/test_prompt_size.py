import json
import unittest
from types import SimpleNamespace

from core.capability_service import CapabilityService
from core.tool_defs import build_tool_definitions

# Every request carries the tool list, so its size is paid on every model call.
# Raise this deliberately, together with a measurement, not by accident.
TOOL_SCHEMA_CHAR_BUDGET = 33_000


def _service(tools):
    skills = SimpleNamespace(
        get_capability_catalog=lambda include_inactive=True: [
            {
                "name": "browser",
                "type": "skill",
                "description": "Control a local browser.",
                "state": "disabled",
            }
        ]
    )
    subagents = SimpleNamespace(
        get_agent_descriptions=lambda: {"reviewer": "Reviews diffs."}
    )
    return CapabilityService(
        get_skill_registry=lambda: skills,
        get_subagent_registry=lambda: subagents,
        get_session_state=lambda: {},
        get_tool_definitions=lambda: tools,
        tool_definition_names=lambda items: [t["function"]["name"] for t in items],
        log_tool_debug=lambda *args, **kwargs: None,
        get_capability_turn_context=lambda *args, **kwargs: {},
        get_tool_definitions_for_turn=lambda *args, **kwargs: tools,
    )


class PromptSizeTests(unittest.TestCase):
    def test_the_capability_inventory_does_not_repeat_the_tool_list(self):
        tools = build_tool_definitions(enabled_skills=[])
        prompt = _service(tools).capability_catalog_prompt()

        self.assertIn("`browser` (skill)", prompt)
        self.assertIn("`reviewer` (subagent)", prompt)
        for tool in tools:
            name = tool["function"]["name"]
            self.assertNotIn(f"`{name}`", prompt, f"{name} is already in the tool list")

    def test_the_tool_schemas_stay_within_their_size_budget(self):
        tools = build_tool_definitions(
            enabled_skills=["browser", "filesystem", "discord"],
            available_agents={"reviewer": "x", "verifier": "y", "explorer": "z"},
            search_available=True,
        )
        size = len(json.dumps(tools, separators=(",", ":")))
        self.assertLessEqual(
            size,
            TOOL_SCHEMA_CHAR_BUDGET,
            f"tool schemas grew to {size} characters; trim descriptions or raise the budget with a measurement",
        )


if __name__ == "__main__":
    unittest.main()
