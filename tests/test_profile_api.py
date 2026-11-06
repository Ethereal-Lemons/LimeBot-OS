from pathlib import Path
from types import SimpleNamespace
import tempfile
import unittest
from unittest.mock import patch

from fastapi.testclient import TestClient

from channels.web import WebChannel
from core.bus import MessageBus
from core.paths import PersonaPaths
from core.profiles import ProfileRegistry


class TestProfileApi(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        root = Path(self.temporary.name)
        self.registry = ProfileRegistry(
            root / "data" / "profiles.json",
            configured_model="provider/default-model",
        )
        config = SimpleNamespace(
            whitelist=SimpleNamespace(api_key="test-api-key", allowed_paths=[]),
            web=SimpleNamespace(port=8000, allowed_origins=[]),
            llm=SimpleNamespace(model="provider/default-model"),
        )
        self.channel = WebChannel(config=config, bus=MessageBus())
        self.channel.set_agent(SimpleNamespace(profile_registry=self.registry))
        self.client = TestClient(self.channel.app)

    def tearDown(self):
        self.client.close()
        self.temporary.cleanup()

    @property
    def auth_headers(self):
        return {"X-API-Key": "test-api-key"}

    def test_profile_routes_require_existing_api_auth(self):
        response = self.client.get("/api/profiles")

        self.assertEqual(response.status_code, 401)

    def test_create_update_archive_and_restore_profiles(self):
        created = self.client.post(
            "/api/profiles",
            headers=self.auth_headers,
            json={
                "display_name": "Research Desk",
                "model": "provider/research-model",
                "policy": {"allowed_tools": ["read_file"], "approval_policy": "manual"},
            },
        )

        self.assertEqual(created.status_code, 201)
        profile = created.json()["profile"]
        self.assertEqual(profile["profile_id"], "research-desk")
        self.assertEqual(profile["resolved_model"], "provider/research-model")
        self.assertEqual(profile["policy"]["allowed_tools"], ["read_file"])

        updated = self.client.patch(
            "/api/profiles/research-desk",
            headers=self.auth_headers,
            json={"display_name": "Research", "model": None},
        )
        self.assertEqual(updated.status_code, 200)
        self.assertEqual(updated.json()["profile"]["resolved_model"], "provider/default-model")

        archived = self.client.post(
            "/api/profiles/research-desk/archive", headers=self.auth_headers
        )
        self.assertTrue(archived.json()["profile"]["archived"])
        self.assertEqual(
            self.client.get(
                "/api/profiles/research-desk/persona", headers=self.auth_headers
            ).status_code,
            404,
        )
        listed = self.client.get(
            "/api/profiles?include_archived=true", headers=self.auth_headers
        )
        self.assertTrue(
            next(p for p in listed.json()["profiles"] if p["profile_id"] == "research-desk")[
                "archived"
            ]
        )

        restored = self.client.post(
            "/api/profiles/research-desk/unarchive", headers=self.auth_headers
        )
        self.assertFalse(restored.json()["profile"]["archived"])

    def test_default_profile_cannot_be_archived(self):
        response = self.client.delete(
            "/api/profiles/default", headers=self.auth_headers
        )

        self.assertEqual(response.status_code, 422)
        self.assertFalse(self.registry.get_profile("default").archived)

    def test_persona_write_uses_profile_validator_and_isolated_paths(self):
        profile = self.registry.create_profile("Writer")
        paths = PersonaPaths(Path(self.temporary.name) / "persona" / profile.profile_id)

        with patch("core.profile_api.get_persona_paths", return_value=paths):
            saved = self.client.put(
                f"/api/profiles/{profile.profile_id}/persona/soul",
                headers=self.auth_headers,
                json={
                    "content": "# Writer\n\nI value curiosity, kindness, clarity, and steady learning. "
                    "I speak with warmth and keep my answers grounded in what I know. "
                    "I ask thoughtful questions and respect the people I help."
                },
            )
            loaded = self.client.get(
                f"/api/profiles/{profile.profile_id}/persona",
                headers=self.auth_headers,
            )
            rejected = self.client.put(
                f"/api/profiles/{profile.profile_id}/persona/soul",
                headers=self.auth_headers,
                json={"content": "too short"},
            )

        self.assertEqual(saved.status_code, 200)
        self.assertEqual(loaded.status_code, 200)
        self.assertIn("curiosity", loaded.json()["soul"])
        self.assertEqual(rejected.status_code, 422)
        self.assertIn("curiosity", paths.soul.read_text(encoding="utf-8"))


if __name__ == "__main__":
    unittest.main()
