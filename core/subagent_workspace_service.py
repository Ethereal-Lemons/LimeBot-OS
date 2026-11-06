"""Subagent execution in isolated copies and explicit changeset application."""

from __future__ import annotations

import json
import hashlib
import re
import uuid
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

from loguru import logger


class SubagentWorkspaceService:
    """Own subagent workspace lifecycle while delegating tools to Toolbox."""

    def __init__(
        self,
        *,
        get_agent: Callable[[], Any],
        get_subagent_registry: Callable[[], Any],
        active_tool_root: Callable[[], Path],
        write_file: Callable[[str, str], Any],
        edit_file: Callable[[str, Any, str], Any],
        delete_file: Callable[[str], Any],
    ) -> None:
        self.get_agent = get_agent
        self.get_subagent_registry = get_subagent_registry
        self.active_tool_root = active_tool_root
        self.write_file = write_file
        self.edit_file = edit_file
        self.delete_file = delete_file

    async def spawn_agent(
        self,
        task: str,
        session_key: str = None,
        agent: Optional[str] = None,
        background: Optional[bool] = None,
        isolation: str = "auto",
    ) -> str:
        """Spawn a subagent and optionally let it report back in the background."""
        agent_loop = self.get_agent()
        if not agent_loop:
            return "Error: Agent loop not linked to toolbox."

        requested_isolation = str(isolation or "auto").strip().lower()
        if requested_isolation not in {"auto", "copy", "none"}:
            return "Error: isolation must be one of: auto, copy, none."

        if not session_key:
            from core.context import tool_context

            ctx = tool_context.get()
            session_key = f"system:{ctx.get('chat_id', 'global')}"

        sub_session_key = f"{session_key}_sub_{uuid.uuid4().hex[:6]}"
        logger.info(f"🚀 Spawning sub-agent '{sub_session_key}' for task: {task}")

        subagent_profile = None
        if agent:
            logger.info(f"Using sub-agent profile '{agent}' for '{sub_session_key}'")
            registry = self.get_subagent_registry()
            if registry is not None:
                try:
                    subagent_profile = registry.get_subagent(agent)
                except Exception:
                    subagent_profile = None

        use_background = bool(background)
        if background is None and subagent_profile:
            use_background = bool(subagent_profile.get("background"))

        coding_task = bool(
            re.search(
                r"\b(code|coding|repo|repository|bug|fix|implement|edit|patch|review|verify|test|refactor|lint|diagnos)\w*\b",
                str(task or ""),
                re.IGNORECASE,
            )
        )
        use_isolated_copy = requested_isolation == "copy" or (
            requested_isolation == "auto"
            and (coding_task or agent in {"explorer", "reviewer", "verifier"})
        )
        isolation_mode = "copy" if use_isolated_copy else "none"
        isolated_workspace = None
        if use_isolated_copy:
            try:
                from core.workspace_isolation import IsolatedWorkspace

                isolated_workspace = await IsolatedWorkspace.create(
                    self.active_tool_root(),
                    label=sub_session_key,
                    owner_session_key=session_key,
                )
            except Exception as exc:
                logger.error(f"Could not create isolated sub-agent workspace: {exc}")
                return f"Error: Could not create isolated sub-agent workspace: {exc}"

        try:
            if use_background:
                task_id = await agent_loop.start_background_subagent(
                    session_key,
                    sub_session_key,
                    task,
                    agent_name=agent,
                    isolated_workspace=isolated_workspace,
                    isolation_mode=isolation_mode,
                )
                # Ownership moves to the background task after it has been
                # registered. The task wrapper cleans it up on exit.
                isolated_workspace = None
                mode_label = f"'{agent}'" if agent else "generic worker"
                isolation_label = (
                    " in an isolated copy" if isolation_mode == "copy" else ""
                )
                return (
                    f"Started background sub-agent {mode_label} as "
                    f"'{sub_session_key}' (task_id: {task_id}){isolation_label}. "
                    "It will report back when finished."
                )

            result = await agent_loop.run_subagent(
                session_key,
                sub_session_key,
                task,
                agent_name=agent,
                isolated_workspace=isolated_workspace,
                isolation_mode=isolation_mode,
            )
            if (
                isolated_workspace is not None
                and isolated_workspace.root.exists()
                and not isolated_workspace.is_pending()
            ):
                capture = isolated_workspace.last_capture
                if capture is None:
                    capture = await isolated_workspace.capture()
                if capture.get("status") == "changed":
                    isolated_workspace.retain()
            return str(result)
        except Exception as exc:
            logger.error(f"Error spawning agent: {exc}")
            return f"Error spawning agent: {exc}"
        finally:
            if isolated_workspace is not None and not isolated_workspace.is_pending():
                try:
                    await isolated_workspace.cleanup()
                except Exception as cleanup_error:
                    logger.warning(
                        f"Could not clean up isolated sub-agent workspace: {cleanup_error}"
                    )

    async def apply_workspace_changeset(
        self,
        workspace_id: Optional[str] = None,
        changeset: Optional[Any] = None,
        *,
        session_key: Optional[str] = None,
    ) -> str:
        """Apply a retained copy-isolation capture to the live tree, then clean clones."""
        from core.context import workspace_context
        from core.workspace_isolation import (
            IsolatedWorkspace,
            cleanup_leftover_clones,
            get_pending_workspace,
            latest_pending_workspace,
            list_pending_workspaces,
        )

        workspace: Optional[IsolatedWorkspace] = None
        capture: Optional[Dict[str, Any]] = None
        requested_id = str(workspace_id or "").strip()
        active_session = str(session_key or "").strip()
        if requested_id:
            workspace = get_pending_workspace(requested_id)
            if (
                workspace is not None
                and active_session
                and workspace.owner_session_key != active_session
            ):
                return "Error: This isolated workspace belongs to another session."
        if workspace is None and not requested_id:
            if active_session:
                workspace = next(
                    (
                        pending
                        for pending in reversed(list_pending_workspaces())
                        if pending.owner_session_key == active_session
                    ),
                    None,
                )
            else:
                workspace = latest_pending_workspace()
        if changeset:
            if isinstance(changeset, str):
                try:
                    changeset = json.loads(changeset)
                except json.JSONDecodeError as exc:
                    return f"Error: changeset is not valid JSON: {exc}"
            if not isinstance(changeset, dict):
                return "Error: changeset must be an object with changed_files."
            capture = changeset
            capture_id = str(capture.get("workspace_id") or "").strip()
            if workspace is None and capture_id:
                workspace = get_pending_workspace(capture_id)
            if (
                workspace is not None
                and active_session
                and workspace.owner_session_key != active_session
            ):
                return "Error: This isolated workspace belongs to another session."
            if (
                workspace is None
                and active_session
                and str(capture.get("owner_session_key") or "") != active_session
            ):
                return "Error: This changeset does not belong to the current session."
        if workspace is None and capture is None:
            return (
                "Error: No retained isolated workspace to apply. "
                "spawn_agent(isolation='copy') must finish with changes first."
            )
        if capture is None and workspace is not None:
            if workspace.last_capture is not None:
                capture = workspace.last_capture
            else:
                capture = await workspace.capture()
        if not isinstance(capture, dict):
            return "Error: Isolated workspace capture is missing."

        changed_files = list(capture.get("changed_files") or [])
        source_root = (
            workspace.source_root
            if workspace is not None
            else Path(str(capture.get("source_root") or Path.cwd())).resolve()
        )
        apply_token = workspace_context.set({})
        applied: List[Dict[str, Any]] = []
        try:
            for item in changed_files:
                if not isinstance(item, dict):
                    continue
                relative = str(item.get("path") or "").strip()
                if not relative or relative.startswith("/") or ".." in Path(relative).parts:
                    return f"Error: Refusing to apply unsafe path '{relative}'."
                live_path = str((source_root / relative).resolve())
                status = str(item.get("status") or "modified")
                if status == "deleted":
                    result = await self.delete_file(live_path)
                    applied.append({"path": relative, "status": status, "result": result})
                    if str(result).startswith("Error:"):
                        return json.dumps(
                            {"status": "error", "applied": applied, "failed": relative},
                            ensure_ascii=False,
                        )
                    continue
                after_text = (
                    workspace.applyable_file_text(item)
                    if workspace is not None
                    else item.get("after_text")
                )
                if not isinstance(after_text, str):
                    return (
                        f"Error: No applyable UTF-8 content for '{relative}'. "
                        "The clone was deleted before apply and the capture had no after_text."
                    )
                before_sha = str(item.get("before_sha256") or "").strip().lower()
                before_text = (
                    workspace.applyable_before_text(item)
                    if workspace is not None
                    else item.get("before_text")
                )
                live_target = Path(live_path)
                if status == "added" or not live_target.exists():
                    result = await self.write_file(live_path, after_text)
                elif isinstance(before_text, str) and re.fullmatch(r"[0-9a-f]{64}", before_sha):
                    result = await self.edit_file(
                        live_path,
                        [{"old_text": before_text, "new_text": after_text}],
                        before_sha,
                    )
                else:
                    if re.fullmatch(r"[0-9a-f]{64}", before_sha) and live_target.is_file():
                        current_sha = hashlib.sha256(live_target.read_bytes()).hexdigest()
                        if current_sha != before_sha:
                            return (
                                f"Error: Stale apply for '{relative}'. Live SHA-256 is "
                                f"{current_sha}, not {before_sha}."
                            )
                    result = await self.write_file(live_path, after_text)
                applied.append({"path": relative, "status": status, "result": result})
                if str(result).startswith("Error:"):
                    return json.dumps(
                        {"status": "error", "applied": applied, "failed": relative},
                        ensure_ascii=False,
                    )
        finally:
            workspace_context.reset(apply_token)

        if workspace is not None:
            await workspace.cleanup()
        leftovers = cleanup_leftover_clones(source_root)
        return json.dumps(
            {
                "status": "applied",
                "workspace_id": (
                    workspace.workspace_id if workspace is not None else capture.get("workspace_id")
                ),
                "applied": [
                    {"path": item["path"], "status": item["status"]} for item in applied
                ],
                "cleaned_leftovers": leftovers,
            },
            ensure_ascii=False,
        )
