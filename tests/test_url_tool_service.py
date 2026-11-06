import tempfile
import unittest
from pathlib import Path


class _FakeFetcher:
    MAX_DOWNLOAD_BYTES = 15 * 1024 * 1024

    def __init__(self, *, safe=True, content_type="text/plain", data=b"payload"):
        self.safe = safe
        self.content_type = content_type
        self.data = data
        self.fetch_calls = []

    def is_safe_public_url(self, url):
        return self.safe, "blocked destination"

    async def fetch(self, url, **kwargs):
        self.fetch_calls.append((url, kwargs))
        return url, self.content_type, self.data

    @staticmethod
    def guess_download_extension(_data, _content_type, _url):
        return ".bin"


class TestUrlToolService(unittest.IsolatedAsyncioTestCase):
    async def test_download_uses_guarded_fetch_and_preserves_request_headers(self):
        from core.url_tool_service import UrlToolService

        with tempfile.TemporaryDirectory() as temporary:
            fetcher = _FakeFetcher()
            progress = []

            async def send_progress(message):
                progress.append(message)

            service = UrlToolService(
                get_fetcher=lambda: fetcher,
                download_directory=lambda: Path(temporary),
                display_path=lambda path: path.name,
                send_progress=send_progress,
            )
            result = await service.fetch_url_to_temp(
                "https://example.com/file",
                max_bytes=1024,
                referer="https://example.com/",
                user_agent="LimeBot test",
                extra_headers={"X-Test": "yes"},
            )

            self.assertTrue(result.startswith("dl_"))
            self.assertTrue((Path(temporary) / result).exists())
            self.assertEqual(progress, ["⬇️ Downloading: https://example.com/file"])
            self.assertEqual(
                fetcher.fetch_calls[0][1],
                {
                    "max_bytes": 1024,
                    "timeout": 30.0,
                    "extra_headers": {
                        "X-Test": "yes",
                        "Referer": "https://example.com/",
                        "User-Agent": "LimeBot test",
                    },
                },
            )

    async def test_readable_fetch_strips_page_chrome_and_bounds_text(self):
        from core.url_tool_service import UrlToolService

        fetcher = _FakeFetcher(
            content_type="text/html",
            data=(
                "<html><nav>Navigation</nav><script>secret()</script>"
                "<main>Hello world</main></html>"
            ).encode(),
        )
        service = UrlToolService(
            get_fetcher=lambda: fetcher,
            download_directory=Path,
            display_path=str,
            send_progress=lambda _message: None,
        )

        result = await service.fetch_readable_text("https://example.com", max_chars=5)

        self.assertEqual(result, "Hello\n... (truncated at 5 chars)")
        self.assertEqual(
            fetcher.fetch_calls[0][1],
            {"max_bytes": 5 * 1024 * 1024, "timeout": 20.0},
        )

    async def test_unsafe_url_is_rejected_before_fetch_or_progress(self):
        from core.url_tool_service import UrlToolService

        fetcher = _FakeFetcher(safe=False)
        progress = []
        service = UrlToolService(
            get_fetcher=lambda: fetcher,
            download_directory=Path,
            display_path=str,
            send_progress=lambda message: progress.append(message),
        )

        result = await service.fetch_url_to_temp("http://localhost/private")

        self.assertEqual(result, "Error: blocked destination")
        self.assertEqual(fetcher.fetch_calls, [])
        self.assertEqual(progress, [])
