"""Caption selection, VTT parsing, and transcript budgeting.

Substantially adapted from bradautomates/claude-video at revision
83da59fa78c3eee9e20f515fe75c438bb5166efd (MIT).
"""

from __future__ import annotations

import html
import re
from dataclasses import dataclass

from .constants import MAX_TRANSCRIPT_CHARS
from .time_utils import format_timestamp


@dataclass(frozen=True)
class Segment:
    start: float
    end: float
    text: str


def choose_caption(info: dict) -> tuple[str | None, dict | None, bool]:
    declared = str(info.get("language") or "").lower()
    declared_base = declared.split("-", 1)[0]
    for automatic, collection_name in ((False, "subtitles"), (True, "automatic_captions")):
        collection = info.get(collection_name) or {}
        if not collection:
            continue
        keys = list(collection)
        ordered = []
        if declared:
            ordered.extend(key for key in keys if key.lower() == declared or key.lower().startswith(declared + "-"))
            ordered.extend(
                key for key in keys
                if key.lower() == declared_base or key.lower().startswith(declared_base + "-")
            )
        ordered.extend(key for key in keys if key.lower() == "en" or key.lower().startswith("en-"))
        ordered.extend(keys)
        for language in dict.fromkeys(ordered):
            tracks = collection.get(language) or []
            vtt = next((track for track in tracks if str(track.get("ext", "")).lower() == "vtt"), None)
            if vtt is not None:
                return language, vtt, automatic
    return None, None, False


_TIMING = re.compile(
    r"(?P<start>\d{2}:\d{2}:\d{2}[.,]\d{3})\s+-->\s+"
    r"(?P<end>\d{2}:\d{2}:\d{2}[.,]\d{3})"
)


def _vtt_seconds(value: str) -> float:
    hours, minutes, seconds = value.replace(",", ".").split(":")
    return int(hours) * 3600 + int(minutes) * 60 + float(seconds)


def parse_vtt(content: str) -> list[Segment]:
    blocks = re.split(r"\r?\n\s*\r?\n", content.lstrip("\ufeff"))
    result: list[Segment] = []
    last_text = ""
    for block in blocks:
        lines = [line.strip() for line in block.splitlines() if line.strip()]
        timing_index = next((i for i, line in enumerate(lines) if _TIMING.search(line)), None)
        if timing_index is None:
            continue
        match = _TIMING.search(lines[timing_index])
        if match is None:
            continue
        text = " ".join(lines[timing_index + 1 :])
        text = re.sub(r"<[^>]+>", "", html.unescape(text))
        text = re.sub(r"\s+", " ", text).strip()
        if not text:
            continue
        # Auto-caption tracks often repeat the previous cue verbatim or as a prefix.
        if text == last_text:
            continue
        if last_text and text.startswith(last_text):
            text = text[len(last_text) :].strip()
        if not text:
            continue
        result.append(Segment(_vtt_seconds(match["start"]), _vtt_seconds(match["end"]), text))
        last_text = " ".join(lines[timing_index + 1 :])
        last_text = re.sub(r"<[^>]+>", "", html.unescape(last_text))
        last_text = re.sub(r"\s+", " ", last_text).strip()
    return result


def segments_in_range(segments: list[Segment], start: float, end: float) -> list[Segment]:
    return [segment for segment in segments if segment.end >= start and segment.start <= end]


def render_transcript(segments: list[Segment], question: str = "") -> tuple[str, bool]:
    lines = [f"[{format_timestamp(segment.start)}] {segment.text}" for segment in segments]
    full = "\n".join(lines)
    if len(full) <= MAX_TRANSCRIPT_CHARS:
        return full, False

    query_words = {
        word.lower() for word in re.findall(r"[A-Za-z0-9]{3,}", question)
    }
    selected: dict[int, str] = {}
    coverage = max(1, len(lines) // 12)
    for index in range(0, len(lines), coverage):
        selected[index] = lines[index]
    for index, line in enumerate(lines):
        lower = line.lower()
        if query_words and any(word in lower for word in query_words):
            selected[index] = line
    selected[0] = lines[0]
    selected[len(lines) - 1] = lines[-1]
    ordered = [selected[index] for index in sorted(selected)]
    text = "\n".join(ordered)
    if len(text) > MAX_TRANSCRIPT_CHARS:
        text = text[: MAX_TRANSCRIPT_CHARS - 1].rstrip() + "…"
    return text, True
