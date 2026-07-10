import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock


class TestPromptMemoryCache(unittest.TestCase):
    def setUp(self):
        from core import prompt

        prompt.clear_memory_context_cache()

    def tearDown(self):
        from core import prompt

        prompt.clear_memory_context_cache()

    def test_unchanged_files_are_not_read_twice_and_write_reloads(self):
        from core import prompt

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            memory_dir = root / "memory"
            memory_dir.mkdir()
            journal = memory_dir / "2026-07-09.md"
            essence = root / "MEMORY.md"
            journal.write_text("one\ntwo\nthree\nfour\nfive\nsix", encoding="utf-8")
            essence.write_text("lasting essence", encoding="utf-8")

            real_read_text = Path.read_text
            reads = []

            def counted_read(path, *args, **kwargs):
                reads.append(Path(path))
                return real_read_text(path, *args, **kwargs)

            fake_now = mock.Mock()
            fake_now.strftime.return_value = "2026-07-09"
            fake_datetime = mock.Mock()
            fake_datetime.now.return_value = fake_now
            with (
                mock.patch.object(prompt, "MEMORY_DIR", memory_dir),
                mock.patch.object(prompt, "LONG_TERM_MEMORY_FILE", essence),
                mock.patch.object(prompt, "datetime", fake_datetime),
                mock.patch.object(Path, "read_text", counted_read),
            ):
                first = prompt.get_memory_context(True)
                second = prompt.get_memory_context(True)
                self.assertEqual(first, second)
                self.assertEqual(reads.count(journal), 1)
                self.assertEqual(reads.count(essence), 1)
                self.assertIn("Earlier events omitted", first)
                self.assertNotIn("one", first)

                essence.write_text("updated essence!", encoding="utf-8")
                os.utime(essence, None)
                updated = prompt.get_memory_context(True)
                self.assertIn("updated essence!", updated)
                self.assertEqual(reads.count(essence), 2)

    def test_date_and_privacy_are_isolated(self):
        from core import prompt

        hidden = prompt.get_memory_context(False)
        self.assertIn("intentionally hidden", hidden)

        with tempfile.TemporaryDirectory() as tmp:
            memory_dir = Path(tmp)
            (memory_dir / "2026-07-09.md").write_text("day one", encoding="utf-8")
            (memory_dir / "2026-07-10.md").write_text("day two", encoding="utf-8")
            missing_essence = memory_dir / "missing.md"
            dates = iter(["2026-07-09", "2026-07-10"])

            def fake_now():
                value = mock.Mock()
                value.strftime.return_value = next(dates)
                return value

            fake_datetime = mock.Mock()
            fake_datetime.now.side_effect = fake_now

            with (
                mock.patch.object(prompt, "MEMORY_DIR", memory_dir),
                mock.patch.object(prompt, "LONG_TERM_MEMORY_FILE", missing_essence),
                mock.patch.object(prompt, "datetime", fake_datetime),
            ):
                self.assertIn("day one", prompt.get_memory_context(True))
                self.assertIn("day two", prompt.get_memory_context(True))

        self.assertNotIn("day one", hidden)
