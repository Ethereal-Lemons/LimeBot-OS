import json
from types import SimpleNamespace

import pytest


class FakeWS:
    def __init__(self):
        self.sent = []
        self.owner = None
        self.file_result = "fileSent"

    async def send(self, payload: str):
        self.sent.append(payload)
        data = json.loads(payload)
        if self.owner and data.get("type") == "sendFile":
            await self.owner._handle_bridge_message(
                json.dumps({"type": self.file_result, "requestId": data.get("requestId")})
            )


@pytest.mark.asyncio
async def test_send_file_blocks_forbidden(tmp_path):
    from channels.whatsapp import WhatsAppChannel
    from core.bus import MessageBus

    config = SimpleNamespace(bridge_url="ws://localhost:3000", allow_from=[])
    channel = WhatsAppChannel(config, MessageBus())
    channel._connected = True
    channel._ws = FakeWS()
    channel._ws.owner = channel

    forbidden = tmp_path / ".env"
    forbidden.write_text("secret", encoding="utf-8")
    ok = await channel.send_file("123@s.whatsapp.net", str(forbidden), "caption")
    assert ok is False
    assert channel._ws.sent == []


@pytest.mark.asyncio
async def test_send_file_blocks_persona_dir(tmp_path):
    from channels.whatsapp import WhatsAppChannel
    from core.bus import MessageBus

    config = SimpleNamespace(bridge_url="ws://localhost:3000", allow_from=[])
    channel = WhatsAppChannel(config, MessageBus())
    channel._connected = True
    channel._ws = FakeWS()
    channel._ws.owner = channel

    persona_dir = tmp_path / "persona"
    persona_dir.mkdir()
    p = persona_dir / "note.txt"
    p.write_text("hello", encoding="utf-8")

    ok = await channel.send_file("123@s.whatsapp.net", str(p), None)
    assert ok is False
    assert channel._ws.sent == []


@pytest.mark.asyncio
async def test_send_file_success(tmp_path):
    from channels.whatsapp import WhatsAppChannel
    from core.bus import MessageBus

    config = SimpleNamespace(bridge_url="ws://localhost:3000", allow_from=[])
    channel = WhatsAppChannel(config, MessageBus())
    channel._connected = True
    channel._ws = FakeWS()
    channel._ws.owner = channel

    p = tmp_path / "file.txt"
    p.write_text("ok", encoding="utf-8")

    ok = await channel.send_file("123@s.whatsapp.net", str(p), "caption")
    assert ok is True
    assert len(channel._ws.sent) == 1

    payload = json.loads(channel._ws.sent[0])
    assert payload["type"] == "sendFile"
    assert payload["to"] == "123@s.whatsapp.net"
    assert payload["filePath"] == str(p.resolve())
    assert payload["caption"] == "caption"


@pytest.mark.asyncio
async def test_send_routes_native_file_message(tmp_path):
    from channels.whatsapp import WhatsAppChannel
    from core.bus import MessageBus
    from core.events import OutboundMessage

    config = SimpleNamespace(bridge_url="ws://localhost:3000", allow_from=[])
    channel = WhatsAppChannel(config, MessageBus())
    channel._connected = True
    channel._ws = FakeWS()
    channel._ws.owner = channel

    p = tmp_path / "shared.png"
    p.write_text("ok", encoding="utf-8")

    await channel.send(
        OutboundMessage(
            channel="whatsapp",
            chat_id="123@s.whatsapp.net",
            content="",
            metadata={
                "type": "file",
                "file_path": str(p),
                "caption": "look",
            },
        )
    )

    assert len(channel._ws.sent) == 1
    payload = json.loads(channel._ws.sent[0])
    assert payload["type"] == "sendFile"
    assert payload["to"] == "123@s.whatsapp.net"
    assert payload["filePath"] == str(p.resolve())
    assert payload["caption"] == "look"


