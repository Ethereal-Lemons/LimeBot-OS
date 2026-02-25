import unittest
from pathlib import Path


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
