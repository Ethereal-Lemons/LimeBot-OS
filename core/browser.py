"""
Browser automation module using Playwright.
Provides persistent Chrome session with accessibility tree navigation.
Enhanced with human-like interactions to bypass bot detection.
"""

import asyncio
import random
import sys
import time
import urllib.parse
from pathlib import Path
from typing import Any, Dict, List, Optional

from loguru import logger


if sys.platform == "win32":
    asyncio.set_event_loop_policy(asyncio.WindowsProactorEventLoopPolicy())

try:
    from playwright.async_api import async_playwright, Browser, BrowserContext, Page

    PLAYWRIGHT_AVAILABLE = True
except ImportError:
    PLAYWRIGHT_AVAILABLE = False
    async_playwright = None
    Browser = Any
    BrowserContext = Any
    Page = Any
    logger.warning("Playwright not installed. Browser features will be disabled.")


class BrowserManager:
    """
    Manages a persistent Chrome browser instance for web automation.
    Uses accessibility tree for element identification.
    """

    USER_DATA_DIR = Path.home() / ".limebot" / "browser" / "user-data"
    SCREENSHOTS_DIR = Path.home() / ".limebot" / "browser" / "screenshots"

    _LB_ATTR = "data-lb-id"

    def __init__(self, headless: bool = False):
        self.headless = headless
        self._playwright = None
        self._browser: Optional[Browser] = None
        self._context: Optional[BrowserContext] = None
        self._page: Optional[Page] = None
        self._element_map: Dict[str, Any] = {}

        self.USER_DATA_DIR.mkdir(parents=True, exist_ok=True)
        self.SCREENSHOTS_DIR.mkdir(parents=True, exist_ok=True)

        self._browser_lock = asyncio.Lock()

    async def _ensure_browser(self) -> Page:
        """Ensure browser is running and return the active page."""
        async with self._browser_lock:
            if self._page is not None:
                if not self._page.is_closed():
                    return self._page
                logger.warning("Active page was closed. Recovering...")
                if self._context and self._context.pages:
                    self._page = self._context.pages[-1]
                    return self._page
                await self._do_close()

            logger.info("Launching browser...")
            self._playwright = await async_playwright().start()

            self._context = await self._playwright.chromium.launch_persistent_context(
                user_data_dir=str(self.USER_DATA_DIR),
                headless=self.headless,
                viewport={"width": 1280, "height": 720},
                args=[
                    "--disable-blink-features=AutomationControlled",
                    "--no-sandbox",
                ],
            )

            self._context.on("page", self._handle_new_page)

            self._page = (
                self._context.pages[0]
                if self._context.pages
                else await self._context.new_page()
            )

            await self._page.add_init_script(
                "Object.defineProperty(navigator, 'webdriver', { get: () => undefined });"
            )

            logger.info("Browser launched successfully")
            return self._page

    async def _handle_new_page(self, page: Page) -> None:
        """Switch focus to newly opened tabs."""
        logger.info(f"New tab detected: {page.url}. Switching focus.")
        self._page = page
        self._element_map.clear()

    async def close(self) -> None:
        """Close the browser instance."""
        async with self._browser_lock:
            await self._do_close()

    async def _do_close(self) -> None:
        """Internal close — call only when lock is already held."""
        if self._context:
            await self._context.close()
            self._context = None
            self._page = None

        if self._playwright:
            await self._playwright.stop()
            self._playwright = None

        self._browser = None
        self._element_map.clear()
        logger.info("Browser closed")

    async def list_tabs(self) -> List[Dict[str, Any]]:
        """List all open tabs."""
        if not self._context:
            return []
        tabs = []
        for i, p in enumerate(self._context.pages):
            try:
                tabs.append(
                    {
                        "index": i,
                        "title": await p.title(),
                        "url": p.url,
                        "active": p == self._page,
                    }
                )
            except Exception:
                continue
        return tabs

    async def switch_tab(self, index: int) -> bool:
        """Switch to a specific tab by index."""
        if not self._context or index >= len(self._context.pages):
            return False
        self._page = self._context.pages[index]
        await self._page.bring_to_front()
        self._element_map.clear()
        return True

    async def _handle_overlays(self) -> None:
        """Dismiss common cookie banners or overlays that may block interactions."""
        if not self._page:
            return

        accept_selectors = [
            "button:has-text('Accept')",
            "button:has-text('Aceptar')",
            "button:has-text('Agree')",
            "button:has-text('Consent')",
            "#onetrust-accept-btn-handler",
            ".cookie-banner-close",
        ]
        close_selectors = [
            "button:has-text('Close')",
            "button:has-text('Cerrar')",
            "[aria-label='Close']",
            ".modal-close",
        ]

        for selector in accept_selectors:
            try:
                btn = self._page.locator(selector).first
                if await btn.is_visible(timeout=500):
                    logger.info(f"Auto-dismissing overlay: {selector}")
                    await btn.click()
                    await self._page.wait_for_timeout(500)
            except Exception:
                continue

        for selector in close_selectors:
            try:
                btn = self._page.locator(selector).first
                if await btn.is_visible(timeout=500):
                    has_inputs = await btn.evaluate("""
                        (el) => {
                            const parent = el.closest('div, section, dialog, [role="dialog"]');
                            return parent ? parent.querySelectorAll('input, textbox').length > 0 : false;
                        }
                    """)
                    if not has_inputs:
                        logger.info(f"Dismissing non-form overlay: {selector}")
                        await btn.click()
                        await self._page.wait_for_timeout(500)
            except Exception:
                continue

    _JS_COLLECT = """
    (args) => {
        const { startId, framePrefix, attrName } = args;
        let currentId = startId;
        const items = [];

        function isVisible(elem) {
            if (!elem) return false;
            const style = window.getComputedStyle(elem);
            if (style.display === 'none' || style.visibility === 'hidden' || style.opacity === '0') return false;
            const rect = elem.getBoundingClientRect();
            return rect.width > 0 && rect.height > 0;
        }

        function walk(root) {
            const candidates = root.querySelectorAll(
                'a, button, input, select, textarea, [role="button"], [role="link"], ' +
                '[role="textbox"], [role="checkbox"], [role="radio"], [onclick], ' +
                '[tabindex]:not([tabindex="-1"])'
            );

            for (const elem of candidates) {
                if (!isVisible(elem)) continue;

                const idVal = framePrefix + "-" + currentId;
                elem.setAttribute(attrName, idVal);

                let text = (elem.innerText || elem.getAttribute("aria-label") ||
                            elem.getAttribute("placeholder") || elem.value || "").trim().substring(0, 50);
                const tag  = elem.tagName.toLowerCase();
                const role = elem.getAttribute("role") || tag;

                items.push({ lb_id: "e" + currentId, unique_id: idVal, text, tag, role });
                currentId++;
            }

            // Recurse into shadow roots
            for (const el of root.querySelectorAll('*')) {
                if (el.shadowRoot) walk(el.shadowRoot);
            }
        }

        walk(document);
        return { items, nextId: currentId };
    }
    """

    _ELEMENT_TYPE_MAP = {
        "a": "link",
        "button": "button",
        "input": "input",
        "select": "dropdown",
        "textarea": "textbox",
        "checkbox": "checkbox",
        "radio": "radio",
    }

    def _get_element_type(self, tag: str, role: str) -> str:
        return self._ELEMENT_TYPE_MAP.get(tag, role)

    async def _build_accessibility_tree(self) -> str:
        """Build a text representation of the page's accessibility tree across all frames."""
        page = await self._ensure_browser()
        self._element_map.clear()

        tree_lines: List[str] = []

        counter = {"id": 1}

        async def process_frame(frame, frame_index: int) -> None:
            try:
                result = await frame.evaluate(
                    self._JS_COLLECT,
                    {
                        "startId": counter["id"],
                        "framePrefix": f"f{frame_index}",
                        "attrName": self._LB_ATTR,
                    },
                )
            except Exception as e:
                logger.debug(f"Frame {frame_index} skipped: {e}")
                return

            items = result.get("items", [])
            counter["id"] = result.get("nextId", counter["id"])

            is_main = frame == page.main_frame
            for item in items:
                eid = item["lb_id"]
                unique_id = item["unique_id"]
                text = item["text"]
                role = item["role"]
                tag = item["tag"]

                self._element_map[eid] = frame.locator(
                    f'[{self._LB_ATTR}="{unique_id}"]'
                )

                type_desc = self._get_element_type(tag, role)
                text_display = f'"{text}"' if text else "(no label)"
                frame_info = "" if is_main else f" [Frame: {frame.name or 'anon'}]"
                tree_lines.append(f"[{eid}]{frame_info} {text_display} ({type_desc})")

        await process_frame(page.main_frame, 0)
        for i, child in enumerate(page.frames):
            if child != page.main_frame:
                await process_frame(child, i + 1)

        return (
            "\n".join(tree_lines) if tree_lines else "(No interactive elements found)"
        )

    async def navigate(self, url: str, on_progress=None) -> Dict[str, Any]:
        """Navigate to a URL and return a page snapshot."""
        page = await self._ensure_browser()

        try:
            if on_progress:
                await on_progress(f"🌍 Navigating to {url}...")
            logger.info(f"Navigating to: {url}")
            await asyncio.sleep(random.uniform(0.5, 1.5))

            await page.goto(url, wait_until="domcontentloaded", timeout=30000)
            if on_progress:
                await on_progress("⏳ Waiting for page to settle...")
            await page.wait_for_timeout(2000)

            if on_progress:
                await on_progress("🛡️ Handling overlays...")
            await self._handle_overlays()

            title = await page.title()
            current_url = page.url
            if on_progress:
                await on_progress("🌳 Building accessibility tree...")
            a11y_tree = await self._build_accessibility_tree()

            if on_progress:
                await on_progress("✅ Navigation complete.")
            return {
                "success": True,
                "title": title,
                "url": current_url,
                "elements": a11y_tree,
            }

        except Exception as e:
            logger.error(f"Navigation failed: {e}")
            return {"success": False, "error": str(e)}

    async def click(self, element_id: str) -> Dict[str, Any]:
        """Click an element by its accessibility ID."""
        if element_id not in self._element_map:
            return {
                "success": False,
                "error": f"Element '{element_id}' not found. Run snapshot first.",
            }

        try:
            locator = self._element_map[element_id]
            try:
                await locator.scroll_into_view_if_needed(timeout=2000)
            except Exception:
                pass

            await asyncio.sleep(random.uniform(0.2, 0.5))
            await locator.click()
            await self._page.wait_for_timeout(1000)
            await self._handle_overlays()

            title = await self._page.title()
            a11y_tree = await self._build_accessibility_tree()

            return {
                "success": True,
                "message": f"Clicked {element_id}",
                "title": title,
                "elements": a11y_tree,
            }

        except Exception as e:
            logger.error(f"Click failed: {e}")
            return {"success": False, "error": str(e)}

    async def type_text(self, element_id: str, text: str) -> Dict[str, Any]:
        """Type text into an element with human-like typing speed."""
        if element_id not in self._element_map:
            return {
                "success": False,
                "error": f"Element '{element_id}' not found. Run snapshot first.",
            }

        try:
            locator = self._element_map[element_id]
            await locator.click(timeout=5000)
            await asyncio.sleep(random.uniform(0.3, 0.7))
            await self._page.keyboard.press("Control+A")
            await self._page.keyboard.press("Backspace")
            await locator.type(text, delay=random.randint(50, 150))

            return {"success": True, "message": f"Typed text into {element_id}"}

        except Exception as e:
            logger.error(f"Type failed: {e}")
            return {"success": False, "error": str(e)}

    async def snapshot(self, take_screenshot: bool = True) -> Dict[str, Any]:
        """Get current page state including accessibility tree."""
        page = await self._ensure_browser()

        try:
            await self._handle_overlays()

            title = await page.title()
            current_url = page.url
            a11y_tree = await self._build_accessibility_tree()

            result = {
                "success": True,
                "title": title,
                "url": current_url,
                "elements": a11y_tree,
            }

            if take_screenshot:
                screenshot_path = (
                    self.SCREENSHOTS_DIR / f"snapshot_{time.time():.0f}.png"
                )
                await page.screenshot(path=str(screenshot_path))
                result["screenshot"] = str(screenshot_path)

            return result

        except Exception as e:
            logger.error(f"Snapshot failed: {e}")
            return {"success": False, "error": str(e)}

    async def scroll(
        self, direction: str = "down", amount: int = 500
    ) -> Dict[str, Any]:
        """Scroll the page up or down."""

        if direction not in ("up", "down"):
            return {
                "success": False,
                "error": f"Invalid direction '{direction}'. Use 'up' or 'down'.",
            }

        page = await self._ensure_browser()

        try:
            scroll_amount = amount if direction == "down" else -amount

            await page.evaluate("(amt) => window.scrollBy(0, amt)", scroll_amount)
            await page.wait_for_timeout(300)

            a11y_tree = await self._build_accessibility_tree()
            return {
                "success": True,
                "message": f"Scrolled {direction} by {amount}px",
                "elements": a11y_tree,
            }

        except Exception as e:
            logger.error(f"Scroll failed: {e}")
            return {"success": False, "error": str(e)}

    async def wait(self, ms: int = 1000) -> Dict[str, Any]:
        """Wait for a specified time to let the page update."""

        ms = max(100, min(ms, 30_000))
        page = await self._ensure_browser()

        try:
            await page.wait_for_timeout(ms)
            a11y_tree = await self._build_accessibility_tree()
            return {"success": True, "message": f"Waited {ms}ms", "elements": a11y_tree}

        except Exception as e:
            logger.error(f"Wait failed: {e}")
            return {"success": False, "error": str(e)}

    async def extract(
        self, selector: str = "body", limit: int = 5000
    ) -> Dict[str, Any]:
        """
        Extract text content from the page or a specific selector.
        Searches all frames if not found in the main frame.

        FIX: merged extract() and extract_large() into one method with a limit parameter.
        """
        page = await self._ensure_browser()

        try:
            elem = await page.query_selector(selector)

            if not elem:
                for frame in page.frames:
                    if frame == page.main_frame:
                        continue
                    try:
                        elem = await frame.query_selector(selector)
                        if elem:
                            break
                    except Exception:
                        continue

            if not elem:
                return {
                    "success": False,
                    "error": f"Selector '{selector}' not found in any frame",
                }

            text = await elem.inner_text()
            truncated = len(text) > limit
            if truncated:
                text = text[:limit] + f"\n... (truncated at {limit} chars)"

            return {
                "success": True,
                "selector": selector,
                "text": text,
                "truncated": truncated,
                "original_length": len(text),
            }

        except Exception as e:
            logger.error(f"Extract failed: {e}")
            return {"success": False, "error": str(e)}

    async def list_media(self, on_progress=None) -> Dict[str, Any]:
        """Extract a list of images from the current page."""
        page = await self._ensure_browser()
        url = page.url

        try:
            if on_progress:
                await on_progress("📸 Scanning page for media...")

            if "google.com" in url and "tbm=isch" in url:
                if on_progress:
                    await on_progress(
                        "🔍 Detected Google Image results. Extracting thumbnails..."
                    )
                js_script = """
                () => {
                    const items = Array.from(document.querySelectorAll('div.isv-r, div.rg_bx'));
                    return items.map(item => {
                        const img   = item.querySelector('img');
                        const title = item.querySelector('h3, .mVD9t');
                        if (!img || !img.src) return null;
                        return { src: img.src, alt: (title ? title.innerText : img.alt) || "Google Image Result",
                                 width: img.width, height: img.height, visible: true };
                    }).filter(Boolean);
                }
                """
            else:
                js_script = """
                () => {
                    return Array.from(document.querySelectorAll('img')).map(img => {
                        const rect = img.getBoundingClientRect();
                        return { src: img.src, alt: img.alt || img.title || "",
                                 width: rect.width, height: rect.height,
                                 visible: rect.width > 10 && rect.height > 10 };
                    }).filter(img => img.src && img.src.startsWith('http'));
                }
                """

            all_imgs = await page.evaluate(js_script)

            seen: set = set()
            filtered = []
            for img in all_imgs:
                if img["src"] in seen:
                    continue
                if (
                    img["width"] > 50 or img["height"] > 50 or len(img["alt"]) > 3
                ) and img["visible"]:
                    filtered.append(img)
                    seen.add(img["src"])

            filtered = filtered[:20]

            if not filtered:
                return {
                    "success": True,
                    "count": 0,
                    "media_summary": "No significant images found. Try scrolling or navigating deeper.",
                }

            lines = [f"📸 Found {len(filtered)} images:\n"]
            for i, img in enumerate(filtered, 1):
                desc = img["alt"].replace("\n", " ").strip() or f"Image {i}"
                lines.append(f"{i}. {desc}\n   URL: {img['src']}\n")

            if on_progress:
                await on_progress(f"✅ {len(filtered)} images indexed.")
            return {
                "success": True,
                "count": len(filtered),
                "media_summary": "\n".join(lines),
            }

        except Exception as e:
            logger.error(f"Media extraction failed: {e}")
            return {"success": False, "error": str(e)}

    async def google_search(self, query: str, on_progress=None) -> Dict[str, Any]:
        """Search Google and return structured results."""
        page = await self._ensure_browser()

        try:
            if on_progress:
                await on_progress(f"🔍 Searching Google for: {query}")
            logger.info(f"Google search: {query}")

            encoded_query = urllib.parse.quote_plus(query)
            await page.goto(f"https://www.google.com/search?q={encoded_query}")
            await page.wait_for_timeout(1000)

            if on_progress:
                await on_progress("📄 Extracting top results...")
            results = []

            for container in (await page.query_selector_all("div.g"))[:5]:
                title_elem = await container.query_selector("h3")
                link_elem = await container.query_selector("a")
                snippet_elem = await container.query_selector("div.VwiC3b")

                if title_elem and link_elem:
                    results.append(
                        {
                            "title": await title_elem.inner_text(),
                            "url": await link_elem.get_attribute("href"),
                            "snippet": await snippet_elem.inner_text()
                            if snippet_elem
                            else "",
                        }
                    )

            if not results:
                text = await page.inner_text("body")
                if "No results found" in text:
                    return {
                        "success": True,
                        "results": [],
                        "message": "No results found",
                    }

            result_text = "".join(
                f"{i}. {r['title']}\n   URL: {r['url']}\n   {r['snippet']}\n\n"
                for i, r in enumerate(results, 1)
            )

            if on_progress:
                await on_progress("🌳 Building accessibility tree...")
            a11y_tree = await self._build_accessibility_tree()

            if on_progress:
                await on_progress(f"✅ Found {len(results)} results.")
            return {
                "success": True,
                "query": query,
                "results_summary": result_text
                or "Results found but could not be parsed.",
                "elements": a11y_tree,
            }

        except Exception as e:
            logger.error(f"Google search failed: {e}")
            return {"success": False, "error": str(e)}

    async def press_key(self, key: str) -> Dict[str, Any]:
        """Press a keyboard key (e.g. 'Enter', 'Escape', 'Tab')."""
        page = await self._ensure_browser()
        try:
            await page.keyboard.press(key)
            await page.wait_for_timeout(500)
            a11y_tree = await self._build_accessibility_tree()
            return {
                "success": True,
                "message": f"Pressed '{key}'",
                "elements": a11y_tree,
            }
        except Exception as e:
            logger.error(f"Key press failed: {e}")
            return {"success": False, "error": str(e)}

    async def go_back(self) -> Dict[str, Any]:
        """Navigate to the previous page in history."""
        page = await self._ensure_browser()
        try:
            await page.go_back(wait_until="domcontentloaded", timeout=15000)
            await page.wait_for_timeout(1000)
            a11y_tree = await self._build_accessibility_tree()
            return {
                "success": True,
                "title": await page.title(),
                "url": page.url,
                "elements": a11y_tree,
            }
        except Exception as e:
            logger.error(f"go_back failed: {e}")
            return {"success": False, "error": str(e)}

    async def get_page_text(self) -> Dict[str, Any]:
        """Return all visible text on the current page — useful for reading articles."""
        return await self.extract("body", limit=20_000)


_browser_manager: Optional[BrowserManager] = None

_singleton_lock = asyncio.Lock()


async def get_browser_manager(headless: bool = False) -> BrowserManager:
    """Get or create the global BrowserManager instance."""
    global _browser_manager
    async with _singleton_lock:
        if _browser_manager is None:
            _browser_manager = BrowserManager(headless=headless)
    return _browser_manager


async def close_browser() -> None:
    """Close and discard the global BrowserManager."""
    global _browser_manager
    async with _singleton_lock:
        if _browser_manager:
            await _browser_manager.close()
            _browser_manager = None
