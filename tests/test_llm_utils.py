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

    def test_resolve_provider_config_uses_openrouter_gateway(self):
        cfg = SimpleNamespace(llm=SimpleNamespace(proxy_url=""))
        with patch("config.load_config", return_value=cfg), patch.dict(
            "os.environ",
            {"OPENROUTER_API_KEY": "openrouter-secret"},
            clear=False,
        ):
            resolved = resolve_provider_config("openrouter/anthropic/claude-sonnet-4.6")

        self.assertEqual(resolved["model"], "anthropic/claude-sonnet-4.6")
        self.assertEqual(resolved["base_url"], "https://openrouter.ai/api/v1")
        self.assertEqual(resolved["api_key"], "openrouter-secret")
        self.assertEqual(resolved["custom_llm_provider"], "openai")

    def test_openrouter_gateway_ignores_stale_llm_base_url(self):
        cfg = SimpleNamespace(llm=SimpleNamespace(proxy_url=""))
        with patch("config.load_config", return_value=cfg), patch.dict(
            "os.environ",
            {"OPENROUTER_API_KEY": "openrouter-secret"},
            clear=False,
        ):
            resolved = resolve_provider_config(
                "openrouter/anthropic/claude-sonnet-4.6",
                default_base_url="https://api.openai.com/v1",
            )

        self.assertEqual(resolved["base_url"], "https://openrouter.ai/api/v1")

    def test_bare_curated_openrouter_id_routes_through_openrouter(self):
        cfg = SimpleNamespace(llm=SimpleNamespace(proxy_url=""))
        with patch("config.load_config", return_value=cfg), patch.dict(
            "os.environ",
            {
                "OPENROUTER_API_KEY": "openrouter-secret",
                "GEMINI_API_KEY": "gemini-secret",
                "OPENAI_API_KEY": "openai-secret",
            },
            clear=False,
        ):
            resolved = resolve_provider_config("mistralai/mistral-medium-3.1")

        self.assertEqual(resolved["model"], "mistralai/mistral-medium-3.1")
        self.assertEqual(resolved["base_url"], "https://openrouter.ai/api/v1")
        self.assertEqual(resolved["api_key"], "openrouter-secret")
        self.assertEqual(resolved["custom_llm_provider"], "openai")

    def test_openrouter_respects_explicit_proxy_url(self):
        cfg = SimpleNamespace(llm=SimpleNamespace(proxy_url="http://localhost:8080/v1"))
        with patch("config.load_config", return_value=cfg), patch.dict(
            "os.environ",
            {"OPENROUTER_API_KEY": "openrouter-secret"},
            clear=False,
        ):
            resolved = resolve_provider_config("openrouter/anthropic/claude-sonnet-4.6")

        self.assertEqual(resolved["base_url"], "http://localhost:8080/v1")

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
