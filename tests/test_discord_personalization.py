import asyncio
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest


class DummyGuild:
    def __init__(self, gid: str, name: str = "Guild"):
        self.id = int(gid)
        self.name = name


class DummyTarget:
    def __init__(self, cid: str, guild: DummyGuild | None = None):
        self.id = int(cid)
        self.guild = guild


class DummyLiveMessage:
    def __init__(self, content: str = ""):
        self.content = content
        self.edits: list[str] = []
        self.deleted = False

    async def edit(self, *, content: str):
        self.content = content
        self.edits.append(content)

    async def delete(self):
        self.deleted = True


class DummySendTarget(DummyTarget):
    def __init__(self, cid: str, guild: DummyGuild | None = None):
        super().__init__(cid, guild=guild)
        self.sent_messages: list[DummyLiveMessage] = []

    async def send(self, content=None, **kwargs):
        msg = DummyLiveMessage(content or "")
        self.sent_messages.append(msg)
        return msg


def make_config(**overrides):
    base = {
        "token": None,
        "allow_channels": [],
        "allow_from": [],
        "style_overrides": {},
        "signature": "",
        "emoji_set": ["🍋"],
        "verbosity_limits": {"short": 10, "medium": 50, "long": 200},
        "tone_prefixes": {
            "neutral": "",
            "friendly": "Hey!",
            "direct": "Heads up:",
            "formal": "Note:",
        },
        "embed_theme": {},
        "nickname_templates": {},
        "avatar_overrides": {},
    }
    base.update(overrides)
    return SimpleNamespace(**base)


def test_apply_style_default_tone_signature_emoji():
    from channels.discord import DiscordChannel
    from core.bus import MessageBus

    config = make_config(
        signature="LimeBot",
        style_overrides={"default": {"tone": "friendly", "emoji_usage": "light"}},
    )
    channel = DiscordChannel(config, MessageBus())
    target = DummyTarget("1")

    result = channel._apply_style("hello world", target)
    assert "Hey!" in result
    assert "🍋" in result
    assert "— LimeBot" in result


def test_apply_style_guild_override_disables_emoji():
    from channels.discord import DiscordChannel
    from core.bus import MessageBus

    config = make_config(
        signature="Base",
        style_overrides={
            "default": {"tone": "friendly", "emoji_usage": "light"},
            "guilds": {"123": {"tone": "direct", "emoji_usage": "none", "signature": "Ops"}},
        },
    )
    channel = DiscordChannel(config, MessageBus())
    target = DummyTarget("10", guild=DummyGuild("123", "OpsGuild"))

    result = channel._apply_style("status update", target)
    assert "Heads up:" in result
    assert "🍋" not in result
    assert "— Ops" in result


def test_apply_style_channel_override_max_length():
    from channels.discord import DiscordChannel
    from core.bus import MessageBus

    config = make_config(
        style_overrides={
            "channels": {"555": {"max_length": 10, "emoji_usage": "none"}},
        },
    )
    channel = DiscordChannel(config, MessageBus())
    target = DummyTarget("555")

    result = channel._apply_style("this message is too long", target)
    assert len(result) <= 10
    assert result.endswith("…")


def test_theme_color_override():
    from channels.discord import DiscordChannel
    from core.bus import MessageBus

    config = make_config(
        embed_theme={"default": "#00FF00", "guilds": {"777": "#123456"}},
    )
    channel = DiscordChannel(config, MessageBus())
    target = DummyTarget("1", guild=DummyGuild("777", "ThemeGuild"))

    color = channel._get_theme_color(target, 0xABCDEF)
    assert color == 0x123456


def test_extract_discord_attachment_urls_promotes_first_image():
    from channels.discord import DiscordChannel

    attachments = [
        SimpleNamespace(
            url="https://cdn.example.com/image.png",
            content_type="image/png",
            filename="image.png",
        ),
        SimpleNamespace(
            url="https://cdn.example.com/document.pdf",
            content_type="application/pdf",
            filename="document.pdf",
        ),
        SimpleNamespace(
            url="https://cdn.example.com/second.jpg",
            content_type="image/jpeg",
            filename="second.jpg",
        ),
    ]

    image_url, attachment_urls = DiscordChannel._extract_discord_attachment_urls(
        attachments
    )

    assert image_url == "https://cdn.example.com/image.png"
    assert attachment_urls == [
        "https://cdn.example.com/document.pdf",
        "https://cdn.example.com/second.jpg",
    ]


@pytest.mark.asyncio
async def test_finalize_live_message_edits_existing_stream_message():
    from channels.discord import DiscordChannel
    from core.bus import MessageBus

    channel = DiscordChannel(make_config(), MessageBus())
    target = DummySendTarget("99")
    session_key = "discord_99"
    live = DummyLiveMessage("draft")
    channel._live_messages[session_key] = live

    handled = await channel._finalize_live_message(target, "99", "Final answer")

    assert handled is True
    assert live.edits == ["Final answer 🍋"]
    assert session_key not in channel._live_messages
    assert target.sent_messages == []


@pytest.mark.asyncio
async def test_normalize_discord_attachments_extracts_document_text():
    from channels.discord import DiscordChannel
    from core.bus import MessageBus

    class FakeResponse:
        status = 200

        async def read(self):
            return b"%PDF-1.4 fake"

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return False

    class FakeSession:
        def __init__(self, *args, **kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return False

        def get(self, url, timeout=None):
            return FakeResponse()

    channel = DiscordChannel(make_config(), MessageBus())
    attachments = [
        SimpleNamespace(
            url="https://cdn.example.com/report.pdf",
            content_type="application/pdf",
            filename="report.pdf",
        )
    ]

    with patch("channels.discord.aiohttp.ClientSession", FakeSession), patch.object(
        DiscordChannel,
        "_extract_discord_document_text",
        return_value=("Quarterly metrics", None),
    ):
        normalized = await channel._normalize_discord_attachments("chat-7", attachments)

    assert len(normalized) == 1
    assert normalized[0]["kind"] == "document"
    assert normalized[0]["extracted_text"] == "Quarterly metrics"
    assert normalized[0]["path"].endswith(".pdf")
