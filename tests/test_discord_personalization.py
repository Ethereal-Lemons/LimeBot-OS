from types import SimpleNamespace

import pytest


class DummyGuild:
    def __init__(self, gid: str, name: str = "Guild"):
        self.id = int(gid)
        self.name = name


class DummyTarget:
    def __init__(self, cid: str, guild: DummyGuild | None = None):
        self.id = int(cid)
        self.guild = guild


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
