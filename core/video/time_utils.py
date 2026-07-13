"""Timestamp parsing and frame-budget helpers.

Substantially adapted from bradautomates/claude-video at revision
83da59fa78c3eee9e20f515fe75c438bb5166efd (MIT).
"""

import re


def parse_timestamp(value: str | None) -> float | None:
    if value is None or str(value).strip() == "":
        return None
    raw = str(value).strip()
    if not re.fullmatch(r"\d+(?:\.\d+)?(?::\d+(?:\.\d+)?){0,2}", raw):
        raise ValueError("timestamps must use SS, MM:SS, or HH:MM:SS")
    parts = [float(part) for part in raw.split(":")]
    if len(parts) == 1:
        result = parts[0]
    elif len(parts) == 2:
        if parts[1] >= 60:
            raise ValueError("timestamp seconds must be below 60")
        result = parts[0] * 60 + parts[1]
    else:
        if parts[1] >= 60 or parts[2] >= 60:
            raise ValueError("timestamp minutes and seconds must be below 60")
        result = parts[0] * 3600 + parts[1] * 60 + parts[2]
    return result


def format_timestamp(seconds: float) -> str:
    value = max(0, int(round(seconds)))
    hours, remainder = divmod(value, 3600)
    minutes, secs = divmod(remainder, 60)
    return f"{hours:02d}:{minutes:02d}:{secs:02d}"


def frame_budget(duration: float, focused: bool, requested: int | None) -> int:
    if focused:
        budget = 10 if duration <= 5 else 30 if duration <= 15 else 60 if duration <= 30 else 80 if duration <= 60 else 100
    else:
        budget = 30 if duration <= 30 else 40 if duration <= 60 else 60 if duration <= 180 else 80 if duration <= 600 else 100
    if requested is not None:
        budget = min(budget, requested)
    return max(1, min(100, budget, max(1, int(duration * 2) + 1)))
