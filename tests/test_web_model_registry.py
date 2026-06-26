import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch


class TestWebModelRegistry(unittest.TestCase):
    def test_load_piai_provider_models_uses_registry_entries(self):
        try:
            from channels import web as web_module
        except Exception:
            raise unittest.SkipTest("Missing web channel dependencies.")

        registry = """
export const MODELS = {
  "openai-codex": {
    "gpt-5.4": {
      id: "gpt-5.4",
      name: "GPT-5.4",
      provider: "openai-codex",
    },
    "gpt-5.4-mini": {
      id: "gpt-5.4-mini",
      name: "GPT-5.4 Mini",
      provider: "openai-codex",
    }
  },
  "openai": {}
}
"""

        with tempfile.TemporaryDirectory() as tmpdir:
            registry_path = Path(tmpdir) / "models.generated.js"
            registry_path.write_text(registry, encoding="utf-8")

            original_path = web_module._PIAI_MODELS_JS_PATH
            original_cache = dict(web_module._PIAI_PROVIDER_MODEL_CACHE)
            try:
                web_module._PIAI_MODELS_JS_PATH = registry_path
                web_module._PIAI_PROVIDER_MODEL_CACHE.clear()
                models = web_module._load_piai_provider_models("openai-codex")
            finally:
                web_module._PIAI_MODELS_JS_PATH = original_path
                web_module._PIAI_PROVIDER_MODEL_CACHE.clear()
                web_module._PIAI_PROVIDER_MODEL_CACHE.update(original_cache)

        self.assertEqual(
            models,
            [
                {
                    "id": "openai-codex/gpt-5.4",
                    "name": "GPT-5.4",
                    "provider": "openai-codex",
                },
                {
                    "id": "openai-codex/gpt-5.4-mini",
                    "name": "GPT-5.4 Mini",
                    "provider": "openai-codex",
                },
            ],
        )

    def test_llm_models_returns_codex_fallback_when_auth_configured_without_registry(self):
        try:
            from fastapi.testclient import TestClient
            from channels import web as web_module
            from channels.web import WebChannel
            from core.bus import MessageBus
        except Exception:
            raise unittest.SkipTest("Missing web channel dependencies.")

        config = SimpleNamespace(
            whitelist=SimpleNamespace(api_key=None, allowed_paths=[]),
            web=SimpleNamespace(port=8000, allowed_origins=[]),
            llm=SimpleNamespace(model="openai-codex/gpt-5.4", base_url=None),
        )
        channel = WebChannel(config=config, bus=MessageBus())

        with tempfile.TemporaryDirectory() as tmpdir:
            missing_registry_path = Path(tmpdir) / "missing-models.generated.js"
            original_path = web_module._PIAI_MODELS_JS_PATH
            original_cache = dict(web_module._PIAI_PROVIDER_MODEL_CACHE)
            try:
                web_module._PIAI_MODELS_JS_PATH = missing_registry_path
                web_module._PIAI_PROVIDER_MODEL_CACHE.clear()
                with patch(
                    "channels.web.get_codex_oauth_status",
                    return_value={"configured": True, "provider": "openai-codex"},
                ):
                    response = TestClient(channel.app).get("/api/llm/models")
            finally:
                web_module._PIAI_MODELS_JS_PATH = original_path
                web_module._PIAI_PROVIDER_MODEL_CACHE.clear()
                web_module._PIAI_PROVIDER_MODEL_CACHE.update(original_cache)

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        codex_models = [
            model
            for model in payload["models"]
            if model.get("provider") == "openai-codex"
        ]
        self.assertGreaterEqual(len(codex_models), 1)
        self.assertIn(
            "openai-codex/gpt-5.4",
            {model["id"] for model in codex_models},
        )


if __name__ == "__main__":
    unittest.main()
