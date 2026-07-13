import asyncio
from types import SimpleNamespace

import pytest


class _DummyTypingContext:
    def __init__(self):
        self.entered = 0
        self.exited = 0
        self.enter_event = asyncio.Event()

    async def __aenter__(self):
        self.entered += 1
        self.enter_event.set()
        return self

    async def __aexit__(self, exc_type, exc, tb):
        self.exited += 1
        return False


class _DummyTarget:
    def __init__(self):
        self.ctx = _DummyTypingContext()
        self.id = 1
        self.guild = None

    def typing(self):
        return self.ctx


def _make_config():
    return SimpleNamespace(
        token=None,
        allow_channels=[],
        allow_from=[],
        style_overrides={},
        signature="",
        emoji_set=["🍋"],
        verbosity_limits={"short": 10, "medium": 50, "long": 200},
        tone_prefixes={
            "neutral": "",
            "friendly": "Hey!",
            "direct": "Heads up:",
            "formal": "Note:",
        },
        embed_theme={},
        nickname_templates={},
        avatar_overrides={},
    )


@pytest.mark.asyncio
async def test_discord_typing_keepalive_starts_and_stops():
    from channels.discord import DiscordChannel
    from core.bus import MessageBus

    channel = DiscordChannel(_make_config(), MessageBus())
    target = _DummyTarget()

    await channel._send_typing(target, "123")
    await asyncio.wait_for(target.ctx.enter_event.wait(), timeout=1.0)

    session_key = "discord_123"
    assert session_key in channel._typing_tasks
    assert target.ctx.entered == 1

    await channel._stop_typing("123")

    assert session_key not in channel._typing_tasks
    assert target.ctx.exited == 1


@pytest.mark.asyncio
async def test_discord_typing_keepalive_is_reused():
    from channels.discord import DiscordChannel
    from core.bus import MessageBus

    channel = DiscordChannel(_make_config(), MessageBus())
    target = _DummyTarget()

    await channel._send_typing(target, "123")
    await asyncio.wait_for(target.ctx.enter_event.wait(), timeout=1.0)
    first_task = channel._typing_tasks["discord_123"]

    await channel._send_typing(target, "123")

    assert channel._typing_tasks["discord_123"] is first_task
    assert target.ctx.entered == 1

    await channel._stop_typing("123")


@pytest.mark.asyncio
async def test_discord_waits_for_complete_response_without_stream_events():
    from core.bus import MessageBus
    from core.events import InboundMessage
    from core.loop import AgentLoop

    class _TestAgentLoop(AgentLoop):
        async def _init_skills_and_tools(self) -> None:
            self._tool_definitions = []
            self._warmed = True

    async def _stream():
        for content in ("This is ", "the complete ", "Discord response."):
            delta = SimpleNamespace(content=content, tool_calls=None)
            yield SimpleNamespace(
                choices=[SimpleNamespace(delta=delta)],
                usage=None,
            )

    bus = MessageBus()
    agent = _TestAgentLoop(bus=bus)
    msg = InboundMessage(
        channel="discord",
        sender_id="user-1",
        chat_id="123",
        content="answer this",
        metadata={"mentioned": True},
    )
    queued_outputs = []

    result = await agent._consume_stream(
        _stream(),
        msg,
        msg.session_key,
        on_output_queued=queued_outputs.append,
    )
    full_content, tool_calls, _, streamed_to_web, streamed_to_discord = (
        agent._unpack_stream_result(result)
    )

    assert full_content == "This is the complete Discord response."
    assert tool_calls == []
    assert streamed_to_web is False
    assert streamed_to_discord is False
    assert queued_outputs == []
    assert bus.outbound.empty()


@pytest.mark.asyncio
async def test_discord_forbidden_dm_reports_to_origin_chat():
    import discord
    from channels.discord import DiscordChannel
    from core.bus import MessageBus
    from core.events import OutboundMessage

    bus = MessageBus()
    reported = []

    async def _capture(msg):
        reported.append(msg)

    bus.publish_outbound = _capture
    channel = DiscordChannel(_make_config(), bus)

    async def _raise_forbidden(_msg):
        response = SimpleNamespace(status=403, reason="Forbidden")
        raise discord.Forbidden(response, "Cannot send messages to this user")

    channel._send_impl_once = _raise_forbidden

    await channel._send_impl(
        OutboundMessage(
            channel="discord",
            chat_id="123456789012345678",
            content="hello",
            metadata={
                "target_type": "dm",
                "origin_channel": "web",
                "origin_chat_id": "web-chat",
            },
        )
    )

    assert len(reported) == 1
    assert reported[0].channel == "web"
    assert reported[0].chat_id == "web-chat"
    assert "Discord rejected the DM" in reported[0].content
    assert "DMs disabled" in reported[0].content
