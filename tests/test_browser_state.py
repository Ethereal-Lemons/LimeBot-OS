import types
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch


class TestBrowserState(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        try:
            import loguru  # noqa: F401
        except Exception:
            raise unittest.SkipTest("Missing dependencies (loguru).")

    async def asyncTearDown(self):
        from core.browser import close_browser

        await close_browser()

    async def test_browser_manager_is_scoped_per_session(self):
        from core.browser import BrowserManager, get_browser_manager

        profiles_dir = Path("temp") / "test-browser-profiles"
        screenshots_dir = Path("temp") / "test-browser-screenshots"

        with patch.object(BrowserManager, "PROFILES_DIR", profiles_dir), patch.object(
            BrowserManager, "SCREENSHOTS_DIR", screenshots_dir
        ):
            first = await get_browser_manager("web:alpha")
            first_again = await get_browser_manager("web:alpha")
            second = await get_browser_manager("discord:beta")

        self.assertIs(first, first_again)
        self.assertIsNot(first, second)
        self.assertNotEqual(first.user_data_dir, second.user_data_dir)
        self.assertNotEqual(first.screenshots_dir, second.screenshots_dir)

    async def test_browser_manager_can_be_shared_across_sessions(self):
        from core.browser import BrowserManager, get_browser_manager

        profiles_dir = Path("temp") / "test-browser-profiles-shared"
        screenshots_dir = Path("temp") / "test-browser-screenshots-shared"
        cfg = SimpleNamespace(browser=SimpleNamespace(mode="shared"))

        with patch.object(BrowserManager, "PROFILES_DIR", profiles_dir), patch.object(
            BrowserManager, "SCREENSHOTS_DIR", screenshots_dir
        ):
            first = await get_browser_manager("web:alpha", config=cfg)
            second = await get_browser_manager("discord:beta", config=cfg)

        self.assertIs(first, second)
        self.assertEqual(first.mode, "shared")

    async def test_execute_browser_tool_uses_calling_session_key(self):
        from core.bus import MessageBus
        from core.loop import AgentLoop

        class _TestAgentLoop(AgentLoop):
            async def _init_skills_and_tools(self) -> None:
                self._tool_definitions = []
                self._warmed = True

        class _DummyBrowser:
            async def snapshot(self):
                return {
                    "success": True,
                    "title": "Example",
                    "url": "https://example.com",
                }

        captured = {}

        async def _fake_get_browser_manager(
            session_key: str, headless: bool = False, config=None
        ):
            captured["session_key"] = session_key
            captured["headless"] = headless
            captured["config"] = config
            return _DummyBrowser()

        bus = MessageBus()
        agent = _TestAgentLoop(bus=bus)

        with patch("core.loop.get_browser_manager", _fake_get_browser_manager):
            result = await agent._execute_tool(
                "browser_snapshot", {}, session_key="discord:session-42"
            )

        self.assertEqual(captured["session_key"], "discord:session-42")
        self.assertIs(captured["config"], agent.config)
        self.assertIn("https://example.com", str(result))

    async def test_browser_tool_results_are_not_cached(self):
        from core.bus import MessageBus
        from core.loop import AgentLoop

        class _TestAgentLoop(AgentLoop):
            async def _init_skills_and_tools(self) -> None:
                self._tool_definitions = []
                self._warmed = True

        bus = MessageBus()
        agent = _TestAgentLoop(bus=bus)
        calls = []

        async def _fake_execute_browser_tool(self, function_name, function_args, session_key):
            calls.append((function_name, dict(function_args), session_key))
            return f"browser call {len(calls)}"

        agent._execute_browser_tool = types.MethodType(_fake_execute_browser_tool, agent)

        first = await agent._execute_tool("browser_snapshot", {}, session_key="web:test")
        second = await agent._execute_tool("browser_snapshot", {}, session_key="web:test")

        self.assertEqual(calls, [
            ("browser_snapshot", {}, "web:test"),
            ("browser_snapshot", {}, "web:test"),
        ])
        self.assertEqual(first, "browser call 1")
        self.assertEqual(second, "browser call 2")

    async def test_navigate_recovers_when_site_hands_off_to_new_tab(self):
        from core.browser import BrowserManager

        profiles_dir = Path("temp") / "test-browser-profiles-handoff"
        screenshots_dir = Path("temp") / "test-browser-screenshots-handoff"

        class _FakeContext:
            def __init__(self, pages):
                self.pages = pages

        class _FakePage:
            def __init__(self, url, goto_impl=None):
                self.url = url
                self._goto_impl = goto_impl

            async def goto(self, *_args, **_kwargs):
                if self._goto_impl:
                    await self._goto_impl()

            def is_closed(self):
                return False

        with patch.object(BrowserManager, "PROFILES_DIR", profiles_dir), patch.object(
            BrowserManager, "SCREENSHOTS_DIR", screenshots_dir
        ):
            manager = BrowserManager("web:test")

        new_page = _FakePage("https://x.com/sharbel/status/2029893898496069694")
        context = _FakeContext([])

        async def _raise_aborted():
            context.pages.append(new_page)
            manager._page = new_page
            raise RuntimeError(
                "Page.goto: net::ERR_ABORTED at "
                "https://x.com/sharbel/status/2029893898496069694"
            )

        original_page = _FakePage("about:blank", goto_impl=_raise_aborted)
        context.pages.append(original_page)
        manager._context = context
        manager._page = original_page

        finalize = AsyncMock(
            return_value={
                "success": True,
                "title": "Sharbel on X",
                "url": new_page.url,
                "elements": "(No interactive elements found)",
                "recovered": True,
            }
        )

        with patch.object(manager, "_ensure_browser", AsyncMock(return_value=original_page)), patch(
            "core.browser.random.uniform", return_value=0
        ), patch("core.browser.asyncio.sleep", AsyncMock()), patch.object(
            manager, "_finalize_navigation", finalize
        ):
            result = await manager.navigate(new_page.url)

        self.assertTrue(result["success"])
        self.assertTrue(result["recovered"])
        finalize.assert_awaited_once()
        recovered_page = finalize.await_args.args[0]
        self.assertIs(recovered_page, new_page)
        self.assertEqual(finalize.await_args.kwargs["recovered"], True)
