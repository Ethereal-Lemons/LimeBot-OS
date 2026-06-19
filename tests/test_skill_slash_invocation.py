import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from channels.web import _extract_client_prompt_metadata
from core.skill_invocation import parse_skill_invocation


class TestSkillInvocationParser(unittest.TestCase):
    def test_parse_inventory_forms(self):
        parsed = parse_skill_invocation("/skills")
        self.assertEqual(parsed.kind, "inventory")

        parsed = parse_skill_invocation("/skill list")
        self.assertEqual(parsed.kind, "inventory")

    def test_parse_explicit_and_shorthand_skill_forms(self):
        explicit = parse_skill_invocation("/skill improve audit the repo")
        shorthand = parse_skill_invocation("/improve audit the repo")

        self.assertEqual(explicit.kind, "skill")
        self.assertEqual(explicit.requested_name, "improve")
        self.assertEqual(explicit.task, "audit the repo")
        self.assertEqual(shorthand.kind, "skill")
        self.assertEqual(shorthand.requested_name, "improve")
        self.assertEqual(shorthand.task, "audit the repo")

    def test_parse_shorthand_skill_without_task(self):
        parsed = parse_skill_invocation("/improve")

        self.assertEqual(parsed.kind, "skill")
        self.assertEqual(parsed.requested_name, "improve")
        self.assertEqual(parsed.task, "")

    def test_rejects_path_like_strings(self):
        self.assertEqual(parse_skill_invocation("/path/to/file").kind, "none")
        self.assertEqual(parse_skill_invocation("/C:/Users/brite/skill.md").kind, "none")
        self.assertEqual(parse_skill_invocation("/skills/local").kind, "none")


class TestSkillRegistrySlashResolution(unittest.TestCase):
    def _build_registry(self):
        from core.skills import SkillRegistry

        temp_dir = TemporaryDirectory()
        root = Path(temp_dir.name)
        skill_specs = {
            "improve": (
                "---\nname: improve\ndescription: Planning skill\nmetadata:\n"
                "  aliases:\n    - planner-mode\n    - slash-plan\n---\n"
                "Use this skill to create plans.\n"
            ),
            "browser": (
                "---\nname: browser\ndescription: Browser skill\n---\n"
                "Use this skill for browsing.\n"
            ),
        }

        for name, content in skill_specs.items():
            skill_dir = root / name
            skill_dir.mkdir(parents=True, exist_ok=True)
            (skill_dir / "SKILL.md").write_text(content, encoding="utf-8")

        registry = SkillRegistry(
            skill_dirs=[str(root)],
            config={"skills": {"enabled": list(skill_specs.keys())}},
        )
        registry.discover_and_load()
        return temp_dir, registry

    def test_resolve_active_skill_name_matches_name_and_alias(self):
        temp_dir, registry = self._build_registry()
        try:
            self.assertEqual(registry.resolve_active_skill_name("improve"), "improve")
            self.assertEqual(registry.resolve_active_skill_name("Improve"), "improve")
            self.assertEqual(
                registry.resolve_active_skill_name("planner-mode"), "improve"
            )
            self.assertEqual(
                registry.resolve_active_skill_name("planner mode"), "improve"
            )
        finally:
            temp_dir.cleanup()

    def test_resolve_active_skill_name_returns_none_when_ambiguous(self):
        from core.skills import SkillRegistry

        with TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            skill_specs = {
                "alpha": (
                    "---\nname: alpha\ndescription: Alpha skill\nmetadata:\n"
                    "  aliases:\n    - helper\n---\nAlpha docs.\n"
                ),
                "beta": (
                    "---\nname: beta\ndescription: Beta skill\nmetadata:\n"
                    "  aliases:\n    - helper\n---\nBeta docs.\n"
                ),
            }
            for name, content in skill_specs.items():
                skill_dir = root / name
                skill_dir.mkdir(parents=True, exist_ok=True)
                (skill_dir / "SKILL.md").write_text(content, encoding="utf-8")

            registry = SkillRegistry(
                skill_dirs=[str(root)],
                config={"skills": {"enabled": list(skill_specs.keys())}},
            )
            registry.discover_and_load()

            self.assertIsNone(registry.resolve_active_skill_name("helper"))

    def test_get_forced_prompt_addition_limits_docs_to_requested_skill(self):
        temp_dir, registry = self._build_registry()
        try:
            additions = registry.get_forced_prompt_addition("improve")

            self.assertIn("## Forced Skill", additions)
            self.assertIn("### improve", additions)
            self.assertIn("Use this skill to create plans.", additions)
            self.assertNotIn("### browser", additions)
        finally:
            temp_dir.cleanup()


