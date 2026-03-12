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
