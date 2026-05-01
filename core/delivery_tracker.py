"""
DeliveryTracker — in-memory outbound delivery registry with bounded JSON persistence.
"""

import asyncio
import json
import time
import uuid
from collections import deque
from dataclasses import asdict, dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any, Dict, List, Optional

from loguru import logger


class DeliveryStatus(str, Enum):
    QUEUED = "queued"
    SENDING = "sending"
    SENT = "sent"
    FAILED = "failed"
    RETRYING = "retrying"


_TERMINAL_STATUSES = frozenset({DeliveryStatus.SENT, DeliveryStatus.FAILED})

_MAX_HISTORY = 500
_FLUSH_DEBOUNCE_SECONDS = 2.0


@dataclass
class Delivery:
    delivery_id: str
    channel: str
    message_kind: str = "text"
    target: str = ""
    status: str = DeliveryStatus.QUEUED.value
    attempts: int = 0
    last_error: str = ""
    created_at: float = 0.0
    last_attempt_at: float = 0.0
    sent_at: float = 0.0
    task_id: str = ""
    metadata: Dict[str, Any] = field(default_factory=dict)


class DeliveryTracker:
    """Thread-safe, bounded delivery registry with JSON snapshot persistence."""

    def __init__(self, data_dir: str = "data"):
        self._data_file = Path(data_dir) / "deliveries.json"
        self._active: Dict[str, Delivery] = {}
        self._history: deque[Delivery] = deque(maxlen=_MAX_HISTORY)
        self._lock = asyncio.Lock()
        self._last_flush: float = 0.0
        self._flush_pending: bool = False
        self._load()

    def _load(self) -> None:
        if not self._data_file.exists():
            return
        try:
            raw = json.loads(self._data_file.read_text(encoding="utf-8"))
            for item in raw.get("history", []):
                self._history.append(Delivery(**{
                    k: v for k, v in item.items()
                    if k in Delivery.__dataclass_fields__
                }))
            logger.info(f"DeliveryTracker: loaded {len(self._history)} history entries.")
        except Exception as e:
            logger.error(f"DeliveryTracker: failed to load history: {e}")

    def _flush_sync(self) -> None:
        try:
            self._data_file.parent.mkdir(parents=True, exist_ok=True)
            snapshot = {
                "active": [asdict(d) for d in self._active.values()],
                "history": [asdict(d) for d in self._history],
            }
            self._data_file.write_text(json.dumps(snapshot, indent=2, default=str), encoding="utf-8")
            self._last_flush = time.time()
        except Exception as e:
            logger.error(f"DeliveryTracker: flush failed: {e}")

    async def _schedule_flush(self) -> None:
        if self._flush_pending:
            return
        now = time.time()
        if now - self._last_flush >= _FLUSH_DEBOUNCE_SECONDS:
            await asyncio.to_thread(self._flush_sync)
            self._flush_pending = False
        else:
            self._flush_pending = True
            delay = _FLUSH_DEBOUNCE_SECONDS - (now - self._last_flush)
            asyncio.get_event_loop().call_later(
                delay, lambda: asyncio.ensure_future(self._do_deferred_flush())
            )

    async def _do_deferred_flush(self) -> None:
        self._flush_pending = False
        await asyncio.to_thread(self._flush_sync)

    async def track_delivery(
        self, channel: str, target: str, message_kind: str = "text",
        task_id: str = "", metadata: Optional[Dict[str, Any]] = None,
    ) -> str:
        delivery_id = uuid.uuid4().hex[:12]
        now = time.time()
        delivery = Delivery(
            delivery_id=delivery_id, channel=channel, message_kind=message_kind,
            target=target, status=DeliveryStatus.QUEUED.value,
            created_at=now, task_id=task_id, metadata=metadata or {},
        )
        async with self._lock:
            self._active[delivery_id] = delivery
            await self._schedule_flush()
        return delivery_id

    async def mark_sending(self, delivery_id: str) -> Optional[Delivery]:
        async with self._lock:
            d = self._active.get(delivery_id)
            if d is None:
                return None
            d.status = DeliveryStatus.SENDING.value
            d.attempts += 1
            d.last_attempt_at = time.time()
            await self._schedule_flush()
            return d

    async def mark_sent(self, delivery_id: str) -> Optional[Delivery]:
        async with self._lock:
            d = self._active.get(delivery_id)
            if d is None:
                return None
            d.status = DeliveryStatus.SENT.value
            d.sent_at = time.time()
            self._active.pop(delivery_id, None)
            self._history.append(d)
            await self._schedule_flush()
            return d

    async def mark_failed(self, delivery_id: str, error: str) -> Optional[Delivery]:
        async with self._lock:
            d = self._active.get(delivery_id)
            if d is None:
                return None
            d.status = DeliveryStatus.FAILED.value
            d.last_error = error
            self._active.pop(delivery_id, None)
            self._history.append(d)
            await self._schedule_flush()
            return d

    async def get_delivery(self, delivery_id: str) -> Optional[Delivery]:
        async with self._lock:
            d = self._active.get(delivery_id)
            if d:
                return d
            for item in reversed(self._history):
                if item.delivery_id == delivery_id:
                    return item
        return None

    async def list_deliveries(
        self, *, status_filter: Optional[str] = None,
        channel_filter: Optional[str] = None, limit: int = 100,
    ) -> List[Delivery]:
        async with self._lock:
            pool: List[Delivery] = list(self._active.values())
            pool.extend(self._history)
            results: List[Delivery] = []
            for d in pool:
                if status_filter and d.status != status_filter:
                    continue
                if channel_filter and d.channel != channel_filter:
                    continue
                results.append(d)
            results.sort(key=lambda x: x.created_at, reverse=True)
            return results[:limit]


_instance: Optional[DeliveryTracker] = None


def get_delivery_tracker() -> DeliveryTracker:
    global _instance
    if _instance is None:
        _instance = DeliveryTracker()
    return _instance
