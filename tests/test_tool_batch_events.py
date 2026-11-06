import unittest


class TestToolBatchEventService(unittest.IsolatedAsyncioTestCase):
    async def test_planned_running_and_result_events_keep_order_and_redaction(self):
        from core.tool_batch_events import (
            ToolBatchEventContext,
            ToolBatchEventService,
        )

        sequence = []
        published = []
        session_events = []

        async def publish_both(_message, content, metadata):
            published.append((content, metadata))
            sequence.append(metadata["status"])

        def log_session_event(_session_key, event):
            session_events.append(event)
            sequence.append(event["type"])

        service = ToolBatchEventService(
            publish_both=publish_both,
            parse_tool_call=lambda _call, _session: (
                "call-1",
                "run_command",
                {"command": "pytest -q", "token": "private"},
            ),
            build_preview=lambda *_args: {"kind": "command", "command": "pytest -q"},
            display_args=lambda _name, _args, *_preview: {"command": "pytest -q"},
            log_session_event=log_session_event,
            sensitive_tools={"run_command"},
            broadcast_max_chars=100,
        )
        context = ToolBatchEventContext(
            session_key="web_test",
            message=None,
            turn_id="turn-1",
            message_id="message-1",
        )
        progress = []

        def note_progress(kind):
            progress.append(kind)
            sequence.append(kind)

        await service.publish_planned([{}], context)
        await service.publish_running(
            context,
            tool_call_id="call-1",
            function_name="run_command",
            function_args={"command": "pytest -q", "token": "private"},
            is_whatsapp=False,
            on_progress_queued=note_progress,
        )
        service.record_finished(
            context,
            tool_call_id="call-1",
            function_name="run_command",
            result="done",
            is_blocked=False,
            is_internal=False,
        )
        await service.publish_result(
            context,
            tool_call_id="call-1",
            function_name="run_command",
            function_args={"command": "pytest -q"},
            result="done\n" + ("x" * 150),
        )

        self.assertEqual(
            sequence,
            [
                "planned",
                "running",
                "tool_progress",
                "tool_started",
                "tool_finished",
                "completed",
            ],
        )
        self.assertEqual(progress, ["tool_progress"])
        self.assertEqual(session_events[0]["args"], {"redacted": True})
        self.assertEqual(published[0][1]["turn_id"], "turn-1")
        self.assertEqual(published[-1][1]["status"], "completed")
        self.assertEqual(len(published[-1][1]["result"]), 100)

    async def test_failed_event_publication_does_not_escape(self):
        from core.tool_batch_events import (
            ToolBatchEventContext,
            ToolBatchEventService,
        )

        async def fail_publish(*_args):
            raise RuntimeError("socket closed")

        service = ToolBatchEventService(
            publish_both=fail_publish,
            parse_tool_call=lambda *_args: ("call-1", "read_file", {}),
            build_preview=lambda *_args: {},
            display_args=lambda *_args: {},
            log_session_event=lambda *_args: None,
            sensitive_tools=set(),
            broadcast_max_chars=100,
        )
        context = ToolBatchEventContext("web_test", None)

        await service.publish_failure(
            context,
            tool_call_id="call-1",
            function_name="read_file",
            function_args={},
            exception_type="RuntimeError",
        )
        await service.publish_result(
            context,
            tool_call_id="call-1",
            function_name="read_file",
            function_args={},
            result="ok",
        )
