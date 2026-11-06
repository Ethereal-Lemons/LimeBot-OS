"""Approval request lifecycle for sensitive tool calls."""

from __future__ import annotations

import asyncio
import uuid
from dataclasses import dataclass
from typing import Any, Awaitable, Callable, Dict, Optional

from core.confirmation import ConfirmationManager
from core.profile_context import ProfileRuntimeContext


@dataclass(frozen=True)
class ToolApprovalRequest:
    session_key: str
    tool_call_id: str
    function_name: str
    function_args: Dict[str, Any]
    message: Any = None
    turn_id: Optional[str] = None
    message_id: Optional[str] = None
    is_internal: bool = False
    is_whatsapp: bool = False


@dataclass(frozen=True)
class ToolApprovalResult:
    proceed: bool
    rejection: str = ""


class ToolApprovalFlowService:
    """Coordinate policy, confirmation state, audit, and approval delivery."""

    def __init__(
        self,
        *,
        confirmation: ConfirmationManager,
        decide: Callable[..., Dict[str, Any]],
        get_profile_context: Callable[[], Optional[ProfileRuntimeContext]],
        get_profile_id: Callable[[], str],
        session_whitelist_key: Callable[[str, Dict[str, Any]], str],
        log_session_event: Callable[[str, Dict[str, Any]], None],
        approval_audit_preview: Callable[[Dict[str, Any]], Dict[str, Any]],
        display_tool_args: Callable[..., Any],
        with_trace_metadata: Callable[..., Dict[str, Any]],
        publish: Callable[[Any, str, Dict[str, Any]], Awaitable[None]],
        publish_web_mirror: Callable[[Any, Dict[str, Any]], Awaitable[None]],
    ) -> None:
        self._confirmation = confirmation
        self._decide = decide
        self._get_profile_context = get_profile_context
        self._get_profile_id = get_profile_id
        self._session_whitelist_key = session_whitelist_key
        self._log_session_event = log_session_event
        self._approval_audit_preview = approval_audit_preview
        self._display_tool_args = display_tool_args
        self._with_trace_metadata = with_trace_metadata
        self._publish = publish
        self._publish_web_mirror = publish_web_mirror

    async def authorize(self, request: ToolApprovalRequest) -> ToolApprovalResult:
        """Return whether the sensitive operation may proceed after approval."""
        decision = self._decide(
            request.session_key,
            request.function_name,
            is_internal=request.is_internal,
            is_whatsapp=request.is_whatsapp,
            function_args=request.function_args,
            msg=request.message,
        )
        client_source = str(
            getattr(request.message, "channel", "") or "system"
        ).strip().lower()

        if decision["allowed"]:
            self._log_session_event(
                request.session_key,
                {
                    "type": "approval_decided",
                    "conf_id": f"policy_{uuid.uuid4().hex[:8]}",
                    "tool": request.function_name,
                    "approved": True,
                    "session_whitelist": decision["reason"] == "session_whitelist",
                    "policy_profile": decision["policy_profile"],
                    "decision_reason": decision["reason"],
                    "client_source": client_source,
                },
            )

        if not decision["allowed"] and not decision["requires_confirmation"]:
            return ToolApprovalResult(
                proceed=False,
                rejection=(
                    "ACTION CANCELLED: Unattended policy denied "
                    f"{request.function_name} ({decision['reason']})."
                ),
            )

        if not decision["requires_confirmation"]:
            return ToolApprovalResult(proceed=True)

        conf_id = f"conf_{uuid.uuid4().hex[:8]}"
        preview = self._confirmation.build_preview(
            request.function_name, request.function_args, request.session_key
        )
        profile_context = self._get_profile_context()
        self._confirmation.register_pending(
            conf_id,
            {
                "session_key": request.session_key,
                "tool": request.function_name,
                "whitelist_key": self._session_whitelist_key(
                    request.function_name, request.function_args
                ),
                "preview": preview,
                "policy_profile": decision["policy_profile"],
                "decision_reason": decision["reason"],
                "client_source": client_source,
                "profile_id": self._get_profile_id(),
                "profile_name": (
                    profile_context.profile.display_name
                    if profile_context is not None
                    else None
                ),
            },
        )
        self._log_session_event(
            request.session_key,
            {
                "type": "approval_requested",
                "conf_id": conf_id,
                "tool": request.function_name,
                "preview": self._approval_audit_preview(preview),
                "policy_profile": decision["policy_profile"],
                "decision_reason": decision["reason"],
                "client_source": client_source,
            },
        )

        embed_fields = self._confirmation.build_embed(
            request.function_name,
            request.function_args,
            request.session_key,
            preview=preview,
        )
        tool_content = (
            f"🛠️ Executing {request.function_name}..."
            if request.is_whatsapp
            else "⏳"
        )
        metadata = {
            "type": "tool_execution",
            "status": "waiting_confirmation",
            "tool": request.function_name,
            "args": self._display_tool_args(
                request.function_name, request.function_args, preview
            ),
            "preview": preview,
            "tool_call_id": request.tool_call_id,
            "conf_id": conf_id,
            "policy_profile": decision["policy_profile"],
            "decision_reason": decision["reason"],
            "turn_id": request.turn_id,
            "message_id": request.message_id,
            "embed": {
                "title": "Exec Approval Required",
                "description": "A command needs your approval.",
                "color": "#F59E0B",
                "fields": embed_fields,
                "footer": f"Expires in 300s | ID: {conf_id}",
            },
        }
        metadata = self._with_trace_metadata(
            metadata, turn_id=request.turn_id, message_id=request.message_id
        )
        await self._publish(request.message, tool_content, metadata)
        if request.message and getattr(request.message, "channel", "") != "web":
            web_metadata = {
                key: value for key, value in metadata.items() if key != "embed"
            }
            await self._publish_web_mirror(request.message, web_metadata)

        try:
            approved = await self._confirmation.wait_for_resolution(
                conf_id, timeout=300
            )
        except asyncio.TimeoutError:
            self._log_session_event(
                request.session_key,
                {
                    "type": "approval_timed_out",
                    "conf_id": conf_id,
                    "tool": request.function_name,
                    "approved": False,
                    "session_whitelist": False,
                    "policy_profile": decision["policy_profile"],
                    "decision_reason": "timeout",
                    "client_source": client_source,
                },
            )
            return ToolApprovalResult(
                proceed=False, rejection="ACTION CANCELLED: Timed out."
            )
        except asyncio.CancelledError:
            self._log_session_event(
                request.session_key,
                {
                    "type": "approval_decided",
                    "conf_id": conf_id,
                    "tool": request.function_name,
                    "approved": False,
                    "session_whitelist": False,
                    "policy_profile": decision["policy_profile"],
                    "decision_reason": "run_cancelled",
                    "client_source": client_source,
                },
            )
            raise

        if not approved:
            return ToolApprovalResult(
                proceed=False, rejection="ACTION CANCELLED: User denied."
            )
        return ToolApprovalResult(proceed=True)
