"""Browser tool execution and host-owned search orchestration."""

from __future__ import annotations

from collections.abc import Awaitable, Callable, Mapping
from dataclasses import asdict, dataclass
from typing import Any, Literal, Optional, Union

from loguru import logger

from core.redaction import redact_sensitive_text

SearchKind = Literal["web", "news", "images"]


@dataclass(frozen=True)
class SearchRequest:
    query: str
    kind: SearchKind
    count: int
    attach_to_chat: bool = False


@dataclass(frozen=True)
class Navigate:
    url: str


@dataclass(frozen=True)
class Click:
    element_id: str


@dataclass(frozen=True)
class TypeText:
    element_id: str
    text: str


@dataclass(frozen=True)
class Scroll:
    direction: str
    amount: int


@dataclass(frozen=True)
class Wait:
    milliseconds: int


@dataclass(frozen=True)
class PressKey:
    key: str


@dataclass(frozen=True)
class GoBack:
    pass


@dataclass(frozen=True)
class ListTabs:
    pass


@dataclass(frozen=True)
class SwitchTab:
    index: int


@dataclass(frozen=True)
class Download:
    element_id: str
    filename: str
    timeout_ms: int
    destination: str
    url: str


@dataclass(frozen=True)
class Snapshot:
    pass


@dataclass(frozen=True)
class Extract:
    selector: str
    limit: int


@dataclass(frozen=True)
class GetPageText:
    pass


@dataclass(frozen=True)
class ListMedia:
    pass


@dataclass(frozen=True)
class UnknownBrowserAction:
    action: str


@dataclass(frozen=True)
class UnknownBrowserTool:
    name: str


BrowserCommand = Union[
    Navigate,
    Click,
    TypeText,
    Scroll,
    Wait,
    PressKey,
    GoBack,
    ListTabs,
    SwitchTab,
    Download,
    Snapshot,
    Extract,
    GetPageText,
    ListMedia,
    UnknownBrowserAction,
    UnknownBrowserTool,
]

_BrowserManagerProvider = Callable[[str], Awaitable[Any]]
_ProgressReporter = Callable[[str], Awaitable[Any]]
_ImageAttacher = Callable[[str, str], Awaitable[Any]]


