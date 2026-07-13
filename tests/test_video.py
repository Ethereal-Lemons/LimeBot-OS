import asyncio
import json
import socket
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest
from PIL import Image

from core.tool_defs import build_tool_definitions, shortlist_tool_definitions
from core.video.captions import Segment, choose_caption, parse_vtt, render_transcript
from core.video.contact_sheets import create_contact_sheets
from core.video.frames import (
    Frame,
    candidate_timestamps,
    deduplicate_frames,
    extract_frames,
    probe_video,
    select_included_frames,
)
from core.video.proxy import GuardedProxy, ProxySecurityError, is_public_address, resolve_public_host
from core.video.runtime import _merge_windows_path, resolve_video_binary
from core.video.service import (
    _base_result,
    _download_caption,
    _finalize,
    _validate_info,
    _ydl_options,
    analyze_video,
)
from core.video.time_utils import frame_budget, parse_timestamp
from core.video.whisper import _transcribe_chunk


def test_timestamps_and_candidate_budget():
    assert parse_timestamp("75") == 75
    assert parse_timestamp("01:15") == 75
    assert parse_timestamp("01:02:03.5") == 3723.5
    with pytest.raises(ValueError):
        parse_timestamp("1:99")
    assert frame_budget(5, True, 100) <= 11  # hard 2-fps ceiling
    assert frame_budget(3600, False, None) == 100


def test_windows_video_path_merge_preserves_order_and_deduplicates():
    assert _merge_windows_path(
        r"C:\Windows;C:\FFmpeg\bin",
        r"c:\ffmpeg\bin\;C:\NewTool",
    ) == r"C:\Windows;C:\FFmpeg\bin;C:\NewTool"


def test_core_video_import_keeps_optional_pillow_lazy():
    result = subprocess.run(
        [sys.executable, "-c", "import sys, core.video; assert 'PIL' not in sys.modules"],
        capture_output=True,
        text=True,
        timeout=10,
    )
    assert result.returncode == 0, result.stderr


def test_video_toggle_ui_loads_saves_and_discloses_upload():
    source = Path("web/src/components/config/ConfigPage.tsx").read_text(encoding="utf-8")
    assert "checked={config.VIDEO_WHISPER_ENABLED === 'true'}" in source
    assert "handleChange('VIDEO_WHISPER_ENABLED'" in source
    assert "caption-less video audio is uploaded to OpenAI" in source


def test_caption_selection_prefers_manual_declared_then_english():
    info = {
        "language": "es",
        "subtitles": {
            "en": [{"ext": "vtt", "url": "https://example/en"}],
            "es": [{"ext": "vtt", "url": "https://example/es"}],
        },
        "automatic_captions": {"es": [{"ext": "vtt", "url": "https://example/auto"}]},
    }
    language, track, automatic = choose_caption(info)
    assert language == "es"
    assert track["url"].endswith("/es")
    assert automatic is False


def test_vtt_deduplicates_overlapping_auto_cues():
    segments = parse_vtt("""WEBVTT

00:00:00.000 --> 00:00:01.000
Hello

00:00:01.000 --> 00:00:02.000
Hello world

00:00:02.000 --> 00:00:03.000
Hello world
""")
    assert [segment.text for segment in segments] == ["Hello", "world"]


def test_transcript_budget_keeps_chronology_and_relevance():
    segments = [Segment(index, index + 1, ("ordinary words " * 20) + ("needle" if index == 90 else "")) for index in range(120)]
    text, truncated = render_transcript(segments, "Where is the needle?")
    assert truncated
    assert len(text) <= 24_000
    assert "needle" in text
    timestamps = [line.split("]", 1)[0] for line in text.splitlines()]
    assert timestamps == sorted(timestamps)


def test_total_tool_result_is_bounded_and_valid_json():
    result = _base_result("balanced")
    result["transcript"]["text"] = "x" * 40_000
    payload = _finalize(result)
    assert len(payload) <= 30_000
    assert json.loads(payload)["transcript"]["truncated"] is True


