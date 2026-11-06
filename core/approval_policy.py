"""Approval decisions for sensitive tool calls.

The policy is deliberately stateless. ``AgentLoop`` owns the session whitelist
and passes it in, while this module resolves the effective policy and returns
the decision consumed by the existing confirmation flow.
"""

from __future__ import annotations

import shlex
from typing import Any, Dict, Mapping, Optional, Set

from core.profile_context import get_current_profile_context
from core.unattended import evaluate_unattended_tool, is_unattended_turn


_VALID_PROFILES = frozenset({"manual", "session", "autonomous", "review"})
_STRICTNESS = {"autonomous": 0, "session": 1, "manual": 2, "review": 3}


class ToolApprovalPolicy:
    """Resolve tool approval requirements without owning session state."""

    @staticmethod
    def session_whitelist_key(
        function_name: str, function_args: Optional[dict] = None
    ) -> str:
        """Scope shell approvals to their first command binary."""
        if function_name in {"run_command", "run_steps"} and isinstance(
            function_args, dict
        ):
            command = str(function_args.get("command") or "").strip()
            if function_name == "run_steps":
                commands = function_args.get("commands") or []
                if isinstance(commands, list) and commands:
                    command = str(commands[0] or "").strip()
            if not command:
                return function_name
            binary = command
            try:
                parts = shlex.split(command)
                if parts:
                    binary = parts[0]
            except Exception:
                binary = command
            return f"{function_name}::{binary}"
        return function_name

    @classmethod
    def decide(
        cls,
        session_key: str,
        function_name: str,
        *,
        config: Any = None,
        session_whitelists: Optional[Mapping[str, Set[str]]] = None,
        is_internal: bool = False,
        is_whatsapp: bool = False,
        function_args: Optional[dict] = None,
        msg: Any = None,
    ) -> Dict[str, Any]:
        """Return the effective approval decision for one tool call."""
        runtime_profile = get_current_profile_context()
        configured_profile = (
            runtime_profile.profile.policy.approval_policy
            if runtime_profile is not None
            else None
        )
        global_profile = str(
            getattr(config, "approval_policy_profile", "manual") or "manual"
        ).strip().lower()
        if global_profile not in _VALID_PROFILES:
            global_profile = "manual"
        requested_profile = str(configured_profile or global_profile).strip().lower()
        if requested_profile not in _VALID_PROFILES:
            requested_profile = "manual"
        profile = max(
            (global_profile, requested_profile), key=lambda item: _STRICTNESS[item]
        )

        if is_whatsapp:
            # WhatsApp is explicitly fast; contact allow-listing and hard tool
            # safety checks still apply in the tool layer.
            return {
                "allowed": True,
                "requires_confirmation": False,
                "reason": "channel_whatsapp_autonomous",
                "policy_profile": profile,
            }
        if is_unattended_turn(msg):
            decision = evaluate_unattended_tool(function_name, function_args, config)
            decision["policy_profile"] = profile
            return decision
        if is_internal:
            return {
                "allowed": True,
                "requires_confirmation": False,
                "reason": "internal",
                "policy_profile": profile,
            }
        if profile == "autonomous":
            return {
                "allowed": True,
                "requires_confirmation": False,
                "reason": "policy_autonomous",
                "policy_profile": profile,
            }

        whitelist = (session_whitelists or {}).get(session_key, set())
        if profile != "review" and cls.session_whitelist_key(
            function_name, function_args
        ) in whitelist:
            return {
                "allowed": True,
                "requires_confirmation": False,
                "reason": "session_whitelist",
                "policy_profile": profile,
            }
        return {
            "allowed": False,
            "requires_confirmation": True,
            "reason": "manual_required",
            "policy_profile": profile,
        }
