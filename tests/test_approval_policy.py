import asyncio
import unittest
from types import SimpleNamespace

from core.confirmation import ConfirmationManager
from core.loop import AgentLoop
from core.profile_context import activate_profile
from core.profiles import AgentProfile, AgentProfilePolicy


def make_loop(profile="manual"):
    loop = AgentLoop.__new__(AgentLoop)
    loop.config = SimpleNamespace(approval_policy_profile=profile)
    loop.session_whitelists = {}
    loop.pending_confirmations = {}
    loop.confirm = ConfirmationManager(
        toolbox=None,
        truncate_fn=lambda text, limit: str(text)[:limit],
        safe_json_load_fn=lambda value: value,
        session_whitelists=loop.session_whitelists,
        pending_confirmations=loop.pending_confirmations,
    )
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

    def test_run_command_whitelist_is_scoped_to_the_binary(self):
        loop = make_loop("session")
        # User approved "always allow" for a `git status` invocation.
        loop.session_whitelists["web_one"] = {"run_command::git"}

        git_decision = loop._get_tool_approval_decision(
            "web_one",
            "run_command",
            function_args={"command": "git log --oneline"},
        )
        self.assertTrue(git_decision["allowed"])
        self.assertEqual(git_decision["reason"], "session_whitelist")

        # A different binary (rm) must NOT be unlocked by the git approval.
        rm_decision = loop._get_tool_approval_decision(
            "web_one",
            "run_command",
            function_args={"command": "rm -rf important"},
        )
        self.assertFalse(rm_decision["allowed"])
        self.assertTrue(rm_decision["requires_confirmation"])
        self.assertEqual(rm_decision["reason"], "manual_required")

    def test_session_whitelist_key_scopes_run_command_by_binary(self):
        self.assertEqual(
            AgentLoop._session_whitelist_key(
                "run_command", {"command": "git status --short"}
            ),
            "run_command::git",
        )
        self.assertEqual(
            AgentLoop._session_whitelist_key("write_file", {"path": "a.txt"}),
            "write_file",
        )

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

    def test_profile_policy_cannot_weaken_global_approval_policy(self):
        loop = make_loop("manual")
        autonomous_profile = AgentProfile(
            "researcher",
            "Researcher",
            policy=AgentProfilePolicy(approval_policy="autonomous"),
        )

        with activate_profile(autonomous_profile):
            decision = loop._get_tool_approval_decision("web_one", "write_file")

        self.assertFalse(decision["allowed"])
        self.assertTrue(decision["requires_confirmation"])
        self.assertEqual(decision["policy_profile"], "manual")

    def test_profile_can_tighten_global_autonomous_approval_policy(self):
        loop = make_loop("autonomous")
        manual_profile = AgentProfile(
            "reviewer",
            "Reviewer",
            policy=AgentProfilePolicy(approval_policy="manual"),
        )

        with activate_profile(manual_profile):
            decision = loop._get_tool_approval_decision("web_one", "write_file")

        self.assertFalse(decision["allowed"])
        self.assertTrue(decision["requires_confirmation"])
        self.assertEqual(decision["policy_profile"], "manual")

    def test_whatsapp_uses_fast_autonomous_policy_for_sensitive_tools(self):
        # WhatsApp is intentionally fast: contact allow-listing and hard tool
        # safety checks remain active, but no interactive approval is required.
        for tool in ("run_command", "write_file", "delete_file", "edit_skill"):
            decision = make_loop()._get_tool_approval_decision(
                "whatsapp_one", tool, is_whatsapp=True
            )
            self.assertTrue(decision["allowed"], tool)
            self.assertFalse(decision["requires_confirmation"], tool)
            self.assertEqual(decision["reason"], "channel_whatsapp_autonomous", tool)

        # The same policy applies to every sensitive tool, including cron removal.
        cron = make_loop()._get_tool_approval_decision(
            "whatsapp_one", "cron_remove", is_whatsapp=True
        )
        self.assertTrue(cron["allowed"])
        self.assertFalse(cron["requires_confirmation"])
        self.assertEqual(cron["reason"], "channel_whatsapp_autonomous")

        # Read-only tools remain allowed as before.
        read_only = make_loop()._get_tool_approval_decision(
            "whatsapp_one", "read_file", is_whatsapp=True
        )
        self.assertTrue(read_only["allowed"])
        self.assertEqual(read_only["reason"], "channel_whatsapp_autonomous")

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
            {
                "write_file",
                "delete_file",
                "run_command",
                "cron_remove",
                "edit_skill",
            }.issubset(
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


class TestConfirmationState(unittest.IsolatedAsyncioTestCase):
    def make_confirmation_manager(self):
        return ConfirmationManager(
            toolbox=None,
            truncate_fn=lambda text, limit: str(text)[:limit],
            safe_json_load_fn=lambda value: value,
        )

    async def test_wait_returns_decision_and_removes_pending_state(self):
        manager = self.make_confirmation_manager()
        manager.register_pending(
            "conf_approved", {"session_key": "web_one", "tool": "write_file"}
        )
        manager.resolve(
            "conf_approved",
            True,
            log_event=lambda *_args: None,
        )

        self.assertTrue(await manager.wait_for_resolution("conf_approved"))
        self.assertNotIn("conf_approved", manager.pending_confirmations)

    async def test_timeout_removes_pending_state(self):
        manager = self.make_confirmation_manager()
        manager.register_pending("conf_timeout", {"session_key": "web_one"})

        with self.assertRaises(asyncio.TimeoutError):
            await manager.wait_for_resolution("conf_timeout", timeout=0.001)

        self.assertNotIn("conf_timeout", manager.pending_confirmations)

    async def test_cancellation_removes_pending_state(self):
        manager = self.make_confirmation_manager()
        manager.register_pending("conf_cancelled", {"session_key": "web_one"})
        waiter = asyncio.create_task(
            manager.wait_for_resolution("conf_cancelled", timeout=5)
        )
        await asyncio.sleep(0)
        waiter.cancel()

        with self.assertRaises(asyncio.CancelledError):
            await waiter

        self.assertNotIn("conf_cancelled", manager.pending_confirmations)

    async def test_release_denies_and_wakes_only_related_sessions(self):
        manager = self.make_confirmation_manager()
        related = manager.register_pending(
            "conf_related", {"session_key": "web_one"}
        )
        unrelated = manager.register_pending(
            "conf_unrelated", {"session_key": "web_two"}
        )

        released = manager.release_for_sessions({"web_one"})

        self.assertEqual([conf_id for conf_id, _ in released], ["conf_related"])
        self.assertFalse(related["approved"])
        self.assertTrue(related["event"].is_set())
        self.assertFalse(unrelated["event"].is_set())
