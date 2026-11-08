from types import SimpleNamespace

import pytest

from core.discord_voice import VoiceUtteranceBuffer


def test_voice_buffer_splits_speech_per_speaker_after_silence():
    buffer = VoiceUtteranceBuffer(silence_seconds=0.75)
    frame = b"\x01" * 3_840  # 20 ms of 48 kHz, 16-bit stereo PCM

    for index in range(15):
        buffer.add_frame("user-a", "A", frame, received_at=1.0 + index * 0.02)
        buffer.add_frame("user-b", "B", frame, received_at=1.0 + index * 0.02)

    assert buffer.take_ready(now=1.9) == []
    utterances = buffer.take_ready(now=2.1)

    assert {item.speaker_id for item in utterances} == {"user-a", "user-b"}
    assert {item.speaker_name for item in utterances} == {"A", "B"}
    assert all(item.pcm == frame * 15 for item in utterances)


def test_voice_buffer_discards_too_short_audio():
    buffer = VoiceUtteranceBuffer(silence_seconds=0.75)
    frame = b"\x01" * 3_840
    buffer.add_frame("user-a", "A", frame * 5, received_at=1.0)

    assert buffer.take_ready(now=1.8) == []


def test_voice_buffer_bounds_active_speakers_and_utterance_length():
    buffer = VoiceUtteranceBuffer(
        silence_seconds=0.75,
        max_seconds=1,
        min_seconds=0,
        max_active_speakers=1,
    )
    frame = b"\x01" * 3_840

    for index in range(50):
        buffer.add_frame("user-a", "A", frame, received_at=1.0 + index * 0.02)
    buffer.add_frame("user-b", "B", frame, received_at=2.0)

    utterances = buffer.take_ready(now=2.1)

    assert len(utterances) == 1
    assert utterances[0].speaker_id == "user-a"
    assert len(utterances[0].pcm) == 48_000 * 2 * 2

    buffer.add_frame("user-b", "B", frame, received_at=2.2)
    assert buffer.take_ready(now=3.0)[0].speaker_id == "user-b"


@pytest.mark.asyncio
async def test_voice_session_announces_before_recording_and_uses_connect_defaults(monkeypatch):
    from core.discord_voice import DiscordVoiceConversation
    from core.stt import ElevenLabsSTT
    from core.tts import ElevenLabsTTS

    monkeypatch.setattr(ElevenLabsSTT, "is_enabled", staticmethod(lambda: True))
    monkeypatch.setattr(ElevenLabsTTS, "get_api_key", staticmethod(lambda: "configured"))
    events = []

    class Sink:
        def __init__(self, *, filters):
            self.filters = filters

    class VoiceClient:
        _connection = SimpleNamespace(dave_session=SimpleNamespace(ready=True))

        def is_dave_connection(self):
            return True

        def is_connected(self):
            return True

        async def disconnect(self, *, force):
            events.append("disconnect")

        def start_recording(self, sink):
            events.append("recording")

        def is_recording(self):
            return "recording" in events

        def stop_recording(self):
            events.append("stop_recording")

    voice_client = VoiceClient()

    class VoiceChannel:
        id = 20

        async def connect(self, **kwargs):
            events.append(("connect", kwargs))
            return voice_client

    class TextChannel:
        async def send(self, content):
            events.append("public message")

    async def notify(_text):
        events.append("notice")

    discord_module = SimpleNamespace(
        Bot=object,
        StageChannel=type("StageChannel", (), {}),
        sinks=SimpleNamespace(Sink=Sink),
    )
    conversation = DiscordVoiceConversation(
        discord_module,
        on_transcript=lambda **_kwargs: None,
        is_allowed=lambda _speaker_id: True,
    )

    await conversation.join(
        guild=SimpleNamespace(id=10),
        voice_channel=VoiceChannel(),
        text_channel=TextChannel(),
        owner_id=1,
        notify=notify,
    )
    await conversation.leave(10)

    # The notice goes to the caller, never to the shared text channel.
    assert events[:3] == [
        ("connect", {}),
        "notice",
        "recording",
    ]
    assert "public message" not in events


@pytest.mark.asyncio
async def test_voice_session_refuses_to_record_if_notice_cannot_be_delivered(monkeypatch):
    from core.discord_voice import DiscordVoiceConversation
    from core.stt import ElevenLabsSTT
    from core.tts import ElevenLabsTTS

    monkeypatch.setattr(ElevenLabsSTT, "is_enabled", staticmethod(lambda: True))
    monkeypatch.setattr(ElevenLabsTTS, "get_api_key", staticmethod(lambda: "configured"))
    events = []

    class Sink:
        def __init__(self, *, filters):
            self.filters = filters

    class VoiceClient:
        _connection = SimpleNamespace(dave_session=SimpleNamespace(ready=True))

        def is_dave_connection(self):
            return True

        def is_connected(self):
            return True

        async def disconnect(self, *, force):
            events.append("disconnect")

        def start_recording(self, sink):
            events.append("recording")

        def is_recording(self):
            return False

    voice_client = VoiceClient()

    class VoiceChannel:
        id = 20

        async def connect(self, **_kwargs):
            events.append("connect")
            return voice_client

    class TextChannel:
        async def send(self, _content):
            raise AssertionError("the shared channel must stay quiet")

    async def notify(_text):
        raise RuntimeError("missing send permission")

    discord_module = SimpleNamespace(
        Bot=object,
        StageChannel=type("StageChannel", (), {}),
        sinks=SimpleNamespace(Sink=Sink),
    )
    conversation = DiscordVoiceConversation(
        discord_module,
        on_transcript=lambda **_kwargs: None,
        is_allowed=lambda _speaker_id: True,
    )

    with pytest.raises(RuntimeError, match="missing send permission"):
        await conversation.join(
            guild=SimpleNamespace(id=10),
            voice_channel=VoiceChannel(),
            text_channel=TextChannel(),
            owner_id=1,
            notify=notify,
        )

    assert events == ["connect", "disconnect"]


