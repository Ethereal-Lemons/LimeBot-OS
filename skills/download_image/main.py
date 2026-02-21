"""
Image Downloader - Downloads images from direct URLs or extracts them from pages.

Usage:
    python main.py <url> <save_path> [--max-mb 20]
"""

import asyncio
import json
import re
import sys
from pathlib import Path


project_root = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(project_root))


_DIRECT_EXTENSIONS = {
    ".jpg",
    ".jpeg",
    ".png",
    ".webp",
    ".gif",
    ".avif",
    ".svg",
    ".bmp",
    ".tiff",
}
_ALLOWED_CONTENT_TYPES = {
    "image/jpeg",
    "image/png",
    "image/webp",
    "image/gif",
    "image/avif",
    "image/svg+xml",
    "image/bmp",
    "image/tiff",
}
_DEFAULT_MAX_BYTES = 20 * 1024 * 1024


_SSRF_BLOCKED_PREFIXES = (
    "http://localhost",
    "https://localhost",
    "http://127.",
    "https://127.",
    "http://0.",
    "https://0.",
    "http://10.",
    "https://10.",
    "http://192.168.",
    "https://192.168.",
    "http://169.254.",
    "https://169.254.",
    "http://[::1]",
    "https://[::1]",
)

_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/122.0.0.0 Safari/537.36"
    ),
    "Accept": "image/avif,image/webp,image/apng,image/svg+xml,image/*,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9",
    "Referer": "https://www.google.com/",
}


_PINTEREST_SIZE_TOKENS = ["/170x/", "/236x/", "/474x/", "/736x/"]


def _check_ssrf(url: str) -> None:
    """Raise ValueError if the URL targets a private/internal address."""
    lower = url.lower()
    for blocked in _SSRF_BLOCKED_PREFIXES:
        if lower.startswith(blocked):
            raise ValueError(f"Blocked URL targeting internal network: {url}")


def _is_direct_image_url(url: str) -> bool:
    suffix = Path(url.split("?")[0]).suffix.lower()
    return suffix in _DIRECT_EXTENSIONS


def _og_image(html: str) -> str | None:
    """Extract og:image or twitter:image meta tag content."""
    for pattern in (
        r'property=["\']og:image["\']\s+content=["\'](.*?)["\']',
        r'content=["\'](.*?)["\']\s+property=["\']og:image["\']',
        r'name=["\']twitter:image["\']\s+content=["\'](.*?)["\']',
    ):
        m = re.search(pattern, html)
        if m:
            return m.group(1)
    return None


def _pinterest_to_original(url: str) -> str:
    for token in _PINTEREST_SIZE_TOKENS:
        url = url.replace(token, "/originals/")
    return url


def _extract_image_url(url: str, html: str) -> str | None:
    """
    Site-aware extraction of the best image URL from a page's HTML.
    Returns the extracted URL or None if nothing useful was found.
    """
    extracted: str | None = None

    if "reddit.com" in url:
        extracted = _og_image(html)

    elif "pinterest.com" in url or "pin.it" in url:
        extracted = _og_image(html)
        if extracted:
            extracted = _pinterest_to_original(extracted)

        if not extracted:
            m = re.search(
                r'<script type="application/ld\+json">(.*?)</script>', html, re.DOTALL
            )
            if m:
                try:
                    data = json.loads(m.group(1))
                    img = data.get("image") if isinstance(data, dict) else None
                    if isinstance(img, list):
                        img = img[0]
                    if isinstance(img, str):
                        extracted = _pinterest_to_original(img)
                except (json.JSONDecodeError, AttributeError):
                    pass

    elif "pinimg.com" in url:
        extracted = _pinterest_to_original(url)

    elif "wikimedia.org" in url or "wikipedia.org" in url:
        m = re.search(r'class="internal"\s+href="(//[^"]+)"', html)
        if m:
            extracted = "https:" + m.group(1)

    if not extracted:
        extracted = _og_image(html)

    return extracted


