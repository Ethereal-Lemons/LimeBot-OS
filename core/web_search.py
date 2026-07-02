"""
core/web_search.py
──────────────────
Hybrid, multi-provider web / news / image search.

The provider used is chosen from configured API keys with a graceful fallback
chain:

    Tavily > Brave > SerpAPI > DuckDuckGo HTML (keyless, no browser)

Google-via-Playwright scraping is intentionally NOT implemented here: it needs a
live browser session owned by ``core/loop.py``, so the loop appends it as a
final fallback after this chain is exhausted.

All providers normalize their responses into ``SearchResponse`` so the rest of
the system never has to care which backend answered.
"""

from __future__ import annotations

import re
import urllib.parse
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from loguru import logger

try:
    import httpx

    _HTTPX_AVAILABLE = True
except Exception:  # pragma: no cover - httpx is a core dep, but stay defensive
    httpx = None  # type: ignore
    _HTTPX_AVAILABLE = False

try:
    from bs4 import BeautifulSoup

    _BS4_AVAILABLE = True
except Exception:  # pragma: no cover - optional, DDG degrades without it
    BeautifulSoup = None  # type: ignore
    _BS4_AVAILABLE = False


DEFAULT_TIMEOUT = 15.0
DEFAULT_COUNT = 8
MAX_COUNT = 20

_UA = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
    ),
    "Accept-Language": "en-US,en;q=0.9",
}


# ── Normalized result types ──────────────────────────────────────────────


@dataclass
class SearchResult:
    title: str = ""
    url: str = ""
    snippet: str = ""
    content: str = ""  # extended/full content when the provider supplies it
    published: str = ""
    source: str = ""  # provider name


@dataclass
class ImageResult:
    title: str = ""
    image_url: str = ""
    thumbnail_url: str = ""
    source_page: str = ""
    width: int = 0
    height: int = 0


@dataclass
class SearchResponse:
    kind: str = "web"  # web | news | images
    query: str = ""
    provider: str = ""
    answer: str = ""  # provider-supplied direct answer (e.g. Tavily)
    results: List[SearchResult] = field(default_factory=list)
    images: List[ImageResult] = field(default_factory=list)
    error: str = ""

    @property
    def ok(self) -> bool:
        return not self.error and bool(self.results or self.images or self.answer)


# ── Config helpers ────────────────────────────────────────────────────────


def _search_config(config: Any) -> Dict[str, str]:
    sc = getattr(config, "search", None)

    def g(name: str, default: str = "") -> str:
        value = getattr(sc, name, default) if sc is not None else default
        return str(value or default).strip()

    provider = g("provider", "auto").lower() or "auto"
    return {
        "provider": provider,
        "tavily_key": g("tavily_api_key", ""),
        "brave_key": g("brave_api_key", ""),
        "serpapi_key": g("serpapi_api_key", ""),
    }


def search_api_configured(config: Any) -> bool:
    """True when at least one paid/keyed search provider is configured."""
    c = _search_config(config)
    return bool(c["tavily_key"] or c["brave_key"] or c["serpapi_key"])


def _clamp_count(count: Any) -> int:
    try:
        return max(1, min(int(count), MAX_COUNT))
    except (TypeError, ValueError):
        return DEFAULT_COUNT


# ── Providers ─────────────────────────────────────────────────────────────


class WebSearchProvider:
    name = "base"

    def __init__(self, config: Any):
        self.config = config
        self._cfg = _search_config(config)

    @property
    def supports_images(self) -> bool:
        return False

    async def search(
        self, query: str, count: int = DEFAULT_COUNT, kind: str = "web"
    ) -> SearchResponse:  # pragma: no cover - interface
        raise NotImplementedError

    @staticmethod
    def _client() -> "httpx.AsyncClient":
        return httpx.AsyncClient(
            timeout=DEFAULT_TIMEOUT, follow_redirects=True, headers=dict(_UA)
        )


