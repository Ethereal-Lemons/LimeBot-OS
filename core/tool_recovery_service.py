"""Recovery instructions, retry coordination, and user-visible tool failures."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable, Dict, List, Optional, Set, Tuple

from core.recovery_controller import RecoveryState
from core.redaction import redact_sensitive_text
from core.tool_dispatcher import MUTATION_TOOL_NAMES
from core.tool_outcomes import ToolOutcome, is_tool_result_error
from core.voice_tool_policy import is_voice_session_key, voice_allows_tool


@dataclass(frozen=True)
class ToolRecoveryRequest:
    session_key: str
    fingerprints: Set[str]
    attempts: int
    message: Any = None
    coding_turn: bool = False
    allowed_tools: Optional[Set[str]] = None
    recovery_state: Optional[RecoveryState] = None
    turn_id: Optional[str] = None
    message_id: Optional[str] = None


@dataclass(frozen=True)
class ToolRecoveryResult:
    attempts: int
    blocked: Optional[str] = None
    queued: bool = False


class ToolRecoveryService:
    """Own tool recovery policy while delegating loop events through explicit ports."""

    def __init__(
        self,
        *,
        config: Any,
        get_tool_definitions: Callable[[], List[Dict[str, Any]]],
        get_tool_outcomes: Callable[[Optional[str]], List[ToolOutcome]],
        append_history: Callable[[str, Dict[str, Any]], None],
        mark_dirty: Callable[[str], None],
        log_session_event: Callable[[str, Dict[str, Any]], None],
        log_tool_debug: Callable[..., None],
        emit_coding_phase: Callable[..., Any],
    ) -> None:
        self._config = config
        self._get_tool_definitions = get_tool_definitions
        self._get_tool_outcomes = get_tool_outcomes
        self._append_history = append_history
        self._mark_dirty = mark_dirty
        self._log_session_event = log_session_event
        self._log_tool_debug = log_tool_debug
        self._emit_coding_phase = emit_coding_phase

    def tool_definitions(
        self,
        initial: Optional[List[Dict[str, Any]]],
        state: Optional[RecoveryState],
        session_key: Optional[str] = None,
    ) -> Optional[List[Dict[str, Any]]]:
        """Rebuild a recovery catalog from the exact registered tool names.

        Voice sessions keep their reduced catalog: recovery must not hand the
        model file or shell tools the session policy has taken away.
        """
        if state is None or not state.active:
            return initial
        all_tools = self._voice_filtered(self._get_tool_definitions(), session_key)
        by_name = {
            str(tool.get("function", {}).get("name") or ""): tool
            for tool in all_tools
            if isinstance(tool, dict)
        }
        allowed = state.allowed_tool_names(by_name.keys())
        ordered: List[Dict[str, Any]] = []
        seen: Set[str] = set()
        for tool in list(initial or []) + all_tools:
            name = str(tool.get("function", {}).get("name") or "")
            if name in allowed and name not in seen:
                ordered.append(by_name[name])
                seen.add(name)
        self._log_tool_debug(
            "recovery_tool_catalog_rebuilt",
            failed_tool=state.failed_tool,
            failure_category=state.failure_category,
            tools=sorted(seen),
        )
        return ordered

    @staticmethod
    def _voice_filtered(
        definitions: List[Dict[str, Any]], session_key: Optional[str]
    ) -> List[Dict[str, Any]]:
        if not is_voice_session_key(session_key):
            return definitions
        return [
            tool
            for tool in definitions
            if isinstance(tool, dict)
            and voice_allows_tool(str(tool.get("function", {}).get("name") or ""))
        ]

    def tool_contract(
        self, state: Optional[RecoveryState], session_key: Optional[str] = None
    ) -> str:
        if state is None or not state.active:
            return ""
        definitions = self._voice_filtered(self._get_tool_definitions(), session_key)
        names = sorted(
            state.allowed_tool_names(
                str(item.get("function", {}).get("name") or "")
                for item in definitions
                if isinstance(item, dict)
            )
        )
        exact = ", ".join(names) or "(no recovery tools are currently available)"
        return (
            "RECOVERY TOOL CONTRACT. Use only these exact registered tool names; do not invent "
            f"aliases or wrapper names: {exact}. Inspect the source/schema before retrying an "
            "invalid-argument failure, and do not repeat an identical failed action without new evidence."
        )

    @staticmethod
    def coding_recovery_message(outcome: ToolOutcome, attempt: int, budget: int) -> str:
        return (
            "Coding verification failed. Continue deliberately: inspect the relevant "
            "failure, apply one targeted edit only if justified, then rerun a narrowed "
            "verification command; otherwise report a blocker. Do not blindly retry a "
            "mutation. "
            f"Repair attempt {attempt}/{budget}. Tool: {outcome.tool}. "
            f"Exit code: {outcome.exit_code!r}. Diagnostic tail: "
            f"{redact_sensitive_text(outcome.diagnostic_tail)}"
        )

    @staticmethod
    def tool_recovery_message(
        outcome: ToolOutcome,
        attempt: int,
        budget: int,
        *,
        repeated: bool = False,
    ) -> str:
        repeated_note = (
            " The same failure fingerprint appeared before; choose a materially "
            "different diagnostic or route."
            if repeated
            else ""
        )
        return (
            "A tool operation failed while pursuing the user's original goal. "
            "Continue toward that goal instead of finalizing a blocker. Classify "
            "the failure (local/client, server, authentication, permission, or "
            "policy), inspect the evidence, and try a different safe route or "
            "diagnostic. Do not disable security controls and do not repeat the "
            "identical failing call unless new evidence justifies it. Only report "
            "the goal as blocked after the blocker is independently confirmed and "
            "reasonable safe alternatives are exhausted."
            f"{repeated_note} Recovery attempt {attempt}/{budget}. "
            f"Tool: {outcome.tool}. Diagnostic tail: "
            f"{redact_sensitive_text(outcome.diagnostic_tail)}"
        )

    @staticmethod
    def has_successful_mutation_since_previous_verifier_failure(
        entries: List[Dict[str, Any]],
    ) -> bool:
        """Allow one repeated failure only when an edit succeeded between failures."""
        failure_indexes = [
            index
            for index, entry in enumerate(entries)
            if entry.get("role") == "tool"
            and entry.get("name") == "run_command"
            and is_tool_result_error("run_command", entry.get("content", ""))
        ]
        if len(failure_indexes) < 2:
            return False
        start = failure_indexes[-2] + 1
        end = failure_indexes[-1]
        return any(
            entry.get("role") == "tool"
            and entry.get("name") in MUTATION_TOOL_NAMES
            and not is_tool_result_error(
                str(entry.get("name")), entry.get("content", "")
            )
            for entry in entries[start:end]
        )

    @staticmethod
    def build_step_cap_fallback(entries: List[Dict[str, Any]]) -> str:
        recent = [entry for entry in entries if entry.get("role") == "tool"][-2:]
        if not recent:
            return "The coding step limit was reached before verification could run."
        details = "; ".join(
            f"{entry.get('name', 'tool')}: "
            f"{redact_sensitive_text(str(entry.get('content', ''))[-220:])}"
            for entry in recent
        )
        return (
            "The coding step limit was reached. I ran: "
            + details
            + ". Remaining work: inspect the last diagnostic and run the narrowed verification."
        )

    def _recovery_budget(self) -> int:
        configured_budget = getattr(
            self._config, "tool_recovery_max_corrective_failures", None
        )
        legacy_budget = getattr(self._config, "tool_recovery_max_attempts", None)
        # Older integrations mutate only the alias. Respect that explicit
        # change while treating the new name as authoritative when both values
        # still match the loaded configuration.
        if configured_budget is None or (
            legacy_budget is not None and legacy_budget != configured_budget
        ):
            configured_budget = legacy_budget
        if configured_budget is None:
            configured_budget = getattr(self._config, "coding_repair_max_attempts", 5)
        return max(1, int(configured_budget or 5))

    async def queue_recovery(self, request: ToolRecoveryRequest) -> ToolRecoveryResult:
        """Queue one model-directed recovery step, or return a concrete blocker."""
        failures = [
            outcome
            for outcome in self._get_tool_outcomes(request.turn_id)
            if not outcome.success
            and (request.allowed_tools is None or outcome.tool in request.allowed_tools)
        ]
        if not failures:
            return ToolRecoveryResult(request.attempts)

        failure = failures[-1]
        budget = self._recovery_budget()
        recovery_state = request.recovery_state
        if recovery_state is not None:
            # Persist the effective budget with the recovery record so a
            # continuation cannot silently switch policy between slices.
            recovery_state.max_corrective_failures = budget

        repeated = failure.failure_fingerprint in request.fingerprints
        attempts = request.attempts + 1
        request.fingerprints.add(failure.failure_fingerprint)
        recovery_limit_reached = (
            not recovery_state.can_continue()
            if recovery_state is not None
            else attempts > budget
        )
        if recovery_limit_reached:
            corrective_count = (
                recovery_state.corrective_failures
                if recovery_state is not None
                else attempts - 1
            )
            blocked = (
                f"Tool recovery budget reached ({corrective_count}/{budget} corrective "
                "failures). The original goal remains incomplete. "
                f"Failure category: {failure.failure_category}. Last diagnostic: "
                f"{redact_sensitive_text(failure.diagnostic_tail)}. "
                "Next action: inspect this diagnostic and provide a new direction, "
                "credential, permission, or corrected input."
            )
            if request.coding_turn:
                await self._emit_coding_phase(
                    "blocked",
                    request.session_key,
                    request.message,
                    turn_id=request.turn_id,
                    message_id=request.message_id,
                    outcome=failure,
                )
            else:
                self._log_session_event(
                    request.session_key,
                    {
                        "type": "tool_recovery_blocked",
                        "tool": failure.tool,
                        "failure_fingerprint": failure.failure_fingerprint,
                        "diagnostic": redact_sensitive_text(failure.diagnostic_tail),
                    },
                )
            return ToolRecoveryResult(attempts, blocked)

        recovery_attempt = (
            max(1, recovery_state.corrective_failures + 1)
            if recovery_state is not None
            else attempts
        )
        recovery_message = (
            self.coding_recovery_message(failure, recovery_attempt, budget)
            if request.coding_turn
            else self.tool_recovery_message(
                failure, recovery_attempt, budget, repeated=repeated
            )
        )
        self._append_history(
            request.session_key,
            {"role": "system", "content": recovery_message},
        )
        self._mark_dirty(request.session_key)
        if request.coding_turn:
            await self._emit_coding_phase(
                "repair",
                request.session_key,
                request.message,
                turn_id=request.turn_id,
                message_id=request.message_id,
                outcome=failure,
            )
        else:
            self._log_session_event(
                request.session_key,
                {
                    "type": "tool_recovery_queued",
                    "tool": failure.tool,
                    "attempt": recovery_attempt,
                    "budget": budget,
                    "corrective_failures": (
                        recovery_state.corrective_failures
                        if recovery_state is not None
                        else max(0, attempts - 1)
                    ),
                    "failure_category": failure.failure_category,
                    "repeated_failure": repeated,
                },
            )
        return ToolRecoveryResult(attempts, queued=True)

    @staticmethod
    def collect_turn_tool_errors(entries: List[Dict[str, Any]]) -> List[Tuple[str, str]]:
        """Return distinct error/rejection/policy-deny results from this turn."""
        errors: List[Tuple[str, str]] = []
        seen: Set[str] = set()
        for entry in reversed(entries):
            role = entry.get("role")
            if role == "user":
                break
            if role != "tool":
                continue
            name = str(entry.get("name") or "tool")
            content = str(entry.get("content") or "").strip()
            if is_tool_result_error(name, content):
                safe_content = redact_sensitive_text(content)
                signature = " ".join(safe_content.casefold().split())
                if signature not in seen:
                    errors.append((name, safe_content))
                    seen.add(signature)
        errors.reverse()
        return errors

    @staticmethod
    def _format_user_visible_tool_error(
        name: str, content: str, reply: str
    ) -> str:
        """Turn tool diagnostics into one concise, actionable chat message."""
        normalized = " ".join(str(content or "").casefold().split())
        visible = str(reply or "").casefold()
        browser_missing = (
            "browser is not installed" in normalized
            or "playwright is not installed" in normalized
        )
        if browser_missing:
            setup = "`npm run lime-bot setup -- --recommended`"
            reply_already_explains_cause = (
                "browser" in visible
                and any(
                    phrase in visible
                    for phrase in ("not installed", "isn't installed", "isn’t installed", "missing")
                )
            )
            if reply_already_explains_cause:
                return (
                    "To enable search, run "
                    f"{setup} from the LimeBot folder."
                )
            return (
                "Search is unavailable because LimeBot's browser is not installed. "
                f"Run {setup} from the LimeBot folder."
            )

        detail = str(content or "").strip()
        for prefix in (f"Error executing {name}:", "Error:"):
            if detail.casefold().startswith(prefix.casefold()):
                detail = detail[len(prefix):].strip()
                break
        label = "Web search" if name == "web_search" else name.replace("_", " ").capitalize()
        return f"{label} failed: {detail or 'unknown error'}"

    @staticmethod
    def ensure_reply_includes_tool_errors(
        reply: str, errors: List[Tuple[str, str]]
    ) -> str:
        """Make each distinct tool failure visible once in clear language."""
        if not errors:
            return reply
        visible = str(reply or "")
        missing: List[Tuple[str, str]] = []
        seen: Set[str] = set()
        for name, content in errors:
            message = ToolRecoveryService._format_user_visible_tool_error(
                name, content, visible
            )
            signature = " ".join(message.casefold().split())
            if signature in seen or message.casefold() in visible.casefold():
                continue
            missing.append((name, message))
            seen.add(signature)
        if not missing:
            return visible
        appendix = "\n\n".join(message for _name, message in missing)
        if visible.strip():
            return visible.rstrip() + "\n\n" + appendix
        return "I couldn't complete the request. " + appendix

    def build_tool_fallback_reply(
        self, entries: List[Dict[str, Any]], max_items: int = 2
    ) -> str:
        errors = self.collect_turn_tool_errors(entries)
        if errors:
            return self.ensure_reply_includes_tool_errors("", errors)

        tool_rows: List[Tuple[str, str]] = []
        for entry in reversed(entries):
            if entry.get("role") != "tool":
                continue
            name = str(entry.get("name") or "tool")
            content = str(entry.get("content") or "").strip()
            tool_rows.append((name, content))
            if len(tool_rows) >= max_items:
                break

        if not tool_rows:
            return "I finished running the tool, but I don't have a follow-up response yet."

        recent_tools = ", ".join(f"`{name}`" for name, _ in reversed(tool_rows))
        return (
            "I finished the requested tool step(s) successfully, but I did not produce "
            f"a natural-language wrap-up. Recent steps: {recent_tools}."
        )
