"""
Skill Registry - Manages loaded skills and injects into LLM context.
"""

import os
from typing import Dict, List, Any, Optional

from loguru import logger
from .loader import SkillLoader


class SkillRegistry:
    """
    Central registry for all loaded skills.
    Provides skill documentation for LLM system prompt injection.
    """

    def __init__(self, skill_dirs: List[str] = None, config: Dict = None):
        """
        Initialize the skill registry.

        Args:
            skill_dirs: Directories to scan for skills
            config: Optional config dict with skill-specific settings
        """
        self.config = config or {}
        self.loader = SkillLoader(skill_dirs)
        self.skills: Dict[str, Dict[str, Any]] = {}
        self._api_handlers: Dict[str, Any] = {}

    def discover_and_load(self) -> Dict[str, Dict[str, Any]]:
        """
        Discover all skills and load their API handlers if present.

        Returns:
            Dictionary of loaded skills
        """
        logger.info("Loading skills...")
        self.skills = self.loader.discover_skills()

        for name, skill in self.skills.items():
            if skill.get("has_api"):
                self._load_api_handler(name, skill)

        logger.info(f"Loaded {len(self.skills)} skills")

        enabled_skills = []

        if self.config:
            if hasattr(self.config, "skills"):
                skills_cfg = self.config.skills
                if hasattr(skills_cfg, "enabled"):
                    enabled_skills = skills_cfg.enabled

            elif isinstance(self.config, dict) and "skills" in self.config:
                skills_cfg = self.config["skills"]

                enabled_skills = skills_cfg.get("enabled", [])

        for name in self.skills:
            self.skills[name]["active"] = False

        if enabled_skills:
            logger.info(f"Enabled skills: {enabled_skills}")
            for name in enabled_skills:
                if name in self.skills:
                    self.skills[name]["active"] = True
        else:
            logger.warning("No skills enabled (default secure state).")

        return self.skills

    def _load_api_handler(self, skill_name: str, skill: Dict) -> None:
        """Load the api.py handler module for a skill."""
        import importlib.util
        from pathlib import Path

        api_path = Path(skill["base_dir"]) / "api.py"
        if not api_path.exists():
            return

        try:
            spec = importlib.util.spec_from_file_location(
                f"skill_api_{skill_name}", api_path
            )
            if spec and spec.loader:
                module = importlib.util.module_from_spec(spec)
                spec.loader.exec_module(module)

                if hasattr(module, "handle"):
                    self._api_handlers[skill_name] = module.handle
                    logger.debug(f"    API handler loaded for: {skill_name}")
                elif hasattr(module, "SkillHandler"):
                    self._api_handlers[skill_name] = module.SkillHandler()
                    logger.debug(f"    API handler class loaded for: {skill_name}")
        except SystemExit as e:
            logger.error(
                f"    API handler for {skill_name} exited during import (code={e.code}). Skipping."
            )
        except Exception as e:
            logger.error(f"    Error loading API handler for {skill_name}: {e}")

    def get_system_prompt_additions(self) -> str:
        """
        Generate skill documentation to append to LLM system prompt.
        Cached after first call since skills don't change at runtime.

        Returns:
            Formatted string with all skill documentation
        """
        if hasattr(self, "_cached_prompt_additions"):
            return self._cached_prompt_additions

        active_skills = {k: v for k, v in self.skills.items() if v.get("active", True)}

        if not active_skills:
            self._cached_prompt_additions = ""
            return ""

        sections = ["\n## Available Skills\n"]
        sections.append("Use the `run_command` tool to execute skill commands.\n")

        for name, skill in active_skills.items():
            sections.append(f"### {name}")
            sections.append(f"*{skill['description']}*\n")
            sections.append(skill["documentation"])
            sections.append("")

        self._cached_prompt_additions = "\n".join(sections)
        return self._cached_prompt_additions

    def get_skill_env(self, skill_name: str) -> Dict[str, str]:
        """
        Get environment variables for a skill execution.
        Injects API keys and config from the main config.

        Args:
            skill_name: Name of the skill

        Returns:
            Environment dictionary with skill-specific variables
        """
        env = os.environ.copy()

        skill_entries = self.config.get("skills", {}).get("entries", {})
        skill_config = skill_entries.get(skill_name, {})

        if "apiKey" in skill_config:
            env_key = f"{skill_name.upper().replace('-', '_')}_API_KEY"
            env[env_key] = skill_config["apiKey"]

        for key, value in skill_config.items():
            if key != "apiKey" and isinstance(value, str):
                env_key = f"{skill_name.upper().replace('-', '_')}_{key.upper()}"
                env[env_key] = value

        return env

    def get_skill(self, skill_name: str) -> Optional[Dict[str, Any]]:
        """Get a skill by name."""
        return self.skills.get(skill_name)

    def has_api_handler(self, skill_name: str) -> bool:
        """Check if a skill has an API handler."""
        return skill_name in self._api_handlers

    def get_api_handler(self, skill_name: str):
        """Get the API handler for a skill."""
        return self._api_handlers.get(skill_name)

    def reload_skill(self, skill_name: str) -> bool:
        """Reload a specific skill."""
        if self.loader.reload_skill(skill_name):
            skill = self.loader.skills.get(skill_name)
            if skill:
                self.skills[skill_name] = skill
                if skill.get("has_api"):
                    self._load_api_handler(skill_name, skill)
                return True
        return False
