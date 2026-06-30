import asyncio
import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import core.session_manager as session_manager_module


class FakeEditAgent:
    def __init__(self, history):
        self.history = history
        self.active_tasks = {}
        self.cancel_session = AsyncMock(return_value=True)
        self._mark_dirty = MagicMock()
        self._flush_history = AsyncMock(return_value=None)


class TestWebMessageEditing(unittest.TestCase):
    def _make_channel(self, session_manager):
        try:
            from channels.web import WebChannel
            from core.bus import MessageBus
        except Exception:
            raise unittest.SkipTest("Missing web test dependencies.")

        config = SimpleNamespace(
            whitelist=SimpleNamespace(api_key=None, allowed_paths=[]),
            web=SimpleNamespace(port=8000, allowed_origins=[]),
            llm=SimpleNamespace(model="openai/gpt-4o-mini", base_url=None),
            discord=SimpleNamespace(
                allow_from=[],
                allow_channels=[],
                activity_type="playing",
                activity_text="LimeBot",
                status="online",
                token="",
            ),
            whatsapp=SimpleNamespace(enabled=False, bridge_url="", allow_from=[]),
            telegram=SimpleNamespace(
                enabled=False,
                api_base="https://api.telegram.org",
                allow_from=[],
                allow_chats=[],
                poll_timeout=30,
                token="",
            ),
            browser=SimpleNamespace(
                mode="isolated",
                channel="",
                cdp_url="",
                user_data_dir="",
                profile_directory="",
            ),
        )
        return WebChannel(config=config, bus=MessageBus(), session_manager=session_manager)

    def test_edit_route_truncates_history_and_requeues_message(self):
        try:
            from fastapi.testclient import TestClient
        except Exception:
            raise unittest.SkipTest("Missing web test dependencies.")

        with tempfile.TemporaryDirectory() as tmpdir:
            session_dir = Path(tmpdir) / "sessions"
            logs_dir = session_dir / "logs"
            events_dir = session_dir / "events"
            session_file = session_dir / "sessions.json"
            with patch.multiple(
                session_manager_module,
                SESSION_DIR=session_dir,
                LOGS_DIR=logs_dir,
                EVENTS_DIR=events_dir,
                SESSION_FILE=session_file,
            ):
                manager = session_manager_module.SessionManager()
                session_key = "web_chat-edit"
                history = [
                    {"role": "system", "content": "system"},
                    {"role": "user", "content": "first"},
                    {"role": "assistant", "content": "reply one"},
                    {"role": "user", "content": "second"},
                    {"role": "assistant", "content": "reply two"},
                ]
                asyncio.run(manager.save_history(session_key, history))
                asyncio.run(
                    manager.save_chat_log(
                        session_key,
                        [
                            {"role": "user", "content": "first", "message_id": "usr-1"},
                            {"role": "assistant", "content": "reply one"},
                            {"role": "user", "content": "second", "message_id": "usr-2"},
                            {"role": "assistant", "content": "reply two"},
                        ],
                    )
                )
                channel = self._make_channel(manager)
                agent = FakeEditAgent({session_key: list(history)})
                channel.set_agent(agent)

                response = TestClient(channel.app).post(
                    "/api/chat/chat-edit/messages/usr-2/edit",
                    json={
                        "content": "second, but better",
                        "user_turn_index": 1,
                        "metadata": {"skill_name": "discord", "ponytail_mode": "full"},
                    },
                )

                inbound = channel.bus.inbound.get_nowait()
                persisted_history = asyncio.run(manager.load_history(session_key))
                persisted_log = asyncio.run(manager.load_chat_log(session_key))
                event_file = events_dir / f"{session_key}.jsonl"
                event_rows = [
                    json.loads(line)
                    for line in event_file.read_text(encoding="utf-8").splitlines()
                    if line.strip()
                ]

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertEqual(payload["status"], "queued")
        self.assertTrue(payload["message_id"].startswith("usr_"))
        agent.cancel_session.assert_awaited_once_with(session_key)
        agent._mark_dirty.assert_called_once_with(session_key)
        agent._flush_history.assert_awaited_once()
        self.assertEqual(
            agent.history[session_key],
            [
                {"role": "system", "content": "system"},
                {"role": "user", "content": "first"},
                {"role": "assistant", "content": "reply one"},
            ],
        )
        self.assertEqual(persisted_history, agent.history[session_key])
        self.assertEqual(
            persisted_log,
            [
                {"role": "user", "content": "first", "message_id": "usr-1"},
                {"role": "assistant", "content": "reply one"},
            ],
        )
        self.assertEqual(inbound.chat_id, "chat-edit")
        self.assertEqual(inbound.content, "second, but better")
        self.assertEqual(inbound.metadata["source"], "web")
        self.assertEqual(inbound.metadata["edited_from_message_id"], "usr-2")
        self.assertEqual(inbound.metadata["skill_name"], "discord")
        self.assertEqual(inbound.metadata["ponytail_mode"], "full")
        self.assertEqual(event_rows[-1]["type"], "message_edited")
        self.assertEqual(event_rows[-1]["replaced_message_id"], "usr-2")