async def download_image(
    url: str,
    save_path: str,
    max_bytes: int = _DEFAULT_MAX_BYTES,
) -> dict:
    """
    Download an image from a URL (direct or page) and save it to disk.

    Args:
        url:       Direct image URL or page URL containing an image.
        save_path: Where to save the file. Relative paths are anchored to project root.
        max_bytes: Maximum file size allowed (default 20 MB).

    Returns:
        dict with 'status', and either 'path'/'size'/'url' or 'message'.
    """
    import httpx

    try:
        _check_ssrf(url)
    except ValueError as e:
        return {"status": "error", "message": str(e), "url": url}

    save = Path(save_path)
    if not save.is_absolute():
        save = project_root / save
    save.parent.mkdir(parents=True, exist_ok=True)

    async with httpx.AsyncClient(
        follow_redirects=True, timeout=30.0, verify=True
    ) as client:
        try:
            target_url = url

            if not _is_direct_image_url(url):
                page_res = await client.get(url, headers=_HEADERS)
                page_res.raise_for_status()

                extracted = _extract_image_url(url, page_res.text)
                if extracted:
                    try:
                        _check_ssrf(extracted)
                        target_url = extracted
                    except ValueError:
                        return {
                            "status": "error",
                            "message": f"Extracted URL blocked (SSRF): {extracted}",
                            "url": url,
                        }

            response = await client.get(target_url, headers=_HEADERS)
            response.raise_for_status()

            content_type = (
                response.headers.get("content-type", "").split(";")[0].strip().lower()
            )
            if content_type and content_type not in _ALLOWED_CONTENT_TYPES:
                return {
                    "status": "error",
                    "message": f"Unexpected content type '{content_type}' — not an image.",
                    "url": target_url,
                }

            content_length = int(response.headers.get("content-length", 0))
            if content_length > max_bytes:
                return {
                    "status": "error",
                    "message": f"File too large: {content_length / 1024 / 1024:.1f} MB (limit {max_bytes / 1024 / 1024:.0f} MB).",
                    "url": target_url,
                }

            content = response.content
            if len(content) > max_bytes:
                return {
                    "status": "error",
                    "message": f"Downloaded content too large: {len(content) / 1024 / 1024:.1f} MB.",
                    "url": target_url,
                }

            lower_start = content[:100].lower()
            if b"<html" in lower_start or b"<!doctype" in lower_start:
                return {
                    "status": "error",
                    "message": "Response appears to be HTML, not an image. Could not extract a direct image URL.",
                    "url": target_url,
                }

            save.write_bytes(content)

            return {
                "status": "success",
                "path": str(
                    save.relative_to(project_root)
                    if save.is_relative_to(project_root)
                    else save
                ),
                "size": len(content),
                "content_type": content_type or "unknown",
                "url": target_url,
            }

        except httpx.HTTPStatusError as e:
            return {
                "status": "error",
                "message": f"HTTP {e.response.status_code}: {e.request.url}",
                "url": url,
            }
        except httpx.RequestError as e:
            return {"status": "error", "message": f"Request failed: {e}", "url": url}
        except Exception as e:
            return {"status": "error", "message": str(e), "url": url}


if __name__ == "__main__":
    args = sys.argv[1:]

    if len(args) < 2:
        print(
            json.dumps(
                {
                    "status": "error",
                    "message": "Usage: python main.py <url> <save_path> [--max-mb N]",
                }
            )
        )
        sys.exit(1)

    input_url = args[0]
    input_path = args[1]
    max_mb = _DEFAULT_MAX_BYTES

    if "--max-mb" in args:
        idx = args.index("--max-mb")
        try:
            max_mb = int(args[idx + 1]) * 1024 * 1024
        except (IndexError, ValueError):
            print(json.dumps({"status": "error", "message": "Invalid --max-mb value"}))
            sys.exit(1)

    result = asyncio.run(download_image(input_url, input_path, max_bytes=max_mb))
    print(json.dumps(result))
    sys.exit(0 if result["status"] == "success" else 1)
