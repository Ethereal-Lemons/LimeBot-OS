"""Native video-analysis orchestration.

Pipeline concepts are substantially adapted from bradautomates/claude-video at
revision 83da59fa78c3eee9e20f515fe75c438bb5166efd (MIT). LimeBot adds its own
egress proxy, opt-in transcription, native tool contract, and contact sheets.
"""

from __future__ import annotations

import asyncio
import importlib.util
import json
import os
import re
import shutil
import stat
import subprocess
import urllib.request
import uuid
from pathlib import Path
from urllib.parse import urlsplit

from .captions import Segment, choose_caption, parse_vtt, render_transcript, segments_in_range
from .constants import (
    INSTALL_COMMAND,
    JOB_RETENTION_SECONDS,
    MAX_DOWNLOAD_BYTES,
    MAX_DURATION_SECONDS,
    MAX_RESULT_CHARS,
    VIDEO_EXTENSIONS,
)
from .proxy import GuardedProxy
from .runtime import resolve_video_binary
from .time_utils import frame_budget, parse_timestamp
from .whisper import prepare_audio, transcribe

VIDEO_ROOT = Path.cwd() / "temp" / "video"


class _QuietYdlLogger:
    def debug(self, _message):
        pass

    def warning(self, _message):
        pass

    def error(self, _message):
        pass


class _ForcedLoopbackProxyHandler(urllib.request.ProxyHandler):
    """Use the supplied proxy even when ambient NO_PROXY matches the target."""

    handler_order = 100

    def __init__(self, proxy_url: str):
        self.proxy_url = proxy_url

    def http_open(self, request):
        return self._proxy_open(request)

    def https_open(self, request):
        return self._proxy_open(request)

    def _proxy_open(self, request):
        parsed = urlsplit(self.proxy_url)
        request.set_proxy(parsed.netloc, parsed.scheme)
        return None

def sweep_expired_jobs(now: float | None = None) -> None:
    import time

    cutoff = (now if now is not None else time.time()) - JOB_RETENTION_SECONDS
    if not VIDEO_ROOT.exists():
        return
    for child in VIDEO_ROOT.iterdir():
        try:
            if child.is_dir() and child.stat().st_mtime < cutoff:
                shutil.rmtree(child, ignore_errors=True)
        except OSError:
            continue


async def _delayed_cleanup(job_dir: Path) -> None:
    await asyncio.sleep(JOB_RETENTION_SECONDS)
    shutil.rmtree(job_dir, ignore_errors=True)


def _preflight_error() -> str | None:
    missing = []
    if importlib.util.find_spec("yt_dlp") is None:
        missing.append("yt-dlp")
    if importlib.util.find_spec("PIL") is None:
        missing.append("Pillow")
    if not _binary_ready("ffmpeg"):
        missing.append("ffmpeg")
    if not _binary_ready("ffprobe"):
        missing.append("ffprobe")
    if missing:
        return f"Video analysis is not installed ({', '.join(missing)} missing). Run: {INSTALL_COMMAND}"
    return None


def _binary_ready(name: str) -> bool:
    executable = resolve_video_binary(name)
    if not executable:
        return False
    try:
        result = subprocess.run(
            [executable, "-version"],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            timeout=5,
            check=False,
        )
        return result.returncode == 0
    except (OSError, subprocess.SubprocessError):
        return False


def _base_result(detail: str) -> dict:
    return {
        "status": "ok",
        "title": "",
        "duration_seconds": 0,
        "range": {"start_seconds": 0, "end_seconds": 0},
        "detail": detail,
        "transcript": {
            "source": "none",
            "language": None,
            "text": "",
            "truncated": False,
        },
        "visuals": {
            "candidate_count": 0,
            "deduplicated_count": 0,
            "included_frame_count": 0,
            "contact_sheets": [],
        },
        "warnings": [],
    }


def _error(message: str, detail: str = "balanced") -> str:
    result = _base_result(detail)
    result["status"] = "error"
    result["error"] = str(message)[:2_000]
    return json.dumps(result, ensure_ascii=False)


