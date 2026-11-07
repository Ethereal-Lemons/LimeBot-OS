import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch


ROSE_REQUEST = (
    "download a picture of rose of blackpink for me and send me that in this chat"
)
JENNIE_ELLE_REQUEST = "traeme una foto de jennie de elle korea"
class TestChatMediaPromptAndTools(unittest.TestCase):
    def test_stable_prompt_leaves_tool_choice_to_the_model(self):
        from core.prompt import build_stable_system_prompt

        prompt = build_stable_system_prompt(
            sender_id="owner",
            channel="web",
            chat_id="chat",
            model="test-model",
            allowed_paths=[],
            skill_registry=None,
            config=SimpleNamespace(
                llm=SimpleNamespace(enable_dynamic_personality=False),
                personality_whitelist=["owner"],
            ),
            soul=(
                "Core values matter. Truth, boundaries, and personality are important. "
                "This soul description is long enough to pass validation and explain who I am."
            ),
            identity_raw=(
                "# IDENTITY.md - Who I Am\n\n"
                "*   **Name:** LimeBot\n"
                "*   **Emoji:** 🍋\n"
                "*   **Style:** Clear and direct\n"
            ),
            sender_name="Owner",
        )

        self.assertIn("Choose tools by matching their descriptions", prompt)
        self.assertNotIn("MEDIA DELIVERY", prompt)
        self.assertNotIn("Do not call `send_media`", prompt)
        self.assertIn("Do not read or dump AGENTS.md", prompt)
        self.assertNotIn("Core Module Reference", prompt)
        self.assertNotIn("DurableJobQueue", prompt)

    def test_tool_descriptions_and_schema_explain_media_outcomes(self):
        from core.tool_defs import build_tool_definitions

        tools = build_tool_definitions(enabled_skills=[])
        by_name = {
            tool["function"]["name"]: tool["function"] for tool in tools
        }
        self.assertIn("web_search", by_name)
        self.assertIn("generate_image", by_name)
        self.assertIn("send_media", by_name)
        self.assertNotIn("image_search", by_name)
        self.assertNotIn("google_search", by_name)
        self.assertNotIn("capability_search", by_name)
        self.assertNotIn("deep_research", by_name)

        properties = by_name["web_search"]["parameters"]["properties"]
        self.assertEqual(properties["kind"]["enum"], ["web", "news", "images"])
        self.assertEqual(properties["attach_to_chat"]["type"], "boolean")
        self.assertIn("existing photos", by_name["web_search"]["description"])
        self.assertIn("Do NOT use this to download", by_name["generate_image"]["description"])
        self.assertIn("photo into the current chat", by_name["spawn_agent"]["description"])

    def test_search_tools_are_registered_without_browser_or_keys(self):
        from core.tool_defs import build_tool_definitions

        tools = build_tool_definitions(enabled_skills=[], search_available=False)
        names = {tool["function"]["name"] for tool in tools}
        self.assertNotIn("image_search", names)
        self.assertIn("web_search", names)
        self.assertIn("browser_navigate", names)
        self.assertIn("browser_act", names)
        self.assertIn("browser_extract", names)


class TestChatMediaPromptBloat(unittest.IsolatedAsyncioTestCase):
    async def test_live_prompt_keeps_capabilities_skills_and_subagents_available(self):
        from core.loop import AgentLoop
        from unittest.mock import AsyncMock

        agent = object.__new__(AgentLoop)
        agent._get_stable_prompt = AsyncMock(return_value="STABLE")
        agent.config = SimpleNamespace(
            llm=SimpleNamespace(enable_dynamic_personality=False),
            personality_whitelist=["owner"],
        )
        agent._get_capability_turn_context = lambda *_args, **_kwargs: {
            "routing_text": ROSE_REQUEST
        }
        agent.skill_registry = SimpleNamespace(
            get_forced_prompt_addition=lambda _name: "FORCED_SKILL",
            get_relevant_prompt_additions=lambda _text: "AVAILABLE_SKILL_MANUAL",
        )
        agent.subagent_registry = SimpleNamespace(
            get_prompt_additions=lambda _text: "AVAILABLE_SUBAGENT_LIST"
        )
        agent._capability_catalog_prompt = lambda: "AVAILABLE_CAPABILITIES"

        with patch(
            "core.loop.prompt_module.is_setup_complete",
            return_value=True,
        ), patch(
            "core.loop.prompt_module.should_load_private_context",
            return_value=False,
        ), patch(
            "core.loop.prompt_module.get_volatile_prompt_suffix",
            return_value="\n--- CONTEXT & MEMORY ---\n",
        ):
            prompt = await agent._build_full_system_prompt(
                sender_id="owner",
                channel="web",
                chat_id="chat",
                current_message=ROSE_REQUEST,
            )

        self.assertIn("AVAILABLE_SKILL_MANUAL", prompt)
        self.assertIn("AVAILABLE_SUBAGENT_LIST", prompt)
        self.assertIn("AVAILABLE_CAPABILITIES", prompt)
        self.assertNotIn("Core Module Reference", prompt)


