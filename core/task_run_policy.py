"""Acceptance policy for durable task runs."""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from core.operator_invariants import (
    evaluate_operator_invariants,
    has_verification_evidence,
    turn_had_file_mutations,
)
from core.task_runs import is_coding_goal, is_read_only_request


def acceptance_satisfied(
    run: Any,
    outcomes: List[Any],
    *,
    coding_turn: bool,
    iterations_limit_reached: bool,
    unresolved_tool_failure: bool,
    history: Optional[List[Dict[str, Any]]] = None,
    session_key: str = "",
    casual_turn: bool = False,
    plan_mode: bool = False,
    pending_workspaces: Optional[List[Any]] = None,
) -> bool:
    """Require verification and operator invariants before task completion."""
    if iterations_limit_reached or unresolved_tool_failure:
        return False
    goal = getattr(run, "goal", "")
    coding_goal = is_coding_goal(goal)
    mutated = turn_had_file_mutations(outcomes, history)
    if casual_turn and not mutated and not coding_turn and not coding_goal:
        return True
    verdict = evaluate_operator_invariants(
        casual_turn=casual_turn,
        plan_mode=plan_mode,
        coding_turn=coding_turn,
        coding_goal=coding_goal,
        outcomes=outcomes,
        history=history,
        session_key=session_key,
        pending_workspaces=pending_workspaces,
        unresolved_tool_failure=unresolved_tool_failure,
        iterations_limit_reached=iterations_limit_reached,
    )
    if verdict.applies and not verdict.ok:
        return False
    # A question that asks for no change and changed nothing has nothing to verify.
    # Anything that mutated files, or a coding request that did not read as a
    # question, still needs proof before it can claim completion.
    read_only_answer = not mutated and is_read_only_request(goal)
    if not outcomes:
        checkpoint = getattr(run, "checkpoint", {}) or {}
        if read_only_answer:
            return True
        if mutated or coding_goal:
            return checkpoint.get("verification_state") == "passed"
        return True
    if read_only_answer:
        return True
    if mutated or coding_turn or coding_goal:
        return has_verification_evidence(outcomes, history=history)
    return True