@pytest.mark.parametrize("address", ["127.0.0.1", "10.0.0.1", "169.254.1.1", "::1", "fc00::1", "224.0.0.1", "0.0.0.0"])
def test_private_and_special_addresses_are_rejected(address):
    assert not is_public_address(address)


@pytest.mark.asyncio
async def test_mixed_dns_answers_are_rejected():
    answers = [
        (socket.AF_INET, socket.SOCK_STREAM, 6, "", ("93.184.216.34", 443)),
        (socket.AF_INET, socket.SOCK_STREAM, 6, "", ("127.0.0.1", 443)),
    ]
    with patch("core.video.proxy.socket.getaddrinfo", return_value=answers):
        with pytest.raises(ProxySecurityError):
            await resolve_public_host("example.test", 443)


@pytest.mark.asyncio
async def test_proxy_pins_destination_and_relays_http():
    seen = []

    async def upstream(reader, writer):
        seen.append(await reader.readuntil(b"\r\n\r\n"))
        writer.write(b"HTTP/1.1 200 OK\r\nContent-Length: 2\r\nConnection: close\r\n\r\nOK")
        await writer.drain()
        writer.close()

    server = await asyncio.start_server(upstream, "127.0.0.1", 0)
    port = server.sockets[0].getsockname()[1]
    with patch("core.video.proxy.resolve_public_host", new=AsyncMock(return_value="127.0.0.1")) as resolver:
        async with GuardedProxy() as proxy:
            reader, writer = await asyncio.open_connection("127.0.0.1", int(proxy.url.rsplit(":", 1)[1]))
            writer.write(f"GET http://video.example:{port}/clip HTTP/1.1\r\nHost: video.example:{port}\r\nConnection: close\r\n\r\n".encode())
            await writer.drain()
            response = await reader.read()
            writer.close()
            await writer.wait_closed()
        resolver.assert_awaited_once_with("video.example", port)
    server.close()
    await server.wait_closed()
    assert response.endswith(b"OK")
    assert seen[0].startswith(b"GET /clip HTTP/1.1")


@pytest.mark.asyncio
async def test_proxy_aborts_aggregate_response_over_limit():
    async def upstream(_reader, writer):
        writer.write(b"HTTP/1.1 200 OK\r\nContent-Length: 8\r\nConnection: close\r\n\r\n12345678")
        await writer.drain()
        writer.close()

    server = await asyncio.start_server(upstream, "127.0.0.1", 0)
    port = server.sockets[0].getsockname()[1]
    with patch("core.video.proxy.resolve_public_host", new=AsyncMock(return_value="127.0.0.1")):
        async with GuardedProxy(byte_limit=4) as proxy:
            reader, writer = await asyncio.open_connection("127.0.0.1", int(proxy.url.rsplit(":", 1)[1]))
            writer.write(f"GET http://video.example:{port}/clip HTTP/1.1\r\nHost: video.example\r\n\r\n".encode())
            await writer.drain()
            await reader.read()
            writer.close()
            await writer.wait_closed()
            await asyncio.sleep(0)
            assert isinstance(proxy.error, ProxySecurityError)
    server.close()
    await server.wait_closed()


def test_yt_dlp_is_forced_through_proxy_and_locked_down(tmp_path):
    options = _ydl_options("http://127.0.0.1:43210", tmp_path)
    assert options["proxy"] == "http://127.0.0.1:43210"
    assert options["noplaylist"] is True
    assert options["usenetrc"] is False
    assert options["cookiefile"] is None
    assert options["external_downloader"] == {}


@pytest.mark.parametrize(
    ("info", "message"),
    [
        ({"_type": "playlist", "duration": 10}, "playlists"),
        ({"duration": 10, "is_live": True}, "livestreams"),
        ({"duration": 10, "availability": "private"}, "private"),
        ({"duration": 7201}, "two-hour"),
    ],
)
def test_remote_metadata_rejections(info, message):
    with pytest.raises(ValueError, match=message):
        _validate_info(info)


