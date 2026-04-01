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
            "disallowed_tools: [Write]\n"
            "model: inherit\n"
            "max_turns: 6\n"
            "background: true\n"
            "---\n"
            "Focus on bugs, regressions, and missing tests.\n",
            encoding="utf-8",
        )

        registry = SubagentRegistry(agent_dirs=[str(agents_dir)])
        registry.discover_and_load()
        agent = registry.get_subagent("code-reviewer")

        self.assertIsNotNone(agent)
        self.assertEqual(agent["tools"], ["read_file", "search_files", "run_command"])
        self.assertEqual(agent["disallowed_tools"], ["write_file"])
        self.assertEqual(agent["model"], "inherit")
        self.assertEqual(agent["max_turns"], 6)
        self.assertTrue(agent["background"])
        self.assertIn("missing tests", agent["prompt"])

        additions = registry.get_prompt_additions()
        self.assertIn("code-reviewer", additions)
        self.assertIn("read_file, search_files, run_command", additions)
        self.assertIn("disallowed: write_file", additions)

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

        registry = SubagentRegistry()
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

    def test_builtin_subagents_are_available_and_can_be_selected(self):
        from core.subagents import SubagentRegistry

        tmp = self._tempdir()
        empty_dir = tmp / "empty_agents"
        empty_dir.mkdir(parents=True, exist_ok=True)
        settings_file = tmp / "subagents.json"
        registry = SubagentRegistry(
            agent_dirs=[str(empty_dir)],
            settings_file=str(settings_file),
        )
        registry.discover_and_load()

        explorer = registry.get_subagent("explorer")
        self.assertIsNotNone(explorer)
        self.assertTrue(explorer["builtin"])

        registry.set_default_selection("explorer")

        reloaded = SubagentRegistry(
            agent_dirs=[str(empty_dir)],
            settings_file=str(settings_file),
        )
        reloaded.discover_and_load()
        self.assertEqual(reloaded.get_default_selection(), "explorer")
        selector_values = {option["value"] for option in reloaded.get_selector_options()}
        self.assertIn("auto", selector_values)
        self.assertIn("explorer", selector_values)

    def test_prompt_additions_recommend_matching_builtin_subagent_for_turn(self):
        from core.subagents import SubagentRegistry

        tmp = self._tempdir()
        empty_dir = tmp / "empty_agents"
        empty_dir.mkdir(parents=True, exist_ok=True)
        registry = SubagentRegistry(agent_dirs=[str(empty_dir)])
        registry.discover_and_load()

        additions = registry.get_prompt_additions(
            "Review my auth changes for bugs and missing tests."
        )

        self.assertIn("`reviewer` is a strong match", additions)

    def test_recommend_subagent_uses_description_overlap_for_custom_agents(self):
        from core.subagents import SubagentRegistry

        tmp = self._tempdir()
        agents_dir = tmp / "agents"
        agents_dir.mkdir(parents=True, exist_ok=True)
        (agents_dir / "planner.md").write_text(
            "---\n"
            "name: planner\n"
            "description: Break down implementation plans into steps, risks, and verification tasks.\n"
            "---\n"
            "Turn requests into practical execution plans.\n",
            encoding="utf-8",
        )

        registry = SubagentRegistry(agent_dirs=[str(agents_dir)])
        registry.discover_and_load()

        recommendation = registry.recommend_subagent(
            "Break this implementation into steps and risks before coding."
        )

        self.assertIsNotNone(recommendation)
        self.assertEqual(recommendation[0], "planner")

    def test_project_subagent_can_shadow_builtin(self):
        from core.subagents import SubagentRegistry

        tmp = self._tempdir()
        project_dir = tmp / "project_agents"
        project_dir.mkdir(parents=True, exist_ok=True)
        (project_dir / "reviewer.md").write_text(
            "---\n"
            "name: reviewer\n"
            "description: Custom reviewer\n"
            "---\n"
            "Project-specific review instructions.\n",
            encoding="utf-8",
        )

        registry = SubagentRegistry(agent_dirs=[str(project_dir)])
        registry.discover_and_load()

        reviewer = registry.get_subagent("reviewer")
        self.assertEqual(reviewer["description"], "Custom reviewer")

        listed = registry.list_definitions()
        builtin_entry = next(item for item in listed if item["id"] == "builtin:reviewer")
        self.assertFalse(builtin_entry["active"])
        self.assertEqual(builtin_entry["shadowed_by"], "project_limebot:reviewer")

    def test_explicit_empty_tools_list_means_no_tools_not_inherit(self):
        from core.subagents import SubagentRegistry

        tmp = self._tempdir()
        agents_dir = tmp / "agents"
        agents_dir.mkdir(parents=True, exist_ok=True)

        registry = SubagentRegistry(agent_dirs=[str(agents_dir)])
        agent_file = agents_dir / "silent-runner.md"
        agent_file.write_text(
            registry.render_subagent_markdown(
                name="silent-runner",
                description="Runs without inherited tools.",
                prompt="Do not use tools unless explicitly provided elsewhere.",
                tools=[],
            ),
            encoding="utf-8",
        )

        registry.discover_and_load()
        saved = registry.get_subagent("silent-runner")

        self.assertIsNotNone(saved)
        self.assertEqual(saved["tools"], [])

        loaded = registry._load_subagent(agent_file, location="project_limebot")
        self.assertIsNotNone(loaded)
        self.assertEqual(loaded["tools"], [])

        additions = registry.get_prompt_additions()
        self.assertIn("no tools", additions)


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
        self.assertIn(
            "background",
            spawn_tool["function"]["parameters"]["properties"],
        )
        self.assertIn("matches that specialist's description", agent_param["description"])
