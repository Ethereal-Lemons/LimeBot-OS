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
    def _chunk(content=None, tool_call=None, thinking=None):
        delta = SimpleNamespace(content=content, tool_calls=None)
        if tool_call is not None:
            delta.tool_calls = [tool_call]
        if thinking is not None:
            delta.reasoning_content = thinking
        return SimpleNamespace(choices=[SimpleNamespace(delta=delta)], usage=None)

    def test_sanitize_tool_call_content_drops_orphan_fences(self):
        self.assertEqual(self.agent._sanitize_tool_call_content("}\n```"), "")
        self.assertEqual(
            self.agent._sanitize_tool_call_content("Let me check.\n}\n```"),
            "Let me check.",
        )
        self.assertEqual(
            self.agent._sanitize_tool_call_content(
                '{"name":"run_command","arguments":{"command":"echo hi"}} '
                "Therefresh voice note in English just landed in your DMs."
            ),
            "",
        )
        self.assertEqual(
            self.agent._sanitize_tool_call_content(
                "Sending that now.\n"
                'functions.run_command:1{"command":"echo hi"}\n'
                "Extra promotional fluff."
            ),
            "Sending that now.",
        )
        self.assertEqual(
            self.agent._sanitize_tool_call_content(
                "Voy a hacerlo.\n<read_file>index.html</read_file>\nMas texto."
            ),
            "Voy a hacerlo.",
        )
        self.assertEqual(
            self.agent._sanitize_tool_call_content(
                'Voy a hacerlo.\n<tool_code>list_dir("C:/tmp")</tool_code>\nMas texto.'
            ),
            "Voy a hacerlo.",
        )
        self.assertEqual(
            self.agent._sanitize_tool_call_content('Checking now.\n{"path":"."}'),
            "Checking now.",
        )
        self.assertEqual(
            self.agent._sanitize_tool_call_content(
                'Checking now.\n{"path":"C:\\Users\\brite\\OneDrive\\Images\\LimeBot-OS"}'
            ),
            "Checking now.",
        )
        self.assertEqual(
            self.agent._sanitize_tool_call_content(
                'Checking now.\n<|list_dir|>{"path": "."}<|/list_dir|>\nDone.'
            ),
            "Checking now.",
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

    async def test_consume_stream_scrubs_streamed_tool_args_from_web_message(self):
        from core.events import InboundMessage

        tool_call = SimpleNamespace(
            index=0,
            id="call_1",
            function=SimpleNamespace(name="list_dir", arguments='{"path":"."}'),
        )

        async def _stream():
            yield self._chunk(content='Checking now.\n{"path":"."}')
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

        self.assertEqual(full_content, "Checking now.")
        self.assertTrue(streamed_to_web)
        self.assertEqual(len(tool_calls), 1)

        events = []
        while not self.bus.outbound.empty():
            events.append(await self.bus.outbound.get())

        self.assertEqual(len(events), 2)
        self.assertEqual(events[0].metadata.get("type"), "chunk")
        self.assertEqual(events[1].metadata.get("type"), "full_content")
        self.assertEqual(events[1].content, "Checking now.")

    async def test_consume_stream_extracts_tool_from_reasoning_content(self):
        from core.events import InboundMessage

        async def _stream():
            yield self._chunk(thinking='We should call list_dir.\n<list_dir path=".">')

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
        self.assertEqual(tool_calls[0]["function"]["arguments"], '{"path": "."}')

    async def test_consume_stream_extracts_shell_cmd_array_from_reasoning_content(self):
        from core.events import InboundMessage

        async def _stream():
            yield self._chunk(thinking='{"cmd":["bash","-lc","ls -R"]}')

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
        self.assertEqual(tool_calls[0]["function"]["arguments"], '{"path": "."}')

    async def test_consume_stream_pairs_reasoning_tool_hint_with_args_blob(self):
        from core.events import InboundMessage

        async def _stream():
            yield self._chunk(thinking='Use list_dir(".").')
            yield self._chunk(content='{"path": "."}')

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
        self.assertEqual(tool_calls[0]["function"]["arguments"], '{"path": "."}')

    async def test_consume_stream_pairs_reasoning_tool_hint_with_windows_path_blob(self):
        from core.events import InboundMessage

        async def _stream():
            yield self._chunk(
                thinking='Use list_dir("C:/Users/brite/OneDrive/Images/LimeBot-OS").'
            )
            yield self._chunk(
                content='{"path":"C:\\Users\\brite\\OneDrive\\Images\\LimeBot-OS"}'
            )

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
        self.assertEqual(
            tool_calls[0]["function"]["arguments"],
            '{"path": "C:/Users/brite/OneDrive/Images/LimeBot-OS"}',
        )
