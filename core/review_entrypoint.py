"""Constrained, review-only unified diff processing for CI and local use."""

from __future__ import annotations

import asyncio
import json
import os
import re
import shlex
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Optional


DEFAULT_MAX_DIFF_BYTES = 80 * 1024
DEFAULT_MAX_INPUT_BYTES = 2 * 1024 * 1024
MAX_FILES = 500
MAX_HUNKS_PER_FILE = 200


@dataclass
class DiffHunk:
    old_start: int
    old_count: int
    new_start: int
    new_count: int
    heading: str = ""
    added: int = 0
    removed: int = 0


@dataclass
class ChangedFile:
    old_path: str
    new_path: str
    added: int = 0
    removed: int = 0
    hunks: list[DiffHunk] = field(default_factory=list)


@dataclass
class ParsedDiff:
    files: list[ChangedFile]
    added: int
    removed: int
    text: str
    truncated: bool
    input_truncated: bool = False


_HUNK_RE = re.compile(
    r"^@@\s+-(\d+)(?:,(\d+))?\s+\+(\d+)(?:,(\d+))?\s+@@(?:\s?(.*))?$"
)


def _replace_private_key(match: re.Match[str]) -> str:
    value = match.group(0)
    return "[REDACTED PRIVATE KEY]" + ("\n" * value.count("\n"))


def redact_secrets(value: str) -> str:
    """Redact common credential shapes while preserving diff line structure."""
    text = re.sub(
        r"-----BEGIN [^-\n]*PRIVATE KEY-----[\s\S]*?-----END [^-\n]*PRIVATE KEY-----",
        _replace_private_key,
        value,
        flags=re.IGNORECASE,
    )
    text = re.sub(
        r"(?im)(\b(?:api[_-]?key|access[_-]?token|auth[_-]?token|token|secret|password)\b\s*[:=]\s*[\"']?)([^\s\"'#,;]+)",
        r"\1[REDACTED]",
        text,
    )
    text = re.sub(
        r"(?i)(authorization\s*:\s*bearer\s+)[^\s]+",
        r"\1[REDACTED]",
        text,
    )
    token_patterns = (
        r"\bAKIA[0-9A-Z]{16}\b",
        r"\bAIza[0-9A-Za-z_-]{20,}\b",
        r"\bgh[pousr]_[A-Za-z0-9]{20,}\b",
        r"\b(?:sk|xai|nvapi)-[A-Za-z0-9_-]{8,}\b",
        r"\beyJ[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+\b",
    )
    for pattern in token_patterns:
        text = re.sub(pattern, "[REDACTED]", text)
    return text


def _safe_path(value: str) -> str:
    cleaned = value.strip().strip('"').replace("\\", "/")
    if cleaned.startswith(("a/", "b/")):
        cleaned = cleaned[2:]
    cleaned = "".join(char for char in cleaned if char.isprintable())
    return cleaned[:300] or "unknown"


def _paths_from_diff_header(line: str) -> tuple[str, str]:
    try:
        parts = shlex.split(line)
    except ValueError:
        parts = line.split()
    if len(parts) >= 4:
        return _safe_path(parts[-2]), _safe_path(parts[-1])
    return "unknown", "unknown"


def _cap_utf8(value: str, max_bytes: int) -> tuple[str, bool]:
    encoded = value.encode("utf-8")
    if len(encoded) <= max_bytes:
        return value, False
    return encoded[:max_bytes].decode("utf-8", errors="ignore"), True


