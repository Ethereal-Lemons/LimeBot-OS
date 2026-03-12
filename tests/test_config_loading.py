import json
import unittest
from pathlib import Path
from unittest.mock import patch


class TestConfigLoading(unittest.TestCase):
    def setUp(self):
        self.config_path = Path("limebot.json")
        self.original_exists = self.config_path.exists()
        self.original_content = (
            self.config_path.read_text(encoding="utf-8")
            if self.original_exists
            else None
        )

    def tearDown(self):
        if self.original_exists:
            self.config_path.write_text(self.original_content or "", encoding="utf-8")
        elif self.config_path.exists():
            self.config_path.unlink()

        import config as config_module

        config_module._cached_config = None

    def test_empty_json_model_does_not_override_env_model(self):
        self.config_path.write_text(
            json.dumps({"llm": {"model": ""}}, indent=2),
            encoding="utf-8",
        )

        import config as config_module

        config_module._cached_config = None
        with patch.dict("os.environ", {"LLM_MODEL": "nvidia/llama/4-scout"}, clear=False):
            loaded = config_module.load_config(force_reload=True)

        self.assertEqual(loaded.llm.model, "nvidia/llama/4-scout")

    def test_json_model_is_ignored_when_env_model_exists(self):
        self.config_path.write_text(
            json.dumps({"llm": {"model": "gemini/gemini-1.5-flash"}}, indent=2),
            encoding="utf-8",
        )

        import config as config_module

        config_module._cached_config = None
        with patch.dict("os.environ", {"LLM_MODEL": "nvidia/moonshotai/kimi-k2-thinking"}, clear=False):
            loaded = config_module.load_config(force_reload=True)

        self.assertEqual(loaded.llm.model, "nvidia/moonshotai/kimi-k2-thinking")

    def test_json_model_is_ignored_when_env_model_missing(self):
        self.config_path.write_text(
            json.dumps({"llm": {"model": "nvidia/moonshotai/kimi-k2-thinking"}}, indent=2),
            encoding="utf-8",
        )

        import config as config_module

        config_module._cached_config = None
        with patch.dict("os.environ", {"LLM_MODEL": ""}, clear=False):
            loaded = config_module.load_config(force_reload=True)

        self.assertEqual(loaded.llm.model, "gemini/gemini-2.0-flash")
