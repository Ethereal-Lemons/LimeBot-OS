"""User-facing and session events for tool-call batches."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable, Dict, List, Optional, Set, Tuple

from core.events import InboundMessage
from core.redaction import redact_sensitive_text, redact_sensitive_value
from core.tool_outcomes import is_tool_result_error


@dataclass(frozen=True)
class ToolBatchEventContext:
    session_key: str
    message: Optional[InboundMessage]
    turn_id: Optional[str] = None
    message_id: Optional[str] = None


class ToolBatchEventService:
    """Publish the existing planned/running/result events for tool calls."""

    def __init__(
        self,
        *,
        publish_both: Callable[..., Any],
        parse_tool_call: Callable[..., Tuple[str, str, Dict[str, Any]]],
        build_preview: Callable[..., Dict[str, Any]],
        display_args: Callable[..., Dict[str, Any]],
        log_session_event: Callable[[str, Dict[str, Any]], None],
        sensitive_tools: Set[str],
        broadcast_max_chars: int,
    ) -> None:
        self._publish_both = publish_both
        self._parse_tool_call = parse_tool_call
        self._build_preview = build_preview
        self._display_args = display_args
        self._log_session_event = log_session_event
        self._sensitive_tools = sensitive_tools
        self._broadcast_max_chars = broadcast_max_chars

    async def publish_planned(
        self, tool_calls: List[Dict[str, Any]], context: ToolBatchEventContext
    ) -> None:
        for tool_call in tool_calls:
            tool_call_id, function_name, function_args = self._parse_tool_call(
                tool_call, context.session_key
            )
            preview = self._build_preview(
                function_name, function_args, context.session_key
            )
            await self._publish_both(
                context.message,
                "",
                {
                    "type": "tool_execution",
                    "status": "planned",
                    "tool": function_name,
                    "args": self._display_args(function_name, function_args, preview),
                    "preview": redact_sensitive_value(preview),
                    "tool_call_id": tool_call_id,
                    "turn_id": context.turn_id,
                    "message_id": context.message_id,
                },
            )

    async def publish_running(
        self,
        context: ToolBatchEventContext,
        *,
        tool_call_id: str,
        function_name: str,
        function_args: Dict[str, Any],
        is_whatsapp: bool,
        on_progress_queued: Optional[Callable[[str], None]] = None,
    ) -> None:
        tool_content = f"🛠️ Executing {function_name}..." if is_whatsapp else "⏳"
        await self._publish_both(
            context.message,
            tool_content,
            {
                "type": "tool_execution",
                "status": "running",
                "tool": function_name,
                "args": self._display_args(function_name, function_args),
                "tool_call_id": tool_call_id,
                "turn_id": context.turn_id,
                "message_id": context.message_id,
            },
        )
        if on_progress_queued:
            on_progress_queued("tool_progress")
        self._log_session_event(
            context.session_key,
            {
                "type": "tool_started",
                "tool": function_name,
                "tool_call_id": tool_call_id,
                "args": (
                    {"redacted": True}
                    if function_name in self._sensitive_tools
                    else redact_sensitive_value(function_args)
                ),
            },
        )

    def record_finished(
        self,
        context: ToolBatchEventContext,
        *,
        tool_call_id: str,
        function_name: str,
        result: Any,
        is_blocked: bool,
        is_internal: bool,
    ) -> None:
        self._log_session_event(
            context.session_key,
            {
                "type": "tool_finished",
                "tool": function_name,
                "tool_call_id": tool_call_id,
                "blocked": is_blocked,
                "is_internal": is_internal,
                "result_preview": str(result)[:400],
            },
        )

    async def publish_failure(
        self,
        context: ToolBatchEventContext,
        *,
        tool_call_id: str,
        function_name: str,
        function_args: Dict[str, Any],
        exception_type: str,
    ) -> None:
        try:
            await self._publish_both(
                context.message,
                function_name,
                {
                    "type": "tool_execution",
                    "tool": function_name,
                    "status": "error",
                    "args": self._display_args(function_name, function_args),
                    "result": redact_sensitive_text(
                        f"Execution failed or was cancelled: {exception_type}"
                    ),
                    "tool_call_id": tool_call_id,
                    "turn_id": context.turn_id,
                    "message_id": context.message_id,
                },
            )
        except Exception:
            pass

    async def publish_result(
        self,
        context: ToolBatchEventContext,
        *,
        tool_call_id: str,
        function_name: str,
        function_args: Dict[str, Any],
        result: Any,
    ) -> None:
        status = "error" if is_tool_result_error(function_name, result) else "completed"
        try:
            await self._publish_both(
                context.message,
                "",
                {
                    "type": "tool_execution",
                    "tool": function_name,
                    "status": status,
                    "args": self._display_args(function_name, function_args),
                    "result": redact_sensitive_text(result)[
                        : self._broadcast_max_chars
                    ],
                    "tool_call_id": tool_call_id,
                    "turn_id": context.turn_id,
                    "message_id": context.message_id,
                },
            )
        except Exception:
            pass