def parse_unified_diff(
    value: str,
    *,
    max_diff_bytes: int = DEFAULT_MAX_DIFF_BYTES,
    input_truncated: bool = False,
) -> ParsedDiff:
    if max_diff_bytes < 1024:
        raise ValueError("max_diff_bytes must be at least 1024")

    files: list[ChangedFile] = []
    current_file: Optional[ChangedFile] = None
    current_hunk: Optional[DiffHunk] = None
    old_header_path = "unknown"

    for line in value.splitlines():
        if line.startswith("diff --git "):
            if len(files) >= MAX_FILES:
                current_file = None
                current_hunk = None
                continue
            old_path, new_path = _paths_from_diff_header(line)
            current_file = ChangedFile(old_path=old_path, new_path=new_path)
            files.append(current_file)
            current_hunk = None
            continue
        if line.startswith("--- "):
            old_header_path = _safe_path(line[4:].split("\t", 1)[0])
            continue
        if line.startswith("+++ "):
            new_path = _safe_path(line[4:].split("\t", 1)[0])
            if current_file is None and len(files) < MAX_FILES:
                current_file = ChangedFile(
                    old_path=old_header_path, new_path=new_path
                )
                files.append(current_file)
            elif current_file is not None:
                current_file.old_path = old_header_path
                current_file.new_path = new_path
            continue
        hunk_match = _HUNK_RE.match(line)
        if hunk_match and current_file is not None:
            if len(current_file.hunks) >= MAX_HUNKS_PER_FILE:
                current_hunk = None
                continue
            current_hunk = DiffHunk(
                old_start=int(hunk_match.group(1)),
                old_count=int(hunk_match.group(2) or "1"),
                new_start=int(hunk_match.group(3)),
                new_count=int(hunk_match.group(4) or "1"),
                heading=(hunk_match.group(5) or "")[:200],
            )
            current_file.hunks.append(current_hunk)
            continue
        if current_file is None or current_hunk is None:
            continue
        if line.startswith("+") and not line.startswith("+++"):
            current_file.added += 1
            current_hunk.added += 1
        elif line.startswith("-") and not line.startswith("---"):
            current_file.removed += 1
            current_hunk.removed += 1

    redacted = redact_secrets(value)
    capped_text, payload_truncated = _cap_utf8(redacted, max_diff_bytes)
    return ParsedDiff(
        files=files,
        added=sum(item.added for item in files),
        removed=sum(item.removed for item in files),
        text=capped_text,
        truncated=payload_truncated or input_truncated,
        input_truncated=input_truncated,
    )


def read_diff_input(
    path: Optional[str], *, max_input_bytes: int = DEFAULT_MAX_INPUT_BYTES
) -> tuple[str, bool]:
    if max_input_bytes < DEFAULT_MAX_DIFF_BYTES:
        raise ValueError("max_input_bytes cannot be smaller than 80 KiB")
    if path:
        with Path(path).open("rb") as handle:
            payload = handle.read(max_input_bytes + 1)
    else:
        import sys

        payload = sys.stdin.buffer.read(max_input_bytes + 1)
    truncated = len(payload) > max_input_bytes
    return payload[:max_input_bytes].decode("utf-8", errors="replace"), truncated


def build_review_prompt(parsed: ParsedDiff) -> str:
    file_summary = "\n".join(
        f"- {item.new_path}: +{item.added}/-{item.removed}; "
        f"hunks {', '.join(f'+{h.new_start},{h.new_count}' for h in item.hunks) or 'none'}"
        for item in parsed.files
    ) or "- No textual file changes detected"
    truncation_note = (
        "The diff payload was truncated. Do not infer behavior outside the supplied hunks."
        if parsed.truncated
        else "The supplied diff payload is complete within configured limits."
    )
    return f"""You are performing a review-only code audit of a unified diff.

Return findings only, ordered by severity (P0, P1, P2, P3). Each finding must include:
- a concise title
- the changed file path and new-file line or hunk reference
- the concrete bug, regression, security risk, or missing critical test
- why it matters and the smallest safe remediation

Do not praise, summarize the patch, propose unrelated refactors, execute code, or claim access to files outside this diff. Do not reproduce credentials, tokens, private keys, or other secret values even if visible; refer to them only as [REDACTED]. If no actionable findings exist, return exactly: No actionable findings.

Changed files: {len(parsed.files)}; additions: {parsed.added}; removals: {parsed.removed}.
{truncation_note}

File and hunk index:
{file_summary}

Redacted unified diff:
```diff
{parsed.text}
```
"""


