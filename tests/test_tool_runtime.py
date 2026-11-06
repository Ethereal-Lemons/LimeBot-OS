import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock


class _MemoryCache:
    def __init__(self):
        self.values = {}
        self.set_calls = []

    def get(self, name, args):
        return self.values.get((name, tuple(sorted(args.items()))))

    def set(self, name, args, value):
        self.set_calls.append(name)
        self.values[(name, tuple(sorted(args.items())))] = value

    def clear(self):
        self.values.clear()


class TestToolRuntime(unittest.IsolatedAsyncioTestCase):
    async def test_apply_changeset_dispatch_includes_the_current_session(self):
        from core.tool_runtime import (
            ToolExecutionContext,
            ToolRuntime,
            ToolRuntimeHooks,
        )

        toolbox = SimpleNamespace(
            apply_workspace_changeset=AsyncMock(return_value="applied")
        )

        async def unused(*_args, **_kwargs):
            return ""

        runtime = ToolRuntime(
            toolbox=toolbox,
            tool_registry={},
            tool_cache=_MemoryCache(),
            metrics=SimpleNamespace(record_anomaly=lambda *_args, **_kwargs: None),
            hooks=ToolRuntimeHooks(
                background_task=unused,
                search=unused,
                browser=unused,
                tag_compat=unused,
            ),
        )
        session_key = "profile__writer__web_chat"

        result = await runtime.execute(
            "apply_workspace_changeset",
            {"workspace_id": "workspace-123"},
            ToolExecutionContext(session_key=session_key),
        )

        self.assertEqual(result, "applied")
        toolbox.apply_workspace_changeset.assert_awaited_once_with(
            session_key=session_key,
            workspace_id="workspace-123",
        )

    async def test_browser_extract_is_not_cached_as_a_read_only_tool(self):
        from core.tool_runtime import (
            ToolExecutionContext,
            ToolRuntime,
            ToolRuntimeHooks,
        )

        cache = _MemoryCache()
        page_reads = 0

        async def browser(_function_name, _args, _session_key):
            nonlocal page_reads
            page_reads += 1
            return f"page state {page_reads}"

        async def unused(*_args, **_kwargs):
            return ""

        runtime = ToolRuntime(
            toolbox=SimpleNamespace(),
            tool_registry={},
            tool_cache=cache,
            metrics=SimpleNamespace(record_anomaly=lambda *_args, **_kwargs: None),
            hooks=ToolRuntimeHooks(
                background_task=unused,
                search=unused,
                browser=browser,
                tag_compat=unused,
            ),
        )
        context = ToolExecutionContext(session_key="web:page")

        first = await runtime.execute("browser_extract", {"mode": "text"}, context)
        second = await runtime.execute("browser_extract", {"mode": "text"}, context)

        self.assertEqual((first, second), ("page state 1", "page state 2"))
        self.assertEqual(cache.set_calls, [])
