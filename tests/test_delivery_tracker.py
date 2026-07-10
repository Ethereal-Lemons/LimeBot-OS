import asyncio
import json
import tempfile
import threading
import time
import unittest
from unittest.mock import patch

import core.delivery_tracker as delivery_module
from core.delivery_tracker import DeliveryTracker


class DeliveryTrackerPersistenceTests(unittest.IsolatedAsyncioTestCase):
    async def test_mutations_do_not_wait_for_blocked_writer_and_flushes_latest(self):
        with tempfile.TemporaryDirectory() as directory:
            tracker = DeliveryTracker(directory, debounce_seconds=0)
            entered = threading.Event()
            release = threading.Event()
            original_write = tracker._write_snapshot_sync

            def blocked_write(payload):
                entered.set()
                release.wait(1.0)
                original_write(payload)

            tracker._write_snapshot_sync = blocked_write
            delivery_id = await tracker.track_delivery("web", "chat")
            await asyncio.to_thread(entered.wait, 0.5)
            started = time.perf_counter()
            await tracker.mark_sending(delivery_id)
            await tracker.mark_sent(delivery_id)
            self.assertLess(time.perf_counter() - started, 0.1)
            flush_task = asyncio.create_task(tracker.flush())
            await asyncio.sleep(0)
            self.assertFalse(flush_task.done())
            release.set()
            await asyncio.wait_for(flush_task, 1.0)
            payload = json.loads(tracker._data_file.read_text(encoding="utf-8"))
            self.assertEqual(payload["active"], [])
            self.assertEqual(payload["history"][-1]["status"], "sent")

    async def test_history_is_bounded_to_500(self):
        with tempfile.TemporaryDirectory() as directory:
            tracker = DeliveryTracker(directory, debounce_seconds=60)
            for index in range(510):
                delivery_id = await tracker.track_delivery("web", str(index))
                await tracker.mark_sent(delivery_id)
            self.assertEqual(len(await tracker.list_deliveries(limit=1000)), 500)
            if tracker._writer_task:
                tracker._writer_task.cancel()
                await asyncio.gather(tracker._writer_task, return_exceptions=True)

    async def test_agent_shutdown_flushes_dirty_delivery_before_returning(self):
        from core.loop import AgentLoop

        class _Metrics:
            def flush(self, _timeout):
                return True

        with tempfile.TemporaryDirectory() as directory:
            tracker = DeliveryTracker(directory, debounce_seconds=60)
            delivery_id = await tracker.track_delivery("web", "chat")
            await tracker.mark_sent(delivery_id)
            agent = object.__new__(AgentLoop)
            agent._running = True
            agent._initialization_task = None
            agent.metrics = _Metrics()

            with patch.object(delivery_module, "_instance", tracker):
                await agent.stop()

            payload = json.loads(tracker._data_file.read_text(encoding="utf-8"))
            self.assertEqual(payload["history"][-1]["delivery_id"], delivery_id)
            self.assertEqual(payload["history"][-1]["status"], "sent")


if __name__ == "__main__":
    unittest.main()
