import unittest


class TestToolCallNormalization(unittest.IsolatedAsyncioTestCase):
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

        self.agent = _TestAgentLoop(bus=MessageBus())

    async def test_parse_tool_call_repairs_invalid_json_args(self):
        tool_call = {
            "id": "call_1",
            "type": "function",
            "function": {
                "name": "download_image",
                "arguments": '{"url":"https://example.com/image.png}',
            },
        }

        _, function_name, function_args = self.agent._parse_tool_call(
            tool_call, "web_chat-1"
        )

        self.assertEqual(function_name, "download_image")
        self.assertEqual(function_args, {})
        self.assertEqual(tool_call["function"]["arguments"], "{}")

    async def test_sanitize_messages_for_llm_repairs_history_in_place(self):
        messages = [
            {
                "role": "assistant",
                "content": "",
                "tool_calls": [
                    {
                        "id": "call_1",
                        "type": "function",
                        "function": {
                            "name": "download_image",
                            "arguments": '{"url":"https://example.com/image.png}',
                        },
                    }
                ],
            }
        ]

        sanitized = self.agent._sanitize_messages_for_llm(messages, "web_chat-1")

        self.assertIs(sanitized, messages)
        self.assertEqual(
            messages[0]["tool_calls"][0]["function"]["arguments"],
            "{}",
        )
