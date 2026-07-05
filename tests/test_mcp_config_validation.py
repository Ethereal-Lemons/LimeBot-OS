import os
import unittest
from unittest.mock import patch


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

    def test_build_server_env_preserves_dotenv_over_empty_config(self):
        from core.mcp_client import build_server_env

        with patch.dict(
            os.environ,
            {"GENKO_MCP_API_KEY": "gk_mcp_test", "GENKO_MCP_AUTH_HEADER": "Bearer gk_mcp_test"},
            clear=False,
        ):
            env = build_server_env(
                {
                    "GENKO_MCP_API_KEY": "",
                    "GENKO_MCP_AUTH_HEADER": "Bearer ${GENKO_MCP_API_KEY}",
                    "UCP_PLATFORM_URL": "https://genko-platform-production.up.railway.app",
                }
            )

        self.assertEqual(env["GENKO_MCP_API_KEY"], "gk_mcp_test")
        self.assertEqual(env["GENKO_MCP_AUTH_HEADER"], "Bearer gk_mcp_test")
        self.assertEqual(env["UCP_PLATFORM_URL"], "https://genko-platform-production.up.railway.app")

    def test_expand_server_args(self):
        from core.mcp_client import expand_server_args

        env = {"GENKO_MCP_AUTH_HEADER": "Bearer gk_mcp_test"}
        args = expand_server_args(["--header", "Authorization:${GENKO_MCP_AUTH_HEADER}"], env)
        self.assertEqual(args[1], "Authorization:Bearer gk_mcp_test")
