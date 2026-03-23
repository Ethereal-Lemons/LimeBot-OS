import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from litellm import AuthenticationError


class TestLlmFallbacks(unittest.IsolatedAsyncioTestCase):
    async def test_llm_call_switches_to_fallback_model_after_auth_failure(self):
        from core.loop import AgentLoop

        loop = AgentLoop.__new__(AgentLoop)
        loop.model = "xai/grok-4-fast-reasoning"
        loop.fallback_models = ["gemini/gemini-2.0-flash"]
        loop.config = SimpleNamespace(llm=SimpleNamespace(base_url=None))
        loop._sanitize_messages_for_llm = lambda messages, session_key: messages
        loop._get_tool_definitions_for_turn = lambda text: []
        loop._tool_definition_names = lambda tools: []
        loop._log_tool_debug = lambda *args, **kwargs: None
        loop._with_trace_metadata = lambda meta, **kwargs: meta
        loop._should_retry_without_images = lambda e, messages: False
        loop._downgrade_image_messages_for_text_model = lambda messages, session_key: False
        loop._disable_image_inputs_for_session = lambda session_key: None
        loop.bus = SimpleNamespace(publish_outbound=AsyncMock())

        response = object()
        auth_error = AuthenticationError(
            message="Incorrect API key provided",
            llm_provider="xai",
            model="grok-4-fast-reasoning",
        )

        with patch.object(
            loop,
            "_resolve_provider_chain",
            return_value=[
                (
                    "xai/grok-4-fast-reasoning",
                    "grok-4-fast-reasoning",
                    "https://api.x.ai/v1",
                    "bad-key",
                    "openai",
                ),
                (
                    "gemini/gemini-2.0-flash",
                    "gemini-2.0-flash",
                    None,
                    "good-key",
                    "gemini",
                ),
            ],
        ), patch("core.loop.acompletion", new=AsyncMock(side_effect=[auth_error, response])) as mocked:
            result = await loop._llm_call_with_retry(
                messages=[{"role": "user", "content": "hi"}],
                session_key="web_demo",
                msg=None,
                max_retries=3,
                stream=False,
                include_tools=False,
            )

        self.assertIs(result, response)
        self.assertEqual(mocked.await_count, 2)
        first_call = mocked.await_args_list[0].kwargs
        second_call = mocked.await_args_list[1].kwargs
        self.assertEqual(first_call["model"], "grok-4-fast-reasoning")
        self.assertEqual(second_call["model"], "gemini-2.0-flash")
