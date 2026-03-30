from types import SimpleNamespace

import pytest


class _FakeSession:
    def __init__(self):
        self.closed = False

    async def close(self):
        self.closed = True


@pytest.mark.asyncio
async def test_send_telegram_message_uses_send_message():
    from channels.telegram import TelegramChannel
    from core.bus import MessageBus
    from core.events import OutboundMessage

    config = SimpleNamespace(
        token="telegram-token",
        api_base="https://api.telegram.org",
        allow_from=[],
        allow_chats=[],
        poll_timeout=30,
    )
    channel = TelegramChannel(config, MessageBus())
    channel._session = _FakeSession()
    calls = []

    async def _fake_api_call(method, payload=None):
        calls.append((method, payload))
        return True

    channel._api_call = _fake_api_call

    await channel.send(
        OutboundMessage(channel="telegram", chat_id="1001", content="hello world")
    )

    assert calls == [
        (
            "sendMessage",
            {
                "chat_id": "1001",
                "text": "hello world",
                "disable_web_page_preview": True,
            },
        )
    ]


@pytest.mark.asyncio
async def test_handle_update_publishes_inbound_message():
    from channels.telegram import TelegramChannel
    from core.bus import MessageBus

    config = SimpleNamespace(
        token="telegram-token",
        api_base="https://api.telegram.org",
        allow_from=[],
        allow_chats=[],
        poll_timeout=30,
    )
    bus = MessageBus()
    inbound = []

    async def _capture(msg):
        inbound.append(msg)

    bus.publish_inbound = _capture
    channel = TelegramChannel(config, bus)

    await channel._handle_update(
        {
            "update_id": 42,
            "message": {
                "message_id": 7,
                "text": "hi from telegram",
                "chat": {"id": 1001, "type": "private"},
                "from": {
                    "id": 2002,
                    "username": "limeuser",
                    "first_name": "Lime",
                    "last_name": "User",
                },
            },
        }
    )

    assert channel._offset == 43
    assert len(inbound) == 1
    msg = inbound[0]
    assert msg.channel == "telegram"
    assert msg.chat_id == "1001"
    assert msg.sender_id == "2002"
    assert msg.content == "hi from telegram"
    assert msg.metadata["source"] == "telegram"
    assert msg.metadata["sender_username"] == "limeuser"


@pytest.mark.asyncio
async def test_refresh_bot_profile_updates_status():
    from channels.telegram import TelegramChannel
    from core.bus import MessageBus

    config = SimpleNamespace(
        token="telegram-token",
        api_base="https://api.telegram.org",
        allow_from=[],
        allow_chats=[],
        poll_timeout=30,
    )
    channel = TelegramChannel(config, MessageBus())

    async def _fake_api_call(method, payload=None):
        assert method == "getMe"
        return {
            "id": 123456,
            "username": "limebot_test_bot",
            "first_name": "LimeBot Test",
            "can_join_groups": True,
        }

    channel._api_call = _fake_api_call
    profile = await channel._refresh_bot_profile()

    assert profile["username"] == "limebot_test_bot"
    status = channel.get_status()
    assert status["connected"] is True
    assert status["status"] == "connected"
    assert status["username"] == "limebot_test_bot"
    assert status["bot_id"] == 123456
