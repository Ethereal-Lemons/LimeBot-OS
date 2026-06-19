"""
Helpers for parsing slash-based skill invocation from user messages.
"""

from dataclasses import dataclass
import re


_SKILL_NAME_PATTERN = r"[A-Za-z0-9_-]+"


@dataclass(frozen=True)
class SkillInvocation:
    kind: str = "none"
    requested_name: str = ""
    task: str = ""
    raw: str = ""


def parse_skill_invocation(text: str) -> SkillInvocation:
    raw = str(text or "")
    stripped = raw.strip()
    if not stripped.startswith("/"):
        return SkillInvocation(raw=raw)

    inventory_match = re.fullmatch(r"/skills\s*", stripped, flags=re.IGNORECASE)
    if inventory_match:
        return SkillInvocation(kind="inventory", raw=raw)

    verbose_match = re.fullmatch(
        rf"/skill\s+({_SKILL_NAME_PATTERN})(?:\s+(.*\S))?\s*",
        stripped,
        flags=re.IGNORECASE,
    )
    if verbose_match:
        requested_name = verbose_match.group(1) or ""
        task = verbose_match.group(2) or ""
        if requested_name.lower() == "list" and not task:
            return SkillInvocation(kind="inventory", raw=raw)
        return SkillInvocation(
            kind="skill",
            requested_name=requested_name,
            task=task.strip(),
            raw=raw,
        )

    shorthand_match = re.fullmatch(
        rf"/({_SKILL_NAME_PATTERN})(?:\s+(.*\S))?\s*",
        stripped,
        flags=re.IGNORECASE,
    )
    if not shorthand_match:
        return SkillInvocation(raw=raw)

    requested_name = shorthand_match.group(1) or ""
    task = shorthand_match.group(2) or ""
    if requested_name.lower() == "skills" and not task:
        return SkillInvocation(kind="inventory", raw=raw)
    if requested_name.lower() == "skills":
        return SkillInvocation(raw=raw)

    return SkillInvocation(
        kind="skill",
        requested_name=requested_name,
        task=task.strip(),
        raw=raw,
    )
