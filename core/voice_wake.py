"""Decide whether a voice transcript is addressed to the assistant by name."""

from __future__ import annotations

import difflib
import re
import unicodedata
from dataclasses import dataclass
from typing import Any, Iterable

_NAME_SEPARATORS = re.compile(r"[,;\n]+")
_TOKEN = re.compile(r"[^\W_]+", re.UNICODE)
_LEADING_WORDS = 6
_TRAILING_EXTRA_WORDS = 1
_FUZZY_MIN_LENGTH = 4
_FUZZY_RATIO = 0.7
DEFAULT_FOLLOW_UP_SECONDS = 0.0


@dataclass(frozen=True)
class ListeningSettings:
    """How a voice session decides which speech is meant for the assistant."""

    mode: str = "wake"  # "wake": only when addressed by name; "always": everything
    names: tuple[str, ...] = ("LimeBot",)
    follow_up_seconds: float = DEFAULT_FOLLOW_UP_SECONDS


def _fold(text: str) -> str:
    decomposed = unicodedata.normalize("NFKD", str(text or "").casefold())
    return "".join(char for char in decomposed if not unicodedata.combining(char))


def _tokens(text: str) -> list[str]:
    return _TOKEN.findall(_fold(text))


def parse_voice_names(value: Any) -> tuple[str, ...]:
    """Split a comma-separated "Voice Name" setting into clean names."""
    if isinstance(value, str):
        parts: Iterable[str] = _NAME_SEPARATORS.split(value)
    elif isinstance(value, (list, tuple)):
        parts = (str(item) for item in value)
    else:
        parts = ()
    names: list[str] = []
    for part in parts:
        name = " ".join(str(part).split())[:40]
        if name and name not in names:
            names.append(name)
    return tuple(names[:8])


def listening_settings_from_config(
    voice_config: dict[str, Any], identity_name: str = ""
) -> ListeningSettings:
    """Build settings from the Voice tab config; fall back to the persona name."""
    mode = str(voice_config.get("listen_mode") or "wake").strip().lower()
    if mode not in {"wake", "always"}:
        mode = "wake"
    names = parse_voice_names(voice_config.get("voice_name"))
    if not names:
        names = parse_voice_names(identity_name) or ("LimeBot",)
    try:
        follow_up = float(
            voice_config.get("voice_follow_up_seconds", DEFAULT_FOLLOW_UP_SECONDS)
        )
    except (TypeError, ValueError):
        follow_up = DEFAULT_FOLLOW_UP_SECONDS
    follow_up = min(max(follow_up, 0.0), 120.0)
    return ListeningSettings(mode=mode, names=names, follow_up_seconds=follow_up)


def _token_matches(token: str, name_token: str) -> bool:
    if token == name_token:
        return True
    if len(name_token) < _FUZZY_MIN_LENGTH or len(token) < _FUZZY_MIN_LENGTH:
        return False
    # Speech-to-text often spells a name differently ("Jenny" for "Jennie").
    return difflib.SequenceMatcher(None, token, name_token).ratio() >= _FUZZY_RATIO


def _window_matches(window: list[str], name_tokens: list[str]) -> bool:
    size = len(name_tokens)
    for start in range(0, len(window) - size + 1):
        if all(
            _token_matches(window[start + offset], name_tokens[offset])
            for offset in range(size)
        ):
            return True
    return False


def is_addressed_to(text: str, names: Iterable[str]) -> bool:
    """True when a name appears at the start or end of the utterance.

    Only the first and last few words count, so a friend mentioning the name in
    the middle of a sentence to someone else does not wake the assistant.
    """
    words = _tokens(text)
    if not words:
        return False
    leading = words[:_LEADING_WORDS]
    for name in names:
        name_tokens = _tokens(name)
        if not name_tokens:
            continue
        trailing = words[-(len(name_tokens) + _TRAILING_EXTRA_WORDS) :]
        if _window_matches(leading, name_tokens) or _window_matches(
            trailing, name_tokens
        ):
            return True
    return False
