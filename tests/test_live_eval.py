import asyncio
import os
from pathlib import Path
from types import SimpleNamespace

import pytest

from core.live_eval import (
    BudgetedLLMClient,
    BudgetExceeded,
    LivePlan,
    LiveRunEvidence,
    assemble_reply,
    command_allowed,
    compare_reports,
    evaluate_checks,
    is_workspace_path,
    live_case_from_dict,
    load_live_cases,
    run_live,
    summarize,
    validate_live_case,
    worker_environment,
    write_fixture_files,
)
from core.task_eval import SourceSnapshot

ROOT = Path(__file__).resolve().parents[1]
CASES = load_live_cases(ROOT / "evals" / "live")


def test_there_is_a_meaningful_set_of_cases():
    assert len(CASES) >= 10
    assert len({case.id for case in CASES}) == len(CASES)
    assert len({case.category for case in CASES}) >= 5


@pytest.mark.parametrize("case", CASES, ids=lambda case: case.id)
def test_each_case_fails_untouched_and_passes_with_its_solution(case, tmp_path):
    validate_live_case(case)
    write_fixture_files(tmp_path, case.fixtures)
    write_fixture_files(tmp_path, case.hidden_fixtures)
    assert not all(evaluate_checks(case, tmp_path, "").values()), "a doing-nothing agent must not pass"

    write_fixture_files(tmp_path, case.solution)
    reply = " ".join(check.any_of[0] for check in case.reply_checks)
    results = evaluate_checks(case, tmp_path, reply)
    assert all(results.values()), [name for name, ok in results.items() if not ok]


def test_hidden_files_are_not_visible_to_the_agent():
    for case in CASES:
        visible = {item.path for item in case.fixtures}
        assert not visible & {item.path for item in case.hidden_fixtures}, case.id


def test_case_validation_rejects_unsafe_or_empty_cases():
    base = {"id": "ok-case", "prompt": "do it", "checks": {"reply": [{"id": "r", "any_of": ["x"]}]}}
    validate_live_case(live_case_from_dict(base))
    with pytest.raises(ValueError):
        validate_live_case(live_case_from_dict({**base, "checks": {}}))
    with pytest.raises(ValueError):
        validate_live_case(live_case_from_dict({**base, "fixtures": [{"path": "../evil.txt", "content": "x"}]}))
    with pytest.raises(ValueError):
        validate_live_case(live_case_from_dict({**base, "fixtures": [{"path": "workspace/.env", "content": "x"}]}))
    with pytest.raises(ValueError):
        validate_live_case(
            live_case_from_dict({**base, "checks": {"commands": [{"id": "c", "argv": ["bash", "-c", "x"]}]}})
        )
    with pytest.raises(ValueError):
        validate_live_case(live_case_from_dict({**base, "max_tokens": 10}))


def test_commands_are_limited_to_python_pytest_and_read_only_git():
    assert command_allowed("python -m unittest test_x")
    assert command_allowed("python workspace/primes.py")
    assert command_allowed("pytest -q workspace")
    assert command_allowed("git diff --stat")
    for blocked in (
        "rm -rf workspace",
        "curl http://example.com",
        "python a.py && curl x",
        "python a.py | sh",
        "git push origin main",
        "git commit -am x",
        "powershell -c ls",
        "",
    ):
        assert not command_allowed(blocked), blocked


def test_workspace_path_check_blocks_escapes(tmp_path):
    (tmp_path / "workspace").mkdir()
    assert is_workspace_path("workspace/a.py", tmp_path)
    assert is_workspace_path(str(tmp_path / "workspace" / "a.py"), tmp_path)
    assert not is_workspace_path("core/loop.py", tmp_path)
    assert not is_workspace_path("workspace/../core/loop.py", tmp_path)
    assert not is_workspace_path(str(tmp_path / "other.py"), tmp_path)
    assert not is_workspace_path("", tmp_path)


def test_worker_environment_passes_provider_keys_but_not_other_secrets(tmp_path):
    source = {
        "PATH": "p",
        "LLM_MODEL": "openai/x",
        "OPENAI_API_KEY": "sk-1",
        "OPENROUTER_API_KEY": "sk-2",
        "APP_API_KEY": "dashboard-secret",
        "ELEVENLABS_API_KEY": "voice-secret",
        "DISCORD_TOKEN": "discord-secret",
        "GITHUB_TOKEN": "gh-secret",
    }
    env = worker_environment(tmp_path / "p", tmp_path / "s", source, model="openai/override")
    assert env["LLM_MODEL"] == "openai/override"
    assert env["OPENAI_API_KEY"] == "sk-1" and env["OPENROUTER_API_KEY"] == "sk-2"
    for leaked in ("APP_API_KEY", "ELEVENLABS_API_KEY", "DISCORD_TOKEN", "GITHUB_TOKEN"):
        assert leaked not in env
    assert env["ENABLE_DISCORD"] == "false" and env["LIMEBOT_EVAL_LIVE"] == "1"


class _FakeStream:
    def __init__(self, usage):
        self._chunks = [SimpleNamespace(usage=None), SimpleNamespace(usage=usage)]

    def __aiter__(self):
        return self

    async def __anext__(self):
        if not self._chunks:
            raise StopAsyncIteration
        return self._chunks.pop(0)


class _FakeClient:
    def __init__(self):
        self.calls = 0

    async def complete(self, provider, request):
        self.calls += 1
        return _FakeStream(SimpleNamespace(prompt_tokens=700, completion_tokens=100))

    def resolve_provider(self, model):
        return ("provider", model)


async def _drain(stream):
    return [chunk async for chunk in stream]


