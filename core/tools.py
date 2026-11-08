"""
Toolbox implementation — OS and OS-like capabilities for the agent.
Provides safe, whitelisted, and confirmed interface for file/OS operations.
"""

import hashlib
import os
import re
import shlex
import stat
import tempfile
from pathlib import Path
from typing import Any, Dict, List, Optional, Union
from loguru import logger

from core.tool_defs import build_tool_definitions
from core.calculator import calculate_expression
from core.cron_tool_service import CronToolService
from core.discord_tool_service import DiscordToolService
from core.file_edits import EditValidationError
from core.memory_tool_service import MemoryToolService
from core.skill_tool_service import SkillToolService
from core.spreadsheet_tool_service import SpreadsheetToolService
from core.safe_http_fetch import SafeHttpFetcher
from core.url_tool_service import UrlToolService
from core.voice_tool_service import VoiceToolService
from core.image_provider_service import ImageProviderService
from core.image_generation_service import ImageGenerationService
from core.image_reference_service import ImageReferenceService
from core.media_delivery_service import MediaDeliveryService
from core.command_execution_service import CommandExecutionService
from core.file_search_service import FileSearchService
from core.file_read_service import FileReadService
from core.file_mutation_service import FileMutationService
from core.subagent_workspace_service import SubagentWorkspaceService
from core.command_validation import validate_shell_command
from core.vectors import get_profile_vector_service, get_vector_service
from core.paths import LONG_TERM_MEMORY_FILE, MEMORY_DIR, PERSONA_DIR
from core.profile_context import get_current_profile_context
from core.redaction import redact_sensitive_text
from core.tool_path_policy import ToolPathPolicy

_PROFILE_SHARED_WORKSPACE_TOOLS = frozenset(
    {"run_command", "run_steps", "spawn_agent"}
)


