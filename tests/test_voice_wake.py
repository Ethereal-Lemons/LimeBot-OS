import asyncio
from types import SimpleNamespace

import pytest

from core.discord_voice import (
    DiscordVoiceConversation,
    VoiceUtterance,
    VoiceUtteranceBuffer,
    _VoiceSession,
)
from core.voice_wake import (
    ListeningSettings,
    is_addressed_to,
    listening_settings_from_config,
    parse_voice_names,
)


def test_parse_voice_names_splits_and_dedupes():
    assert parse_voice_names("Jennie, Yeni; jennie\n Jen") == ("Jennie", "Yeni", "jennie", "Jen")
    assert parse_voice_names("") == ()
    assert parse_voice_names(None) == ()


def test_settings_fall_back_to_persona_name_and_validate_mode():
    settings = listening_settings_from_config({"voice_name": "", "listen_mode": "bogus"}, "Jennie")
    assert settings.names == ("Jennie",)
    assert settings.mode == "wake"

    custom = listening_settings_from_config(
        {"voice_name": "Lima", "listen_mode": "always", "voice_follow_up_seconds": "abc"}, "Jennie"
    )
    assert custom.names == ("Lima",)
    assert custom.mode == "always"
    assert custom.follow_up_seconds == 0.0


@pytest.mark.parametrize(
    "text",
    [
        "Jennie, podés movernos a otro canal",
        "jenny puedes mover a todos",  # speech-to-text spelling drift
        "Hey Jennie what's the weather",
        "podés ayudarme, Jennie",
        "JÉNNIE dime algo",
    ],
)
def test_is_addressed_to_matches_name_at_start_or_end(text):
    assert is_addressed_to(text, ["Jennie"])


@pytest.mark.parametrize(
    "text",
    [
        "no sé qué quería decir con eso de lo que hablábamos ayer Jennie lo vio",
        "estamos jugando algo",
        "",
        "Jenkins llegó tarde",
    ],
)
def test_is_addressed_to_ignores_other_speech(text):
    assert not is_addressed_to(text, ["Jennie"])


def test_multi_word_name_and_short_names_are_exact():
    assert is_addressed_to("hey lime bot open the door", ["Lime Bot"])
    assert is_addressed_to("Al, ven", ["Al"])
    assert not is_addressed_to("Ana, ven", ["Al"])


def _session(owner_id=1, **kwargs):
    return _VoiceSession(
        session_id="discord-voice:1:2",
        guild_id=1,
        voice_channel=kwargs.pop("voice_channel", SimpleNamespace(id=2, name="General")),
        text_channel=SimpleNamespace(),
        owner_id=owner_id,
        voice_client=kwargs.pop("voice_client", SimpleNamespace()),
        sink=None,
        utterances=asyncio.Queue(),
        **kwargs,
    )


def _conversation(settings, forwarded):
    async def on_transcript(**kwargs):
        forwarded.append(kwargs["transcript"])

    return DiscordVoiceConversation(
        SimpleNamespace(),
        on_transcript=on_transcript,
        is_allowed=lambda _id: True,
        get_listening_settings=lambda: settings,
    )


@pytest.mark.asyncio
async def test_wake_mode_only_forwards_addressed_speech_then_allows_follow_up(monkeypatch):
    from core.stt import ElevenLabsSTT

    transcripts = iter(["cosas que digo a mis amigos", "Jennie, hola", "y también esto", "ruido de otro amigo"])

    async def fake_transcribe(_path, _mime):
        return next(transcripts)

    monkeypatch.setattr(ElevenLabsSTT, "transcribe_file", staticmethod(fake_transcribe))
    monkeypatch.setattr("core.discord_voice.count_speech_seconds", lambda _pcm: 5.0)

    forwarded: list[str] = []
    conversation = _conversation(ListeningSettings(mode="wake", names=("Jennie",), follow_up_seconds=60), forwarded)
    session = _session()

    def utterance(speaker):
        return VoiceUtterance(speaker_id=speaker, speaker_name=speaker, pcm=b"\x00" * 3840)

    await conversation._transcribe_and_forward(session, utterance("a"))
    await conversation._transcribe_and_forward(session, utterance("a"))
    await conversation._transcribe_and_forward(session, utterance("a"))
    await conversation._transcribe_and_forward(session, utterance("b"))

    assert forwarded == ["Jennie, hola", "y también esto"]


@pytest.mark.asyncio
async def test_always_mode_and_muted_listening(monkeypatch):
    from core.stt import ElevenLabsSTT

    async def fake_transcribe(_path, _mime):
        return "sin nombre"

    monkeypatch.setattr(ElevenLabsSTT, "transcribe_file", staticmethod(fake_transcribe))
    monkeypatch.setattr("core.discord_voice.count_speech_seconds", lambda _pcm: 5.0)
    forwarded: list[str] = []
    conversation = _conversation(ListeningSettings(mode="always"), forwarded)
    session = _session()
    item = VoiceUtterance("a", "A", b"\x00" * 3840)

    await conversation._transcribe_and_forward(session, item)
    session.listening = False
    await conversation._transcribe_and_forward(session, item)

    assert forwarded == ["sin nombre"]


