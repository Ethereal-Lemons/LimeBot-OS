"""Host-side checks for tool calls whose arguments cannot be handled safely."""

from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, FrozenSet, Iterable, List, Optional
from urllib.parse import unquote, urlparse


IMAGE_FILE_EXTENSIONS: FrozenSet[str] = frozenset(
    {
        ".jpg",
        ".jpeg",
        ".jpe",
        ".jfif",
        ".png",
        ".gif",
        ".webp",
        ".bmp",
        ".tif",
        ".tiff",
        ".heic",
        ".heif",
        ".avif",
        ".ico",
    }
)
IMAGE_CONTENT_TYPES: FrozenSet[str] = frozenset(
    {
        "image/jpeg",
        "image/jpg",
        "image/pjpeg",
        "image/png",
        "image/gif",
        "image/webp",
        "image/bmp",
        "image/x-ms-bmp",
        "image/tiff",
        "image/heic",
        "image/heif",
        "image/avif",
        "image/x-icon",
        "image/vnd.microsoft.icon",
    }
)

_CHAT_UPLOAD_MARKERS = (
    "temp/discord_uploads/",
    "temp/web_uploads/",
    "temp\\discord_uploads\\",
    "temp\\web_uploads\\",
)
_JPEG_MAGIC = (b"\xff\xd8\xff",)
_PNG_MAGIC = (b"\x89PNG\r\n\x1a\n",)
_GIF_MAGIC = (b"GIF87a", b"GIF89a")
_WEBP_RIFF = b"RIFF"
_WEBP_TAG = b"WEBP"
_BMP_MAGIC = (b"BM",)


def _normalize_content_type(value: Any) -> str:
    raw = str(value or "").strip().lower()
    return raw.split(";", 1)[0].strip()


def _locator_path(value: str) -> str:
    raw = str(value or "").strip()
    if not raw:
        return ""
    if "://" in raw or raw.lower().startswith("data:"):
        parsed = urlparse(raw)
        return unquote(parsed.path or "")
    return raw.split("?", 1)[0].split("#", 1)[0]


def locator_suffix(value: str) -> str:
    """Return the file suffix of a path or URL, ignoring query strings."""
    path = _locator_path(value)
    if not path:
        return ""
    return Path(path).suffix.lower()


def is_image_content_type(value: Any) -> bool:
    normalized = _normalize_content_type(value)
    if not normalized:
        return False
    if normalized in IMAGE_CONTENT_TYPES:
        return True
    return normalized.startswith("image/") and normalized not in {
        "image/svg+xml",
    }


def is_image_locator(value: str, content_type: str = "") -> bool:
    """True when a path or URL names an image by suffix or content-type."""
    if is_image_content_type(content_type):
        return True
    raw = str(value or "").strip()
    if not raw:
        return False
    lowered = raw.lower()
    if lowered.startswith("data:image/") and not lowered.startswith("data:image/svg"):
        return True
    suffix = locator_suffix(raw)
    return suffix in IMAGE_FILE_EXTENSIONS


def turn_image_attachments(attachments: Optional[Iterable[Any]]) -> List[Dict[str, Any]]:
    images: List[Dict[str, Any]] = []
    for attachment in attachments or []:
        if not isinstance(attachment, dict):
            continue
        kind = str(attachment.get("kind") or "").strip().lower()
        mime = attachment.get("mime_type") or attachment.get("mimeType") or ""
        name = str(attachment.get("name") or attachment.get("filename") or "")
        path = str(attachment.get("path") or "")
        url = str(attachment.get("url") or "")
        if (
            kind == "image"
            or is_image_content_type(mime)
            or is_image_locator(name, mime)
            or is_image_locator(path, mime)
            or is_image_locator(url, mime)
        ):
            images.append(attachment)
    return images


def turn_has_image_attachments(attachments: Optional[Iterable[Any]]) -> bool:
    return bool(turn_image_attachments(attachments))


