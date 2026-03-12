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


class CronManager:
    """Manages scheduled tasks and reminders."""

    CRON_MISFIRE_GRACE_SECONDS = 60

    def __init__(self, bus: Any):
        self.bus = bus
        self.jobs: List[Dict[str, Any]] = []
        self._running = False
        self.data_file = Path("data/cron.json")
        self.lock = asyncio.Lock()
        self._load_jobs()

    @staticmethod
    def _get_timezone(tz_offset: Optional[int]) -> timezone:
        return (
            timezone(timedelta(minutes=tz_offset))
            if tz_offset is not None
            else timezone.utc
        )

    def _next_cron_trigger(
        self,
        cron_expr: str,
        base_timestamp: float,
        tz_offset: Optional[int],
        min_timestamp: Optional[float] = None,
    ) -> float:
        if not croniter:
            raise ImportError("croniter library is required for cron expressions.")

        tz = self._get_timezone(tz_offset)
        base = datetime.fromtimestamp(base_timestamp, tz=tz)
        cron_it = croniter(cron_expr, base)
        next_trigger = cron_it.get_next(float)

        while min_timestamp is not None and next_trigger <= min_timestamp:
            next_trigger = cron_it.get_next(float)

        return next_trigger

    def _is_stale_recurring_job(self, job: Dict[str, Any], now: float) -> bool:
        trigger = job.get("trigger")
        if trigger is None or not job.get("cron_expr"):
            return False
        return (now - trigger) > self.CRON_MISFIRE_GRACE_SECONDS

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
            if cron_expr and trigger_time is None:
                trigger_time = self._next_cron_trigger(
                    cron_expr,
                    time.time(),
                    tz_offset,
                )

            job_id = str(uuid.uuid4())[:8]
            job = {
                "id": job_id,
                "trigger": trigger_time,
                "cron_expr": cron_expr,
                "tz_offset": tz_offset,
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
                        if j.get("trigger") is not None and j["trigger"] <= now
                    ]

                    for job in due:
                        if job.get("cron_expr"):
                            tz_off = job.get("tz_offset")
                            if self._is_stale_recurring_job(job, now):
                                previous_trigger = job["trigger"]
                                job["trigger"] = self._next_cron_trigger(
                                    job["cron_expr"],
                                    now,
                                    tz_off,
                                    min_timestamp=now,
                                )
                                logger.info(
                                    f"Skipped stale cron run for job {job['id']} "
                                    f"(trigger={previous_trigger}) → {job['trigger']} "
                                    f"(tz_offset={tz_off})"
                                )
                                continue

                            asyncio.create_task(self._execute_job(job))
                            job["trigger"] = self._next_cron_trigger(
                                job["cron_expr"],
                                job["trigger"],
                                tz_off,
                                min_timestamp=now,
                            )
                            logger.info(
                                f"Rescheduled job {job['id']} → {job['trigger']} "
                                f"(tz_offset={tz_off})"
                            )
                        else:
                            asyncio.create_task(self._execute_job(job))
                            self.jobs = [j for j in self.jobs if j["id"] != job["id"]]

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
        except Exception as e:
            logger.error(f"Failed to publish job {job['id']}: {e}")
