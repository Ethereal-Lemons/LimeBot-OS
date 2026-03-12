import unittest


class TestMultimodalFallback(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        try:
            import loguru  # noqa: F401
        except Exception:
            raise unittest.SkipTest("Missing dependencies (loguru).")

    async def test_multimodal_error_downgrades_messages_to_text(self):
        from core.bus import MessageBus
        from core.loop import AgentLoop

        class _TestAgentLoop(AgentLoop):
            async def _init_skills_and_tools(self) -> None:
                self._tool_definitions = []
                self._warmed = True

        agent = _TestAgentLoop(bus=MessageBus())
        messages = [
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": "What is in this image?"},
                    {
                        "type": "image_url",
                        "image_url": {"url": "https://cdn.example.com/cat.png"},
                    },
                ],
            }
        ]
        error = Exception("/config/models/kimi_k2 is not a multimodal model")

        should_retry = agent._should_retry_without_images(error, messages)
        downgraded = agent._downgrade_image_messages_for_text_model(
            messages, "discord_chat-1"
        )
        agent._disable_image_inputs_for_session("discord_chat-1")

        self.assertTrue(should_retry)
        self.assertTrue(downgraded)
        self.assertIsInstance(messages[0]["content"], str)
        self.assertIn("does not support vision", messages[0]["content"])
        self.assertIn("https://cdn.example.com/cat.png", messages[0]["content"])
        self.assertTrue(agent._image_inputs_disabled_for_session("discord_chat-1"))

    async def test_render_text_only_message_content_includes_image_note(self):
        from core.bus import MessageBus
        from core.loop import AgentLoop

        class _TestAgentLoop(AgentLoop):
            async def _init_skills_and_tools(self) -> None:
                self._tool_definitions = []
                self._warmed = True

        agent = _TestAgentLoop(bus=MessageBus())

        content = agent._render_text_only_message_content(
            [
                {"type": "text", "text": "check this"},
                {
                    "type": "image_url",
                    "image_url": {"url": "https://cdn.example.com/cat.png"},
                },
            ]
        )

        self.assertIn("check this", content)
        self.assertIn("does not support vision", content)
        self.assertIn("https://cdn.example.com/cat.png", content)
