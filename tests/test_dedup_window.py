import asyncio
import unittest
import uuid


class TestDedupWindow(unittest.IsolatedAsyncioTestCase):
    async def test_identical_message_only_deduped_within_2s(self):
        try:
            import loguru  # noqa: F401
        except Exception:
            raise unittest.SkipTest("Missing dependencies (loguru).")

        from core.bus import MessageBus
        from core.events import InboundMessage
        from core.loop import AgentLoop

        class _TestAgentLoop(AgentLoop):
            async def _init_skills_and_tools(self) -> None:
                self._tool_definitions = []
                self._warmed = True

            async def _llm_call_with_retry(self, *args, **kwargs):
                return None

            async def _consume_stream(self, *args, **kwargs):
                return ("ok", [], {"total_tokens": 2})

            async def _build_full_system_prompt(self, *args, **kwargs):
                return "SYSTEM: TEST"

            async def _trim_history(self, *args, **kwargs):
                return

        bus = MessageBus()
        agent = _TestAgentLoop(bus=bus)
        chat_id = f"dedup_{uuid.uuid4().hex[:10]}"

        msg = InboundMessage(
            channel="web",
            sender_id="dedup-user",
            chat_id=chat_id,
            content="hello",
            metadata={},
        )

        await agent._process_message(msg)
        first_len = len(agent.history[msg.session_key])

        await agent._process_message(msg)
        second_len = len(agent.history[msg.session_key])
        self.assertEqual(
            second_len,
            first_len,
            "Duplicate message inside 2s should be skipped.",
        )

        await asyncio.sleep(2.1)
        await agent._process_message(msg)
        third_len = len(agent.history[msg.session_key])

        self.assertGreater(
            third_len,
            second_len,
            "Same message after 2s should be processed again.",
        )

