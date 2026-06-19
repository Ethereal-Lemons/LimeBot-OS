import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import core.vectors as vectors_module


def _cfg(
    *,
    model: str,
    embedding_model: str = "",
    api_key: str = "",
    base_url: str = "",
    embedding_allow_local_fallback: bool = False,
):
    return SimpleNamespace(
        llm=SimpleNamespace(
            model=model,
            embedding_model=embedding_model,
            api_key=api_key,
            base_url=base_url,
            embedding_allow_local_fallback=embedding_allow_local_fallback,
        )
    )


class TestVectorProviderResolution(unittest.TestCase):
    def setUp(self):
        self._env_patcher = patch.dict("os.environ", {}, clear=True)
        self._env_patcher.start()

    def tearDown(self):
        self._env_patcher.stop()
        vectors_module._instance = None

    def test_nvidia_chat_model_uses_nvidia_embedding_model_when_key_exists(self):
        cfg = _cfg(
            model="nvidia/llama-3.1-nemotron-ultra-253b-v1",
        )

        with patch.dict("os.environ", {"NVIDIA_API_KEY": "test-nvidia-key"}, clear=False):
            service = vectors_module.get_vector_service(cfg)

        self.assertEqual(service.model, "nvidia_nim/NV-Embed-v2")
        self.assertEqual(
            [candidate.model for candidate in service.candidate_models],
            ["nvidia_nim/NV-Embed-v2", "disabled"],
        )

    def test_chat_provider_without_embedding_credentials_falls_back_to_disabled(self):
        cfg = _cfg(model="nvidia/llama-3.1-nemotron-ultra-253b-v1")

        service = vectors_module.get_vector_service(cfg)

        self.assertEqual(service.model, "disabled")
        self.assertFalse(service.is_enabled)

    def test_explicit_embedding_model_override_wins(self):
        cfg = _cfg(
            model="nvidia/llama-3.1-nemotron-ultra-253b-v1",
            embedding_model="custom/embedding-model",
        )

        service = vectors_module.get_vector_service(cfg)

        self.assertEqual(service.model, "custom/embedding-model")
        self.assertEqual(
            [candidate.model for candidate in service.candidate_models[:2]],
            ["custom/embedding-model", "disabled"],
        )

    def test_legacy_nvidia_embedding_model_is_normalized_for_litellm(self):
        cfg = _cfg(
            model="nvidia/llama-3.1-nemotron-ultra-253b-v1",
            embedding_model="nvidia/NV-Embed-v2",
        )
        response = SimpleNamespace(data=[{"embedding": [0.1, 0.2, 0.3]}])

        service = vectors_module.get_vector_service(cfg)
        service._config = cfg

        with patch.dict(
            "os.environ",
            {"NVIDIA_API_KEY": "test-nvidia-key"},
            clear=False,
        ), patch("litellm.embedding", return_value=response) as mock_embedding:
            vector = vectors_module.asyncio.run(service._get_embedding("hello"))

        self.assertEqual(vector, [0.1, 0.2, 0.3])
        self.assertEqual(mock_embedding.call_args.kwargs["model"], "NV-Embed-v2")
        self.assertEqual(
            mock_embedding.call_args.kwargs["base_url"],
            "https://integrate.api.nvidia.com/v1",
        )
        self.assertEqual(
            mock_embedding.call_args.kwargs["custom_llm_provider"], "nvidia_nim"
        )

    def test_openrouter_chat_model_uses_openrouter_embedding_model(self):
        cfg = _cfg(
            model="openrouter/openai/gpt-5.2-pro",
            api_key="test-openrouter-key",
        )

        service = vectors_module.get_vector_service(cfg)

        self.assertEqual(service.model, "openrouter/openai/text-embedding-3-small")
        self.assertEqual(service._get_provider(), "openrouter")

    def test_moonshot_chat_model_uses_moonshot_embedding_model(self):
        cfg = _cfg(
            model="moonshot/kimi-latest",
            api_key="test-moonshot-key",
        )

        service = vectors_module.get_vector_service(cfg)

        self.assertEqual(service.model, "moonshot/moonshot-embed-v1")
        self.assertEqual(service._get_provider(), "moonshot")

    def test_custom_embedding_model_override_wins(self):
        cfg = _cfg(
            model="gemini/gemini-2.0-flash",
            embedding_model="openrouter/openai/text-embedding-3-small",
        )

        service = vectors_module.get_vector_service(cfg)

        self.assertEqual(service.model, "openrouter/openai/text-embedding-3-small")
        self.assertEqual(service._get_provider(), "openrouter")

    def test_codex_chat_defaults_to_disabled_embeddings_without_gemini_key(self):
        cfg = _cfg(model="openai-codex/gpt-5-codex")

        with patch.dict("os.environ", {}, clear=True):
            service = vectors_module.get_vector_service(cfg)

        self.assertEqual(service.model, "disabled")
        self.assertFalse(service.is_enabled)

    def test_codex_chat_prefers_gemini_embeddings_when_key_exists(self):
        cfg = _cfg(model="openai-codex/gpt-5-codex")

        with patch.dict("os.environ", {"GEMINI_API_KEY": "test-gemini-key"}, clear=False):
            service = vectors_module.get_vector_service(cfg)

        self.assertEqual(service.model, "gemini/gemini-embedding-001")
        self.assertEqual(service._get_provider(), "gemini")

    def test_disabled_embedding_override_turns_vectors_off(self):
        cfg = _cfg(
            model="gemini/gemini-2.0-flash",
            embedding_model="disabled",
        )

        service = vectors_module.get_vector_service(cfg)

        self.assertEqual(service.model, "disabled")
        self.assertFalse(service.is_enabled)

    def test_openai_embeddings_do_not_reuse_codex_chat_api_key(self):
        cfg = _cfg(
            model="openai-codex/gpt-5-codex",
            embedding_model="text-embedding-3-small",
            api_key="codex-session-token",
        )
        service = vectors_module.get_vector_service(cfg)

        self.assertIsNone(service._resolve_api_key(cfg))

    def test_rate_limit_disables_vectors_for_session(self):
        cfg = _cfg(
            model="openai/gpt-4o-mini",
            embedding_model="text-embedding-3-small",
        )
        service = vectors_module.get_vector_service(cfg)
        service._config = cfg

        error = Exception(
            "RateLimitError: OpenAIException - Error code: 429 - "
            "{'error': {'message': 'You exceeded your current quota', "
            "'code': 'insufficient_quota'}}"
        )

        with patch.dict(
            "os.environ",
            {"OPENAI_API_KEY": "test-openai-key"},
            clear=False,
        ), patch("litellm.embedding", side_effect=error):
            vector = vectors_module.asyncio.run(service._get_embedding("hello"))

        self.assertIsNone(vector)
        self.assertTrue(service._disabled)

    def test_local_fallback_is_added_only_when_enabled(self):
        disabled_cfg = _cfg(model="openai-codex/gpt-5-codex")
        enabled_cfg = _cfg(
            model="openai-codex/gpt-5-codex",
            embedding_allow_local_fallback=True,
        )

        disabled_service = vectors_module.get_vector_service(disabled_cfg)
        vectors_module._instance = None
        enabled_service = vectors_module.get_vector_service(enabled_cfg)

        self.assertNotIn(
            "ollama/nomic-embed-text",
            [candidate.model for candidate in disabled_service.candidate_models],
        )
        self.assertIn(
            "ollama/nomic-embed-text",
            [candidate.model for candidate in enabled_service.candidate_models],
        )

    def test_first_candidate_failure_falls_through_to_next_candidate(self):
        cfg = _cfg(
            model="openai-codex/gpt-5-codex",
            embedding_model="text-embedding-3-small",
        )
        response = SimpleNamespace(data=[{"embedding": [0.9, 0.8, 0.7]}])
        calls = []

        def fake_embedding(**kwargs):
            calls.append(kwargs["model"])
            if kwargs["model"] == "text-embedding-3-small":
                raise Exception(
                    "RateLimitError: OpenAIException - Error code: 429 - "
                    "{'error': {'message': 'You exceeded your current quota', "
                    "'code': 'insufficient_quota'}}"
                )
            return response

        with patch.dict(
            "os.environ",
            {
                "OPENAI_API_KEY": "test-openai-key",
                "GEMINI_API_KEY": "test-gemini-key",
            },
            clear=False,
        ), patch("litellm.embedding", side_effect=fake_embedding):
            service = vectors_module.get_vector_service(cfg)
            service._config = cfg
            vector = vectors_module.asyncio.run(service._get_embedding("hello"))

        self.assertEqual(vector, [0.9, 0.8, 0.7])
        self.assertEqual(calls, ["text-embedding-3-small", "gemini/gemini-embedding-001"])
        self.assertEqual(service.model, "gemini/gemini-embedding-001")
        self.assertEqual(service._active_candidate_model, "gemini/gemini-embedding-001")
        self.assertIn("text-embedding-3-small", service._failed_candidate_models)

    def test_search_uses_grep_when_semantic_candidates_are_unavailable(self):
        cfg = _cfg(model="openai-codex/gpt-5-codex")
        service = vectors_module.get_vector_service(cfg)
        service.search_grep = AsyncMock(
            return_value=[{"text": "remembered line", "score": 2}]
        )

        result = vectors_module.asyncio.run(service.search("remembered line", limit=3))

        self.assertEqual(result, [{"text": "remembered line", "score": 2}])
        service.search_grep.assert_awaited_once()

    def test_get_embedding_status_reports_candidates_and_fallback(self):
        cfg = _cfg(
            model="openai-codex/gpt-5-codex",
            embedding_allow_local_fallback=True,
        )
        service = vectors_module.get_vector_service(cfg)
        service._config = cfg

        status = service.get_embedding_status()

        self.assertEqual(status["fallback"], "grep")
        self.assertIn("candidate_models", status)
        self.assertIn("ollama/nomic-embed-text", status["candidate_models"])
        self.assertTrue(status["semantic_enabled"])