class TavilyProvider(WebSearchProvider):
    name = "tavily"

    @property
    def supports_images(self) -> bool:
        return True

    async def search(self, query, count=DEFAULT_COUNT, kind="web") -> SearchResponse:
        resp = SearchResponse(kind=kind, query=query, provider=self.name)
        key = self._cfg["tavily_key"]
        if not key:
            resp.error = "tavily key missing"
            return resp
        payload: Dict[str, Any] = {
            "api_key": key,
            "query": query,
            "max_results": _clamp_count(count),
            "search_depth": "advanced",
            "include_answer": True,
        }
        if kind == "news":
            payload["topic"] = "news"
        if kind == "images":
            payload["include_images"] = True
            payload["include_image_descriptions"] = True
        try:
            async with self._client() as client:
                r = await client.post(
                    "https://api.tavily.com/search",
                    json=payload,
                    headers={"Authorization": f"Bearer {key}"},
                )
                r.raise_for_status()
                data = r.json()
        except Exception as e:
            resp.error = f"tavily request failed: {e}"
            return resp

        resp.answer = str(data.get("answer") or "")
        for it in data.get("results", []) or []:
            resp.results.append(
                SearchResult(
                    title=str(it.get("title") or ""),
                    url=str(it.get("url") or ""),
                    snippet=str(it.get("content") or "")[:500],
                    content=str(it.get("raw_content") or it.get("content") or ""),
                    source=self.name,
                )
            )
        for im in data.get("images", []) or []:
            if isinstance(im, str):
                resp.images.append(ImageResult(image_url=im))
            elif isinstance(im, dict):
                resp.images.append(
                    ImageResult(
                        title=str(im.get("description") or ""),
                        image_url=str(im.get("url") or ""),
                    )
                )
        return resp


class BraveProvider(WebSearchProvider):
    name = "brave"

    @property
    def supports_images(self) -> bool:
        return True

    async def search(self, query, count=DEFAULT_COUNT, kind="web") -> SearchResponse:
        resp = SearchResponse(kind=kind, query=query, provider=self.name)
        key = self._cfg["brave_key"]
        if not key:
            resp.error = "brave key missing"
            return resp
        count = _clamp_count(count)
        if kind == "images":
            url = "https://api.search.brave.com/res/v1/images/search"
        elif kind == "news":
            url = "https://api.search.brave.com/res/v1/news/search"
        else:
            url = "https://api.search.brave.com/res/v1/web/search"
        headers = {"X-Subscription-Token": key, "Accept": "application/json"}
        try:
            async with self._client() as client:
                r = await client.get(
                    url, params={"q": query, "count": count}, headers=headers
                )
                r.raise_for_status()
                data = r.json()
        except Exception as e:
            resp.error = f"brave request failed: {e}"
            return resp

        if kind == "images":
            for it in data.get("results", []) or []:
                props = it.get("properties") or {}
                thumb = it.get("thumbnail") or {}
                resp.images.append(
                    ImageResult(
                        title=str(it.get("title") or ""),
                        image_url=str(props.get("url") or it.get("url") or ""),
                        thumbnail_url=str(thumb.get("src") or ""),
                        source_page=str(it.get("url") or ""),
                    )
                )
        elif kind == "news":
            for it in data.get("results", []) or []:
                resp.results.append(
                    SearchResult(
                        title=str(it.get("title") or ""),
                        url=str(it.get("url") or ""),
                        snippet=str(it.get("description") or ""),
                        published=str(it.get("age") or ""),
                        source=self.name,
                    )
                )
        else:
            web = (data.get("web") or {}).get("results") or []
            for it in web:
                resp.results.append(
                    SearchResult(
                        title=str(it.get("title") or ""),
                        url=str(it.get("url") or ""),
                        snippet=str(it.get("description") or ""),
                        source=self.name,
                    )
                )
        return resp


