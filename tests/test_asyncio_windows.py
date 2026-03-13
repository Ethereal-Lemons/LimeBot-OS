import unittest
from types import SimpleNamespace
from unittest.mock import patch

from core.asyncio_windows import (
    install_windows_asyncio_exception_filter,
    should_suppress_windows_proactor_error,
)


def _winerror(code: int) -> OSError:
    exc = OSError("transport closed")
    exc.winerror = code
    return exc


class _FakeLoop:
    def __init__(self):
        self.handler = None
        self.default_calls = []
        self.previous_calls = []

    def get_exception_handler(self):
        def _previous(loop, context):
            loop.previous_calls.append(context)

        return _previous

    def set_exception_handler(self, handler):
        self.handler = handler

    def default_exception_handler(self, context):
        self.default_calls.append(context)


class TestAsyncioWindows(unittest.TestCase):
    def test_suppresses_known_proactor_connection_lost_winerror(self):
        context = {
            "exception": _winerror(10038),
            "handle": SimpleNamespace(
                _callback=SimpleNamespace(
                    __qualname__="_ProactorBasePipeTransport._call_connection_lost"
                )
            ),
        }

        with patch("core.asyncio_windows._is_windows", return_value=True):
            self.assertTrue(should_suppress_windows_proactor_error(context))

    def test_does_not_suppress_other_windows_socket_errors(self):
        context = {
            "exception": _winerror(10054),
            "handle": SimpleNamespace(
                _callback=SimpleNamespace(
                    __qualname__="_ProactorBasePipeTransport._call_connection_lost"
                )
            ),
        }

        with patch("core.asyncio_windows._is_windows", return_value=True):
            self.assertFalse(should_suppress_windows_proactor_error(context))

    def test_installed_handler_skips_known_benign_context(self):
        loop = _FakeLoop()
        context = {
            "exception": _winerror(10038),
            "message": "Exception in callback _ProactorBasePipeTransport._call_connection_lost()",
        }

        with patch("core.asyncio_windows._is_windows", return_value=True):
            install_windows_asyncio_exception_filter(loop)
            self.assertIsNotNone(loop.handler)
            loop.handler(loop, context)

        self.assertEqual(loop.previous_calls, [])
        self.assertEqual(loop.default_calls, [])

    def test_installed_handler_delegates_unrelated_context(self):
        loop = _FakeLoop()
        context = {
            "exception": RuntimeError("boom"),
            "message": "something else",
        }

        with patch("core.asyncio_windows._is_windows", return_value=True):
            install_windows_asyncio_exception_filter(loop)
            self.assertIsNotNone(loop.handler)
            loop.handler(loop, context)

        self.assertEqual(loop.previous_calls, [context])
        self.assertEqual(loop.default_calls, [])