def test_budget_client_counts_usage_and_refuses_past_the_call_cap():
    inner = _FakeClient()
    client = BudgetedLLMClient(inner, max_calls=2, max_tokens=1_000_000)
    request = SimpleNamespace(stream=True, session_id="s")

    async def scenario():
        for _ in range(2):
            await _drain(await client.complete(None, request))
        with pytest.raises(BudgetExceeded):
            await client.complete(None, request)

    asyncio.run(scenario())
    assert inner.calls == 2, "the capped call must never reach the provider"
    assert (client.tokens_in, client.tokens_out, client.exceeded) == (1400, 200, True)
    assert client.resolve_provider("m") == ("provider", "m")


def test_budget_client_refuses_past_the_token_cap_and_warmup_is_free():
    inner = _FakeClient()
    client = BudgetedLLMClient(inner, max_calls=50, max_tokens=1_000)

    async def scenario():
        warm = await client.complete(None, SimpleNamespace(stream=False, session_id="__warmup__"))
        assert warm.choices[0].message.content
        await _drain(await client.complete(None, SimpleNamespace(stream=True, session_id="s")))
        await _drain(await client.complete(None, SimpleNamespace(stream=True, session_id="s")))
        with pytest.raises(BudgetExceeded):
            await client.complete(None, SimpleNamespace(stream=True, session_id="s"))

    asyncio.run(scenario())
    assert inner.calls == 2 and client.calls == 2


def _row(case_id, passed, status="completed", category="bugfix"):
    return LiveRunEvidence(
        case_id=case_id,
        category=category,
        repeat=0,
        status=status,
        checks={"a": passed},
        tool_counts={},
        tool_failures=0,
        model_calls=4,
        tokens_in=1000,
        tokens_out=100,
        wall_time_ms=1,
    )


def test_summary_flags_false_successes_and_budget_overruns():
    rows = [_row("a", True), _row("b", False), _row("c", False, status="blocked")]
    summary = summarize(rows)
    assert summary["pass_rate"] == pytest.approx(0.333, abs=0.001)
    assert summary["false_successes"] == 1  # "b" claimed completed but failed its checks
    assert summary["by_case"] == {"a": 1.0, "b": 0.0, "c": 0.0}
    overrun = LiveRunEvidence(**{**_row("d", True).__dict__, "budget_exceeded": True})
    assert not overrun.passed


def test_compare_reports_names_regressions_and_improvements():
    before = {"summary": {"pass_rate": 0.5, "false_successes": 2, "by_case": {"a": 1.0, "b": 0.0, "c": 0.5}}}
    after = {"summary": {"pass_rate": 0.5, "false_successes": 1, "by_case": {"a": 0.0, "b": 1.0, "d": 1.0}}}
    result = compare_reports(before, after)
    assert result["regressions"] == ["a"] and result["improvements"] == ["b"]
    assert result["verdict"] == "mixed"
    assert result["only_before"] == ["c"] and result["only_after"] == ["d"]
    assert compare_reports(before, before)["verdict"] == "same"


def test_nothing_runs_without_a_model_or_with_a_tiny_budget():
    plan = LivePlan(cases=CASES[:1], snapshot=SourceSnapshot("live", ROOT), total_token_budget=100)
    with pytest.raises(ValueError):
        run_live(plan, {"LLM_MODEL": "openai/x"})
    plan = LivePlan(cases=CASES[:1], snapshot=SourceSnapshot("live", ROOT), total_token_budget=50_000)
    with pytest.raises(ValueError):
        run_live(plan, {})


def test_end_to_end_with_the_built_in_fake_model(tmp_path, monkeypatch):
    """The whole pipeline (agent, outbound capture, hidden checks, report) at zero cost."""
    monkeypatch.setenv("LIMEBOT_STATE_DIR", str(tmp_path / "parent-state"))
    case = next(item for item in CASES if item.id == "scoped-edit")
    source_env = {
        "PATH": os.environ.get("PATH", ""),
        "SYSTEMROOT": os.environ.get("SYSTEMROOT", ""),
        "LLM_MODEL": "openai/fake-model",
        "OPENAI_API_KEY": "sk-placeholder",
        "LIMEBOT_TEST_LLM_MODE": "1",
        "LIMEBOT_TEST_LLM_REPLY": "Done.",
    }
    plan = LivePlan(cases=(case,), snapshot=SourceSnapshot("live", ROOT), total_token_budget=50_000)
    report = run_live(plan, source_env)

    run = report["runs"][0]
    assert run["worker_code"] == "ok"
    assert run["status"] == "completed" and run["model_calls"] == 1
    # The fake model claimed success without editing anything: hidden checks catch it.
    assert run["checks"]["request_timeout_updated"] is False
    assert run["false_success"] is True and report["summary"]["false_successes"] == 1
    assert (tmp_path / "parent-state" / "data" / "evaluations").is_dir()

def _outbound(content, **metadata):
    return SimpleNamespace(content=content, metadata=metadata)


def test_reply_uses_the_final_answer_not_chunks_joined_with_newlines():
    # The model streams "E104" in pieces; joining chunks with newlines broke it.
    messages = [
        _outbound("E", type="chunk"),
        _outbound("104 appears most", type="chunk"),
        _outbound("", type="activity"),
        _outbound("E104 appears most often: 58 times.", event_kind="assistant_message", terminal=True),
    ]
    assert assemble_reply(messages) == "E104 appears most often: 58 times."


def test_reply_falls_back_to_chunks_joined_without_separators():
    messages = [_outbound("E", type="chunk"), _outbound("104", type="chunk"), _outbound("x", type="thinking")]
    assert assemble_reply(messages) == "E104"
    assert assemble_reply([]) == ""
