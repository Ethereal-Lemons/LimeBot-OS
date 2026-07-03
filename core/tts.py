"""ElevenLabs Text-to-Speech integration."""

import os
import json
import httpx
from typing import Dict, Any, List
from loguru import logger
from pathlib import Path

# Paths
LIMEBOT_CONFIG_PATH = Path("limebot.json")
TEMP_DIR = Path("temp")

# Filename prefix used by every synthesis call site (synthesize_and_save /
# synthesize_to_file both default to this) — also doubles as the glob pattern
# the stale-file reaper uses to only ever touch files it created.
_VOICE_FILE_PREFIX = "voice"


class ElevenLabsTTS:
    """Handles communications with ElevenLabs API for Voice List and Text-to-Speech synthesis."""

    # ElevenLabs rejects very long requests (HTTP 400) and long replies cost
    # disproportionately more; cap and truncate at a clean boundary instead.
    MAX_TTS_CHARS = 2500

    @staticmethod
    def get_api_key() -> str:
        """Fetch the ElevenLabs API Key from environment variables."""
        return os.getenv("ELEVENLABS_API_KEY", "").strip()

    @classmethod
    def get_voice_config(cls) -> Dict[str, Any]:
        """Read voice settings from limebot.json."""
        default_config = {
            "enabled": False,
            "voice_id": "21m00Tcm4TlvDq8ikWAM",  # Rachel (Default)
            "stability": 0.5,
            "similarity_boost": 0.75,
            "style": 0.0,
            "use_speaker_boost": True,
            "speed": 1.0,
            "model_id": "eleven_multilingual_v2",
            "output_format": "mp3_44100_128",
            "channels": ["web"],
            "send_text_with_audio": False,
        }

        if LIMEBOT_CONFIG_PATH.exists():
            try:
                with open(LIMEBOT_CONFIG_PATH, "r", encoding="utf-8") as f:
                    data = json.load(f)
                    return data.get("voice", default_config)
            except Exception as e:
                logger.error(f"[TTS] Error reading limebot.json: {e}")

        return default_config

    @classmethod
    def save_voice_config(cls, voice_config: Dict[str, Any]) -> None:
        """Write voice settings back to limebot.json, preserving other fields.

        Uses a write-to-temp-then-replace pattern so a crash or concurrent
        write mid-save can't leave limebot.json truncated/corrupted.
        """
        data = {}
        if LIMEBOT_CONFIG_PATH.exists():
            try:
                with open(LIMEBOT_CONFIG_PATH, "r", encoding="utf-8") as f:
                    data = json.load(f)
            except Exception as e:
                logger.error(f"[TTS] Error reading limebot.json before save: {e}")

        data["voice"] = voice_config

        tmp_path = LIMEBOT_CONFIG_PATH.with_suffix(".json.tmp")
        try:
            with open(tmp_path, "w", encoding="utf-8") as f:
                json.dump(data, f, indent=2)
            tmp_path.replace(LIMEBOT_CONFIG_PATH)
            logger.info("[TTS] Saved voice configuration successfully.")
        except Exception as e:
            logger.error(f"[TTS] Failed to save voice config: {e}")
        finally:
            if tmp_path.exists():
                try:
                    tmp_path.unlink()
                except OSError:
                    pass

    @classmethod
    async def list_voices(cls) -> List[Dict[str, Any]]:
        """Fetch available custom and premade voices from ElevenLabs."""
        api_key = cls.get_api_key()
        if not api_key:
            logger.warning("[TTS] ElevenLabs API Key is not configured.")
            return []

        headers = {
            "xi-api-key": api_key,
            "Accept": "application/json"
        }

        async with httpx.AsyncClient(timeout=15.0) as client:
            try:
                response = await client.get("https://api.elevenlabs.io/v1/voices", headers=headers)
                if response.status_code != 200:
                    logger.error(f"[TTS] ElevenLabs list voices error ({response.status_code}): {response.text}")
                    return []
                
                res_data = response.json()
                return res_data.get("voices", [])
            except Exception as e:
                logger.error(f"[TTS] Failed to connect to ElevenLabs voices API: {e}")
                return []

    @classmethod
    async def synthesize_text(cls, text: str, voice_id: str = None, settings: Dict[str, Any] = None) -> bytes:
        """
        Synthesize text into speech using ElevenLabs API.
        Returns the raw MP3 audio bytes.
        """
        api_key = cls.get_api_key()
        if not api_key:
            raise ValueError("ElevenLabs API Key is not configured in .env.")

        if len(text) > cls.MAX_TTS_CHARS:
            logger.warning(
                f"[TTS] Text is {len(text)} chars, over the {cls.MAX_TTS_CHARS}-char cap; "
                "truncating to avoid an ElevenLabs request failure."
            )
            text = cls._truncate_for_speech(text, cls.MAX_TTS_CHARS)

        cfg = cls.get_voice_config()
        active_voice_id = voice_id or cfg.get("voice_id")
        
        # Merge settings
        merged_settings = {
            "stability": cfg.get("stability", 0.5),
            "similarity_boost": cfg.get("similarity_boost", 0.75),
            "style": cfg.get("style", 0.0),
            "use_speaker_boost": cfg.get("use_speaker_boost", True),
            "speed": cfg.get("speed", 1.0)
        }
        
        active_model_id = cfg.get("model_id", "eleven_multilingual_v2")
        active_output_format = cfg.get("output_format", "mp3_44100_128")
        
        if settings:
            if "model_id" in settings:
                active_model_id = settings.get("model_id") or active_model_id
            if "output_format" in settings:
                active_output_format = settings.get("output_format") or active_output_format
                
            cleaned_settings = {
                k: v for k, v in settings.items() 
                if k not in ["model_id", "output_format"] and v is not None
            }
            merged_settings.update(cleaned_settings)

        payload = {
            "text": text,
            "model_id": active_model_id,
            "voice_settings": merged_settings
        }

        headers = {
            "xi-api-key": api_key,
            "Content-Type": "application/json",
            "Accept": "audio/mpeg"
        }

        url = f"https://api.elevenlabs.io/v1/text-to-speech/{active_voice_id}?output_format={active_output_format}"

        async with httpx.AsyncClient(timeout=30.0) as client:
            try:
                logger.info(f"[TTS] Sending TTS request to ElevenLabs using voice {active_voice_id}, model {active_model_id}, format {active_output_format}")
                response = await client.post(url, json=payload, headers=headers)
                if response.status_code != 200:
                    error_msg = f"ElevenLabs TTS error ({response.status_code}): {response.text}"
                    logger.error(f"[TTS] {error_msg}")
                    raise RuntimeError(error_msg)
                
                return response.content
            except httpx.HTTPError as e:
                logger.error(f"[TTS] HTTP request failed: {e}")
                raise RuntimeError(f"HTTP request failed: {e}")
            except Exception as e:
                logger.error(f"[TTS] TTS synthesis failed: {e}")
                raise

    @classmethod
    async def synthesize_and_save(cls, text: str, filename_prefix: str = "voice") -> str:
        """
        Synthesize text, save it as an MP3 file in temp/ folder, and return the web-accessible URL.
        """
        import uuid
        
        # Ensure temp directory exists
        TEMP_DIR.mkdir(parents=True, exist_ok=True)
        
        # Remove any markdown formatting / tags to make the speech sound clean
        clean_text = cls.clean_text_for_speech(text)
        if not clean_text:
            return ""

        try:
            audio_bytes = await cls.synthesize_text(clean_text)
            
            filename = f"{filename_prefix}_{uuid.uuid4().hex[:8]}.mp3"
            filepath = TEMP_DIR / filename
            
            with open(filepath, "wb") as f:
                f.write(audio_bytes)
                
            logger.info(f"[TTS] Audio saved to {filepath}")
            return f"/temp/{filename}"
        except Exception as e:
            logger.error(f"[TTS] Failed to synthesize and save: {e}")
            return ""

    @classmethod
    async def synthesize_to_file(cls, text: str, filename_prefix: str = "voice") -> str:
        """Synthesize `text` to an mp3 under temp/ and return the LOCAL FILE PATH.

        Unlike synthesize_and_save (which returns a /temp/ URL for the web UI),
        this returns an absolute filesystem path suitable for Discord/WhatsApp
        file sends. Returns "" on failure or empty text.
        """
        import uuid

        TEMP_DIR.mkdir(parents=True, exist_ok=True)
        clean_text = cls.clean_text_for_speech(text)
        if not clean_text:
            return ""
        try:
            audio_bytes = await cls.synthesize_text(clean_text)
            filepath = TEMP_DIR / f"{filename_prefix}_{uuid.uuid4().hex[:8]}.mp3"
            with open(filepath, "wb") as f:
                f.write(audio_bytes)
            logger.info(f"[TTS] Voice file saved to {filepath}")
            return str(filepath.resolve())
        except Exception as e:
            logger.error(f"[TTS] synthesize_to_file failed: {e}")
            return ""

    @staticmethod
    def _truncate_for_speech(text: str, limit: int) -> str:
        """Truncate `text` to `limit` chars at a clean sentence/word boundary."""
        if len(text) <= limit:
            return text
        truncated = text[:limit]
        for boundary in (". ", "! ", "? "):
            idx = truncated.rfind(boundary)
            if idx > limit * 0.5:
                return truncated[: idx + 1].strip()
        idx = truncated.rfind(" ")
        if idx > limit * 0.5:
            return truncated[:idx].strip()
        return truncated.strip()

    @classmethod
    def purge_stale_audio(cls, max_age_hours: float = 2.0) -> int:
        """Delete stray synthesized mp3s under temp/ older than `max_age_hours`.

        Discord/WhatsApp voice sends clean up after themselves immediately
        (`cleanup_file=True`), but the web channel's synthesize_and_save()
        leaves a /temp/ URL for the browser to stream, with no signal for
        when playback is done. This is the safety net for those (and for any
        cleanup_file send that failed to delete itself).
        """
        import time as _time

        if not TEMP_DIR.exists():
            return 0

        cutoff = _time.time() - (max_age_hours * 3600)
        deleted = 0
        try:
            for path in TEMP_DIR.glob(f"{_VOICE_FILE_PREFIX}_*.mp3"):
                try:
                    if path.is_file() and path.stat().st_mtime < cutoff:
                        path.unlink()
                        deleted += 1
                except OSError as e:
                    logger.debug(f"[TTS] Could not purge stale audio {path}: {e}")
        except Exception as e:
            logger.error(f"[TTS] Stale audio purge failed: {e}")

        if deleted:
            logger.info(f"[TTS] Purged {deleted} stale temp voice file(s).")
        return deleted

    @staticmethod
    def clean_text_for_speech(text: str) -> str:
        """Strip markdown markers and system XML tags to prepare text for high-quality speech."""
        import re
        
        if not text:
            return ""

        # Remove XML tags (e.g. <save_soul>...</save_soul>, <log_memory>...)
        text = re.sub(r"<[^>]+>[^<]*</[^>]+>", "", text)
        text = re.sub(r"<[^>]+>", "", text)
        
        # Remove markdown symbols
        text = re.sub(r"\*\*([^*]+)\*\*", r"\1", text)  # bold
        text = re.sub(r"\*([^*]+)\*", r"\1", text)      # italic
        text = re.sub(r"`([^`]+)`", r"\1", text)        # code blocks/inline
        text = re.sub(r"\[([^\]]+)\]\([^)]+\)", r"\1", text) # markdown links
        text = re.sub(r"#{1,6}\s+", "", text)           # headers
        text = re.sub(r"-\s+", "", text)                # bullets
        
        # Clean extra spaces
        text = re.sub(r"\s+", " ", text).strip()
        return text