class TestSendMediaWebEnvelope(unittest.IsolatedAsyncioTestCase):
    async def test_web_send_media_stamps_turn_ids_and_image_envelope(self):
        from pathlib import Path
        from types import SimpleNamespace

        from core.bus import MessageBus
        from core.context import tool_context
        from core.tools import Toolbox

        sent = []
        bus = MessageBus()

        async def _capture(msg):
            sent.append(msg)

        bus.publish_outbound = _capture
        toolbox = Toolbox(
            allowed_paths=[str(Path.cwd())],
            bus=bus,
            config=SimpleNamespace(skills=SimpleNamespace(enabled=[])),
        )

        tmp_dir = Path("temp")
        tmp_dir.mkdir(exist_ok=True)
        tmp_file = tmp_dir / "rose_envelope.png"
        tmp_file.write_bytes(b"\x89PNG\r\n\x1a\n" + b"0" * 32)

        token = tool_context.set(
            {
                "channel": "web",
                "chat_id": "dash",
                "sender_id": "u1",
                "turn_id": "turn_media",
                "message_id": "msg_media",
            }
        )
        try:
            result = await toolbox.send_media(str(tmp_file), "Rosé")
        finally:
            tool_context.reset(token)
            tmp_file.unlink(missing_ok=True)

        self.assertIn("Displayed", result)
        self.assertEqual(len(sent), 1)
        meta = sent[0].metadata
        self.assertEqual(meta["turn_id"], "turn_media")
        self.assertEqual(meta["message_id"], "msg_media")
        self.assertEqual(meta["image"], meta["attachments"][0]["url"])
        self.assertEqual(meta["attachments"][0]["kind"], "image")
        self.assertTrue(meta["attachments"][0]["url"].startswith("/temp/"))
        self.assertEqual(sent[0].content, "Rosé")