class BrowserSearchService:
    """Own browser execution and host-search orchestration behind typed calls."""

    def __init__(
        self,
        *,
        manager_provider: _BrowserManagerProvider,
        report_progress: _ProgressReporter,
        attach_chat_image: _ImageAttacher,
    ) -> None:
        self._manager_provider = manager_provider
        self._report_progress = report_progress
        self._attach_chat_image = attach_chat_image

    async def execute_browser(
        self,
        command: BrowserCommand,
        *,
        session_key: str,
        source_tool_name: str,
    ) -> str:
        from core.browser import (
            BROWSER_INSTALL_HINT,
            PLAYWRIGHT_AVAILABLE,
            compact_browser_launch_error,
            playwright_launch_dead_error,
        )

        dead = playwright_launch_dead_error()
        if dead:
            return dead

        try:
            browser = await self._manager_provider(session_key)
            result = await self._run_browser_command(browser, command)
            return self._format_browser_result(result)
        except Exception as error:
            compact = compact_browser_launch_error(error)
            if compact:
                logger.warning(
                    "Browser launch failed; returning a one-line error without chrome flags."
                )
                return compact
            if not PLAYWRIGHT_AVAILABLE or BROWSER_INSTALL_HINT in str(error):
                return f"Error: {BROWSER_INSTALL_HINT}"
            logger.exception(
                "Browser tool execution failed: "
                f"{source_tool_name} args={redact_sensitive_text(asdict(command))}"
            )
            return f"Error executing browser tool: {error}"

    async def _run_browser_command(self, browser: Any, command: BrowserCommand) -> Any:
        if isinstance(command, Navigate):
            return await browser.navigate(
                command.url, on_progress=self._report_progress
            )
        if isinstance(command, Click):
            return await browser.click(command.element_id)
        if isinstance(command, TypeText):
            return await browser.type_text(command.element_id, command.text)
        if isinstance(command, Scroll):
            return await browser.scroll(command.direction, command.amount)
        if isinstance(command, Wait):
            return await browser.wait(command.milliseconds)
        if isinstance(command, PressKey):
            return await browser.press_key(command.key)
        if isinstance(command, GoBack):
            return await browser.go_back()
        if isinstance(command, ListTabs):
            return await browser.list_tabs()
        if isinstance(command, SwitchTab):
            return await browser.switch_tab(command.index)
        if isinstance(command, Download):
            return await browser.download(
                command.element_id,
                command.filename,
                command.timeout_ms,
                command.destination,
                command.url,
            )
        if isinstance(command, Snapshot):
            return await browser.snapshot()
        if isinstance(command, Extract):
            return await browser.extract(command.selector, limit=command.limit)
        if isinstance(command, GetPageText):
            return await browser.get_page_text()
        if isinstance(command, ListMedia):
            return await browser.list_media(on_progress=self._report_progress)
        if isinstance(command, UnknownBrowserAction):
            return {
                "success": False,
                "error": f"Unknown browser_act action '{command.action}'",
            }
        return f"Error: Unknown browser tool '{command.name}'"

    @staticmethod
    def _format_browser_result(result: Any) -> str:
        if isinstance(result, dict):
            if result.get("success"):
                parts = []
                for key, label in [
                    ("query", "**Search Query:**"),
                    ("results_summary", "**Results:**"),
                    ("title", "**Page:**"),
                    ("url", "**URL:**"),
                    ("note", "**Note:**"),
                    ("warning", "**Warning:**"),
                    ("message", None),
                    ("path", "**Saved path:**"),
                    ("elements", "**Elements:**"),
                    ("media_summary", None),
                ]:
                    value = result.get(key)
                    if value:
                        parts.append(f"{label}\n{value}" if label else value)
                if result.get("text"):
                    parts.append(f"**Content:**\n{result['text'][:2000]}")
                return "\n".join(parts) if parts else "Success"
            return f"Error: {result.get('error', 'Unknown error')}"

        if isinstance(result, list):
            return "Open Tabs:\n" + "".join(
                f"{tab['index']}: {tab['title']} ({tab['url']}) "
                f"{'[ACTIVE]' if tab.get('active') else ''}\n"
                for tab in result
            )

        return str(result)

    async def execute_search(
        self,
        request: SearchRequest,
        *,
        session_key: str,
        user_text: str,
    ) -> str:
        from core.browser import BROWSER_INSTALL_HINT, PLAYWRIGHT_AVAILABLE
        from core.web_search import format_search_response

        if not request.query:
            return "Error: a search query is required."

        response, last_error = await self._gather_search(
            request, session_key=session_key, on_progress=self._report_progress
        )
        if response is None:
            if (not PLAYWRIGHT_AVAILABLE) or (
                last_error and BROWSER_INSTALL_HINT in last_error
            ):
                return f"Error: {BROWSER_INSTALL_HINT}"
            return (
                "Error: web search returned no usable results "
                f"({last_error or 'empty'}). Answer from what you know and say the "
                "live lookup was empty. Do not open a search engine in the browser."
            )

        await self._maybe_attach_search_image(
            response, request.query, attach_to_chat=request.attach_to_chat
        )
        return format_search_response(response)

    async def _gather_search(
        self,
        request: SearchRequest,
        *,
        session_key: str,
        on_progress: Optional[_ProgressReporter] = None,
    ) -> tuple[Any, str]:
        from core.browser import BROWSER_INSTALL_HINT, PLAYWRIGHT_AVAILABLE
        from core.web_search import run_host_search

        if not PLAYWRIGHT_AVAILABLE:
            return None, BROWSER_INSTALL_HINT

        last_error = ""
        try:
            if on_progress:
                label = "images" if request.kind == "images" else (
                    "news" if request.kind == "news" else "the web"
                )
                await on_progress(f"🔍 Searching {label} for: {request.query}")
            browser = await self._manager_provider(session_key)

            async def fetch_html(url: str, scroll: bool = False) -> str:
                return await browser.fetch_page_html(url, scroll=scroll)

            response = await run_host_search(
                request.query,
                kind=request.kind,
                count=request.count,
                fetch_html=fetch_html,
            )
            if response.ok:
                return response, ""
            last_error = response.error or "no results"
            if BROWSER_INSTALL_HINT in last_error:
                return None, BROWSER_INSTALL_HINT
        except Exception as error:
            last_error = str(error)
            logger.warning(f"Host search failed: {error}")
            if BROWSER_INSTALL_HINT in last_error:
                return None, BROWSER_INSTALL_HINT

        return None, last_error

    async def _maybe_attach_search_image(
        self,
        response: Any,
        query: str,
        *,
        attach_to_chat: bool,
    ) -> None:
        if (
            not attach_to_chat
            or getattr(response, "kind", "") != "images"
            or not getattr(response, "images", None)
        ):
            return

        image = response.images[0]
        caption = str(image.title or query or "").strip()
        try:
            result = await self._attach_chat_image(image.image_url, caption)
        except Exception as error:
            logger.warning(f"Host image attach failed: {error}")
            return
        if str(result or "").startswith("Error:"):
            logger.warning(f"Host image attach failed: {result}")
            return
        response.attached = True


