import os
import unittest
from unittest.mock import patch


class TestWebConfig(unittest.TestCase):
    def tearDown(self):
        import config as config_module

        config_module._cached_config = None

    def _load_config_with_env(self, env_updates):
        import config as config_module

        config_module._cached_config = None
        with patch.dict(os.environ, env_updates, clear=False):
            return config_module.load_config(force_reload=True)

    def test_web_config_exists_with_port(self):
        try:
            import loguru  # noqa: F401
        except Exception:
            raise unittest.SkipTest("Missing dependencies (loguru).")

        cfg = self._load_config_with_env({"WEB_PORT": "8001", "PORT": ""})
        self.assertTrue(hasattr(cfg, "web"))
        self.assertTrue(hasattr(cfg.web, "port"))
        self.assertIsInstance(cfg.web.port, int)
        self.assertEqual(cfg.web.port, 8001)

    def test_web_allowed_origins_defaults_to_local_frontend(self):
        cfg = self._load_config_with_env(
            {
                "WEB_ALLOWED_ORIGINS": "",
                "FRONTEND_PORT": "",
                "VITE_DEV_SERVER_PORT": "",
            }
        )
        self.assertEqual(
            cfg.web.allowed_origins,
            ["http://localhost:5173", "http://127.0.0.1:5173"],
        )

    def test_web_allowed_origins_honors_explicit_env_values(self):
        cfg = self._load_config_with_env(
            {
                "WEB_ALLOWED_ORIGINS": "http://localhost:3001, http://127.0.0.1:3001",
            }
        )
        self.assertEqual(
            cfg.web.allowed_origins,
            ["http://localhost:3001", "http://127.0.0.1:3001"],
        )

    def test_web_allowed_origins_replaces_wildcard_with_local_defaults(self):
        cfg = self._load_config_with_env(
            {
                "WEB_ALLOWED_ORIGINS": "*",
                "VITE_DEV_SERVER_PORT": "5179",
            }
        )
        self.assertEqual(
            cfg.web.allowed_origins,
            ["http://localhost:5179", "http://127.0.0.1:5179"],
        )


if __name__ == "__main__":
    unittest.main()
