import json
import tempfile
import unittest
from pathlib import Path

from core.review_entrypoint import (
    artifact_to_markdown,
    build_review_artifact,
    build_review_prompt,
    parse_unified_diff,
    read_diff_input,
    redact_secrets,
)


SAMPLE_DIFF = """diff --git a/core/example.py b/core/example.py
index 1111111..2222222 100644
--- a/core/example.py
+++ b/core/example.py
@@ -10,2 +10,3 @@ def work():
-    return False
+    token = "ghp_abcdefghijklmnopqrstuvwxyz123456"
+    return True
 context = 1
diff --git a/tests/test_example.py b/tests/test_example.py
--- a/tests/test_example.py
+++ b/tests/test_example.py
@@ -1 +1,2 @@
 def test_work():
+    assert work()
"""


class TestReviewEntrypoint(unittest.TestCase):
    def test_parser_extracts_files_counts_and_hunk_ranges(self):
        parsed = parse_unified_diff(SAMPLE_DIFF)

        self.assertEqual(len(parsed.files), 2)
        self.assertEqual(parsed.added, 3)
        self.assertEqual(parsed.removed, 1)
        self.assertEqual(parsed.files[0].new_path, "core/example.py")
        self.assertEqual(parsed.files[0].hunks[0].new_start, 10)
        self.assertEqual(parsed.files[0].hunks[0].new_count, 3)

    def test_parser_caps_utf8_payload_without_losing_summary(self):
        large = SAMPLE_DIFF + ("+extra line\n" * 1000)
        parsed = parse_unified_diff(large, max_diff_bytes=1024)

        self.assertTrue(parsed.truncated)
        self.assertLessEqual(len(parsed.text.encode("utf-8")), 1024)
        self.assertEqual(len(parsed.files), 2)

    def test_secret_redaction_preserves_safe_context(self):
        value = (
            "OPENAI_API_KEY=sk-abcdefghijklmnopqrstuvwxyz\n"
            "Authorization: Bearer top-secret-token\n"
            "safe=value\n"
        )

        redacted = redact_secrets(value)

        self.assertNotIn("abcdefghijklmnopqrstuvwxyz", redacted)
        self.assertNotIn("top-secret-token", redacted)
        self.assertIn("safe=value", redacted)
        self.assertEqual(redacted.count("\n"), value.count("\n"))

    def test_prompt_requires_findings_references_and_secret_safety(self):
        prompt = build_review_prompt(parse_unified_diff(SAMPLE_DIFF))

        self.assertIn("P0, P1, P2, P3", prompt)
        self.assertIn("file path and new-file line or hunk reference", prompt)
        self.assertIn("No actionable findings.", prompt)
        self.assertIn("Do not reproduce credentials", prompt)
        self.assertNotIn("ghp_abcdefghijklmnopqrstuvwxyz123456", prompt)

    def test_file_reader_reads_only_explicit_file_and_marks_large_input(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / "change.diff"
            path.write_bytes(b"x" * (90 * 1024))
            value, truncated = read_diff_input(str(path), max_input_bytes=80 * 1024)

        self.assertTrue(truncated)
        self.assertEqual(len(value), 80 * 1024)

    def test_artifact_and_markdown_never_restore_redacted_secret(self):
        parsed = parse_unified_diff(SAMPLE_DIFF)
        prompt = build_review_prompt(parsed)
        artifact = build_review_artifact(parsed, prompt)
        markdown = artifact_to_markdown(artifact)

        self.assertEqual(artifact["mode"], "prompt_only")
        self.assertNotIn("ghp_abcdefghijklmnopqrstuvwxyz123456", json.dumps(artifact))
        self.assertIn("Redacted Review Prompt", markdown)


if __name__ == "__main__":
    unittest.main()
