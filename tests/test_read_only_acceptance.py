"""A question that changes nothing must not be sent back for verification."""

from types import SimpleNamespace

import pytest

from core.task_run_policy import acceptance_satisfied
from core.task_runs import derive_acceptance_criteria, is_coding_goal, is_read_only_request

LOG_QUESTION = (
    "Look at workspace/app.log. Which error code appears most often, how many times does it "
    "appear, and at what time (HH:MM:SS) did it first appear? Answer briefly."
)


def outcome(tool, success=True, text="", verification_status=None):
    return SimpleNamespace(
        tool=tool,
        success=success,
        diagnostic_head=text,
        diagnostic_tail=text,
        verification_status=verification_status,
        verification_detail=text,
    )


def accepted(goal, outcomes, *, coding_turn=False):
    run = SimpleNamespace(goal=goal, checkpoint={})
    return acceptance_satisfied(
        run,
        outcomes,
        coding_turn=coding_turn,
        iterations_limit_reached=False,
        unresolved_tool_failure=False,
        casual_turn=False,
    )


@pytest.mark.parametrize(
    "goal",
    [
        LOG_QUESTION,
        "Which function in core/loop.py handles retries?",
        "¿Qué sucursal tuvo las ventas más altas este mes?",
        "Count how many tests fail in the last report",
    ],
)
def test_questions_are_read_only(goal):
    assert is_read_only_request(goal)


@pytest.mark.parametrize(
    "goal",
    [
        "Fix the failing test in workspace/test_pricing.py",
        "Arregla el bug en workspace/pricing.py",
        "Can you fix this bug?",
        "Rename get_user_name to fetch_display_name everywhere",
        "Write a script that prints the sum of primes and tell me the number",
        "hello",
        "",
    ],
)
def test_requests_for_a_change_are_not_read_only(goal):
    assert not is_read_only_request(goal)


def test_the_keyword_match_is_what_made_the_question_a_coding_goal():
    # "error code" matches the coding keywords; that is why verification was demanded.
    assert is_coding_goal(LOG_QUESTION)
    assert "verification" not in " ".join(derive_acceptance_criteria(LOG_QUESTION))


def test_a_read_only_answer_completes_without_verification():
    reads = [outcome("read_file"), outcome("search_files")]
    assert accepted(LOG_QUESTION, reads)
    assert accepted(LOG_QUESTION, reads, coding_turn=True)
    assert accepted(LOG_QUESTION, [])


def test_verify_still_applies_when_the_turn_changed_files():
    # Same question wording, but the turn edited a file: proof is required.
    edited = [outcome("read_file"), outcome("edit_file", text="workspace/app.log")]
    assert not accepted(LOG_QUESTION, edited)
    verified = edited + [outcome("verify_files", verification_status="passed")]
    assert accepted(LOG_QUESTION, verified)


def test_a_coding_request_that_changed_nothing_still_needs_proof():
    # Claiming "fixed" without touching a file must not pass on its own.
    goal = "Fix the failing test in workspace/test_pricing.py"
    assert not accepted(goal, [outcome("read_file")])
    assert not accepted(goal, [])
    proof = [outcome("run_command", text="python -m unittest passed", verification_status="passed")]
    assert accepted(goal, proof)


def test_failures_and_the_iteration_limit_still_block_a_question():
    run = SimpleNamespace(goal=LOG_QUESTION, checkpoint={})
    base = dict(coding_turn=False, casual_turn=False)
    assert not acceptance_satisfied(
        run, [], iterations_limit_reached=True, unresolved_tool_failure=False, **base
    )
    assert not acceptance_satisfied(
        run, [], iterations_limit_reached=False, unresolved_tool_failure=True, **base
    )
