"""Coding-plan and change-set review artifacts for companion workspaces."""

from __future__ import annotations

import uuid
from typing import Any, Awaitable, Callable, Dict, List, Optional, Tuple

from core.events import InboundMessage
from core.tool_outcomes import ToolOutcome


class WorkspaceReviewService:
    """Own review-artifact lifecycle while using loop-owned UI and policy ports."""

    def __init__(
        self,
        *,
        parse_tool_call: Callable[[dict, str], Tuple[str, str, Dict[str, Any]]],
        build_confirmation_preview: Callable[[str, Dict[str, Any], str], Dict[str, Any]],
        workspace_id_from_message: Callable[[Optional[InboundMessage]], str],
        publish_changeset: Callable[..., Awaitable[None]],
        publish_coding_plan: Callable[..., Awaitable[None]],
    ) -> None:
        self._parse_tool_call = parse_tool_call
        self._build_confirmation_preview = build_confirmation_preview
        self._workspace_id_from_message = workspace_id_from_message
        self._publish_changeset = publish_changeset
        self._publish_coding_plan = publish_coding_plan
        self._workspace_changesets: Dict[str, Tuple[str, str]] = {}

    async def stage_changeset(
        self,
        tool_calls: List[Dict[str, Any]],
        session_key: str,
        msg: Optional[InboundMessage],
        *,
        turn_id: Optional[str] = None,
        message_id: Optional[str] = None,
    ) -> Optional[Dict[str, Any]]:
        """Create one redacted review artifact before a coding mutation runs."""
        from core.review_entrypoint import build_changeset_artifact, changeset_for_app
        from core.task_tracker import get_task_tracker
        from core.tool_dispatcher import MUTATION_TOOL_NAMES

        write_previews: List[Dict[str, Any]] = []
        for tool_call in tool_calls or []:
            _, name, args = self._parse_tool_call(tool_call, session_key)
            if name not in MUTATION_TOOL_NAMES:
                continue
            write_previews.append(
                self._build_confirmation_preview(name, args, session_key)
            )
        if not write_previews:
            return None

        diff_parts: List[str] = []
        for index, preview in enumerate(write_previews):
            diff = str(preview.get("diff") or "").strip()
            if diff:
                diff_parts.append(diff)
                continue
            path = str(preview.get("path") or f"file-{index + 1}")
            mode = str(preview.get("mode") or preview.get("kind") or "change")
            if mode == "delete":
                diff_parts.append(
                    f"diff --git a/{path} b/{path}\n--- a/{path}\n+++ /dev/null\n"
                    "@@ -1 +0,0 @@\n- [content redacted]"
                )
            else:
                diff_parts.append(
                    f"diff --git a/{path} b/{path}\n--- /dev/null\n+++ b/{path}\n"
                    "@@ -0,0 +1 @@\n+ [content redacted]"
                )

        changeset = build_changeset_artifact(
            "\n".join(diff_parts),
            status="awaiting_approval",
            summary=f"{len(write_previews)} file change(s) are staged for review.",
        )
        changeset["id"] = f"changeset-{turn_id or uuid.uuid4().hex[:12]}"
        workspace_id = self._workspace_id_from_message(msg)
        if workspace_id:
            artifact = await get_task_tracker().add_workspace_artifact(
                workspace_id,
                kind="change_set",
                title="Patch review",
                metadata=changeset,
            )
            if artifact:
                self._workspace_changesets[session_key] = (
                    workspace_id,
                    artifact.artifact_id,
                )
        await self._publish_changeset(
            changeset_for_app(changeset),
            msg,
            turn_id=turn_id,
            message_id=message_id,
        )
        return changeset

    async def refresh_changeset(
        self,
        session_key: str,
        msg: Optional[InboundMessage],
        outcome: ToolOutcome,
        *,
        blocked: bool = False,
        turn_id: Optional[str] = None,
        message_id: Optional[str] = None,
    ) -> None:
        """Update the active review artifact with tool and verification outcomes."""
        stored = self._workspace_changesets.get(session_key)
        if not stored:
            return
        from core.review_entrypoint import changeset_for_app
        from core.task_tracker import get_task_tracker

        workspace_id, artifact_id = stored
        tracker = get_task_tracker()
        workspace = await tracker.get_workspace(workspace_id)
        if workspace is None:
            return
        artifact = next(
            (item for item in workspace.artifacts if item.artifact_id == artifact_id),
            None,
        )
        if artifact is None or artifact.kind != "change_set":
            return

        changeset = dict(artifact.metadata)
        verification = list(changeset.get("verification") or [])
        if outcome.tool == "run_command":
            verification.append(
                {
                    "id": f"verification-{len(verification) + 1}",
                    "label": "Verification",
                    "status": "passed" if outcome.success else "failed",
                    "exit_code": outcome.exit_code,
                    "diagnostic": outcome.diagnostic_tail,
                }
            )
        elif outcome.verification_status:
            verification.append(
                {
                    "id": f"verification-{len(verification) + 1}",
                    "label": "Native file checks",
                    "status": (
                        "passed"
                        if outcome.verification_status == "passed"
                        else "failed"
                    ),
                    "diagnostic": outcome.verification_detail,
                }
            )

        if blocked:
            changeset["status"] = "blocked"
        elif outcome.tool == "run_command":
            changeset["status"] = "verified" if outcome.success else "failed"
        elif outcome.verification_status == "failed":
            changeset["status"] = "failed"
        elif outcome.success:
            changeset["status"] = "applied"
        changeset["verification"] = verification

        updated = await tracker.update_workspace_artifact(
            workspace_id,
            artifact_id,
            metadata_update=changeset,
        )
        if updated:
            await self._publish_changeset(
                changeset_for_app(changeset),
                msg,
                turn_id=turn_id,
                message_id=message_id,
            )

    async def persist_coding_plan(
        self,
        plan_text: str,
        msg: Optional[InboundMessage],
        *,
        turn_id: Optional[str] = None,
        message_id: Optional[str] = None,
    ) -> None:
        """Save a read-only coding plan to the workspace and companion stream."""
        from core.review_entrypoint import build_coding_plan_artifact
        from core.task_tracker import get_task_tracker

        artifact_data = build_coding_plan_artifact(plan_text)
        workspace_id = self._workspace_id_from_message(msg)
        if workspace_id:
            await get_task_tracker().add_workspace_artifact(
                workspace_id,
                kind="coding_plan",
                title="Coding plan",
                metadata=artifact_data,
            )
        await self._publish_coding_plan(
            artifact_data,
            msg,
            turn_id=turn_id,
            message_id=message_id,
        )
