import asyncio
import unittest
import uuid


class TestDedupWindow(unittest.IsolatedAsyncioTestCase):
    async def test_reply_dedup_handles_glued_exact_repeat(self):
        try:
            import loguru  # noqa: F401
        except Exception:
            raise unittest.SkipTest("Missing dependencies (loguru).")

        from core.loop import AgentLoop

        reply = (
            "I’ll set it up, but I need to be careful here: I can only schedule "
            "the reminder if I can target a valid delivery context. For a "
            "Discord DM, I need the bot to have an actual DM-capable context "
            "or a channel ID it can send to.\n\n"
            "If you want, I can still help you do either of these right now:\n"
            "1. **Find the Discord DM-capable channel/recipient setup**, or\n"
            "2. **Set it to a specific server channel** you can confirm.\n\n"
            "If you already know the DM setup works for this ID, I can proceed "
            "once you confirm the bot can message that target."
        )

        duplicated = reply + reply

        self.assertEqual(
            AgentLoop._dedupe_repeated_reply_sections(duplicated),
            reply,
        )

    async def test_reply_dedup_handles_repeated_paragraph_run(self):
        try:
            import loguru  # noqa: F401
        except Exception:
            raise unittest.SkipTest("Missing dependencies (loguru).")

        from core.loop import AgentLoop

        reply = "\n\n".join(
            [
                "First paragraph with enough content to resemble a real reply.",
                "Second paragraph that should only appear once.",
                "A useful final note that is not part of the repeated run.",
                "First paragraph with enough content to resemble a real reply.",
                "Second paragraph that should only appear once.",
            ]
        )

        self.assertEqual(
            AgentLoop._dedupe_repeated_reply_sections(reply),
            "\n\n".join(
                [
                    "First paragraph with enough content to resemble a real reply.",
                    "Second paragraph that should only appear once.",
                    "A useful final note that is not part of the repeated run.",
                ]
            ),
        )

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

    async def test_identical_message_from_different_sender_is_not_deduped(self):
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

        first = InboundMessage(
            channel="discord",
            sender_id="user-a",
            chat_id=chat_id,
            content="una big mac o un burrito, elige 1",
            metadata={"mentioned": True, "message_id": "111"},
        )
        second = InboundMessage(
            channel="discord",
            sender_id="user-b",
            chat_id=chat_id,
            content="una big mac o un burrito, elige 1",
            metadata={"mentioned": True, "message_id": "222"},
        )

        await agent._process_message(first)
        first_len = len(agent.history[first.session_key])

        await agent._process_message(second)
        second_len = len(agent.history[first.session_key])

        self.assertGreater(
            second_len,
            first_len,
            "Same content from a different sender should still be processed.",
        )
