import unittest
from types import SimpleNamespace

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

        self.assertEqual(service.model, "nvidia/NV-Embed-v2")

    def test_explicit_embedding_model_override_wins(self):
        cfg = SimpleNamespace(
            llm=SimpleNamespace(
                model="nvidia/llama-3.1-nemotron-ultra-253b-v1",
                embedding_model="custom/embedding-model",
            )
        )

        service = vectors_module.get_vector_service(cfg)

        self.assertEqual(service.model, "custom/embedding-model")
