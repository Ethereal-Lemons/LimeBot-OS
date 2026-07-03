import json
import unittest
from pathlib import Path
from unittest.mock import patch


class TestVoiceDeliveryRouting(unittest.TestCase):
    """The pure routing decision that drives audio-vs-text delivery."""

    def _resolve(self, channel, cfg, voice_on, has_reply):
        from core.loop import AgentLoop

        return AgentLoop._resolve_voice_delivery(channel, cfg, voice_on, has_reply)

    def test_text_when_voice_off(self):
        cfg = {"channels": ["discord"], "send_text_with_audio": False}
        self.assertEqual(self._resolve("discord", cfg, False, True), "text")

    def test_text_when_no_reply(self):
        cfg = {"channels": ["discord"], "send_text_with_audio": False}
        self.assertEqual(self._resolve("discord", cfg, True, False), "text")

    def test_audio_only_for_discord(self):
        cfg = {"channels": ["discord"], "send_text_with_audio": False}
        self.assertEqual(self._resolve("discord", cfg, True, True), "audio_only")

    def test_audio_and_text_for_whatsapp(self):
        cfg = {"channels": ["whatsapp"], "send_text_with_audio": True}
        self.assertEqual(
            self._resolve("whatsapp", cfg, True, True), "audio_and_text"
        )

    def test_web_url_for_web(self):
        cfg = {"channels": ["web"], "send_text_with_audio": False}
        self.assertEqual(self._resolve("web", cfg, True, True), "web_url")

    def test_text_when_channel_not_enabled(self):
        # Default config (web only) must keep Discord replies as plain text.
        cfg = {"channels": ["web"], "send_text_with_audio": False}
        self.assertEqual(self._resolve("discord", cfg, True, True), "text")