def _is_chat_upload_image_path(path: str, attachments: Optional[Iterable[Any]] = None) -> bool:
    raw = str(path or "").strip().replace("\\", "/")
    if not raw:
        return False
    lowered = raw.lower()
    if any(marker.replace("\\", "/") in lowered for marker in _CHAT_UPLOAD_MARKERS):
        if is_image_locator(raw):
            return True
        for attachment in turn_image_attachments(attachments):
            attached = str(attachment.get("path") or "").replace("\\", "/")
            if attached and (attached in raw or raw.endswith(attached)):
                return True
    for attachment in turn_image_attachments(attachments):
        attached = str(attachment.get("path") or "").replace("\\", "/")
        if attached and (raw == attached or raw.endswith(attached)):
            return True
    return False


def _sniff_image_magic(path: str) -> bool:
    candidate = Path(str(path or "").strip())
    if not candidate.is_file():
        return False
    try:
        header = candidate.read_bytes()[:16]
    except OSError:
        return False
    if not header:
        return False
    if any(header.startswith(magic) for magic in _JPEG_MAGIC):
        return True
    if any(header.startswith(magic) for magic in _PNG_MAGIC):
        return True
    if any(header.startswith(magic) for magic in _GIF_MAGIC):
        return True
    if any(header.startswith(magic) for magic in _BMP_MAGIC):
        return True
    if header.startswith(_WEBP_RIFF) and _WEBP_TAG in header:
        return True
    return False


def is_image_read_target(
    path: str,
    *,
    content_type: str = "",
    attachments: Optional[Iterable[Any]] = None,
) -> bool:
    raw = str(path or "").strip()
    if not raw:
        return False
    if is_image_locator(raw, content_type):
        return True
    if _is_chat_upload_image_path(raw, attachments):
        return True
    return _sniff_image_magic(raw)


def is_image_navigate_target(
    url: str,
    *,
    content_type: str = "",
    attachments: Optional[Iterable[Any]] = None,
) -> bool:
    raw = str(url or "").strip()
    if not raw:
        return False
    if is_image_locator(raw, content_type):
        return True
    for attachment in turn_image_attachments(attachments):
        attached_url = str(attachment.get("url") or "").strip()
        attached_type = str(
            attachment.get("mime_type") or attachment.get("mimeType") or ""
        )
        if attached_url and raw.split("?", 1)[0] == attached_url.split("?", 1)[0]:
            return True
        if attached_url == raw:
            return True
        if is_image_content_type(attached_type) and attached_url and attached_url in raw:
            return True
    return False


def refuse_read_file_image(
    path: str,
    *,
    content_type: str = "",
    attachments: Optional[Iterable[Any]] = None,
) -> Optional[str]:
    if not is_image_read_target(
        path, content_type=content_type, attachments=attachments
    ):
        return None
    return (
        "Error: read_file cannot dump image bytes (jpeg/png/gif/webp and similar). "
        "Chat-attached photos are already in vision context — inspect them directly. "
        "Do not call browser_navigate on the image URL. "
        "Use web_search(kind=\"images\") only if the user asked you to find a "
        "different public photo."
    )


def refuse_browser_navigate_image(
    url: str,
    *,
    content_type: str = "",
    attachments: Optional[Iterable[Any]] = None,
) -> Optional[str]:
    if not is_image_navigate_target(
        url, content_type=content_type, attachments=attachments
    ):
        return None
    return (
        "Error: browser_navigate cannot open image URLs. "
        "This target is an image (path or content-type), not a webpage. "
        "Chat-attached photos are already in vision context. "
        "Use web_search(kind=\"images\") to find a public photo, or "
        "browser_navigate only for an HTML page."
    )


def refuse_illegal_tool_call(
    function_name: str,
    function_args: Optional[Dict[str, Any]] = None,
    *,
    attachments: Optional[Iterable[Any]] = None,
    user_text: str = "",
) -> Optional[str]:
    """Return a recoverable Error string, or None if the host may execute."""
    name = str(function_name or "").strip()
    args = dict(function_args or {})
    if name == "read_file":
        path = str(
            args.get("path") or args.get("file") or args.get("filename") or ""
        ).strip()
        content_type = str(args.get("content_type") or args.get("mime_type") or "")
        return refuse_read_file_image(
            path, content_type=content_type, attachments=attachments
        )
    if name == "browser_navigate":
        url = str(args.get("url") or args.get("path") or args.get("href") or "").strip()
        content_type = str(args.get("content_type") or args.get("mime_type") or "")
        return refuse_browser_navigate_image(
            url, content_type=content_type, attachments=attachments
        )
    return None
