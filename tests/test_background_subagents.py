import asyncio
import tempfile
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, patch

from core.loop import AgentLoop
from core.task_tracker import TaskStatus, TaskTracker, TaskType


class TestBackgroundSubagents(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.tracker = TaskTracker(data_dir=self.temp_dir.name)
        self.loop = AgentLoop.__new__(AgentLoop)
        self.loop.background_subagent_tasks = {}
        self.loop.background_subagent_sessions = {}
        self.loop.background_subagent_parents = {}
        self.loop.run_subagent = AsyncMock(return_value="--- SUB-AGENT REPORT ---\nDone")
        self.tracker_patch = patch("core.task_tracker._instance", self.tracker)
        self.tracker_patch.start()

    async def asyncTearDown(self):
        self.tracker_patch.stop()
        self.temp_dir.cleanup()

    async def test_background_job_has_stable_id_and_idempotent_completion(self):
        task_id = await self.loop.start_background_subagent(
            "web:chat",
            "web:chat_sub_abc123",
            "Inspect the repository",
        )
        self.assertEqual(len(task_id), 12)
        self.assertIn(task_id, self.loop.background_subagent_tasks)

        await self.loop.wait_background_subagent_task(task_id)
        task = await self.loop.get_background_subagent_task(task_id)
        self.assertEqual(task.type, TaskType.SUBAGENT_JOB.value)
        self.assertEqual(task.status, TaskStatus.COMPLETED.value)
        self.assertNotIn(task_id, self.loop.background_subagent_tasks)

        # A second wait observes the same terminal record, without a duplicate
        # history entry or a second subagent invocation.
        await self.loop.wait_background_subagent_task(task_id)
        history = await self.tracker.list_tasks(type_filter=TaskType.SUBAGENT_JOB.value)
        self.assertEqual(len(history), 1)
        self.loop.run_subagent.assert_awaited_once()

    async def test_kill_cancels_live_handle_and_marks_task_cancelled(self):
        started = asyncio.Event()

        async def never_finishes(*args, **kwargs):
            started.set()
            await asyncio.Event().wait()

        self.loop.run_subagent = AsyncMock(side_effect=never_finishes)
        task_id = await self.loop.start_background_subagent(
            "web:chat",
            "web:chat_sub_deadbe",
            "Wait forever",
        )
        await asyncio.wait_for(started.wait(), timeout=1)

        task = await self.loop.kill_background_subagent_task(task_id)
        self.assertEqual(task.status, TaskStatus.CANCELLED.value)
        self.assertNotIn(task_id, self.loop.background_subagent_tasks)

    async def test_model_facing_get_and_kill_task_tools_use_stable_id(self):
        task_id = await self.loop.start_background_subagent(
            "web:chat",
            "web:chat_sub_tools",
            "Return a report",
        )
        await self.loop.wait_background_subagent_task(task_id)
        output = await self.loop._execute_background_task_tool(
            "get_task_output", {"task_ids": [task_id]}
        )
        self.assertIn(f"Task {task_id}", output)
        self.assertIn("Status: completed", output)

        killed = await self.loop._execute_background_task_tool(
            "kill_task", {"task_id": task_id}
        )
        self.assertIn("already exited", killed)


if __name__ == "__main__":
    unittest.main()
