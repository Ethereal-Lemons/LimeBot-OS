"""Explicitly opted-in OpenAI Whisper fallback."""

from __future__ import annotations

import asyncio
import subprocess
from pathlib import Path

import httpx

from .captions import Segment
from .runtime import resolve_video_binary

MAX_AUDIO_CHUNK_BYTES = 24 * 1024 * 1024


def prepare_audio(source: Path, output_dir: Path) -> list[tuple[Path, float]]:
    audio = output_dir / "audio.mp3"
    subprocess.run([
        resolve_video_binary("ffmpeg") or "ffmpeg", "-nostdin", "-hide_banner", "-loglevel", "error", "-i", str(source),
        "-vn", "-ac", "1", "-ar", "16000", "-b:a", "64k", "-y", str(audio),
    ], capture_output=True, timeout=240, check=True)
    if audio.stat().st_size < MAX_AUDIO_CHUNK_BYTES:
        return [(audio, 0.0)]
    audio.unlink(missing_ok=True)
    pattern = output_dir / "audio-%03d.mp3"
    subprocess.run([
        resolve_video_binary("ffmpeg") or "ffmpeg", "-nostdin", "-hide_banner", "-loglevel", "error", "-i", str(source),
        "-vn", "-ac", "1", "-ar", "16000", "-b:a", "64k", "-f", "segment",
        "-segment_time", "2700", "-reset_timestamps", "1", "-y", str(pattern),
    ], capture_output=True, timeout=300, check=True)
    chunks = sorted(output_dir.glob("audio-*.mp3"))
    if any(chunk.stat().st_size >= MAX_AUDIO_CHUNK_BYTES for chunk in chunks):
        raise ValueError("audio chunk remained above the 24 MiB Whisper limit")
    return [(chunk, index * 2700.0) for index, chunk in enumerate(chunks)]


async def _transcribe_chunk(client: httpx.AsyncClient, path: Path, offset: float, api_key: str) -> tuple[list[Segment], str | None]:
    for attempt in range(3):
        try:
            with path.open("rb") as stream:
                response = await client.post(
                    "https://api.openai.com/v1/audio/transcriptions",
                    headers={"Authorization": f"Bearer {api_key}"},
                    data={
                        "model": "whisper-1",
                        "response_format": "verbose_json",
                        "timestamp_granularities[]": "segment",
                    },
                    files={"file": (path.name, stream, "audio/mpeg")},
                )
        except httpx.HTTPError as exc:
            if attempt == 2:
                raise RuntimeError("OpenAI transcription request failed") from exc
            await asyncio.sleep(2**attempt)
            continue
        if response.status_code < 400:
            payload = response.json()
            segments = [
                Segment(
                    offset + float(item.get("start") or 0),
                    offset + float(item.get("end") or item.get("start") or 0),
                    str(item.get("text") or "").strip(),
                )
                for item in payload.get("segments") or []
                if str(item.get("text") or "").strip()
            ]
            if not segments and str(payload.get("text") or "").strip():
                segments = [Segment(offset, offset, str(payload["text"]).strip())]
            return segments, payload.get("language")
        if response.status_code != 429 and response.status_code < 500:
            raise RuntimeError(f"OpenAI transcription rejected the request (HTTP {response.status_code})")
        if attempt == 2:
            raise RuntimeError(f"OpenAI transcription was unavailable (HTTP {response.status_code})")
        retry_after = response.headers.get("Retry-After")
        try:
            delay = float(retry_after) if retry_after else 2**attempt
        except ValueError:
            delay = 2**attempt
        await asyncio.sleep(min(delay, 30))
    raise RuntimeError("OpenAI transcription failed")


async def transcribe(chunks: list[tuple[Path, float]], api_key: str) -> tuple[list[Segment], str | None]:
    segments: list[Segment] = []
    language = None
    async with httpx.AsyncClient(timeout=httpx.Timeout(120.0, connect=20.0)) as client:
        for path, offset in chunks:
            part, detected = await _transcribe_chunk(client, path, offset, api_key)
            segments.extend(part)
            language = language or detected
    return segments, language
