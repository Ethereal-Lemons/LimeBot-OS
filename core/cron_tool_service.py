"""Scheduler-facing operations for LimeBot's cron tools."""

from __future__ import annotations

import time
from datetime import datetime
from typing import Any, Dict, Optional

from core.profile_context import get_current_profile_context


class CronToolService:
    """Translate native cron tool inputs into durable scheduler operations."""

    def __init__(self, scheduler: Any = None) -> None:
        self.scheduler = scheduler

    def set_scheduler(self, scheduler: Any) -> None:
        self.scheduler = scheduler

    async def add(
        self,
        message: str,
        context: Dict[str, Any],
        time_expr: Optional[str] = None,
        cron_expr: Optional[str] = None,
        tz: Optional[str] = None,
        name: Optional[str] = None,
    ) -> str:
        if not self.scheduler:
            return "Error: Scheduler not available."
        try:
            job_context = dict(context or {})
            profile_context = get_current_profile_context()
            job_context["profile_id"] = (
                profile_context.profile_id
                if profile_context is not None
                else str(job_context.get("profile_id") or "default")
            )
            trigger_time = None
            if time_expr:
                delta_seconds = 0
                if time_expr.endswith("s"):
                    delta_seconds = int(time_expr[:-1])
                elif time_expr.endswith("m"):
                    delta_seconds = int(time_expr[:-1]) * 60
                elif time_expr.endswith("h"):
                    delta_seconds = int(time_expr[:-1]) * 3600
                elif time_expr.endswith("d"):
                    delta_seconds = int(time_expr[:-1]) * 86400
                else:
                    return f"Error: Invalid time format '{time_expr}'. Use '10s', '5m', '2h'."
                trigger_time = time.time() + delta_seconds

            job_id = await self.scheduler.add_job(
                trigger_time=trigger_time,
                message=message,
                context=job_context,
                cron_expr=cron_expr,
                tz=tz,
                name=name,
            )
            return f"Success: Scheduled job {job_id}"
        except Exception as error:
            return f"Error adding cron: {error}"

    async def list(self) -> str:
        if not self.scheduler:
            return "Error: Scheduler not available."
        try:
            jobs = await self.scheduler.list_jobs()
            if not jobs:
                return "No scheduled tasks."

            lines = ["Scheduled Jobs:"]
            for job in jobs:
                trigger = (
                    datetime.fromtimestamp(job["trigger"]).strftime("%Y-%m-%d %H:%M:%S")
                    if job.get("trigger")
                    else "N/A"
                )
                recurrence = f" (RECURS: {job['cron_expr']})" if job.get("cron_expr") else ""
                status = "PAUSED" if job.get("active") is False else "ACTIVE"
                state = job.get("state") or {}
                last_status = state.get("lastStatus") or state.get("last_status")
                duration = state.get("lastDurationMs") or state.get("last_duration_ms")
                name = job.get("name") or job["id"]
                state_bits = []
                if last_status:
                    state_bits.append(f"last={last_status}")
                if duration is not None:
                    state_bits.append(f"{duration}ms")
                state_suffix = f" [{' '.join(state_bits)}]" if state_bits else ""
                lines.append(
                    f" - [{job['id']}] [{status}] {name}: {trigger}{recurrence}{state_suffix}\n"
                    f"   {job['payload']}"
                )
            return "\n".join(lines)
        except Exception as error:
            return f"Error listing cron: {error}"

    async def remove(self, job_id: str) -> str:
        if not self.scheduler:
            return "Error: Scheduler not available."
        try:
            if await self.scheduler.remove_job(job_id):
                return f"Success: Removed job {job_id}."
            return f"Error: Job {job_id} not found."
        except Exception as error:
            return f"Error removing cron: {error}"

    async def set_active(self, job_id: str, active: bool) -> str:
        action = "activating" if active else "deactivating"
        success = "Activated" if active else "Deactivated"
        if not self.scheduler:
            return "Error: Scheduler not available."
        try:
            updated = await self.scheduler.set_job_active(job_id, active)
            if updated:
                return f"Success: {success} job {job_id}."
            return f"Error: Job {job_id} not found."
        except Exception as error:
            return f"Error {action} cron: {error}"
