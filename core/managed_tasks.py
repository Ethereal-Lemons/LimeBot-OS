"""Runtime task registry with stable IDs and exactly-once terminal state.

``TaskTracker`` is the durable projection used by the dashboard.  This module
keeps the live ``asyncio.Task`` handles that make that projection actionable:
callers can wait for, cancel, and await every task created by the agent loop.

The registry deliberately keeps a bounded terminal cache.  A completed task is
therefore still observable after its live handle has been removed, and repeated
``wait``/``kill`` calls converge on the same terminal snapshot instead of
publishing a second completion.
"""

from __future__ import annotations

import asyncio
import time
from collections import deque
from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable, Dict, Iterable, List, Optional


_TERMINAL_STATUSES = frozenset({"completed", "failed", "cancelled"})


@dataclass
class ManagedTask:
    """A live handle plus its stable lifecycle identity."""

    task_id: str
    handle: Optional[asyncio.Task] = field(default=None, repr=False)
    kind: str = ""
    session_key: str = ""
    parent_task_id: str = ""
    status: str = "queued"
    result: Any = None
    error: str = ""
    created_at: float = field(default_factory=time.time)
    updated_at: float = field(default_factory=time.time)
    done: asyncio.Event = field(default_factory=asyncio.Event, repr=False)
    metadata: Dict[str, Any] = field(default_factory=dict)
    on_terminal: Optional[Callable[["ManagedTask"], Awaitable[None]]] = field(
        default=None, repr=False
    )

    @property
    def terminal(self) -> bool:
        return self.status in _TERMINAL_STATUSES


