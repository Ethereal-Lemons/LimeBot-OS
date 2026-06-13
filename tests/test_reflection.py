import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

from core.llm_client import ChatRequest, ProviderConfig
from core.reflection import ReflectiveService


class TestReflection(unittest.TestCase):
    def test_reflection_routes_through_llm_client(self):
        service = ReflectiveService(bus=None, model="openai-codex/gpt-5.4")
        fake_response = type(
            "Response",
            (),
            {
                "choices": [
                    type(
                        "Choice",
                        (),
                        {
                            "message": type(
                                "Message",
                                (),
                                {"content": "<save_memory>updated</save_memory>"},
                            )()
                        },
                    )()
                ]
            },
        )()
        provider = ProviderConfig(
            source_model="openai-codex/gpt-5.4",
            model="gpt-5.4",
            base_url="https://chatgpt.com/backend-api/codex",
            api_key="codex-secret",
            custom_llm_provider="openai",
            is_codex=True,
        )
        service.llm_client = MagicMock()
        service.llm_client.resolve_provider.return_value = provider
        service.llm_client.complete = AsyncMock(return_value=fake_response)

        with tempfile.TemporaryDirectory() as tmpdir:
            memory_dir = Path(tmpdir) / "memory"
            memory_dir.mkdir(parents=True, exist_ok=True)
            (memory_dir / "2026-04-22.md").write_text("- hello", encoding="utf-8")
            long_term = Path(tmpdir) / "MEMORY.md"
            long_term.write_text("# Long Term Memory\n", encoding="utf-8")
            cfg = SimpleNamespace(llm=SimpleNamespace(base_url=""))

            with patch("core.reflection.datetime") as dt, patch(
                "core.reflection.MEMORY_DIR", memory_dir
            ), patch(
                "core.reflection.LONG_TERM_MEMORY_FILE", long_term
            ), patch(
                "core.reflection._load_config", return_value=cfg
            ):
                dt.now.return_value = __import__("datetime").datetime(2026, 4, 22, 12, 0, 0)
                dt.strftime = __import__("datetime").datetime.strftime
                result = __import__("asyncio").run(service.run_reflection_cycle())

        self.assertEqual(result, "<save_memory>updated</save_memory>")
        service.llm_client.resolve_provider.assert_called_once_with(
            "openai-codex/gpt-5.4", default_base_url=""
        )
        service.llm_client.complete.assert_awaited_once()
        request = service.llm_client.complete.await_args.args[1]
        self.assertIsInstance(request, ChatRequest)
        self.assertEqual(request.session_id, "reflection-cycle")
        self.assertIn("CURRENT MEMORY.md:", request.messages[1]["content"])
        self.assertIn("TODAY'S LOGS:\n- hello", request.messages[1]["content"])


if __name__ == "__main__":
    unittest.main()
