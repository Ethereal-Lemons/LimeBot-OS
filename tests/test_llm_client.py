import unittest
from unittest.mock import AsyncMock, patch

from core.llm_client import ChatRequest, LimeLLMClient, ProviderConfig


class TestLlmClient(unittest.IsolatedAsyncioTestCase):
    def test_resolve_provider_uses_existing_openrouter_resolution(self):
        client = LimeLLMClient()

        with patch(
            "core.llm_client.resolve_provider_config",
            return_value={
                "model": "anthropic/claude-sonnet-4.6",
                "base_url": "https://openrouter.ai/api/v1",
                "api_key": "openrouter-secret",
                "custom_llm_provider": "openai",
            },
        ):
            provider = client.resolve_provider(
                "openrouter/anthropic/claude-sonnet-4.6"
            )

        self.assertEqual(provider.source_model, "openrouter/anthropic/claude-sonnet-4.6")
        self.assertEqual(provider.model, "anthropic/claude-sonnet-4.6")
        self.assertEqual(provider.base_url, "https://openrouter.ai/api/v1")
        self.assertEqual(provider.api_key, "openrouter-secret")
        self.assertEqual(provider.custom_llm_provider, "openai")
        self.assertFalse(provider.is_codex)

    def test_resolve_provider_marks_codex_models(self):
        client = LimeLLMClient()

        with patch(
            "core.llm_client.resolve_provider_config",
            return_value={
                "model": "gpt-5.4",
                "base_url": "https://chatgpt.com/backend-api/codex",
                "api_key": "codex-secret",
                "custom_llm_provider": "openai",
            },
        ):
            provider = client.resolve_provider("openai-codex/gpt-5.4")

        self.assertEqual(provider.source_model, "openai-codex/gpt-5.4")
        self.assertEqual(provider.model, "gpt-5.4")
        self.assertEqual(provider.base_url, "https://chatgpt.com/backend-api/codex")
        self.assertEqual(provider.api_key, "codex-secret")
        self.assertEqual(provider.custom_llm_provider, "openai")
        self.assertTrue(provider.is_codex)

    async def test_complete_uses_codex_stream_bridge_for_streaming_requests(self):
        client = LimeLLMClient()
        provider = ProviderConfig(
            source_model="openai-codex/gpt-5.4",
            model="gpt-5.4",
            base_url="https://chatgpt.com/backend-api/codex",
            api_key="codex-secret",
            custom_llm_provider="openai",
            is_codex=True,
        )
        request = ChatRequest(
            messages=[{"role": "user", "content": "hi"}],
            stream=True,
            session_id="session-123",
        )
        sentinel = object()

        async def passthrough(func, *args):
            return func(*args)

        with patch("core.llm_client.asyncio.to_thread", side_effect=passthrough), patch(
            "core.llm_client.stream_codex_response",
            return_value=sentinel,
        ) as stream_mock:
            result = await client.complete(provider, request)

        self.assertIs(result, sentinel)
        stream_mock.assert_called_once_with(
            "openai-codex/gpt-5.4",
            [{"role": "user", "content": "hi"}],
            None,
            "session-123",
        )

    async def test_complete_uses_litellm_with_tools_when_present(self):
        client = LimeLLMClient()
        provider = ProviderConfig(
            source_model="openai/gpt-4o",
            model="gpt-4o",
            base_url="https://api.openai.com/v1",
            api_key="openai-secret",
            custom_llm_provider=None,
            is_codex=False,
        )
        request = ChatRequest(
            messages=[{"role": "user", "content": "hi"}],
            tools=[
                {
                    "type": "function",
                    "function": {
                        "name": "list_dir",
                        "parameters": {"type": "object", "properties": {}},
                    },
                }
            ],
            max_tokens=32,
        )
        response = object()

        with patch("core.llm_client.acompletion", new=AsyncMock(return_value=response)) as mock_completion:
            result = await client.complete(provider, request)

        self.assertIs(result, response)
        kwargs = mock_completion.await_args.kwargs
        self.assertEqual(kwargs["model"], "gpt-4o")
        self.assertEqual(kwargs["messages"], [{"role": "user", "content": "hi"}])
        self.assertEqual(kwargs["tools"], request.tools)
        self.assertEqual(kwargs["tool_choice"], "auto")
        self.assertEqual(kwargs["max_tokens"], 32)
        self.assertFalse(kwargs["stream"])
        self.assertNotIn("stream_options", kwargs)

    async def test_complete_omits_empty_optional_fields(self):
        client = LimeLLMClient()
        provider = ProviderConfig(
            source_model="gemini/gemini-2.0-flash",
            model="gemini-2.0-flash",
            base_url=None,
            api_key="gemini-secret",
            custom_llm_provider="gemini",
            is_codex=False,
        )
        request = ChatRequest(messages=[{"role": "user", "content": "hello"}])
        response = object()

        with patch("core.llm_client.acompletion", new=AsyncMock(return_value=response)) as mock_completion:
            result = await client.complete(provider, request)

        self.assertIs(result, response)
        kwargs = mock_completion.await_args.kwargs
        self.assertNotIn("tools", kwargs)
        self.assertNotIn("tool_choice", kwargs)
        self.assertNotIn("max_tokens", kwargs)
        self.assertNotIn("stream_options", kwargs)

    async def test_complete_resolves_local_image_paths(self):
        import base64
        from pathlib import Path

        client = LimeLLMClient()
        provider = ProviderConfig(
            source_model="openai/gpt-4o",
            model="gpt-4o",
            base_url="https://api.openai.com/v1",
            api_key="openai-secret",
            custom_llm_provider=None,
            is_codex=False,
        )

        # Create a temp file in Path.cwd() / "temp" / "web_uploads" / "test_session"
        temp_dir = Path.cwd() / "temp" / "web_uploads" / "test_session"
        temp_dir.mkdir(parents=True, exist_ok=True)
        img_file = temp_dir / "test_image.png"
        img_content = b"fake image bytes"
        img_file.write_bytes(img_content)

        relative_url = f"/temp/web_uploads/test_session/test_image.png"

        request = ChatRequest(
            messages=[
                {
                    "role": "user",
                    "content": [
                        {"type": "text", "text": "here is an image:"},
                        {"type": "image_url", "image_url": {"url": relative_url}},
                    ],
                }
            ]
        )

        response = object()
        try:
            with patch("core.llm_client.acompletion", new=AsyncMock(return_value=response)) as mock_completion:
                result = await client.complete(provider, request)

            self.assertIs(result, response)
            kwargs = mock_completion.await_args.kwargs
            processed_messages = kwargs["messages"]
            self.assertEqual(len(processed_messages), 1)
            content = processed_messages[0]["content"]
            self.assertEqual(content[0]["type"], "text")
            self.assertEqual(content[1]["type"], "image_url")
            
            expected_base64 = base64.b64encode(img_content).decode("utf-8")
            self.assertEqual(
                content[1]["image_url"]["url"],
                f"data:image/png;base64,{expected_base64}"
            )
        finally:
            if img_file.exists():
                img_file.unlink()


if __name__ == "__main__":
    unittest.main()
