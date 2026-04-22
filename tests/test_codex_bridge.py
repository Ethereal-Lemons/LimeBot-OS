import json
import unittest
from unittest.mock import patch

from core.codex_bridge import (
    build_codex_context,
    complete_codex_response,
    is_codex_model_name,
    stream_codex_response,
)


class TestCodexBridge(unittest.TestCase):
    def test_build_codex_context_merges_system_messages_and_tools(self):
        context = build_codex_context(
            [
                {"role": "system", "content": "You are helpful."},
                {"role": "system", "content": "Be concise."},
                {"role": "user", "content": "Hello"},
                {
                    "role": "assistant",
                    "content": "Need a tool.",
                    "tool_calls": [
                        {
                            "id": "call_1",
                            "type": "function",
                            "function": {
                                "name": "list_dir",
                                "arguments": json.dumps({"path": "."}),
                            },
                        }
                    ],
                },
                {
                    "role": "tool",
                    "tool_call_id": "call_1",
                    "name": "list_dir",
                    "content": "[]",
                },
            ],
            [
                {
                    "type": "function",
                    "function": {
                        "name": "list_dir",
                        "description": "List files.",
                        "parameters": {"type": "object", "properties": {}},
                    },
                }
            ],
        )

        self.assertEqual(context["systemPrompt"], "You are helpful.\n\nBe concise.")
        self.assertEqual(context["messages"][0]["role"], "user")
        self.assertEqual(context["messages"][1]["role"], "assistant")
        self.assertEqual(context["messages"][1]["content"][1]["type"], "toolCall")
        self.assertEqual(context["messages"][2]["role"], "toolResult")
        self.assertEqual(context["tools"][0]["name"], "list_dir")

    def test_complete_codex_response_normalizes_tool_calls(self):
        payload = {
            "text": "Done.",
            "thinking": "quiet reasoning",
            "toolCalls": [
                {
                    "id": "call_1",
                    "name": "read_file",
                    "arguments": {"path": "README.md"},
                }
            ],
            "usage": {"input": 12, "output": 8, "totalTokens": 20},
        }
        with patch("core.codex_bridge._run_codex_bridge", return_value=payload):
            response = complete_codex_response(
                "openai-codex/gpt-5.4",
                [{"role": "user", "content": "Hi"}],
                [],
                "session-1",
            )

        message = response.choices[0].message
        self.assertEqual(message.content, "Done.")
        self.assertEqual(message.reasoning_content, "quiet reasoning")
        self.assertEqual(message.tool_calls[0].function.name, "read_file")
        self.assertEqual(
            json.loads(message.tool_calls[0].function.arguments),
            {"path": "README.md"},
        )
        self.assertEqual(response.usage["total_tokens"], 20)

    def test_complete_codex_response_preserves_unicode_text(self):
        payload = {
            "text": "hi ✨ Jisoo here 🥪\n\nWhat’s up?",
            "thinking": "",
            "toolCalls": [],
            "usage": {"input": 4, "output": 6, "totalTokens": 10},
        }
        with patch("core.codex_bridge._run_codex_bridge", return_value=payload):
            response = complete_codex_response(
                "openai-codex/gpt-5.4",
                [{"role": "user", "content": "Hi"}],
                [],
                "session-unicode",
            )

        self.assertEqual(
            response.choices[0].message.content,
            "hi ✨ Jisoo here 🥪\n\nWhat’s up?",
        )

    def test_stream_codex_response_yields_synthetic_chunk(self):
        payload = {
            "text": "Hello",
            "thinking": "",
            "toolCalls": [],
            "usage": {"input": 1, "output": 1, "totalTokens": 2},
        }
        with patch("core.codex_bridge._run_codex_bridge", return_value=payload):
            stream = stream_codex_response(
                "openai-codex/gpt-5.4",
                [{"role": "user", "content": "Hi"}],
                [],
                "session-2",
            )

        async def collect():
            chunks = []
            async for chunk in stream:
                chunks.append(chunk)
            return chunks

        import asyncio

        chunks = asyncio.run(collect())
        self.assertEqual(len(chunks), 1)
        self.assertEqual(chunks[0].choices[0].delta.content, "Hello")
        self.assertEqual(chunks[0].usage["total_tokens"], 2)

    def test_is_codex_model_name_detects_codex_prefix(self):
        self.assertTrue(is_codex_model_name("openai-codex/gpt-5.4"))
        self.assertFalse(is_codex_model_name("openai/gpt-5.4"))


if __name__ == "__main__":
    unittest.main()
