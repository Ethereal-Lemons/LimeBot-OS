"""Safe URL download and readable-page workflows for Toolbox."""

from __future__ import annotations

import asyncio
import re
import uuid
from pathlib import Path
from typing import Any, Callable, Dict, Optional

from core.safe_http_fetch import SafeHttpFetcher


class UrlToolService:
    """Own URL validation, bounded fetches, and local download presentation."""

    def __init__(
        self,
        *,
        get_fetcher: Callable[[], SafeHttpFetcher],
        download_directory: Callable[[], Path],
        display_path: Callable[[Path], str],
        send_progress: Callable[[str], Any],
    ) -> None:
        self._get_fetcher = get_fetcher
        self._download_directory = download_directory
        self._display_path = display_path
        self._send_progress = send_progress

    async def fetch_url_to_temp(
        self,
        url: str,
        max_bytes: int = SafeHttpFetcher.MAX_DOWNLOAD_BYTES,
        *,
        referer: str = "",
        user_agent: str = "",
        extra_headers: Optional[Dict[str, str]] = None,
    ) -> str:
        url = str(url or "").strip()
        fetcher = self._get_fetcher()
        ok, reason = fetcher.is_safe_public_url(url)
        if not ok:
            return f"Error: {reason}"

        await self._send_progress(f"⬇️ Downloading: {url}")
        dest_dir = self._download_directory()
        try:
            await asyncio.to_thread(lambda: dest_dir.mkdir(parents=True, exist_ok=True))
        except Exception as exc:
            return f"Error: Could not create download directory: {exc}"

        headers: Dict[str, str] = dict(extra_headers or {})
        if referer:
            headers["Referer"] = referer
        if user_agent:
            headers["User-Agent"] = user_agent
        try:
            final_url, content_type, data = await fetcher.fetch(
                url, max_bytes=max_bytes, timeout=30.0, extra_headers=headers or None
            )
        except ValueError as exc:
            return f"Error: {exc}"
        except Exception as exc:
            return f"Error: Download failed: {exc}"

        if not data:
            return "Error: Downloaded file was empty."

        extension = fetcher.guess_download_extension(data, content_type, final_url)
        destination = dest_dir / f"dl_{uuid.uuid4().hex[:12]}{extension}"
        try:
            await asyncio.to_thread(destination.write_bytes, data)
        except Exception as exc:
            return f"Error: Could not save file: {exc}"
        return self._display_path(destination)

    async def fetch_readable_text(self, url: str, max_chars: int = 4000) -> str:
        url = str(url or "").strip()
        fetcher = self._get_fetcher()
        ok, reason = fetcher.is_safe_public_url(url)
        if not ok:
            return f"Error: {reason}"
        try:
            _final_url, content_type, data = await fetcher.fetch(
                url, max_bytes=5 * 1024 * 1024, timeout=20.0
            )
        except ValueError as exc:
            return f"Error: {exc}"
        except Exception as exc:
            return f"Error: Fetch failed: {exc}"

        text = data.decode("utf-8", errors="ignore")
        if "html" in content_type or "<html" in text[:2000].lower():
            text = self._html_to_text(text)
        text = text.strip()
        if not text:
            return "Error: No readable text found at that URL."
        if len(text) > max_chars:
            text = text[:max_chars] + f"\n... (truncated at {max_chars} chars)"
        return text

    @staticmethod
    def _html_to_text(html: str) -> str:
        try:
            from bs4 import BeautifulSoup
        except Exception:
            return re.sub(r"<[^>]+>", " ", html)
        soup = BeautifulSoup(html, "html.parser")
        for tag in soup(
            ["script", "style", "noscript", "nav", "footer", "header", "svg", "form"]
        ):
            tag.decompose()
        text = soup.get_text("\n", strip=True)
        return re.sub(r"\n{3,}", "\n\n", text)