@pytest.mark.asyncio
async def test_explicit_yt_dlp_proxy_cannot_be_bypassed_by_no_proxy(tmp_path):
    pytest.importorskip("yt_dlp")
    import yt_dlp
    from yt_dlp.networking.common import Request

    reached_target = asyncio.Event()

    async def forbidden_target(_reader, writer):
        reached_target.set()
        writer.close()

    target = await asyncio.start_server(forbidden_target, "127.0.0.1", 0)
    target_port = target.sockets[0].getsockname()[1]
    async with GuardedProxy() as proxy:
        options = _ydl_options(proxy.url, tmp_path)
        options["skip_download"] = True

        def request_localhost():
            with yt_dlp.YoutubeDL(options) as ydl:
                return ydl.urlopen(Request(f"http://127.0.0.1:{target_port}/blocked")).read()

        with patch.dict("os.environ", {"NO_PROXY": "127.0.0.1", "no_proxy": "127.0.0.1"}):
            with pytest.raises(Exception):
                await asyncio.to_thread(request_localhost)
        assert isinstance(proxy.error, ProxySecurityError)
        assert not reached_target.is_set()
    target.close()
    await target.wait_closed()


@pytest.mark.asyncio
async def test_caption_download_cannot_be_bypassed_by_no_proxy(tmp_path):
    reached_target = asyncio.Event()

    async def forbidden_target(_reader, writer):
        reached_target.set()
        writer.write(b"HTTP/1.1 200 OK\r\nContent-Length: 6\r\n\r\nWEBVTT")
        await writer.drain()
        writer.close()

    target = await asyncio.start_server(forbidden_target, "127.0.0.1", 0)
    target_port = target.sockets[0].getsockname()[1]
    async with GuardedProxy() as proxy:
        with patch.dict("os.environ", {"NO_PROXY": "127.0.0.1", "no_proxy": "127.0.0.1"}):
            with pytest.raises(Exception):
                await asyncio.to_thread(
                    _download_caption,
                    f"http://127.0.0.1:{target_port}/captions.vtt",
                    proxy.url,
                    tmp_path / "captions.vtt",
                )
        assert isinstance(proxy.error, ProxySecurityError)
        assert not reached_target.is_set()
    target.close()
    await target.wait_closed()


def test_shortlist_makes_video_tool_mandatory():
    definitions = build_tool_definitions({})
    video_schema = next(tool["function"] for tool in definitions if tool["function"]["name"] == "analyze_video")
    properties = video_schema["parameters"]["properties"]
    assert video_schema["parameters"]["required"] == ["source"]
    assert properties["detail"]["enum"] == ["transcript", "efficient", "balanced"]
    assert properties["resolution"]["enum"] == [512, 1024]
    assert properties["max_frames"]["maximum"] == 100
    names = {tool["function"]["name"] for tool in shortlist_tool_definitions(definitions, "watch this YouTube video https://youtu.be/example")}
    assert "analyze_video" in names


@pytest.mark.asyncio
async def test_missing_feature_error_has_exact_install_command():
    with patch("core.video.service.importlib.util.find_spec", return_value=None):
        payload = json.loads(await analyze_video("https://example.com/video.mp4", is_path_allowed=lambda _path: True))
    assert payload["status"] == "error"
    assert "npm run lime-bot feature install video" in payload["error"]


@pytest.mark.asyncio
async def test_remote_credentials_are_rejected_before_network():
    with patch("core.video.service._preflight_error", return_value=None):
        payload = json.loads(await analyze_video("https://user:secret@example.com/video.mp4", is_path_allowed=lambda _path: True))
    assert payload["status"] == "error"
    assert "credentials" in payload["error"]


@pytest.mark.asyncio
async def test_non_http_remote_scheme_is_rejected():
    with patch("core.video.service._preflight_error", return_value=None):
        payload = json.loads(await analyze_video("ftp://example.com/video.mp4", is_path_allowed=lambda _path: True))
    assert payload["status"] == "error"
    assert "HTTP(S)" in payload["error"]


