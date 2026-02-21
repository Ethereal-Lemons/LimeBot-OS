"""
Skill Loader - Discovers and parses skills from directories.
"""

import re
from pathlib import Path
from typing import Dict, List, Optional, Any

from loguru import logger

try:
    import yaml

    HAS_YAML = True
except ImportError:
    HAS_YAML = False


class SkillLoader:
    """
    Discovers and loads skills from the skills directory.
    Parses SKILL.md files to extract metadata and documentation.
    """

    def __init__(self, skill_dirs: List[str] = None):
        """
        Initialize the skill loader.

        Args:
            skill_dirs: List of directories to scan for skills.
                       Defaults to ['./skills']
        """
        self.skill_dirs = skill_dirs or ["./skills"]
        self.skills: Dict[str, Dict[str, Any]] = {}

    def discover_skills(self) -> Dict[str, Dict[str, Any]]:
        """
        Scan configured directories for skills (folders with SKILL.md).

        Returns:
            Dictionary of skill_name -> skill_data
        """
        for skill_dir in self.skill_dirs:
            dir_path = Path(skill_dir)
            if not dir_path.exists():
                continue

            for folder in dir_path.iterdir():
                if folder.is_dir() and not folder.name.startswith(("__", ".")):
                    skill_md = folder / "SKILL.md"
                    if skill_md.exists():
                        try:
                            skill = self._load_skill(skill_md)
                            if skill:
                                self.skills[skill["name"]] = skill
                                logger.debug(f"  - Loaded skill: {skill['name']}")
                        except Exception as e:
                            logger.error(f"  - Error loading skill {folder.name}: {e}")

        return self.skills

    def _load_skill(self, skill_md: Path) -> Optional[Dict[str, Any]]:
        """
        Parse a SKILL.md file and extract metadata + documentation.

        Args:
            skill_md: Path to the SKILL.md file

        Returns:
            Skill data dictionary or None if parsing fails
        """
        content = skill_md.read_text(encoding="utf-8")
        base_dir = str(skill_md.parent.absolute()).replace("\\", "/")

        metadata = {}
        body = content

        if content.startswith("---"):
            match = re.match(r"^---\s*\n(.*?)\n---\s*\n(.*)$", content, re.DOTALL)
            if match:
                frontmatter = match.group(1)
                body = match.group(2)
                metadata = self._parse_frontmatter(frontmatter)

        documentation = body.strip().replace("{baseDir}", base_dir)

        name = metadata.get("name", skill_md.parent.name)

        requires_backend = metadata.get("requires_backend", False)

        has_api = (skill_md.parent / "api.py").exists()

        return {
            "name": name,
            "description": metadata.get("description", ""),
            "base_dir": base_dir,
            "documentation": documentation,
            "metadata": metadata.get("metadata", {}),
            "requires_backend": requires_backend or has_api,
            "has_api": has_api,
        }

    def _parse_frontmatter(self, frontmatter: str) -> Dict[str, Any]:
        """
        Parse YAML frontmatter, with fallback to simple parsing if yaml not available.
        """
        if HAS_YAML:
            try:
                return yaml.safe_load(frontmatter) or {}
            except Exception:
                pass

        result = {}
        for line in frontmatter.splitlines():
            line = line.strip()
            if ":" in line and not line.startswith("{"):
                key, value = line.split(":", 1)
                key = key.strip()
                value = value.strip()

                if value.startswith('"') and value.endswith('"'):
                    value = value[1:-1]
                elif value.startswith("'") and value.endswith("'"):
                    value = value[1:-1]

                elif value.lower() == "true":
                    value = True
                elif value.lower() == "false":
                    value = False
                result[key] = value

        return result

    def reload_skill(self, skill_name: str) -> bool:
        """
        Reload a specific skill from disk.

        Returns:
            True if skill was found and reloaded
        """
        for skill_dir in self.skill_dirs:
            skill_path = Path(skill_dir) / skill_name / "SKILL.md"
            if skill_path.exists():
                skill = self._load_skill(skill_path)
                if skill:
                    self.skills[skill["name"]] = skill
                    return True
        return False