def test_set_listening_pauses_capture_and_resumes():
    conversation = _conversation(ListeningSettings(), [])
    buffer = VoiceUtteranceBuffer(min_seconds=0)
    session = _session(utterance_buffer=buffer)
    conversation._sessions[1] = session
    frame = b"\x01" * 3_840

    assert conversation.set_listening(1, False)
    buffer.add_frame("a", "A", frame)
    assert buffer.take_ready(now=10**9) == []

    # Playback finishing must not silently re-enable a user-requested pause.
    buffer.unmute()
    buffer.add_frame("a", "A", frame)
    assert buffer.take_ready(now=10**9) == []

    assert conversation.set_listening(1, True)
    buffer.add_frame("a", "A", frame)
    assert len(buffer.take_ready(now=10**9)) == 1


class _Member:
    def __init__(self, member_id, name, bot=False, moves=None, fail=False):
        self.id = member_id
        self.display_name = name
        self.name = name.lower()
        self.bot = bot
        self._moves = moves if moves is not None else []
        self._fail = fail

    async def move_to(self, channel, reason=None):
        if self._fail:
            raise RuntimeError("nope")
        self._moves.append((self.display_name, channel.name))


def _guild(moves, *, can_move=True):
    perms = SimpleNamespace(move_members=can_move, connect=True)
    make = lambda cid, name, members: SimpleNamespace(
        id=cid, name=name, members=members, permissions_for=lambda _me: perms
    )
    general = make(2, "General", [_Member(10, "Ana", moves=moves), _Member(11, "Beto", moves=moves), _Member(99, "Bot", bot=True)])
    games = make(3, "Games", [])
    guild = SimpleNamespace(
        me=object(),
        voice_channels=[general, games],
        get_member=lambda _id: SimpleNamespace(guild_permissions=SimpleNamespace(administrator=False, manage_channels=False)),
    )
    general.guild = guild
    games.guild = guild
    return guild, general


@pytest.mark.asyncio
async def test_move_everyone_moves_humans_and_bot_follows():
    moves: list = []
    guild, general = _guild(moves)
    followed = []

    class VoiceClient:
        channel = None

        async def move_to(self, channel):
            followed.append(channel.name)
            self.channel = channel

        def is_connected(self):
            return True

        def is_recording(self):
            return True

        _connection = SimpleNamespace(dave_session=SimpleNamespace(ready=True))

        def is_dave_connection(self):
            return True

    conversation = _conversation(ListeningSettings(), [])
    session = _session(voice_channel=general, voice_client=VoiceClient())

    result = await conversation.move_members(
        session, target_ref="games", member_refs=["everyone"], reason="test"
    )

    assert moves == [("Ana", "Games"), ("Beto", "Games")]
    assert followed == ["Games"]
    assert session.voice_channel.name == "Games"
    assert "followed" in result


@pytest.mark.asyncio
async def test_bot_leaves_if_its_own_move_silently_times_out():
    guild, general = _guild([])
    left = []

    class VoiceClient:
        channel = general  # Pycord logs a warning and stays put on a move timeout

        async def move_to(self, _channel):
            return None

    conversation = _conversation(ListeningSettings(), [])

    async def fake_leave(guild_id):
        left.append(guild_id)

    conversation.leave = fake_leave
    session = _session(voice_channel=general, voice_client=VoiceClient())

    result = await conversation.move_members(
        session, target_ref="Games", member_refs=["everyone"], reason="test"
    )

    assert left == [1]
    assert "could not follow" in result


@pytest.mark.asyncio
async def test_move_some_members_keeps_bot_and_validates_names():
    moves: list = []
    guild, general = _guild(moves)
    conversation = _conversation(ListeningSettings(), [])
    session = _session(voice_channel=general)

    result = await conversation.move_members(
        session, target_ref="Games", member_refs=["ana"], reason="test"
    )
    assert moves == [("Ana", "Games")]
    assert session.voice_channel is general
    assert "followed" not in result

    missing = await conversation.move_members(
        session, target_ref="Games", member_refs=["zed"], reason="test"
    )
    assert missing.startswith("Error")
    unknown = await conversation.move_members(
        session, target_ref="Nowhere", member_refs=None, reason="test"
    )
    assert unknown.startswith("Error") and "Games" in unknown


@pytest.mark.asyncio
async def test_move_requires_discord_permission():
    guild, general = _guild([], can_move=False)
    conversation = _conversation(ListeningSettings(), [])
    result = await conversation.move_members(
        _session(voice_channel=general), target_ref="Games", member_refs=None, reason="x"
    )
    assert "Move Members" in result


@pytest.mark.asyncio
async def test_voice_tools_are_limited_to_the_session_owner(monkeypatch):
    from core.context import tool_context
    from core.discord_tool_service import DiscordToolService

    moves: list = []
    guild, general = _guild(moves)
    conversation = _conversation(ListeningSettings(), [])
    session = _session(owner_id=10, voice_channel=general)
    conversation._sessions[1] = session
    service = DiscordToolService(
        bus=None,
        get_channel_by_name=lambda _name: SimpleNamespace(voice_conversation=conversation),
    )

    tool_context.set({"discord_voice_session": session.session_id, "sender_id": "11"})
    denied = await service.voice_move("Games", None)
    assert denied.startswith("Error: Only the person who started")
    assert moves == []
    assert (await service.voice_listening(False)).startswith("Error: Only")
    assert session.listening is True

    listing = await service.voice_members()
    assert "Ana" in listing and "Games" in listing and "Bot" not in listing

    tool_context.set({"discord_voice_session": session.session_id, "sender_id": "10"})
    assert "Moved" in await service.voice_move("Games", ["ana"])
    assert "Listening stopped" in await service.voice_listening(False)
    assert session.listening is False

    tool_context.set({"sender_id": "10"})
    assert (await service.voice_members()).startswith("Error: This only works")
