"""Asyncio compatibility helpers across supported Python versions."""

import sys


def configure_asyncio_runtime() -> None:
    """Keep asyncio on the platform default event loop policy.

    Windows already defaults to Proactor, so manually forcing
    WindowsProactorEventLoopPolicy() adds no value on Python 3.11-3.13 and
    becomes a deprecated code path on Python 3.14. This helper keeps one
    centralized hook for future compatibility tweaks without pinning LimeBot to
    a manual policy override.
    """

    if sys.platform == "win32":
        return
