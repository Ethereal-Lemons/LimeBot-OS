"""Own task tracking and cleanup around one managed inbound turn."""

from __future__ import annotations

import asyncio
import re
import time
import uuid
from typing import Any, Awaitable, Callable, Optional

from loguru import logger

from core.events import InboundMessage, OutboundMessage
from core.job_queue import FAILED, SUCCEEDED
from core.profile_context import ProfileCoordinator
from core.profiles import ProfileError
from core.task_runs import ACTIVE_STATES as TASK_RUN_ACTIVE_STATES
from core.task_tracker import TaskStatus


class InboundTurnLifecycle:
    """Run a message while coordinating task, queue, profile, and cron state."""

    def __init__(
        self,
        *,
        tracker: Any,
        registry: Any,
        task_runs: Any,
        profile_coordinator: Optional[ProfileCoordinator],
        job_queue: Any,
        scheduler: Any,
        bus: Any,
        active_tasks: dict[str, asyncio.Task],
        queue_worker_id: str,
        process_message: Callable[..., Awaitable[Any]],
        trace_metadata: Callable[..., dict[str, Any]],
    ) -> None:
        self.tracker = tracker
        self.registry = registry
        self.task_runs = task_runs
        self.profile_coordinator = profile_coordinator
        self.job_queue = job_queue
        self.scheduler = scheduler
        self.bus = bus
        self.active_tasks = active_tasks
        self.queue_worker_id = queue_worker_id
        self.process_message = process_message
        self.trace_metadata = trace_metadata

    async def dispatch(
        self,
        msg: InboundMessage,
        *,
        run_managed_message: Callable[..., Awaitable[Any]],
        persist_task_terminal: Callable[[Any], Awaitable[Any]],
    ) -> asyncio.Task:
        """Claim and register one inbound turn before starting its coroutine."""
        if self.profile_coordinator is not None:
            try:
                self.profile_coordinator.resolve_message_profile(msg)
            except ProfileError as exc:
                profile_id = ProfileCoordinator.requested_profile_id(msg)
                error_text = f"Agent profile '{profile_id}' is unavailable: {exc}"
                await self.bus.publish_outbound(
                    OutboundMessage(
                        channel=msg.channel,
                        chat_id=msg.chat_id,
                        content=error_text,
                        metadata={
                            "profile_id": profile_id,
                            "is_error": True,
                            "event_kind": "transport_error",
                            "error_code": "profile_unavailable",
                        },
                    )
                )

                async def rejected_profile() -> None:
                    return None

                return asyncio.create_task(rejected_profile())

        metadata = dict(msg.metadata or {})
        if not metadata.get("task_run_id"):
            followup_words = re.compile(
                r"\b(continue|again|retry|re-?try|fix|correct|use|proceed|"
                r"do it|try that|corrige|arregla|intenta|continua|de nuevo|hazlo)\b",
                re.IGNORECASE,
            )
            active_runs = (
                self.task_runs.list_runs(statuses=TASK_RUN_ACTIVE_STATES, limit=100)
                if self.task_runs is not None
                else []
            )
            matching = [
                run
                for run in active_runs
                if str((run.metadata or {}).get("session_key") or "")
                == msg.session_key
            ]
            if matching and (
                bool(metadata.get("reply_to_task_run"))
                or bool(followup_words.search(msg.content or ""))
            ):
                metadata["task_run_id"] = matching[0].run_id
                metadata["task_run_followup"] = True
                msg.metadata = metadata
                durable_id = str(
                    (matching[0].metadata or {}).get("durable_job_id") or ""
                ).strip()
                if durable_id and self.job_queue is not None:
                    self.job_queue.update_payload_metadata(
                        durable_id,
                        {
                            "task_run_id": matching[0].run_id,
                            "task_run_followup": True,
                        },
                    )

        durable_job_id = (
            msg.metadata.get("durable_job_id")
            if isinstance(msg.metadata, dict)
            else None
        )
        if durable_job_id and self.job_queue is not None:
            claimed = self.job_queue.claim(
                str(durable_job_id), self.queue_worker_id
            )
            if claimed is None:
                current = self.job_queue.get(str(durable_job_id))
                queued_for_later = bool(
                    current is not None
                    and current.status == "queued"
                    and current.next_retry_at is not None
                    and float(current.next_retry_at) > time.time()
                )
                if current is None or current.status != "queued" or queued_for_later:
                    logger.info(
                        "Skipping inbound duplicate for durable job %s (%s)",
                        durable_job_id,
                        current.status if current else "missing",
                    )

                    async def no_op() -> None:
                        return None

                    return asyncio.create_task(no_op())

        task_id = uuid.uuid4().hex[:12]
        await self.tracker.create_task(
            task_type="inbound_message",
            summary=f"{msg.channel}: {(msg.content or '')[:80]}",
            channel=msg.channel,
            session_key=msg.session_key,
            chat_id=msg.chat_id,
            metadata={
                "runtime": True,
                "sender_id": msg.sender_id,
                "profile_id": (
                    msg.metadata.get("profile_id", "default")
                    if isinstance(msg.metadata, dict)
                    else "default"
                ),
                "profile_name": (
                    msg.metadata.get("profile_name", "Default")
                    if isinstance(msg.metadata, dict)
                    else "Default"
                ),
                "workspace_id": msg.metadata.get("workspace_id")
                if isinstance(msg.metadata, dict)
                else "",
                "client_message_id": msg.metadata.get("client_message_id")
                if isinstance(msg.metadata, dict)
                else "",
            },
            task_id=task_id,
        )
        start_gate = asyncio.Event()
        handle = asyncio.create_task(
            run_managed_message(msg, task_id, start_gate),
            name=f"limebot-turn-{task_id}",
        )
        try:
            await self.registry.register(
                task_id,
                handle,
                kind="inbound_message",
                session_key=msg.session_key,
                metadata={"channel": msg.channel, "chat_id": msg.chat_id},
                on_terminal=persist_task_terminal,
            )
        except BaseException:
            handle.cancel()
            await asyncio.gather(handle, return_exceptions=True)
            raise
        finally:
            start_gate.set()
        self.active_tasks[msg.session_key] = handle
        return handle

    async def run(
        self,
        msg: InboundMessage,
        task_id: str,
        start_gate: Optional[asyncio.Event] = None,
    ) -> None:
        if start_gate is not None:
            await start_gate.wait()
        await self.registry.mark_running(task_id)

        durable_job_id = (
            msg.metadata.get("durable_job_id")
            if isinstance(msg.metadata, dict)
            else None
        )
        heartbeat_task = None
        if durable_job_id and self.job_queue is not None:

            async def heartbeat() -> None:
                while True:
                    await asyncio.sleep(15)
                    self.job_queue.heartbeat(
                        str(durable_job_id), self.queue_worker_id
                    )

            heartbeat_task = asyncio.create_task(heartbeat())

        try:
            if self.profile_coordinator is None:
                await self.process_message(msg, _task_id=task_id)
            else:
                with self.profile_coordinator.activate_message(msg):
                    await self.process_message(msg, _task_id=task_id)
        except asyncio.CancelledError:
            await self._mark_cancelled(task_id)
            raise
        except ProfileError as error:
            await self._reject_profile(msg, task_id, error)
        except Exception as error:
            logger.exception("Unhandled managed inbound turn error: %s", error)
            tracked = await self.tracker.get_task(task_id)
            if tracked is not None and tracked.status not in {
                TaskStatus.COMPLETED.value,
                TaskStatus.FAILED.value,
                TaskStatus.CANCELLED.value,
            }:
                await self.tracker.update_task(
                    task_id,
                    status=TaskStatus.FAILED.value,
                    error=str(error)[:500],
                )
        finally:
            await self._finalize(msg, task_id, durable_job_id, heartbeat_task)

    async def _mark_cancelled(self, task_id: str) -> None:
        tracked = await self.tracker.get_task(task_id)
        if tracked is not None and tracked.status not in {
            TaskStatus.COMPLETED.value,
            TaskStatus.FAILED.value,
            TaskStatus.CANCELLED.value,
        }:
            await self.tracker.update_task(
                task_id,
                status=TaskStatus.CANCELLED.value,
                error="Inbound turn cancelled.",
            )

    async def _reject_profile(
        self, msg: InboundMessage, task_id: str, error: ProfileError
    ) -> None:
        profile_id = ProfileCoordinator.requested_profile_id(msg)
        error_text = f"Agent profile '{profile_id}' is unavailable: {error}"
        if isinstance(msg.metadata, dict):
            msg.metadata["profile_rejected"] = True
        logger.warning(error_text)
        await self.tracker.update_task(
            task_id,
            status=TaskStatus.FAILED.value,
            error=error_text[:500],
            metadata_update={"profile_id": profile_id, "profile_rejected": True},
        )
        try:
            await self.bus.publish_outbound(
                OutboundMessage(
                    channel=msg.channel,
                    chat_id=msg.chat_id,
                    content=error_text,
                    metadata=self.trace_metadata(
                        {
                            "is_error": True,
                            "event_kind": "transport_error",
                            "error_code": "profile_unavailable",
                        },
                        task_id=task_id,
                    ),
                )
            )
        except Exception:
            logger.exception("Could not publish profile rejection.")

    async def _finalize(
        self,
        msg: InboundMessage,
        task_id: str,
        durable_job_id: Any,
        heartbeat_task: Optional[asyncio.Task],
    ) -> None:
        tracked = await self.tracker.get_task(task_id)
        status = tracked.status if tracked is not None else TaskStatus.COMPLETED.value
        if status not in {
            TaskStatus.COMPLETED.value,
            TaskStatus.FAILED.value,
            TaskStatus.CANCELLED.value,
            TaskStatus.RETRYING.value,
            TaskStatus.WAITING.value,
        }:
            current = asyncio.current_task()
            status = (
                TaskStatus.CANCELLED.value
                if current and getattr(current, "cancelling", lambda: 0)()
                else TaskStatus.COMPLETED.value
            )
            await self.tracker.update_task(task_id, status=status)

        registry_status = status
        if registry_status not in {
            TaskStatus.COMPLETED.value,
            TaskStatus.FAILED.value,
            TaskStatus.CANCELLED.value,
        }:
            registry_status = TaskStatus.COMPLETED.value
        await self.registry.finalize(
            task_id,
            registry_status,
            error=tracked.error if tracked is not None else None,
        )
        if heartbeat_task is not None:
            heartbeat_task.cancel()
            await asyncio.gather(heartbeat_task, return_exceptions=True)

        continuation_scheduled = bool(
            isinstance(msg.metadata, dict)
            and msg.metadata.get("task_run_continuation_scheduled")
        )
        if durable_job_id and self.job_queue is not None and not continuation_scheduled:
            await self._finish_durable_job(msg, durable_job_id, status, tracked)

        if self.active_tasks.get(msg.session_key) is asyncio.current_task():
            self.active_tasks.pop(msg.session_key, None)

    async def _finish_durable_job(
        self, msg: InboundMessage, durable_job_id: Any, status: str, tracked: Any
    ) -> None:
        error_text = tracked.error if tracked is not None else None
        if isinstance(msg.metadata, dict) and msg.metadata.get("profile_rejected"):
            finished = self.job_queue.finish(str(durable_job_id), FAILED)
        elif status == TaskStatus.COMPLETED.value:
            finished = self.job_queue.finish(str(durable_job_id), SUCCEEDED)
        elif status == TaskStatus.CANCELLED.value:
            finished = self.job_queue.finish(str(durable_job_id), "cancelled")
        else:
            finished = self.job_queue.fail_or_retry(
                str(durable_job_id), error_text or "Agent turn failed."
            )

        cron_job_id = (
            msg.metadata.get("original_job_id")
            if isinstance(msg.metadata, dict)
            else None
        )
        if cron_job_id and self.scheduler is not None and hasattr(
            self.scheduler, "mark_run_finished"
        ):
            cron_status = "ok"
            if finished is not None and finished.status == FAILED:
                cron_status = "error"
            elif status != TaskStatus.COMPLETED.value:
                cron_status = "error"
            await self.scheduler.mark_run_finished(
                str(cron_job_id),
                status=cron_status,
                error=error_text,
                tracker_task_id=msg.metadata.get("tracker_task_id")
                if isinstance(msg.metadata, dict)
                else None,
            )