class SerpApiProvider(WebSearchProvider):
    name = "serpapi"

    @property
    def supports_images(self) -> bool:
        return True

    async def search(self, query, count=DEFAULT_COUNT, kind="web") -> SearchResponse:
        resp = SearchResponse(kind=kind, query=query, provider=self.name)
        key = self._cfg["serpapi_key"]
        if not key:
            resp.error = "serpapi key missing"
            return resp
        count = _clamp_count(count)
        params: Dict[str, Any] = {"api_key": key, "q": query}
        if kind == "images":
            params["engine"] = "google_images"
        elif kind == "news":
            params["engine"] = "google_news"
        else:
            params["engine"] = "google"
            params["num"] = count
        try:
            async with self._client() as client:
                r = await client.get("https://serpapi.com/search.json", params=params)
                r.raise_for_status()
                data = r.json()
        except Exception as e:
            resp.error = f"serpapi request failed: {e}"
            return resp

        if kind == "images":
            for it in (data.get("images_results") or [])[:count]:
                resp.images.append(
                    ImageResult(
                        title=str(it.get("title") or ""),
                        image_url=str(it.get("original") or it.get("thumbnail") or ""),
                        thumbnail_url=str(it.get("thumbnail") or ""),
                        source_page=str(it.get("link") or it.get("source") or ""),
                    )
                )
        elif kind == "news":
            for it in (data.get("news_results") or [])[:count]:
                resp.results.append(
                    SearchResult(
                        title=str(it.get("title") or ""),
                        url=str(it.get("link") or ""),
                        snippet=str(it.get("snippet") or ""),
                        published=str(it.get("date") or ""),
                        source=self.name,
                    )
                )
        else:
            for it in (data.get("organic_results") or [])[:count]:
                resp.results.append(
                    SearchResult(
                        title=str(it.get("title") or ""),
                        url=str(it.get("link") or ""),
                        snippet=str(it.get("snippet") or ""),
                        source=self.name,
                    )
                )
        return resp


class DuckDuckGoProvider(WebSearchProvider):
    """Keyless fallback. Web/news via the HTML endpoint, images via i.js."""

    name = "duckduckgo"

    @property
    def supports_images(self) -> bool:
        return True

    @staticmethod
    def _unwrap(href: str) -> str:
        if not href:
            return ""
        if href.startswith("//"):
            href = "https:" + href
        if "uddg=" in href:
            try:
                qs = urllib.parse.urlparse(href).query
                params = urllib.parse.parse_qs(qs)
                if params.get("uddg"):
                    return urllib.parse.unquote(params["uddg"][0])
            except Exception:
                return href
        return href

    async def search(self, query, count=DEFAULT_COUNT, kind="web") -> SearchResponse:
        resp = SearchResponse(kind=kind, query=query, provider=self.name)
        if not _HTTPX_AVAILABLE:
            resp.error = "httpx unavailable"
            return resp
        if kind == "images":
            return await self._image_search(query, _clamp_count(count), resp)
        return await self._html_search(query, _clamp_count(count), resp)

    async def _html_search(self, query, count, resp: SearchResponse) -> SearchResponse:
        if not _BS4_AVAILABLE:
            resp.error = "beautifulsoup4 unavailable for DuckDuckGo parsing"
            return resp
        try:
            async with self._client() as client:
                r = await client.post(
                    "https://html.duckduckgo.com/html/", data={"q": query}
                )
                r.raise_for_status()
                html = r.text
        except Exception as e:
            resp.error = f"duckduckgo request failed: {e}"
            return resp

        try:
            soup = BeautifulSoup(html, "html.parser")
            for node in soup.select("div.result")[: count * 2]:
                a = node.select_one("a.result__a")
                if not a:
                    continue
                url = self._unwrap(a.get("href", ""))
                if not url:
                    continue
                snip = node.select_one(".result__snippet")
                resp.results.append(
                    SearchResult(
                        title=a.get_text(strip=True),
                        url=url,
                        snippet=snip.get_text(" ", strip=True) if snip else "",
                        source=self.name,
                    )
                )
                if len(resp.results) >= count:
                    break
        except Exception as e:
            resp.error = f"duckduckgo parse failed: {e}"
        return resp

    async def _image_search(self, query, count, resp: SearchResponse) -> SearchResponse:
        try:
            async with self._client() as client:
                seed = await client.get(
                    "https://duckduckgo.com/",
                    params={"q": query, "iax": "images", "ia": "images"},
                )
                m = re.search(r'vqd=["\']?([\d-]+)["\']?', seed.text)
                if not m:
                    resp.error = "duckduckgo image token not found"
                    return resp
                vqd = m.group(1)
                r = await client.get(
                    "https://duckduckgo.com/i.js",
                    params={
                        "l": "us-en",
                        "o": "json",
                        "q": query,
                        "vqd": vqd,
                        "f": ",,,",
                        "p": "1",
                    },
                    headers={"Referer": "https://duckduckgo.com/"},
                )
                r.raise_for_status()
                data = r.json()
        except Exception as e:
            resp.error = f"duckduckgo image request failed: {e}"
            return resp

        for it in (data.get("results") or [])[:count]:
            resp.images.append(
                ImageResult(
                    title=str(it.get("title") or ""),
                    image_url=str(it.get("image") or ""),
                    thumbnail_url=str(it.get("thumbnail") or ""),
                    source_page=str(it.get("url") or ""),
                    width=int(it.get("width") or 0),
                    height=int(it.get("height") or 0),
                )
            )
        return resp


