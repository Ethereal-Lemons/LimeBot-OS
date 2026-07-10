"""Speech-to-text integration for inbound voice messages."""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any, Dict

import httpx
from loguru import logger


LIMEBOT_CONFIG_PATH = Path("limebot.json")
ELEVENLABS_STT_URL = "https://api.elevenlabs.io/v1/speech-to-text"


class ElevenLabsSTT:
    """Transcribe audio files with ElevenLabs Scribe."""

    MAX_AUDIO_BYTES = 15 * 1024 * 1024
    DEFAULT_MODEL_ID = "scribe_v2"

    @staticmethod
    def get_api_key() -> str:
        return os.getenv("ELEVENLABS_API_KEY", "").strip()

    @classmethod
    def get_config(cls) -> Dict[str, Any]:
        """Read inbound voice settings without coupling them to outgoing TTS."""
        default = {
            "enabled": True,
            "provider": "elevenlabs",
            "model_id": cls.DEFAULT_MODEL_ID,
        }
        if not LIMEBOT_CONFIG_PATH.exists():
            return default
        try:
            with LIMEBOT_CONFIG_PATH.open("r", encoding="utf-8") as handle:
                data = json.load(handle)
            configured = data.get("voice_input", {})
            return {**default, **configured} if isinstance(configured, dict) else default
        except Exception as exc:
            logger.warning(f"[STT] Failed to read voice_input config: {exc}")
            return default

    @classmethod
    def is_enabled(cls) -> bool:
        config = cls.get_config()
        return (
            bool(config.get("enabled", True))
            and config.get("provider", "elevenlabs") == "elevenlabs"
            and bool(cls.get_api_key())
        )

    @classmethod
    async def transcribe_file(
        cls, file_path: str | Path, mimetype: str = "audio/ogg"
    ) -> str:
        """Transcribe one local audio file and return clean text.

        The caller owns cleanup of the temporary file. Raw audio is never logged.
        """
        api_key = cls.get_api_key()
        config = cls.get_config()
        if not config.get("enabled", True):
            raise RuntimeError("WhatsApp voice transcription is disabled")
        if config.get("provider", "elevenlabs") != "elevenlabs":
            raise RuntimeError("Unsupported voice transcription provider")
        if not api_key:
            raise RuntimeError("ELEVENLABS_API_KEY is not configured")

        path = Path(file_path).resolve()
        if not path.is_file():
            raise FileNotFoundError(f"Audio file does not exist: {path.name}")
        size = path.stat().st_size
        if size <= 0:
            raise ValueError("Audio file is empty")
        if size > cls.MAX_AUDIO_BYTES:
            raise ValueError("Audio file exceeds the 15 MB limit")

        model_id = str(config.get("model_id") or cls.DEFAULT_MODEL_ID)
        headers = {"xi-api-key": api_key, "Accept": "application/json"}
        try:
            with path.open("rb") as handle:
                files = {
                    "file": (path.name, handle, mimetype or "application/octet-stream")
                }
                data = {"model_id": model_id}
                async with httpx.AsyncClient(timeout=60.0) as client:
                    response = await client.post(
                        ELEVENLABS_STT_URL,
                        headers=headers,
                        data=data,
                        files=files,
                    )
        except httpx.HTTPError as exc:
            raise RuntimeError(f"ElevenLabs STT request failed: {exc}") from exc

        if response.status_code != 200:
            detail = response.text[:500]
            raise RuntimeError(f"ElevenLabs STT error ({response.status_code}): {detail}")

        try:
            payload = response.json()
        except ValueError as exc:
            raise RuntimeError("ElevenLabs STT returned invalid JSON") from exc

        text = payload.get("text") if isinstance(payload, dict) else None
        if not isinstance(text, str) or not text.strip():
            raise RuntimeError("ElevenLabs STT returned an empty transcript")
        return text.strip()
