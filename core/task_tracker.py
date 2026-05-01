"""
TaskTracker — in-memory task registry with bounded JSON persistence.

Tracks all significant work units flowing through the agent loop so the
operator dashboard can answer "what is LimeBot doing right now?" without
reading logs.
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


# ── Enums ──────────────────────────────────────────────────────────────────────


class TaskType(str, Enum):
    INBOUND_MESSAGE = "inbound_message"
    LLM_TURN = "llm_turn"
    TOOL_CALL = "tool_call"
    SUBAGENT_JOB = "subagent_job"
    OUTBOUND_DELIVERY = "outbound_delivery"
    SCHEDULED_JOB = "scheduled_job"
    CONFIRMATION_WAIT = "confirmation_wait"


class TaskStatus(str, Enum):
    QUEUED = "queued"
    RUNNING = "running"
    WAITING = "waiting"
    RETRYING = "retrying"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"


_TERMINAL_STATUSES = frozenset({TaskStatus.COMPLETED, TaskStatus.FAILED, TaskStatus.CANCELLED})


# ── Data model ─────────────────────────────────────────────────────────────────


@dataclass
class Task:
    task_id: str
    type: str
    status: str = TaskStatus.QUEUED.value
    channel: str = ""
    session_key: str = ""
    chat_id: str = ""
    summary: str = ""
    created_at: float = 0.0
    updated_at: float = 0.0
    started_at: float = 0.0
    completed_at: float = 0.0
    parent_task_id: str = ""
    attempt: int = 1
    error: str = ""
    metadata: Dict[str, Any] = field(default_factory=dict)


# ── Tracker ────────────────────────────────────────────────────────────────────

_MAX_HISTORY = 500
_FLUSH_DEBOUNCE_SECONDS = 2.0


class TaskTracker:
    """Thread-safe, bounded task registry with JSON snapshot persistence."""

    def __init__(self, data_dir: str = "data"):
        self._data_file = Path(data_dir) / "tasks.json"
        self._active: Dict[str, Task] = {}
        self._history: deque[Task] = deque(maxlen=_MAX_HISTORY)
        self._lock = asyncio.Lock()
        self._last_flush: float = 0.0
        self._flush_pending: bool = False
        self._load()

    # ── Persistence ────────────────────────────────────────────────────────

    def _load(self) -> None:
        if not self._data_file.exists():
            return
        try:
            raw = json.loads(self._data_file.read_text(encoding="utf-8"))
            for item in raw.get("history", []):
                self._history.append(Task(**{
                    k: v for k, v in item.items() if k in Task.__dataclass_fields__
                }))
            logger.info(f"TaskTracker: loaded {len(self._history)} history entries.")
        except Exception as e:
            logger.error(f"TaskTracker: failed to load history: {e}")

    def _flush_sync(self) -> None:
        try:
            self._data_file.parent.mkdir(parents=True, exist_ok=True)
            snapshot = {
                "active": [asdict(t) for t in self._active.values()],
                "history": [asdict(t) for t in self._history],
            }
            self._data_file.write_text(
                json.dumps(snapshot, indent=2, default=str), encoding="utf-8"
            )
            self._last_flush = time.time()
        except Exception as e:
            logger.error(f"TaskTracker: flush failed: {e}")

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

    # ── Public API ─────────────────────────────────────────────────────────

    async def create_task(
        self,
        task_type: str,
        summary: str,
        channel: str = "",
        session_key: str = "",
        chat_id: str = "",
        parent_task_id: str = "",
        metadata: Optional[Dict[str, Any]] = None,
    ) -> str:
        task_id = uuid.uuid4().hex[:12]
        now = time.time()
        task = Task(
            task_id=task_id,
            type=task_type,
            status=TaskStatus.QUEUED.value,
            channel=channel,
            session_key=session_key,
            chat_id=chat_id,
            summary=summary,
            created_at=now,
            updated_at=now,
            parent_task_id=parent_task_id,
            metadata=metadata or {},
        )
        async with self._lock:
            self._active[task_id] = task
            await self._schedule_flush()
        return task_id

    async def update_task(
        self,
        task_id: str,
        status: Optional[str] = None,
        summary: Optional[str] = None,
        error: Optional[str] = None,
        metadata_update: Optional[Dict[str, Any]] = None,
    ) -> Optional[Task]:
        async with self._lock:
            task = self._active.get(task_id)
            if task is None:
                return None

            now = time.time()
            if status is not None:
                task.status = status
                if status == TaskStatus.RUNNING.value and task.started_at == 0.0:
                    task.started_at = now
            if summary is not None:
                task.summary = summary
            if error is not None:
                task.error = error
            if metadata_update:
                task.metadata.update(metadata_update)
            task.updated_at = now

            # Move to history if terminal
            if task.status in {s.value for s in _TERMINAL_STATUSES}:
                task.completed_at = now
                self._active.pop(task_id, None)
                self._history.append(task)

            await self._schedule_flush()
            return task

    async def complete_task(self, task_id: str, error: Optional[str] = None) -> Optional[Task]:
        status = TaskStatus.FAILED.value if error else TaskStatus.COMPLETED.value
        return await self.update_task(task_id, status=status, error=error)

    async def cancel_task(self, task_id: str) -> Optional[Task]:
        async with self._lock:
            task = self._active.get(task_id)
            if task is None:
                return None
            if task.status not in {TaskStatus.QUEUED.value, TaskStatus.WAITING.value}:
                return None
            task.status = TaskStatus.CANCELLED.value
            task.completed_at = time.time()
            task.updated_at = task.completed_at
            self._active.pop(task_id, None)
            self._history.append(task)
            await self._schedule_flush()
            return task

    async def get_task(self, task_id: str) -> Optional[Task]:
        async with self._lock:
            task = self._active.get(task_id)
            if task:
                return task
            for t in reversed(self._history):
                if t.task_id == task_id:
                    return t
        return None

    async def list_tasks(
        self,
        *,
        status_filter: Optional[str] = None,
        type_filter: Optional[str] = None,
        channel_filter: Optional[str] = None,
        session_filter: Optional[str] = None,
        active_only: bool = False,
        failed_only: bool = False,
        limit: int = 100,
    ) -> List[Task]:
        async with self._lock:
            pool: List[Task] = list(self._active.values())
            if not active_only:
                pool.extend(self._history)

            results: List[Task] = []
            for task in pool:
                if status_filter and task.status != status_filter:
                    continue
                if type_filter and task.type != type_filter:
                    continue
                if channel_filter and task.channel != channel_filter:
                    continue
                if session_filter and task.session_key != session_filter:
                    continue
                if failed_only and task.status != TaskStatus.FAILED.value:
                    continue
                results.append(task)

            results.sort(key=lambda t: t.updated_at, reverse=True)
            return results[:limit]

    async def get_active_tasks(self) -> List[Task]:
        async with self._lock:
            return sorted(
                self._active.values(),
                key=lambda t: t.created_at,
                reverse=True,
            )

    async def get_recent_failures(self, limit: int = 20) -> List[Task]:
        async with self._lock:
            failures = [
                t for t in self._history if t.status == TaskStatus.FAILED.value
            ]
            failures.sort(key=lambda t: t.completed_at, reverse=True)
            return failures[:limit]


# ── Singleton ──────────────────────────────────────────────────────────────────

_instance: Optional[TaskTracker] = None


def get_task_tracker() -> TaskTracker:
    global _instance
    if _instance is None:
        _instance = TaskTracker()
    return _instance
