import unittest
from types import SimpleNamespace
from unittest.mock import patch

from core.llm_utils import resolve_provider_config


class TestLlmUtils(unittest.TestCase):
    def test_resolve_provider_config_uses_nvidia_nim_provider_for_kimi_alias(self):
        cfg = SimpleNamespace(llm=SimpleNamespace(proxy_url=""))
        with patch("config.load_config", return_value=cfg), patch.dict(
            "os.environ",
            {"NVIDIA_API_KEY": "nvidia-secret"},
            clear=False,
        ):
            resolved = resolve_provider_config("nvidia/moonshotai/kimi-k2-5")

        self.assertEqual(resolved["model"], "moonshotai/kimi-k2.5")
        self.assertEqual(resolved["base_url"], "https://integrate.api.nvidia.com/v1")
        self.assertEqual(resolved["api_key"], "nvidia-secret")
        self.assertEqual(resolved["custom_llm_provider"], "nvidia_nim")