@pytest.mark.asyncio
async def test_local_path_policy_is_checked_before_ffprobe(tmp_path):
    source = tmp_path / "denied.mp4"
    source.write_bytes(b"not opened")
    with patch("core.video.service._preflight_error", return_value=None):
        payload = json.loads(await analyze_video(str(source), is_path_allowed=lambda _path: False))
    assert payload["status"] == "error"
    assert "allowed paths" in payload["error"]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("enabled", "key", "message"),
    [(False, "", "VIDEO_WHISPER_ENABLED=false"), (True, "", "OPENAI_API_KEY is missing")],
)
async def test_transcript_mode_enforces_whisper_opt_in_and_key(tmp_path, enabled, key, message):
    source = tmp_path / "video.mp4"
    source.write_bytes(b"video")
    job = tmp_path / "job"
    job.mkdir()

    async def no_cleanup(_job):
        return None

    with (
        patch("core.video.service._preflight_error", return_value=None),
        patch("core.video.service._prepare_job", return_value=job),
        patch("core.video.service._delayed_cleanup", side_effect=no_cleanup),
        patch("core.video.frames.probe_video", return_value={"duration": 10, "width": 320, "height": 180}),
    ):
        payload = json.loads(await analyze_video(
            str(source),
            detail="transcript",
            is_path_allowed=lambda _path: True,
            whisper_enabled=enabled,
            openai_api_key=key,
        ))
    assert payload["status"] == "error"
    assert message in payload["error"]


@pytest.mark.asyncio
async def test_whisper_multipart_shape_and_timestamp_offset(tmp_path):
    audio = tmp_path / "audio.mp3"
    audio.write_bytes(b"audio")

    class Response:
        status_code = 200
        headers = {}

        @staticmethod
        def json():
            return {"language": "en", "segments": [{"start": 1.0, "end": 2.5, "text": "hello"}]}

    class Client:
        calls = []

        async def post(self, url, **kwargs):
            self.calls.append((url, kwargs))
            return Response()

    client = Client()
    segments, language = await _transcribe_chunk(client, audio, 2700.0, "test-key")
    assert language == "en"
    assert segments[0].start == 2701.0
    url, kwargs = client.calls[0]
    assert url.endswith("/v1/audio/transcriptions")
    assert kwargs["data"]["model"] == "whisper-1"
    assert kwargs["data"]["response_format"] == "verbose_json"
    assert kwargs["files"]["file"][2] == "audio/mpeg"
    assert kwargs["headers"]["Authorization"] == "Bearer test-key"


@pytest.mark.asyncio
async def test_whisper_retries_429_then_succeeds_without_exposing_body(tmp_path):
    audio = tmp_path / "audio.mp3"
    audio.write_bytes(b"audio")

    class Response:
        def __init__(self, status_code):
            self.status_code = status_code
            self.headers = {"Retry-After": "0"}

        def json(self):
            return {"segments": [{"start": 0, "end": 1, "text": "ok"}]}

    class Client:
        calls = 0

        async def post(self, _url, **_kwargs):
            self.calls += 1
            return Response(429 if self.calls == 1 else 200)

    client = Client()
    with patch("core.video.whisper.asyncio.sleep", new=AsyncMock()) as sleep:
        segments, _language = await _transcribe_chunk(client, audio, 0, "test-key")
    assert client.calls == 2
    sleep.assert_awaited_once()
    assert segments[0].text == "ok"


