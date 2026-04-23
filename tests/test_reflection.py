import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from core.reflection import ReflectiveService


class TestReflection(unittest.TestCase):
    def test_reflection_uses_codex_bridge_for_codex_models(self):
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
                "core.reflection.load_config", return_value=cfg
            ), patch(
                "core.reflection.complete_codex_response", return_value=fake_response
            ) as codex_complete, patch(
                "core.reflection.completion"
            ) as litellm_completion:
                dt.now.return_value = __import__("datetime").datetime(2026, 4, 22, 12, 0, 0)
                dt.strftime = __import__("datetime").datetime.strftime
                result = __import__("asyncio").run(service.run_reflection_cycle())

        self.assertEqual(result, "<save_memory>updated</save_memory>")
        codex_complete.assert_called_once()
        litellm_completion.assert_not_called()


if __name__ == "__main__":
    unittest.main()