class ManagedTaskRegistry:
    """Track every live agent task and make terminal transitions idempotent."""

    def __init__(self, max_history: int = 500):
        self._active: Dict[str, ManagedTask] = {}
        self._history: Dict[str, ManagedTask] = {}
        self._max_history = max(1, int(max_history))
        self._history_order: deque[str] = deque()
        self._lock = asyncio.Lock()

    @staticmethod
    def _copy(entry: ManagedTask) -> ManagedTask:
        # Keep the event/handle references: callers use them to await/cancel,
        # while lifecycle fields remain copied so outside code cannot mutate the
        # registry's state accidentally.
        return ManagedTask(
            task_id=entry.task_id,
            handle=entry.handle,
            kind=entry.kind,
            session_key=entry.session_key,
            parent_task_id=entry.parent_task_id,
            status=entry.status,
            result=entry.result,
            error=entry.error,
            created_at=entry.created_at,
            updated_at=entry.updated_at,
            done=entry.done,
            metadata=dict(entry.metadata),
            on_terminal=entry.on_terminal,
        )

    async def register(
        self,
        task_id: str,
        handle: asyncio.Task,
        *,
        kind: str = "",
        session_key: str = "",
        parent_task_id: str = "",
        metadata: Optional[Dict[str, Any]] = None,
        on_terminal: Optional[Callable[[ManagedTask], Awaitable[None]]] = None,
    ) -> ManagedTask:
        """Register a handle before its coroutine is allowed to run.

        Re-registering the same live handle is harmless.  A reused ID for a
        different handle is rejected so task identity cannot silently fork.
        """

        clean_id = str(task_id or "").strip()
        if not clean_id:
            raise ValueError("task_id is required")
        async with self._lock:
            existing = self._active.get(clean_id) or self._history.get(clean_id)
            if existing is not None:
                if existing.handle is handle:
                    return self._copy(existing)
                raise ValueError(f"task_id already registered: {clean_id}")
            entry = ManagedTask(
                task_id=clean_id,
                handle=handle,
                kind=str(kind or ""),
                session_key=str(session_key or ""),
                parent_task_id=str(parent_task_id or ""),
                metadata=dict(metadata or {}),
                on_terminal=on_terminal,
            )
            self._active[clean_id] = entry
            return self._copy(entry)

    async def mark_running(self, task_id: str) -> Optional[ManagedTask]:
        async with self._lock:
            entry = self._active.get(str(task_id))
            if entry is None:
                entry = self._history.get(str(task_id))
                return self._copy(entry) if entry else None
            if not entry.terminal:
                entry.status = "running"
                entry.updated_at = time.time()
            return self._copy(entry)

    async def get(self, task_id: str) -> Optional[ManagedTask]:
        async with self._lock:
            entry = self._active.get(str(task_id)) or self._history.get(str(task_id))
            return self._copy(entry) if entry else None

    async def active(self) -> List[ManagedTask]:
        async with self._lock:
            return [self._copy(entry) for entry in self._active.values()]

    async def active_for(
        self,
        predicate: Optional[Callable[[ManagedTask], bool]] = None,
    ) -> List[ManagedTask]:
        async with self._lock:
            entries = list(self._active.values())
            if predicate is not None:
                entries = [entry for entry in entries if predicate(entry)]
            return [self._copy(entry) for entry in entries]

    async def finalize(
        self,
        task_id: str,
        status: str,
        *,
        result: Any = None,
        error: Optional[str] = None,
        metadata_update: Optional[Dict[str, Any]] = None,
    ) -> Optional[ManagedTask]:
        """Publish one terminal transition and return its stable snapshot.

        Once a task is terminal, later finalizers return the original terminal
        snapshot.  This is the key guard against a cancellation racing with a
        worker's normal completion path.
        """

        normalized = str(status or "failed").strip().lower()
        if normalized not in _TERMINAL_STATUSES:
            raise ValueError(f"unsupported terminal status: {status}")
        clean_id = str(task_id or "")
        callback: Optional[Callable[[ManagedTask], Awaitable[None]]] = None
        snapshot: Optional[ManagedTask] = None
        async with self._lock:
            entry = self._active.get(clean_id)
            if entry is None:
                entry = self._history.get(clean_id)
                return self._copy(entry) if entry else None
            if entry.terminal:
                return self._copy(entry)

            entry.status = normalized
            if isinstance(result, str) and len(result) > 4_000:
                entry.result = result[:4_000] + "\n... (truncated)"
            else:
                entry.result = result
            if error is not None:
                entry.error = str(error)[:1_000]
            if metadata_update:
                entry.metadata.update(metadata_update)
            entry.updated_at = time.time()
            entry.done.set()
            self._active.pop(clean_id, None)
            self._history[clean_id] = entry
            self._history_order.append(clean_id)
            while len(self._history_order) > self._max_history:
                oldest = self._history_order.popleft()
                self._history.pop(oldest, None)
            snapshot = self._copy(entry)
            callback = entry.on_terminal
        if callback is not None and snapshot is not None:
            try:
                await callback(snapshot)
            except Exception:
                # A durable projection must never prevent the live handle from
                # reaching its terminal state.
                pass
        return snapshot

    async def wait(
        self, task_id: str, timeout: Optional[float] = None
    ) -> Optional[ManagedTask]:
        entry = await self.get(task_id)
        if entry is None:
            return None
        if not entry.done.is_set() and not entry.terminal:
            waiter = asyncio.shield(entry.done.wait())
            if timeout is None:
                await waiter
            else:
                await asyncio.wait_for(waiter, timeout=max(0.0, float(timeout)))
        return await self.get(task_id)

    async def cancel(
        self, task_id: str, *, timeout: float = 2.0
    ) -> Optional[ManagedTask]:
        """Cancel and bounded-await a live task, then close its lifecycle."""

        entry = await self.get(task_id)
        if entry is None or entry.terminal:
            return entry
        handle = entry.handle
        current = asyncio.current_task()
        if handle is not None and handle is not current and not handle.done():
            handle.cancel()
            try:
                await asyncio.wait_for(
                    asyncio.shield(handle), timeout=max(0.0, float(timeout))
                )
            except asyncio.TimeoutError:
                # A misbehaving coroutine must not hold shutdown or the UI
                # forever.  A second cancellation gives cooperative workers a
                # final chance while the registry still records cancellation.
                handle.cancel()
            except asyncio.CancelledError:
                # The expected result of cancelling a cooperative asyncio task
                # is a CancelledError from the shielded await itself.  Preserve
                # cancellation of the *caller* when the handle is still live.
                if not handle.done():
                    raise
        final = await self.get(task_id)
        if final is not None and not final.terminal:
            final = await self.finalize(
                task_id,
                "cancelled",
                error="Task cancelled.",
            )
        return final

    async def cancel_all(
        self,
        *,
        predicate: Optional[Callable[[ManagedTask], bool]] = None,
        exclude: Optional[Iterable[asyncio.Task]] = None,
        timeout: float = 2.0,
    ) -> List[ManagedTask]:
        excluded = set(exclude or ())
        entries = await self.active_for(predicate)
        selected = [entry for entry in entries if entry.handle not in excluded]
        outcomes = await asyncio.gather(
            *(self.cancel(entry.task_id, timeout=timeout) for entry in selected),
            return_exceptions=True,
        )
        return [
            outcome
            for outcome in outcomes
            if isinstance(outcome, ManagedTask)
        ]
