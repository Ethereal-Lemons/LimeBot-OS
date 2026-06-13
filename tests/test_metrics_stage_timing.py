import json
import unittest


class _UnserializableValue:
    def __str__(self):
        return "unserializable-value"


class TestMetricsStageTiming(unittest.TestCase):
    def setUp(self):
        import core.metrics as metrics_module

        metrics_module.MetricsCollector._instance = None

    def tearDown(self):
        import core.metrics as metrics_module

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
