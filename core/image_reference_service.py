"""Resolve, validate, and load images used as generation references."""

from __future__ import annotations

import asyncio
import base64
import mimetypes
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple

_MAX_IMAGE_REFERENCE_BYTES = 50 * 1024 * 1024
_MAX_IMAGE_REFERENCES = 4
_IMAGE_REFERENCE_EXTENSIONS = frozenset({".png", ".jpg", ".jpeg", ".webp"})


class ImageReferenceService:
    """Collect explicit or attached references after outbound path checks."""

    def __init__(
        self,
        *,
        is_path_shareable: Callable[
            [str], Tuple[bool, Optional[str], Optional[Path]]
        ],
    ) -> None:
        self._is_path_shareable = is_path_shareable

    async def resolve_references(
        self,
        reference_images: Optional[List[str]],
        use_attached_images: Optional[bool],
    ) -> tuple[List[Dict[str, Any]], bool]:
        from core.context import tool_context

        explicit = (
            [reference_images]
            if isinstance(reference_images, str)
            else list(reference_images or [])
        )
        context = tool_context.get() or {}
        should_use_attached = (
            bool(context.get("auto_reference_images"))
            if use_attached_images is None
            else bool(use_attached_images)
        )
        candidates = [str(path or "").strip() for path in explicit]
        if should_use_attached:
            attachments = [
                *(context.get("attachments") or []),
                *(context.get("recent_image_attachments") or []),
            ]
            for attachment in attachments:
                if not isinstance(attachment, dict):
                    continue
                path = str(attachment.get("path") or "").strip()
                if path:
                    candidates.append(path)

        reference_requested = bool(explicit) or should_use_attached
        references: List[Dict[str, Any]] = []
        seen: set[Path] = set()
        for candidate in candidates:
            if not candidate:
                continue
            allowed, reason, path = self._is_path_shareable(candidate)
            if not allowed or path is None:
                if explicit:
                    raise ValueError(
                        reason or f"Reference image is unavailable: {candidate}"
                    )
                continue
            if path in seen:
                continue
            if path.suffix.lower() not in _IMAGE_REFERENCE_EXTENSIONS:
                raise ValueError(
                    f"Unsupported reference image format '{path.suffix or 'unknown'}'. "
                    "Use PNG, JPEG, or WebP."
                )
            size = path.stat().st_size
            if size <= 0 or size > _MAX_IMAGE_REFERENCE_BYTES:
                raise ValueError(
                    f"Reference image '{path.name}' must be between 1 byte and 50 MiB."
                )
            blob = await asyncio.to_thread(path.read_bytes)
            mime_type = mimetypes.guess_type(path.name)[0] or "image/png"
            references.append(
                {
                    "name": path.name,
                    "path": path,
                    "mime_type": mime_type,
                    "bytes": blob,
                    "data_url": (
                        f"data:{mime_type};base64,"
                        f"{base64.b64encode(blob).decode('ascii')}"
                    ),
                }
            )
            seen.add(path)
            if len(references) >= _MAX_IMAGE_REFERENCES:
                break

        return references, reference_requested