def _validate_remote_source(source: str) -> None:
    parsed = urlsplit(source)
    if parsed.scheme.lower() not in {"http", "https"} or not parsed.hostname:
        raise ValueError("source must be an allowed local video or a public HTTP(S) URL")
    if parsed.username or parsed.password:
        raise ValueError("embedded URL credentials are not allowed")


def _validate_info(info: dict) -> None:
    if info.get("_type") in {"playlist", "multi_video"} or info.get("entries") is not None:
        raise ValueError("playlists are not supported; provide one video URL")
    if info.get("is_live") or info.get("live_status") in {"is_live", "is_upcoming", "post_live"}:
        raise ValueError("livestreams are not supported")
    if str(info.get("availability") or "").lower() in {"private", "premium_only", "subscriber_only", "needs_auth"}:
        raise ValueError("authenticated or private media is not supported")
    duration = float(info.get("duration") or 0)
    if duration <= 0:
        raise ValueError("video duration could not be validated")
    if duration > MAX_DURATION_SECONDS:
        raise ValueError("video exceeds the two-hour duration limit")


def _ydl_options(proxy_url: str, output: Path, *, audio: bool = False) -> dict:
    return {
        "proxy": proxy_url,
        "noplaylist": True,
        "playlistend": 1,
        "quiet": True,
        "no_warnings": True,
        "logger": _QuietYdlLogger(),
        "socket_timeout": 30,
        "retries": 2,
        "fragment_retries": 2,
        "max_filesize": MAX_DOWNLOAD_BYTES,
        "outtmpl": str(output / "source.%(ext)s"),
        "format": "bestaudio/best" if audio else "best[height<=720]/best[height<=720][ext=mp4]",
        "cachedir": False,
        "usenetrc": False,
        "cookiefile": None,
        "external_downloader": {},
        "overwrites": True,
        "nopart": True,
    }


def _extract_remote_info(source: str, proxy_url: str, job_dir: Path) -> dict:
    import yt_dlp

    options = _ydl_options(proxy_url, job_dir)
    options["skip_download"] = True
    with yt_dlp.YoutubeDL(options) as ydl:
        info = ydl.extract_info(source, download=False)
    _validate_info(info)
    return info


def _download_remote(source: str, proxy_url: str, job_dir: Path, *, audio: bool = False) -> Path:
    import yt_dlp

    with yt_dlp.YoutubeDL(_ydl_options(proxy_url, job_dir, audio=audio)) as ydl:
        info = ydl.extract_info(source, download=True)
        _validate_info(info)
    candidates = [path for path in job_dir.glob("source.*") if path.is_file() and path.suffix != ".part"]
    if not candidates:
        raise ValueError("video download did not produce a media file")
    media = max(candidates, key=lambda path: path.stat().st_size)
    if media.stat().st_size > MAX_DOWNLOAD_BYTES:
        raise ValueError("video exceeds the 500 MiB download limit")
    return media


def _download_caption(url: str, proxy_url: str, target: Path) -> str:
    parsed = urlsplit(url)
    if parsed.scheme.lower() not in {"http", "https"} or parsed.username or parsed.password:
        raise ValueError("caption URL was not public HTTP(S)")
    opener = urllib.request.build_opener(
        _ForcedLoopbackProxyHandler(proxy_url)
    )
    request = urllib.request.Request(url, headers={"User-Agent": "LimeBot/1 video captions"})
    with opener.open(request, timeout=45) as response:
        content = response.read(10 * 1024 * 1024 + 1)
    if len(content) > 10 * 1024 * 1024:
        raise ValueError("caption track is unexpectedly large")
    target.write_bytes(content)
    return content.decode("utf-8", errors="replace")


def _prepare_job() -> Path:
    sweep_expired_jobs()
    VIDEO_ROOT.mkdir(parents=True, exist_ok=True)
    job = VIDEO_ROOT / uuid.uuid4().hex
    job.mkdir(mode=0o700)
    try:
        os.chmod(job, stat.S_IRUSR | stat.S_IWUSR | stat.S_IXUSR)
    except OSError:
        pass
    return job


