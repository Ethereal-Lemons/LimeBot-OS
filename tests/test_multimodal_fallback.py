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

    async def test_inline_attachment_is_not_duplicated_by_discord_cdn_url(self):
        from core.loop import AgentLoop

        data_url = "data:image/png;base64,ZmFrZQ=="
        cdn_url = "https://cdn.discordapp.com/attachments/1/image.png"

        image_inputs = AgentLoop._collect_image_inputs(
            [
                {
                    "kind": "image",
                    "data_url": data_url,
                    "url": cdn_url,
                }
            ],
            [cdn_url],
            cdn_url,
        )

        self.assertEqual(image_inputs, [data_url])

    async def test_base64_image_has_bounded_token_estimate(self):
        from core.loop import AgentLoop

        messages = [
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": "What is in this image?"},
                    {
                        "type": "image_url",
                        "image_url": {
                            "url": "data:image/png;base64," + ("A" * 4_000_000)
                        },
                    },
                ],
            }
        ]

        self.assertLess(AgentLoop._estimate_tokens(messages), 2_000)

    async def test_summary_payload_never_contains_base64_image_bytes(self):
        from core.loop import AgentLoop

        payload = AgentLoop._history_summary_payload(
            [
                {
                    "role": "user",
                    "content": [
                        {"type": "text", "text": "Can you see this?"},
                        {
                            "type": "image_url",
                            "image_url": {
                                "url": "data:image/png;base64," + ("A" * 10_000)
                            },
                        },
                    ],
                }
            ]
        )

        self.assertIn("Can you see this?", payload)
        self.assertIn("Image attachment processed", payload)
        self.assertNotIn("base64", payload)

    async def test_text_only_fallback_never_embeds_base64_bytes(self):
        from core.loop import AgentLoop

        content = AgentLoop._render_text_only_message_content(
            [
                {"type": "text", "text": "Can you see this?"},
                {
                    "type": "image_url",
                    "image_url": {
                        "url": "data:image/png;base64," + ("A" * 10_000)
                    },
                },
            ]
        )

        self.assertIn("Can you see this?", content)
        self.assertIn("does not support vision", content)
        self.assertNotIn("base64", content)

    async def test_history_fallback_preserves_latest_multimodal_user_turn(self):
        from core.bus import MessageBus
        from core.loop import AgentLoop

        class _TestAgentLoop(AgentLoop):
            async def _init_skills_and_tools(self) -> None:
                self._tool_definitions = []
                self._warmed = True

        agent = _TestAgentLoop(bus=MessageBus())
        current_user = {
            "role": "user",
            "content": [
                {"type": "text", "text": "Can you see this image?"},
                {
                    "type": "image_url",
                    "image_url": {"url": "data:image/png;base64,ZmFrZQ=="},
                },
            ],
        }
        history = [
            {"role": "user", "content": "old question " * 2_000},
            {"role": "assistant", "content": "old answer " * 2_000},
            current_user,
        ]

        trimmed = agent._truncate_history_fallback(history, target_tokens=1)

        self.assertEqual(trimmed, [current_user])

    async def test_history_flush_does_not_persist_image_bytes(self):
        from unittest.mock import AsyncMock

        from core.bus import MessageBus
        from core.loop import AgentLoop

        class _TestAgentLoop(AgentLoop):
            async def _init_skills_and_tools(self) -> None:
                self._tool_definitions = []
                self._warmed = True

        agent = _TestAgentLoop(bus=MessageBus())
        session_key = "discord_chat-image"
        image_content = [
            {"type": "text", "text": "Inspect this card"},
            {
                "type": "image_url",
                "image_url": {"url": "data:image/png;base64," + ("A" * 10_000)},
            },
        ]
        agent.history[session_key] = [{"role": "user", "content": image_content}]
        agent._mark_dirty(session_key)
        agent.session_manager.save_history = AsyncMock()

        await agent._flush_history(session_key, force=True)

        persisted = agent.session_manager.save_history.await_args.args[1]
        self.assertIn("Image attachment processed", persisted[0]["content"])
        self.assertNotIn("base64", persisted[0]["content"])
        self.assertIs(agent.history[session_key][0]["content"], image_content)

    async def test_recent_chat_image_is_available_for_generate_it_followup(self):
        from pathlib import Path

        from core.bus import MessageBus
        from core.loop import AgentLoop

        class _TestAgentLoop(AgentLoop):
            async def _init_skills_and_tools(self) -> None:
                self._tool_definitions = []
                self._warmed = True

        agent = _TestAgentLoop(bus=MessageBus())
        path = Path("temp/recent-reference.png")
        path.parent.mkdir(exist_ok=True)
        path.write_bytes(b"image")
        try:
            agent._remember_image_attachments(
                "discord_recent",
                [{"kind": "image", "path": path.as_posix(), "name": path.name}],
            )

            remembered = agent._get_recent_image_attachments("discord_recent")
        finally:
            path.unlink(missing_ok=True)

        self.assertEqual(remembered[0]["name"], "recent-reference.png")
        self.assertTrue(agent._message_refers_to_recent_image("ok, genérala"))
