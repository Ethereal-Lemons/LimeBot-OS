import unittest


class TestMcpConfigValidation(unittest.TestCase):
    def test_valid_config(self):
        from core.mcp_client import validate_mcp_config

        cfg = {
            "mcpServers": {
                "server1": {
                    "command": "python",
                    "args": ["-m", "server"],
                    "env": {"API_KEY": "x"},
                }
            }
        }

        ok, err = validate_mcp_config(cfg)
        self.assertTrue(ok)
        self.assertEqual(err, "")

    def test_invalid_config(self):
        from core.mcp_client import validate_mcp_config

        bad = {"mcpServers": {"": {"command": "", "args": "nope", "env": []}}}
        ok, err = validate_mcp_config(bad)
        self.assertFalse(ok)
        self.assertTrue(err)
