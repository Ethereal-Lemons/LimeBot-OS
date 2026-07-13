"""Video runtime dependency discovery."""

from __future__ import annotations

import os
import shutil
from functools import lru_cache


def _merge_windows_path(current: str, *persisted_values: str) -> str:
    result: list[str] = []
    seen: set[str] = set()
    for value in (current, *persisted_values):
        for entry in str(value or "").split(";"):
            entry = entry.strip()
            if not entry:
                continue
            normalized = entry.rstrip("\\/").casefold()
            if normalized in seen:
                continue
            seen.add(normalized)
            result.append(entry)
    return ";".join(result)


@lru_cache(maxsize=1)
def refresh_windows_path() -> str:
    """Merge persisted Windows PATH values into a possibly stale process."""

    current = os.environ.get("PATH", "")
    if os.name != "nt":
        return current

    persisted: list[str] = []
    try:
        import winreg

        locations = (
            (winreg.HKEY_LOCAL_MACHINE, r"SYSTEM\CurrentControlSet\Control\Session Manager\Environment"),
            (winreg.HKEY_CURRENT_USER, r"Environment"),
        )
        for hive, key_name in locations:
            try:
                with winreg.OpenKey(hive, key_name) as key:
                    value, _kind = winreg.QueryValueEx(key, "Path")
                    persisted.append(os.path.expandvars(str(value)))
            except OSError:
                continue
    except (ImportError, OSError):
        return current

    refreshed = _merge_windows_path(current, *persisted)
    os.environ["PATH"] = refreshed
    return refreshed


def resolve_video_binary(name: str) -> str | None:
    executable = shutil.which(name)
    if executable:
        return executable

    # Retry the registry read when a long-running backend previously checked
    # before the installer updated PATH.
    refresh_windows_path.cache_clear()
    refresh_windows_path()
    return shutil.which(name)
