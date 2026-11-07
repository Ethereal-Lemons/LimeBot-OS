"""Resolve, route, and describe capabilities for LimeBot turns."""

from __future__ import annotations

import re
from typing import Any, Callable, Dict, List, Optional

from loguru import logger

from core.profile_context import get_current_profile_context
from core.profiles import profile_allows_tool
from core.redaction import redact_sensitive_text


class CapabilityService:
    """Expose registered tools and resolve the redacted capability inventory."""

    def __init__(
        self,
        *,
        get_skill_registry: Callable[[], Any],
        get_subagent_registry: Callable[[], Any],
        get_session_state: Callable[[], Dict[str, Dict[str, Any]]],
        get_tool_definitions: Callable[[], List[Dict[str, Any]]],
        tool_definition_names: Callable[[List[Dict[str, Any]]], List[str]],
        log_tool_debug: Callable[..., None],
        get_capability_turn_context: Callable[[Optional[str], str], Dict[str, Any]],
        get_tool_definitions_for_turn: Callable[..., List[Dict[str, Any]]],
    ) -> None:
        self._get_skill_registry = get_skill_registry
        self._get_subagent_registry = get_subagent_registry
        self._get_session_state = get_session_state
        self._get_tool_definitions = get_tool_definitions
        self._tool_definition_names = tool_definition_names
        self._log_tool_debug = log_tool_debug
        self._get_capability_turn_context = get_capability_turn_context
        self._get_tool_definitions_for_turn = get_tool_definitions_for_turn

    def get_capability_turn_context(
        self, session_key: Optional[str], user_text: str
    ) -> Dict[str, Any]:
        """Resolve sticky skill names and the text used for relevance routing."""
        key = str(session_key or "").strip()
        registry = self._get_skill_registry()
        if registry is None or not hasattr(registry, "get_relevant_skill_names"):
            return {
                "routing_text": str(user_text or ""),
                "skill_names": [],
                "last_substantive_message": "",
                "revision": 0,
            }

        session_state = self._get_session_state()
        prior = dict(session_state.get(key) or {})
        revision = int(getattr(registry, "capability_revision", 0) or 0)
        if prior.get("revision") != revision:
            prior = {}
        current = str(user_text or "").strip()
        current_names = registry.get_relevant_skill_names(current, max_skills=3)
        previous_names = list(prior.get("skill_names") or [])
        previous_message = str(
            prior.get("last_substantive_message") or ""
        ).strip()
        preserve = bool(previous_names) and not current_names
        routing_text = current
        if preserve and previous_message:
            routing_text = f"{current}\n{previous_message}".strip()
        selected_names = registry.get_relevant_skill_names(
            routing_text,
            max_skills=3,
            sticky_skill_names=previous_names if preserve else None,
        )
        substantive = bool(current) and bool(current_names)
        last_substantive = current if substantive else previous_message
        state = {
            "skill_names": selected_names,
            "last_substantive_message": last_substantive,
            "revision": revision,
        }
        if key:
            session_state[key] = state
        return {"routing_text": routing_text, **state}

    def capability_catalog_prompt(self) -> str:
        registry = self._get_skill_registry()
        catalog: list[Dict[str, Any]] = []
        if registry is not None and hasattr(registry, "get_capability_catalog"):
            try:
                catalog.extend(registry.get_capability_catalog(include_inactive=True))
            except Exception:
                pass
        try:
            for tool in self._get_tool_definitions():
                function = tool.get("function") if isinstance(tool, dict) else {}
                if not isinstance(function, dict):
                    continue
                name = str(function.get("name") or "").strip()
                if not name:
                    continue
                description = redact_sensitive_text(
                    re.sub(r"\s+", " ", str(function.get("description") or "")).strip()
                )[:140]
                state = "ready"
                kind = "mcp_tool" if name.startswith("mcp_") else "native_tool"
                if name.startswith("mcp_"):
                    try:
                        from core.mcp_client import get_mcp_manager

                        server = name.split("_", 2)[1]
                        state = str(
                            get_mcp_manager().get_status().get(server, "Offline")
                        ).lower()
                    except Exception:
                        state = "unknown"
                catalog.append(
                    {
                        "name": name,
                        "type": kind,
                        "description": description,
                        "state": state,
                    }
                )
        except Exception:
            pass
        try:
            from core.mcp_client import get_mcp_manager

            catalog.extend(
                {
                    "name": str(name),
                    "type": "mcp_server",
                    "description": "Configured MCP server",
                    "state": str(state).lower(),
                }
                for name, state in sorted(
                    (get_mcp_manager().get_status() or {}).items()
                )
            )
        except Exception:
            pass
        try:
            descriptions = self._get_subagent_registry().get_agent_descriptions()
            catalog.extend(
                {
                    "name": str(name),
                    "type": "subagent",
                    "description": redact_sensitive_text(
                        re.sub(r"\s+", " ", str(description or "")).strip()
                    )[:140],
                    "state": "ready",
                }
                for name, description in sorted((descriptions or {}).items())
            )
        except Exception:
            pass
        if not catalog:
            return ""
        lines = [
            "\n--- CAPABILITY INVENTORY ---",
            "Compact discovery snapshot (not full manuals). `ready` means a native tool is registered or a skill is enabled with declared dependencies present; it does not prove external credentials are connected.",
        ]
        for item in catalog[:32]:
            name = str(item.get("name") or "")
            kind = str(item.get("type") or "capability")
            state = str(item.get("state") or "unknown")
            desc = str(item.get("description") or "").strip()
            required = ", ".join(item.get("required_tools") or [])
            suffix = f"; tools: {required}" if required else ""
            lines.append(f"- `{name}` ({kind}) [{state}]: {desc}{suffix}")
        if len(catalog) > 32:
            lines.append(f"- ... {len(catalog) - 32} more capabilities.")
        return "\n".join(lines) + "\n"

    def get_tool_definitions_for_turn(
        self,
        user_text: str = "",
        forced_skill_name: Optional[str] = None,
        session_key: Optional[str] = None,
        attachments: Optional[List[Dict[str, Any]]] = None,
    ) -> List[Dict[str, Any]]:
        all_tools = self._get_tool_definitions()
        skill_registry = self._get_skill_registry()
        capability_context = self._get_capability_turn_context(
            session_key, user_text
        )
        selected_skill_names = list(capability_context.get("skill_names") or [])
        if forced_skill_name and forced_skill_name not in selected_skill_names:
            selected_skill_names.insert(0, forced_skill_name)
        if skill_registry is not None:
            if hasattr(skill_registry, "get_required_tool_names_for_skills"):
                required_tool_names = skill_registry.get_required_tool_names_for_skills(
                    selected_skill_names
                )
            else:
                required_tool_names = []
                for name in selected_skill_names:
                    required_tool_names.extend(
                        skill_registry.get_required_tool_names(name)
                    )
                required_tool_names = list(dict.fromkeys(required_tool_names))
        else:
            required_tool_names = []
        selected = list(all_tools)
        strategy = "full_schema_model_routed"
        profile_context = get_current_profile_context()
        if profile_context is not None:
            selected = [
                tool
                for tool in selected
                if profile_allows_tool(
                    profile_context.profile,
                    str(tool.get("function", {}).get("name") or ""),
                )
            ]

        all_names = self._tool_definition_names(all_tools)
        selected_names = self._tool_definition_names(selected)
        self._log_tool_debug(
            "tool_schema_selection",
            strategy=strategy,
            user_text=user_text,
            total_tool_count=len(all_names),
            total_tools=all_names,
            selected_tool_count=len(selected_names),
            selected_tools=selected_names,
            forced_skill=forced_skill_name,
            matched_skills=selected_skill_names,
            required_skill_tools=required_tool_names,
        )
        return selected

    @staticmethod
    def capability_match_score(query: str, name: str, description: str = "") -> int:
        tokens = {
            token
            for token in re.findall(r"[a-z0-9_/-]+", str(query or "").lower())
            if len(token) >= 2
        }
        if not tokens:
            return 1
        haystack = {
            token
            for token in re.findall(
                r"[a-z0-9_/-]+", f"{name} {description}".lower()
            )
            if len(token) >= 2
        }
        score = len(tokens & haystack)
        if str(name or "").lower() in str(query or "").lower():
            score += 50
        return score

    def resolve_capabilities(
        self,
        query: str = "",
        include_disabled: bool = True,
        session_key: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Build a redacted capability resolution snapshot for tools and API."""
        registry = self._get_skill_registry()
        skill_rows: list[Dict[str, Any]] = []
        matched_skills: list[str] = []
        required_tools: list[str] = []
        revision = int(getattr(registry, "capability_revision", 0) or 0)
        if registry is not None:
            try:
                skill_rows = registry.search_capabilities(
                    query, include_inactive=include_disabled, limit=12
                )
            except Exception as exc:
                logger.debug(f"Capability skill search failed: {exc}")
            context = self._get_capability_turn_context(session_key, query)
            matched_skills = list(context.get("skill_names") or [])
            if hasattr(registry, "get_required_tool_names_for_skills"):
                required_tools = registry.get_required_tool_names_for_skills(
                    matched_skills
                )

        all_tools = list(self._get_tool_definitions())
        tool_rows: list[Dict[str, Any]] = []
        for tool in all_tools:
            function = tool.get("function") if isinstance(tool, dict) else {}
            if not isinstance(function, dict):
                continue
            name = str(function.get("name") or "").strip()
            if not name:
                continue
            description = redact_sensitive_text(
                re.sub(r"\s+", " ", str(function.get("description") or "")).strip()
            )[:180]
            score = self.capability_match_score(query, name, description)
            if str(query or "").strip() and score <= 0:
                continue
            if name.startswith("mcp_"):
                parts = name.split("_", 2)
                server = parts[1] if len(parts) > 1 else "unknown"
                try:
                    from core.mcp_client import get_mcp_manager

                    connection_state = get_mcp_manager().get_status().get(
                        server, "Offline"
                    )
                except Exception:
                    connection_state = "unknown"
                state = str(connection_state).lower()
                tool_type = "mcp_tool"
            else:
                state = "ready"
                tool_type = "native_tool"
            tool_rows.append(
                {
                    "name": name,
                    "type": tool_type,
                    "description": description,
                    "state": state,
                    "match_score": score,
                }
            )

        try:
            from core.mcp_client import get_mcp_manager

            mcp_status = get_mcp_manager().get_status()
            for server, connection_state in sorted((mcp_status or {}).items()):
                score = self.capability_match_score(query, str(server), "MCP server")
                if str(query or "").strip() and score <= 0:
                    continue
                tool_rows.append(
                    {
                        "name": str(server),
                        "type": "mcp_server",
                        "description": "Configured MCP server",
                        "state": str(connection_state).lower(),
                        "match_score": score,
                    }
                )
        except Exception:
            pass

        subagent_rows: list[Dict[str, Any]] = []
        subagent_registry = self._get_subagent_registry()
        if subagent_registry is not None:
            try:
                descriptions = subagent_registry.get_agent_descriptions()
            except Exception:
                descriptions = {}
            for name, description in sorted((descriptions or {}).items()):
                score = self.capability_match_score(query, name, description)
                if str(query or "").strip() and score <= 0:
                    continue
                subagent_rows.append(
                    {
                        "name": str(name),
                        "type": "subagent",
                        "description": redact_sensitive_text(
                            re.sub(r"\s+", " ", str(description or "")).strip()
                        )[:180],
                        "state": "ready",
                        "match_score": score,
                    }
                )

        rows = skill_rows + tool_rows + subagent_rows
        rows.sort(
            key=lambda item: (
                -int(item.get("match_score", 0) or 0),
                str(item.get("type") or ""),
                str(item.get("name") or ""),
            )
        )
        selected_tools = self._get_tool_definitions_for_turn(
            query, session_key=session_key
        )
        selected_tool_names = self._tool_definition_names(selected_tools)
        ready_matches = [
            row
            for row in rows
            if row.get("state") in {"ready", "online", "registered"}
        ]
        if ready_matches:
            overall_state = "ready"
            reason = "At least one registered, enabled, or connected capability matched the request."
        elif skill_rows:
            overall_state = str(skill_rows[0].get("state") or "unavailable")
            reason = "A discovered capability matched, but it is not currently operational."
        elif rows:
            overall_state = str(rows[0].get("state") or "unavailable")
            reason = "A capability matched, but it is not currently operational."
        else:
            overall_state = "unavailable"
            reason = "No discovered capability matched this query; use the exact integration name or inspect the inventory."
        return {
            "query": str(query or ""),
            "revision": revision,
            "state": overall_state,
            "reason": reason,
            "matched_skills": matched_skills,
            "required_tools": required_tools,
            "selected_tools": selected_tool_names,
            "capabilities": rows[:30],
        }
