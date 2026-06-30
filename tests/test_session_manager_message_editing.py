import asyncio
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import core.session_manager as session_manager_module


class TestSessionManagerMessageEditing(unittest.TestCase):
    def test_truncate_history_from_user_turn_removes_target_and_following_rows(self):
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
                history = [
                    {"role": "system", "content": "system"},
                    {"role": "user", "content": "first"},
                    {"role": "assistant", "content": "reply one"},
                    {"role": "user", "content": "second"},
                    {"role": "assistant", "content": "reply two"},
                ]
                asyncio.run(manager.save_history("web_chat", history))

                truncated, found = asyncio.run(
                    manager.truncate_history_from_user_turn("web_chat", 1)
                )

                self.assertTrue(found)
                self.assertEqual(
                    truncated,
                    [
                        {"role": "system", "content": "system"},
                        {"role": "user", "content": "first"},
                        {"role": "assistant", "content": "reply one"},
                    ],
                )
                persisted = asyncio.run(manager.load_history("web_chat"))
                self.assertEqual(persisted, truncated)

    def test_truncate_chat_log_prefers_message_id_and_falls_back_to_user_turn_index(self):
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
                rows = [
                    {"role": "user", "content": "first", "message_id": "usr-1"},
                    {"role": "assistant", "content": "reply one"},
                    {"role": "user", "content": "second"},
                    {"role": "assistant", "content": "reply two"},
                ]
                asyncio.run(manager.save_chat_log("web_chat", rows))

                result = asyncio.run(
                    manager.truncate_chat_log_from_user_message(
                        "web_chat",
                        "missing-id",
                        user_turn_index=1,
                    )
                )

                self.assertTrue(result["found"])
                self.assertFalse(result["matched_by_id"])
                remaining = asyncio.run(manager.load_chat_log("web_chat"))
                self.assertEqual(
                    remaining,
                    [
                        {"role": "user", "content": "first", "message_id": "usr-1"},
                        {"role": "assistant", "content": "reply one"},
                    ],
                )
