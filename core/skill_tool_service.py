"""Skill inspection, editing, and creation exposed through agent tools."""

from __future__ import annotations

import asyncio
import json
import os
import re
import shutil
import tempfile
from pathlib import Path
from typing import Any, Callable, Dict, Optional

from core.runtime_paths import get_config_file, get_skills_dir


class SkillToolService:
    """Own skill tool orchestration while delegating edits to SkillEditor."""

    def __init__(
        self,
        *,
        get_agent: Callable[[], Any],
        get_config: Callable[[], Any],
        get_active_workspace_paths: Callable[[], tuple[Optional[Path], Optional[Path]]],
        atomic_write_text: Callable[[Path, str], None],
    ) -> None:
        self._get_agent = get_agent
        self._get_config = get_config
        self._get_active_workspace_paths = get_active_workspace_paths
        self._atomic_write_text = atomic_write_text

    async def inspect_skill(
        self,
        skill_name: str,
        path: Optional[str] = None,
        start_line: Optional[int] = None,
        end_line: Optional[int] = None,
    ) -> str:
        """Return bounded, redacted information about a registered skill."""
        agent = self._get_agent()
        registry = getattr(agent, "skill_registry", None)
        if registry is None:
            return json.dumps(
                {"status": "error", "code": "skill_registry_unavailable"},
                ensure_ascii=False,
            )
        from core.skill_editor import SkillEditError, SkillEditor

        try:
            result = await asyncio.to_thread(
                SkillEditor(registry, agent).inspect,
                skill_name,
                path,
                start_line,
                end_line,
            )
        except SkillEditError as exc:
            result = {"status": "error", "code": exc.code, "message": exc.message}
        except Exception:
            result = {
                "status": "error",
                "code": "skill_inspection_failed",
                "message": "The skill could not be inspected.",
            }
        return json.dumps(result, ensure_ascii=False)

    async def edit_skill(self, skill_name: str, changes: Any) -> str:
        """Apply a validated transactional edit to a user-owned skill."""
        agent = self._get_agent()
        registry = getattr(agent, "skill_registry", None)
        if registry is None:
            return json.dumps(
                {"status": "error", "code": "skill_registry_unavailable"},
                ensure_ascii=False,
            )
        from core.skill_editor import SkillEditError, SkillEditor

        try:
            result = await asyncio.to_thread(
                SkillEditor(registry, agent).edit,
                skill_name,
                changes,
            )
        except SkillEditError as exc:
            result = {"status": "error", "code": exc.code, "message": exc.message}
        except Exception:
            result = {
                "status": "error",
                "code": "skill_edit_failed",
                "message": "The skill edit failed and was not applied.",
            }
        return json.dumps(result, ensure_ascii=False)

    async def create_skill(self, name: str, description: str) -> str:
        """Initialize and enable a skill in the current writable workspace."""
        if not re.match(r"^[a-z0-9_]+$", str(name or "")):
            return "Error: Skill name must be snake_case (alphanumeric and underscores only)."
        if not isinstance(description, str) or not description.strip():
            return "Error: Skill description is required."
        if len(description) > 1_000 or "\n" in description or "\r" in description:
            return "Error: Skill description must be one line and at most 1000 characters."

        workspace_root, _ = self._get_active_workspace_paths()
        skill_parent = (
            workspace_root / "skills" if workspace_root is not None else get_skills_dir()
        )
        if skill_parent.exists() and (
            skill_parent.is_symlink() or os.path.islink(str(skill_parent))
        ):
            return "Error: The local skill directory is a symlink and cannot be used."
        skill_dir = skill_parent / name
        if skill_dir.exists() or skill_dir.is_symlink():
            return f"Error: Skill '{name}' already exists."

        content = (
            f"---\n"
            f"name: {name}\n"
            f"description: {description}\n"
            f"version: 1.0.0\n"
            f"---\n\n"
            f"# {name.replace('_', ' ').title()}\n\n"
            f"{description}\n\n"
            f"## Usage\n"
            f"Describe how to use this skill here.\n"
        )
        config_snapshot: Optional[bytes] = None
        config_persisted = False
        try:
            skill_dir.mkdir(parents=True, exist_ok=False)
            await asyncio.to_thread(
                self._atomic_write_text,
                skill_dir / "SKILL.md",
                content,
            )

            if workspace_root is None:
                config_snapshot = await asyncio.to_thread(
                    self._persist_enabled_skill_sync, name
                )
                config_persisted = True
                skills_cfg = getattr(self._get_config(), "skills", None)
                if skills_cfg is not None:
                    enabled = getattr(skills_cfg, "enabled", None)
                    if not isinstance(enabled, list):
                        enabled = []
                        setattr(skills_cfg, "enabled", enabled)
                    if name not in enabled:
                        enabled.append(name)
                agent = self._get_agent()
                if agent and hasattr(agent, "skill_registry"):
                    await asyncio.to_thread(agent.skill_registry.discover_and_load)
                    if hasattr(agent, "_refresh_tool_definitions"):
                        agent._refresh_tool_definitions()
                return json.dumps(
                    {
                        "status": "success",
                        "code": "skill_created",
                        "skill": name,
                        "source_kind": "local",
                        "editable": True,
                        "files": ["SKILL.md"],
                    },
                    ensure_ascii=False,
                )

            return json.dumps(
                {
                    "status": "success",
                    "code": "skill_created",
                    "skill": name,
                    "source_kind": "local",
                    "editable": True,
                    "files": ["SKILL.md"],
                    "isolated_workspace": True,
                },
                ensure_ascii=False,
            )
        except Exception:
            try:
                shutil.rmtree(skill_dir, ignore_errors=True)
            except OSError:
                pass
            if config_persisted:
                try:
                    await asyncio.to_thread(
                        self._restore_config_sync, get_config_file(), config_snapshot
                    )
                except Exception:
                    pass
                skills_cfg = getattr(self._get_config(), "skills", None)
                enabled = getattr(skills_cfg, "enabled", None) if skills_cfg else None
                if isinstance(enabled, list) and name in enabled:
                    enabled.remove(name)
            return json.dumps(
                {
                    "status": "error",
                    "code": "skill_create_failed",
                    "message": "The skill was not created.",
                },
                ensure_ascii=False,
            )

    @staticmethod
    def _persist_enabled_skill_sync(name: str) -> Optional[bytes]:
        """Add a local skill to limebot.json with an atomic replacement."""
        config_path = get_config_file()
        previous_bytes = config_path.read_bytes() if config_path.exists() else None
        config: Dict[str, Any] = {}
        if config_path.exists():
            try:
                loaded = json.loads(config_path.read_text(encoding="utf-8"))
                if isinstance(loaded, dict):
                    config = loaded
            except (OSError, ValueError):
                raise RuntimeError("The LimeBot configuration could not be read.") from None

        skills = config.setdefault("skills", {})
        if not isinstance(skills, dict):
            raise RuntimeError("The LimeBot skills configuration is invalid.")
        enabled = skills.setdefault("enabled", [])
        if not isinstance(enabled, list):
            raise RuntimeError("The LimeBot enabled-skills configuration is invalid.")
        if name not in enabled:
            enabled.append(name)

        config_path.parent.mkdir(parents=True, exist_ok=True)
        temporary: Optional[Path] = None
        try:
            with tempfile.NamedTemporaryFile(
                mode="w",
                encoding="utf-8",
                newline="",
                dir=str(config_path.parent),
                prefix=f".{config_path.name}.limebot-",
                suffix=".tmp",
                delete=False,
            ) as handle:
                temporary = Path(handle.name)
                handle.write(json.dumps(config, indent=2, ensure_ascii=False))
                handle.write("\n")
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temporary, config_path)
            temporary = None
        finally:
            if temporary is not None:
                temporary.unlink(missing_ok=True)
        return previous_bytes

    @staticmethod
    def _restore_config_sync(path: Path, previous_bytes: Optional[bytes]) -> None:
        """Restore the config snapshot captured before a skill was created."""
        if previous_bytes is None:
            path.unlink(missing_ok=True)
            return
        temporary: Optional[Path] = None
        try:
            with tempfile.NamedTemporaryFile(
                mode="wb",
                dir=str(path.parent),
                prefix=f".{path.name}.limebot-restore-",
                suffix=".tmp",
                delete=False,
            ) as handle:
                temporary = Path(handle.name)
                handle.write(previous_bytes)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temporary, path)
            temporary = None
        finally:
            if temporary is not None:
                temporary.unlink(missing_ok=True)
