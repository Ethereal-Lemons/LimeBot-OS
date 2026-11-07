import json

import pytest


def _write_config(path, voice_input):
    path.write_text(json.dumps({"voice_input": voice_input}), encoding="utf-8")


def test_stt_requires_api_key(monkeypatch, tmp_path):
    from core.stt import ElevenLabsSTT

    monkeypatch.setattr("core.stt.LIMEBOT_CONFIG_PATH", tmp_path / "limebot.json")
    monkeypatch.delenv("ELEVENLABS_API_KEY", raising=False)
    assert ElevenLabsSTT.is_enabled() is False


@pytest.mark.asyncio
async def test_stt_transcribes_audio(monkeypatch, tmp_path):
    from core.stt import ElevenLabsSTT

    config_path = tmp_path / "limebot.json"
    _write_config(config_path, {"enabled": True, "provider": "elevenlabs", "model_id": "scribe_v2"})
    monkeypatch.setattr("core.stt.LIMEBOT_CONFIG_PATH", config_path)
    monkeypatch.setenv("ELEVENLABS_API_KEY", "test-key")

    audio = tmp_path / "voice.ogg"
    audio.write_bytes(b"audio")
    calls = {}

    class FakeResponse:
        status_code = 200
        text = ""

        def json(self):
            return {"text": "hello from WhatsApp"}

    class FakeClient:
        def __init__(self, **kwargs):
            calls["timeout"] = kwargs["timeout"]

        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args):
            return False

        async def post(self, url, **kwargs):
            calls["url"] = url
            calls["headers"] = kwargs["headers"]
            calls["data"] = kwargs["data"]
            calls["files"] = kwargs["files"]
            return FakeResponse()

    monkeypatch.setattr("core.stt.httpx.AsyncClient", FakeClient)

    result = await ElevenLabsSTT.transcribe_file(audio, "audio/ogg")

    assert result == "hello from WhatsApp"
    assert calls["headers"]["xi-api-key"] == "test-key"
    assert calls["data"] == {"model_id": "scribe_v2"}
    assert calls["files"]["file"][0] == "voice.ogg"


@pytest.mark.asyncio
async def test_stt_rejects_oversized_audio(monkeypatch, tmp_path):
    from core.stt import ElevenLabsSTT

    monkeypatch.setenv("ELEVENLABS_API_KEY", "test-key")
    audio = tmp_path / "large.ogg"
    with audio.open("wb") as handle:
        handle.truncate(ElevenLabsSTT.MAX_AUDIO_BYTES + 1)

    with pytest.raises(ValueError, match="15 MB"):
        await ElevenLabsSTT.transcribe_file(audio)


@pytest.mark.parametrize(
    ("status", "detail", "expected"),
    [
        (401, '{"status":"missing_permissions"}', "Speech to Text permission"),
        (401, '{"status":"invalid_api_key"}', "rejected the API key"),
        (429, "slow down", "rate limiting"),
        (500, "boom", "Check ElevenLabs voice input"),
    ],
)
def test_stt_error_user_message(status, detail, expected):
    from core.stt import ElevenLabsSTTError

    error = ElevenLabsSTTError(status, detail)

    assert expected in error.user_message
    assert str(status) in str(error)


def test_speech_cleaning_drops_links_emoji_code_and_markup():
    from core.tts import ElevenLabsTTS

    text = (
        "## Plan\n- **Search** the [docs](https://example.com/a) now\n"
        "- see https://example.com/long/url?x=1 \U0001F34B\n```py\nprint(1)\n```\n"
        "| a | b |\n1. Done - really."
    )
    spoken = ElevenLabsTTS.clean_text_for_speech(text)

    assert spoken == "Plan Search the docs now see Done - really."


def test_speech_cleaning_truncates_at_sentence_boundary():
    from core.tts import ElevenLabsTTS

    text = "First sentence here. Second sentence is longer than the cap allows."

    assert ElevenLabsTTS.clean_text_for_speech(text, max_chars=36) == "First sentence here."
    assert ElevenLabsTTS.clean_text_for_speech(text) == text
