import os
import unittest


class TestWebConfig(unittest.TestCase):
    def test_web_config_exists_with_port(self):
        try:
            import loguru  # noqa: F401
        except Exception:
            raise unittest.SkipTest("Missing dependencies (loguru).")

        from config import reload_config

        prev_web_port = os.environ.get("WEB_PORT")
        prev_port = os.environ.get("PORT")
        os.environ["WEB_PORT"] = "8001"
        if "PORT" in os.environ:
            del os.environ["PORT"]

        try:
            cfg = reload_config()
            self.assertTrue(hasattr(cfg, "web"))
            self.assertTrue(hasattr(cfg.web, "port"))
            self.assertIsInstance(cfg.web.port, int)
            self.assertEqual(cfg.web.port, 8001)
        finally:
            if prev_web_port is not None:
                os.environ["WEB_PORT"] = prev_web_port
            else:
                os.environ.pop("WEB_PORT", None)

            if prev_port is not None:
                os.environ["PORT"] = prev_port
            else:
                os.environ.pop("PORT", None)


if __name__ == "__main__":
    unittest.main()