class BrowserSearchToolAdapter:
    """Translate native tool calls into the browser-search service contract."""

    def __init__(self, service: BrowserSearchService) -> None:
        self._service = service

    async def browser_tool(
        self,
        function_name: str,
        function_args: Mapping[str, Any],
        session_key: str,
    ) -> str:
        command = self._browser_command(function_name, dict(function_args or {}))
        return await self._service.execute_browser(
            command,
            session_key=session_key,
            source_tool_name=function_name,
        )

    async def search_tool(
        self,
        function_name: str,
        function_args: Mapping[str, Any],
        session_key: str,
        user_text: str,
    ) -> str:
        from core.web_search import DEFAULT_COUNT, MAX_COUNT

        args = dict(function_args or {})
        query = str(args.get("query") or "").strip()
        raw_kind = str(args.get("kind") or "web").strip().lower()
        if function_name in {"image_search", "imagesearch", "search_images"}:
            kind: SearchKind = "images"
        elif raw_kind in {"image", "images", "photo", "photos", "pic", "pics"}:
            kind = "images"
        elif raw_kind in {"news", "nws"}:
            kind = "news"
        else:
            kind = "web"

        try:
            count = max(1, min(int(args.get("count") or DEFAULT_COUNT), MAX_COUNT))
        except (TypeError, ValueError):
            count = DEFAULT_COUNT
        attach_to_chat = args.get("attach_to_chat")
        if not isinstance(attach_to_chat, bool):
            attach_to_chat = kind == "images"

        return await self._service.execute_search(
            SearchRequest(
                query=query,
                kind=kind,
                count=count,
                attach_to_chat=attach_to_chat,
            ),
            session_key=session_key,
            user_text=user_text,
        )

    @classmethod
    def _browser_command(
        cls, function_name: str, args: Mapping[str, Any]
    ) -> BrowserCommand:
        if function_name == "browser_navigate":
            return Navigate(args.get("url", ""))
        if function_name == "browser_act":
            return cls._browser_action(args)
        if function_name == "browser_click":
            return Click(args.get("element_id", ""))
        if function_name == "browser_download":
            return cls._download(args)
        if function_name == "browser_type":
            return TypeText(args.get("element_id", ""), args.get("text", ""))
        if function_name == "browser_snapshot":
            return Snapshot()
        if function_name == "browser_scroll":
            return Scroll(args.get("direction", "down"), args.get("amount", 500))
        if function_name == "browser_wait":
            return Wait(args.get("ms", 1000))
        if function_name == "browser_press_key":
            return PressKey(args.get("key", "Enter"))
        if function_name == "browser_go_back":
            return GoBack()
        if function_name == "browser_tabs":
            return ListTabs()
        if function_name == "browser_switch_tab":
            return SwitchTab(args.get("index", 0))
        if function_name == "browser_extract":
            mode = str(args.get("mode") or "text").strip().lower()
            if mode == "media":
                return ListMedia()
            if mode == "snapshot":
                return Snapshot()
            return Extract(args.get("selector", "body"), args.get("limit", 5000))
        if function_name == "browser_extract_large":
            return Extract(args.get("selector", "body"), 100_000)
        if function_name == "browser_get_page_text":
            return GetPageText()
        if function_name == "browser_list_media":
            return ListMedia()
        return UnknownBrowserTool(function_name)

    @classmethod
    def _browser_action(cls, args: Mapping[str, Any]) -> BrowserCommand:
        action = str(args.get("action") or "snapshot").strip().lower()
        if action == "snapshot":
            return Snapshot()
        if action == "click":
            return Click(args.get("element_id", ""))
        if action == "type":
            return TypeText(args.get("element_id", ""), args.get("text", ""))
        if action == "scroll":
            return Scroll(args.get("direction", "down"), args.get("amount", 500))
        if action == "wait":
            return Wait(args.get("ms", 1000))
        if action == "press":
            return PressKey(args.get("key", "Enter"))
        if action == "back":
            return GoBack()
        if action == "tabs":
            return ListTabs()
        if action == "switch_tab":
            return SwitchTab(args.get("index", 0))
        if action == "download":
            return cls._download(args)
        return UnknownBrowserAction(action)

    @staticmethod
    def _download(args: Mapping[str, Any]) -> Download:
        return Download(
            args.get("element_id", ""),
            args.get("filename", ""),
            args.get("timeout_ms", 30_000),
            args.get("dest", ""),
            args.get("url", ""),
        )
