import json
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory


class TestSkillInstaller(unittest.TestCase):
    def test_enable_blocks_when_dependencies_are_missing(self):
        from core import skill_installer as installer_module

        with TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            skills_dir = root / "skills"
            skills_dir.mkdir(parents=True, exist_ok=True)
            config_file = root / "limebot.json"
            config_file.write_text(
                json.dumps({"skills": {"enabled": [], "installed": {}}}),
                encoding="utf-8",
            )

            skill_dir = skills_dir / "missing_dep_skill"
            skill_dir.mkdir(parents=True, exist_ok=True)
            (skill_dir / "SKILL.md").write_text(
                "---\nname: missing_dep_skill\ndescription: test\n---\nMissing dep skill.\n",
                encoding="utf-8",
            )
            (skill_dir / "requirements.txt").write_text(
                "definitely-missing-skill-dep-zzz==1.0.0\n",
                encoding="utf-8",
            )

            original_skills_dir = installer_module.SKILLS_DIR
            original_claw_dir = installer_module.CLAW_SKILLS_DIR
            original_config = installer_module.CONFIG_FILE
            installer_module.SKILLS_DIR = skills_dir
            installer_module.CLAW_SKILLS_DIR = skills_dir / "clawhub" / "installed"
            installer_module.CONFIG_FILE = config_file
            try:
                installer = installer_module.SkillInstaller()
                result = installer.enable("missing_dep_skill")
            finally:
                installer_module.SKILLS_DIR = original_skills_dir
                installer_module.CLAW_SKILLS_DIR = original_claw_dir
                installer_module.CONFIG_FILE = original_config

            self.assertEqual(result["status"], "error")
            self.assertEqual(result.get("code"), "deps_missing")
            self.assertIn("missing dependencies", result["message"].lower())

    def test_list_skills_marks_missing_dep_skill_not_active(self):
        from core import skill_installer as installer_module

        with TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            skills_dir = root / "skills"
            skills_dir.mkdir(parents=True, exist_ok=True)
            config_file = root / "limebot.json"
            config_file.write_text(
                json.dumps(
                    {"skills": {"enabled": ["missing_dep_skill"], "installed": {}}}
                ),
                encoding="utf-8",
            )

            skill_dir = skills_dir / "missing_dep_skill"
            skill_dir.mkdir(parents=True, exist_ok=True)
            (skill_dir / "SKILL.md").write_text(
                "---\nname: missing_dep_skill\ndescription: test\n---\nMissing dep skill.\n",
                encoding="utf-8",
            )
            (skill_dir / "requirements.txt").write_text(
                "definitely-missing-skill-dep-zzz==1.0.0\n",
                encoding="utf-8",
            )

            original_skills_dir = installer_module.SKILLS_DIR
            original_claw_dir = installer_module.CLAW_SKILLS_DIR
            original_config = installer_module.CONFIG_FILE
            installer_module.SKILLS_DIR = skills_dir
            installer_module.CLAW_SKILLS_DIR = skills_dir / "clawhub" / "installed"
            installer_module.CONFIG_FILE = config_file
            try:
                installer = installer_module.SkillInstaller()
                result = installer.list_skills()
            finally:
                installer_module.SKILLS_DIR = original_skills_dir
                installer_module.CLAW_SKILLS_DIR = original_claw_dir
                installer_module.CONFIG_FILE = original_config

            skill = next(item for item in result["skills"] if item["id"] == "missing_dep_skill")
            self.assertTrue(skill["enabled"])
            self.assertFalse(skill["deps_ok"])
            self.assertFalse(skill["active"])

