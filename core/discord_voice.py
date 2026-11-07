"""Experimental Discord voice conversations using Pycord's DAVE receive path."""

from __future__ import annotations

import asyncio
import contextlib
import tempfile
import threading
import time
import wave
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

from loguru import logger


_SAMPLE_RATE = 48_000
_CHANNELS = 2
_SAMPLE_WIDTH = 2
_FRAME_BYTES_PER_SECOND = _SAMPLE_RATE * _CHANNELS * _SAMPLE_WIDTH
_END_OF_SPEECH_SECONDS = 0.75
_MAX_UTTERANCE_SECONDS = 30
_MIN_UTTERANCE_SECONDS = 0.25
_CAPTURE_POLL_SECONDS = 0.15
_MAX_QUEUED_UTTERANCES = 4
_MAX_ACTIVE_SPEAKERS = 8
_STATUS_NOTICE_COOLDOWN_SECONDS = 30


@dataclass
class _SpeakerAudio:
    speaker_id: str
    speaker_name: str
    last_frame_at: float
    pcm: bytearray = field(default_factory=bytearray)


@dataclass(frozen=True)
class VoiceUtterance:
    speaker_id: str
    speaker_name: str
    pcm: bytes


class VoiceUtteranceBuffer:
    """Collect per-speaker PCM and split utterances after a short silence."""

    def __init__(
        self,
        *,
        silence_seconds: float = _END_OF_SPEECH_SECONDS,
        max_seconds: int = _MAX_UTTERANCE_SECONDS,
        min_seconds: float = _MIN_UTTERANCE_SECONDS,
        max_active_speakers: int = _MAX_ACTIVE_SPEAKERS,
    ) -> None:
        self.silence_seconds = silence_seconds
        self.max_bytes = int(_FRAME_BYTES_PER_SECOND * max_seconds)
        self.min_bytes = int(_FRAME_BYTES_PER_SECOND * min_seconds)
        self.max_active_speakers = max(1, int(max_active_speakers))
        self._speakers: dict[str, _SpeakerAudio] = {}
        self._lock = threading.Lock()

    def add_frame(
        self,
        speaker_id: str,
        speaker_name: str,
        pcm: bytes,
        *,
        received_at: float | None = None,
    ) -> None:
        if not pcm:
            return
        now = time.monotonic() if received_at is None else received_at
        with self._lock:
            capture = self._speakers.get(speaker_id)
            if capture is None:
                if len(self._speakers) >= self.max_active_speakers:
                    return
                capture = _SpeakerAudio(speaker_id, speaker_name, now)
                self._speakers[speaker_id] = capture
            elif len(capture.pcm) >= self.max_bytes:
                return
            capture.speaker_name = speaker_name
            capture.last_frame_at = now
            remaining = self.max_bytes - len(capture.pcm)
            capture.pcm.extend(pcm[:remaining])

    def take_ready(self, *, now: float | None = None) -> list[VoiceUtterance]:
        current = time.monotonic() if now is None else now
        ready: list[VoiceUtterance] = []
        with self._lock:
            for speaker_id, capture in list(self._speakers.items()):
                reached_limit = len(capture.pcm) >= self.max_bytes
                finished_speaking = current - capture.last_frame_at >= self.silence_seconds
                if not (reached_limit or finished_speaking):
                    continue
                self._speakers.pop(speaker_id, None)
                if len(capture.pcm) >= self.min_bytes:
                    ready.append(
                        VoiceUtterance(
                            speaker_id=capture.speaker_id,
                            speaker_name=capture.speaker_name,
                            pcm=bytes(capture.pcm),
                        )
                    )
        return ready

    def clear(self) -> None:
        with self._lock:
            self._speakers.clear()


@dataclass
class _VoiceSession:
    session_id: str
    guild_id: int
    voice_channel: Any
    text_channel: Any
    owner_id: int
    voice_client: Any
    sink: Any
    utterances: asyncio.Queue[VoiceUtterance]
    capture_task: asyncio.Task | None = None
    process_task: asyncio.Task | None = None
    playback_lock: asyncio.Lock = field(default_factory=asyncio.Lock)
    closed: bool = False
    backlog_notice_at: float = 0.0
    error_notice_at: float = 0.0


