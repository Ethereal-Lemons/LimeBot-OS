import asyncio
import inspect
import os
import sys
import time
import types
import unittest

if "dotenv" not in sys.modules:
    dotenv = types.ModuleType("dotenv")
    dotenv.load_dotenv = lambda *args, **kwargs: None
    sys.modules["dotenv"] = dotenv

if "loguru" not in sys.modules:
    loguru = types.ModuleType("loguru")

    class _DummyLogger:
        def __getattr__(self, name):
            return lambda *args, **kwargs: None

    loguru.logger = _DummyLogger()
    sys.modules["loguru"] = loguru


async def _measure_loop_latency(duration_s: float, interval_s: float) -> float:
    start = time.perf_counter()
    expected = start
    max_delay = 0.0

    while (time.perf_counter() - start) < duration_s:
        expected += interval_s
        await asyncio.sleep(interval_s)
        actual = time.perf_counter()
        delay = actual - expected
        if delay > max_delay:
            max_delay = delay

    return max_delay


class _Delta:
    def __init__(self, content=None, tool_calls=None):
        self.content = content
        self.tool_calls = tool_calls


class _Choice:
    def __init__(self, delta):
        self.delta = delta


class _Chunk:
    def __init__(self, content=None, usage=None):
        self.choices = [_Choice(_Delta(content=content))]
        self.usage = usage


class TestLoopEndToEndLatency(unittest.IsolatedAsyncioTestCase):
    async def test_process_message_does_not_block_event_loop(self):
        try:
            import loguru  # noqa: F401
        except Exception:
            raise unittest.SkipTest("Missing dependencies (loguru).")

        from core.bus import MessageBus
        from core.events import InboundMessage
        from core.loop import AgentLoop

        class _TestAgentLoop(AgentLoop):
            async def _init_skills_and_tools(self) -> None:
                # Skip slow warmups and external connections for perf tests.
                self._tool_definitions = []
                self._warmed = True

            async def _llm_call_with_retry(self, *args, **kwargs):
                async def _stream():
                    for _ in range(60):
                        yield _Chunk(content="ok ")
                        await asyncio.sleep(0)
                    yield _Chunk(
                        content="done",
                        usage={
                            "prompt_tokens": 5,
                            "completion_tokens": 5,
                            "total_tokens": 10,
                        },
                    )

                return _stream()

            async def _build_full_system_prompt(self, *args, **kwargs):
                return "SYSTEM: TEST\n"

            async def _trim_history(self, *args, **kwargs):
                return

        if os.getenv("LIMEBOT_SKIP_PERF"):
            self.skipTest("Set LIMEBOT_SKIP_PERF to skip performance tests.")

        bus = MessageBus()
        agent = _TestAgentLoop(bus=bus)

        msg = InboundMessage(
            channel="web",
            sender_id="perf",
            chat_id="perf_chat",
            content="hi",
            metadata={},
        )

        latency_task = asyncio.create_task(
            _measure_loop_latency(duration_s=0.6, interval_s=0.01)
        )
        await agent._process_message(msg)
        max_delay = await latency_task

        self.assertLess(
            max_delay,
            0.5,
            f"Event loop delay too high: {max_delay:.3f}s",
        )

    async def test_process_message_records_stage_timing_after_loop_integration(self):
        try:
            import loguru  # noqa: F401
        except Exception:
            raise unittest.SkipTest("Missing dependencies (loguru).")

        from core.loop import AgentLoop

        try:
            process_source = inspect.getsource(AgentLoop._process_message)
        except (OSError, TypeError):
            process_source = ""
        if "record_stage_timing" not in process_source:
            self.skipTest(
                "Requires local core/loop.py integration to emit stage_timing events."
            )

        from core.bus import MessageBus
        from core.events import InboundMessage

        class _TestAgentLoop(AgentLoop):
            async def _init_skills_and_tools(self) -> None:
                self._tool_definitions = []
                self._warmed = True

            async def _llm_call_with_retry(self, *args, **kwargs):
                async def _stream():
                    yield _Chunk(content="done", usage={"prompt_tokens": 5, "completion_tokens": 5})

                return _stream()

            async def _build_full_system_prompt(self, *args, **kwargs):
                return "SYSTEM: TEST\n"

            async def _trim_history(self, *args, **kwargs):
                return

        bus = MessageBus()
        agent = _TestAgentLoop(bus=bus)
        events = []
        agent.metrics._log_event = events.append

        msg = InboundMessage(
            channel="web",
            sender_id="perf",
            chat_id="perf_chat",
            content="hi",
            metadata={},
        )

        await agent._process_message(msg)

        stage_events = [event for event in events if event.get("type") == "stage_timing"]
        stage_names = {event.get("stage") for event in stage_events}

        self.assertIn("turn_total", stage_names)
        self.assertIn("prompt_build", stage_names)
        self.assertIn("llm_first_call", stage_names)
