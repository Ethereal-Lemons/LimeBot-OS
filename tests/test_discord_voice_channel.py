"""How the Discord channel treats voice-session traffic in the shared text channel."""

from types import SimpleNamespace

import pytest

VOICE_CHAT = "99:discord-voice:1:2"
VOICE_SESSION = "discord-voice:1:2"


class Target:
    def __init__(self):
        self.sent = []

    async def send(self, content=None, **kwargs):
        self.sent.append({"content": content, **kwargs})
        return SimpleNamespace(edit=None, delete=None)


class FakeVoice:
    def __init__(self, spoken=True):
        self.spoken = spoken
        self.spoken_text = []

    def has_session(self, session_id):
        return session_id == VOICE_SESSION

    async def speak_reply(self, session_id, text):
        self.spoken_text.append(text)
        return self.spoken


def make_channel(monkeypatch, target, voice=None):
    from channels.discord import DiscordChannel
    from core.bus import MessageBus

    config = SimpleNamespace(
        token=None,
        allow_channels=[],
        allow_from=[],
        style_overrides={},
        signature="",
        emoji_set=[],
        verbosity_limits={"short": 10, "medium": 50, "long": 200},
        tone_prefixes={},
        embed_theme={},
        nickname_templates={},
        avatar_overrides={},
        upload_retention_hours=72,
    )
    channel = DiscordChannel(config, MessageBus())
    channel._voice_conversation = voice

    async def resolve(_chat_id):
        return target

    monkeypatch.setattr(channel, "_resolve_target", resolve)
    return channel


def reply(content, chat_id=VOICE_CHAT, **metadata):
    from core.events import OutboundMessage

    meta = {
        "event_kind": "assistant_message",
        "terminal": True,
        "discord_voice_session": VOICE_SESSION,
        **metadata,
    }
    return OutboundMessage(channel="discord", chat_id=chat_id, content=content, metadata=meta)


@pytest.mark.asyncio
async def test_a_short_spoken_reply_is_not_posted_in_the_channel(monkeypatch):
    target, voice = Target(), FakeVoice()
    channel = make_channel(monkeypatch, target, voice)

    await channel._send_impl_once(reply("Sí, aquí estoy. ¿Qué necesitas?"))

    assert voice.spoken_text == ["Sí, aquí estoy. ¿Qué necesitas?"]
    assert target.sent == []


@pytest.mark.asyncio
async def test_links_and_long_answers_are_also_posted_as_text(monkeypatch):
    target, voice = Target(), FakeVoice()
    channel = make_channel(monkeypatch, target, voice)

    await channel._send_impl_once(reply("Mira https://example.com/docs para el detalle."))
    await channel._send_impl_once(reply("palabra " * 120))

    assert len(voice.spoken_text) == 2
    assert len(target.sent) == 2


@pytest.mark.asyncio
async def test_a_failed_playback_falls_back_to_text(monkeypatch):
    target, voice = Target(), FakeVoice(spoken=False)
    channel = make_channel(monkeypatch, target, voice)

    await channel._send_impl_once(reply("Hola"))

    texts = [item["content"] for item in target.sent]
    assert "Hola" in texts[0]
    assert any("couldn't play" in (text or "") for text in texts)


@pytest.mark.asyncio
async def test_warnings_are_posted_even_when_spoken(monkeypatch):
    target, voice = Target(), FakeVoice()
    channel = make_channel(monkeypatch, target, voice)

    await channel._send_impl_once(reply("Tuve que parar.", is_warning=True))

    assert len(voice.spoken_text) == 1 and len(target.sent) == 1


def tool_event(chat_id, status, **extra):
    from core.events import OutboundMessage

    return OutboundMessage(
        channel="discord",
        chat_id=chat_id,
        content="",
        metadata={"type": "tool_execution", "tool": "run_command", "status": status,
                  "args": {"command": "x"}, "result": "Error: nope", **extra},
    )


@pytest.mark.asyncio
async def test_tool_failures_are_not_posted_during_a_voice_session(monkeypatch):
    target = Target()
    channel = make_channel(monkeypatch, target, FakeVoice())

    await channel._send_impl_once(tool_event(VOICE_CHAT, "error"))

    assert target.sent == []


@pytest.mark.asyncio
async def test_tool_failures_still_show_in_normal_text_chats(monkeypatch):
    target = Target()
    channel = make_channel(monkeypatch, target, FakeVoice())

    await channel._send_impl_once(tool_event("99", "error"))

    assert len(target.sent) == 1 and target.sent[0].get("embed") is not None


@pytest.mark.asyncio
async def test_voice_tool_embeds_come_back_with_the_full_setting(monkeypatch):
    monkeypatch.setenv("DISCORD_TOOL_EMBEDS", "full")
    target = Target()
    channel = make_channel(monkeypatch, target, FakeVoice())

    await channel._send_impl_once(tool_event(VOICE_CHAT, "running"))

    assert len(target.sent) == 1
