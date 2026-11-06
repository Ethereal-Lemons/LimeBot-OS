"""Coordinate durable task-run completion, blocking, and continuation."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Awaitable, Callable, Dict, List, Optional

from core.events import InboundMessage
from core.recovery_controller import RecoveryState
from core.tool_outcomes import ToolOutcome


@dataclass(frozen=True)
class TaskRunCompletionRequest:
    task_run: Any
    content: str
    coding_turn: bool
    plan_mode: bool
    outcomes: List[ToolOutcome]
    unresolved_tool_failure: bool
    unresolved_failure_detail: str
    iterations_limit_reached: bool
    history: List[Dict[str, Any]]
    session_key: str
    message: Optional[InboundMessage]
    raw_reply: str
    recovery_state: Optional[RecoveryState]
    pending_workspaces: List[Any]


@dataclass(frozen=True)
class TaskRunCompletionResult:
    task_run: Any
    operator_verdict: Any
    continuation_scheduled: bool = False
    progress_reply: str = ""
    turn_status_override: Optional[str] = None


class TaskRunCompletionService:
    """Apply invariant and acceptance decisions to one durable task run."""

    def __init__(
        self,
        *,
        task_runs: Any,
        evaluate_invariants: Callable[..., Any],
        acceptance_satisfied: Callable[..., bool],
        is_casual_turn: Callable[[str], bool],
        schedule_continuation: Callable[..., Awaitable[bool]],
    ) -> None:
        self._task_runs = task_runs
        self._evaluate_invariants = evaluate_invariants
        self._acceptance_satisfied = acceptance_satisfied
        self._is_casual_turn = is_casual_turn
        self._schedule_continuation = schedule_continuation

    async def finalize(
        self, request: TaskRunCompletionRequest
    ) -> TaskRunCompletionResult:
        """Return the authoritative task state and any continuation to present."""
        from core.operator_invariants import (
            format_invariant_next_action,
            has_verification_evidence,
        )
        from core.task_runs import (
            BLOCKED,
            COMPLETED,
            REPAIRING,
            VERIFYING,
            is_coding_goal,
        )

        task_run = request.task_run
        operator_verdict = self._evaluate_invariants(
            session_key=request.session_key,
            content=request.content,
            coding_turn=request.coding_turn,
            coding_goal=(
                is_coding_goal(getattr(task_run, "goal", ""))
                if task_run is not None
                else is_coding_goal(request.content)
            ),
            plan_mode=request.plan_mode,
            outcomes=request.outcomes,
            unresolved_tool_failure=request.unresolved_tool_failure,
            iterations_limit_reached=request.iterations_limit_reached,
        )
        if task_run is None:
            return TaskRunCompletionResult(
                task_run=None,
                operator_verdict=operator_verdict,
                turn_status_override=(
                    "blocked"
                    if operator_verdict.applies and not operator_verdict.ok
                    else None
                ),
            )

        accepted = bool(request.plan_mode) or (
            self._acceptance_satisfied(
                task_run,
                request.outcomes,
                coding_turn=request.coding_turn,
                iterations_limit_reached=request.iterations_limit_reached,
                unresolved_tool_failure=request.unresolved_tool_failure,
                history=request.history,
                session_key=request.session_key,
                casual_turn=self._is_casual_turn(request.content),
                plan_mode=request.plan_mode,
                pending_workspaces=request.pending_workspaces,
            )
            and (not operator_verdict.applies or operator_verdict.ok)
        )
        verified = bool(
            operator_verdict.ok
            and has_verification_evidence(
                request.outcomes,
                mutated_paths=operator_verdict.mutated_paths,
                history=request.history,
            )
        )
        checkpoint = dict(task_run.checkpoint or {})
        if verified:
            checkpoint["verification_state"] = "passed"
        task_run.checkpoint = checkpoint

        if accepted:
            if request.recovery_state is not None and request.recovery_state.active:
                request.recovery_state.resolve()
            task_run = self._task_runs.complete(
                task_run.run_id,
                result=str(request.raw_reply or ""),
                checkpoint={
                    **checkpoint,
                    "recovery": (
                        request.recovery_state.to_dict()
                        if request.recovery_state is not None
                        else {}
                    ),
                    "verification_state": (
                        "passed" if verified else checkpoint.get("verification_state")
                    ),
                },
            ) or task_run
            return TaskRunCompletionResult(task_run, operator_verdict)

        hard_category = (
            request.recovery_state.failure_category
            if request.recovery_state is not None
            else ""
        )
        hard_blocker = hard_category in {
            "authentication",
            "permission_policy",
            "cancellation",
        }
        invariant_error = (
            format_invariant_next_action(operator_verdict)
            if operator_verdict.applies and not operator_verdict.ok
            else ""
        )
        block_error = (
            request.unresolved_failure_detail
            or invariant_error
            or "A required external permission or credential is unavailable."
        )
        if hard_blocker:
            task_run = self._task_runs.block(
                task_run.run_id,
                error=block_error,
                next_action="Resolve the blocker, then retry the task.",
            ) or task_run
            return TaskRunCompletionResult(task_run, operator_verdict)

        next_action = (
            invariant_error
            or (
                "Inspect the last tool diagnostic and apply one targeted correction."
                if request.unresolved_tool_failure
                else "Run a narrowed verification check before claiming completion."
            )
        )
        progress_prefix = (
            "The last step failed, but I’m continuing this task from its last checkpoint. "
            if request.unresolved_tool_failure
            else "I’m continuing this task from its last checkpoint. "
        )
        progress_reply = progress_prefix + f"Next: {next_action}"
        continuation_scheduled = await self._schedule_continuation(
            task_run,
            request.message,
            next_action=next_action,
            phase=(
                REPAIRING
                if request.unresolved_tool_failure
                or getattr(operator_verdict, "missing_apply", False)
                else VERIFYING
            ),
            error=request.unresolved_failure_detail or invariant_error,
            corrective_failure=bool(
                request.unresolved_tool_failure
                and request.recovery_state is not None
                and request.recovery_state.corrective_failures > 0
            ),
            recovery=request.recovery_state,
        )
        if not continuation_scheduled:
            task_run = self._task_runs.block(
                task_run.run_id,
                error=block_error,
                next_action=next_action,
            ) or task_run
            progress_reply = block_error
        return TaskRunCompletionResult(
            task_run=task_run,
            operator_verdict=operator_verdict,
            continuation_scheduled=continuation_scheduled,
            progress_reply=progress_reply,
        )