def _finalize(result: dict) -> str:
    payload = json.dumps(result, ensure_ascii=False)
    if len(payload) <= MAX_RESULT_CHARS:
        return payload
    transcript = result["transcript"]["text"]
    while len(payload) > MAX_RESULT_CHARS and transcript:
        overflow = len(payload) - MAX_RESULT_CHARS + 64
        transcript = transcript[: max(0, len(transcript) - overflow)].rstrip()
        result["transcript"]["text"] = transcript + ("…" if transcript else "")
        result["transcript"]["truncated"] = True
        payload = json.dumps(result, ensure_ascii=False)
    if len(payload) > MAX_RESULT_CHARS:
        result["warnings"] = ["Tool result metadata was reduced to fit the 30,000-character limit."]
        result["title"] = str(result.get("title") or "")[:200]
        payload = json.dumps(result, ensure_ascii=False)
    return payload


async def analyze_video(
    source: str,
    question: str = "",
    detail: str = "balanced",
    start: str | None = None,
    end: str | None = None,
    max_frames: int | None = None,
    resolution: int = 512,
    *,
    is_path_allowed,
    whisper_enabled: bool = False,
    openai_api_key: str = "",
) -> str:
    detail = detail or "balanced"
    if detail not in {"transcript", "efficient", "balanced"}:
        return _error("detail must be transcript, efficient, or balanced", detail)
    if resolution not in {512, 1024}:
        return _error("resolution must be 512 or 1024", detail)
    if max_frames is not None and (not isinstance(max_frames, int) or not 1 <= max_frames <= 100):
        return _error("max_frames must be an integer from 1 to 100", detail)
    preflight = _preflight_error()
    if preflight:
        return _error(preflight, detail)

    # Pillow-backed modules remain lazy so core startup and the actionable
    # missing-feature response work without optional video dependencies.
    from .contact_sheets import create_contact_sheets
    from .frames import (
        candidate_timestamps,
        deduplicate_frames,
        extract_frames,
        probe_video,
        select_included_frames,
        uniform_timestamps,
    )

    job_dir = _prepare_job()
    cleanup_task = asyncio.create_task(_delayed_cleanup(job_dir))
    result = _base_result(detail)
    try:
        async with asyncio.timeout(595):
            source_text = str(source)
            parsed_source = urlsplit(source_text)
            windows_path = bool(re.match(r"^[A-Za-z]:[\\/]", source_text))
            if parsed_source.scheme and parsed_source.scheme.lower() not in {"http", "https"} and not windows_path:
                raise ValueError("only local video paths and public HTTP(S) URLs are supported")
            is_remote = source_text.lower().startswith(("http://", "https://"))
            local_candidate = Path(source).expanduser() if not is_remote else None
            is_local = local_candidate is not None
            proxy = None
            caption_segments: list[Segment] = []
            language = None
            media_path: Path | None = None
            if is_local:
                media_path = local_candidate.resolve()
                if not is_path_allowed(media_path):
                    raise ValueError("local video path is outside LimeBot's allowed paths")
                if not media_path.is_file() or media_path.suffix.lower() not in VIDEO_EXTENSIONS:
                    raise ValueError("local source must be a regular video file")
                if media_path.stat().st_size > MAX_DOWNLOAD_BYTES:
                    raise ValueError("local video exceeds the 500 MiB limit")
                metadata = await asyncio.to_thread(probe_video, media_path)
                duration = metadata["duration"]
                title = media_path.stem
                if duration > MAX_DURATION_SECONDS:
                    raise ValueError("video exceeds the two-hour duration limit")
            else:
                _validate_remote_source(source)
                proxy = GuardedProxy()
                await proxy.__aenter__()
                try:
                    info = await asyncio.to_thread(_extract_remote_info, source, proxy.url, job_dir)
                    duration = float(info["duration"])
                    title = str(info.get("title") or "Remote video")[:500]
                    language, track, _automatic = choose_caption(info)
                    if track:
                        vtt = await asyncio.to_thread(_download_caption, track["url"], proxy.url, job_dir / "captions.vtt")
                        caption_segments = parse_vtt(vtt)
                    if proxy.error:
                        raise proxy.error

                    if detail != "transcript" or not caption_segments:
                        media_path = await asyncio.to_thread(
                            _download_remote, source, proxy.url, job_dir, audio=(detail == "transcript")
                        )
                        if proxy.error:
                            raise proxy.error
                finally:
                    await proxy.__aexit__(None, None, None)

            range_start = parse_timestamp(start) or 0.0
            range_end = parse_timestamp(end) if end is not None else duration
            if range_end is None:
                range_end = duration
            if range_start < 0 or range_end <= range_start or range_end > duration + 0.5:
                raise ValueError("requested time range is outside the video duration")
            range_end = min(duration, range_end)
            result.update({
                "title": title,
                "duration_seconds": round(duration, 3),
                "range": {"start_seconds": round(range_start, 3), "end_seconds": round(range_end, 3)},
            })

            transcript_source = "none"
            if caption_segments:
                caption_segments = segments_in_range(caption_segments, range_start, range_end)
                transcript_source = "captions"
            elif whisper_enabled and openai_api_key and media_path is not None:
                chunks = await asyncio.to_thread(prepare_audio, media_path, job_dir)
                caption_segments, language = await transcribe(chunks, openai_api_key)
                caption_segments = segments_in_range(caption_segments, range_start, range_end)
                transcript_source = "openai_whisper"
            elif detail == "transcript":
                reason = "VIDEO_WHISPER_ENABLED=false" if not whisper_enabled else "OPENAI_API_KEY is missing"
                raise ValueError(f"no captions are available and transcription cannot run because {reason}")
            else:
                reason = "enable VIDEO_WHISPER_ENABLED to use OpenAI Whisper" if not whisper_enabled else "configure OPENAI_API_KEY to use Whisper"
                result["warnings"].append(f"No captions available; returning frames-only evidence. To transcribe, {reason}.")

            transcript_text, truncated = render_transcript(caption_segments, question)
            result["transcript"] = {
                "source": transcript_source,
                "language": language,
                "text": transcript_text,
                "truncated": truncated,
            }

            if detail == "transcript":
                if max_frames is not None or resolution != 512:
                    result["warnings"].append("Frame arguments are ignored in transcript mode.")
                return _finalize(result)

            if media_path is None:
                raise ValueError("video frames were requested but no local media was available")
            requested = max_frames if max_frames is not None else (50 if detail == "efficient" else 100)
            budget = frame_budget(range_end - range_start, start is not None or end is not None, requested)
            candidates = await asyncio.to_thread(
                candidate_timestamps, media_path, detail, range_start, range_end, budget
            )
            frames = await asyncio.to_thread(extract_frames, media_path, job_dir / "frames", candidates, resolution)
            deduplicated = await asyncio.to_thread(deduplicate_frames, frames)
            if budget >= 4 and len(deduplicated) < 4 and any(
                reason not in {"uniform", "range start", "range end"}
                for _timestamp, reason in candidates
            ):
                candidates = uniform_timestamps(range_start, range_end, budget)
                frames = await asyncio.to_thread(
                    extract_frames, media_path, job_dir / "frames", candidates, resolution
                )
                deduplicated = await asyncio.to_thread(deduplicate_frames, frames)
                result["warnings"].append("Sparse visual candidates were replaced with uniform coverage.")
            included = select_included_frames(deduplicated)
            sheets = await asyncio.to_thread(create_contact_sheets, included, job_dir, resolution)
            result["visuals"] = {
                "candidate_count": len(frames),
                "deduplicated_count": len(deduplicated),
                "included_frame_count": len(included),
                "contact_sheets": [
                    {
                        "path": path.relative_to(Path.cwd()).as_posix(),
                        "name": path.name,
                    }
                    for path in sheets
                ],
            }
            return _finalize(result)
    except asyncio.TimeoutError:
        cleanup_task.cancel()
        shutil.rmtree(job_dir, ignore_errors=True)
        return _error("video analysis exceeded the 10-minute timeout", detail)
    except Exception as exc:
        cleanup_task.cancel()
        shutil.rmtree(job_dir, ignore_errors=True)
        return _error(str(exc) or "video analysis failed", detail)
