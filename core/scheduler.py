"""
CronManager — scheduled tasks and reminders.
Persists jobs to data/cron.json.
Supports one-time (trigger timestamp) and repeating (cron expression) jobs.
"""

import asyncio
import json
import time
import uuid
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Any, Dict, List, Optional

from loguru import logger

from core.events import InboundMessage

try:
    from croniter import croniter
except ImportError:
    croniter = None

RECURRING_JOB_MAX_LAG_SECONDS = 300

# Per-job in-memory dedup: job_id -> last fired unix timestamp.
# Prevents duplicate fires when the bot restarts multiple times within a period.
_job_last_fired: dict[str, float] = {}


class CronManager:
    """Manages scheduled tasks and reminders."""

    def __init__(self, bus: Any, session_manager: Any | None = None):
        self.bus = bus
        self.session_manager = session_manager
        self.jobs: List[Dict[str, Any]] = []
        self._running = False
        self.data_file = Path("data/cron.json")
        self.runs_dir = Path("data/cron_runs")
        self.lock = asyncio.Lock()
        self._load_jobs()

    def _load_jobs(self) -> None:
        """Load jobs from disk (sync — called only at init)."""
        if not self.data_file.exists():
            return
        try:
            self.jobs = json.loads(self.data_file.read_text(encoding="utf-8"))

            valid = [
                j
                for j in self.jobs
                if j.get("trigger") is not None or j.get("cron_expr")
            ]
            if len(valid) != len(self.jobs):
                logger.warning(
                    f"Dropped {len(self.jobs) - len(valid)} jobs with null trigger."
                )
                self.jobs = valid
            for job in self.jobs:
                job.setdefault("active", True)
            logger.info(f"Loaded {len(self.jobs)} scheduled job(s).")
        except Exception as e:
            logger.error(f"Error loading jobs: {e}")
            self.jobs = []

    def _save_jobs(self) -> None:
        """Save jobs to disk (sync — always called while lock is held)."""
        try:
            self.data_file.parent.mkdir(parents=True, exist_ok=True)
            self.data_file.write_text(json.dumps(self.jobs, indent=2), encoding="utf-8")
        except Exception as e:
            logger.error(f"Error saving jobs: {e}")

    def _append_run_event(self, job_id: str, event: Dict[str, Any]) -> None:
        try:
            runs_dir = getattr(self, "runs_dir", self.data_file.parent / "cron_runs")
            runs_dir.mkdir(parents=True, exist_ok=True)
            run_file = runs_dir / f"{job_id}.jsonl"
            payload = dict(event)
            payload.setdefault("timestamp", time.time())
            with run_file.open("a", encoding="utf-8") as f:
                f.write(json.dumps(payload, default=str) + "\n")
        except Exception as e:
            logger.error(f"Error writing cron run event for {job_id}: {e}")

    async def add_job(
        self,
        trigger_time: Optional[float],
        message: str,
        context: Dict[str, Any],
        cron_expr: Optional[str] = None,
        tz_offset: Optional[int] = None,
    ) -> str:
        """
        Add a new job.

        Args:
            trigger_time: Unix timestamp for the first (or only) execution.
            message:      Content to send/process when the job fires.
            context:      {channel, chat_id, sender_id, ...}
            cron_expr:    Standard cron expression for repeating jobs.
            tz_offset:    Timezone offset in minutes (e.g. -360 for UTC-6).
        """
        async with self.lock:
            if cron_expr and not trigger_time:
                if not croniter:
                    raise ImportError(
                        "croniter library is required for cron expressions."
                    )
                tz = (
                    timezone(timedelta(minutes=tz_offset))
                    if tz_offset is not None
                    else timezone.utc
                )
                base = datetime.fromtimestamp(time.time(), tz=tz)

                cron_it = croniter(cron_expr, base)
                trigger_time = cron_it.get_next(float)

            job_id = str(uuid.uuid4())[:8]
            job = {
                "id": job_id,
                "trigger": trigger_time,
                "cron_expr": cron_expr,
                "tz_offset": tz_offset,
                "active": True,
                "payload": message,
                "context": context,
                "created_at": time.time(),
            }
            self.jobs.append(job)
            self._save_jobs()

            kind = f"repeating '{cron_expr}'" if cron_expr else "one-time"
            logger.info(f"Added {kind} job {job_id}: '{message}' at {trigger_time}")
            return job_id

    async def remove_job(self, job_id: str) -> bool:
        """Remove a job by ID. Returns True if found and removed."""
        async with self.lock:
            before = len(self.jobs)
            self.jobs = [j for j in self.jobs if j["id"] != job_id]
            if len(self.jobs) < before:
                self._save_jobs()
                logger.info(f"Removed job {job_id}")
                return True
            return False

    async def set_job_active(self, job_id: str, active: bool) -> Optional[Dict[str, Any]]:
        """Pause or resume a job. Returns the updated job if found."""
        async with self.lock:
            for job in self.jobs:
                if job["id"] != job_id:
                    continue
                job["active"] = bool(active)
                self._save_jobs()
                logger.info(
                    f"{'Resumed' if active else 'Paused'} job {job_id}"
                )
                return dict(job)
        return None

    async def list_jobs(self) -> List[Dict[str, Any]]:
        """Return a sorted snapshot of all pending jobs."""
        async with self.lock:
            return sorted(
                [j for j in self.jobs if j.get("trigger") is not None],
                key=lambda x: x["trigger"],
            )

    async def register_system_jobs(self, channels: list) -> None:
        """
        Register built-in system jobs (e.g., greetings, check-ins).
        'channels' should be a list of dicts: [{'channel': 'discord', 'chat_id': '...'}]
        """
        existing_payloads = {j["payload"] for j in self.jobs}

        for ch in channels:
            chat_id = ch["chat_id"]
            channel = ch["channel"]
            ctx = {
                "channel": channel,
                "chat_id": chat_id,
                "sender_id": ch.get("sender_id", "system"),
            }

            greeting_payload = f"@morning_greeting::{chat_id}"
            if greeting_payload not in existing_payloads:
                await self.add_job(
                    trigger_time=None,
                    message=greeting_payload,
                    context=ctx,
                    cron_expr="0 8 * * *",
                )
                logger.info(f"Registered morning greeting job for {chat_id}")

            checkin_payload = f"@silence_checkin::{chat_id}"
            if checkin_payload not in existing_payloads:
                await self.add_job(
                    trigger_time=None,
                    message=checkin_payload,
                    context=ctx,
                    cron_expr="0 10 * * *",
                )
                logger.info(f"Registered silence check-in job for {chat_id}")

    async def run(self) -> None:
        """Main loop — checks for triggered jobs every second."""
        self._running = True
        logger.info("Scheduler started.")

        while self._running:
            try:
                now = time.time()

                async with self.lock:
                    due = [
                        j
                        for j in self.jobs
                        if j.get("active", True)
                        and j.get("trigger") is not None
                        and j["trigger"] <= now
                    ]

                    for job in due:
                        job_id = job["id"]

                        if job.get("cron_expr"):
                            tz_off = job.get("tz_offset")
                            tz = (
                                timezone(timedelta(minutes=tz_off))
                                if tz_off is not None
                                else timezone.utc
                            )

                            lag = max(0.0, now - float(job["trigger"]))
                            scheduled_dt = datetime.fromtimestamp(job["trigger"], tz=tz)
                            cron_it = croniter(job["cron_expr"], scheduled_dt)
                            next_trigger = cron_it.get_next(float)
                            skipped_stale = lag > RECURRING_JOB_MAX_LAG_SECONDS

                            while next_trigger <= now:
                                skipped_stale = True
                                scheduled_dt = datetime.fromtimestamp(next_trigger, tz=tz)
                                cron_it = croniter(job["cron_expr"], scheduled_dt)
                                next_trigger = cron_it.get_next(float)

                            job["trigger"] = next_trigger
                            logger.info(
                                f"Rescheduled job {job_id} → {job['trigger']} (tz_offset={tz_off})"
                            )
                            if skipped_stale:
                                logger.info(
                                    f"Skipping stale recurring job {job_id} (lag={lag:.1f}s)"
                                )
                                continue

                            # ── Dedup: skip if already fired in this same period ──
                            last_fired = _job_last_fired.get(job_id, 0.0)
                            if (now - last_fired) < lag + 1.0:
                                logger.debug(
                                    f"Dedup: skipping duplicate fire for job {job_id} "
                                    f"(last_fired={last_fired:.1f}, lag={lag:.1f}s)"
                                )
                                continue

                        else:
                            self.jobs = [j for j in self.jobs if j["id"] != job_id]

                        _job_last_fired[job_id] = now
                        asyncio.create_task(self._execute_job(job))

                    if due:
                        self._save_jobs()

                await asyncio.sleep(1)

            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"Scheduler loop error: {e}")
                await asyncio.sleep(5)

        logger.info("Scheduler stopped.")

    async def stop(self) -> None:
        """Signal the run loop to exit."""
        self._running = False

    async def _execute_job(self, job: Dict[str, Any]) -> None:
        """Fire a triggered job by publishing an inbound message."""
        logger.info(f"Executing job {job['id']}: {job['payload']}")
        await asyncio.to_thread(
            self._append_run_event,
            job["id"],
            {
                "type": "job_started",
                "payload": job["payload"],
                "context": job.get("context", {}),
            },
        )

        context = job.get("context", {})
        msg = InboundMessage(
            channel=context.get("channel", "unknown"),
            sender_id=context.get("sender_id", "unknown"),
            chat_id=context.get("chat_id", "unknown"),
            content=f"[SCHEDULER] {job['payload']}",
            metadata={
                "is_scheduler": True,
                "original_job_id": job["id"],
                "reply_to": context.get("sender_id", "unknown"),
            },
        )
        try:
            await self.bus.publish_inbound(msg)
            await asyncio.to_thread(
                self._append_run_event,
                job["id"],
                {
                    "type": "job_published",
                    "payload": job["payload"],
                    "channel": context.get("channel", "unknown"),
                    "chat_id": context.get("chat_id", "unknown"),
                },
            )
        except Exception as e:
            logger.error(f"Failed to publish job {job['id']}: {e}")
            await asyncio.to_thread(
                self._append_run_event,
                job["id"],
                {
                    "type": "job_error",
                    "payload": job["payload"],
                    "error": str(e),
                },
            )
