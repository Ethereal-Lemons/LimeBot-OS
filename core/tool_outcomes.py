"""Typed, redacted outcomes shared by tool execution and task recovery."""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from typing import Any, Dict, Optional

from core.recovery_controller import classify_failure
from core.redaction import redact_sensitive_text
from core.tool_dispatcher import (
    MUTATION_TOOL_NAMES,
    READ_ONLY_TOOL_NAMES,
    truncate_tool_result,
)


@dataclass(frozen=True)
class ToolOutcome:
    """Bounded, redaction-safe evidence retained for task recovery."""

    tool: str
    success: bool
    exit_code: Optional[int]
    timed_out: bool
    stalled: bool
    retry_safe: bool
    failure_fingerprint: str
    diagnostic_head: str
    diagnostic_tail: str
    verification_status: Optional[str] = None
    verification_detail: str = ""
    failure_category: str = "unknown"
    progress: bool = False


def extract_run_command_exit_code(result: Any) -> Optional[int]:
    match = re.search(r"Exit Code:\s*(-?\d+)", str(result))
    if not match:
        return None
    try:
        return int(match.group(1))
    except (TypeError, ValueError):
        return None


def is_tool_result_error(function_name: str, result: Any) -> bool:
    text = str(result or "")
    if text.startswith("Error:") or text.startswith("Error executing"):
        return True
    if text.startswith("ACTION CANCELLED:") or text.startswith("ACTION BLOCKED:"):
        return True
    if "[TIMEOUT]" in text or "[STALL]" in text:
        return True

    if function_name in {"run_command", "run_steps"}:
        exit_code = extract_run_command_exit_code(text)
        if exit_code not in (None, 0):
            return True

    if function_name in {
        "edit_file",
        "edit_skill",
        "create_skill",
        "inspect_skill",
        "verify_files",
        "diagnose_files",
    }:
        try:
            payload = json.loads(text)
        except (TypeError, json.JSONDecodeError):
            payload = None
        if isinstance(payload, dict):
            status = str(payload.get("status") or "").strip().lower()
            code = str(payload.get("code") or "").strip().lower()
            if status in {"error", "failed", "blocked", "cancelled"}:
                return True
            if code in {
                "validation_failed",
                "skill_read_only",
                "stale_match",
                "path_rejected",
                "symlink_rejected",
                "sensitive_path",
                "reload_failed",
                "skill_edit_failed",
                "skill_create_failed",
            }:
                return True
            verification = payload.get("verification")
            if (
                function_name == "edit_file"
                and isinstance(verification, dict)
                and verification.get("status") == "failed"
            ):
                return True
    return False


def tool_phase(function_name: str, function_args: Dict[str, Any]) -> str:
    if function_name in {"verify_files", "diagnose_files"}:
        return "verify"
    if function_name in MUTATION_TOOL_NAMES:
        return "apply"
    if function_name in {"run_command", "run_steps"}:
        command = str(
            (function_args or {}).get("command")
            or (function_args or {}).get("commands")
            or ""
        ).lower()
        if re.search(r"\b(test|pytest|unittest|lint|build|compile|check|verify)\b", command):
            return "verify"
        return "apply"
    return "inspect"


def build_tool_outcome(function_name: str, result: Any) -> ToolOutcome:
    text = str(result or "")
    exit_code = extract_run_command_exit_code(text)
    timed_out = "[TIMEOUT]" in text or "timed out" in text.lower()
    stalled = "[STALL]" in text
    success = not is_tool_result_error(function_name, text)
    normalized = re.sub(r"\d+", "#", text.lower())[:2000]
    fingerprint = ""
    if not success:
        fingerprint = hashlib.sha256(
            f"{function_name}:{normalized}".encode("utf-8", "replace")
        ).hexdigest()[:16]

    verification_status: Optional[str] = None
    verification_detail = ""
    if function_name in {"edit_file", "edit_skill", "verify_files", "diagnose_files"}:
        try:
            payload = json.loads(text)
        except (TypeError, json.JSONDecodeError):
            payload = None
        if isinstance(payload, dict):
            verification = payload.get("verification")
            if function_name == "verify_files":
                verification = payload
            if isinstance(verification, dict):
                status = str(verification.get("status") or "").strip().lower()
                if status:
                    verification_status = status
                verification_detail = truncate_tool_result(
                    redact_sensitive_text(json.dumps(verification, ensure_ascii=False)),
                    600,
                )

    diagnostic = truncate_tool_result(redact_sensitive_text(text), 800)
    split = diagnostic.split("\n... [truncated diagnostic] ...\n", 1)
    failure_category = "" if success else classify_failure(function_name, text)
    progress = success and function_name in {
        "edit_file",
        "write_file",
        "edit_skill",
        "create_skill",
        "verify_files",
        "diagnose_files",
        "run_command",
    }
    return ToolOutcome(
        tool=function_name,
        success=success,
        exit_code=exit_code,
        timed_out=timed_out,
        stalled=stalled,
        retry_safe=function_name in READ_ONLY_TOOL_NAMES,
        failure_fingerprint=fingerprint,
        diagnostic_head=split[0][:400],
        diagnostic_tail=(split[1] if len(split) == 2 else split[0])[-400:],
        verification_status=verification_status,
        verification_detail=verification_detail,
        failure_category=failure_category or "unknown",
        progress=progress,
    )
