import unittest


class TestOperationalRedaction(unittest.TestCase):
    def test_redacts_cli_and_mapping_credentials(self):
        from core.redaction import redact_sensitive_text, redact_sensitive_value

        command = "python api.py --token super-secret --site https://example.test"
        redacted_command = redact_sensitive_text(command)
        self.assertNotIn("super-secret", redacted_command)
        self.assertIn("[REDACTED]", redacted_command)

        payload = redact_sensitive_value(
            {
                "command": command,
                "token": "mapping-secret",
                "nested": ["Bearer bearer-secret"],
            }
        )
        self.assertNotIn("super-secret", str(payload))
        self.assertNotIn("mapping-secret", str(payload))
        self.assertNotIn("bearer-secret", str(payload))

    def test_preserves_nonsecret_command_arguments(self):
        from core.redaction import redact_sensitive_text

        command = "python api.py --site https://example.test list_courses"
        self.assertEqual(redact_sensitive_text(command), command)
