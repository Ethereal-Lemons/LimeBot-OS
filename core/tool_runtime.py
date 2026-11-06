"""Policy-to-result boundary for one native or integration tool call."""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any, Awaitable, Callable, Dict, Iterable, Mapping, Optional

from loguru import logger

from core.profile_context import ProfileRuntimeContext
from core.profiles import profile_allows_tool
from core.tool_dispatcher import (
    BROWSER_CACHEABLE,
    SEARCH_TOOL_NAMES,
    TAG_COMPAT_TOOLS,
    TOOL_CACHEABLE_NAMES,
    preflight_tool_call,
)


@dataclass(frozen=True)
class ToolExecutionContext:
    """Immutable turn data needed to authorize and execute one tool call."""

    session_key: str
    user_text: str = ""
    attachments: Optional[Iterable[Any]] = None
    profile: Optional[ProfileRuntimeContext] = None

    @property
    def profile_id(self) -> str:
        return self.profile.profile_id if self.profile is not None else "default"


@dataclass(frozen=True)
class ToolRuntimeHooks:
    """Loop-owned integrations that the runtime delegates to explicitly."""

    background_task: Callable[..., Awaitable[Any]]
    search: Callable[..., Awaitable[Any]]
    browser: Callable[..., Awaitable[Any]]
    tag_compat: Callable[..., Awaitable[Any]]


def tool_cache_scope_args(
    function_args: Mapping[str, Any], profile_id: str
) -> Dict[str, Any]:
    """Include the profile namespace in cached reads when it is non-default."""
    scoped_args = dict(function_args or {})
    if profile_id and profile_id != "default":
        scoped_args["__limebot_profile_scope__"] = profile_id
    return scoped_args


class ToolRuntime:
    """Apply profile and host policy, dispatch a tool, then update its cache."""

    _MEMORY_ECHO_RE = re.compile(r'^echo\s+["\'](.+?)["\']\s*$', re.DOTALL)
    _MUTATING_CACHE_INVALIDATORS = frozenset(
        {
            "edit_file",
            "write_file",
            "delete_file",
            "create_skill",
            "edit_skill",
            "run_command",
        }
    )

    def __init__(
        self,
        *,
        toolbox: Any,
        tool_registry: Mapping[str, Callable[..., Awaitable[Any]]],
        tool_cache: Any,
        metrics: Any,
        hooks: ToolRuntimeHooks,
    ) -> None:
        self.toolbox = toolbox
        self.tool_registry = tool_registry
        self.tool_cache = tool_cache
        self.metrics = metrics
        self.hooks = hooks

    async def execute(
        self,
        function_name: str,
        function_args: Dict[str, Any],
        context: ToolExecutionContext,
    ) -> Any:
        profile_context = context.profile
        if profile_context is not None:
            if not profile_allows_tool(profile_context.profile, function_name):
                return (
                    f"Error: Tool '{function_name}' is disabled for profile "
                    f"'{profile_context.profile_id}'."
                )

        refusal = preflight_tool_call(
            function_name,
            function_args,
            attachments=context.attachments,
            user_text=context.user_text,
        )
        if refusal:
            return refusal

        cache_args = tool_cache_scope_args(function_args, context.profile_id)
        cached = self.tool_cache.get(function_name, cache_args)
        if cached:
            logger.debug(f"⚡ Cache hit: {function_name}")
            return cached

        if function_name == "run_command":
            command = function_args.get("command", "")
            echo_match = self._MEMORY_ECHO_RE.match(command)
            if echo_match:
                entry = echo_match.group(1).strip()
                if len(entry) > 10:
                    try:
                        result = await self.toolbox.memory_save(entry, scope="journal")
                        logger.info(f"Redirected echo to log_memory: {entry[:80]}")
                        return result
                    except Exception as error:
                        logger.error(f"Error in echo-to-log_memory redirect: {error}")

        session_key = context.session_key
        try:
            if function_name == "spawn_agent":
                result = await self.toolbox.spawn_agent(
                    session_key=session_key, **function_args
                )
            elif function_name == "apply_workspace_changeset":
                result = await self.toolbox.apply_workspace_changeset(
                    session_key=session_key, **function_args
                )
            elif function_name == "capability_search":
                result = (
                    "Error: capability_search is not available. Use the native tools "
                    "and enabled skills directly."
                )
            elif function_name in {"get_task_output", "wait_tasks", "kill_task"}:
                result = await self.hooks.background_task(function_name, function_args)
            elif function_name in SEARCH_TOOL_NAMES:
                result = await self.hooks.search(
                    function_name,
                    function_args,
                    session_key,
                    context.user_text,
                )
            elif function_name.startswith("browser_"):
                result = await self.hooks.browser(
                    function_name, function_args, session_key
                )
            elif function_name.startswith("mcp_"):
                from core.mcp_client import get_mcp_manager

                result = await get_mcp_manager().execute_tool(
                    function_name, function_args
                )
            elif function_name in TAG_COMPAT_TOOLS:
                result = await self.hooks.tag_compat(
                    function_name, function_args, session_key
                )
            else:
                handler = self.tool_registry.get(function_name)
                if handler:
                    result = await handler(**function_args)
                else:
                    self.metrics.record_anomaly(
                        session_key,
                        "unknown_tool",
                        detail=f"{function_name}({function_args})",
                    )
                    result = f"Error: Unknown tool '{function_name}'"

            is_cacheable = (
                function_name in BROWSER_CACHEABLE
                or function_name in TOOL_CACHEABLE_NAMES
            )
            if result and not str(result).startswith("Error:") and is_cacheable:
                self.tool_cache.set(function_name, cache_args, result)

            if (
                function_name in self._MUTATING_CACHE_INVALIDATORS
                and result
                and not str(result).startswith("Error:")
            ):
                self.tool_cache.clear()

            return result
        except Exception as error:
            return f"Error executing '{function_name}': {error}"
