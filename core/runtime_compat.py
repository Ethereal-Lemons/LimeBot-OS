"""Runtime compatibility helpers."""

from __future__ import annotations

import sys
from typing import Optional, Sequence

_MIN_PYTHON = (3, 11)
_MAX_PYTHON = (3, 14)


def describe_supported_python(platform: Optional[str] = None) -> str:
    return "Python 3.11 to 3.14"


def is_supported_python_runtime(
    version_info: Optional[Sequence[int]] = None,
    platform: Optional[str] = None,
) -> bool:
    target_platform = platform or sys.platform
    info = tuple(version_info or sys.version_info)
    current = info[:2]

    if current < _MIN_PYTHON:
        return False
    if current > _MAX_PYTHON:
        return False
    return True


def get_unsupported_python_message(
    version_info: Optional[Sequence[int]] = None,
    platform: Optional[str] = None,
) -> str:
    target_platform = platform or sys.platform
    info = tuple(version_info or sys.version_info)
    version = ".".join(str(part) for part in info[:3])
    message = (
        f"LimeBot does not support Python {version} on "
        f"{'Windows' if target_platform == 'win32' else target_platform}. "
        f"Expected {describe_supported_python(target_platform)}."
    )
    return message


def enforce_supported_python_runtime(
    version_info: Optional[Sequence[int]] = None,
    platform: Optional[str] = None,
) -> None:
    if is_supported_python_runtime(version_info=version_info, platform=platform):
        return
    raise RuntimeError(
        get_unsupported_python_message(
            version_info=version_info,
            platform=platform,
        )
    )