@pytest.mark.asyncio
async def test_incoming_voice_is_transcribed_and_audio_removed(monkeypatch, tmp_path):
    from channels import whatsapp as whatsapp_module
    from channels.whatsapp import WhatsAppChannel
    from core.bus import MessageBus
    from core.stt import ElevenLabsSTT

    bus = MessageBus()
    channel = WhatsAppChannel(SimpleNamespace(allow_from=[]), bus)
    monkeypatch.setattr(channel, "_check_contact_allowed", lambda *_args, **_kwargs: True)
    monkeypatch.setattr(whatsapp_module, "INBOUND_AUDIO_DIR", tmp_path.resolve())
    monkeypatch.setattr(ElevenLabsSTT, "is_enabled", classmethod(lambda cls: True))

    audio = tmp_path / "whatsapp_abc.ogg"
    audio.write_bytes(b"audio")

    async def fake_transcribe(path, mimetype):
        assert path == audio.resolve()
        assert mimetype == "audio/ogg"
        return "please summarize this"

    monkeypatch.setattr(ElevenLabsSTT, "transcribe_file", fake_transcribe)

    await channel._handle_incoming_message(
        {
            "id": "msg-1",
            "sender": "123@s.whatsapp.net",
            "content": "[Voice Message]",
            "audio": {
                "path": str(audio),
                "mimetype": "audio/ogg",
                "durationSeconds": 3,
            },
        }
    )

    message = await bus.consume_inbound()
    assert message.content == "[Voice message transcript]\nplease summarize this"
    assert message.metadata["voice_transcription"] is True
    assert message.metadata["voice_duration_seconds"] == 3
    assert not audio.exists()


@pytest.mark.asyncio
async def test_incoming_voice_without_elevenlabs_key_returns_status(monkeypatch, tmp_path):
    from channels import whatsapp as whatsapp_module
    from channels.whatsapp import WhatsAppChannel
    from core.bus import MessageBus
    from core.stt import ElevenLabsSTT

    bus = MessageBus()
    channel = WhatsAppChannel(SimpleNamespace(allow_from=[]), bus)
    monkeypatch.setattr(channel, "_check_contact_allowed", lambda *_args, **_kwargs: True)
    monkeypatch.setattr(whatsapp_module, "INBOUND_AUDIO_DIR", tmp_path.resolve())
    monkeypatch.setattr(ElevenLabsSTT, "is_enabled", classmethod(lambda cls: False))

    audio = tmp_path / "whatsapp_no_key.ogg"
    audio.write_bytes(b"audio")

    await channel._handle_incoming_message(
        {
            "id": "msg-2",
            "sender": "123@s.whatsapp.net",
            "content": "[Voice Message]",
            "audio": {"path": str(audio), "mimetype": "audio/ogg"},
        }
    )

    response = await bus.consume_outbound()
    assert response.channel == "whatsapp"
    assert "ELEVENLABS_API_KEY" in response.content
    assert not audio.exists()


def test_whatsapp_status_snapshot_distinguishes_bridge_and_account():
    from channels.whatsapp import WhatsAppChannel
    from core.bus import MessageBus

    channel = WhatsAppChannel(SimpleNamespace(allow_from=[]), MessageBus())
    channel._bridge_connected = True
    channel._whatsapp_status = "connecting"

    status = channel.get_status()

    assert status["status"] == "connecting"
    assert status["connected"] is False
    assert status["bridge_connected"] is True


@pytest.mark.asyncio
async def test_failed_voice_file_delivery_falls_back_to_text(tmp_path):
    from channels.whatsapp import WhatsAppChannel
    from core.bus import MessageBus
    from core.events import OutboundMessage

    bus = MessageBus()
    channel = WhatsAppChannel(SimpleNamespace(allow_from=[]), bus)
    channel._connected = True
    channel._ws = FakeWS()
    channel._ws.owner = channel
    channel._ws.file_result = "queued"

    audio = tmp_path / "voice.mp3"
    audio.write_bytes(b"audio")

    await channel.send(
        OutboundMessage(
            channel="whatsapp",
            chat_id="123@s.whatsapp.net",
            content="",
            metadata={
                "type": "file",
                "file_path": str(audio),
                "cleanup_file": True,
                "fallback_text": "Here is the reply as text.",
            },
        )
    )

    payloads = [json.loads(payload) for payload in channel._ws.sent]
    assert payloads[0]["type"] == "sendFile"
    assert payloads[1] == {
        "type": "send",
        "to": "123@s.whatsapp.net",
        "text": "Here is the reply as text.",
    }
    assert audio.exists()