class TestHostOwnedPhotoAttach(unittest.IsolatedAsyncioTestCase):
    async def test_model_selected_image_attach_works_for_any_followup_text(self):
        from core.browser_search_service import BrowserSearchService, BrowserSearchToolAdapter
        from core.web_search import ImageResult, SearchResponse

        response = SearchResponse(kind="images", query="Jennie", provider="host")
        response.images = [
            ImageResult(
                title="Jennie",
                image_url="https://cdn.example.test/jennie.jpg",
            )
        ]
        attach = AsyncMock(return_value="Displayed in the web chat.")
        service = BrowserSearchService(
            manager_provider=AsyncMock(),
            report_progress=AsyncMock(),
            attach_chat_image=attach,
        )
        service._gather_search = AsyncMock(return_value=(response, ""))
        adapter = BrowserSearchToolAdapter(service)

        await adapter.search_tool(
            "web_search",
            {
                "query": "Jennie",
                "kind": "images",
                "attach_to_chat": True,
            },
            session_key="web_chat",
            user_text="can't see it",
        )

        self.assertTrue(response.attached)
        attach.assert_awaited_once_with(
            "https://cdn.example.test/jennie.jpg", "Jennie"
        )

    async def test_model_can_request_image_results_without_chat_attachment(self):
        from core.browser_search_service import BrowserSearchService, BrowserSearchToolAdapter
        from core.web_search import ImageResult, SearchResponse

        response = SearchResponse(kind="images", query="Jennie", provider="host")
        response.images = [
            ImageResult(
                title="Jennie",
                image_url="https://cdn.example.test/jennie.jpg",
            )
        ]
        attach = AsyncMock(return_value="Displayed in the web chat.")
        service = BrowserSearchService(
            manager_provider=AsyncMock(),
            report_progress=AsyncMock(),
            attach_chat_image=attach,
        )
        service._gather_search = AsyncMock(return_value=(response, ""))
        adapter = BrowserSearchToolAdapter(service)

        await adapter.search_tool(
            "web_search",
            {
                "query": "Jennie",
                "kind": "images",
                "attach_to_chat": False,
            },
            session_key="web_chat",
            user_text="anything at all",
        )

        self.assertFalse(response.attached)
        attach.assert_not_awaited()

    async def test_host_attach_stamps_turn_ids_and_image_envelope(self):
        from pathlib import Path
        from types import SimpleNamespace

        from core.bus import MessageBus
        from core.context import tool_context
        from core.browser_search_service import BrowserSearchService
        from core.loop import AgentLoop
        from core.tools import Toolbox
        from core.web_search import ImageResult, SearchResponse

        sent = []
        bus = MessageBus()

        async def _capture(msg):
            sent.append(msg)

        bus.publish_outbound = _capture
        toolbox = Toolbox(
            allowed_paths=[str(Path.cwd())],
            bus=bus,
            config=SimpleNamespace(skills=SimpleNamespace(enabled=[])),
        )
        loop = AgentLoop.__new__(AgentLoop)
        loop.toolbox = toolbox
        loop.browser_search_service = BrowserSearchService(
            manager_provider=AsyncMock(),
            report_progress=AsyncMock(),
            attach_chat_image=toolbox.attach_chat_image,
        )

        tmp_dir = Path("temp")
        tmp_dir.mkdir(exist_ok=True)
        tmp_file = tmp_dir / "host_rose.png"
        tmp_file.write_bytes(b"\x89PNG\r\n\x1a\n" + b"0" * 32)

        async def fake_fetch(url, max_bytes=None):
            self.assertEqual(url, "https://cdn.example.test/rose.jpg")
            return str(tmp_file)

        toolbox.fetch_url_to_temp = fake_fetch
        response = SearchResponse(kind="images", query="rose blackpink", provider="host")
        response.images = [
            ImageResult(
                title="Rosé",
                image_url="https://cdn.example.test/rose.jpg",
                source_page="https://wiki.test/rose",
            )
        ]
        token = tool_context.set(
            {
                "channel": "web",
                "chat_id": "dash",
                "sender_id": "u1",
                "turn_id": "turn_host",
                "message_id": "msg_host",
                "user_text": ROSE_REQUEST,
            }
        )
        try:
            await loop.browser_search_service._maybe_attach_search_image(
                response, "rose blackpink", attach_to_chat=True
            )
        finally:
            tool_context.reset(token)
            tmp_file.unlink(missing_ok=True)

        self.assertTrue(response.attached)
        self.assertEqual(len(sent), 1)
        meta = sent[0].metadata
        self.assertEqual(meta["turn_id"], "turn_host")
        self.assertEqual(meta["message_id"], "msg_host")
        self.assertEqual(meta["image"], meta["attachments"][0]["url"])
        self.assertEqual(meta["attachments"][0]["kind"], "image")
        self.assertTrue(meta["attachments"][0]["url"].startswith("/temp/"))
        self.assertTrue(toolbox.media_delivered_this_turn("turn_host"))

    async def test_discord_host_attach_sends_file_without_wrapup(self):
        from pathlib import Path
        from types import SimpleNamespace

        from core.bus import MessageBus
        from core.context import tool_context
        from core.browser_search_service import BrowserSearchService
        from core.loop import AgentLoop
        from core.tools import Toolbox
        from core.web_search import ImageResult, SearchResponse

        sent = []
        bus = MessageBus()

        async def _capture(msg):
            sent.append(msg)

        bus.publish_outbound = _capture
        toolbox = Toolbox(
            allowed_paths=[str(Path.cwd())],
            bus=bus,
            config=SimpleNamespace(skills=SimpleNamespace(enabled=[])),
        )
        loop = AgentLoop.__new__(AgentLoop)
        loop.toolbox = toolbox
        loop.browser_search_service = BrowserSearchService(
            manager_provider=AsyncMock(),
            report_progress=AsyncMock(),
            attach_chat_image=toolbox.attach_chat_image,
        )

        tmp_dir = Path("temp")
        tmp_dir.mkdir(exist_ok=True)
        tmp_file = tmp_dir / "host_jennie.jpg"
        tmp_file.write_bytes(b"\xff\xd8\xff\xe0jpegdata")

        async def fake_fetch(url, max_bytes=None):
            self.assertEqual(url, "https://cdn.example.test/jennie-elle.jpg")
            return str(tmp_file)

        toolbox.fetch_url_to_temp = fake_fetch
        response = SearchResponse(kind="images", query="Jennie ELLE Korea", provider="host")
        response.images = [
            ImageResult(
                title="JENNIE for ELLE Korea",
                image_url="https://cdn.example.test/jennie-elle.jpg",
                source_page="https://elle.test/jennie",
            )
        ]
        token = tool_context.set(
            {
                "channel": "discord",
                "chat_id": "42",
                "sender_id": "u1",
                "turn_id": "turn_discord_photo",
                "message_id": "msg_discord_photo",
                "user_text": JENNIE_ELLE_REQUEST,
            }
        )
        try:
            await loop.browser_search_service._maybe_attach_search_image(
                response, "Jennie ELLE Korea", attach_to_chat=True
            )
        finally:
            tool_context.reset(token)
            tmp_file.unlink(missing_ok=True)

        self.assertTrue(response.attached)
        self.assertEqual(len(sent), 1)
        self.assertEqual(sent[0].channel, "discord")
        self.assertEqual(sent[0].metadata["type"], "file")
        self.assertTrue(str(sent[0].metadata["file_path"]).endswith("host_jennie.jpg"))
        self.assertEqual(sent[0].content, "")
        self.assertTrue(toolbox.media_delivered_this_turn("turn_discord_photo"))
        self.assertTrue(loop._turn_already_delivered_media("turn_discord_photo"))
        self.assertNotIn("natural-language wrap-up", sent[0].content)

    def test_browser_surface_is_three_tools(self):
        from core.tool_defs import build_tool_definitions

        for skills in ([], ["browser"]):
            names = {
                tool["function"]["name"]
                for tool in build_tool_definitions(enabled_skills=skills)
            }
            browser_names = {name for name in names if name.startswith("browser_")}
            self.assertEqual(
                browser_names,
                {"browser_navigate", "browser_act", "browser_extract"},
            )



if __name__ == "__main__":
    unittest.main()
