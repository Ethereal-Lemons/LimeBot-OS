import tempfile
import unittest
from pathlib import Path


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


if __name__ == "__main__":
    unittest.main()
