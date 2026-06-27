import asyncio
import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from core.events import OutboundMessage


class FakeAgent:
    def __init__(self):
        self.model = "openai/gpt-4o-mini"
        self.pending_confirmations = {
            "conf_safe": {
                "tool": "run_command",
                "session_key": "web_app",
                "policy_profile": "review",
                "decision_reason": "manual_required",
                "client_source": "app",
                "preview": {
                    "kind": "run_command",
                    "command": "curl https://example.test?token=secret-value",
                    "risk_flags": ["network_access"],
                },
            }
        }
        self.confirm_tool = AsyncMock(return_value=True)

    def get_readiness_status(self):
        return {
            "status": "ready",
            "phase": "ready",
            "ready": True,
            "elapsed_ms": 12,
            "degraded_reasons": [],
            "failure_code": None,
            "internal_secret": "never-return-this",
        }


class CapturingSocket:
    def __init__(self):
        self.messages = []

    async def send_text(self, payload):
        self.messages.append(json.loads(payload))


class TestAppServerApi(unittest.TestCase):
    def _make_channel(self, api_key="app-key"):
        try:
            from channels.web import WebChannel
            from core.bus import MessageBus
        except Exception:
            raise unittest.SkipTest("Missing web test dependencies.")

        config = SimpleNamespace(
            whitelist=SimpleNamespace(api_key=api_key, allowed_paths=[]),
            web=SimpleNamespace(port=8000, allowed_origins=[]),
            llm=SimpleNamespace(
                model="openai/gpt-4o-mini",
                base_url="https://api.example.test/v1",
            ),
            discord=SimpleNamespace(enabled=False),
            whatsapp=SimpleNamespace(enabled=False),
            telegram=SimpleNamespace(enabled=False),
        )
        channel = WebChannel(config=config, bus=MessageBus())
        channel.set_agent(FakeAgent())
        channel.set_channels([channel])
        return channel

    @staticmethod
    def _headers():
        return {"X-API-Key": "app-key"}

    def test_state_is_authenticated_and_omits_paths_metadata_and_secrets(self):
        try:
            from fastapi.testclient import TestClient
        except Exception:
            raise unittest.SkipTest("Missing web test dependencies.")
        from core.task_tracker import TaskTracker

        async def prepare(tracker):
            workspace = await tracker.create_workspace(
                "Remote coding task",
                "app",
                session_key="web_app",
                chat_id="app",
                metadata={"api_key": "secret-value"},
            )
            await tracker.add_workspace_artifact(
                workspace.workspace_id,
                kind="diff",
                title="Patch preview",
                path="C:/private/project/secret.patch",
                metadata={"token": "secret-value"},
            )
            await tracker.create_task(
                "inbound_message",
                "Continue remote task",
                channel="web",
                session_key="web_app",
                metadata={"password": "secret-value"},
            )
            return workspace

        with tempfile.TemporaryDirectory() as tmpdir:
            tracker = TaskTracker(data_dir=tmpdir)
            workspace = asyncio.run(prepare(tracker))
            channel = self._make_channel()
            with patch("core.task_tracker.get_task_tracker", return_value=tracker):
                client = TestClient(channel.app)
                self.assertEqual(client.get("/api/app/state").status_code, 401)
                response = client.get("/api/app/state", headers=self._headers())

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertEqual(payload["workspaces"][0]["workspace_id"], workspace.workspace_id)
        self.assertTrue(payload["workspaces"][0]["artifacts"][0]["available_locally"])
        self.assertEqual(payload["pending_approvals"][0]["preview"]["kind"], "run_command")
        serialized = response.text
        self.assertNotIn("secret-value", serialized)
        self.assertNotIn("C:/private", serialized)
        self.assertNotIn("command", payload["pending_approvals"][0]["preview"])
        self.assertNotIn("internal_secret", payload["runtime"]["readiness"])

    def test_app_server_stays_disabled_until_an_api_key_is_configured(self):
        try:
            from fastapi.testclient import TestClient
        except Exception:
            raise unittest.SkipTest("Missing web test dependencies.")

        response = TestClient(self._make_channel(api_key=None).app).get(
            "/api/app/state"
        )

        self.assertEqual(response.status_code, 503)
        self.assertIn("APP_API_KEY", response.json()["detail"])

    def test_workspace_message_validates_and_enqueues_with_workspace_context(self):
        try:
            from fastapi.testclient import TestClient
        except Exception:
            raise unittest.SkipTest("Missing web test dependencies.")
        from core.task_tracker import TaskStatus, TaskTracker

        with tempfile.TemporaryDirectory() as tmpdir:
            tracker = TaskTracker(data_dir=tmpdir)
            workspace = asyncio.run(tracker.create_workspace("App task", "app"))
            channel = self._make_channel()
            with patch("core.task_tracker.get_task_tracker", return_value=tracker):
                client = TestClient(channel.app)
                empty = client.post(
                    f"/api/app/workspaces/{workspace.workspace_id}/message",
                    headers=self._headers(),
                    json={"content": "   "},
                )
                response = client.post(
                    f"/api/app/workspaces/{workspace.workspace_id}/message",
                    headers=self._headers(),
                    json={"content": "Inspect the failing tests", "client_message_id": "m-1"},
                )
                inbound = channel.bus.inbound.get_nowait()
                running = asyncio.run(tracker.get_workspace(workspace.workspace_id))
                asyncio.run(
                    channel.send(
                        OutboundMessage(
                            channel="web",
                            chat_id=inbound.chat_id,
                            content="Tests are fixed.",
                            metadata={"turn_id": "turn-1", "message_id": "msg-1"},
                        )
                    )
                )
                completed = asyncio.run(tracker.get_workspace(workspace.workspace_id))

        self.assertEqual(empty.status_code, 400)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["status"], "queued")
        self.assertEqual(inbound.metadata["workspace_id"], workspace.workspace_id)
        self.assertEqual(inbound.metadata["source"], "app")
        self.assertEqual(inbound.content, "Inspect the failing tests")
        self.assertEqual(running.status, TaskStatus.RUNNING.value)
        self.assertEqual(completed.status, TaskStatus.COMPLETED.value)
        self.assertEqual(completed.attempts[-1].status, TaskStatus.COMPLETED.value)

    def test_app_workspace_creation_filters_metadata(self):
        try:
            from fastapi.testclient import TestClient
        except Exception:
            raise unittest.SkipTest("Missing web test dependencies.")
        from core.task_tracker import TaskTracker

        with tempfile.TemporaryDirectory() as tmpdir:
            tracker = TaskTracker(data_dir=tmpdir)
            channel = self._make_channel()
            with patch("core.task_tracker.get_task_tracker", return_value=tracker):
                response = TestClient(channel.app).post(
                    "/api/app/workspaces",
                    headers=self._headers(),
                    json={
                        "title": "companion selection",
                        "origin": "companion",
                        "metadata": {
                            "client": "companion",
                            "workspace_name": "limebot",
                            "api_key": "secret-value",
                        },
                    },
                )
                workspace_id = response.json()["workspace"]["workspace_id"]
                stored = asyncio.run(tracker.get_workspace(workspace_id))

        self.assertEqual(response.status_code, 200)
        self.assertEqual(stored.origin, "companion")
        self.assertEqual(stored.metadata["client"], "companion")
        self.assertNotIn("api_key", stored.metadata)
        self.assertNotIn("secret-value", response.text)

    def test_approval_reuses_agent_path_and_legacy_endpoint_stays_compatible(self):
        try:
            from fastapi.testclient import TestClient
        except Exception:
            raise unittest.SkipTest("Missing web test dependencies.")

        channel = self._make_channel()
        client = TestClient(channel.app)
        app_response = client.post(
            "/api/app/approvals/conf_safe",
            headers=self._headers(),
            json={"approved": True, "session_whitelist": False},
        )
        legacy_response = client.post(
            "/api/confirm-tool",
            headers=self._headers(),
            json={"conf_id": "conf_safe", "approved": False},
        )

        self.assertEqual(app_response.status_code, 200)
        self.assertEqual(legacy_response.status_code, 200)
        self.assertEqual(channel.agent.confirm_tool.await_count, 2)
        self.assertEqual(channel.agent.confirm_tool.await_args_list[0].kwargs["source"], "app")
        self.assertEqual(channel.agent.confirm_tool.await_args_list[1].kwargs["source"], "web")

    def test_multiple_workspace_messages_complete_attempts_in_fifo_order(self):
        try:
            from fastapi.testclient import TestClient
        except Exception:
            raise unittest.SkipTest("Missing web test dependencies.")
        from core.task_tracker import TaskStatus, TaskTracker

        with tempfile.TemporaryDirectory() as tmpdir:
            tracker = TaskTracker(data_dir=tmpdir)
            workspace = asyncio.run(tracker.create_workspace("Queued steering", "app"))
            channel = self._make_channel()
            with patch("core.task_tracker.get_task_tracker", return_value=tracker):
                client = TestClient(channel.app)
                first = client.post(
                    f"/api/app/workspaces/{workspace.workspace_id}/message",
                    headers=self._headers(),
                    json={"content": "First instruction"},
                )
                second = client.post(
                    f"/api/app/workspaces/{workspace.workspace_id}/message",
                    headers=self._headers(),
                    json={"content": "Second instruction"},
                )
                first_inbound = channel.bus.inbound.get_nowait()
                channel.bus.inbound.get_nowait()

                asyncio.run(
                    channel.send(
                        OutboundMessage(
                            channel="web",
                            chat_id=first_inbound.chat_id,
                            content="First result",
                        )
                    )
                )
                midway = asyncio.run(tracker.get_workspace(workspace.workspace_id))
                asyncio.run(
                    channel.send(
                        OutboundMessage(
                            channel="web",
                            chat_id=first_inbound.chat_id,
                            content="Second result",
                        )
                    )
                )
                finished = asyncio.run(tracker.get_workspace(workspace.workspace_id))

        self.assertEqual(first.status_code, 200)
        self.assertEqual(second.status_code, 200)
        self.assertEqual(midway.status, TaskStatus.RUNNING.value)
        self.assertEqual(midway.attempts[0].status, TaskStatus.COMPLETED.value)
        self.assertEqual(midway.attempts[1].status, TaskStatus.RUNNING.value)
        self.assertEqual(finished.status, TaskStatus.COMPLETED.value)
        self.assertTrue(
            all(
                attempt.status == TaskStatus.COMPLETED.value
                for attempt in finished.attempts
            )
        )

    def test_events_are_normalized_and_do_not_return_stored_arguments(self):
        try:
            from fastapi.testclient import TestClient
        except Exception:
            raise unittest.SkipTest("Missing web test dependencies.")
        from core.task_tracker import TaskTracker

        with tempfile.TemporaryDirectory() as tmpdir:
            tracker = TaskTracker(data_dir=tmpdir)
            workspace = asyncio.run(
                tracker.create_workspace(
                    "Event task", "app", session_key="web_event-session"
                )
            )
            events_dir = Path(tmpdir) / "events"
            events_dir.mkdir()
            (events_dir / "web_event-session.jsonl").write_text(
                "\n".join(
                    [
                        json.dumps(
                            {
                                "type": "approval_requested",
                                "timestamp": 10,
                                "tool": "run_command",
                                "args": {"command": "echo secret-value"},
                                "preview": {
                                    "kind": "run_command",
                                    "command": "echo secret-value",
                                    "risk_flags": [],
                                },
                            }
                        ),
                        "not-json",
                    ]
                ),
                encoding="utf-8",
            )
            channel = self._make_channel()
            with patch("core.task_tracker.get_task_tracker", return_value=tracker), patch(
                "core.session_manager.EVENTS_DIR", events_dir
            ):
                response = TestClient(channel.app).get(
                    f"/api/app/workspaces/{workspace.workspace_id}/events",
                    headers=self._headers(),
                )

        self.assertEqual(response.status_code, 200)
        event = response.json()["events"][0]
        self.assertEqual(event["type"], "workspace_event")
        self.assertEqual(event["event"], "approval_requested")
        self.assertNotIn("args", event["payload"])
        self.assertNotIn("command", event["payload"]["preview"])
        self.assertNotIn("secret-value", response.text)

    def test_app_websocket_authenticates_and_returns_initial_state(self):
        try:
            from fastapi.testclient import TestClient
        except Exception:
            raise unittest.SkipTest("Missing web test dependencies.")
        from core.task_tracker import TaskTracker

        with tempfile.TemporaryDirectory() as tmpdir:
            tracker = TaskTracker(data_dir=tmpdir)
            workspace = asyncio.run(tracker.create_workspace("Socket task", "app"))
            channel = self._make_channel()
            with patch("core.task_tracker.get_task_tracker", return_value=tracker):
                client = TestClient(channel.app)
                with client.websocket_connect("/ws/app?api_key=app-key") as socket:
                    self.assertEqual(socket.receive_json()["type"], "auth_ok")
                    initial = socket.receive_json()
                    self.assertEqual(initial["type"], "app_state")
                    socket.send_json({"type": "ping"})
                    self.assertEqual(socket.receive_json()["type"], "pong")
                    queued_response = client.post(
                        f"/api/app/workspaces/{workspace.workspace_id}/message",
                        headers=self._headers(),
                        json={"content": "Stream this turn"},
                    )
                    self.assertEqual(queued_response.status_code, 200)
                    queued_event = socket.receive_json()
                    self.assertEqual(queued_event["type"], "workspace_event")
                    self.assertEqual(queued_event["event"], "message_queued")
                    self.assertEqual(
                        queued_event["workspace_id"], workspace.workspace_id
                    )

    def test_outbound_app_stream_uses_stable_envelope_and_omits_tool_arguments(self):
        channel = self._make_channel()
        socket = CapturingSocket()
        channel.app_connections.add(socket)
        channel._app_chat_workspaces["app_chat"] = "workspace-1"
        channel._app_chat_sessions["app_chat"] = "web_app_chat"

        asyncio.run(
            channel.send(
                OutboundMessage(
                    channel="web",
                    chat_id="app_chat",
                    content="",
                    metadata={
                        "type": "tool_execution",
                        "status": "waiting_confirmation",
                        "tool": "run_command",
                        "tool_call_id": "tool-1",
                        "args": {"command": "echo secret-value"},
                        "result": "secret-value",
                        "preview": {
                            "kind": "run_command",
                            "command": "echo secret-value",
                            "risk_flags": [],
                        },
                    },
                )
            )
        )

        event = socket.messages[0]
        self.assertEqual(event["type"], "workspace_event")
        self.assertEqual(event["workspace_id"], "workspace-1")
        self.assertEqual(event["session_key"], "web_app_chat")
        self.assertEqual(event["event"], "tool_execution")
        self.assertEqual(event["payload"]["tool"], "run_command")
        self.assertNotIn("args", event["payload"])
        self.assertNotIn("result", event["payload"])
        self.assertNotIn("command", event["payload"]["preview"])
        self.assertNotIn("secret-value", json.dumps(event))


if __name__ == "__main__":
    unittest.main()
