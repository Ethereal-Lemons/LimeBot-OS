import asyncio
import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from core.events import InboundMessage
from core.paths import PERSONA_DIR, PersonaPaths, get_persona_paths
from core.profile_context import activate_profile
from core.profiles import (
    AgentProfile,
    AgentProfilePolicy,
    ProfileError,
    ProfileRegistry,
    profile_allows_tool,
)


class TestProfileRegistry(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.registry_path = Path(self.temp_dir.name) / "data" / "profiles.json"

    def tearDown(self):
        self.temp_dir.cleanup()

    def test_empty_install_migrates_default_without_changing_legacy_locations(self):
        registry = ProfileRegistry(
            self.registry_path, configured_model="gemini/gemini-3.6-flash"
        )

        default = registry.get_profile()
        self.assertEqual(default.profile_id, "default")
        self.assertIsNone(default.model)
        self.assertEqual(registry.resolve_model(), "gemini/gemini-3.6-flash")
        self.assertEqual(get_persona_paths("default").root, PERSONA_DIR)

        legacy_message = InboundMessage(
            channel="web",
            sender_id="owner",
            chat_id="chat-a",
            content="hello",
        )
        self.assertEqual(legacy_message.session_key, "web_chat-a")

    def test_profile_store_startup_is_idempotent_and_preserves_existing_default(self):
        first = ProfileRegistry(
            self.registry_path, configured_model="model-from-config"
        )
        first.update_profile("default", display_name="My LimeBot")
        saved = self.registry_path.read_bytes()

        second = ProfileRegistry(
            self.registry_path, configured_model="new-config-model"
        )

        self.assertEqual(self.registry_path.read_bytes(), saved)
        self.assertEqual(second.get_profile().display_name, "My LimeBot")
        self.assertEqual(second.resolve_model(), "new-config-model")

    def test_legacy_registry_without_default_migrates_once(self):
        self.registry_path.parent.mkdir(parents=True)
        self.registry_path.write_text(
            json.dumps(
                {
                    "version": 1,
                    "profiles": [
                        {
                            "profile_id": "research",
                            "display_name": "Research",
                            "model": "openai/gpt-5",
                            "policy": None,
                        }
                    ],
                }
            ),
            encoding="utf-8",
        )

        registry = ProfileRegistry(self.registry_path, configured_model="base-model")
        migrated = self.registry_path.read_bytes()
        reloaded = ProfileRegistry(self.registry_path, configured_model="base-model")

        self.assertEqual(
            [profile.profile_id for profile in registry.list_profiles()],
            ["default", "research"],
        )
        self.assertEqual(reloaded.resolve_model("research"), "openai/gpt-5")
        self.assertEqual(self.registry_path.read_bytes(), migrated)

    def test_archive_is_reversible_and_default_is_protected(self):
        registry = ProfileRegistry(self.registry_path)
        registry.create_profile("Research Team")

        with self.assertRaises(ProfileError):
            registry.archive_profile("default")

        archived = registry.archive_profile("research-team")
        self.assertTrue(archived.archived)
        with self.assertRaises(ProfileError):
            registry.get_profile("research-team")
        self.assertTrue(
            registry.get_profile("research-team", include_archived=True).archived
        )
        self.assertFalse(registry.unarchive_profile("research-team").archived)

    def test_profile_ids_namespace_sessions_and_persona_paths(self):
        registry = ProfileRegistry(self.registry_path)
        registry.create_profile("Research Team")
        registry.create_profile("Writing")

        def message(profile_id):
            return InboundMessage(
                channel="web",
                sender_id="owner",
                chat_id="same-chat",
                content="hello",
                metadata={"profile_id": profile_id},
            )

        research_key = message("research-team").session_key
        writing_key = message("writing").session_key
        self.assertEqual(message("default").session_key, "web_same-chat")
        self.assertNotEqual(research_key, writing_key)
        self.assertEqual(research_key, "profile__research-team__web_same-chat")
        self.assertEqual(
            get_persona_paths("research-team").root,
            PERSONA_DIR / "profiles" / "research-team",
        )
        with self.assertRaises(ValueError):
            get_persona_paths("../outside")

    def test_invalid_registry_data_fails_closed(self):
        self.registry_path.parent.mkdir(parents=True)
        self.registry_path.write_text(
            json.dumps(
                {
                    "version": 1,
                    "profiles": [
                        {
                            "profile_id": "../escape",
                            "display_name": "Escape",
                        }
                    ],
                }
            ),
            encoding="utf-8",
        )
        with self.assertRaises(ProfileError):
            ProfileRegistry(self.registry_path)

    def test_unsupported_registry_version_fails_closed(self):
        self.registry_path.parent.mkdir(parents=True)
        self.registry_path.write_text(
            json.dumps({"version": 99, "profiles": []}), encoding="utf-8"
        )

        with self.assertRaisesRegex(ProfileError, "unsupported profile registry version"):
            ProfileRegistry(self.registry_path)

    def test_shared_workspace_tools_require_named_profile_opt_in(self):
        named_default = AgentProfile("writer", "Writer")
        explicit_shell = AgentProfile(
            "writer",
            "Writer",
            policy=AgentProfilePolicy(allowed_tools=("read_file", "run_command")),
        )
        restricted_paths = AgentProfile(
            "writer",
            "Writer",
            policy=AgentProfilePolicy(
                allowed_tools=("run_command",), allowed_paths=("./notes",)
            ),
        )
        legacy_default = AgentProfile("default", "LimeBot")

        self.assertFalse(profile_allows_tool(named_default, "run_command"))
        self.assertFalse(profile_allows_tool(named_default, "spawn_agent"))
        self.assertFalse(
            profile_allows_tool(named_default, "apply_workspace_changeset")
        )
        self.assertTrue(profile_allows_tool(explicit_shell, "run_command"))
        self.assertFalse(profile_allows_tool(explicit_shell, "run_steps"))
        self.assertFalse(profile_allows_tool(restricted_paths, "run_command"))
        self.assertTrue(profile_allows_tool(legacy_default, "run_command"))
        explicit_merge = AgentProfile(
            "writer",
            "Writer",
            policy=AgentProfilePolicy(
                allowed_tools=("spawn_agent", "apply_workspace_changeset")
            ),
        )
        self.assertTrue(
            profile_allows_tool(explicit_merge, "apply_workspace_changeset")
        )

    def test_app_pending_approval_keeps_profile_attribution(self):
        from channels.web import _serialize_pending_approval_for_app

        serialized = _serialize_pending_approval_for_app(
            "conf-1",
            {
                "tool": "write_file",
                "session_key": "profile__writer__web_chat",
                "profile_id": "writer",
                "profile_name": "Writer",
                "policy_profile": "manual",
                "preview": {"kind": "write_file", "summary": "Write notes"},
            },
        )

        self.assertEqual(serialized["profile_id"], "writer")
        self.assertEqual(serialized["profile_name"], "Writer")


class TestProfileRuntimeIsolation(unittest.IsolatedAsyncioTestCase):
    async def test_unapproved_shared_workspace_tool_is_refused_before_execution(self):
        from core.bus import MessageBus
        from core.loop import AgentLoop

        class _TestAgentLoop(AgentLoop):
            async def _init_skills_and_tools(self) -> None:
                self._tool_definitions = []
                self._warmed = True

        agent = _TestAgentLoop(bus=MessageBus())
        profile = AgentProfile("writer", "Writer")

        with activate_profile(profile):
            result = await agent._execute_tool(
                "run_command", {"command": "whoami"}, "profile__writer__web_chat"
            )
            merge_result = await agent._execute_tool(
                "apply_workspace_changeset",
                {"workspace_id": "other-session-workspace"},
                "profile__writer__web_chat",
            )

        self.assertIn("disabled for profile 'writer'", result)
        self.assertIn("disabled for profile 'writer'", merge_result)

    async def test_selected_subagent_routing_requires_profile_opt_in(self):
        from core.loop import AgentLoop

        agent = AgentLoop.__new__(AgentLoop)
        agent.subagent_registry = SimpleNamespace(
            get_default_selection=lambda: "reviewer"
        )

        with activate_profile(AgentProfile("writer", "Writer")):
            self.assertEqual(agent._selected_subagent_for_turn(), "auto")

        opted_in = AgentProfile(
            "writer",
            "Writer",
            policy=AgentProfilePolicy(allowed_tools=("spawn_agent",)),
        )
        with activate_profile(opted_in):
            self.assertEqual(agent._selected_subagent_for_turn(), "reviewer")

    async def test_subagent_runner_receives_active_profile_persona_paths(self):
        from core.loop import AgentLoop

        with tempfile.TemporaryDirectory() as temporary:
            persona_root = Path(temporary) / "persona"
            paths = {
                "default": PersonaPaths(persona_root),
                "writer": PersonaPaths(persona_root / "profiles" / "writer"),
            }
            agent = AgentLoop.__new__(AgentLoop)
            agent.model = "provider/default-model"
            agent.subagent_registry = object()
            agent.session_manager = object()
            agent.bus = object()

            with patch(
                "core.profile_context.get_persona_paths",
                side_effect=lambda profile_id: paths[profile_id],
            ):
                with activate_profile(AgentProfile("writer", "Writer")):
                    with patch(
                        "core.loop.run_subagent", new_callable=AsyncMock
                    ) as run:
                        run.return_value = "done"
                        result = await agent.run_subagent(
                            "profile__writer__web_chat",
                            "profile__writer__web_chat_sub_test",
                            "review this task",
                        )

            self.assertEqual(result, "done")
            self.assertEqual(
                run.await_args.kwargs["soul_file"], paths["writer"].soul
            )
            self.assertEqual(
                run.await_args.kwargs["identity_file"], paths["writer"].identity
            )

    async def test_injected_user_note_marker_uses_active_profile_paths(self):
        from core.loop import AgentLoop

        with tempfile.TemporaryDirectory() as temporary:
            persona_root = Path(temporary) / "persona"
            paths = {
                "default": PersonaPaths(persona_root),
                "writer": PersonaPaths(persona_root / "profiles" / "writer"),
            }
            paths["default"].users.mkdir(parents=True)
            (paths["default"].users / "sender-1.md").write_text(
                "default-only note", encoding="utf-8"
            )
            paths["writer"].users.mkdir(parents=True)

            with (
                patch("core.loop.USERS_DIR", paths["default"].users),
                patch(
                    "core.profile_context.get_persona_paths",
                    side_effect=lambda profile_id: paths[profile_id],
                ),
            ):
                with activate_profile(AgentProfile("writer", "Writer")):
                    writer_files = AgentLoop._build_session_injected_files("sender-1", [])
                with activate_profile(AgentProfile("default", "LimeBot")):
                    default_files = AgentLoop._build_session_injected_files(
                        "sender-1", []
                    )

            self.assertNotIn("users/sender-1.md", writer_files)
            self.assertIn("users/sender-1.md", default_files)

    async def test_profile_file_tools_cannot_cross_persona_boundaries(self):
        from core.tools import Toolbox

        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        root = Path(temporary.name).resolve()
        persona_root = root / "persona"
        writer_paths = PersonaPaths(persona_root / "profiles" / "writer")
        researcher_paths = PersonaPaths(persona_root / "profiles" / "researcher")
        legacy_paths = PersonaPaths(persona_root)
        writer_paths.long_term_memory.parent.mkdir(parents=True, exist_ok=True)
        writer_paths.long_term_memory.write_text("writer-private", encoding="utf-8")
        researcher_paths.long_term_memory.parent.mkdir(parents=True, exist_ok=True)
        researcher_paths.long_term_memory.write_text("researcher-private", encoding="utf-8")
        legacy_paths.long_term_memory.write_text("legacy-private", encoding="utf-8")
        toolbox = object.__new__(Toolbox)
        toolbox.allowed_paths = [root]
        toolbox.blocked_files = {".git", "__pycache__", "node_modules"}

        paths = {
            "default": legacy_paths,
            "writer": writer_paths,
            "researcher": researcher_paths,
        }
        with (
            patch("core.tools.PERSONA_DIR", persona_root),
            patch(
                "core.profile_context.get_persona_paths",
                side_effect=lambda profile_id: paths[profile_id],
            ),
        ):
            with activate_profile(AgentProfile("writer", "Writer")):
                self.assertTrue(
                    toolbox._is_path_allowed(writer_paths.long_term_memory)
                )
                self.assertFalse(
                    toolbox._is_path_allowed(researcher_paths.long_term_memory)
                )
                self.assertFalse(
                    toolbox._is_path_allowed(legacy_paths.long_term_memory)
                )
                listing = await toolbox.list_dir(str(root))
                search = await toolbox.search_files(
                    "researcher-private", path=str(root), mode="content"
                )
                self.assertNotIn("persona", listing)
                self.assertEqual(json.loads(search)["matches"], [])

            with activate_profile(AgentProfile("default", "LimeBot")):
                self.assertTrue(toolbox._is_path_allowed(legacy_paths.long_term_memory))
                self.assertFalse(
                    toolbox._is_path_allowed(researcher_paths.long_term_memory)
                )

            self.assertFalse(
                toolbox._is_path_allowed(researcher_paths.long_term_memory)
            )

    async def test_concurrent_turns_read_profile_models_without_mutating_loop(self):
        from core.loop import AgentLoop

        agent = AgentLoop.__new__(AgentLoop)
        agent.model = "configured-model"
        profile_a = AgentProfile("writer", "Writer", model="model-a")
        profile_b = AgentProfile("researcher", "Researcher", model="model-b")

        async def observe(profile):
            with activate_profile(profile, default_model=agent.model):
                before = agent.model
                cache_scope = agent._tool_cache_scope_args(
                    {"query": "private note"}
                )
                await asyncio.sleep(0)
                after = agent.model
                return before, after, cache_scope

        results = await asyncio.gather(observe(profile_a), observe(profile_b))

        self.assertEqual(
            results,
            [
                (
                    "model-a",
                    "model-a",
                    {
                        "query": "private note",
                        "__limebot_profile_scope__": "writer",
                    },
                ),
                (
                    "model-b",
                    "model-b",
                    {
                        "query": "private note",
                        "__limebot_profile_scope__": "researcher",
                    },
                ),
            ],
        )
        self.assertEqual(agent.model, "configured-model")
        with activate_profile(profile_a):
            trace_metadata = AgentLoop._with_trace_metadata()
            self.assertEqual(trace_metadata["profile_id"], "writer")
            self.assertEqual(trace_metadata["profile_name"], "Writer")

    async def test_profiles_write_and_search_only_their_own_markdown_memory(self):
        from core.tools import Toolbox
        from core.vectors import VectorService

        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        root = Path(temporary.name)
        roots = {
            "writer": root / "writer",
            "researcher": root / "researcher",
        }
        paths = {profile_id: PersonaPaths(path) for profile_id, path in roots.items()}
        profiles = {
            "writer": AgentProfile("writer", "Writer"),
            "researcher": AgentProfile("researcher", "Researcher"),
        }
        toolbox = object.__new__(Toolbox)
        toolbox.vector_service = None
        toolbox.config = None

        with (
            patch(
                "core.profile_context.get_persona_paths",
                side_effect=lambda profile_id: paths[profile_id],
            ),
            patch("core.tools.get_profile_vector_service", return_value=None),
        ):
            for profile_id, profile in profiles.items():
                with activate_profile(profile):
                    await toolbox.memory_save(
                        f"{profile_id}onlysecret", scope="journal"
                    )

        writer_file = next(paths["writer"].memory.glob("*.md"))
        researcher_file = next(paths["researcher"].memory.glob("*.md"))
        self.assertIn("writeronlysecret", writer_file.read_text(encoding="utf-8"))
        self.assertNotIn("researcheronlysecret", writer_file.read_text(encoding="utf-8"))
        self.assertIn(
            "researcheronlysecret", researcher_file.read_text(encoding="utf-8")
        )
        self.assertNotIn("writeronlysecret", researcher_file.read_text(encoding="utf-8"))

        writer_service = VectorService(
            db_path=str(root / "vectors-writer"),
            model="disabled",
            persona_paths=paths["writer"],
        )
        researcher_service = VectorService(
            db_path=str(root / "vectors-researcher"),
            model="disabled",
            persona_paths=paths["researcher"],
        )
        writer_matches = await writer_service.search("writeronlysecret")
        researcher_matches = await researcher_service.search("writeronlysecret")

        self.assertEqual(len(writer_matches), 1)
        self.assertEqual(researcher_matches, [])

    async def test_scheduled_jobs_keep_the_active_profile(self):
        from unittest.mock import AsyncMock

        from core.tools import Toolbox

        profile = AgentProfile("researcher", "Researcher")
        scheduler = SimpleNamespace(add_job=AsyncMock(return_value="job-1"))
        toolbox = object.__new__(Toolbox)
        toolbox.scheduler = scheduler

        with activate_profile(profile):
            result = await toolbox.cron_add(
                "check the report",
                context={
                    "channel": "web",
                    "chat_id": "same-chat",
                    "sender_id": "owner",
                    "profile_id": "default",
                },
                time_expr="10s",
            )

        self.assertIn("job-1", result)
        self.assertEqual(
            scheduler.add_job.await_args.kwargs["context"]["profile_id"],
            "researcher",
        )


if __name__ == "__main__":
    unittest.main()
