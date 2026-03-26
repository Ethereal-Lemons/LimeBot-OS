import unittest
from types import SimpleNamespace
from unittest.mock import patch

import core.vectors as vectors_module


class TestVectorProviderResolution(unittest.TestCase):
    def tearDown(self):
        vectors_module._instance = None

    def test_nvidia_chat_model_uses_nvidia_embedding_model(self):
        cfg = SimpleNamespace(
            llm=SimpleNamespace(
                model="nvidia/llama-3.1-nemotron-ultra-253b-v1",
                embedding_model="",
            )
        )

        service = vectors_module.get_vector_service(cfg)

        self.assertEqual(service.model, "nvidia_nim/NV-Embed-v2")

    def test_explicit_embedding_model_override_wins(self):
        cfg = SimpleNamespace(
            llm=SimpleNamespace(
                model="nvidia/llama-3.1-nemotron-ultra-253b-v1",
                embedding_model="custom/embedding-model",
            )
        )

        service = vectors_module.get_vector_service(cfg)

        self.assertEqual(service.model, "custom/embedding-model")

    def test_legacy_nvidia_embedding_model_is_normalized_for_litellm(self):
        cfg = SimpleNamespace(
            llm=SimpleNamespace(
                model="nvidia/llama-3.1-nemotron-ultra-253b-v1",
                embedding_model="nvidia/NV-Embed-v2",
                base_url="",
                api_key="",
            )
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
        self.assertEqual(
            mock_embedding.call_args.kwargs["model"], "nvidia_nim/NV-Embed-v2"
        )
