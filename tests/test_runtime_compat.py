import unittest

from core.runtime_compat import (
    describe_supported_python,
    enforce_supported_python_runtime,
    get_unsupported_python_message,
    is_supported_python_runtime,
)


class TestRuntimeCompat(unittest.TestCase):
    def test_windows_accepts_python_314(self):
        self.assertTrue(
            is_supported_python_runtime(version_info=(3, 14, 0), platform="win32")
        )

    def test_windows_rejects_python_315(self):
        self.assertFalse(
            is_supported_python_runtime(version_info=(3, 15, 0), platform="win32")
        )

    def test_non_windows_accepts_python_314(self):
        self.assertTrue(
            is_supported_python_runtime(version_info=(3, 14, 0), platform="linux")
        )

    def test_enforce_raises_clear_message(self):
        with self.assertRaises(RuntimeError) as ctx:
            enforce_supported_python_runtime(version_info=(3, 15, 0), platform="win32")

        message = str(ctx.exception)
        self.assertIn("Python 3.15.0", message)
        self.assertIn("Python 3.11 to 3.14", message)

    def test_supported_python_descriptions(self):
        self.assertEqual(describe_supported_python("win32"), "Python 3.11 to 3.14")
        self.assertEqual(describe_supported_python("linux"), "Python 3.11 to 3.14")

    def test_windows_message_is_plain_supported_range_error(self):
        message = get_unsupported_python_message(
            version_info=(3, 15, 0), platform="win32"
        )
        self.assertIn("Python 3.11 to 3.14", message)
        self.assertNotIn("asyncio subprocess usage", message)
