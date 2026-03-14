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

    async def test_extract_tool_from_content_infers_browser_navigate_from_url_dict(self):
        tool_calls = self.agent._extract_tool_from_content(
            '{"url":"https://open.spotify.com/track/5SudOD9R1Of6CsJVWZy6CQ"}'
        )

        self.assertEqual(len(tool_calls), 1)
        self.assertEqual(tool_calls[0]["function"]["name"], "browser_navigate")
        self.assertEqual(
            tool_calls[0]["function"]["arguments"],
            '{"url": "https://open.spotify.com/track/5SudOD9R1Of6CsJVWZy6CQ"}',
        )

    async def test_extract_tool_from_content_recovers_legacy_xml_tool_tag(self):
        tool_calls = self.agent._extract_tool_from_content(
            "<run_command>echo hello</run_command>"
        )

        self.assertEqual(len(tool_calls), 1)
        self.assertEqual(tool_calls[0]["function"]["name"], "run_command")
        self.assertEqual(
            tool_calls[0]["function"]["arguments"],
            '{"command": "echo hello"}',
        )

    async def test_sanitize_tool_call_content_drops_implicit_browser_url_dict(self):
        self.assertEqual(
            self.agent._sanitize_tool_call_content(
                '{"url":"https://open.spotify.com/track/5SudOD9R1Of6CsJVWZy6CQ"}'
            ),
            "",
        )
