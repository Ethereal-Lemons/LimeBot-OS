"""Small, conservative redaction helpers for operational output.

Tool arguments and shell commands may contain credentials even when the tool
itself is approved.  Keep the model's private tool history intact, but redact
known credential-shaped values before writing logs, metrics, or UI events.
"""

from __future__ import annotations

import re
from typing import Any


_FLAG_VALUE_RE = re.compile(
    r"(?i)(--?(?:token|api[-_]?key|secret|password|passwd|authorization|auth)\s+)([^\s'\";&|]+)"
)
_KEY_VALUE_RE = re.compile(
    r"(?i)(\b(?:wstoken|access[_-]?token|refresh[_-]?token|api[_-]?key|client[_-]?secret|password|authorization)\s*[:=]\s*)([^\s,}\]]+)"
)
_BEARER_RE = re.compile(r"(?i)(\bBearer\s+)[A-Za-z0-9._~+/=-]+")
_ENV_ASSIGNMENT_RE = re.compile(
    r"(?i)(\b(?:[A-Z][A-Z0-9_]*(?:TOKEN|SECRET|PASSWORD|API[_-]?KEY))\s*=\s*)([^\s;&|]+)"
)
_SECRET_KEY_RE = re.compile(
    r"(?i)(?:token|secret|password|passwd|authorization|api[_-]?key|access[_-]?token|refresh[_-]?token)"
)


def redact_sensitive_text(value: Any) -> str:
    """Return *value* as text with common credential values replaced."""

    text = str(value)
    text = _FLAG_VALUE_RE.sub(r"\1[REDACTED]", text)
    text = _KEY_VALUE_RE.sub(r"\1[REDACTED]", text)
    text = _BEARER_RE.sub(r"\1[REDACTED]", text)
    text = _ENV_ASSIGNMENT_RE.sub(r"\1[REDACTED]", text)
    return text


def redact_sensitive_value(value: Any) -> Any:
    """Recursively redact strings in mappings and sequences for event payloads."""

    if isinstance(value, dict):
        return {
            key: "[REDACTED]"
            if _SECRET_KEY_RE.search(str(key))
            else redact_sensitive_value(item)
            for key, item in value.items()
        }
    if isinstance(value, list):
        return [redact_sensitive_value(item) for item in value]
    if isinstance(value, tuple):
        return tuple(redact_sensitive_value(item) for item in value)
    if isinstance(value, str):
        return redact_sensitive_text(value)
    return value
