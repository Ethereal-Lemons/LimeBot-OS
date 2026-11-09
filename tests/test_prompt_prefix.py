import copy
import unittest

from core.prompt_prefix import PER_MESSAGE_BOUNDARY, relocate_per_message_context


def _system(volatile: str) -> dict:
    return {
        "role": "system",
        "content": "PERSONA AND RULES" + PER_MESSAGE_BOUNDARY + volatile,
    }


class RelocatePerMessageContextTests(unittest.TestCase):
    def test_per_message_text_moves_after_the_newest_user_message(self):
        messages = [
            _system("clock 10:00:01"),
            {"role": "user", "content": "first"},
            {"role": "assistant", "content": "answer"},
            {"role": "user", "content": "second"},
            {"role": "assistant", "content": "", "tool_calls": [{"id": "1"}]},
            {"role": "tool", "tool_call_id": "1", "content": "result"},
        ]
        out = relocate_per_message_context(messages)

        self.assertEqual(out[0], {"role": "system", "content": "PERSONA AND RULES"})
        self.assertEqual([m["role"] for m in out[1:5]], ["user", "assistant", "user", "system"])
        self.assertIn("clock 10:00:01", out[4]["content"])
        self.assertTrue(out[4]["content"].startswith("--- PER-MESSAGE CONTEXT ---"))
        self.assertEqual(out[5:], messages[4:])

    def test_the_prefix_is_identical_when_only_per_message_text_changes(self):
        history = [
            {"role": "user", "content": "first"},
            {"role": "assistant", "content": "answer"},
        ]
        turn_one = relocate_per_message_context(
            [_system("clock 10:00:01 recalled A")] + history + [{"role": "user", "content": "q1"}]
        )
        turn_two = relocate_per_message_context(
            [_system("clock 10:05:44 recalled B")]
            + history
            + [{"role": "user", "content": "q1"}, {"role": "assistant", "content": "a1"}, {"role": "user", "content": "q2"}]
        )
        shared = 4  # system head, first, answer, q1
        self.assertEqual(turn_one[:shared], turn_two[:shared])

    def test_the_input_is_not_modified(self):
        messages = [_system("clock"), {"role": "user", "content": "hi"}]
        snapshot = copy.deepcopy(messages)
        relocate_per_message_context(messages)
        self.assertEqual(messages, snapshot)

    def test_requests_without_a_boundary_or_a_user_message_are_unchanged(self):
        plain = [{"role": "system", "content": "no boundary"}, {"role": "user", "content": "hi"}]
        self.assertIs(relocate_per_message_context(plain), plain)
        no_user = [_system("clock")]
        self.assertIs(relocate_per_message_context(no_user), no_user)
        empty_tail = [{"role": "system", "content": "head" + PER_MESSAGE_BOUNDARY}, {"role": "user", "content": "hi"}]
        self.assertIs(relocate_per_message_context(empty_tail), empty_tail)
        self.assertEqual(relocate_per_message_context([]), [])

    def test_non_string_system_content_is_left_alone(self):
        messages = [{"role": "system", "content": [{"type": "text", "text": "x"}]}, {"role": "user", "content": "hi"}]
        self.assertIs(relocate_per_message_context(messages), messages)


if __name__ == "__main__":
    unittest.main()
