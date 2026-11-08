import pytest

from core.loop import AgentLoop
from core.session_manager import _sanitize_history_rows
from core.tool_dispatcher import DEFAULT_TOOL_RESULT_LIMIT, TOOL_RESULT_LIMITS


def _loop(model="openai/unknown-model"):
    loop = object.__new__(AgentLoop)
    loop.model = model
    return loop


def test_budget_override_and_unknown_model_default(monkeypatch):
    monkeypatch.delenv("LIMEBOT_CONTEXT_TOKENS", raising=False)
    assert _loop("totally/unknown-model")._context_budget_tokens() == 48_000

    monkeypatch.setenv("LIMEBOT_CONTEXT_TOKENS", "90000")
    assert _loop("totally/unknown-model")._context_budget_tokens() == 90_000

    monkeypatch.setenv("LIMEBOT_CONTEXT_TOKENS", "10")  # too small to be sensible
    assert _loop("another/unknown-model")._context_budget_tokens() == 48_000


def test_budget_follows_a_known_model_window(monkeypatch):
    monkeypatch.delenv("LIMEBOT_CONTEXT_TOKENS", raising=False)
    import litellm

    monkeypatch.setattr(litellm, "get_model_info", lambda _m: {"max_input_tokens": 200_000})
    assert _loop("known/model-a")._context_budget_tokens() == 100_000
    monkeypatch.setattr(litellm, "get_model_info", lambda _m: {"max_input_tokens": 16_000})
    assert _loop("known/model-b")._context_budget_tokens() == 12_000
    monkeypatch.setattr(litellm, "get_model_info", lambda _m: {"max_input_tokens": 2_000_000})
    assert _loop("known/model-c")._context_budget_tokens() == 120_000


def _tool_message(index, size):
    return {"role": "tool", "name": "read_file", "tool_call_id": f"t{index}", "content": f"{index}:" + "x" * size}


def test_squash_only_when_over_budget_and_keeps_recent_results():
    conv = [{"role": "user", "content": "task"}]
    conv += [_tool_message(i, 8_000) for i in range(12)]
    conv += [{"role": "user", "content": "continue"}]
    conv += [_tool_message(20 + i, 8_000) for i in range(3)]

    total = AgentLoop._estimate_tokens(conv)
    # Under budget: nothing changes.
    assert AgentLoop._squash_old_tool_results(conv, total + 1, total) == total
    assert all("[SQUASHED" not in m["content"] for m in conv if m["role"] == "tool")

    # Over budget: oldest results shrink first, the recent ones stay intact.
    squashed_total = AgentLoop._squash_old_tool_results(conv, total // 2, total)
    tools = [m for m in conv if m["role"] == "tool"]
    assert squashed_total < total
    assert "[SQUASHED - read_file" in tools[0]["content"]
    assert 1_000 < len(tools[0]["content"]) < 1_500  # a real excerpt, not 160 chars
    assert all("[SQUASHED" not in m["content"] for m in tools[-8:])
    assert conv[-1]["content"].startswith("22:")


@pytest.mark.asyncio
async def test_trim_history_keeps_everything_that_fits_the_budget(monkeypatch):
    monkeypatch.delenv("LIMEBOT_CONTEXT_TOKENS", raising=False)
    loop = _loop("totally/unknown-model")
    system = {"role": "system", "content": "system prompt"}
    conv = [{"role": "user", "content": "do the long task"}]
    for i in range(10):
        conv.append({"role": "tool", "name": "read_file", "tool_call_id": f"t{i}", "content": "y" * 6_000})
    loop.history = {"s": [system] + conv}
    loop._normalize_history_in_place = lambda _key: loop.history["s"]
    loop._history_replace = lambda key, entries, mark_dirty=False: loop.history.__setitem__(key, entries)
    loop._mark_dirty = lambda _key: None

    await loop._trim_history("s")

    kept = [m for m in loop.history["s"] if m["role"] == "tool"]
    # 60k characters is far below the 48k-token budget; the old 12k limit would squash it.
    assert len(kept) == 10
    assert all(len(m["content"]) == 6_000 for m in kept)


def test_tool_output_limits_leave_room_for_command_output():
    assert TOOL_RESULT_LIMITS["run_command"] >= 6_000
    assert TOOL_RESULT_LIMITS["list_dir"] >= 3_000
    assert DEFAULT_TOOL_RESULT_LIMIT >= 4_000


def test_history_cleaning_is_idempotent_for_tool_call_messages():
    rows = [
        {"role": "user", "content": "hi"},
        {"role": "assistant", "content": "", "tool_calls": [{"id": "1", "function": {"name": "x"}}]},
        {"role": "tool", "tool_call_id": "1", "content": "ok"},
        {"role": "assistant", "content": "done"},
    ]
    cleaned, count = _sanitize_history_rows(rows)
    assert count == 0
    assert cleaned == rows

    # Whitespace text next to tool calls is cleaned once, then stays clean.
    dirty = [dict(rows[1], content="  ")]
    once, first = _sanitize_history_rows(dirty)
    _twice, second = _sanitize_history_rows(once)
    assert (first, second) == (1, 0)

    # An empty assistant message without tool calls is still dropped.
    dropped, dropped_count = _sanitize_history_rows([{"role": "assistant", "content": ""}])
    assert dropped == [] and dropped_count == 1


def test_mid_turn_pressure_shrinks_old_results_but_keeps_the_latest():
    loop = _loop("totally/unknown-model")
    marked = []
    loop._mark_dirty = marked.append
    history = [{"role": "system", "content": "s"}, {"role": "user", "content": "one very long task"}]
    history += [_tool_message(i, 8_000) for i in range(40)]  # ~80k tokens, all in the current turn

    before = AgentLoop._estimate_tokens(history)
    loop._relieve_context_pressure("s", history)

    assert AgentLoop._estimate_tokens(history) < before
    tools = [m for m in history if m["role"] == "tool"]
    assert "[SQUASHED" in tools[0]["content"]
    assert all("[SQUASHED" not in m["content"] for m in tools[-8:])
    assert marked == ["s"]

    # Under the ceiling nothing happens.
    small = [{"role": "system", "content": "s"}, {"role": "user", "content": "q"}, _tool_message(0, 100)]
    loop._relieve_context_pressure("s", small)
    assert "[SQUASHED" not in small[-1]["content"]
