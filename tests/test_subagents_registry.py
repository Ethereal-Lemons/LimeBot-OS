import unittest
from pathlib import Path
import shutil
import uuid


class TestSubagentsRegistry(unittest.TestCase):
    def _tempdir(self) -> Path:
        base_dir = Path("temp")
        base_dir.mkdir(exist_ok=True)
        path = base_dir / f"subagent_test_{uuid.uuid4().hex[:8]}"
        path.mkdir(parents=True, exist_ok=True)
        self.addCleanup(lambda: shutil.rmtree(path, ignore_errors=True))
        return path

    def test_registry_loads_markdown_subagent_and_normalizes_tools(self):
        from core.subagents import SubagentRegistry

        tmp = self._tempdir()
        agents_dir = tmp / "agents"
        agents_dir.mkdir(parents=True, exist_ok=True)
        agent_file = agents_dir / "code-reviewer.md"
        agent_file.write_text(
            "---\n"
            "name: code-reviewer\n"
            "description: Review code for regressions and safety issues.\n"
            "tools: [Read, Grep, Bash]\n"
            "model: inherit\n"
            "---\n"
            "Focus on bugs, regressions, and missing tests.\n",
            encoding="utf-8",
        )

        registry = SubagentRegistry(agent_dirs=[str(agents_dir)])
        registry.discover_and_load()
        agent = registry.get_subagent("code-reviewer")

        self.assertIsNotNone(agent)
        self.assertEqual(agent["tools"], ["read_file", "search_files", "run_command"])
        self.assertEqual(agent["model"], "inherit")
        self.assertIn("missing tests", agent["prompt"])

        additions = registry.get_prompt_additions()
        self.assertIn("code-reviewer", additions)
        self.assertIn("read_file, search_files, run_command", additions)

    def test_project_subagent_definition_wins_over_lower_priority_dirs(self):
        from core.subagents import SubagentRegistry

        tmp = self._tempdir()
        project_dir = tmp / "project_agents"
        user_dir = tmp / "user_agents"
        project_dir.mkdir(parents=True, exist_ok=True)
        user_dir.mkdir(parents=True, exist_ok=True)

        (project_dir / "planner.md").write_text(
            "---\nname: planner\ndescription: Project planner\n---\nProject prompt\n",
            encoding="utf-8",
        )
        (user_dir / "planner.md").write_text(
            "---\nname: planner\ndescription: User planner\n---\nUser prompt\n",
            encoding="utf-8",
        )

        registry = SubagentRegistry(agent_dirs=[str(project_dir), str(user_dir)])
        registry.discover_and_load()
        agent = registry.get_subagent("planner")

        self.assertEqual(agent["description"], "Project planner")
        self.assertIn("Project prompt", agent["prompt"])

    def test_registry_exposes_claude_and_limebot_location_options(self):
        from core.subagents import SubagentRegistry

        registry = SubagentRegistry(agent_dirs=[])
        options = registry.get_location_options()
        values = {option["value"] for option in options}

        self.assertEqual(
            values,
            {
                "project_limebot",
                "project_claude",
                "user_limebot",
                "user_claude",
            },
        )


class TestSubagentToolDefinitions(unittest.TestCase):
    def test_spawn_agent_schema_lists_available_named_agents(self):
        from core.tool_defs import build_tool_definitions

        tools = build_tool_definitions(
            enabled_skills=[],
            available_agents={"code-reviewer": "Review code for regressions."},
        )
        spawn_tool = next(
            tool for tool in tools if tool["function"]["name"] == "spawn_agent"
        )
        agent_param = spawn_tool["function"]["parameters"]["properties"]["agent"]

        self.assertEqual(agent_param["enum"], ["code-reviewer"])
        self.assertIn("Review code for regressions.", agent_param["description"])
