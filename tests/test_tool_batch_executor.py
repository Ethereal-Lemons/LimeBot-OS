import asyncio
import unittest


class TestToolBatchExecutor(unittest.IsolatedAsyncioTestCase):
    async def test_reads_overlap_but_stateful_calls_remain_barriers(self):
        from core.tool_batch_executor import ToolBatchExecutor

        executor = ToolBatchExecutor()
        timeline = []
        active_reads = 0
        max_active_reads = 0

        async def execute_one(tool_call):
            nonlocal active_reads, max_active_reads
            tool_name = tool_call["function"]["name"]
            label = tool_call["id"]
            timeline.append(f"start:{label}")
            if tool_name == "read_file":
                active_reads += 1
                max_active_reads = max(max_active_reads, active_reads)
                await asyncio.sleep(0.02 if label == "read_a" else 0.01)
                active_reads -= 1
            elif tool_name == "write_file":
                self.assertEqual(active_reads, 0)
                await asyncio.sleep(0)
            elif tool_name == "run_command":
                self.assertEqual(active_reads, 0)
                timeline.append("verified-after-write")
            timeline.append(f"done:{label}")
            return label

        calls = [
            {"id": "read_a", "function": {"name": "read_file"}},
            {"id": "read_b", "function": {"name": "read_file"}},
            {"id": "write", "function": {"name": "write_file"}},
            {"id": "verify", "function": {"name": "run_command"}},
            {"id": "read_c", "function": {"name": "read_file"}},
        ]

        outcomes = await executor.execute_ordered(calls, execute_one)

        self.assertEqual(
            outcomes,
            ["read_a", "read_b", "write", "verify", "read_c"],
        )
        self.assertEqual(max_active_reads, 2)
        self.assertLess(
            timeline.index("done:read_a"), timeline.index("start:write")
        )
        self.assertLess(
            timeline.index("done:read_b"), timeline.index("start:write")
        )
        self.assertLess(timeline.index("done:write"), timeline.index("start:verify"))
        self.assertLess(timeline.index("done:verify"), timeline.index("start:read_c"))

    async def test_outcomes_are_consumed_in_model_order(self):
        from core.tool_batch_executor import ToolBatchExecutor

        executor = ToolBatchExecutor()
        consumed = []

        async def execute_one(tool_call):
            label = tool_call["id"]
            if label == "read_a":
                await asyncio.sleep(0.02)
            return label

        async def consume_outcome(index, tool_call, outcome):
            consumed.append((index, tool_call["id"], outcome))

        calls = [
            {"id": "read_a", "function": {"name": "read_file"}},
            {"id": "read_b", "function": {"name": "read_file"}},
            {"id": "write", "function": {"name": "write_file"}},
        ]

        await executor.execute_ordered(
            calls, execute_one, on_outcome=consume_outcome
        )

        self.assertEqual(
            consumed,
            [
                (0, "read_a", "read_a"),
                (1, "read_b", "read_b"),
                (2, "write", "write"),
            ],
        )
