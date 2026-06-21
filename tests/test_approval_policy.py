import asyncio
import unittest
from types import SimpleNamespace

from core.loop import AgentLoop


def make_loop(profile="manual"):
    loop = AgentLoop.__new__(AgentLoop)
    loop.config = SimpleNamespace(approval_policy_profile=profile)
    loop.session_whitelists = {}
    loop.pending_confirmations = {}
    return loop


class TestApprovalPolicyDecisions(unittest.TestCase):
    def test_manual_requires_confirmation_by_default(self):
        decision = make_loop()._get_tool_approval_decision("web_one", "run_command")

        self.assertFalse(decision["allowed"])
        self.assertTrue(decision["requires_confirmation"])
        self.assertEqual(decision["reason"], "manual_required")
        self.assertEqual(decision["policy_profile"], "manual")

    def test_session_whitelist_allows_a_previously_approved_tool(self):
        loop = make_loop("session")
        loop.session_whitelists["web_one"] = {"write_file"}

        decision = loop._get_tool_approval_decision("web_one", "write_file")

        self.assertTrue(decision["allowed"])
        self.assertEqual(decision["reason"], "session_whitelist")

    def test_review_ignores_session_whitelist(self):
        loop = make_loop("review")
        loop.session_whitelists["web_one"] = {"write_file"}

        decision = loop._get_tool_approval_decision("web_one", "write_file")

        self.assertFalse(decision["allowed"])
        self.assertEqual(decision["reason"], "manual_required")

    def test_autonomous_and_internal_tools_are_allowed_with_distinct_reasons(self):
        autonomous = make_loop("autonomous")._get_tool_approval_decision(
            "web_one", "delete_file"
        )
        internal = make_loop()._get_tool_approval_decision(
            "system", "run_command", is_internal=True
        )

        self.assertEqual(autonomous["reason"], "policy_autonomous")
        self.assertEqual(internal["reason"], "internal")
        self.assertTrue(autonomous["allowed"])
        self.assertTrue(internal["allowed"])

    def test_whatsapp_legacy_behavior_is_explicit(self):
        allowed = make_loop()._get_tool_approval_decision(
            "whatsapp_one", "run_command", is_whatsapp=True
        )
        delete = make_loop("autonomous")._get_tool_approval_decision(
            "whatsapp_one", "delete_file", is_whatsapp=True
        )

        self.assertEqual(allowed["reason"], "channel_whatsapp_legacy")
        self.assertTrue(allowed["allowed"])
        self.assertTrue(delete["requires_confirmation"])

    def test_audit_preview_never_contains_commands_or_file_content(self):
        safe = AgentLoop._approval_audit_preview(
            {
                "kind": "run_command",
                "command": "curl https://example.test/?token=secret",
                "content_preview": "super-secret-content",
                "risk_flags": ["network_access"],
                "affected_paths": ["one", "two"],
            }
        )

        self.assertEqual(
            safe,
            {
                "kind": "run_command",
                "risk_flags": ["network_access"],
                "affected_path_count": 2,
            },
        )

    def test_sensitive_tool_set_covers_every_state_changing_approval_tool(self):
        from core.confirmation import SENSITIVE_TOOLS

        self.assertTrue(
            {"write_file", "delete_file", "run_command", "cron_remove"}.issubset(
                SENSITIVE_TOOLS
            )
        )


class TestApprovalAuditEvents(unittest.IsolatedAsyncioTestCase):
    async def test_confirm_tool_audits_approval_and_adds_session_whitelist(self):
        loop = make_loop("session")
        events = []
        loop._log_session_event = lambda session_key, event: events.append(
            (session_key, event)
        )
        signal = asyncio.Event()
        loop.pending_confirmations["conf_one"] = {
            "event": signal,
            "approved": False,
            "session_key": "web_one",
            "tool": "write_file",
            "policy_profile": "session",
        }

        result = await loop.confirm_tool(
            "conf_one", True, session_whitelist=True, source="extension"
        )

        self.assertTrue(result)
        self.assertTrue(signal.is_set())
        self.assertIn("write_file", loop.session_whitelists["web_one"])
        self.assertEqual(events[0][1]["type"], "approval_decided")
        self.assertEqual(events[0][1]["client_source"], "extension")
        self.assertTrue(events[0][1]["session_whitelist"])

    async def test_review_approval_does_not_create_a_session_whitelist(self):
        loop = make_loop("review")
        events = []
        loop._log_session_event = lambda session_key, event: events.append(event)
        loop.pending_confirmations["conf_review"] = {
            "event": asyncio.Event(),
            "approved": False,
            "session_key": "web_one",
            "tool": "run_command",
            "policy_profile": "review",
        }

        await loop.confirm_tool(
            "conf_review", True, session_whitelist=True, source="not-a-client"
        )

        self.assertNotIn("web_one", loop.session_whitelists)
        self.assertFalse(events[0]["session_whitelist"])
        self.assertEqual(events[0]["client_source"], "api")

    async def test_denial_is_audited_without_arguments(self):
        loop = make_loop()
        events = []
        loop._log_session_event = lambda session_key, event: events.append(event)
        loop.pending_confirmations["conf_deny"] = {
            "event": asyncio.Event(),
            "approved": False,
            "session_key": "web_one",
            "tool": "delete_file",
            "policy_profile": "manual",
        }

        await loop.confirm_tool("conf_deny", False, source="web")

        self.assertEqual(events[0]["decision_reason"], "user_denied")
        self.assertNotIn("args", events[0])
        self.assertNotIn("preview", events[0])