def test_voice_replies_post_text_only_when_speech_cannot_carry_them():
    from core.discord_voice import reply_needs_text

    assert not reply_needs_text("Sí, aquí estoy, te escucho. ¿Qué pasó?")
    assert not reply_needs_text("   ")
    assert reply_needs_text("Mira esto: https://example.com/docs")
    assert reply_needs_text("```print(1)```")
    assert reply_needs_text("palabra " * 100)


def _tone(seconds: float, amplitude: int) -> bytes:
    import array

    samples = array.array("h", [amplitude, -amplitude] * int(48_000 * seconds))
    return samples.tobytes()


def test_speech_gate_rejects_quiet_noise_and_accepts_speech():
    from core.discord_voice import count_speech_seconds

    assert count_speech_seconds(_tone(1.0, 80)) == 0
    assert count_speech_seconds(_tone(1.0, 4_000)) == pytest.approx(1.0, abs=0.05)
    # A short click inside silence is not speech.
    click = _tone(0.06, 8_000) + _tone(1.0, 0)
    assert count_speech_seconds(click) < 0.3


def test_non_speech_transcripts_are_dropped():
    from core.discord_voice import is_non_speech_transcript

    assert is_non_speech_transcript("")
    assert is_non_speech_transcript("(background noise)")
    assert is_non_speech_transcript(" [click] ")
    assert not is_non_speech_transcript("hello there (laughs)")


def test_voice_buffer_ignores_audio_while_muted_and_during_echo_tail():
    buffer = VoiceUtteranceBuffer(min_seconds=0)
    frame = b"\x01" * 3_840

    buffer.mute()
    buffer.add_frame("user-a", "A", frame)
    buffer.unmute(tail_seconds=60)
    buffer.add_frame("user-a", "A", frame)
    assert buffer.take_ready(now=10**9) == []

    buffer.unmute()
    buffer.add_frame("user-a", "A", frame)
    assert len(buffer.take_ready(now=10**9)) == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("pycord_order", [True, False])
async def test_tool_confirmation_buttons_accept_both_library_argument_orders(pycord_order):
    import discord

    from channels.discord import ToolConfirmationView

    resolved = []
    published = []

    class Agent:
        async def confirm_tool(self, conf_id, approved, whitelist, source):
            resolved.append((conf_id, approved, source))
            return True

    class Bus:
        async def publish_inbound(self, message):
            published.append(message.content)

    class FakeInteraction:
        user = SimpleNamespace(id=7)

        def __init__(self):
            self.message = SimpleNamespace(embeds=[discord.Embed(title="Exec Approval Required")])
            self.response = SimpleNamespace(edit_message=self._edit)
            self.edited = False

        async def _edit(self, **_kwargs):
            self.edited = True

    interaction = FakeInteraction()
    button = SimpleNamespace()
    view = ToolConfirmationView("conf_1", "chat", Bus(), SimpleNamespace(), Agent())
    approve = type(view).approve_button

    # isinstance(..., discord.Interaction) is how the view tells the two apart.
    import channels.discord as channel_module

    original = channel_module.discord.Interaction
    channel_module.discord.Interaction = FakeInteraction
    try:
        args = (button, interaction) if pycord_order else (interaction, button)
        await approve(view, *args)
    finally:
        channel_module.discord.Interaction = original

    assert interaction.edited
    assert resolved == [("conf_1", True, "discord")]
    assert published == ["[CONFIRMATION_APPROVED:conf_1]"]


@pytest.mark.asyncio
async def test_discord_tool_embeds_are_quiet_unless_failed(monkeypatch):
    from channels.discord import DiscordChannel

    sent = []

    class Target:
        async def send(self, **kwargs):
            sent.append(kwargs)

    channel = DiscordChannel.__new__(DiscordChannel)
    channel._tool_messages = {}
    channel._build_tool_embed = lambda **kwargs: kwargs["status"]
    monkeypatch.delenv("DISCORD_TOOL_EMBEDS", raising=False)

    for status in ("running", "completed", "error"):
        await channel._handle_tool_execution(
            Target(), {"tool_call_id": "t", "status": status, "tool": "web_search"}
        )

    assert sent == [{"embed": "error"}]
