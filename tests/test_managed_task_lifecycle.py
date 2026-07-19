import asyncio
import json
import tempfile
import unittest
from types import SimpleNamespace
from pathlib import Path
from unittest.mock import AsyncMock, patch

from core.events import InboundMessage
from core.loop import AgentLoop
from core.managed_tasks import ManagedTaskRegistry
from core.task_tracker import TaskStatus, TaskTracker


class TestManagedTaskRegistry(unittest.IsolatedAsyncioTestCase):
    async def test_cancel_and_wait_are_idempotent(self):
        started = asyncio.Event()

        async def worker():
            started.set()
            await asyncio.Event().wait()

        handle = asyncio.create_task(worker())
        registry = ManagedTaskRegistry()
        await registry.register(
            "task-fixed",
            handle,
            kind="inbound_message",
            session_key="web_chat",
        )
        await registry.mark_running("task-fixed")
        await started.wait()

        first = await registry.cancel("task-fixed")
        second = await registry.cancel("task-fixed")
        observed = await registry.wait("task-fixed")

        self.assertEqual(first.status, "cancelled")
        self.assertEqual(second.status, "cancelled")
        self.assertEqual(observed.status, "cancelled")
        self.assertEqual([item.task_id for item in await registry.active()], [])

    async def test_terminal_completion_is_stable_after_handle_cleanup(self):
        handle = asyncio.create_task(asyncio.sleep(0))
        registry = ManagedTaskRegistry()
        await registry.register("task-done", handle)
        await handle

        first = await registry.finalize("task-done", "completed", result="ok")
        second = await registry.finalize("task-done", "failed", error="late failure")

        self.assertEqual(first.status, "completed")
        self.assertEqual(second.status, "completed")
        self.assertEqual(second.result, "ok")


class TestTaskTrackerIdempotency(unittest.IsolatedAsyncioTestCase):
    async def test_repeated_terminal_updates_do_not_duplicate_history(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            tracker = TaskTracker(data_dir=tmpdir)
            task_id = await tracker.create_task(
                "inbound_message",
                "Stable task",
                task_id="task-fixed",
            )
            await tracker.update_task(task_id, status=TaskStatus.RUNNING.value)
            completed = await tracker.update_task(
                task_id, status=TaskStatus.COMPLETED.value
            )
            late = await tracker.update_task(
                task_id,
                status=TaskStatus.FAILED.value,
                error="late failure",
            )
            history = await tracker.list_tasks()

        self.assertEqual(completed.status, TaskStatus.COMPLETED.value)
        self.assertEqual(late.status, TaskStatus.COMPLETED.value)
        self.assertEqual(len(history), 1)

    async def test_restart_recovers_flushed_active_task_as_terminal_failure(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            tracker = TaskTracker(data_dir=tmpdir)
            task_id = await tracker.create_task(
                "subagent_job",
                "Interrupted work",
                task_id="task-restart",
            )
            await tracker.update_task(task_id, status=TaskStatus.RUNNING.value)
            tracker._flush_sync()

            restarted = TaskTracker(data_dir=tmpdir)
            active = await restarted.get_active_tasks()
            recovered = await restarted.get_task(task_id)
            snapshot = json.loads((Path(tmpdir) / "tasks.json").read_text())

        self.assertEqual(active, [])
        self.assertEqual(recovered.status, TaskStatus.FAILED.value)
        self.assertEqual(
            recovered.error,
            "Runtime restarted before task completed.",
        )
        self.assertTrue(recovered.metadata["recovered_from_restart"])
        self.assertEqual(recovered.metadata["previous_status"], TaskStatus.RUNNING.value)
        self.assertEqual(snapshot["active"], [])
        self.assertEqual([item["task_id"] for item in snapshot["history"]], [task_id])


class TestSessionEventEnvelope(unittest.IsolatedAsyncioTestCase):
    async def test_event_ids_and_sequences_survive_concurrent_writes_and_restart(self):
        from core.session_manager import SessionManager

        with tempfile.TemporaryDirectory() as tmpdir:
            events_dir = Path(tmpdir) / "events"
            events_dir.mkdir()
            with patch("core.session_manager.EVENTS_DIR", events_dir):
                manager = SessionManager()
                await asyncio.gather(
                    *(
                        manager.append_event_log("web_chat", {"type": "progress"})
                        for _ in range(3)
                    )
                )
                restarted = SessionManager()
                await restarted.append_event_log("web_chat", {"type": "complete"})

            rows = [
                json.loads(line)
                for line in (events_dir / "web_chat.jsonl").read_text().splitlines()
            ]

        self.assertEqual([row["sequence"] for row in rows], [1, 2, 3, 4])
        self.assertEqual(len({row["event_id"] for row in rows}), 4)
        self.assertTrue(all(row["schema_version"] == 1 for row in rows))


class TestInboundDispatchLifecycle(unittest.IsolatedAsyncioTestCase):
    async def test_dispatch_registers_before_processing_and_cancellation_closes_task(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            tracker = TaskTracker(data_dir=tmpdir)
            loop = AgentLoop.__new__(AgentLoop)
            loop.active_tasks = {}
            loop.task_registry = ManagedTaskRegistry()
            loop.bus = SimpleNamespace(publish_outbound=AsyncMock())

            async def never_process(*args, **kwargs):
                await asyncio.Event().wait()

            loop._process_message = AsyncMock(side_effect=never_process)
            message = InboundMessage(
                channel="web",
                sender_id="user-1",
                chat_id="chat-1",
                content="hang safely",
            )

            with patch("core.task_tracker._instance", tracker):
                handle = await loop._dispatch_message(message)
                active = await loop.task_registry.active()
                self.assertEqual(len(active), 1)
                self.assertEqual(active[0].session_key, message.session_key)

                await loop.task_registry.cancel(active[0].task_id)
                await asyncio.gather(handle, return_exceptions=True)
                durable = await tracker.get_task(active[0].task_id)

        self.assertEqual(durable.status, TaskStatus.CANCELLED.value)
        self.assertNotIn(message.session_key, loop.active_tasks)
        loop.bus.publish_outbound.assert_awaited_once()
        terminal = loop.bus.publish_outbound.await_args.args[0]
        self.assertEqual(terminal.metadata["type"], "stop_typing")
        self.assertEqual(terminal.metadata["task_status"], TaskStatus.CANCELLED.value)


if __name__ == "__main__":
    unittest.main()