@pytest.mark.asyncio
async def test_native_captions_skip_media_download_and_whisper(tmp_path):
    class FakeProxy:
        url = "http://127.0.0.1:1"
        error = None

        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args):
            return None

    info = {
        "duration": 10,
        "title": "Captioned",
        "language": "en",
        "subtitles": {"en": [{"ext": "vtt", "url": "https://captions.example/track.vtt"}]},
    }

    async def no_cleanup(_job):
        return None

    job = tmp_path / "job"
    job.mkdir()
    with (
        patch("core.video.service._preflight_error", return_value=None),
        patch("core.video.service._prepare_job", return_value=job),
        patch("core.video.service._delayed_cleanup", side_effect=no_cleanup),
        patch("core.video.service.GuardedProxy", return_value=FakeProxy()),
        patch("core.video.service._extract_remote_info", return_value=info),
        patch("core.video.service._download_caption", return_value="WEBVTT\n\n00:00:00.000 --> 00:00:02.000\nHello"),
        patch("core.video.service._download_remote") as download,
        patch("core.video.service.transcribe", new=AsyncMock()) as whisper,
    ):
        payload = json.loads(await analyze_video(
            "https://video.example/watch",
            detail="transcript",
            is_path_allowed=lambda _path: True,
            whisper_enabled=True,
            openai_api_key="test-key",
        ))
    assert payload["status"] == "ok"
    assert payload["transcript"]["source"] == "captions"
    download.assert_not_called()
    whisper.assert_not_awaited()


def test_dedup_and_contact_sheet_limits(tmp_path):
    frames = []
    for index in range(50):
        path = tmp_path / f"{index}.jpg"
        Image.new("RGB", (320, 180), (index * 5 % 255, index * 3 % 255, index * 7 % 255)).save(path)
        frames.append(Frame(path, float(index), "uniform"))
    deduped = deduplicate_frames(frames, threshold=0.0)
    included = select_included_frames(deduped)
    sheets = create_contact_sheets(included, tmp_path, 512)
    assert len(included) == 48
    assert len(sheets) == 3
    assert all(path.stat().st_size < 4 * 1024 * 1024 for path in sheets)


@pytest.mark.skipif(not resolve_video_binary("ffmpeg") or not resolve_video_binary("ffprobe"), reason="FFmpeg integration runtime not installed")
def test_ffmpeg_generated_clip_is_probeable(tmp_path):
    clip = tmp_path / "clip.mp4"
    subprocess.run([
        "ffmpeg", "-nostdin", "-hide_banner", "-loglevel", "error", "-f", "lavfi",
        "-i", "testsrc=size=320x180:rate=2", "-t", "3", "-pix_fmt", "yuv420p", "-y", str(clip),
    ], check=True, timeout=30)
    metadata = probe_video(clip)
    assert 2.5 <= metadata["duration"] <= 3.5


@pytest.mark.skipif(not resolve_video_binary("ffmpeg") or not resolve_video_binary("ffprobe"), reason="FFmpeg integration runtime not installed")
def test_ffmpeg_clip_exercises_keyframe_scene_range_and_dedup(tmp_path):
    clip = tmp_path / "scenes.mp4"
    subprocess.run([
        "ffmpeg", "-nostdin", "-hide_banner", "-loglevel", "error",
        "-f", "lavfi", "-i", "color=c=red:s=320x180:d=2:r=2",
        "-f", "lavfi", "-i", "color=c=blue:s=320x180:d=2:r=2",
        "-f", "lavfi", "-i", "color=c=green:s=320x180:d=2:r=2",
        "-filter_complex", "[0:v][1:v][2:v]concat=n=3:v=1:a=0,format=yuv420p",
        "-g", "4", "-y", str(clip),
    ], check=True, timeout=30)
    efficient = candidate_timestamps(clip, "efficient", 1.0, 5.0, 8)
    balanced = candidate_timestamps(clip, "balanced", 1.0, 5.0, 8)
    assert efficient[0][0] == 1.0 and efficient[-1][0] <= 5.0
    assert balanced[0][0] == 1.0 and balanced[-1][0] <= 5.0
    frames = extract_frames(clip, tmp_path / "frames", balanced, 512)
    deduped = deduplicate_frames(frames)
    assert frames
    assert deduped[0].timestamp >= 1.0
    assert deduped[-1].timestamp <= 5.0