class Toolbox:
    """
    The Toolbox manages the agent's interaction with the external world.
    It provides a safe, whitelisted, and confirmed interface for file/OS operations.
    """

    def __init__(self, allowed_paths: List[str], bus: Any, config: Any):

        self.allowed_paths = [Path.cwd().resolve()]
        self.bus = bus
        self.config = config
        self.agent = None
        self.skill_tool_service = SkillToolService(
            get_agent=lambda: self.agent,
            get_config=lambda: self.config,
            get_active_workspace_paths=self._active_workspace_paths,
            atomic_write_text=self._atomic_write_text_sync,
        )
        self.spreadsheet_tool_service = self._create_spreadsheet_tool_service()
        self.voice_tool_service = VoiceToolService(get_bus=lambda: self.bus)
        self.image_provider_service = ImageProviderService(
            get_config=lambda: self.config,
            get_codex_account_id=lambda token: self._codex_account_id(token),
        )
        self.safe_http_fetcher = SafeHttpFetcher()
        self.subagent_registry = None
        self.scheduler = None
        self.cron_tool_service = CronToolService()
        self.channels: List[Any] = []
        self.discord_tool_service = DiscordToolService(
            bus, self._get_channel_by_name
        )
        self.vector_service = get_vector_service(config)
        # Delivery tools are side effects. Keep a small, turn-scoped ledger so
        # a model cannot resend the same file repeatedly while it is reasoning
        # through one response. Captions are intentionally excluded from the
        # fingerprint because changing narration must not bypass the guard.
        self.media_delivery_service = self._create_media_delivery_service()
        self.command_execution_service = self._create_command_execution_service()
        self.file_search_service = self._create_file_search_service()
        self.file_read_service = self._create_file_read_service()
        self.file_mutation_service = self._create_file_mutation_service()
        self.subagent_workspace_service = self._create_subagent_workspace_service()

        if allowed_paths:
            for p in allowed_paths:
                try:
                    path = Path(p).resolve()
                    if path not in self.allowed_paths:
                        self.allowed_paths.append(path)
                except Exception as e:
                    logger.warning(f"Could not add allowed path {p}: {e}")

        self.blocked_files = {
            ".env",
            "limebot.json",
            ".git",
            "__pycache__",
            "node_modules",
        }
        self.tool_path_policy = self._create_tool_path_policy_service()
        try:
            from core.video.service import sweep_expired_jobs

            sweep_expired_jobs()
        except Exception as exc:
            logger.debug(f"Video temp cleanup skipped: {exc}")

    def set_agent(self, agent: Any):
        """Set the agent loop instance."""
        self.agent = agent

    def _create_tool_path_policy_service(self) -> ToolPathPolicy:
        return ToolPathPolicy(
            get_allowed_paths=lambda: getattr(self, "allowed_paths", []),
            get_blocked_files=lambda: getattr(self, "blocked_files", set()),
            get_active_workspace_paths=lambda: self._active_workspace_paths(),
            get_profile_context=get_current_profile_context,
            get_persona_root=lambda: PERSONA_DIR,
        )

    def _get_tool_path_policy_service(self) -> ToolPathPolicy:
        service = getattr(self, "tool_path_policy", None)
        if service is None:
            service = self._create_tool_path_policy_service()
            self.tool_path_policy = service
        return service

    def _get_current_vector_service(self):
        profile_context = get_current_profile_context()
        if profile_context is not None and profile_context.profile_id != "default":
            return get_profile_vector_service(
                self.config, profile_id=profile_context.profile_id
            )
        return self.vector_service

    def _get_memory_tool_service(self) -> MemoryToolService:
        service = getattr(self, "_memory_tool_service", None)
        if service is None:
            service = MemoryToolService()
            self._memory_tool_service = service
        return service

    def _get_discord_tool_service(self) -> DiscordToolService:
        service = getattr(self, "discord_tool_service", None)
        if service is None:
            service = DiscordToolService(
                self.bus, self._get_channel_by_name
            )
            self.discord_tool_service = service
        return service

    def _get_skill_tool_service(self) -> SkillToolService:
        service = getattr(self, "skill_tool_service", None)
        if service is None:
            service = SkillToolService(
                get_agent=lambda: self.agent,
                get_config=lambda: self.config,
                get_active_workspace_paths=self._active_workspace_paths,
                atomic_write_text=self._atomic_write_text_sync,
            )
            self.skill_tool_service = service
        return service

    def _create_spreadsheet_tool_service(self) -> SpreadsheetToolService:
        return SpreadsheetToolService(
            is_path_allowed=lambda path: self._is_path_allowed(path),
            resolve_output_path=lambda path: self._resolve_tool_path(
                path, for_write=True
            ),
            is_persona_managed_path=lambda path: self._is_persona_managed_path(path),
        )

    def _get_spreadsheet_tool_service(self) -> SpreadsheetToolService:
        service = getattr(self, "spreadsheet_tool_service", None)
        if service is None:
            service = self._create_spreadsheet_tool_service()
            self.spreadsheet_tool_service = service
        return service

    def _get_voice_tool_service(self) -> VoiceToolService:
        service = getattr(self, "voice_tool_service", None)
        if service is None:
            service = VoiceToolService(get_bus=lambda: self.bus)
            self.voice_tool_service = service
        return service

    def _get_image_provider_service(self) -> ImageProviderService:
        service = getattr(self, "image_provider_service", None)
        if service is None:
            service = ImageProviderService(
                get_config=lambda: self.config,
                get_codex_account_id=lambda token: self._codex_account_id(token),
            )
            self.image_provider_service = service
        return service

    def _get_image_generation_service(self) -> ImageGenerationService:
        service = getattr(self, "image_generation_service", None)
        if service is None:
            service = ImageGenerationService(
                get_config=lambda: self.config,
                is_negated_prompt=self._is_negated_image_generation_prompt,
                resolve_references=self._resolve_image_references,
                model_candidates=self._image_model_candidates,
                is_gemini_model=self._is_gemini_image_model,
                is_openai_model=self._is_openai_image_model,
                generate_gemini=self._generate_gemini_image,
                generate_codex=self._generate_codex_image,
                generate_litellm=self._generate_litellm_image,
                generate_openai_edit=self._generate_openai_image_edit,
                image_extension=self._image_extension_for_mime,
                is_path_allowed=self._is_path_allowed,
                output_directory=lambda: Path("temp/generated_images").resolve(),
                display_path=self._to_display_path,
                publish_preview=self._publish_generated_image_preview,
                log_warning=logger.warning,
            )
            self.image_generation_service = service
        return service

    def _get_image_reference_service(self) -> ImageReferenceService:
        service = getattr(self, "image_reference_service", None)
        if service is None:
            service = ImageReferenceService(
                is_path_shareable=lambda path: self._is_path_shareable(path),
            )
            self.image_reference_service = service
        return service

    def _create_media_delivery_service(self) -> MediaDeliveryService:
        return MediaDeliveryService(
            get_bus=lambda: self.bus,
            get_project_root=lambda: self.allowed_paths[0],
            resolve_tool_path=lambda path: self._resolve_tool_path(path),
            is_path_shareable=lambda path: self._is_path_shareable(path),
            to_display_path=lambda path: self._to_display_path(path),
            fetch_url_to_temp=lambda url: self.fetch_url_to_temp(url),
        )

    def _get_media_delivery_service(self) -> MediaDeliveryService:
        service = getattr(self, "media_delivery_service", None)
        if service is None:
            service = self._create_media_delivery_service()
            self.media_delivery_service = service
        return service

    def _create_command_execution_service(self) -> CommandExecutionService:
        return CommandExecutionService(
            validate_command=lambda command: self.validate_command(command),
            get_command_environment=lambda command: self._command_environment(command),
            send_progress=lambda message: self.send_progress(message),
            get_active_tool_root=lambda: self._active_tool_root(),
            detect_skill_path=lambda command: self._detect_skill_path(command),
            get_config=lambda: self.config,
        )

    def _get_command_execution_service(self) -> CommandExecutionService:
        service = getattr(self, "command_execution_service", None)
        if service is None:
            service = self._create_command_execution_service()
            self.command_execution_service = service
        return service

    def _create_file_search_service(self) -> FileSearchService:
        return FileSearchService(
            is_path_allowed=lambda path: self._is_path_allowed(path),
            resolve_tool_path=lambda path: self._resolve_tool_path(path),
            to_display_path=lambda path: self._to_display_path(path),
        )

    def _get_file_search_service(self) -> FileSearchService:
        service = getattr(self, "file_search_service", None)
        if service is None:
            service = self._create_file_search_service()
            self.file_search_service = service
        return service

    def _create_file_read_service(self) -> FileReadService:
        return FileReadService(
            is_path_allowed=lambda path: self._is_path_allowed(path),
            resolve_tool_path=lambda path: self._resolve_tool_path(path),
            sha256_file_sync=self._sha256_file_sync,
        )

    def _get_file_read_service(self) -> FileReadService:
        service = getattr(self, "file_read_service", None)
        if service is None:
            service = self._create_file_read_service()
            self.file_read_service = service
        return service

    def _create_file_mutation_service(self) -> FileMutationService:
        return FileMutationService(
            is_path_allowed=lambda path: self._is_path_allowed(path),
            resolve_tool_path=lambda path, for_write=False: self._resolve_tool_path(
                path, for_write=for_write
            ),
            is_persona_managed_path=lambda path: self._is_persona_managed_path(path),
            persona_write_error=self._persona_write_error,
            to_display_path=lambda path: self._to_display_path(path),
            atomic_write_text_if_unchanged=lambda path, expected, content: self._atomic_write_text_if_unchanged_sync(
                path, expected, content
            ),
            verify_text_syntax=lambda path, text: self._verify_text_syntax(
                path, text
            ),
            verify_git_diff_check=lambda path: self._verify_git_diff_check_sync(
                path
            ),
            verify_files=lambda paths, **kwargs: self.verify_files(paths, **kwargs),
            diagnose_files=lambda paths, **kwargs: self.diagnose_files(paths, **kwargs),
            active_tool_root=lambda: self._active_tool_root(),
            sanitized_env=lambda: self._sanitized_env(),
        )

    def _get_file_mutation_service(self) -> FileMutationService:
        service = getattr(self, "file_mutation_service", None)
        if service is None:
            service = self._create_file_mutation_service()
            self.file_mutation_service = service
        return service

    def _create_subagent_workspace_service(self) -> SubagentWorkspaceService:
        return SubagentWorkspaceService(
            get_agent=lambda: self.agent,
            get_subagent_registry=lambda: self.subagent_registry,
            active_tool_root=lambda: self._active_tool_root(),
            write_file=lambda path, content: self.write_file(path, content),
            edit_file=lambda path, edits, expected_sha256: self.edit_file(
                path, edits, expected_sha256
            ),
            delete_file=lambda path: self.delete_file(path),
        )

    def _get_subagent_workspace_service(self) -> SubagentWorkspaceService:
        service = getattr(self, "subagent_workspace_service", None)
        if service is None:
            service = self._create_subagent_workspace_service()
            self.subagent_workspace_service = service
        return service

    def _get_safe_http_fetcher(self) -> SafeHttpFetcher:
        service = getattr(self, "safe_http_fetcher", None)
        if service is None:
            service = SafeHttpFetcher()
            self.safe_http_fetcher = service
        return service

    def _get_url_tool_service(self) -> UrlToolService:
        service = getattr(self, "url_tool_service", None)
        if service is None:
            service = UrlToolService(
                get_fetcher=self._get_safe_http_fetcher,
                download_directory=lambda: self.allowed_paths[0]
                / "temp"
                / "downloads",
                display_path=self._to_display_path,
                send_progress=self.send_progress,
            )
            self.url_tool_service = service
        return service

    async def analyze_video(
        self,
        source: str,
        question: str = "",
        detail: str = "balanced",
        start: Optional[str] = None,
        end: Optional[str] = None,
        max_frames: Optional[int] = None,
        resolution: int = 512,
    ) -> str:
        """Delegate to the optional native video pipeline."""
        from core.video import analyze_video

        video_config = getattr(self.config, "video", None)
        return await analyze_video(
            source=source,
            question=question,
            detail=detail,
            start=start,
            end=end,
            max_frames=max_frames,
            resolution=resolution,
            is_path_allowed=self._is_path_allowed,
            whisper_enabled=bool(getattr(video_config, "whisper_enabled", False)),
            openai_api_key=os.getenv("OPENAI_API_KEY", "").strip(),
        )

    def set_scheduler(self, scheduler: Any):
        """Set the scheduler instance."""
        self.scheduler = scheduler
        self.cron_tool_service.set_scheduler(scheduler)

    def _get_cron_tool_service(self) -> CronToolService:
        service = getattr(self, "cron_tool_service", None)
        if service is None:
            service = CronToolService()
            self.cron_tool_service = service
        service.set_scheduler(getattr(self, "scheduler", None))
        return service

    def set_subagent_registry(self, registry: Any):
        """Set the subagent registry used to enrich delegation tools."""
        self.subagent_registry = registry

    def set_channels(self, channels: List[Any]):
        """Expose live channel instances for channel-native tools."""
        self.channels = list(channels or [])

    def _detect_skill_path(self, command: str):
        """Best-effort detection of a skill directory from a command string."""
        match = re.search(r"skills[\\/](?P<name>[^\\/\\s]+)", command)
        if not match:
            return None

        name = match.group("name")
        return {"name": name, "path": Path("skills") / name}

    @staticmethod
    def _preferred_python_executable() -> str:
        return CommandExecutionService.preferred_python_executable()

    @staticmethod
    def _windows_browser_binary(browser_name: str) -> Optional[Path]:
        return CommandExecutionService.windows_browser_binary(browser_name)

    @staticmethod
    def _is_local_port_in_use(port: int) -> bool:
        return CommandExecutionService.is_local_port_in_use(port)

    @staticmethod
    def _extract_remote_debug_port(command: str) -> Optional[int]:
        return CommandExecutionService.extract_remote_debug_port(command)

    @staticmethod
    def _is_windows_process_running(image_name: str) -> bool:
        return CommandExecutionService.is_windows_process_running(image_name)

    def _normalize_browser_launch_command(
        self, command: str
    ) -> tuple[str, Optional[str]]:
        return self._get_command_execution_service().normalize_browser_launch_command(
            command
        )

    @staticmethod
    def _is_browser_remote_debug_launch(command: str) -> bool:
        return CommandExecutionService.is_browser_remote_debug_launch(command)

    async def send_progress(self, message: str):
        """Broadcast tool progress if an agent/bus is available."""
        message = redact_sensitive_text(message)
        if self.agent and hasattr(self.agent, "send_tool_progress"):
            from core.context import tool_context

            ctx = tool_context.get()
            if ctx and "tc_id" in ctx:
                await self.agent.send_tool_progress(
                    ctx["tc_id"], ctx.get("chat_id", "system"), message
                )
        logger.info(f"🛠️ Tool Progress: {message}")

    def get_tool_definitions(self) -> List[Dict[str, Any]]:
        """Return the tool definitions based on the current config."""
        enabled_skills = []
        if self.config:
            if hasattr(self.config, "skills") and hasattr(
                self.config.skills, "enabled"
            ):
                enabled_skills = self.config.skills.enabled
            elif isinstance(self.config, dict) and "skills" in self.config:
                enabled_skills = self.config["skills"].get("enabled", [])

        available_agents = {}
        if self.subagent_registry is not None:
            try:
                available_agents = self.subagent_registry.get_agent_descriptions()
            except Exception as e:
                logger.warning(f"Failed to read subagent registry: {e}")

        # Search and browser tools always stay registered; missing Playwright
        # fails at execution with BROWSER_INSTALL_HINT rather than hiding them.
        tools = build_tool_definitions(
            enabled_skills,
            available_agents=available_agents,
            search_available=True,
        )

        # Load MCP tools dynamically
        try:
            from core.mcp_client import get_mcp_manager

            mcp_tools = get_mcp_manager().get_tools()
            for mt in mcp_tools:
                tools.append(mt)
                logger.debug(f"Registered MCP tool: {mt['function']['name']}")
        except Exception as e:
            logger.warning(f"Failed to load MCP tools: {e}")

        return tools

    def _active_workspace_paths(self) -> tuple[Optional[Path], Optional[Path]]:
        return self._get_tool_path_policy_service().active_workspace_paths()

    def _resolve_tool_path(
        self, path_str: Union[str, Path], *, for_write: bool = False
    ) -> Path:
        """Resolve a tool path inside the active isolated workspace when present.

        Writes stay in the temporary clone. Reads fall back to the live
        source or other parent-allowed roots so explorer/reviewer can see
        AGENTS.md and allowlisted temp/ instead of inventing a denial.
        """
        return self._get_tool_path_policy_service().resolve_tool_path(
            path_str, for_write=for_write
        )

    def _active_tool_root(self) -> Path:
        return self._get_tool_path_policy_service().active_tool_root()

    def _is_path_allowed(self, path_str: Union[str, Path]) -> bool:
        """Enforce whitelist and block sensitive files."""
        return self._get_tool_path_policy_service().is_path_allowed(path_str)

    def _to_display_path(self, path: Path) -> str:
        """Prefer project-relative paths in tool responses."""
        return self._get_tool_path_policy_service().to_display_path(path)

    @staticmethod
    def _sha256_file_sync(path: Path) -> str:
        digest = hashlib.sha256()
        with path.open("rb") as handle:
            for chunk in iter(lambda: handle.read(1024 * 1024), b""):
                digest.update(chunk)
        return digest.hexdigest()

    def _is_persona_managed_path(self, path: Path) -> bool:
        return self._get_tool_path_policy_service().is_persona_managed_path(path)

    @staticmethod
    def _persona_write_error() -> str:
        return (
            "Error: Direct modification of state-managed files under 'persona/' is blocked. "
            "Please use the appropriate XML tags to update your persona, mood, relationship, "
            "memories, or user profiles (e.g., <save_soul>, <save_identity>, <save_mood>, "
            "<save_relationship>, <save_memory>, <log_memory>, or <save_user>)."
        )

    @staticmethod
    def _atomic_write_text_sync(path: Path, content: str) -> None:
        """Atomically replace an existing text file in its own directory."""
        temporary_path: Optional[Path] = None
        try:
            mode = stat.S_IMODE(path.stat().st_mode) if path.exists() else 0o600
            with tempfile.NamedTemporaryFile(
                mode="w",
                encoding="utf-8",
                newline="",
                dir=str(path.parent),
                prefix=f".{path.name}.limebot-",
                suffix=".tmp",
                delete=False,
            ) as handle:
                temporary_path = Path(handle.name)
                handle.write(content)
                handle.flush()
                os.fsync(handle.fileno())
            try:
                os.chmod(temporary_path, mode)
            except OSError:
                logger.debug("Could not preserve permissions for temporary edit file")
            os.replace(temporary_path, path)
            temporary_path = None
        finally:
            if temporary_path is not None:
                try:
                    temporary_path.unlink(missing_ok=True)
                except OSError:
                    pass

    @classmethod
    def _atomic_write_text_if_unchanged_sync(
        cls, path: Path, expected_sha256: str, content: str
    ) -> None:
        """Recheck the target immediately before an atomic replacement."""
        current_sha256 = cls._sha256_file_sync(path)
        if current_sha256 != expected_sha256:
            raise EditValidationError(
                "The file changed while the edit was being prepared; re-read it and retry."
            )
        cls._atomic_write_text_sync(path, content)

    def _format_search_results(
        self, rows: List[Dict[str, Any]], query: str
    ) -> str:
        return self._get_file_search_service()._format_search_results(rows, query)

    @staticmethod
    def _slice_text_for_read(
        text: str,
        max_chars: int,
        start_line: Optional[int] = None,
        end_line: Optional[int] = None,
    ) -> str:
        return FileReadService._slice_text_for_read(
            text, max_chars, start_line, end_line
        )

    def _get_channel_by_name(self, channel_name: str) -> Any | None:
        for channel in self.channels:
            if getattr(channel, "name", "") == channel_name:
                return channel
        return None

    def _is_path_shareable(self, path_str: Union[str, Path]) -> tuple[bool, str | None, Path | None]:
        """Validate whether a file is safe to send back to a user."""
        return self._get_tool_path_policy_service().is_path_shareable(path_str)

    @staticmethod
    def _extract_docx_text(path: Path) -> str:
        return FileReadService._extract_docx_text(path)

    @staticmethod
    def _extract_pdf_text(path: Path) -> str:
        return FileReadService._extract_pdf_text(path)

    async def read_file(
        self,
        path: str,
        max_chars: int = 20_000,
        start_line: int = None,
        end_line: int = None,
        include_hash: bool = False,
    ) -> str:
        return await self._get_file_read_service().read_file(
            path, max_chars, start_line, end_line, include_hash
        )

    async def write_file(self, path: str, content: str) -> str:
        """Write content to a file."""
        return await self._get_file_mutation_service().write_file(path, content)

    async def edit_file(
        self,
        path: str,
        edits: Any,
        expected_sha256: str,
    ) -> str:
        """Apply exact, hash-guarded text edits atomically."""
        return await self._get_file_mutation_service().edit_file(
            path, edits, expected_sha256
        )

    @staticmethod
    def _verify_text_syntax(path: Path, text: str) -> Dict[str, Any]:
        return FileMutationService.verify_text_syntax(path, text)

    @staticmethod
    def _verify_git_diff_check_sync(path: Path) -> Dict[str, Any]:
        return FileMutationService.verify_git_diff_check_sync(path)

    async def verify_files(
        self,
        paths: List[str],
        include_diagnostics: bool = False,
        provider: str = "auto",
    ) -> str:
        return await self._get_file_mutation_service().verify_files(
            paths, include_diagnostics, provider
        )

    async def diagnose_files(
        self,
        paths: List[str],
        provider: str = "auto",
        timeout: float = 45,
    ) -> str:
        return await self._get_file_mutation_service().diagnose_files(
            paths, provider, timeout
        )

    async def calculate(self, expression: str) -> str:
        """Evaluate bounded arithmetic without invoking a shell or interpreter."""
        return calculate_expression(expression)

    async def create_spreadsheet(
        self,
        path: str,
        sheets: List[Dict[str, Any]],
        title: str = "",
    ) -> str:
        """Create a styled, formula-capable XLSX workbook as a native tool."""
        return await self._get_spreadsheet_tool_service().create(path, sheets, title)

    async def delete_file(self, path: str) -> str:
        """Delete a file or directory."""
        return await self._get_file_mutation_service().delete_file(path)

    async def list_dir(
        self,
        path: str = ".",
        limit: int = 200,
        offset: int = 0,
        include_hidden: bool = False,
        sort_by: str = "name",
        descending: bool = False,
        folders_first: bool = True,
    ) -> str:
        """List files in a directory with pagination and configurable sorting."""
        return await self._get_file_search_service().list_directory(
            path, limit, offset, include_hidden, sort_by, descending, folders_first
        )

    async def search_files(
        self,
        query: str,
        path: str = ".",
        file_glob: str = "*",
        mode: str = "content",
        case_sensitive: bool = False,
        max_results: int = 40,
    ) -> str:
        return await self._get_file_search_service().search_files(
            query, path, file_glob, mode, case_sensitive, max_results
        )

    def _search_file_names_sync(
        self,
        root: Path,
        query: str,
        file_glob: str,
        case_sensitive: bool,
        max_results: int,
    ) -> List[Dict[str, Any]]:
        return self._get_file_search_service()._search_file_names_sync(
            root, query, file_glob, case_sensitive, max_results
        )

    def _search_file_content_sync(
        self,
        root: Path,
        query: str,
        file_glob: str,
        case_sensitive: bool,
        max_results: int,
    ) -> List[Dict[str, Any]]:
        return self._get_file_search_service()._search_file_content_sync(
            root, query, file_glob, case_sensitive, max_results
        )

    @staticmethod
    def _sanitized_env() -> dict:
        """Return a copy of the process environment with secrets stripped.

        ``config.py`` injects provider credentials into ``os.environ`` for
        legitimate in-process consumers (LiteLLM, vector embeddings, browser
        tooling). Subprocesses spawned by ``run_command`` inherit the full
        environment by default, so an approved-by-mistake ``env``/``printenv``
        would exfiltrate every key. Drop anything that looks like a secret
        while preserving PATH/HOME/TMPDIR and other benign vars.
        """
        secret_suffixes = ("API_KEY", "_TOKEN", "_SECRET", "PASSWORD", "APIKEY")
        sanitized = {}
        for key, value in os.environ.items():
            upper = key.upper()
            if any(upper.endswith(suffix) for suffix in secret_suffixes):
                continue
            sanitized[key] = value
        return sanitized

    @staticmethod
    def _is_discord_skill_invocation(command: str) -> bool:
        """Match only the shipped Discord skill entrypoint, never inline code."""
        try:
            parts = shlex.split(command, posix=os.name != "nt")
        except ValueError:
            return False
        if len(parts) < 2:
            return False

        executable = (
            str(parts[0])
            .strip("\"'")
            .replace("\\", "/")
            .rsplit("/", 1)[-1]
            .lower()
        )
        script = str(parts[1]).strip("\"'").replace("\\", "/").lower()
        return (
            executable in {"python", "python.exe", "python3", "python3.exe"}
            and script == "skills/discord/main.py"
        )

    def _command_environment(self, command: str) -> dict:
        """Keep secrets out of shell tools except a required first-party API call."""
        env = self._sanitized_env()
        workspace_root, source_root = self._active_workspace_paths()
        if workspace_root is not None or source_root is not None:
            return env
        if not self._is_discord_skill_invocation(command):
            return env

        whitelist = getattr(self.config, "whitelist", None)
        api_key = getattr(whitelist, "api_key", None)
        if api_key:
            # This fixed entrypoint sends the key in its authenticated request;
            # arbitrary and isolated subprocesses continue receiving sanitized envs.
            env["APP_API_KEY"] = str(api_key)
        return env

    def validate_command(self, command: str) -> Optional[str]:
        """Return a deterministic validation error before approval/execution."""
        if not str(command or "").strip():
            return "Error: Command is required."
        workspace_root, source_root = self._active_workspace_paths()
        allow_unsafe = bool(
            getattr(self.config, "allow_unsafe_commands", False)
        ) if self.config else False
        return validate_shell_command(
            command,
            allow_unsafe_commands=allow_unsafe,
            workspace_root=workspace_root,
            source_root=source_root,
            project_root=self._active_tool_root(),
            cwd=Path.cwd().resolve(),
        )
    async def run_command(self, command: str) -> str:
        """Execute a terminal command through the bounded runner service."""
        return await self._get_command_execution_service().run_command(command)

    async def run_steps(self, commands: Any) -> str:
        """Run one or more shell commands in order without requiring &&."""
        if isinstance(commands, str):
            commands = [commands]
        if not isinstance(commands, list) or not commands:
            return "Error: run_steps requires a non-empty commands array."
        steps = [str(item).strip() for item in commands if str(item or "").strip()]
        if not steps:
            return "Error: run_steps requires a non-empty commands array."

        parts: List[str] = []
        for index, command in enumerate(steps, start=1):
            validation_error = self.validate_command(command)
            if validation_error:
                parts.append(
                    f"--- step {index}/{len(steps)} ---\n{command}\n{validation_error}"
                )
                return (
                    "Error: run_steps stopped after a rejected command.\n"
                    + "\n\n".join(parts)
                )
            result = await self.run_command(command)
            parts.append(f"--- step {index}/{len(steps)} ---\n{command}\n{result}")
            failed = str(result).startswith("Error:") or (
                "Exit Code:" in str(result)
                and not str(result).rstrip().endswith("Exit Code: 0")
                and "Success (Exit Code: 0" not in str(result)
            )
            if failed:
                return (
                    "Error: run_steps stopped after a failed command.\n"
                    + "\n\n".join(parts)
                )
        return "\n\n".join(parts)

    async def memory_search(self, query: str) -> str:
        """Search durable memory, using vectors when available and Markdown otherwise."""
        return await self._get_memory_tool_service().search(
            query, self._get_current_vector_service()
        )

    async def memory_save(self, content: str, scope: str = "journal") -> str:
        """Persist an explicit user-requested memory in the Markdown source of truth."""
        profile_context = get_current_profile_context()
        if profile_context is not None and profile_context.profile_id != "default":
            memory_dir = profile_context.paths.memory
            long_term_memory_file = profile_context.paths.long_term_memory
        else:
            # Preserve the module constants as the default-profile test seam.
            memory_dir = MEMORY_DIR
            long_term_memory_file = LONG_TERM_MEMORY_FILE
        return await self._get_memory_tool_service().save(
            content,
            scope,
            memory_dir=memory_dir,
            long_term_memory_file=long_term_memory_file,
            vector_service=self._get_current_vector_service(),
        )

    async def spawn_agent(
        self,
        task: str,
        session_key: str = None,
        agent: Optional[str] = None,
        background: Optional[bool] = None,
        isolation: str = "auto",
    ) -> str:
        """Spawn a sub-agent and optionally let it report back in the background."""
        return await self._get_subagent_workspace_service().spawn_agent(
            task, session_key, agent, background, isolation
        )

    async def apply_workspace_changeset(
        self,
        workspace_id: Optional[str] = None,
        changeset: Optional[Any] = None,
        *,
        session_key: Optional[str] = None,
    ) -> str:
        """Apply a retained copy-isolation capture to the live tree."""
        return await self._get_subagent_workspace_service().apply_workspace_changeset(
            workspace_id, changeset, session_key=session_key
        )

    async def fetch_url_to_temp(
        self,
        url: str,
        max_bytes: int = SafeHttpFetcher.MAX_DOWNLOAD_BYTES,
        *,
        referer: str = "",
        user_agent: str = "",
        extra_headers: Optional[Dict[str, str]] = None,
    ) -> str:
        """Download a public http(s) URL into temp/downloads and return its path."""
        return await self._get_url_tool_service().fetch_url_to_temp(
            url,
            max_bytes,
            referer=referer,
            user_agent=user_agent,
            extra_headers=extra_headers,
        )

    async def fetch_readable_text(self, url: str, max_chars: int = 4000) -> str:
        """Fetch a page and return its readable text, SSRF-guarded."""
        return await self._get_url_tool_service().fetch_readable_text(url, max_chars)

    async def _publish_web_media(self, file_path: Path, caption: str) -> None:
        await self._get_media_delivery_service()._publish_web_media(
            file_path, caption
        )

    async def send_media(self, path: str, caption: str = "") -> str:
        return await self._get_media_delivery_service().send_media(path, caption)

    async def attach_chat_image(self, image_url: str, caption: str = "") -> str:
        """Host-owned download + stamp of an existing photo into the current chat.

        Used after web_search(kind='images') on a media-delivery turn. Not a
        model-facing tool.
        """
        return await self.send_media(image_url, caption)

    def _remember_sent_media(self, turn_id: str, fingerprint: str) -> None:
        self._get_media_delivery_service()._remember_sent_media(
            turn_id, fingerprint
        )

    def media_delivered_this_turn(self, turn_id: str) -> bool:
        return self._get_media_delivery_service().media_delivered_this_turn(turn_id)

    async def send_voice(self, text: str, channel: str = "") -> str:
        """Speak `text` aloud as a voice message in the current chat.

        Synthesizes speech with ElevenLabs and delivers it as audio: an mp3 file
        on Discord/WhatsApp, or an inline playable clip on web. Use this when the
        user asks to be sent a voice message / voice note instead of text.

        Requires an ElevenLabs API key (Settings → Credentials). Returns an
        "Error: ..." string if voice is unavailable so you can fall back to text.
        """
        return await self._get_voice_tool_service().send_voice(text, channel)

    @staticmethod
    def _normalize_image_model(model: str) -> str:
        return ImageProviderService._normalize_image_model(model)

    @staticmethod
    def _is_gemini_image_model(model: str) -> bool:
        return ImageProviderService._is_gemini_image_model(model)

    @staticmethod
    def _is_openai_image_model(model: str) -> bool:
        return ImageProviderService._is_openai_image_model(model)

    @staticmethod
    def _is_negated_image_generation_prompt(prompt: str) -> bool:
        return ImageProviderService._is_negated_image_generation_prompt(prompt)

    @staticmethod
    def _image_extension_for_mime(mime_type: str) -> str:
        return ImageProviderService._image_extension_for_mime(mime_type)

    @staticmethod
    def _gemini_aspect_ratio(size: str) -> str:
        return ImageProviderService._gemini_aspect_ratio(size)

    @staticmethod
    def _codex_account_id(token: str) -> str:
        return ImageProviderService._codex_account_id(token)

    @staticmethod
    def _codex_image_model(model: str) -> str:
        return ImageProviderService._codex_image_model(model)

    def _image_model_candidates(self, requested_model: str) -> List[str]:
        return self._get_image_provider_service()._image_model_candidates(
            requested_model
        )

    async def _resolve_image_references(
        self,
        reference_images: Optional[List[str]],
        use_attached_images: Optional[bool],
    ) -> tuple[List[Dict[str, Any]], bool]:
        return await self._get_image_reference_service().resolve_references(
            reference_images, use_attached_images
        )

    async def _generate_codex_image(
        self,
        prompt: str,
        model: str,
        count: int,
        size: str,
        quality: str,
        references: Optional[List[Dict[str, Any]]] = None,
    ) -> List[Dict[str, str]]:
        return await self._get_image_provider_service()._generate_codex_image(
            prompt, model, count, size, quality, references
        )

    async def _generate_gemini_image(
        self,
        prompt: str,
        model: str,
        count: int,
        size: str,
        references: Optional[List[Dict[str, Any]]] = None,
    ) -> List[Dict[str, str]]:
        return await self._get_image_provider_service()._generate_gemini_image(
            prompt, model, count, size, references
        )

    async def _generate_litellm_image(
        self, prompt: str, model: str, count: int, size: str, quality: str
    ) -> List[Dict[str, str]]:
        return await self._get_image_provider_service()._generate_litellm_image(
            prompt, model, count, size, quality
        )

    async def _generate_openai_image_edit(
        self,
        prompt: str,
        model: str,
        count: int,
        size: str,
        quality: str,
        references: List[Dict[str, Any]],
    ) -> List[Dict[str, str]]:
        return await self._get_image_provider_service()._generate_openai_image_edit(
            prompt, model, count, size, quality, references
        )

    async def _publish_generated_image_preview(
        self,
        paths: List[Path],
        caption: str,
    ) -> None:
        await self._get_media_delivery_service().publish_generated_image_preview(
            paths, caption
        )

    async def generate_image(
        self,
        prompt: str,
        model: str = "",
        size: str = "",
        quality: str = "",
        count: int = 1,
        reference_images: Optional[List[str]] = None,
        use_attached_images: Optional[bool] = None,
    ) -> str:
        """Generate or edit image files using text and optional references."""
        return await self._get_image_generation_service().generate_image(
            prompt=prompt,
            model=model,
            size=size,
            quality=quality,
            count=count,
            reference_images=reference_images,
            use_attached_images=use_attached_images,
        )

    async def send_discord_message(
        self,
        message: str = "",
        channel_id: str = "",
        user_id: str = "",
    ) -> str:
        """Send a plain Discord message to a channel or user DM."""
        return await self._get_discord_tool_service().send_message(
            message, channel_id, user_id
        )

    async def send_discord_embed(
        self,
        title: str = "",
        description: str = "",
        color: str = "#5865F2",
        footer: str = "",
        image: str = "",
        thumbnail: str = "",
        fields: Optional[List[Dict[str, Any]]] = None,
        channel_id: str = "",
        user_id: str = "",
    ) -> str:
        """Send a native Discord embed via the live Discord channel."""
        return await self._get_discord_tool_service().send_embed(
            title,
            description,
            color,
            footer,
            image,
            thumbnail,
            fields,
            channel_id,
            user_id,
        )

    async def list_discord_channels(self) -> str:
        """List Discord guilds and text channels available to the running bot."""
        return await self._get_discord_tool_service().list_channels()

    async def voice_list_members(self) -> str:
        """List people in the active Discord voice channel and other voice channels."""
        return await self._get_discord_tool_service().voice_members()

    async def voice_move_members(
        self, channel: str = "", members: Optional[List[str]] = None
    ) -> str:
        """Move people from the active Discord voice channel to another one."""
        return await self._get_discord_tool_service().voice_move(channel, members)

    async def voice_set_listening(self, enabled: bool = False) -> str:
        """Stop or resume listening in the active Discord voice conversation."""
        return await self._get_discord_tool_service().voice_listening(enabled)

    async def cron_add(
        self,
        message: str,
        context: Dict[str, Any],
        time_expr: str = None,
        cron_expr: str = None,
        tz: str = None,
        name: str = None,
    ) -> str:
        """Add a scheduled task."""
        return await self._get_cron_tool_service().add(
            message, context, time_expr, cron_expr, tz, name
        )

    async def cron_list(self) -> str:
        """List all pending scheduled tasks."""
        return await self._get_cron_tool_service().list()

    async def cron_remove(self, job_id: str) -> str:
        """Remove a scheduled task by ID."""
        return await self._get_cron_tool_service().remove(job_id)

    async def cron_deactivate(self, job_id: str) -> str:
        """Pause a scheduled task by ID."""
        return await self._get_cron_tool_service().set_active(job_id, False)

    async def cron_activate(self, job_id: str) -> str:
        """Resume a scheduled task by ID."""
        return await self._get_cron_tool_service().set_active(job_id, True)

    async def inspect_skill(
        self,
        skill_name: str,
        path: Optional[str] = None,
        start_line: Optional[int] = None,
        end_line: Optional[int] = None,
    ) -> str:
        """Return bounded, redacted information about a registered skill."""
        return await self._get_skill_tool_service().inspect_skill(
            skill_name, path, start_line, end_line
        )

    async def edit_skill(self, skill_name: str, changes: Any) -> str:
        """Apply a validated transactional edit to a user-owned skill."""
        return await self._get_skill_tool_service().edit_skill(skill_name, changes)

    async def create_skill(self, name: str, description: str) -> str:
        """Initialize and enable a skill in local storage."""
        return await self._get_skill_tool_service().create_skill(name, description)
