"""SSRF-guarded public HTTP fetching for agent-owned tool workflows."""

from __future__ import annotations

import ipaddress
import mimetypes
import re
import socket
import urllib.parse
from pathlib import Path
from typing import Dict, Optional


class SafeHttpFetcher:
    """Fetch bounded HTTP responses after validating every destination hop."""

    MAX_DOWNLOAD_BYTES = 15 * 1024 * 1024
    _USER_AGENT = (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
    )

    @staticmethod
    def is_safe_public_url(url: str) -> tuple[bool, str]:
        """Allow only http(s) URLs whose resolved addresses are public."""
        url = str(url or "").strip()
        if not url:
            return False, "Empty URL."
        try:
            parsed = urllib.parse.urlparse(url)
        except Exception:
            return False, "Invalid URL."
        if parsed.scheme not in ("http", "https"):
            return False, f"Only http(s) URLs are allowed (got '{parsed.scheme or 'none'}')."
        host = parsed.hostname
        if not host:
            return False, "URL has no host."
        try:
            port = parsed.port or (443 if parsed.scheme == "https" else 80)
        except ValueError:
            return False, "Invalid port in URL."
        try:
            infos = socket.getaddrinfo(host, port, proto=socket.IPPROTO_TCP)
        except Exception as exc:
            return False, f"Could not resolve host '{host}': {exc}"
        for info in infos:
            ip_str = info[4][0]
            try:
                ip = ipaddress.ip_address(ip_str)
            except ValueError:
                return False, f"Invalid resolved address for '{host}'."
            if (
                ip.is_private
                or ip.is_loopback
                or ip.is_link_local
                or ip.is_reserved
                or ip.is_multicast
                or ip.is_unspecified
            ):
                return False, (
                    f"Refusing to fetch a non-public address ({ip_str}) for host '{host}'."
                )
        return True, ""

    async def fetch(
        self,
        url: str,
        *,
        max_bytes: int,
        timeout: float,
        extra_headers: Optional[Dict[str, str]] = None,
    ) -> tuple[str, str, bytes]:
        """Return final URL, content type, and body after guarded redirects."""
        try:
            import httpx
        except Exception as exc:  # pragma: no cover - httpx is a core dep
            raise ValueError(f"httpx is required for URL fetching: {exc}")

        headers = {
            "User-Agent": self._USER_AGENT,
            "Accept": "*/*",
            "Accept-Language": "en-US,en;q=0.9",
            "Referer": "https://www.google.com/",
        }
        if extra_headers:
            headers.update({str(k): str(v) for k, v in extra_headers.items() if v})
        current = url
        async with httpx.AsyncClient(
            timeout=timeout, follow_redirects=False, headers=headers
        ) as client:
            for _ in range(6):
                ok, reason = self.is_safe_public_url(current)
                if not ok:
                    raise ValueError(reason)
                async with client.stream("GET", current) as response:
                    if response.is_redirect:
                        location = response.headers.get("location", "")
                        if not location:
                            raise ValueError("Redirect without a location header.")
                        current = urllib.parse.urljoin(current, location)
                        continue
                    response.raise_for_status()
                    content_type = (
                        (response.headers.get("content-type") or "")
                        .split(";", 1)[0]
                        .strip()
                        .lower()
                    )
                    body = bytearray()
                    total = 0
                    async for chunk in response.aiter_bytes():
                        total += len(chunk)
                        if total > max_bytes:
                            raise ValueError(
                                f"Response exceeds the {max_bytes // (1024 * 1024)}MB limit."
                            )
                        body.extend(chunk)
                    return current, content_type, bytes(body)
        raise ValueError("Too many redirects.")

    @staticmethod
    def guess_download_extension(data: bytes, content_type: str, url: str) -> str:
        """Pick a file extension from magic bytes, content type, then URL."""
        if data[:3] == b"\xff\xd8\xff":
            return ".jpg"
        if data[:8] == b"\x89PNG\r\n\x1a\n":
            return ".png"
        if data[:4] == b"GIF8":
            return ".gif"
        if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
            return ".webp"
        if data[:4] == b"%PDF":
            return ".pdf"
        if content_type:
            ext = mimetypes.guess_extension(content_type)
            if ext:
                return ".jpg" if ext == ".jpe" else ext
        suffix = Path(urllib.parse.urlparse(url).path).suffix
        if suffix and len(suffix) <= 6 and re.fullmatch(r"\.[A-Za-z0-9]+", suffix):
            return suffix
        return ".bin"
