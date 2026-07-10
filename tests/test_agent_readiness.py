import asyncio
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

from core.bus import MessageBus
from core.events import InboundMessage
from core.loop import AgentLoop


class _Delta:
    def __init__(self, content=None, tool_calls=None):
        self.content = content
        self.tool_calls = tool_calls


class _Choice:
    def __init__(self, delta):
        self.delta = delta


class _Chunk:
    def __init__(self, content=None):
        self.choices = [_Choice(_Delta(content=content))]
        self.usage = None


class TestAgentReadiness(unittest.IsolatedAsyncioTestCase):
    async def test_stop_cancels_active_provider_tasks(self):
        class StoppableAgent(AgentLoop):
            async def _initialize_capabilities(self):
                self._readiness_event.set()

        agent = StoppableAgent(MessageBus())
        provider_task = asyncio.create_task(asyncio.Event().wait())
        agent.active_tasks["web_chat"] = provider_task

        await agent.stop()

        self.assertTrue(provider_task.cancelled())
        self.assertEqual(agent.active_tasks, {})

    async def test_llm_warmup_uses_openai_minimum_output_tokens(self):
        class WarmupAgent(AgentLoop):
            async def _initialize_capabilities(self):
                self._readiness_event.set()

        agent = WarmupAgent(MessageBus())
        agent.vector_service = SimpleNamespace(
            _ensure_init=AsyncMock(return_value=None),
            _get_embedding=AsyncMock(return_value=None),
        )
        provider = SimpleNamespace(is_codex=False)
        agent.llm_client = SimpleNamespace(
            resolve_provider=MagicMock(return_value=provider),
            complete=AsyncMock(return_value=object()),
        )

        await agent._warm_up_services()

        request = agent.llm_client.complete.await_args.args[1]
        self.assertEqual(request.max_tokens, 16)
        await agent.stop()

    async def test_required_phases_reach_ready_in_order(self):
        manager = type("Manager", (), {"initialize": AsyncMock(return_value=None)})()
        with patch(
            "core.loop.SkillRegistry.discover_and_load", return_value=None
        ), patch(
            "core.loop.SubagentRegistry.discover_and_load", return_value=None
        ), patch(
            "core.mcp_client.get_mcp_manager", return_value=manager
        ), patch.object(
            AgentLoop, "_warm_up_services", new=AsyncMock(return_value=None)
        ):
            agent = AgentLoop(MessageBus())
            status = await agent.await_ready(timeout=1)

        self.assertTrue(status["ready"])
        self.assertEqual(status["status"], "ready")
        self.assertEqual(
            agent._readiness_phase_history,
            ["created", "skills", "subagents", "mcp", "tools", "ready"],
        )
        await agent.stop()

    async def test_optional_mcp_failure_becomes_degraded_ready(self):
        manager = type(
            "Manager",
            (),
            {"initialize": AsyncMock(side_effect=RuntimeError("private endpoint failed"))},
        )()
        with patch(
            "core.loop.SkillRegistry.discover_and_load", return_value=None
        ), patch(
            "core.loop.SubagentRegistry.discover_and_load", return_value=None
        ), patch(
            "core.mcp_client.get_mcp_manager", return_value=manager
        ), patch.object(
            AgentLoop, "_warm_up_services", new=AsyncMock(return_value=None)
        ):
            agent = AgentLoop(MessageBus())
            status = await agent.await_ready(timeout=1)

        self.assertTrue(status["ready"])
        self.assertEqual(status["status"], "degraded")
        self.assertEqual(status["degraded_reasons"], ["mcp_unavailable"])
        self.assertNotIn("private endpoint", str(status))
        await agent.stop()

    async def test_required_discovery_failure_is_redacted(self):
        with patch(
            "core.loop.SkillRegistry.discover_and_load",
            side_effect=RuntimeError("C:/private/path failed"),
        ):
            agent = AgentLoop(MessageBus())
            status = await agent.await_ready(timeout=1)

        self.assertFalse(status["ready"])
        self.assertEqual(status["status"], "failed")
        self.assertEqual(
            status["failure_code"], "required_capability_initialization_failed"
        )
        self.assertNotIn("private/path", str(status))
        await agent.stop()

    async def test_timeout_and_cancellation_never_leave_waiters_hanging(self):
        class SlowAgent(AgentLoop):
            def __init__(self, *args, **kwargs):
                self.release_initialization = asyncio.Event()
                super().__init__(*args, **kwargs)

            async def _init_skills_and_tools(self):
                await self.release_initialization.wait()

        agent = SlowAgent(MessageBus())
        timed_out = await agent.await_ready(timeout=0.01)
        self.assertEqual(timed_out["status"], "timeout")
        self.assertFalse(timed_out["ready"])

        await agent.stop()
        cancelled = await agent.await_ready(timeout=0.01)
        self.assertEqual(cancelled["status"], "failed")
        self.assertEqual(cancelled["failure_code"], "initialization_cancelled")

    async def test_first_message_waits_for_late_tool_discovery(self):
        class RaceAgent(AgentLoop):
            def __init__(self, *args, **kwargs):
                self.release_initialization = asyncio.Event()
                self.llm_called = asyncio.Event()
                self.seen_tools = []
                super().__init__(*args, **kwargs)
                # This test targets readiness ordering, not fast-mode's casual
                # tool gate. Keep the former full-schema behavior explicit.
                self.config.ai_harness.mode = "balanced"
                self.config.tool_shortlist_enabled = False

            async def _init_skills_and_tools(self):
                await self.release_initialization.wait()
                self._tool_definitions = [
                    {
                        "type": "function",
                        "function": {
                            "name": "late_discovered_tool",
                            "description": "Appears after discovery",
                            "parameters": {"type": "object", "properties": {}},
                        },
                    }
                ]

            async def _llm_call_with_retry(self, *args, **kwargs):
                self.seen_tools = kwargs.get("tool_definitions_override") or []
                self.llm_called.set()

                async def stream():
                    yield _Chunk(content="ready")

                return stream()

            async def _build_full_system_prompt(self, *args, **kwargs):
                return "SYSTEM: TEST"

            async def _trim_history(self, *args, **kwargs):
                return None

        bus = MessageBus()
        agent = RaceAgent(bus)
        message = InboundMessage(
            channel="web",
            sender_id="readiness-user",
            chat_id="readiness-chat",
            content="hi",
            metadata={},
        )
        processing = asyncio.create_task(agent._process_message(message))
        await asyncio.sleep(0.03)
        self.assertFalse(agent.llm_called.is_set())

        agent.release_initialization.set()
        await asyncio.wait_for(processing, timeout=2)
        self.assertTrue(agent.llm_called.is_set())
        self.assertEqual(
            agent.seen_tools[0]["function"]["name"], "late_discovered_tool"
        )
        await agent.stop()

    async def test_failed_readiness_returns_one_user_visible_error(self):
        class FailedAgent(AgentLoop):
            async def _init_skills_and_tools(self):
                raise RuntimeError("required discovery failed")

        bus = MessageBus()
        agent = FailedAgent(bus)
        await agent.await_ready(timeout=1)
        await agent._process_message(
            InboundMessage(
                channel="web",
                sender_id="failed-user",
                chat_id="failed-chat",
                content="hello",
                metadata={},
            )
        )

        messages = []
        while not bus.outbound.empty():
            messages.append(await bus.consume_outbound())
        visible = [message for message in messages if message.content]
        self.assertEqual(len(visible), 1)
        self.assertIn("required capabilities", visible[0].content)
        self.assertEqual(
            visible[0].metadata["error_code"],
            "required_capability_initialization_failed",
        )
        await agent.stop()


if __name__ == "__main__":
    unittest.main()
