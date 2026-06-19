"""Turn-scoped prompt modes for LimeBot."""

from __future__ import annotations

PONYTAIL_MODES = frozenset({"off", "lite", "full", "ultra"})


def normalize_ponytail_mode(value: object) -> str:
    if not isinstance(value, str):
        return "off"
    raw = value.strip().lower()
    return raw if raw in PONYTAIL_MODES else "off"


def build_ponytail_prompt_addition(mode: object) -> str:
    normalized = normalize_ponytail_mode(mode)
    if normalized == "off":
        return ""

    mode_notes = {
        "lite": (
            "Before implementing, do a brief Ponytail pass to see whether the "
            "request can be skipped, simplified, or solved with less code."
        ),
        "full": (
            "Use Ponytail mode as your default posture for this turn: prefer "
            "the smallest correct solution and avoid writing code that does not "
            "need to exist."
        ),
        "ultra": (
            "Be aggressive about finding a no-op, deletion, native-platform, or "
            "one-line solution before adding new code."
        ),
    }

    return "\n".join(
        [
            f"### PONYTAIL MODE ({normalized})",
            mode_notes[normalized],
            "Work down this ladder in order:",
            "1. Does this need to exist? If no, skip it.",
            "2. If the standard library already solves it, use that.",
            "3. If the native platform already solves it, use that.",
            "4. If an already-installed dependency solves it, reuse that.",
            "5. If a one-line solution is enough, keep it to one line.",
            "6. Only then write the minimum new code that works.",
            "Lazy is not negligent. Do not shortcut trust-boundary validation, "
            "security, data-loss handling, accessibility, or tests.",
            "If you deliberately choose a temporary shortcut with a clear future "
            "upgrade path, you may add one short code comment that starts with "
            "'ponytail:' to mark that tradeoff.",
        ]
    )
