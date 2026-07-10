"""Bounded delivery history with debounced, non-blocking atomic persistence."""

import asyncio
import json
import os
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
    """Mutations never wait for disk; flushes serialize immutable snapshots."""

    def __init__(self, data_dir: str = "data", *, debounce_seconds: float = _FLUSH_DEBOUNCE_SECONDS):
        self._data_file = Path(data_dir) / "deliveries.json"
        self._active: Dict[str, Delivery] = {}
        self._history: deque[Delivery] = deque(maxlen=_MAX_HISTORY)
        self._lock = asyncio.Lock()
        self._debounce_seconds = max(0.0, debounce_seconds)
        self._dirty_generation = 0
        self._written_generation = 0
        self._writer_task: asyncio.Task | None = None
        self._flush_requested = asyncio.Event()
        self._load()

    def _load(self) -> None:
        if not self._data_file.exists():
            return
        try:
            raw = json.loads(self._data_file.read_text(encoding="utf-8"))
            for item in raw.get("history", []):
                self._history.append(
                    Delivery(**{k: v for k, v in item.items() if k in Delivery.__dataclass_fields__})
                )
            logger.info(f"DeliveryTracker: loaded {len(self._history)} history entries.")
        except Exception as exc:
            logger.error(f"DeliveryTracker: failed to load history: {exc}")

    def _snapshot_locked(self) -> tuple[int, str]:
        payload = {
            "active": [asdict(item) for item in self._active.values()],
            "history": [asdict(item) for item in self._history],
        }
        return self._dirty_generation, json.dumps(payload, indent=2, default=str)

    def _write_snapshot_sync(self, payload: str) -> None:
        self._data_file.parent.mkdir(parents=True, exist_ok=True)
        temp = self._data_file.with_name(f".{self._data_file.name}.{uuid.uuid4().hex}.tmp")
        try:
            temp.write_text(payload, encoding="utf-8")
            os.replace(temp, self._data_file)
        finally:
            try:
                temp.unlink(missing_ok=True)
            except OSError:
                pass

    def _mark_dirty_locked(self) -> None:
        self._dirty_generation += 1
        if self._writer_task is None or self._writer_task.done():
            self._writer_task = asyncio.create_task(
                self._writer_loop(), name="limebot-delivery-persistence"
            )

    async def _writer_loop(self) -> None:
        try:
            while True:
                if not self._flush_requested.is_set() and self._debounce_seconds:
                    try:
                        await asyncio.wait_for(
                            self._flush_requested.wait(), timeout=self._debounce_seconds
                        )
                    except asyncio.TimeoutError:
                        pass
                self._flush_requested.clear()
                async with self._lock:
                    if self._written_generation >= self._dirty_generation:
                        return
                    generation, payload = self._snapshot_locked()
                try:
                    await asyncio.to_thread(self._write_snapshot_sync, payload)
                except Exception as exc:
                    logger.error(f"DeliveryTracker: flush failed: {exc}")
                    async with self._lock:
                        # Contain persistent disk failures instead of making shutdown hang.
                        self._written_generation = max(self._written_generation, generation)
                    return
                async with self._lock:
                    self._written_generation = max(self._written_generation, generation)
                    if self._written_generation >= self._dirty_generation:
                        return
        finally:
            current = asyncio.current_task()
            if self._writer_task is current:
                self._writer_task = None

    async def track_delivery(
        self,
        channel: str,
        target: str,
        message_kind: str = "text",
        task_id: str = "",
        metadata: Optional[Dict[str, Any]] = None,
    ) -> str:
        delivery_id = uuid.uuid4().hex[:12]
        delivery = Delivery(
            delivery_id=delivery_id,
            channel=channel,
            message_kind=message_kind,
            target=target,
            created_at=time.time(),
            task_id=task_id,
            metadata=dict(metadata or {}),
        )
        async with self._lock:
            self._active[delivery_id] = delivery
            self._mark_dirty_locked()
        return delivery_id

    async def mark_sending(self, delivery_id: str) -> Optional[Delivery]:
        async with self._lock:
            delivery = self._active.get(delivery_id)
            if delivery is None:
                return None
            delivery.status = DeliveryStatus.SENDING.value
            delivery.attempts += 1
            delivery.last_attempt_at = time.time()
            self._mark_dirty_locked()
            return delivery

    async def mark_sent(self, delivery_id: str) -> Optional[Delivery]:
        async with self._lock:
            delivery = self._active.pop(delivery_id, None)
            if delivery is None:
                return None
            delivery.status = DeliveryStatus.SENT.value
            delivery.sent_at = time.time()
            self._history.append(delivery)
            self._mark_dirty_locked()
            return delivery

    async def mark_failed(self, delivery_id: str, error: str) -> Optional[Delivery]:
        async with self._lock:
            delivery = self._active.pop(delivery_id, None)
            if delivery is None:
                return None
            delivery.status = DeliveryStatus.FAILED.value
            delivery.last_error = error
            self._history.append(delivery)
            self._mark_dirty_locked()
            return delivery

    async def flush(self) -> None:
        """Wait until the latest dirty generation has been atomically persisted."""
        while True:
            async with self._lock:
                target = self._dirty_generation
                if self._written_generation >= target:
                    return
                if self._writer_task is None or self._writer_task.done():
                    self._writer_task = asyncio.create_task(self._writer_loop())
                task = self._writer_task
                self._flush_requested.set()
            await task

    async def get_delivery(self, delivery_id: str) -> Optional[Delivery]:
        async with self._lock:
            delivery = self._active.get(delivery_id)
            if delivery:
                return delivery
            return next(
                (item for item in reversed(self._history) if item.delivery_id == delivery_id),
                None,
            )

    async def list_deliveries(
        self,
        *,
        status_filter: Optional[str] = None,
        channel_filter: Optional[str] = None,
        limit: int = 100,
    ) -> List[Delivery]:
        async with self._lock:
            pool = list(self._active.values()) + list(self._history)
            results = [
                item
                for item in pool
                if (not status_filter or item.status == status_filter)
                and (not channel_filter or item.channel == channel_filter)
            ]
            return sorted(results, key=lambda item: item.created_at, reverse=True)[:limit]


_instance: Optional[DeliveryTracker] = None


def get_delivery_tracker() -> DeliveryTracker:
    global _instance
    if _instance is None:
        _instance = DeliveryTracker()
    return _instance
