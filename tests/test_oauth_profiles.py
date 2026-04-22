import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from core import oauth_profiles


class TestOAuthProfiles(unittest.TestCase):
    def test_codex_status_is_unconfigured_when_store_missing(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            store_path = Path(tmpdir) / "oauth_profiles.json"
            with patch.object(oauth_profiles, "OAUTH_PROFILES_PATH", store_path):
                status = oauth_profiles.get_codex_oauth_status()

        self.assertFalse(status["configured"])
        self.assertEqual(status["provider"], "openai-codex")
        self.assertIsNone(status["email"])
        self.assertIsNone(status["expiresAt"])

    def test_codex_status_sanitizes_profile_without_tokens(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            store_path = Path(tmpdir) / "oauth_profiles.json"
            store_path.write_text(
                json.dumps(
                    {
                        "version": 1,
                        "providers": {
                            "openai-codex": {
                                "credential": {
                                    "type": "oauth",
                                    "provider": "openai-codex",
                                    "access": "secret-access-token",
                                    "refresh": "secret-refresh-token",
                                    "email": "jane@example.com",
                                    "displayName": "Jane",
                                    "accountId": "acct_123",
                                    "expires": 2208988800,
                                },
                                "source": "cli-login",
                                "updatedAt": "2026-04-22T04:00:00Z",
                            }
                        },
                    }
                ),
                encoding="utf-8",
            )

            with patch.object(oauth_profiles, "OAUTH_PROFILES_PATH", store_path):
                status = oauth_profiles.get_codex_oauth_status()

        self.assertTrue(status["configured"])
        self.assertEqual(status["email"], "jane@example.com")
        self.assertEqual(status["displayName"], "Jane")
        self.assertEqual(status["accountId"], "acct_123")
        self.assertEqual(status["source"], "cli-login")
        self.assertEqual(status["updatedAt"], "2026-04-22T04:00:00Z")
        self.assertTrue(status["expiresAt"].startswith("2040-"))
        self.assertNotIn("access", status)
        self.assertNotIn("refresh", status)

    def test_resolve_codex_oauth_api_key_uses_helper_json_payload(self):
        completed = type(
            "CompletedProcess",
            (),
            {
                "returncode": 0,
                "stdout": json.dumps({"apiKey": "oauth-token"}),
                "stderr": "",
            },
        )()
        oauth_profiles._CODEX_API_KEY_CACHE["value"] = None
        oauth_profiles._CODEX_API_KEY_CACHE["expires_at"] = 0.0
        with patch.object(oauth_profiles, "_node_executable", return_value="node"), patch(
            "subprocess.run",
            return_value=completed,
        ):
            api_key = oauth_profiles.resolve_codex_oauth_api_key()

        self.assertEqual(api_key, "oauth-token")

    def test_codex_status_normalizes_millisecond_expiry(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            store_path = Path(tmpdir) / "oauth_profiles.json"
            store_path.write_text(
                json.dumps(
                    {
                        "version": 1,
                        "providers": {
                            "openai-codex": {
                                "credential": {
                                    "type": "oauth",
                                    "provider": "openai-codex",
                                    "access": "secret-access-token",
                                    "refresh": "secret-refresh-token",
                                    "expires": 1777704939419,
                                }
                            }
                        },
                    }
                ),
                encoding="utf-8",
            )

            with patch.object(oauth_profiles, "OAUTH_PROFILES_PATH", store_path):
                status = oauth_profiles.get_codex_oauth_status()

        self.assertEqual(status["expiresAt"], "2026-05-02T06:55:39Z")


if __name__ == "__main__":
    unittest.main()