class TestAgentLoopSkillInvocation(unittest.IsolatedAsyncioTestCase):
    async def test_build_full_system_prompt_uses_forced_skill_docs_when_present(self):
        from core.loop import AgentLoop

        agent = object.__new__(AgentLoop)
        agent._get_stable_prompt = AsyncMock(return_value="STABLE\n")
        agent.skill_registry = SimpleNamespace(
            get_relevant_prompt_additions=lambda _: "AUTO\n",
            get_forced_prompt_addition=lambda name: f"FORCED:{name}\n",
        )
        agent.subagent_registry = SimpleNamespace(get_prompt_additions=lambda _: "")
        agent.config = SimpleNamespace(
            llm=SimpleNamespace(enable_dynamic_personality=False),
            personality_whitelist=[],
        )

        with patch(
            "core.loop.prompt_module.should_load_private_context",
            return_value=False,
        ), patch(
            "core.loop.prompt_module.get_volatile_prompt_suffix",
            return_value="VOLATILE\n",
        ):
            forced_prompt = await agent._build_full_system_prompt(
                "user",
                "web",
                "chat",
                current_message="hello",
                forced_skill_name="improve",
            )
            natural_prompt = await agent._build_full_system_prompt(
                "user",
                "web",
                "chat",
                current_message="hello",
            )

        self.assertIn("FORCED:improve", forced_prompt)
        self.assertNotIn("AUTO", forced_prompt)
        self.assertIn("AUTO", natural_prompt)

    async def test_resolve_skill_invocation_rewrites_inventory_and_shorthand(self):
        from core.loop import AgentLoop

        agent = object.__new__(AgentLoop)
        agent.skill_registry = SimpleNamespace(
            resolve_active_skill_name=lambda name: "improve"
            if name.lower() == "improve"
            else None,
            list_active_skill_names=lambda: ["browser", "improve"],
        )

        inventory = agent._resolve_skill_invocation("/skills")
        shorthand = agent._resolve_skill_invocation("/improve audit this")
        no_task = agent._resolve_skill_invocation("/improve")

        self.assertEqual(inventory, ("what skills do you have right now", None, None))
        self.assertEqual(shorthand, ("audit this", "improve", None))
        self.assertEqual(no_task, ("Use the improve skill.", "improve", None))

    async def test_resolve_skill_invocation_returns_helpful_error_for_unknown_skill(self):
        from core.loop import AgentLoop

        agent = object.__new__(AgentLoop)
        agent.skill_registry = SimpleNamespace(
            resolve_active_skill_name=lambda _: None,
            list_active_skill_names=lambda: ["browser", "improve"],
        )

        content, forced_skill_name, skill_error = agent._resolve_skill_invocation(
            "/unknown do something"
        )

        self.assertEqual(content, "/unknown do something")
        self.assertIsNone(forced_skill_name)
        self.assertIn("`unknown`", skill_error)
        self.assertIn("`improve`", skill_error)

    async def test_resolve_requested_skill_supports_metadata_driven_selection(self):
        from core.loop import AgentLoop

        agent = object.__new__(AgentLoop)
        agent.skill_registry = SimpleNamespace(
            resolve_active_skill_name=lambda name: "discord"
            if name.lower() == "discord"
            else None,
            list_active_skill_names=lambda: ["browser", "discord"],
        )

        rewritten, forced_skill_name, skill_error = agent._resolve_requested_skill(
            "discord",
            "draft a reply",
        )

        self.assertEqual(rewritten, "draft a reply")
        self.assertEqual(forced_skill_name, "discord")
        self.assertIsNone(skill_error)


class TestWebPromptMetadata(unittest.TestCase):
    def test_extract_client_prompt_metadata_accepts_skill_name(self):
        extracted = _extract_client_prompt_metadata(
            {"metadata": {"skill_name": "discord", "ponytail_mode": "full"}}
        )

        self.assertEqual(
            extracted,
            {"ponytail_mode": "full", "skill_name": "discord"},
        )

    def test_extract_client_prompt_metadata_rejects_invalid_skill_name(self):
        extracted = _extract_client_prompt_metadata(
            {"metadata": {"skill_name": "../discord"}}
        )

        self.assertEqual(extracted, {})
