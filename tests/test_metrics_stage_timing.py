import json
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest.mock import patch


class _UnserializableValue:
    def __str__(self):
        return "unserializable-value"


class TestMetricsStageTiming(unittest.TestCase):
    def setUp(self):
        import core.metrics as metrics_module

        metrics_module.MetricsCollector._instance = None

    def tearDown(self):
        import core.metrics as metrics_module

        if metrics_module.MetricsCollector._instance is not None:
            metrics_module.MetricsCollector._instance.close(0.5)
        metrics_module.MetricsCollector._instance = None

    def test_record_stage_timing_emits_expected_event_shape(self):
        from core.metrics import MetricsCollector

        collector = MetricsCollector()
        events = []
        collector._log_event = events.append

        collector.record_stage_timing(
            "web:test",
            "prompt_build",
            0.1239,
            metadata={"mode": "fast", "tool_count": 3},
        )

        self.assertEqual(len(events), 1)
        event = events[0]
        self.assertEqual(event["type"], "stage_timing")
        self.assertEqual(event["session"], "web:test")
        self.assertEqual(event["stage"], "prompt_build")
        self.assertEqual(event["duration_s"], 0.124)
        self.assertEqual(event["metadata"], {"mode": "fast", "tool_count": 3})
        self.assertIsInstance(event["ts"], float)

    def test_record_stage_timing_normalizes_large_and_unserializable_metadata(self):
        from core.metrics import MetricsCollector

        collector = MetricsCollector()
        events = []
        collector._log_event = events.append

        collector.record_stage_timing(
            "web:test",
            "turn_total",
            1.5,
            metadata={
                "long_text": "x" * 900,
                "sequence": list(range(25)),
                "nested": {
                    "object": _UnserializableValue(),
                    "deep": {"level1": {"level2": {"level3": {"level4": "stop"}}}},
                },
            },
        )

        event = events[0]
        metadata = event["metadata"]
        self.assertLessEqual(len(metadata["long_text"]), 500)
        self.assertEqual(metadata["sequence"][-1], "<truncated:5>")
        self.assertEqual(metadata["nested"]["object"], "unserializable-value")
        self.assertEqual(metadata["nested"]["deep"]["level1"]["level2"], "<truncated>")
        json.dumps(event)

    def test_event_logging_returns_while_disk_writer_is_blocked_and_preserves_order(self):
        import core.metrics as metrics_module

        entered = threading.Event()
        release = threading.Event()
        written = []

        def blocked_write(payloads):
            entered.set()
            release.wait(1.0)
            written.extend(payloads)

        with patch.object(metrics_module.MetricsCollector, "_append_batch", staticmethod(blocked_write)):
            collector = metrics_module.MetricsCollector()
            collector._log_event({"sequence": 1})
            self.assertTrue(entered.wait(0.5))
            started = time.perf_counter()
            collector._log_event({"sequence": 2})
            collector._log_event({"sequence": 3})
            self.assertLess(time.perf_counter() - started, 0.05)
            release.set()
            self.assertTrue(collector.flush(1.0))
        self.assertEqual([json.loads(item)["sequence"] for item in written], [1, 2, 3])

    def test_bounded_queue_counts_overflow_without_recursive_logging(self):
        import core.metrics as metrics_module

        entered = threading.Event()
        release = threading.Event()

        def blocked_write(_payloads):
            entered.set()
            release.wait(1.0)

        with (
            patch.object(metrics_module, "_METRICS_QUEUE_SIZE", 2),
            patch.object(metrics_module.MetricsCollector, "_append_batch", staticmethod(blocked_write)),
        ):
            collector = metrics_module.MetricsCollector()
            collector._log_event({"sequence": 0})
            self.assertTrue(entered.wait(0.5))
            for index in range(20):
                collector._log_event({"sequence": index + 1})
            self.assertGreater(collector.get_snapshot()["global"]["dropped_events"], 0)
            release.set()

    def test_flush_writes_valid_jsonl(self):
        import core.metrics as metrics_module

        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory) / "metrics.jsonl"
            with patch.object(metrics_module, "METRICS_FILE", target):
                collector = metrics_module.MetricsCollector()
                collector._log_event({"type": "one", "value": 1})
                collector._log_event({"type": "two", "value": 2})
                self.assertTrue(collector.flush(1.0))
                records = [json.loads(line) for line in target.read_text(encoding="utf-8").splitlines()]
                self.assertEqual([record["type"] for record in records], ["one", "two"])
