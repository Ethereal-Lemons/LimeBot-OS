import unittest
from unittest.mock import patch

from core.asyncio_compat import configure_asyncio_runtime


class TestAsyncioCompat(unittest.TestCase):
    def test_configure_asyncio_runtime_keeps_windows_default_policy(self):
        with patch("core.asyncio_compat.sys.platform", "win32"), patch(
            "asyncio.set_event_loop_policy"
        ) as set_policy:
            configure_asyncio_runtime()

        set_policy.assert_not_called()