class TestSynthesizeToFile(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        try:
            import loguru  # noqa: F401
        except Exception:
            raise unittest.SkipTest("Missing dependencies (loguru).")

    async def test_returns_empty_when_text_blank(self):
        from core.tts import ElevenLabsTTS

        self.assertEqual(await ElevenLabsTTS.synthesize_to_file("   "), "")

    async def test_returns_empty_when_synthesis_raises(self):
        from core.tts import ElevenLabsTTS

        async def boom(_text):
            raise RuntimeError("no key")

        with patch.object(ElevenLabsTTS, "synthesize_text", side_effect=boom):
            self.assertEqual(await ElevenLabsTTS.synthesize_to_file("hi"), "")


class TestSynthesizeVoiceFileHelper(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        try:
            import loguru  # noqa: F401
        except Exception:
            raise unittest.SkipTest("Missing dependencies (loguru).")

    def _loop(self):
        from core.loop import AgentLoop

        return AgentLoop.__new__(AgentLoop)

    async def test_none_when_disabled(self):
        from core.tts import ElevenLabsTTS

        loop = self._loop()
        with patch.object(
            ElevenLabsTTS, "get_voice_config", return_value={"enabled": False}
        ), patch.object(ElevenLabsTTS, "get_api_key", return_value="k"):
            self.assertIsNone(await loop._synthesize_voice_file("hi"))

    async def test_none_when_no_key(self):
        from core.tts import ElevenLabsTTS

        loop = self._loop()
        with patch.object(
            ElevenLabsTTS, "get_voice_config", return_value={"enabled": True}
        ), patch.object(ElevenLabsTTS, "get_api_key", return_value=""):
            self.assertIsNone(await loop._synthesize_voice_file("hi"))

    async def test_returns_path_when_enabled(self):
        from core.tts import ElevenLabsTTS

        loop = self._loop()

        async def fake_synth(_text, filename_prefix="voice"):
            return "/tmp/voice_abcd1234.mp3"

        with patch.object(
            ElevenLabsTTS, "get_voice_config", return_value={"enabled": True}
        ), patch.object(
            ElevenLabsTTS, "get_api_key", return_value="k"
        ), patch.object(
            ElevenLabsTTS, "synthesize_to_file", side_effect=fake_synth
        ):
            self.assertEqual(
                await loop._synthesize_voice_file("hi"), "/tmp/voice_abcd1234.mp3"
            )


class TestVoiceConfigDefaults(unittest.TestCase):
    def test_defaults_include_channel_fields(self):
        from core.tts import ElevenLabsTTS

        cfg = ElevenLabsTTS.get_voice_config()
        self.assertIn("channels", cfg)
        self.assertIn("send_text_with_audio", cfg)
        self.assertIsInstance(cfg["channels"], list)


class TestSendVoiceTool(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        try:
            import loguru  # noqa: F401
        except Exception:
            raise unittest.SkipTest("Missing dependencies (loguru).")

    def _toolbox(self, sent):
        from pathlib import Path
        from types import SimpleNamespace
        from core.bus import MessageBus
        from core.tools import Toolbox

        bus = MessageBus()

        async def _capture(msg):
            sent.append(msg)

        bus.publish_outbound = _capture
        config = SimpleNamespace(skills=SimpleNamespace(enabled=[]))
        return Toolbox(allowed_paths=[str(Path.cwd())], bus=bus, config=config)

    async def test_error_when_no_key(self):
        from core.context import tool_context
        from core.tts import ElevenLabsTTS

        sent = []
        toolbox = self._toolbox(sent)
        token = tool_context.set(
            {"channel": "discord", "chat_id": "42", "sender_id": "u1"}
        )
        try:
            with patch.object(ElevenLabsTTS, "get_api_key", return_value=""):
                result = await toolbox.send_voice("hello there")
        finally:
            tool_context.reset(token)

        self.assertTrue(result.startswith("Error:"))
        self.assertIn("ElevenLabs", result)
        self.assertEqual(sent, [])

    async def test_sends_audio_file_on_discord(self):
        from core.context import tool_context
        from core.tts import ElevenLabsTTS

        sent = []
        toolbox = self._toolbox(sent)

        async def fake_to_file(_text, filename_prefix="voice"):
            return "/tmp/voice_deadbeef.mp3"

        token = tool_context.set(
            {"channel": "discord", "chat_id": "42", "sender_id": "u1"}
        )
        try:
            with patch.object(
                ElevenLabsTTS, "get_api_key", return_value="k"
            ), patch.object(
                ElevenLabsTTS, "synthesize_to_file", side_effect=fake_to_file
            ):
                result = await toolbox.send_voice("hello there")
        finally:
            tool_context.reset(token)

        self.assertIn("voice message", result)
        self.assertEqual(len(sent), 1)
        self.assertEqual(sent[0].channel, "discord")
        self.assertEqual(sent[0].content, "")
        self.assertEqual(sent[0].metadata["type"], "file")
        self.assertEqual(sent[0].metadata["file_path"], "/tmp/voice_deadbeef.mp3")
        self.assertTrue(sent[0].metadata["cleanup_file"])

    async def test_sends_voice_url_on_web(self):
        from core.context import tool_context
        from core.tts import ElevenLabsTTS

        sent = []
        toolbox = self._toolbox(sent)

        async def fake_save(_text, filename_prefix="voice"):
            return "/temp/voice_cafe.mp3"

        token = tool_context.set(
            {"channel": "web", "chat_id": "dash", "sender_id": "u1"}
        )
        try:
            with patch.object(
                ElevenLabsTTS, "get_api_key", return_value="k"
            ), patch.object(
                ElevenLabsTTS, "synthesize_and_save", side_effect=fake_save
            ):
                result = await toolbox.send_voice("hello there")
        finally:
            tool_context.reset(token)

        self.assertIn("voice message", result)
        self.assertEqual(len(sent), 1)
        self.assertEqual(sent[0].channel, "web")
        self.assertEqual(sent[0].metadata["voice_url"], "/temp/voice_cafe.mp3")


class TestSynthesizeLengthCap(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        try:
            import loguru  # noqa: F401
        except Exception:
            raise unittest.SkipTest("Missing dependencies (loguru).")

    async def test_long_text_is_truncated_before_request(self):
        from core.tts import ElevenLabsTTS

        long_text = ("word " * 1000).strip()  # well over MAX_TTS_CHARS
        self.assertGreater(len(long_text), ElevenLabsTTS.MAX_TTS_CHARS)

        captured = {}

        class _FakeResponse:
            status_code = 200
            content = b"fake-audio-bytes"
            text = ""

        class _FakeAsyncClient:
            def __init__(self, *a, **k):
                pass

            async def __aenter__(self):
                return self

            async def __aexit__(self, *a):
                return False

            async def post(self, url, json=None, headers=None):
                captured["payload"] = json
                return _FakeResponse()

        with patch.object(
            ElevenLabsTTS, "get_api_key", return_value="k"
        ), patch("core.tts.httpx.AsyncClient", _FakeAsyncClient):
            result = await ElevenLabsTTS.synthesize_text(long_text)

        self.assertEqual(result, b"fake-audio-bytes")
        sent_text = captured["payload"]["text"]
        self.assertLessEqual(len(sent_text), ElevenLabsTTS.MAX_TTS_CHARS)

    def test_truncate_prefers_sentence_boundary(self):
        from core.tts import ElevenLabsTTS

        text = "First sentence. Second sentence. " + ("filler " * 50)
        out = ElevenLabsTTS._truncate_for_speech(text, 40)
        self.assertTrue(out.endswith("."))
        self.assertLessEqual(len(out), 40)

    def test_truncate_noop_under_limit(self):
        from core.tts import ElevenLabsTTS

        self.assertEqual(ElevenLabsTTS._truncate_for_speech("short", 2500), "short")


class TestSaveVoiceConfigAtomic(unittest.TestCase):
    def test_save_writes_no_leftover_tmp_and_preserves_other_keys(self):
        from core.tts import ElevenLabsTTS
        import core.tts as tts_module

        original_path = tts_module.LIMEBOT_CONFIG_PATH
        tmp_dir = Path("temp") / "test_limebot_cfg"
        tmp_dir.mkdir(parents=True, exist_ok=True)
        fake_config = tmp_dir / "limebot.json"
        fake_config.write_text(
            json.dumps({"skills": {"enabled": ["filesystem"]}}), encoding="utf-8"
        )

        tts_module.LIMEBOT_CONFIG_PATH = fake_config
        try:
            ElevenLabsTTS.save_voice_config({"enabled": True, "voice_id": "abc"})

            self.assertTrue(fake_config.exists())
            self.assertFalse(fake_config.with_suffix(".json.tmp").exists())

            data = json.loads(fake_config.read_text(encoding="utf-8"))
            self.assertEqual(data["skills"]["enabled"], ["filesystem"])
            self.assertEqual(data["voice"]["voice_id"], "abc")
        finally:
            tts_module.LIMEBOT_CONFIG_PATH = original_path
            fake_config.unlink(missing_ok=True)
            fake_config.with_suffix(".json.tmp").unlink(missing_ok=True)
            tmp_dir.rmdir()


class TestPurgeStaleAudio(unittest.TestCase):
    def test_purges_only_old_voice_files(self):
        from core.tts import ElevenLabsTTS
        import os
        import time

        tmp_dir = Path("temp")
        tmp_dir.mkdir(parents=True, exist_ok=True)
        old_file = tmp_dir / "voice_oldold01.mp3"
        new_file = tmp_dir / "voice_freshfr1.mp3"
        unrelated_file = tmp_dir / "not_voice_related.mp3"
        old_file.write_bytes(b"old")
        new_file.write_bytes(b"new")
        unrelated_file.write_bytes(b"unrelated")

        old_time = time.time() - (3 * 3600)  # 3 hours old
        os.utime(old_file, (old_time, old_time))

        try:
            deleted = ElevenLabsTTS.purge_stale_audio(max_age_hours=2.0)
            self.assertGreaterEqual(deleted, 1)
            self.assertFalse(old_file.exists())
            self.assertTrue(new_file.exists())
            self.assertTrue(unrelated_file.exists())
        finally:
            new_file.unlink(missing_ok=True)
            unrelated_file.unlink(missing_ok=True)
            old_file.unlink(missing_ok=True)

    def test_no_temp_dir_returns_zero(self):
        from core.tts import ElevenLabsTTS
        import core.tts as tts_module

        original = tts_module.TEMP_DIR
        tts_module.TEMP_DIR = Path("temp") / "does_not_exist_xyz"
        try:
            self.assertEqual(ElevenLabsTTS.purge_stale_audio(), 0)
        finally:
            tts_module.TEMP_DIR = original


class TestReaperLoopWiring(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        try:
            import loguru  # noqa: F401
        except Exception:
            raise unittest.SkipTest("Missing dependencies (loguru).")

    async def test_reaper_loop_calls_purge_then_sleeps(self):
        import asyncio

        from core.loop import AgentLoop
        from core.tts import ElevenLabsTTS

        loop = AgentLoop.__new__(AgentLoop)
        calls = {"purge": 0, "slept": []}

        def fake_purge():
            calls["purge"] += 1

        async def fake_sleep(seconds):
            calls["slept"].append(seconds)
            raise asyncio.CancelledError()

        with patch.object(
            ElevenLabsTTS, "purge_stale_audio", side_effect=fake_purge
        ), patch("core.loop.asyncio.sleep", side_effect=fake_sleep):
            with self.assertRaises(asyncio.CancelledError):
                await loop._reap_temp_voice_files_loop()

        self.assertEqual(calls["purge"], 1)
        self.assertEqual(calls["slept"], [1800])


if __name__ == "__main__":
    unittest.main()
