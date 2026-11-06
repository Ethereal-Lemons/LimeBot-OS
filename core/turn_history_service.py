"""In-memory turn-history mutations and debounced persistence."""

from __future__ import annotations

import time
from typing import Any, Callable, Dict, List, MutableMapping


class TurnHistoryService:
    """Own per-session history writes and the persistence dirty/flush boundary."""

    def __init__(
        self,
        *,
        histories: MutableMapping[str, List[Dict[str, Any]]],
        dirty: Dict[str, bool],
        last_flush: Dict[str, float],
        save_history: Callable[[str, List[Dict[str, Any]]], Any],
        safe_content: Callable[[Any], Any],
        flush_interval: Callable[[], float],
        monotonic: Callable[[], float] = time.monotonic,
    ) -> None:
        self._histories = histories
        self._dirty = dirty
        self._last_flush = last_flush
        self._save_history = save_history
        self._safe_content = safe_content
        self._flush_interval = flush_interval
        self._monotonic = monotonic

    def set_history(
        self,
        session_key: str,
        entries: List[Dict[str, Any]],
        *,
        mark_dirty: bool = False,
    ) -> None:
        self._histories[session_key] = entries
        if mark_dirty:
            self.mark_dirty(session_key)

    def append(
        self,
        session_key: str,
        entry: Dict[str, Any],
        *,
        mark_dirty: bool = True,
    ) -> None:
        self._histories[session_key].append(entry)
        if mark_dirty:
            self.mark_dirty(session_key)

    def replace_entry(
        self,
        session_key: str,
        index: int,
        entry: Dict[str, Any],
        *,
        mark_dirty: bool = False,
    ) -> None:
        self._histories[session_key][index] = entry
        if mark_dirty:
            self.mark_dirty(session_key)

    def mark_dirty(self, session_key: str) -> None:
        self._dirty[session_key] = True

    async def flush(self, session_key: str, *, force: bool = False) -> bool:
        history = self._histories.get(session_key)
        if not (self._dirty.get(session_key) and history is not None):
            return False

        now = self._monotonic()
        last = self._last_flush.get(session_key, 0.0)
        if not force and now - last < self._flush_interval():
            return False

        persistable_history = []
        for message in history:
            persisted_message = dict(message)
            persisted_message["content"] = self._safe_content(
                message.get("content")
            )
            persistable_history.append(persisted_message)
        await self._save_history(session_key, persistable_history)
        self._dirty[session_key] = False
        self._last_flush[session_key] = now
        return True
