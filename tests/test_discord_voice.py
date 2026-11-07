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
            events.append("notice")
            return SimpleNamespace(delete=lambda: None)

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
    )
    await conversation.leave(10)

    assert events[:3] == [
        ("connect", {}),
        "notice",
        "recording",
    ]


@pytest.mark.asyncio
async def test_voice_session_refuses_to_record_if_notice_cannot_be_sent(monkeypatch):
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
        )

    assert events == ["connect", "disconnect"]
