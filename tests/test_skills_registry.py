import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch


class TestSkillsRegistry(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        try:
            import loguru  # noqa: F401
        except Exception:
            raise unittest.SkipTest("Missing dependencies (loguru).")

    async def test_skill_registry_loads_custom_skill(self):
        from core.skills import SkillRegistry

        skill_name = "test_skill_temp"
        skill_dir = Path("skills") / skill_name
        skill_md = skill_dir / "SKILL.md"
        skill_dir.mkdir(parents=True, exist_ok=True)

        skill_md.write_text(
            "---\nname: test_skill_temp\ndescription: Test skill\n---\n"
            "Use this skill for testing.\n",
            encoding="utf-8",
        )

        try:
            registry = SkillRegistry(
                skill_dirs=["./skills"],
                config={"skills": {"enabled": [skill_name]}},
            )
            registry.discover_and_load()
            additions = registry.get_system_prompt_additions()

            self.assertIn("test_skill_temp", additions)
            self.assertIn("Use this skill for testing.", additions)
        finally:
            if skill_md.exists():
                skill_md.unlink()
            if skill_dir.exists():
                skill_dir.rmdir()

    async def test_skill_registry_prefers_alias_match_over_generic_skill_words(self):
        from core.skills import SkillRegistry

        with TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            skill_specs = {
                "filesystem": (
                    "---\nname: filesystem\ndescription: File management skill\n---\n"
                    "Use this skill for file operations.\n"
                ),
                "jira": (
                    "---\nname: jira\ndescription: Jira ticket skill\n---\n"
                    "Use this skill for tickets and issues.\n"
                ),
                "manage_cafeteria_credit": (
                    "---\nname: manage_cafeteria_credit\ndescription: Manage cafeteria credit overrides\n"
                    "metadata:\n  keywords:\n    - cafeteria\n    - credit\n    - badge\n---\n"
                    "Use this skill to manage cafeteria credit over FTPS.\n"
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

            additions = registry.get_relevant_prompt_additions(
                "use your cafeteria skill and tell me if you are correctly connected"
            )

            self.assertIn("### manage_cafeteria_credit", additions)
            self.assertNotIn("### filesystem", additions)

    async def test_skill_inventory_request_returns_active_skill_summary(self):
        from core.skills import SkillRegistry

        with TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            skill_specs = {
                "jira": (
                    "---\nname: jira\ndescription: Jira ticket skill\n---\n"
                    "Use this skill for tickets and issues.\n"
                ),
                "manage_cafeteria_credit": (
                    "---\nname: manage_cafeteria_credit\ndescription: Manage cafeteria credit overrides\n---\n"
                    "Use this skill to manage cafeteria credit over FTPS.\n"
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

            additions = registry.get_relevant_prompt_additions(
                "what skills do you have right now"
            )

            self.assertIn("## Active Skills", additions)
            self.assertIn("`jira`", additions)
            self.assertIn("`manage_cafeteria_credit`", additions)
            self.assertNotIn("### jira", additions)

    async def test_skill_with_missing_binary_is_not_activated(self):
        from core.skills import SkillRegistry

        with TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            skill_dir = root / "external-browser"
            skill_dir.mkdir(parents=True)
            (skill_dir / "SKILL.md").write_text(
                "---\n"
                "name: external-browser\n"
                "description: External browser\n"
                "dependencies:\n"
                "  binaries:\n"
                "    - definitely-not-installed-browser-command\n"
                "---\n"
                "Use the external browser.\n",
                encoding="utf-8",
            )

            registry = SkillRegistry(
                skill_dirs=[str(root)],
                config={"skills": {"enabled": ["external-browser"]}},
            )
            with patch("core.skills.registry.shutil.which", return_value=None):
                registry.discover_and_load()

            self.assertFalse(registry.skills["external-browser"]["active"])
            self.assertEqual(
                registry.skills["external-browser"]["missing_dependencies"],
                ["definitely-not-installed-browser-command"],
            )
            self.assertNotIn(
                "external-browser", registry.get_system_prompt_additions()
            )

    async def test_skill_with_missing_python_package_is_not_activated(self):
        from core.skills import SkillRegistry

        with TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            skill_dir = root / "python-dependent"
            skill_dir.mkdir(parents=True)
            (skill_dir / "SKILL.md").write_text(
                "---\n"
                "name: python-dependent\n"
                "description: Needs a package\n"
                "dependencies:\n"
                "  python:\n"
                "    - definitely-not-a-real-python-package>=1.0\n"
                "---\n"
                "Use the package.\n",
                encoding="utf-8",
            )

            registry = SkillRegistry(
                skill_dirs=[str(root)],
                config={"skills": {"enabled": ["python-dependent"]}},
            )
            registry.discover_and_load()

            self.assertFalse(registry.skills["python-dependent"]["active"])
            self.assertEqual(
                registry.skills["python-dependent"]["missing_dependencies"],
                ["python:definitely-not-a-real-python-package"],
            )

    async def test_active_skill_exposes_declared_required_tools(self):
        from core.skills import SkillRegistry

        with TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            skill_dir = root / "document-maker"
            skill_dir.mkdir(parents=True)
            (skill_dir / "SKILL.md").write_text(
                "---\n"
                "name: document-maker\n"
                "description: Create documents\n"
                "metadata:\n"
                "  required_tools:\n"
                "    - run_command\n"
                "    - send_media\n"
                "---\n"
                "Create and deliver documents.\n",
                encoding="utf-8",
            )

            registry = SkillRegistry(
                skill_dirs=[str(root)],
                config={"skills": {"enabled": ["document-maker"]}},
            )
            registry.discover_and_load()

            self.assertEqual(
                registry.get_required_tool_names("document-maker"),
                ["run_command", "send_media"],
            )
            self.assertEqual(registry.get_required_tool_names("missing"), [])
