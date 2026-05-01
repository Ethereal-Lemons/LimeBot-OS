"""
BrowserSessionManager — metadata registry for browser sessions.

Tracks browser profile lifecycle so the operator dashboard can see which
sessions exist, their mode, owner, and status without inspecting the filesystem.
"""

import asyncio
import json
import shutil
import time
import uuid
from dataclasses import asdict, dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any, Dict, List, Optional

from loguru import logger


class SessionStatus(str, Enum):
    ALIVE = "alive"
    STALE = "stale"
    CLOSED = "closed"


_STALE_THRESHOLD_SECONDS = 300  # 5 minutes without activity → stale


@dataclass
class BrowserSession:
    profile_id: str
    mode: str = "isolated"
    session_key: str = ""
    channel: str = ""
    owner_chat_id: str = ""
    created_at: float = 0.0
    last_used_at: float = 0.0
    status: str = SessionStatus.ALIVE.value
    display_name: str = ""
    user_data_dir: str = ""
    cdp_url: str = ""
    metadata: Dict[str, Any] = field(default_factory=dict)


class BrowserSessionManager:
    """Registry for browser session metadata with JSON persistence."""

    def __init__(self, data_dir: str = "data"):
        self._data_file = Path(data_dir) / "browser_sessions.json"
        self._sessions: Dict[str, BrowserSession] = {}
        self._lock = asyncio.Lock()
        self._load()

    def _load(self) -> None:
        if not self._data_file.exists():
            return
        try:
            raw = json.loads(self._data_file.read_text(encoding="utf-8"))
            for item in raw.get("sessions", []):
                session = BrowserSession(**{
                    k: v for k, v in item.items()
                    if k in BrowserSession.__dataclass_fields__
                })
                self._sessions[session.profile_id] = session
            logger.info(f"BrowserSessionManager: loaded {len(self._sessions)} sessions.")
        except Exception as e:
            logger.error(f"BrowserSessionManager: failed to load: {e}")

    def _save_sync(self) -> None:
        try:
            self._data_file.parent.mkdir(parents=True, exist_ok=True)
            snapshot = {"sessions": [asdict(s) for s in self._sessions.values()]}
            self._data_file.write_text(
                json.dumps(snapshot, indent=2, default=str), encoding="utf-8"
            )
        except Exception as e:
            logger.error(f"BrowserSessionManager: save failed: {e}")

    async def _save(self) -> None:
        await asyncio.to_thread(self._save_sync)

    async def register_session(
        self, mode: str, session_key: str, channel: str = "",
        owner_chat_id: str = "", display_name: str = "",
        user_data_dir: str = "", cdp_url: str = "",
        metadata: Optional[Dict[str, Any]] = None,
    ) -> str:
        async with self._lock:
            # Reuse existing session for same session_key + mode
            for s in self._sessions.values():
                if s.session_key == session_key and s.mode == mode:
                    s.last_used_at = time.time()
                    s.status = SessionStatus.ALIVE.value
                    if cdp_url:
                        s.cdp_url = cdp_url
                    if user_data_dir:
                        s.user_data_dir = user_data_dir
                    await self._save()
                    return s.profile_id

            profile_id = uuid.uuid4().hex[:12]
            now = time.time()
            session = BrowserSession(
                profile_id=profile_id, mode=mode, session_key=session_key,
                channel=channel, owner_chat_id=owner_chat_id,
                created_at=now, last_used_at=now,
                status=SessionStatus.ALIVE.value,
                display_name=display_name or f"{mode}-{profile_id[:6]}",
                user_data_dir=user_data_dir, cdp_url=cdp_url,
                metadata=metadata or {},
            )
            self._sessions[profile_id] = session
            await self._save()
            logger.info(f"Registered browser session {profile_id} (mode={mode})")
            return profile_id

    async def update_liveness(self, profile_id: str) -> Optional[BrowserSession]:
        async with self._lock:
            s = self._sessions.get(profile_id)
            if s is None:
                return None
            s.last_used_at = time.time()
            s.status = SessionStatus.ALIVE.value
            await self._save()
            return s

    async def mark_closed(self, profile_id: str) -> Optional[BrowserSession]:
        async with self._lock:
            s = self._sessions.get(profile_id)
            if s is None:
                return None
            s.status = SessionStatus.CLOSED.value
            s.last_used_at = time.time()
            await self._save()
            return s

    async def get_session(self, profile_id: str) -> Optional[BrowserSession]:
        async with self._lock:
            return self._sessions.get(profile_id)

    async def list_sessions(
        self, *, mode_filter: Optional[str] = None, active_only: bool = False,
    ) -> List[BrowserSession]:
        now = time.time()
        async with self._lock:
            results: List[BrowserSession] = []
            for s in self._sessions.values():
                # Auto-mark stale
                if (s.status == SessionStatus.ALIVE.value
                        and now - s.last_used_at > _STALE_THRESHOLD_SECONDS):
                    s.status = SessionStatus.STALE.value

                if mode_filter and s.mode != mode_filter:
                    continue
                if active_only and s.status == SessionStatus.CLOSED.value:
                    continue
                results.append(s)
            results.sort(key=lambda x: x.last_used_at, reverse=True)
            return results

    async def rename_session(self, profile_id: str, display_name: str) -> Optional[BrowserSession]:
        async with self._lock:
            s = self._sessions.get(profile_id)
            if s is None:
                return None
            s.display_name = display_name
            await self._save()
            return s

    async def ping_session(self, profile_id: str) -> Dict[str, Any]:
        async with self._lock:
            s = self._sessions.get(profile_id)
            if s is None:
                return {"reachable": False, "error": "Session not found"}
        # For attach mode, try a quick TCP connect to the CDP port
        if s.mode == "attach" and s.cdp_url:
            try:
                import urllib.parse
                parsed = urllib.parse.urlparse(s.cdp_url)
                host = parsed.hostname or "127.0.0.1"
                port = parsed.port or 9222
                reader, writer = await asyncio.wait_for(
                    asyncio.open_connection(host, port), timeout=3.0
                )
                writer.close()
                await writer.wait_closed()
                return {"reachable": True, "profile_id": profile_id}
            except Exception as e:
                return {"reachable": False, "error": str(e)}

        # For managed profiles, check if the user_data_dir exists
        if s.user_data_dir:
            exists = Path(s.user_data_dir).exists()
            return {"reachable": exists, "profile_id": profile_id,
                    "note": "Profile directory exists" if exists else "Profile directory missing"}
        return {"reachable": s.status == SessionStatus.ALIVE.value, "profile_id": profile_id}

    async def reset_session(self, profile_id: str) -> Dict[str, Any]:
        async with self._lock:
            s = self._sessions.get(profile_id)
            if s is None:
                return {"error": "Session not found"}
            if s.mode in {"system", "attach"}:
                return {"error": f"Cannot reset {s.mode} sessions — they are not LimeBot-managed."}
            if not s.user_data_dir:
                return {"error": "No user data directory associated with this session."}

        # Do filesystem cleanup outside the lock
        profile_path = Path(s.user_data_dir)
        if profile_path.exists():
            try:
                await asyncio.to_thread(shutil.rmtree, profile_path, True)
                profile_path.mkdir(parents=True, exist_ok=True)
                logger.info(f"Reset browser profile for session {profile_id}")
            except Exception as e:
                return {"error": f"Failed to reset profile: {e}"}

        async with self._lock:
            s.status = SessionStatus.CLOSED.value
            s.last_used_at = time.time()
            await self._save()
        return {"success": True, "profile_id": profile_id}

    async def delete_session(self, profile_id: str) -> Dict[str, Any]:
        async with self._lock:
            s = self._sessions.get(profile_id)
            if s is None:
                return {"error": "Session not found"}
            if s.mode in {"system", "attach"}:
                return {"error": f"Cannot delete {s.mode} sessions — they are not LimeBot-managed."}

            user_data_dir = s.user_data_dir
            self._sessions.pop(profile_id, None)
            await self._save()

        # Optionally clean up managed profile directory
        if user_data_dir:
            profile_path = Path(user_data_dir)
            if profile_path.exists():
                try:
                    await asyncio.to_thread(shutil.rmtree, profile_path, True)
                except Exception as e:
                    logger.warning(f"Failed to clean up profile dir for {profile_id}: {e}")

        logger.info(f"Deleted browser session {profile_id}")
        return {"success": True, "profile_id": profile_id}


_instance: Optional[BrowserSessionManager] = None


def get_browser_session_manager() -> BrowserSessionManager:
    global _instance
    if _instance is None:
        _instance = BrowserSessionManager()
    return _instance
