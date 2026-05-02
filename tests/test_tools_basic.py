import unittest
import shutil
from pathlib import Path
from types import SimpleNamespace


class TestToolsBasic(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        try:
            import loguru  # noqa: F401
        except Exception:
            raise unittest.SkipTest("Missing dependencies (loguru).")

    async def test_tool_call_read_file(self):
        from core.bus import MessageBus
        from core.loop import AgentLoop

        class _TestAgentLoop(AgentLoop):
            async def _init_skills_and_tools(self) -> None:
                self._tool_definitions = []
                self._warmed = True

        bus = MessageBus()
        agent = _TestAgentLoop(bus=bus)

        tmp_dir = Path("temp")
        tmp_dir.mkdir(exist_ok=True)
        tmp_file = tmp_dir / "tool_test.txt"
        tmp_file.write_text("hello tool", encoding="utf-8")

        try:
            result = await agent._execute_tool(
                "read_file", {"path": str(tmp_file)}, session_key="test:web"
            )
        finally:
            tmp_file.unlink(missing_ok=True)

        self.assertIn("hello tool", str(result))

    async def test_tool_call_search_files(self):
        from core.bus import MessageBus
        from core.loop import AgentLoop

        class _TestAgentLoop(AgentLoop):
            async def _init_skills_and_tools(self) -> None:
                self._tool_definitions = []
                self._warmed = True

        bus = MessageBus()
        agent = _TestAgentLoop(bus=bus)

        tmp_dir = Path("temp")
        tmp_dir.mkdir(exist_ok=True)
        tmp_file = tmp_dir / "search_tool_test.txt"
        tmp_file.write_text("alpha beta gamma", encoding="utf-8")

        try:
            by_content = await agent._execute_tool(
                "search_files",
                {"query": "beta", "path": str(tmp_dir), "mode": "content"},
                session_key="test:web",
            )
            by_name = await agent._execute_tool(
                "search_files",
                {"query": "search_tool_test", "path": str(tmp_dir), "mode": "name"},
                session_key="test:web",
            )
        finally:
            tmp_file.unlink(missing_ok=True)

        self.assertIn("search_tool_test.txt", str(by_content))
        self.assertIn("search_tool_test.txt", str(by_name))

    async def test_tool_call_read_file_line_range(self):
        from core.bus import MessageBus
        from core.loop import AgentLoop

        class _TestAgentLoop(AgentLoop):
            async def _init_skills_and_tools(self) -> None:
                self._tool_definitions = []
                self._warmed = True

        bus = MessageBus()
        agent = _TestAgentLoop(bus=bus)

        tmp_dir = Path("temp")
        tmp_dir.mkdir(exist_ok=True)
        tmp_file = tmp_dir / "tool_range_test.txt"
        tmp_file.write_text("line1\nline2\nline3\nline4\n", encoding="utf-8")

        try:
            result = await agent._execute_tool(
                "read_file",
                {"path": str(tmp_file), "start_line": 2, "end_line": 3},
                session_key="test:web",
            )
        finally:
            tmp_file.unlink(missing_ok=True)

        self.assertIn("line2", str(result))
        self.assertIn("line3", str(result))
        self.assertNotIn("line1", str(result))
        self.assertNotIn("line4", str(result))

    async def test_tool_call_list_dir_pagination(self):
        from core.bus import MessageBus
        from core.loop import AgentLoop

        class _TestAgentLoop(AgentLoop):
            async def _init_skills_and_tools(self) -> None:
                self._tool_definitions = []
                self._warmed = True

        bus = MessageBus()
        agent = _TestAgentLoop(bus=bus)

        tmp_dir = Path("temp") / "list_dir_test"
        tmp_dir.mkdir(parents=True, exist_ok=True)
        created = []
        for i in range(5):
            f = tmp_dir / f"item_{i}.txt"
            f.write_text(f"file {i}", encoding="utf-8")
            created.append(f)

        try:
            result = await agent._execute_tool(
                "list_dir",
                {"path": str(tmp_dir), "limit": 2, "offset": 1, "sort_by": "name"},
                session_key="test:web",
            )
        finally:
            for f in created:
                f.unlink(missing_ok=True)
            shutil.rmtree(tmp_dir, ignore_errors=True)

        # Expect a paged response and exactly two file rows in the selected window.
        file_rows = [ln for ln in str(result).splitlines() if ln.startswith("[FILE]")]
        self.assertEqual(len(file_rows), 2)

    async def test_tool_call_read_file_docx(self):
        try:
            from docx import Document
        except Exception:
            raise unittest.SkipTest("Missing dependencies (python-docx).")

        from core.bus import MessageBus
        from core.loop import AgentLoop

        class _TestAgentLoop(AgentLoop):
            async def _init_skills_and_tools(self) -> None:
                self._tool_definitions = []
                self._warmed = True

        bus = MessageBus()
        agent = _TestAgentLoop(bus=bus)

        tmp_dir = Path("temp")
        tmp_dir.mkdir(exist_ok=True)
        tmp_file = tmp_dir / "tool_docx_test.docx"

        doc = Document()
        doc.add_paragraph("Hello from DOCX reader")
        doc.save(tmp_file)

        try:
            result = await agent._execute_tool(
                "read_file", {"path": str(tmp_file)}, session_key="test:web"
            )
        finally:
            tmp_file.unlink(missing_ok=True)

        self.assertIn("Hello from DOCX reader", str(result))

    async def test_tool_call_read_file_pdf(self):
        try:
            from pypdf import PdfWriter
        except Exception:
            raise unittest.SkipTest("Missing dependencies (pypdf).")

        from core.bus import MessageBus
        from core.loop import AgentLoop

        class _TestAgentLoop(AgentLoop):
            async def _init_skills_and_tools(self) -> None:
                self._tool_definitions = []
                self._warmed = True

        bus = MessageBus()
        agent = _TestAgentLoop(bus=bus)

        tmp_dir = Path("temp")
        tmp_dir.mkdir(exist_ok=True)
        tmp_file = tmp_dir / "tool_pdf_test.pdf"

        writer = PdfWriter()
        writer.add_blank_page(width=300, height=300)
        with tmp_file.open("wb") as f:
            writer.write(f)

        try:
            result = await agent._execute_tool(
                "read_file", {"path": str(tmp_file)}, session_key="test:web"
            )
        finally:
            tmp_file.unlink(missing_ok=True)

        self.assertIn("No extractable text found in PDF.", str(result))

    async def test_tool_call_send_media_uses_current_chat_context(self):
        from core.bus import MessageBus
        from core.context import tool_context
        from core.tools import Toolbox

        config = SimpleNamespace(skills=SimpleNamespace(enabled=[]))
        bus = MessageBus()
        sent = []

        async def _capture(msg):
            sent.append(msg)

        bus.publish_outbound = _capture
        toolbox = Toolbox(allowed_paths=[str(Path.cwd())], bus=bus, config=config)

        tmp_dir = Path("temp")
        tmp_dir.mkdir(exist_ok=True)
        tmp_file = tmp_dir / "send_media_test.txt"
        tmp_file.write_text("share me", encoding="utf-8")

        token = tool_context.set(
            {
                "channel": "discord",
                "chat_id": "12345",
                "sender_id": "user-1",
            }
        )
        try:
            result = await toolbox.send_media(str(tmp_file), "Here you go")
        finally:
            tool_context.reset(token)
            tmp_file.unlink(missing_ok=True)

        self.assertIn("Sent 'temp", result)
        self.assertEqual(len(sent), 1)
        outbound = sent[0]
        self.assertEqual(outbound.channel, "discord")
        self.assertEqual(outbound.chat_id, "12345")
        self.assertEqual(outbound.metadata["type"], "file")
        self.assertEqual(outbound.metadata["caption"], "Here you go")

    async def test_tool_call_send_media_blocks_persona_files(self):
        from core.bus import MessageBus
        from core.context import tool_context
        from core.tools import Toolbox

        config = SimpleNamespace(skills=SimpleNamespace(enabled=[]))
        bus = MessageBus()
        sent = []

        async def _capture(msg):
            sent.append(msg)

        bus.publish_outbound = _capture
        toolbox = Toolbox(allowed_paths=[str(Path.cwd())], bus=bus, config=config)

        persona_dir = Path("persona")
        persona_dir.mkdir(exist_ok=True)
        tmp_file = persona_dir / "blocked_share.txt"
        tmp_file.write_text("nope", encoding="utf-8")

        token = tool_context.set(
            {
                "channel": "whatsapp",
                "chat_id": "123@s.whatsapp.net",
                "sender_id": "user-1",
            }
        )
        try:
            result = await toolbox.send_media(str(tmp_file))
        finally:
            tool_context.reset(token)
            tmp_file.unlink(missing_ok=True)

        self.assertIn("Blocked files inside the persona directory", result)
        self.assertEqual(sent, [])

    async def test_tool_call_send_discord_embed_uses_current_chat(self):
        from core.bus import MessageBus
        from core.context import tool_context
        from core.tools import Toolbox

        config = SimpleNamespace(skills=SimpleNamespace(enabled=[]))
        bus = MessageBus()
        sent = []

        async def _capture(msg):
            sent.append(msg)

        bus.publish_outbound = _capture
        toolbox = Toolbox(allowed_paths=[str(Path.cwd())], bus=bus, config=config)

        token = tool_context.set(
            {
                "channel": "discord",
                "chat_id": "777",
                "sender_id": "user-1",
            }
        )
        try:
            result = await toolbox.send_discord_embed(
                title="Build",
                description="Passed",
                color="#00FF00",
                fields=[{"name": "Status", "value": "Green", "inline": True}],
            )
        finally:
            tool_context.reset(token)

        self.assertEqual(result, "Sent native Discord embed to channel 777.")
        self.assertEqual(len(sent), 1)
        outbound = sent[0]
        self.assertEqual(outbound.channel, "discord")
        self.assertEqual(outbound.chat_id, "777")
        self.assertEqual(outbound.metadata["embed"]["title"], "Build")
        self.assertEqual(outbound.metadata["embed"]["fields"][0]["name"], "Status")

    async def test_tool_call_list_discord_channels_reads_live_client(self):
        from core.bus import MessageBus
        from core.tools import Toolbox

        class _DummyTextChannel:
            def __init__(self, cid: str, name: str):
                self.id = int(cid)
                self.name = name
                self.type = "text"

        class _DummyVoiceChannel:
            def __init__(self, cid: str, name: str):
                self.id = int(cid)
                self.name = name
                self.type = "voice"

        class _DummyGuild:
            def __init__(self):
                self.id = 1
                self.name = "Main"
                self.channels = [
                    _DummyTextChannel("11", "general"),
                    _DummyVoiceChannel("12", "voice"),
                ]

        discord_channel = SimpleNamespace(
            name="discord",
            client=SimpleNamespace(
                is_ready=lambda: True,
                guilds=[_DummyGuild()],
            ),
        )

        config = SimpleNamespace(skills=SimpleNamespace(enabled=[]))
        toolbox = Toolbox(allowed_paths=[str(Path.cwd())], bus=MessageBus(), config=config)
        toolbox.set_channels([discord_channel])

        result = await toolbox.list_discord_channels()

        self.assertIn('"name": "Main"', result)
        self.assertIn('"name": "general"', result)
        self.assertNotIn('"name": "voice"', result)
