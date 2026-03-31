"""
Subagent registry for Claude-style Markdown agent profiles.
"""

from __future__ import annotations

import re
import json
from pathlib import Path
from typing import Any, Dict, List, Optional

from loguru import logger

try:
    import yaml

    HAS_YAML = True
except ImportError:
    HAS_YAML = False


CLAUDE_TOOL_ALIASES: Dict[str, str] = {
    "read": "read_file",
    "write": "write_file",
    "edit": "write_file",
    "multiedit": "write_file",
    "delete": "delete_file",
    "ls": "list_dir",
    "glob": "search_files",
    "grep": "search_files",
    "bash": "run_command",
    "task": "spawn_agent",
    "websearch": "google_search",
    "webfetch": "browser_get_page_text",
}

SUBAGENT_LOCATIONS: Dict[str, Dict[str, str]] = {
    "project_limebot": {
        "label": "Project (.limebot/agents)",
        "path": ".limebot/agents",
    },
    "project_claude": {
        "label": "Project (.claude/agents)",
        "path": ".claude/agents",
    },
    "user_limebot": {
        "label": "Personal (~/.limebot/agents)",
        "path": str(Path.home() / ".limebot" / "agents"),
    },
    "user_claude": {
        "label": "Personal (~/.claude/agents)",
        "path": str(Path.home() / ".claude" / "agents"),
    },
}

LEGACY_LOCATION_ALIASES: Dict[str, str] = {
    "project": "project_claude",
    "user": "user_claude",
}


def normalize_subagent_tool_name(name: str) -> str:
    raw = str(name or "").strip()
    if not raw:
        return ""

    lowered = raw.lower().replace("-", "_")
    return CLAUDE_TOOL_ALIASES.get(lowered, lowered)


def slugify_subagent_filename(name: str) -> str:
    text = re.sub(r"[^a-zA-Z0-9_-]+", "-", str(name or "").strip()).strip("-_")
    return (text or "subagent").lower()