class DiscordVoiceConversation:
    """Own one listening, transcription, agent, and speech loop per guild."""

    def __init__(
        self,
        discord_module: Any,
        *,
        on_transcript: Callable[..., Any],
        is_allowed: Callable[[str], bool],
    ) -> None:
        self.discord = discord_module
        self._on_transcript = on_transcript
        self._is_allowed = is_allowed
        self._sessions: dict[int, _VoiceSession] = {}

    @property
    def available(self) -> bool:
        return bool(
            hasattr(self.discord, "sinks")
            and hasattr(self.discord.sinks, "Sink")
            and hasattr(self.discord, "Bot")
        )

    async def join(
        self,
        *,
        guild: Any,
        voice_channel: Any,
        text_channel: Any,
        owner_id: int,
    ) -> str:
        from core.stt import ElevenLabsSTT
        from core.tts import ElevenLabsTTS

        if not self.available:
            raise RuntimeError(
                "Discord voice receive is unavailable. Install the optional Discord voice feature first."
            )
        if not ElevenLabsSTT.is_enabled():
            raise RuntimeError(
                "Voice transcription needs an enabled ElevenLabs voice input and ELEVENLABS_API_KEY."
            )
        if not ElevenLabsTTS.get_api_key():
            raise RuntimeError(
                "Voice replies need ELEVENLABS_API_KEY so LimeBot can speak."
            )
        if isinstance(voice_channel, getattr(self.discord, "StageChannel", ())):
            raise RuntimeError("Voice conversations need a regular voice channel, not a Stage channel.")
        if guild.id in self._sessions:
            current = self._sessions[guild.id]
            if current.voice_channel.id == voice_channel.id:
                return current.session_id
            raise RuntimeError(
                "LimeBot is already listening in another voice channel in this server. Use /voice_leave first."
            )

        # Connectable.connect does not expose self_deaf. Pycord's VoiceClient
        # defaults to self_deaf=False, which is required for receive sinks.
        voice_client = await voice_channel.connect()
        notice = None
        try:
            if not await self._wait_for_dave(voice_client):
                raise RuntimeError(
                    "Discord's DAVE voice encryption did not become ready. LimeBot left without processing audio."
                )

            # Announce the external transcription before opening the receive
            # sink, so the channel is never recorded before its notice is sent.
            notice = await text_channel.send(
                f"🎙️ LimeBot is starting a voice conversation in <#{voice_channel.id}>. "
                "Speech from allowed Discord members is sent to ElevenLabs for transcription; "
                "LimeBot does not keep the audio. Use `/voice_leave` to stop."
            )

            utterance_buffer = VoiceUtteranceBuffer()
            sink = self._make_sink(utterance_buffer)
            voice_client.start_recording(sink)

            session_id = f"discord-voice:{guild.id}:{voice_channel.id}"
            session = _VoiceSession(
                session_id=session_id,
                guild_id=int(guild.id),
                voice_channel=voice_channel,
                text_channel=text_channel,
                owner_id=int(owner_id),
                voice_client=voice_client,
                sink=sink,
                utterances=asyncio.Queue(maxsize=_MAX_QUEUED_UTTERANCES),
            )
            session.capture_task = asyncio.create_task(
                self._capture_loop(session, utterance_buffer),
                name=f"discord-voice-capture-{guild.id}",
            )
            session.process_task = asyncio.create_task(
                self._process_loop(session),
                name=f"discord-voice-process-{guild.id}",
            )
            self._sessions[guild.id] = session
            return session_id
        except Exception:
            if notice is not None:
                with contextlib.suppress(Exception):
                    await notice.delete()
            with contextlib.suppress(Exception):
                if voice_client.is_recording():
                    voice_client.stop_recording()
            with contextlib.suppress(Exception):
                await voice_client.disconnect(force=True)
            raise

    async def leave(self, guild_id: int) -> bool:
        session = self._sessions.pop(int(guild_id), None)
        if session is None:
            return False
        session.closed = True
        current_task = asyncio.current_task()
        for task in (session.capture_task, session.process_task):
            if task is not None and task is not current_task and not task.done():
                task.cancel()
        if session.voice_client.is_recording():
            with contextlib.suppress(Exception):
                session.voice_client.stop_recording()
        with contextlib.suppress(Exception):
            await session.voice_client.disconnect(force=True)
        await asyncio.gather(
            *(
                task
                for task in (session.capture_task, session.process_task)
                if task is not None and task is not current_task
            ),
            return_exceptions=True,
        )
        return True

    async def close(self) -> None:
        for guild_id in list(self._sessions):
            await self.leave(guild_id)

    def can_leave(self, guild_id: int, user_id: int, *, administrator: bool) -> bool:
        session = self._sessions.get(int(guild_id))
        return bool(session and (session.owner_id == int(user_id) or administrator))

    def has_session(self, session_id: str) -> bool:
        return any(entry.session_id == session_id for entry in self._sessions.values())

    async def speak_reply(self, session_id: str, text: str) -> bool:
        from core.tts import ElevenLabsTTS

        session = next(
            (entry for entry in self._sessions.values() if entry.session_id == session_id),
            None,
        )
        if session is None or session.closed or not session.voice_client.is_connected():
            return False

        spoken_text = ElevenLabsTTS.clean_text_for_speech(text)
        if not spoken_text:
            return False

        async with session.playback_lock:
            if session.closed or not session.voice_client.is_connected():
                return False
            audio_path = await ElevenLabsTTS.synthesize_to_file(
                spoken_text, filename_prefix="discord_voice"
            )
            if not audio_path:
                logger.warning("[Discord Voice] Speech synthesis returned no audio.")
                return False
            audio_source = None
            try:
                audio_source = self.discord.FFmpegOpusAudio(audio_path, bitrate=96)
                await session.voice_client.play(audio_source, wait_finish=True)
                return True
            except Exception:
                logger.exception("[Discord Voice] Could not play the synthesized reply.")
                return False
            finally:
                if audio_source is not None:
                    with contextlib.suppress(Exception):
                        audio_source.cleanup()
                with contextlib.suppress(OSError):
                    Path(audio_path).unlink()

    async def _wait_for_dave(self, voice_client: Any, timeout: float = 12.0) -> bool:
        deadline = asyncio.get_running_loop().time() + timeout
        while asyncio.get_running_loop().time() < deadline:
            connection = getattr(voice_client, "_connection", None)
            dave_session = getattr(connection, "dave_session", None)
            if (
                callable(getattr(voice_client, "is_dave_connection", None))
                and voice_client.is_dave_connection()
                and bool(getattr(dave_session, "ready", False))
            ):
                return True
            if not voice_client.is_connected():
                return False
            await asyncio.sleep(0.1)
        return False

    def _make_sink(self, utterance_buffer: VoiceUtteranceBuffer) -> Any:
        base_sink = self.discord.sinks.Sink
        is_allowed = self._is_allowed

        class ConversationSink(base_sink):
            def __init__(self) -> None:
                super().__init__(filters={"time": 0, "users": [], "max_size": 0})

            def write(self, data: Any, user: Any) -> None:
                if user is None or getattr(user, "bot", False):
                    return
                speaker_id = str(getattr(user, "id", "") or "")
                if not speaker_id or not is_allowed(speaker_id):
                    return
                pcm = getattr(data, "pcm", data)
                if isinstance(pcm, bytes):
                    utterance_buffer.add_frame(
                        speaker_id,
                        str(getattr(user, "display_name", None) or getattr(user, "name", "User")),
                        pcm,
                    )

        return ConversationSink()

    async def _capture_loop(
        self, session: _VoiceSession, utterance_buffer: VoiceUtteranceBuffer
    ) -> None:
        try:
            while not session.closed:
                if not session.voice_client.is_connected():
                    await self.leave(session.guild_id)
                    return
                await asyncio.sleep(_CAPTURE_POLL_SECONDS)
                for utterance in utterance_buffer.take_ready():
                    try:
                        session.utterances.put_nowait(utterance)
                    except asyncio.QueueFull:
                        now = time.monotonic()
                        if now - session.backlog_notice_at >= 30:
                            session.backlog_notice_at = now
                            await self._send_text(
                                session,
                                "I'm catching up on the conversation. Please wait a moment before speaking again.",
                            )
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("[Discord Voice] Audio capture loop stopped unexpectedly.")
            await self.leave(session.guild_id)

    async def _process_loop(self, session: _VoiceSession) -> None:
        while not session.closed:
            try:
                utterance = await session.utterances.get()
            except asyncio.CancelledError:
                raise
            try:
                await self._transcribe_and_forward(session, utterance)
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                logger.exception(
                    f"[Discord Voice] Could not process an utterance from user {utterance.speaker_id}: {exc}"
                )
                now = time.monotonic()
                if now - session.error_notice_at >= _STATUS_NOTICE_COOLDOWN_SECONDS:
                    session.error_notice_at = now
                    await self._send_text(
                        session,
                        "I couldn't process that voice message. Check ElevenLabs voice input and try again.",
                    )
            finally:
                session.utterances.task_done()

    async def _transcribe_and_forward(
        self, session: _VoiceSession, utterance: VoiceUtterance
    ) -> None:
        from core.stt import ElevenLabsSTT

        audio_path = self._write_wav(utterance.pcm)
        try:
            transcript = await ElevenLabsSTT.transcribe_file(audio_path, "audio/wav")
        finally:
            with contextlib.suppress(OSError):
                Path(audio_path).unlink()
        if not transcript.strip():
            return
        await self._on_transcript(
            sender_id=utterance.speaker_id,
            sender_name=utterance.speaker_name,
            text_channel=session.text_channel,
            voice_channel=session.voice_channel,
            guild_id=str(session.guild_id),
            session_id=session.session_id,
            transcript=transcript,
        )

    @staticmethod
    def _write_wav(pcm: bytes) -> str:
        with tempfile.NamedTemporaryFile(prefix="limebot-discord-voice-", suffix=".wav", delete=False) as handle:
            path = handle.name
        try:
            with wave.open(path, "wb") as audio_file:
                audio_file.setnchannels(_CHANNELS)
                audio_file.setsampwidth(_SAMPLE_WIDTH)
                audio_file.setframerate(_SAMPLE_RATE)
                audio_file.writeframes(pcm)
            return path
        except Exception:
            with contextlib.suppress(OSError):
                Path(path).unlink()
            raise

    @staticmethod
    async def _send_text(session: _VoiceSession, content: str) -> None:
        try:
            await session.text_channel.send(content)
        except Exception:
            logger.debug("[Discord Voice] Could not send a voice status message.")
