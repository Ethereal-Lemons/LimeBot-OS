"""Ordered execution policy for a batch of model-issued tool calls."""

from __future__ import annotations

import asyncio
from typing import Any, Awaitable, Callable, Dict, List, Sequence

from core.tool_dispatcher import READ_ONLY_TOOL_NAMES


ToolCall = Dict[str, Any]
ExecuteToolCall = Callable[[ToolCall], Awaitable[Any]]
ConsumeToolOutcome = Callable[[int, ToolCall, Any], Awaitable[None]]


class ToolBatchExecutor:
    """Run read-only groups concurrently and stateful calls as barriers."""

    def __init__(self, read_only_tool_names=READ_ONLY_TOOL_NAMES) -> None:
        self.read_only_tool_names = frozenset(read_only_tool_names)

    async def execute_ordered(
        self,
        tool_calls: Sequence[ToolCall],
        execute_one: ExecuteToolCall,
        *,
        on_outcome: ConsumeToolOutcome | None = None,
    ) -> List[Any]:
        """Execute calls in model order, overlapping only adjacent safe reads.

        `asyncio.gather` preserves input result order and collects per-call
        failures for the caller to report. A non-read call flushes the current
        read group before it runs, preserving write-then-verify causality. Once
        execution finishes, `on_outcome` consumes results in that same model
        order, so result events and history cannot race across calls.
        """
        outcomes: List[Any] = []
        read_group: List[ToolCall] = []

        async def flush_reads() -> None:
            if not read_group:
                return
            outcomes.extend(
                await asyncio.gather(
                    *(execute_one(call) for call in read_group),
                    return_exceptions=True,
                )
            )
            read_group.clear()

        for tool_call in tool_calls:
            function = (
                tool_call.get("function")
                if isinstance(tool_call, dict)
                else {}
            )
            function_name = str((function or {}).get("name") or "")
            if function_name in self.read_only_tool_names:
                read_group.append(tool_call)
                continue
            await flush_reads()
            outcomes.append(await execute_one(tool_call))

        await flush_reads()
        if on_outcome is not None:
            for index, (tool_call, outcome) in enumerate(zip(tool_calls, outcomes)):
                if isinstance(outcome, asyncio.CancelledError):
                    raise outcome
                await on_outcome(index, tool_call, outcome)
        return outcomes
