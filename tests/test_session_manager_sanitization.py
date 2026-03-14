import unittest

from core.session_manager import (
    _sanitize_assistant_content,
    _sanitize_history_rows,
)


class TestSessionManagerSanitization(unittest.TestCase):
    def test_drops_assistant_message_with_only_legacy_xml_tool_tag(self):
        row, changed = _sanitize_assistant_content(
            {
                "role": "assistant",
                "content": "<run_command>echo hello</run_command>",
            }
        )

        self.assertTrue(changed)
        self.assertIsNone(row)

    def test_keeps_preamble_and_strips_legacy_xml_tool_tag(self):
        row, changed = _sanitize_assistant_content(
            {
                "role": "assistant",
                "content": "Voy a revisarlo.\n<read_file>index.html</read_file>\nMas texto.",
            }
        )

        self.assertTrue(changed)
        self.assertIsNotNone(row)
        self.assertEqual(row["content"], "Voy a revisarlo.")

    def test_drops_inline_tool_payload_without_tool_calls(self):
        row, changed = _sanitize_assistant_content(
            {
                "role": "assistant",
                "content": 'functions.run_command:1{"command":"echo hi"}',
            }
        )

        self.assertTrue(changed)
        self.assertIsNone(row)

    def test_preserves_empty_content_when_tool_calls_exist(self):
        row, changed = _sanitize_assistant_content(
            {
                "role": "assistant",
                "content": "<run_command>echo hello</run_command>",
                "tool_calls": [
                    {
                        "id": "call_1",
                        "type": "function",
                        "function": {
                            "name": "run_command",
                            "arguments": '{"command":"echo hello"}',
                        },
                    }
                ],
            }
        )

        self.assertTrue(changed)
        self.assertIsNotNone(row)
        self.assertEqual(row["content"], "")

    def test_sanitize_history_rows_counts_cleaned_entries(self):
        rows, cleaned_count = _sanitize_history_rows(
            [
                {"role": "user", "content": "hi"},
                {
                    "role": "assistant",
                    "content": "<read_file>index.html</read_file>",
                },
                {
                    "role": "assistant",
                    "content": "Te ayudo.\n<run_command>dir</run_command>",
                },
            ]
        )

        self.assertEqual(cleaned_count, 2)
        self.assertEqual(len(rows), 2)
        self.assertEqual(rows[-1]["content"], "Te ayudo.")


if __name__ == "__main__":
    unittest.main()
