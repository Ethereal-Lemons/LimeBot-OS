import asyncio
import unittest
from types import SimpleNamespace

from core.tool_approval_flow import (
    ToolApprovalFlowService,
    ToolApprovalRequest,
)


class _FakeConfirmation:
    def __init__(self, *, wait_result=True, wait_error=None):
        self.pending = {}
        self.wait_result = wait_result
        self.wait_error = wait_error
        self.waited_for = []

    def build_preview(self, function_name, function_args, _session_key):
        return {"kind": function_name, "path": function_args.get("path", "")}

    def register_pending(self, conf_id, details):
        self.pending[conf_id] = details
        return details

    def build_embed(self, function_name, _function_args, _session_key, preview=None):
        return [{"name": "Tool", "value": function_name}, {"name": "Path", "value": preview["path"]}]

    async def wait_for_resolution(self, conf_id, timeout=300):
        self.waited_for.append((conf_id, timeout))
        if self.wait_error is not None:
            raise self.wait_error
        return self.wait_result


class TestToolApprovalFlow(unittest.IsolatedAsyncioTestCase):
    def make_service(self, decision, *, confirmation=None, profile_context=None):
        events = []
        published = []
        web_mirrors = []
        confirmation = confirmation or _FakeConfirmation()

        async def publish(message, content, metadata):
            published.append((message, content, metadata))

        async def publish_web_mirror(message, metadata):
            web_mirrors.append((message, metadata))

        service = ToolApprovalFlowService(
            confirmation=confirmation,
            decide=lambda *_args, **_kwargs: dict(decision),
            get_profile_context=lambda: profile_context,
            get_profile_id=lambda: "writer",
            session_whitelist_key=lambda name, _args: f"{name}::key",
            log_session_event=lambda session, event: events.append((session, event)),
            approval_audit_preview=lambda preview: {"kind": preview["kind"]},
            display_tool_args=lambda _name, args, _preview: {"path": args["path"]},
            with_trace_metadata=lambda metadata, **trace: {**metadata, **trace},
            publish=publish,
            publish_web_mirror=publish_web_mirror,
        )
        return service, confirmation, events, published, web_mirrors

    @staticmethod
    def request(channel="web"):
        return ToolApprovalRequest(
            session_key="web_chat",
            tool_call_id="call_1",
            function_name="write_file",
            function_args={"path": "notes.txt", "content": "private body"},
            message=SimpleNamespace(channel=channel, chat_id="chat_1"),
            turn_id="turn_1",
            message_id="message_1",
        )

    async def test_policy_approval_is_audited_without_confirmation(self):
        service, confirmation, events, published, web_mirrors = self.make_service(
            {
                "allowed": True,
                "requires_confirmation": False,
                "reason": "session_whitelist",
                "policy_profile": "session",
            }
        )

        result = await service.authorize(self.request())

        self.assertTrue(result.proceed)
        self.assertEqual(events[0][1]["type"], "approval_decided")
        self.assertTrue(events[0][1]["session_whitelist"])
        self.assertEqual(confirmation.waited_for, [])
        self.assertEqual(published, [])
        self.assertEqual(web_mirrors, [])

    async def test_unattended_denial_returns_actionable_rejection(self):
        service, confirmation, events, published, _web_mirrors = self.make_service(
            {
                "allowed": False,
                "requires_confirmation": False,
                "reason": "tool_not_allowed",
                "policy_profile": "manual",
            }
        )

        result = await service.authorize(self.request())

        self.assertFalse(result.proceed)
        self.assertIn("Unattended policy denied write_file", result.rejection)
        self.assertEqual(confirmation.waited_for, [])
        self.assertEqual(events, [])
        self.assertEqual(published, [])

    async def test_confirmation_publishes_card_and_web_mirror_then_proceeds(self):
        profile_context = SimpleNamespace(
            profile=SimpleNamespace(display_name="Writer")
        )
        service, confirmation, events, published, web_mirrors = self.make_service(
            {
                "allowed": False,
                "requires_confirmation": True,
                "reason": "manual_required",
                "policy_profile": "manual",
            },
            profile_context=profile_context,
        )

        result = await service.authorize(self.request(channel="discord"))

        self.assertTrue(result.proceed)
        self.assertEqual(len(confirmation.pending), 1)
        conf_id, pending = next(iter(confirmation.pending.items()))
        self.assertEqual(pending["session_key"], "web_chat")
        self.assertEqual(pending["whitelist_key"], "write_file::key")
        self.assertEqual(pending["profile_id"], "writer")
        self.assertEqual(pending["profile_name"], "Writer")
        self.assertEqual(confirmation.waited_for, [(conf_id, 300)])
        self.assertEqual(events[0][1]["type"], "approval_requested")
        self.assertEqual(events[0][1]["preview"], {"kind": "write_file"})
        self.assertEqual(len(published), 1)
        self.assertEqual(published[0][1], "⏳")
        self.assertEqual(published[0][2]["status"], "waiting_confirmation")
        self.assertEqual(published[0][2]["tool_call_id"], "call_1")
        self.assertIn("embed", published[0][2])
        self.assertEqual(len(web_mirrors), 1)
        self.assertNotIn("embed", web_mirrors[0][1])

    async def test_user_denial_and_timeout_remain_explicit(self):
        decision = {
            "allowed": False,
            "requires_confirmation": True,
            "reason": "manual_required",
            "policy_profile": "manual",
        }
        denied, _confirmation, _events, _published, _mirrors = self.make_service(
            decision, confirmation=_FakeConfirmation(wait_result=False)
        )
        denial = await denied.authorize(self.request())
        self.assertFalse(denial.proceed)
        self.assertEqual(denial.rejection, "ACTION CANCELLED: User denied.")

        timeout_service, _confirmation, timeout_events, _published, _mirrors = (
            self.make_service(
                decision,
                confirmation=_FakeConfirmation(wait_error=asyncio.TimeoutError()),
            )
        )
        timeout = await timeout_service.authorize(self.request())
        self.assertFalse(timeout.proceed)
        self.assertEqual(timeout.rejection, "ACTION CANCELLED: Timed out.")
        self.assertEqual(
            [event["type"] for _session, event in timeout_events],
            ["approval_requested", "approval_timed_out"],
        )