def _response_content(response: Any) -> str:
    choices = getattr(response, "choices", None)
    if choices is None and isinstance(response, dict):
        choices = response.get("choices")
    if not choices:
        raise RuntimeError("Review model returned no choices")
    choice = choices[0]
    message = getattr(choice, "message", None)
    if message is None and isinstance(choice, dict):
        message = choice.get("message")
    content = getattr(message, "content", None)
    if content is None and isinstance(message, dict):
        content = message.get("content")
    if not str(content or "").strip():
        raise RuntimeError("Review model returned an empty response")
    return redact_secrets(str(content).strip())


async def invoke_review_model(prompt: str, model: Optional[str] = None) -> str:
    from config import load_config
    from core.llm_client import ChatRequest, LimeLLMClient

    config = load_config()
    selected_model = str(
        model or os.getenv("LIMEBOT_REVIEW_MODEL") or config.llm.model
    ).strip()
    if not selected_model:
        raise RuntimeError("No review model is configured")
    client = LimeLLMClient()
    provider = client.resolve_provider(
        selected_model, default_base_url=config.llm.base_url
    )
    response = await client.complete(
        provider,
        ChatRequest(
            messages=[
                {
                    "role": "system",
                    "content": "Review code defensively. Never call tools or reproduce secrets.",
                },
                {"role": "user", "content": prompt},
            ],
            max_tokens=2200,
            session_id="ci-review",
            tool_choice=None,
        ),
    )
    return _response_content(response)


def build_review_artifact(
    parsed: ParsedDiff,
    prompt: str,
    *,
    findings: Optional[str] = None,
    model: Optional[str] = None,
) -> dict[str, Any]:
    return {
        "schema_version": 1,
        "generated_at": time.time(),
        "mode": "model_review" if findings is not None else "prompt_only",
        "model": model if findings is not None else None,
        "summary": {
            "files": len(parsed.files),
            "added": parsed.added,
            "removed": parsed.removed,
            "truncated": parsed.truncated,
        },
        "changed_files": [asdict(item) for item in parsed.files],
        "findings": findings,
        "review_prompt": prompt,
    }


def artifact_to_markdown(artifact: dict[str, Any]) -> str:
    summary = artifact["summary"]
    findings = artifact.get("findings") or (
        "Model invocation was not requested. The redacted prompt is included below."
    )
    return (
        "# LimeBot Diff Review\n\n"
        f"- Files: {summary['files']}\n"
        f"- Added: {summary['added']}\n"
        f"- Removed: {summary['removed']}\n"
        f"- Truncated: {str(summary['truncated']).lower()}\n\n"
        f"## Findings\n\n{findings}\n\n"
        f"## Redacted Review Prompt\n\n{artifact['review_prompt']}"
    )


async def run_review(
    diff_text: str,
    *,
    max_diff_bytes: int = DEFAULT_MAX_DIFF_BYTES,
    input_truncated: bool = False,
    invoke_model: bool = False,
    model: Optional[str] = None,
) -> dict[str, Any]:
    parsed = parse_unified_diff(
        diff_text,
        max_diff_bytes=max_diff_bytes,
        input_truncated=input_truncated,
    )
    prompt = build_review_prompt(parsed)
    findings = await invoke_review_model(prompt, model=model) if invoke_model else None
    return build_review_artifact(
        parsed, prompt, findings=findings, model=model or os.getenv("LIMEBOT_REVIEW_MODEL")
    )


def write_review_artifact(
    artifact: dict[str, Any], output: str, output_format: str
) -> None:
    target = Path(output)
    target.parent.mkdir(parents=True, exist_ok=True)
    if output_format == "markdown":
        content = artifact_to_markdown(artifact)
    else:
        content = json.dumps(artifact, indent=2, ensure_ascii=False)
    target.write_text(content, encoding="utf-8")


def run_review_sync(**kwargs: Any) -> dict[str, Any]:
    return asyncio.run(run_review(**kwargs))
