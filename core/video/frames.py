"""FFmpeg-backed frame candidate extraction and visual deduplication.

Substantially adapted from bradautomates/claude-video at revision
83da59fa78c3eee9e20f515fe75c438bb5166efd (MIT).
"""

from __future__ import annotations

import json
import re
import subprocess
from dataclasses import dataclass
from pathlib import Path

from PIL import Image, ImageChops, ImageStat

from .constants import MIN_FRAME_DIFFERENCE
from .runtime import resolve_video_binary


@dataclass(frozen=True)
class Frame:
    path: Path
    timestamp: float
    reason: str


def _run(args: list[str], timeout: float = 120) -> subprocess.CompletedProcess:
    if args and args[0] in {"ffmpeg", "ffprobe"}:
        args = [resolve_video_binary(args[0]) or args[0], *args[1:]]
    return subprocess.run(args, capture_output=True, text=True, timeout=timeout, check=True)


def probe_video(path: Path) -> dict:
    result = _run([
        "ffprobe", "-v", "error", "-show_entries",
        "format=duration:stream=codec_type,width,height", "-of", "json", str(path),
    ])
    payload = json.loads(result.stdout)
    duration = float((payload.get("format") or {}).get("duration") or 0)
    stream = next((item for item in payload.get("streams", []) if item.get("codec_type") == "video"), None)
    if duration <= 0 or not stream:
        raise ValueError("source is not a readable video")
    return {"duration": duration, "width": int(stream.get("width") or 0), "height": int(stream.get("height") or 0)}


def _evenly_select(values: list, count: int) -> list:
    if len(values) <= count:
        return values
    if count == 1:
        return [values[0]]
    indexes = {round(index * (len(values) - 1) / (count - 1)) for index in range(count)}
    return [values[index] for index in sorted(indexes)]


def uniform_timestamps(start: float, end: float, count: int) -> list[tuple[float, str]]:
    if count <= 1 or end <= start:
        return [(start, "range start")]
    return [
        (start + index * (end - start) / (count - 1), "uniform")
        for index in range(count)
    ]


def keyframe_timestamps(path: Path, start: float, end: float, count: int) -> list[tuple[float, str]]:
    result = _run([
        "ffprobe", "-v", "error", "-skip_frame", "nokey", "-select_streams", "v:0",
        "-show_entries", "frame=best_effort_timestamp_time", "-of", "csv=p=0", str(path),
    ])
    values = []
    for line in result.stdout.splitlines():
        try:
            value = float(line.strip().split(",")[0])
        except ValueError:
            continue
        if start <= value <= end:
            values.append((value, "keyframe"))
    return _evenly_select(values, count)


def scene_timestamps(path: Path, start: float, end: float, count: int) -> list[tuple[float, str]]:
    duration = max(0.01, end - start)
    result = subprocess.run([
        resolve_video_binary("ffmpeg") or "ffmpeg", "-nostdin", "-hide_banner", "-ss", f"{start:.3f}", "-i", str(path),
        "-t", f"{duration:.3f}", "-vf", "select='gt(scene,0.30)',showinfo",
        "-an", "-f", "null", "-",
    ], capture_output=True, text=True, timeout=180, check=False)
    values = []
    for match in re.finditer(r"pts_time:(?P<time>[0-9.]+)", result.stderr):
        values.append((start + float(match["time"]), "scene change"))
    return _evenly_select(values, count)


def candidate_timestamps(path: Path, detail: str, start: float, end: float, count: int) -> list[tuple[float, str]]:
    values = keyframe_timestamps(path, start, end, count) if detail == "efficient" else scene_timestamps(path, start, end, count)
    if len(values) < 4:
        values = uniform_timestamps(start, end, count)
    values.extend([(start, "range start"), (max(start, end - 0.05), "range end")])
    unique = {round(timestamp, 3): (timestamp, reason) for timestamp, reason in values}
    return _evenly_select([unique[key] for key in sorted(unique)], count)


def extract_frames(path: Path, output_dir: Path, candidates: list[tuple[float, str]], resolution: int) -> list[Frame]:
    output_dir.mkdir(parents=True, exist_ok=True)
    frames = []
    for index, (timestamp, reason) in enumerate(candidates):
        target = output_dir / f"frame-{index:03d}.jpg"
        _run([
            "ffmpeg", "-nostdin", "-hide_banner", "-loglevel", "error", "-ss", f"{timestamp:.3f}",
            "-i", str(path), "-frames:v", "1", "-vf",
            f"scale={resolution}:-2:force_original_aspect_ratio=decrease", "-q:v", "3", "-y", str(target),
        ], timeout=60)
        if target.is_file():
            frames.append(Frame(target, timestamp, reason))
    return frames


def frame_difference(left: Path, right: Path) -> float:
    with Image.open(left) as first, Image.open(right) as second:
        first = first.convert("L").resize((16, 16))
        second = second.convert("L").resize((16, 16))
        return float(ImageStat.Stat(ImageChops.difference(first, second)).mean[0])


def deduplicate_frames(frames: list[Frame], threshold: float = MIN_FRAME_DIFFERENCE) -> list[Frame]:
    kept: list[Frame] = []
    for frame in frames:
        if not kept or frame_difference(kept[-1].path, frame.path) >= threshold:
            kept.append(frame)
    if len(frames) > 1 and kept[-1].path != frames[-1].path:
        kept.append(frames[-1])
    return kept


def select_included_frames(frames: list[Frame], maximum: int = 48) -> list[Frame]:
    return _evenly_select(frames, maximum)
