import unittest
from types import SimpleNamespace


class TestToolCallResidue(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        try:
            import loguru  # noqa: F401
        except Exception:
            raise unittest.SkipTest("Missing dependencies (loguru).")

        from core.bus import MessageBus
        from core.loop import AgentLoop

        class _TestAgentLoop(AgentLoop):
            async def _init_skills_and_tools(self) -> None:
                self._tool_definitions = []
                self._warmed = True

        self.bus = MessageBus()
        self.agent = _TestAgentLoop(bus=self.bus)

    @staticmethod
    def _chunk(content=None, tool_call=None):
        delta = SimpleNamespace(content=content, tool_calls=None)
        if tool_call is not None:
            delta.tool_calls = [tool_call]
        return SimpleNamespace(choices=[SimpleNamespace(delta=delta)], usage=None)

    def test_sanitize_tool_call_content_drops_orphan_fences(self):
        self.assertEqual(self.agent._sanitize_tool_call_content("}\n```"), "")
        self.assertEqual(
            self.agent._sanitize_tool_call_content("Let me check.\n}\n```"),
            "Let me check.",
        )

    async def test_consume_stream_does_not_publish_residue_only_tool_preamble(self):
        from core.events import InboundMessage

        tool_call = SimpleNamespace(
            index=0,
            id="call_1",
            function=SimpleNamespace(name="list_dir", arguments='{"path":"."}'),
        )

        async def _stream():
            yield self._chunk(content="}\n```")
            yield self._chunk(tool_call=tool_call)

        msg = InboundMessage(
            channel="web",
            sender_id="web-user",
            chat_id="chat-1",
            content="ok do it",
        )
        result = await self.agent._consume_stream(_stream(), msg, msg.session_key)
        full_content, tool_calls, _, streamed_to_web = self.agent._unpack_stream_result(
            result
        )

        self.assertEqual(full_content, "")
        self.assertFalse(streamed_to_web)
        self.assertEqual(len(tool_calls), 1)
        self.assertEqual(tool_calls[0]["function"]["name"], "list_dir")
        self.assertTrue(self.bus.outbound.empty())
