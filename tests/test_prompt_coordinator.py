import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from core.prompt_coordinator import PromptCoordinator


class TestPromptCoordinatorCache(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        root = Path(self.temp_dir.name)
        self.soul_file = root / "SOUL.md"
        self.identity_file = root / "IDENTITY.md"
        self.soul_file.write_text("soul", encoding="utf-8")
        self.identity_file.write_text("identity", encoding="utf-8")
        self.coordinator = PromptCoordinator(
            config=SimpleNamespace(),
            model=lambda: "test-model",
            allowed_paths=lambda: [],
            skill_registry=None,
            subagent_registry=None,
            get_capability_turn_context=lambda _session, message: {
                "routing_text": message
            },
            capability_catalog_prompt=lambda: "",
            soul_file=self.soul_file,
            identity_file=self.identity_file,
        )

    async def asyncTearDown(self):
        self.temp_dir.cleanup()

    async def test_cache_keys_and_sender_scoped_invalidation(self):
        build = lambda **values: (
            f"{values['sender_id']}:{values['channel']}:{values['chat_id']}"
        )
        with patch(
            "core.prompt_coordinator.prompt_module.is_setup_complete",
            return_value=True,
        ), patch(
            "core.prompt_coordinator.prompt_module.build_stable_system_prompt",
            side_effect=build,
        ) as build_prompt:
            first = await self.coordinator.get_stable_prompt(
                "owner", "web", "chat-one"
            )
            same_key = await self.coordinator.get_stable_prompt(
                "owner", "web", "chat-two"
            )
            other_channel = await self.coordinator.get_stable_prompt(
                "owner", "discord", "chat-three"
            )
            other_sender = await self.coordinator.get_stable_prompt(
                "guest", "web", "chat-four"
            )

            self.coordinator.invalidate("owner")
            refreshed = await self.coordinator.get_stable_prompt(
                "owner", "web", "chat-five"
            )
            guest_cached = await self.coordinator.get_stable_prompt(
                "guest", "web", "different-chat"
            )

        self.assertEqual(first, "owner:web:chat-one")
        self.assertEqual(same_key, first)
        self.assertEqual(other_channel, "owner:discord:chat-three")
        self.assertEqual(other_sender, "guest:web:chat-four")
        self.assertEqual(refreshed, "owner:web:chat-five")
        self.assertEqual(guest_cached, other_sender)
        self.assertEqual(build_prompt.call_count, 4)

    async def test_cache_expires_after_configured_ttl(self):
        with patch(
            "core.prompt_coordinator.prompt_module.is_setup_complete",
            return_value=True,
        ), patch(
            "core.prompt_coordinator.prompt_module.build_stable_system_prompt",
            side_effect=["first", "second"],
        ) as build_prompt, patch(
            "core.prompt_coordinator.monotonic",
            side_effect=[100.0, 101.0, 131.0],
        ):
            first = await self.coordinator.get_stable_prompt(
                "owner", "web", "chat"
            )
            cached = await self.coordinator.get_stable_prompt(
                "owner", "web", "other-chat"
            )
            expired = await self.coordinator.get_stable_prompt(
                "owner", "web", "third-chat"
            )

        self.assertEqual((first, cached, expired), ("first", "first", "second"))
        self.assertEqual(build_prompt.call_count, 2)
