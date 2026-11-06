"""Schedule one crash-safe continuation slice for a durable task run."""

from __future__ import annotations

from typing import Any, Callable, Optional

from core.events import InboundMessage
from core.recovery_controller import RecoveryState
from core.redaction import redact_sensitive_text


class TaskRunContinuationService:
    """Persist a checkpoint and hand its continuation to queue and bus."""

    def __init__(
        self,
        *,
        task_runs: Any,
        job_queue: Any,
        bus: Any,
        log_session_event: Callable[[str, dict], None],
        publish_activity: Callable[..., Any],
    ) -> None:
        self.task_runs = task_runs
        self.job_queue = job_queue
        self.bus = bus
        self._log_session_event = log_session_event
        self._publish_activity = publish_activity

    async def schedule(
        self,
        run: Any,
        msg: InboundMessage,
        *,
        next_action: str,
        phase: str,
        error: str = "",
        corrective_failure: bool = False,
        recovery: Optional[RecoveryState] = None,
    ) -> bool:
        if run is None or run.terminal:
            return False
        if run.slice_count >= run.max_slices:
            self.task_runs.block(
                run.run_id,
                error="Task continuation limit reached without verified completion.",
                next_action="Review the last checkpoint and continue explicitly.",
            )
            return False
        if recovery is not None and not recovery.can_continue():
            self.task_runs.block(
                run.run_id,
                error=error or "Corrective recovery limit reached after the recorded failures.",
                next_action="Review the diagnostic and provide a new direction or credential.",
            )
            return False

        checkpoint = dict(run.checkpoint or {})
        if recovery is not None:
            checkpoint["recovery"] = recovery.to_dict()
        updated = self.task_runs.request_resume(
            run.run_id,
            phase=phase,
            next_action=next_action,
            error=error,
            corrective_failure=corrective_failure,
            corrective_failures=(
                recovery.corrective_failures if recovery is not None else None
            ),
            checkpoint=checkpoint,
            metadata_update={"continuation_requested": True},
        )
        if updated is None:
            return False

        self._log_session_event(
            str((updated.metadata or {}).get("session_key") or msg.session_key),
            {
                "type": "task_run_checkpoint",
                "task_run_id": updated.run_id,
                "status": updated.status,
                "phase": updated.phase,
                "slice_count": updated.slice_count,
                "corrective_failures": updated.corrective_failures,
                "next_action": updated.next_action[:500],
                "last_error": redact_sensitive_text(updated.last_error[:500]),
            },
        )

        metadata = dict(msg.metadata or {})
        metadata.update({"task_run_id": run.run_id, "task_run_resume": True})
        durable_id = str(metadata.get("durable_job_id") or "").strip()
        resume_msg = InboundMessage(
            channel=msg.channel,
            sender_id=msg.sender_id,
            chat_id=msg.chat_id,
            content=msg.content,
            media=list(msg.media or []),
            metadata=metadata,
        )
        msg.metadata.update({"task_run_continuation_scheduled": True})
        if durable_id:
            self.job_queue.update_payload_metadata(
                durable_id,
                {"task_run_id": run.run_id, "task_run_resume": True},
            )
            self.job_queue.requeue_continuation(
                durable_id,
                # The bus is the fast handoff; the queue is the crash-safe fallback.
                # Keep it immediately claimable to avoid duplicate replies.
                delay=0,
                error=error or "Continuing durable task from checkpoint.",
            )
        else:
            metadata["durable_job_id"] = run.run_id
            resume_msg.metadata = metadata
            self.job_queue.enqueue(
                resume_msg,
                kind="task_continuation",
                job_id=run.run_id,
                max_attempts=1,
            )

        await self.bus.publish_inbound(resume_msg)
        await self._publish_activity(
            msg,
            f"Continuing task from checkpoint: {next_action}",
            turn_id=None,
            message_id=None,
        )
        return True
