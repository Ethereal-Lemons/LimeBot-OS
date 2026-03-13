"""Windows asyncio compatibility helpers."""

from __future__ import annotations

import asyncio
import sys
from typing import Any

from loguru import logger

_PROACTOR_CALLBACK = "_ProactorBasePipeTransport._call_connection_lost"
_SUPPRESSED_WINERRORS = frozenset({10038})


def _is_windows() -> bool:
    return sys.platform == "win32"


def _context_mentions_proactor_close(context: dict[str, Any]) -> bool:
    message = str(context.get("message") or "")
    if _PROACTOR_CALLBACK in message:
        return True

    handle = context.get("handle")
    callback = getattr(handle, "_callback", None)
    qualname = getattr(callback, "__qualname__", "")
    if _PROACTOR_CALLBACK in str(qualname):
        return True

    transport = context.get("transport")
    transport_name = type(transport).__name__ if transport is not None else ""
    return transport_name == "_ProactorBasePipeTransport"


def should_suppress_windows_proactor_error(context: dict[str, Any]) -> bool:
    """Return True for benign Windows proactor transport shutdown noise."""
    if not _is_windows():
        return False

    exc = context.get("exception")
    if not isinstance(exc, OSError):
        return False

    winerror = getattr(exc, "winerror", None)
    if winerror not in _SUPPRESSED_WINERRORS:
        return False

    return _context_mentions_proactor_close(context)


def install_windows_asyncio_exception_filter(
    loop: asyncio.AbstractEventLoop,
) -> None:
    """Suppress noisy but harmless Windows proactor transport close errors."""
    if not _is_windows():
        return

    if getattr(loop, "_limebot_windows_filter_installed", False):
        return

    previous_handler = loop.get_exception_handler()

    def _handler(
        current_loop: asyncio.AbstractEventLoop, context: dict[str, Any]
    ) -> None:
        if should_suppress_windows_proactor_error(context):
            exc = context.get("exception")
            logger.debug(
                f"Suppressed benign Windows asyncio transport shutdown error: {exc}"
            )
            return

        if previous_handler is not None:
            previous_handler(current_loop, context)
            return

        current_loop.default_exception_handler(context)

    loop.set_exception_handler(_handler)
    setattr(loop, "_limebot_windows_filter_installed", True)
