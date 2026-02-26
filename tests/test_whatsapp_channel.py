import json
from types import SimpleNamespace

import pytest


class FakeWS:
    def __init__(self):
        self.sent = []

    async def send(self, payload: str):
        self.sent.append(payload)


@pytest.mark.asyncio
async def test_send_file_blocks_forbidden(tmp_path):
    from channels.whatsapp import WhatsAppChannel
    from core.bus import MessageBus

    config = SimpleNamespace(bridge_url="ws://localhost:3000", allow_from=[])
    channel = WhatsAppChannel(config, MessageBus())
    channel._connected = True
    channel._ws = FakeWS()

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