class SubagentRegistry:
    """
    Discover and serve named subagent profiles from Markdown files.

    Supported locations mirror Claude Code's project/user split:
    - ./.claude/agents/*.md
    - ~/.claude/agents/*.md
    """

    def __init__(self, agent_dirs: Optional[List[str]] = None):
        self.agent_dirs = agent_dirs or [
            SUBAGENT_LOCATIONS["project_limebot"]["path"],
            SUBAGENT_LOCATIONS["project_claude"]["path"],
            SUBAGENT_LOCATIONS["user_limebot"]["path"],
            SUBAGENT_LOCATIONS["user_claude"]["path"],
        ]
        self.subagents: Dict[str, Dict[str, Any]] = {}

    def _iter_agent_dirs(self) -> List[tuple[str, Path]]:
        resolved: List[tuple[str, Path]] = []
        for raw_dir in self.agent_dirs:
            agent_dir = Path(raw_dir).expanduser()
            location = self._infer_location_from_path(agent_dir)
            resolved.append((location, agent_dir))
        return resolved

    def _resolve_location_dir(self, location: str) -> Path:
        normalized = self._normalize_location(location)
        return Path(SUBAGENT_LOCATIONS[normalized]["path"]).expanduser()

    def _normalize_location(self, location: Optional[str]) -> str:
        normalized = str(location or "project_limebot").strip().lower()
        normalized = LEGACY_LOCATION_ALIASES.get(normalized, normalized)
        if normalized not in SUBAGENT_LOCATIONS:
            raise ValueError("Invalid subagent location")
        return normalized

    def _infer_location_from_path(self, agent_dir: Path) -> str:
        try:
            resolved = agent_dir.expanduser().resolve()
        except Exception:
            resolved = agent_dir.expanduser()

        for location, meta in SUBAGENT_LOCATIONS.items():
            candidate = Path(meta["path"]).expanduser()
            try:
                if resolved == candidate.resolve():
                    return location
            except Exception:
                if str(resolved) == str(candidate):
                    return location
        return "project_limebot"

    @staticmethod
    def make_subagent_id(location: str, filename: str) -> str:
        return f"{location}:{filename}"

    @staticmethod
    def parse_subagent_id(subagent_id: str) -> tuple[str, str]:
        raw = str(subagent_id or "").strip()
        if ":" not in raw:
            raise ValueError("Invalid subagent id")
        location, filename = raw.split(":", 1)
        location = LEGACY_LOCATION_ALIASES.get(location, location)
        if location not in SUBAGENT_LOCATIONS:
            raise ValueError("Invalid subagent location")
        if not filename:
            raise ValueError("Invalid subagent filename")
        return location, filename

    def get_location_options(self) -> List[Dict[str, str]]:
        return [
            {"value": key, "label": meta["label"], "path": meta["path"]}
            for key, meta in SUBAGENT_LOCATIONS.items()
        ]

    def discover_and_load(self) -> Dict[str, Dict[str, Any]]:
        loaded: Dict[str, Dict[str, Any]] = {}

        for location, agent_dir in self._iter_agent_dirs():
            if not agent_dir.exists():
                continue

            for agent_file in sorted(agent_dir.glob("*.md")):
                try:
                    subagent = self._load_subagent(agent_file, location=location)
                except Exception as exc:
                    logger.error(f"Error loading subagent {agent_file}: {exc}")
                    continue

                if not subagent:
                    continue

                name = subagent["name"]
                if name in loaded:
                    logger.debug(
                        f"Skipping duplicate subagent '{name}' from {agent_file}; "
                        "higher-priority definition already loaded."
                    )
                    continue

                loaded[name] = subagent
                logger.debug(f"Loaded subagent: {name} ({agent_file})")

        self.subagents = loaded
        logger.info(f"Loaded {len(self.subagents)} subagent(s)")
        return self.subagents

    def get_subagent(self, name: Optional[str]) -> Optional[Dict[str, Any]]:
        if not name:
            return None
        return self.subagents.get(str(name).strip())

    def list_definitions(self) -> List[Dict[str, Any]]:
        entries: List[Dict[str, Any]] = []
        active_ids: Dict[str, str] = {}

        for active_name, agent in self.subagents.items():
            active_ids[active_name] = self.make_subagent_id(
                str(agent.get("location") or "project_limebot"),
                str(agent.get("filename") or active_name),
            )

        first_seen_by_name: Dict[str, str] = {}
        for location, agent_dir in self._iter_agent_dirs():
            if not agent_dir.exists():
                continue
            for agent_file in sorted(agent_dir.glob("*.md")):
                subagent = self._load_subagent(agent_file, location=location)
                if not subagent:
                    continue
                subagent_id = self.make_subagent_id(location, subagent["filename"])
                existing_active = first_seen_by_name.get(subagent["name"])
                active = existing_active is None
                if active:
                    first_seen_by_name[subagent["name"]] = subagent_id
                entries.append(
                    {
                        "id": subagent_id,
                        "name": subagent["name"],
                        "description": subagent.get("description") or "",
                        "prompt": subagent.get("prompt") or "",
                        "tools": subagent.get("tools"),
                        "model": subagent.get("model") or "inherit",
                        "filename": subagent["filename"],
                        "location": location,
                        "location_label": SUBAGENT_LOCATIONS[location]["label"],
                        "path": subagent.get("source_path"),
                        "active": active,
                        "shadowed_by": None if active else existing_active,
                    }
                )

        return entries

    def get_agent_choices(self) -> List[str]:
        return sorted(self.subagents)

    def get_agent_descriptions(self) -> Dict[str, str]:
        return {
            name: (agent.get("description") or "").strip()
            for name, agent in sorted(self.subagents.items())
        }

    def get_prompt_additions(self) -> str:
        if not self.subagents:
            return ""

        lines = [
            "\n## Available Subagents\n",
            "Use `spawn_agent` when a task should be delegated to an isolated helper.",
            "If one of these fits well, pass its name in the optional `agent` field:\n",
        ]

        for name, agent in sorted(self.subagents.items()):
            description = (agent.get("description") or "").strip() or "No description."
            tools = agent.get("tools")
            if tools is None:
                tool_text = "inherits main tools"
            else:
                tool_text = ", ".join(tools) if tools else "no tools"
            lines.append(f"- `{name}`: {description} (tools: {tool_text})")

        return "\n".join(lines) + "\n"

    def save_subagent(
        self,
        *,
        name: str,
        description: str,
        prompt: str,
        tools: Any = None,
        model: str = "inherit",
        location: str = "project_limebot",
        subagent_id: Optional[str] = None,
    ) -> Dict[str, Any]:
        name = str(name or "").strip()
        description = str(description or "").strip()
        prompt = str(prompt or "").strip()
        model = str(model or "inherit").strip() or "inherit"
        if not name:
            raise ValueError("Subagent name is required")
        if not description:
            raise ValueError("Subagent description is required")
        if not prompt:
            raise ValueError("Subagent prompt is required")

        normalized_location = self._normalize_location(location)
        target_dir = self._resolve_location_dir(normalized_location)
        target_dir.mkdir(parents=True, exist_ok=True)

        existing_path: Optional[Path] = None
        if subagent_id:
            existing_location, existing_filename = self.parse_subagent_id(subagent_id)
            existing_path = self._resolve_location_dir(existing_location) / f"{existing_filename}.md"
            target_dir = self._resolve_location_dir(
                normalized_location or existing_location
            )

        filename = slugify_subagent_filename(name)
        target_path = target_dir / f"{filename}.md"

        if existing_path and existing_path.resolve() != target_path.resolve():
            if target_path.exists():
                raise ValueError(f"Subagent file already exists: {target_path.name}")
            if existing_path.exists():
                existing_path.unlink()

        markdown = self.render_subagent_markdown(
            name=name,
            description=description,
            prompt=prompt,
            tools=tools,
            model=model,
        )
        target_path.write_text(markdown, encoding="utf-8")
        saved = self._load_subagent(
            target_path,
            location=self._infer_location_from_path(target_dir),
        )
        if not saved:
            raise ValueError("Failed to load saved subagent")
        self.discover_and_load()
        return saved

    def delete_subagent(self, subagent_id: str) -> None:
        location, filename = self.parse_subagent_id(subagent_id)
        target_path = self._resolve_location_dir(location) / f"{filename}.md"
        if target_path.exists():
            target_path.unlink()
        self.discover_and_load()

    def render_subagent_markdown(
        self,
        *,
        name: str,
        description: str,
        prompt: str,
        tools: Any = None,
        model: str = "inherit",
    ) -> str:
        normalized_tools = self._normalize_tools(tools)
        frontmatter_lines = [
            "---",
            f"name: {json.dumps(str(name).strip())}",
            f"description: {json.dumps(str(description or '').strip())}",
        ]
        if normalized_tools is not None:
            frontmatter_lines.append(f"tools: {json.dumps(normalized_tools)}")
        if str(model or "").strip():
            frontmatter_lines.append(f"model: {json.dumps(str(model).strip())}")
        frontmatter_lines.append("---")
        body = str(prompt or "").rstrip() + "\n"
        return "\n".join(frontmatter_lines) + "\n" + body

    def _load_subagent(
        self, agent_file: Path, location: Optional[str] = None
    ) -> Optional[Dict[str, Any]]:
        content = agent_file.read_text(encoding="utf-8")
        metadata: Dict[str, Any] = {}
        body = content

        if content.startswith("---"):
            match = re.match(r"^---\s*\n(.*?)\n---\s*\n?(.*)$", content, re.DOTALL)
            if match:
                metadata = self._parse_frontmatter(match.group(1))
                body = match.group(2)

        name = str(metadata.get("name") or agent_file.stem).strip()
        if not name:
            logger.warning(f"Skipping subagent with no name: {agent_file}")
            return None

        description = str(metadata.get("description") or "").strip()
        prompt = body.strip()
        tools = self._normalize_tools(metadata.get("tools"))
        model = str(metadata.get("model") or "").strip() or "inherit"

        return {
            "name": name,
            "description": description,
            "prompt": prompt,
            "tools": tools,
            "model": model,
            "filename": agent_file.stem,
            "location": location or "project_limebot",
            "source_path": str(agent_file.resolve()),
        }

    def _parse_frontmatter(self, frontmatter: str) -> Dict[str, Any]:
        if HAS_YAML:
            try:
                parsed = yaml.safe_load(frontmatter)
                if isinstance(parsed, dict):
                    return parsed
            except Exception:
                pass

        result: Dict[str, Any] = {}
        for line in frontmatter.splitlines():
            line = line.strip()
            if ":" not in line or line.startswith("{"):
                continue
            key, value = line.split(":", 1)
            result[key.strip()] = value.strip()
        return result

    def _normalize_tools(self, value: Any) -> Optional[List[str]]:
        if value in (None, "", []):
            return None

        items: List[str]
        if isinstance(value, list):
            items = [str(item).strip() for item in value]
        else:
            text = str(value).strip()
            if text.startswith("[") and text.endswith("]"):
                text = text[1:-1]
            items = [part.strip().strip("'\"") for part in text.split(",")]

        normalized: List[str] = []
        seen: set[str] = set()
        for item in items:
            name = normalize_subagent_tool_name(item)
            if not name or name in seen:
                continue
            seen.add(name)
            normalized.append(name)

        return normalized or []
