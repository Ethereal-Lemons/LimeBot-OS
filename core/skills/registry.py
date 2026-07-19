"""
Skill Registry - Manages loaded skills and injects into LLM context.
"""

import os
import re
import shutil
from importlib import metadata as importlib_metadata
from typing import Dict, List, Any, Optional

from loguru import logger
from .loader import SkillLoader


_SKILL_INVENTORY_PATTERNS = (
    "what skills do you have",
    "what skill do you have",
    "look at your current skills",
    "current skills",
    "available skills",
    "what are your skills",
    "show skills",
    "list skills",
)


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
        self._skill_prompt_cache: Dict[str, str] = {}

    def discover_and_load(self) -> Dict[str, Dict[str, Any]]:
        """
        Discover all skills and load their API handlers if present.

        Returns:
            Dictionary of loaded skills
        """
        logger.info("Loading skills...")
        self.skills = self.loader.discover_skills()
        self._skill_prompt_cache.clear()
        if hasattr(self, "_cached_prompt_additions"):
            delattr(self, "_cached_prompt_additions")

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
                    missing = self._missing_dependencies(self.skills[name])
                    if missing:
                        logger.warning(
                            f"Skill '{name}' disabled because dependencies are missing: "
                            f"{', '.join(missing)}"
                        )
                        self.skills[name]["missing_dependencies"] = missing
                        continue
                    self.skills[name]["active"] = True
        else:
            logger.warning("No skills enabled (default secure state).")

        return self.skills

    @staticmethod
    def _missing_dependencies(skill: Dict[str, Any]) -> List[str]:
        dependencies = skill.get("dependencies") or {}
        if not isinstance(dependencies, dict):
            return []
        missing: List[str] = []
        python_packages = dependencies.get("python") or []
        if isinstance(python_packages, str):
            python_packages = [python_packages]
        for requirement in python_packages:
            package = re.split(
                r"(?:==|>=|<=|~=|!=|>|<)", str(requirement).strip(), maxsplit=1
            )[0]
            package = package.split("[", 1)[0].strip()
            if not package:
                continue
            try:
                importlib_metadata.version(package)
            except importlib_metadata.PackageNotFoundError:
                missing.append(f"python:{package}")

        binaries = dependencies.get("binaries") or []
        if isinstance(binaries, str):
            binaries = [binaries]
        missing.extend(
            str(binary)
            for binary in binaries
            if str(binary).strip() and shutil.which(str(binary).strip()) is None
        )
        return missing

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

    def _get_active_skills(self) -> Dict[str, Dict[str, Any]]:
        return {k: v for k, v in self.skills.items() if v.get("active", True)}

    @staticmethod
    def _tokenize(text: str) -> set[str]:
        stop_words = {
            "the",
            "and",
            "for",
            "with",
            "from",
            "are",
            "that",
            "this",
            "into",
            "your",
            "current",
            "currently",
            "have",
            "has",
            "will",
            "right",
            "now",
            "when",
            "using",
            "use",
            "via",
            "all",
            "new",
            "get",
            "skill",
            "skills",
            "available",
            "show",
            "list",
            "tell",
            "what",
            "look",
            "about",
            "connected",
            "correctly",
            "does",
            "need",
            "please",
            "agent",
            "bot",
            "their",
            "them",
            "they",
            "you",
        }
        tokens = {
            tok
            for tok in re.findall(r"[a-z0-9_/-]+", (text or "").lower())
            if len(tok) >= 3 and tok not in stop_words
        }
        expanded = set(tokens)
        for token in tokens:
            expanded.update(part for part in re.split(r"[_/-]+", token) if len(part) >= 3)
        return expanded

    def _skill_aliases(self, name: str, skill: Dict[str, Any]) -> set[str]:
        metadata = skill.get("metadata") or {}
        aliases = self._tokenize(name)

        explicit_aliases = metadata.get("aliases", [])
        if isinstance(explicit_aliases, list):
            aliases.update(self._tokenize(" ".join(str(x) for x in explicit_aliases)))
        elif explicit_aliases:
            aliases.update(self._tokenize(str(explicit_aliases)))

        explicit_keywords = metadata.get("keywords", [])
        if isinstance(explicit_keywords, list):
            aliases.update(self._tokenize(" ".join(str(x) for x in explicit_keywords)))
        elif explicit_keywords:
            aliases.update(self._tokenize(str(explicit_keywords)))

        return aliases

    def _skill_keywords(self, name: str, skill: Dict[str, Any]) -> set[str]:
        metadata = skill.get("metadata") or {}
        docs = "\n".join((skill.get("documentation") or "").splitlines()[:40])
        keywords = self._tokenize(f"{name} {skill.get('description', '')} {docs}")
        explicit = metadata.get("keywords", [])
        if isinstance(explicit, list):
            keywords.update(self._tokenize(" ".join(str(x) for x in explicit)))
        elif explicit:
            keywords.update(self._tokenize(str(explicit)))

        # High-value manual hints for terse user requests.
        hints = {
            "browser": {"web", "website", "search", "browse", "page", "url"},
            "discord": {"discord", "guild", "channel", "server", "embed"},
            "download_image": {"image", "photo", "wallpaper", "download"},
            "filesystem": {"file", "folder", "directory", "path", "read", "write"},
            "github": {"github", "repo", "repository", "pull", "pr", "branch"},
            "jira": {"jira", "ticket", "issue", "attachment", "attachments"},
            "scrapling": {"scrape", "scraping", "selector", "html", "extract"},
            "whatsapp": {"whatsapp", "jid", "media", "send"},
        }
        keywords.update(hints.get(name, set()))
        return keywords

    @staticmethod
    def _looks_like_skill_inventory_request(text: str) -> bool:
        lowered = (text or "").strip().lower()
        return any(pattern in lowered for pattern in _SKILL_INVENTORY_PATTERNS)

    def _format_active_skill_summary(self, skills: Dict[str, Dict[str, Any]]) -> str:
        if not skills:
            return ""

        sections = ["\n## Active Skills\n"]
        sections.append(
            "These are the skills currently enabled for this session. "
            "If one sounds relevant, follow its manual; it may call native tools or use a `run_command` workflow.\n"
        )
        for name, skill in skills.items():
            description = str(skill.get("description") or "").strip()
            sections.append(f"- `{name}`: {description}")
        sections.append("")
        return "\n".join(sections)

    @staticmethod
    def _normalized_name(text: str) -> str:
        return re.sub(r"[^a-z0-9]+", "_", (text or "").lower()).strip("_")

    def _format_prompt_additions(self, skills: Dict[str, Dict[str, Any]]) -> str:
        if not skills:
            return ""

        sections = ["\n## Available Skills\n"]
        sections.append(
            "These are skill manuals, not callable tool names. Follow each manual: "
            "it may direct a native tool call or a `run_command` workflow.\n"
        )

        for name, skill in skills.items():
            sections.append(f"### {name}")
            sections.append(f"*{skill['description']}*\n")
            sections.append(skill["documentation"])
            sections.append("")

        return "\n".join(sections)

    @staticmethod
    def _metadata_aliases(skill: Dict[str, Any]) -> list[str]:
        metadata = skill.get("metadata") or {}
        aliases = metadata.get("aliases", [])
        if isinstance(aliases, list):
            return [str(alias).strip() for alias in aliases if str(alias).strip()]
        if aliases:
            alias = str(aliases).strip()
            return [alias] if alias else []
        return []

    def get_system_prompt_additions(self) -> str:
        """
        Generate skill documentation to append to LLM system prompt.
        Cached after first call since skills don't change at runtime.

        Returns:
            Formatted string with all skill documentation
        """
        if hasattr(self, "_cached_prompt_additions"):
            return self._cached_prompt_additions

        active_skills = self._get_active_skills()

        if not active_skills:
            self._cached_prompt_additions = ""
            return ""

        self._cached_prompt_additions = self._format_prompt_additions(active_skills)
        return self._cached_prompt_additions

    def list_active_skill_names(self) -> List[str]:
        return sorted(self._get_active_skills().keys())

    def get_required_tool_names(self, skill_name: Optional[str]) -> List[str]:
        """Return native tools an active skill requires for its workflow."""
        if not skill_name:
            return []
        skill = self._get_active_skills().get(skill_name)
        if not skill:
            return []
        metadata = skill.get("metadata") or {}
        required = metadata.get("required_tools", [])
        if isinstance(required, str):
            required = [required]
        if not isinstance(required, list):
            return []
        return list(
            dict.fromkeys(
                str(name).strip()
                for name in required
                if str(name).strip()
            )
        )

    def resolve_active_skill_name(self, requested_name: str) -> Optional[str]:
        requested = str(requested_name or "").strip()
        if not requested:
            return None

        active_skills = self._get_active_skills()
        if not active_skills:
            return None

        exact_matches = [
            name for name in active_skills if name.lower() == requested.lower()
        ]
        if len(exact_matches) == 1:
            return exact_matches[0]
        if len(exact_matches) > 1:
            return None

        normalized_requested = self._normalized_name(requested)
        matches: list[str] = []
        for name, skill in active_skills.items():
            candidates = {self._normalized_name(name)}
            candidates.update(
                self._normalized_name(alias)
                for alias in self._metadata_aliases(skill)
                if alias
            )
            if normalized_requested in candidates:
                matches.append(name)

        if len(matches) == 1:
            return matches[0]
        return None

    def get_forced_prompt_addition(self, skill_name: str) -> str:
        active_skills = self._get_active_skills()
        skill = active_skills.get(skill_name)
        if not skill:
            return ""

        preface = (
            "\n## Forced Skill\n"
            "The user explicitly invoked this skill with a slash command. "
            "Follow this skill manual for the current turn unless it conflicts "
            "with higher-priority system, safety, or tool-confirmation rules.\n"
        )
        return preface + self._format_prompt_additions({skill_name: skill})

    def get_relevant_prompt_additions(
        self, user_text: str, max_skills: int = 3
    ) -> str:
        """
        Return only the skills that are plausibly relevant to this user turn.
        Falls back to no skill docs instead of dumping every enabled skill.
        """
        active_skills = self._get_active_skills()
        if not active_skills:
            return ""

        text = (user_text or "").strip()
        if not text:
            return ""

        cache_key = f"{text.lower()}::{max_skills}"
        cached = self._skill_prompt_cache.get(cache_key)
        if cached is not None:
            return cached

        if self._looks_like_skill_inventory_request(text):
            additions = self._format_active_skill_summary(active_skills)
            self._skill_prompt_cache[cache_key] = additions
            return additions

        tokens = self._tokenize(text)
        lowered = text.lower()
        normalized_lowered = self._normalized_name(lowered)
        scored: list[tuple[int, str, Dict[str, Any]]] = []

        for name, skill in active_skills.items():
            aliases = self._skill_aliases(name, skill)
            keywords = self._skill_keywords(name, skill)
            alias_overlap = len(tokens & aliases)
            keyword_overlap = len(tokens & keywords)
            score = keyword_overlap + alias_overlap * 6

            explicit_name = self._normalized_name(name) in normalized_lowered
            if explicit_name:
                score += 50

            if "http" in lowered or "www." in lowered:
                if name in {"browser", "scrapling", "download_image"}:
                    score += 3

            if alias_overlap > 0:
                score += 2

            if score > 0:
                scored.append((score, name, skill))

        scored.sort(key=lambda item: (-item[0], item[1]))
        selected = {name: skill for _, name, skill in scored[:max_skills]}
        additions = self._format_prompt_additions(selected)
        self._skill_prompt_cache[cache_key] = additions
        return additions

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
