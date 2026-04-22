import unittest
from types import SimpleNamespace
from unittest.mock import patch

from core.llm_utils import build_provider_chain, get_api_key_for_model, resolve_provider_config


class TestLlmUtils(unittest.TestCase):
    def test_codex_models_do_not_fall_back_to_unrelated_env_keys(self):
        with patch("core.llm_utils.resolve_codex_oauth_api_key", return_value="codex-secret"), patch.dict(
            "os.environ",
            {
                "GEMINI_API_KEY": "gemini-secret",
                "OPENAI_API_KEY": "openai-secret",
            },
            clear=False,
        ):
            api_key = get_api_key_for_model("openai-codex/gpt-5.4")

        self.assertEqual(api_key, "codex-secret")

    def test_resolve_provider_config_uses_codex_base_url_and_model_id(self):
        cfg = SimpleNamespace(llm=SimpleNamespace(proxy_url=""))
        with patch("config.load_config", return_value=cfg), patch(
            "core.llm_utils.resolve_codex_oauth_api_key",
            return_value="codex-secret",
        ):
            resolved = resolve_provider_config("openai-codex/gpt-5.4")

        self.assertEqual(resolved["model"], "gpt-5.4")
        self.assertEqual(resolved["base_url"], "https://chatgpt.com/backend-api/codex")
        self.assertEqual(resolved["api_key"], "codex-secret")
        self.assertEqual(resolved["custom_llm_provider"], "openai")

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

    def test_resolve_provider_config_normalizes_legacy_nvidia_gpt_oss_ids(self):
        cfg = SimpleNamespace(llm=SimpleNamespace(proxy_url=""))
        with patch("config.load_config", return_value=cfg), patch.dict(
            "os.environ",
            {"NVIDIA_API_KEY": "nvidia-secret"},
            clear=False,
        ):
            resolved = resolve_provider_config("nvidia/gpt-oss/120b")

        self.assertEqual(resolved["model"], "openai/gpt-oss-120b")
        self.assertEqual(resolved["base_url"], "https://integrate.api.nvidia.com/v1")
        self.assertEqual(resolved["api_key"], "nvidia-secret")
        self.assertEqual(resolved["custom_llm_provider"], "nvidia_nim")

    def test_build_provider_chain_keeps_order_and_dedupes(self):
        cfg = SimpleNamespace(llm=SimpleNamespace(proxy_url=""))
        with patch("config.load_config", return_value=cfg), patch.dict(
            "os.environ",
            {
                "OPENAI_API_KEY": "openai-secret",
                "GEMINI_API_KEY": "gemini-secret",
            },
            clear=False,
        ):
            chain = build_provider_chain(
                "openai/gpt-4o-mini",
                [
                    "openai/gpt-4o-mini",
                    "gemini/gemini-2.0-flash",
                    "gemini/gemini-2.0-flash",
                ],
            )

        self.assertEqual(
            [item[0] for item in chain],
            ["openai/gpt-4o-mini", "gemini/gemini-2.0-flash"],
        )