# ── Chain assembly + formatting ───────────────────────────────────────────


def build_provider_chain(config: Any) -> List[WebSearchProvider]:
    """Return providers to try in priority order for the given config."""
    c = _search_config(config)
    provider = c["provider"]
    chain: List[WebSearchProvider] = []

    def add_all_api() -> None:
        if c["tavily_key"]:
            chain.append(TavilyProvider(config))
        if c["brave_key"]:
            chain.append(BraveProvider(config))
        if c["serpapi_key"]:
            chain.append(SerpApiProvider(config))

    if provider == "tavily" and c["tavily_key"]:
        chain.append(TavilyProvider(config))
    elif provider == "brave" and c["brave_key"]:
        chain.append(BraveProvider(config))
    elif provider == "serpapi" and c["serpapi_key"]:
        chain.append(SerpApiProvider(config))
    elif provider in {"duckduckgo", "ddg"}:
        pass  # keyless-only; handled below
    elif provider == "scrape":
        return []  # loop.py will use the browser scrape fallback exclusively
    else:  # auto (or unknown/invalid) — use every configured API by priority
        add_all_api()

    # DuckDuckGo is always the keyless safety net unless "scrape" was forced.
    chain.append(DuckDuckGoProvider(config))
    return chain


def _truncate(text: str, limit: int) -> str:
    text = text or ""
    return text if len(text) <= limit else text[:limit].rstrip() + "…"


def format_search_response(resp: SearchResponse) -> str:
    """Render a normalized SearchResponse into compact markdown for the LLM."""
    if resp.kind == "images":
        header = f"**Image search:** {resp.query} (via {resp.provider}) — {len(resp.images)} images"
        lines = [header, ""]
        for i, im in enumerate(resp.images, 1):
            title = im.title or "image"
            dims = f" ({im.width}x{im.height})" if im.width and im.height else ""
            lines.append(f"{i}. {title}{dims}")
            lines.append(f"   Image URL: {im.image_url}")
            if im.source_page:
                lines.append(f"   Source: {im.source_page}")
        lines.append("")
        lines.append(
            "Tip: to deliver one to the user, call "
            "send_media(path='<Image URL>') — it will fetch and attach it."
        )
        return "\n".join(lines)

    label = "News search" if resp.kind == "news" else "Search"
    lines = [f"**{label}:** {resp.query} (via {resp.provider})", ""]
    if resp.answer:
        lines.append(f"**Direct answer:** {_truncate(resp.answer, 800)}")
        lines.append("")
    for i, r in enumerate(resp.results, 1):
        lines.append(f"{i}. {r.title or r.url}")
        lines.append(f"   URL: {r.url}")
        meta = _truncate(r.snippet, 300)
        if r.published:
            meta = f"({r.published}) {meta}".strip()
        if meta:
            lines.append(f"   {meta}")
    return "\n".join(lines)
