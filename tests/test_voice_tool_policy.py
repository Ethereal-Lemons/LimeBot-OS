import asyncio
from types import SimpleNamespace

import pytest

from core.voice_tool_policy import (
    is_voice_session_key,
    voice_allows_tool,
    voice_refusal,
)


def test_voice_session_keys_are_recognised():
    assert is_voice_session_key("discord_discord-voice_895365716981731338_895365716981731342")
    assert not is_voice_session_key("discord_channel_123")
    assert not is_voice_session_key("web_app_1")
    assert not is_voice_session_key(None)


@pytest.mark.parametrize(
    "tool",
    [
        "read_file", "write_file", "edit_file", "delete_file", "list_dir",
        "search_files", "run_command", "run_steps", "spawn_agent", "send_media",
        "generate_image", "create_skill", "cron_remove",
    ],
)
def test_voice_blocks_file_shell_and_agent_tools(tool, monkeypatch):
    monkeypatch.delenv("DISCORD_VOICE_EXTRA_TOOLS", raising=False)
    assert not voice_allows_tool(tool)
    assert tool in voice_refusal(tool)


@pytest.mark.parametrize(
    "tool", ["web_search", "voice_list_members", "voice_move_members", "memory_search", "calculate"]
)
def test_voice_allows_conversational_tools(tool, monkeypatch):
    monkeypatch.delenv("DISCORD_VOICE_EXTRA_TOOLS", raising=False)
    assert voice_allows_tool(tool)


def test_extra_tools_can_be_opted_in(monkeypatch):
    monkeypatch.setenv("DISCORD_VOICE_EXTRA_TOOLS", "read_file, generate_image")
    assert voice_allows_tool("read_file")
    assert voice_allows_tool("generate_image")
    assert not voice_allows_tool("run_command")


@pytest.mark.asyncio
async def test_tool_runtime_refuses_blocked_tools_in_voice_sessions(monkeypatch):
    from core.tool_runtime import ToolExecutionContext, ToolRuntime

    monkeypatch.delenv("DISCORD_VOICE_EXTRA_TOOLS", raising=False)
    called = []

    async def read_file(**kwargs):
        called.append(kwargs)
        return "secret"

    runtime = ToolRuntime(
        toolbox=SimpleNamespace(),
        tool_registry={"read_file": read_file},
        tool_cache=SimpleNamespace(get=lambda *_a: None),
        metrics=SimpleNamespace(),
        hooks=SimpleNamespace(),
    )

    result = await runtime.execute(
        "read_file",
        {"path": "C:/Users/x/Downloads/report.docx"},
        ToolExecutionContext(session_key="discord_discord-voice_1_2"),
    )

    assert result.startswith("Error: Tool 'read_file' is disabled in Discord voice")
    assert called == []


@pytest.mark.asyncio
async def test_session_event_writes_are_tracked_and_drained():
    from core.loop import AgentLoop

    finished = []

    class Sessions:
        async def append_event_log(self, key, payload):
            await asyncio.sleep(0.05)
            finished.append(key)

    loop = object.__new__(AgentLoop)
    loop.session_manager = Sessions()

    loop._log_session_event("s1", {"type": "x"})
    loop._log_session_event("s2", {"type": "y"})
    assert len(loop._event_log_tasks) == 2

    await loop._drain_event_log_tasks()

    assert sorted(finished) == ["s1", "s2"]
    assert not loop._event_log_tasks
