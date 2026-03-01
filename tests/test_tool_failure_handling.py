import unittest


class TestToolFailureHandling(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        try:
            import loguru  # noqa: F401
        except Exception:
            raise unittest.SkipTest("Missing dependencies (loguru).")

    async def test_run_command_nonzero_exit_is_error(self):
        from core.bus import MessageBus
        from core.loop import AgentLoop

        class _TestAgentLoop(AgentLoop):
            async def _init_skills_and_tools(self) -> None:
                self._tool_definitions = []
                self._warmed = True

        bus = MessageBus()
        agent = _TestAgentLoop(bus=bus)

        self.assertTrue(
            agent._is_tool_result_error(
                "run_command", "download failed\n\nExit Code: 1"
            )
        )
        self.assertFalse(
            agent._is_tool_result_error("run_command", "done\n\nExit Code: 0")
        )

    async def test_tool_failure_with_empty_followup_emits_fallback_reply(self):
        from core.bus import MessageBus
        from core.events import InboundMessage
        from core.loop import AgentLoop

        class _TestAgentLoop(AgentLoop):
            def __init__(self, *args, **kwargs):
                super().__init__(*args, **kwargs)
                self._consume_calls = 0

            async def _init_skills_and_tools(self) -> None:
                self._tool_definitions = []
                self._warmed = True

            async def _llm_call_with_retry(self, *args, **kwargs):
                return object()

            async def _consume_stream(self, *args, **kwargs):
                self._consume_calls += 1
                if self._consume_calls == 1:
                    return (
                        "",
                        [
                            {
                                "id": "call_1",
                                "type": "function",
                                "function": {
                                    "name": "read_file",
                                    "arguments": '{"path":"missing.txt"}',
                                },
                            }
                        ],
                        None,
                        False,
                    )
                return ("", [], None, False)

            async def _execute_tool(
                self, function_name: str, function_args: dict, session_key: str
            ):
                return "Error: missing file"

            async def _build_full_system_prompt(self, *args, **kwargs):
                return "SYSTEM: TEST"

            async def _trim_history(self, *args, **kwargs):
                return

        bus = MessageBus()
        agent = _TestAgentLoop(bus=bus)

        msg = InboundMessage(
            channel="web",
            sender_id="tool-user",
            chat_id="tool-chat",
            content="run tool",
            metadata={},
        )

        await agent._process_message(msg)

        outbound = []
        while not bus.outbound.empty():
            outbound.append(await bus.consume_outbound())

        replies = [
            m
            for m in outbound
            if m.metadata.get("reply_to") == msg.sender_id and m.content
        ]
        self.assertTrue(replies, "Expected a user-visible fallback reply.")
        self.assertIn("failed", replies[-1].content.lower())
