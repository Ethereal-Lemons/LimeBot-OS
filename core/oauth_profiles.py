"""Helpers for reading local OAuth profile state without exposing secrets."""

from __future__ import annotations

import json
import shutil
import subprocess
from functools import lru_cache
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
import time


OAUTH_PROFILES_PATH = Path.cwd() / "data" / "oauth_profiles.json"
CODEX_PROVIDER_ID = "openai-codex"
_CODEX_API_KEY_CACHE: dict[str, Any] = {"value": None, "expires_at": 0.0}


def oauth_profiles_path() -> Path:
    return OAUTH_PROFILES_PATH


def load_oauth_profiles() -> dict[str, Any]:
    try:
        if not OAUTH_PROFILES_PATH.exists():
            return {"version": 1, "providers": {}}
        parsed = json.loads(OAUTH_PROFILES_PATH.read_text(encoding="utf-8"))
        if not isinstance(parsed, dict):
            return {"version": 1, "providers": {}}
        providers = parsed.get("providers")
        if not isinstance(providers, dict):
            parsed["providers"] = {}
        return parsed
    except Exception:
        return {"version": 1, "providers": {}}


def _summarize_profile(entry: dict[str, Any] | None) -> dict[str, Any]:
    credential = entry.get("credential") if isinstance(entry, dict) else None
    if not isinstance(credential, dict):
        credential = {}

    expires_raw = credential.get("expires")
    try:
        expires_unix = int(expires_raw)
    except (TypeError, ValueError):
        expires_unix = 0
    if expires_unix > 100000000000:
        expires_unix = expires_unix // 1000

    expires_at = None
    expired = False
    if expires_unix > 0:
        try:
            expires_dt = datetime.fromtimestamp(expires_unix, tz=timezone.utc)
            expires_at = expires_dt.isoformat().replace("+00:00", "Z")
            expired = expires_dt <= datetime.now(timezone.utc)
        except (OverflowError, OSError, ValueError):
            expires_at = None
            expired = False

    def _clean(value: Any) -> str | None:
        text = str(value or "").strip()
        return text or None

    return {
        "configured": bool(_clean(credential.get("access")) and _clean(credential.get("refresh"))),
        "provider": CODEX_PROVIDER_ID,
        "email": _clean(credential.get("email")),
        "displayName": _clean(credential.get("displayName")),
        "accountId": _clean(credential.get("accountId")),
        "source": _clean(entry.get("source")) if isinstance(entry, dict) else None,
        "importedFrom": _clean(entry.get("importedFrom")) if isinstance(entry, dict) else None,
        "updatedAt": _clean(entry.get("updatedAt")) if isinstance(entry, dict) else None,
        "expiresAt": expires_at,
        "expired": expired,
        "storePath": str(OAUTH_PROFILES_PATH),
    }


def get_codex_oauth_status() -> dict[str, Any]:
    providers = load_oauth_profiles().get("providers", {})
    entry = providers.get(CODEX_PROVIDER_ID) if isinstance(providers, dict) else None
    if not isinstance(entry, dict):
        entry = None
    return _summarize_profile(entry)


@lru_cache(maxsize=1)
def _node_executable() -> str:
    return shutil.which("node") or "node"


def resolve_codex_oauth_api_key() -> str:
    """Resolve a usable Codex OAuth API key via the local Node helper."""
    now = time.time()
    cached_value = _CODEX_API_KEY_CACHE.get("value")
    cache_expires_at = float(_CODEX_API_KEY_CACHE.get("expires_at") or 0.0)
    if isinstance(cached_value, str) and cached_value and cache_expires_at > now:
        return cached_value

    script_path = Path.cwd() / "scripts" / "codex-oauth.mjs"
    if not script_path.exists():
        raise RuntimeError(f"Codex auth helper not found at {script_path}")

    proc = subprocess.run(
        [_node_executable(), str(script_path), "get-api-key", "--json"],
        cwd=Path.cwd(),
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=False,
    )
    stdout = (proc.stdout or "").strip()
    stderr = (proc.stderr or "").strip()
    if proc.returncode != 0:
        detail = stderr or stdout or "unknown Codex auth failure"
        raise RuntimeError(detail)

    try:
        payload = json.loads(stdout)
    except Exception as exc:  # pragma: no cover - defensive
        raise RuntimeError("Codex auth helper returned invalid JSON.") from exc

    api_key = str(payload.get("apiKey") or "").strip()
    if not api_key:
        raise RuntimeError("Codex auth helper did not return an API key.")
    _CODEX_API_KEY_CACHE["value"] = api_key
    _CODEX_API_KEY_CACHE["expires_at"] = now + 60.0
    return api_key
