import asyncio
import shutil
import time
import unittest
import uuid
from pathlib import Path


class _TestBus:
    def __init__(self):
        self.messages = []

    async def publish_inbound(self, msg):
        self.messages.append(msg)


class TestScheduler(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        try:
            import loguru  # noqa: F401
        except Exception:
            raise unittest.SkipTest("Missing dependencies (loguru).")

        from core.scheduler import croniter

        if croniter is None:
            raise unittest.SkipTest("Missing dependency (croniter).")

        self.temp_dir = Path("temp") / f"scheduler_test_{uuid.uuid4().hex[:8]}"
        self.temp_dir.mkdir(parents=True, exist_ok=True)

    async def asyncTearDown(self):
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    async def test_stale_recurring_job_is_skipped_instead_of_replayed(self):
        from core.scheduler import CronManager

        class _TestCronManager(CronManager):
            def __init__(self, bus, data_file):
                self.bus = bus
                self.jobs = []
                self._running = False
                self.data_file = data_file
                self.lock = asyncio.Lock()

        bus = _TestBus()
        scheduler = _TestCronManager(bus, self.temp_dir / "cron.json")
        now = time.time()
        scheduler.jobs = [
            {
                "id": "stale123",
                "trigger": now - 7200,
                "cron_expr": "0 */4 * * *",
                "tz_offset": None,
                "payload": "@reflect_and_distill",
                "context": {
                    "channel": "system",
                    "chat_id": "global_reflection",
                    "sender_id": "maintenance",
                },
                "created_at": now - 10800,
            }
        ]

        task = asyncio.create_task(scheduler.run())
        try:
            await asyncio.sleep(1.2)
        finally:
            await scheduler.stop()
            await asyncio.wait_for(task, timeout=2)

        self.assertEqual(
            len(bus.messages),
            0,
            "Stale recurring jobs should be skipped after downtime.",
        )
        self.assertGreater(
            scheduler.jobs[0]["trigger"],
            time.time(),
            "Skipped recurring jobs should advance to a future trigger.",
        )

    async def test_recent_recurring_job_executes_once_and_reschedules(self):
        from core.scheduler import CronManager

        class _TestCronManager(CronManager):
            def __init__(self, bus, data_file):
                self.bus = bus
                self.jobs = []
                self._running = False
                self.data_file = data_file
                self.lock = asyncio.Lock()

        bus = _TestBus()
        scheduler = _TestCronManager(bus, self.temp_dir / "cron_recent.json")
        now = time.time()
        scheduler.jobs = [
            {
                "id": "recent123",
                "trigger": now - 5,
                "cron_expr": "* * * * *",
                "tz_offset": None,
                "payload": "@reflect_and_distill",
                "context": {
                    "channel": "system",
                    "chat_id": "global_reflection",
                    "sender_id": "maintenance",
                },
                "created_at": now - 120,
            }
        ]

        task = asyncio.create_task(scheduler.run())
        try:
            await asyncio.sleep(1.2)
        finally:
            await scheduler.stop()
            await asyncio.wait_for(task, timeout=2)

        self.assertEqual(
            len(bus.messages),
            1,
            "Recently due recurring jobs should still execute once.",
        )
        self.assertGreater(
            scheduler.jobs[0]["trigger"],
            time.time(),
            "Recurring jobs should be rescheduled to a future trigger.",
        )
