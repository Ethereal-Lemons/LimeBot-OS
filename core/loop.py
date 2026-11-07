import asyncio
import ast
import base64
import contextvars
import hashlib
import json
import mimetypes
import os
import re
import time
import uuid
from datetime import datetime
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Set, Tuple

from core.confirmation import (
    ConfirmationManager,
    SENSITIVE_TOOLS,
    APPROVE_WORDS,
    DENY_WORDS,
)
from core.rag_engine import RagEngine
from core.tool_dispatcher import (
    normalize_tool_alias,
    TOOL_RESULT_LIMITS,
    DEFAULT_TOOL_RESULT_LIMIT,
    BROWSER_CACHEABLE,
    TAG_COMPAT_TOOLS,
    SEARCH_TOOL_NAMES,
    truncate_tool_result,
    READ_ONLY_TOOL_NAMES as _READ_ONLY_TOOL_NAMES,
    MUTATION_TOOL_NAMES as _MUTATION_TOOL_NAMES,
)
from core.tool_outcomes import (
    ToolOutcome,
    build_tool_outcome,
    tool_phase,
)
from core.tool_batch_executor import ToolBatchExecutor
from core.tool_batch_events import ToolBatchEventContext, ToolBatchEventService
from core.turn_history_service import TurnHistoryService
from core.turn_preparation_service import TurnPreparationService
from core.conversation_image_service import ConversationImageService
from core.tool_runtime import (
    ToolExecutionContext,
    ToolRuntime,
    ToolRuntimeHooks,
    tool_cache_scope_args,
)
from core.browser_search_service import (
    BrowserSearchService,
    BrowserSearchToolAdapter,
)
from core.tool_call_parser import (
    extract_tool_calls,
    sanitize_tool_call_content,
    trim_leading_structural_lines,
)
from core.capability_service import CapabilityService
from core.stream_consumer import (
    _GHOST_TAG_NAMES,
    _GHOST_TAG_RE,
    consume_stream,
)
from core.llm_call_service import ProviderErrorTypes, llm_call_with_retry
from core.subagent_runner import run_subagent


try:
    from litellm import (
        RateLimitError,
        InternalServerError,
        APIConnectionError,
        ServiceUnavailableError,
        AuthenticationError,
    )
except Exception:
    class _LiteLLMFallbackError(Exception):
        def __init__(self, *args, **kwargs):
            message = kwargs.get("message")
            if message is None and args:
                message = args[0]
            super().__init__(message or "")

    class RateLimitError(_LiteLLMFallbackError):
        pass

    class InternalServerError(_LiteLLMFallbackError):
        pass

    class APIConnectionError(_LiteLLMFallbackError):
        pass

    class ServiceUnavailableError(_LiteLLMFallbackError):
        pass

    class AuthenticationError(_LiteLLMFallbackError):
        pass
from loguru import logger

from config import load_config
from core.browser import get_browser_manager
from core.bus import MessageBus
from core.cache import ToolCache
from core.context import tool_context
from core.events import InboundMessage, OutboundMessage
from core.llm_client import ChatRequest, LimeLLMClient, ProviderConfig
from core.managed_tasks import ManagedTaskRegistry
from core.job_queue import FAILED, SUCCEEDED, get_job_queue
from core.operator_invariants import (
    collect_subagent_failures,
    ensure_visible_failures,
    evaluate_operator_invariants,
    format_invariant_next_action,
    format_subagent_failure_block,
)
from core.approval_policy import ToolApprovalPolicy
from core.tool_approval_flow import ToolApprovalFlowService, ToolApprovalRequest
from core.recovery_controller import RecoveryState
from core.tool_recovery_service import (
    ToolRecoveryRequest,
    ToolRecoveryResult,
    ToolRecoveryService,
)
from core.task_runs import (
    ACTIVE_STATES as TASK_RUN_ACTIVE_STATES,
    BLOCKED as TASK_RUN_BLOCKED,
    COMPLETED as TASK_RUN_COMPLETED,
    EXECUTING as TASK_RUN_EXECUTING,
    PLANNING as TASK_RUN_PLANNING,
    REPAIRING as TASK_RUN_REPAIRING,
    RETRYING as TASK_RUN_RETRYING,
    get_task_run_store,
)
from core.task_run_continuation import TaskRunContinuationService
from core.task_run_completion import (
    TaskRunCompletionRequest,
    TaskRunCompletionService,
)
from core.task_run_policy import acceptance_satisfied
from core.workspace_review_service import WorkspaceReviewService
from core.inbound_turn_lifecycle import InboundTurnLifecycle
from core.turn_state import TurnState, normalize_turn_status
from core.unattended import is_unattended_turn
from core import prompt as prompt_module
from core.prompt_coordinator import PromptCoordinator
from core.profile_context import (
    ProfileCoordinator,
    get_current_model,
    get_current_persona_paths,
    get_current_profile_context,
    get_current_profile_id,
)
from core.profiles import ProfileError, get_profile_registry, profile_allows_tool
from core.metrics import MetricsCollector
from core.prompt_modes import (
    build_ponytail_prompt_addition,
    normalize_ponytail_mode,
)
from core.redaction import redact_sensitive_text, redact_sensitive_value
from core.provider_circuit_breaker import (
    ProviderCircuitBreaker,
    ProviderCircuitOpenError,
)
from core.runtime_paths import get_skill_dirs
from core.skill_invocation import parse_skill_invocation
from core.session_manager import SessionManager
from core.skills import SkillRegistry
from core.subagents import SubagentRegistry, normalize_subagent_tool_name
from core.tag_parser import process_tags
from core.tools import Toolbox
from core.vectors import get_profile_vector_service, get_vector_service


TOOL_BROADCAST_MAX_CHARS = 500
_SUBAGENT_REPORT_DIFF_MAX_CHARS = 10_000
_TOOL_LOCAL_IMAGE_MAX_BYTES = 4 * 1024 * 1024
_RECENT_IMAGE_REFERENCE_TTL_S = 30 * 60
_TOOL_MEDIA_PAYLOAD_START = "<limebot-tool-payload>"
_TOOL_MEDIA_PAYLOAD_END = "</limebot-tool-payload>"
_HISTORY_TOOL_OUTPUT_MAX_CHARS = 8_000
_HISTORY_INTERRUPTED_TOOL_MESSAGE = (
    "[Tool call interrupted before a result was recorded. The previous runtime "
    "did not persist a tool result.]"
)


# Tool result limits and browser cacheability are now in core/tool_dispatcher.py
# and imported at the top of this file as TOOL_RESULT_LIMITS, DEFAULT_TOOL_RESULT_LIMIT,
# BROWSER_CACHEABLE, TAG_COMPAT_TOOLS.
# SENSITIVE_TOOLS, APPROVE_WORDS, DENY_WORDS come from core/confirmation.py.

_INTERIM_SAVE_EVERY = 5
_CURRENT_TASK_ID: contextvars.ContextVar[Optional[str]] = contextvars.ContextVar(
    "limebot_current_task_id", default=None
)

CODING_PHASES = frozenset(
    {"inspect", "plan", "apply", "verify", "repair", "complete", "blocked"}
)
_RESEARCH_TOOL_NAMES = frozenset(
    {
        "web_search",
        "browser_navigate",
        "browser_act",
        "browser_extract",
    }
)
_ARTIFACT_REQUEST_RE = re.compile(
    r"\b(?:xlsx|excel|spreadsheet|csv|pdf|docx|report|document|download|export|"
    r"attachment|attach|file|archivo|adjunta|adjuntar|descarga|descargar|exporta|"
    r"exportar|informe|documento|hoja\s+de\s+c[aá]lculo)\b",
    re.IGNORECASE,
)
_CODING_HINT_RE = re.compile(
    r"\b(code|coding|program|repo|repository|bug|fix|test|pytest|lint|build|compile|"
    r"implement|refactor|function|file|patch|error|failure)\b",
    re.IGNORECASE,
)


_CASUAL_WORDS = frozenset(
    {
        "hi",
        "hey",
        "hello",
        "yo",
        "sup",
        "ok",
        "okay",
        "k",
        "yes",
        "no",
        "nope",
        "yep",
        "sure",
        "thanks",
        "thank you",
        "lol",
        "lmao",
        "haha",
        "nice",
        "cool",
        "good",
        "great",
        "bye",
        "cya",
        "ttyl",
        "brb",
    }
)

_CASUAL_PHRASE_PREFIXES = (
    "how are you",
    "how's it going",
    "hows it going",
    "what's up",
    "whats up",
    "good morning",
    "good night",
    "good evening",
    "thank you",
    "thanks",
)

# Backward-compat aliases for code that still uses the underscore-prefixed names
_TOOL_RESULT_LIMITS = TOOL_RESULT_LIMITS
_DEFAULT_TOOL_RESULT_LIMIT = DEFAULT_TOOL_RESULT_LIMIT
_BROWSER_CACHEABLE = BROWSER_CACHEABLE
_TAG_COMPAT_TOOLS = TAG_COMPAT_TOOLS
_SENSITIVE_TOOLS = SENSITIVE_TOOLS
_APPROVE_WORDS = APPROVE_WORDS
_DENY_WORDS = DENY_WORDS
_AGENT_READINESS_TIMEOUT_S = 20.0
_LLM_WARMUP_MAX_TOKENS = 16

# Host-owned search. Legacy names still execute as web_search internally.
_SEARCH_TOOLS = SEARCH_TOOL_NAMES

from core.paths import PERSONA_DIR, USERS_DIR, MEMORY_DIR


class AgentLoop:
    """Agent loop supporting interactive persona setup and user context."""

    @property
    def model(self) -> str:
        return str(get_current_model(getattr(self, "_model", "")) or "")

    @model.setter
    def model(self, value: str) -> None:
        self._model = str(value or "")

    @property
    def pending_confirmations(self) -> Dict[str, Dict[str, Any]]:
        manager = getattr(self, "confirm", None)
        if manager is not None:
            return manager.pending_confirmations
        return getattr(self, "_pending_confirmations", {})

    @pending_confirmations.setter
    def pending_confirmations(self, value: Dict[str, Dict[str, Any]]) -> None:
        self._pending_confirmations = value
        manager = getattr(self, "confirm", None)
        if manager is not None:
            manager.pending_confirmations = value

    @property
    def session_whitelists(self) -> Dict[str, Set[str]]:
        manager = getattr(self, "confirm", None)
        if manager is not None:
            return manager.session_whitelists
        return getattr(self, "_session_whitelists", {})

    @session_whitelists.setter
    def session_whitelists(self, value: Dict[str, Set[str]]) -> None:
        self._session_whitelists = value
        manager = getattr(self, "confirm", None)
        if manager is not None:
            manager.session_whitelists = value

    def __init__(
        self,
        bus: MessageBus,
        model: str = "gpt-3.5-turbo",
        scheduler: Any = None,
        session_manager: Optional[SessionManager] = None,
        llm_client: Optional[LimeLLMClient] = None,
    ):
        self.bus = bus
        self.model = model
        self.scheduler = scheduler
        self._running = False

        for d in (PERSONA_DIR, USERS_DIR, MEMORY_DIR):
            d.mkdir(exist_ok=True)

        self.history: Dict[str, List[Dict]] = {}
        self.session_locks: Dict[str, asyncio.Lock] = {}
        self._session_whitelists: Dict[str, Set[str]] = {}

        self.session_manager = session_manager or SessionManager()
        self.metrics = MetricsCollector()
        self.llm_client = llm_client or LimeLLMClient()
        self.tool_cache = ToolCache()
        self._pending_confirmations: Dict[str, Dict[str, Any]] = {}
        self.active_tasks: Dict[str, asyncio.Task] = {}
        # One registry owns every live turn and background job.  The legacy
        # maps below remain as compatibility indexes for channel adapters, but
        # lifecycle operations use this registry as the source of truth.
        self.task_registry = ManagedTaskRegistry()
        self.job_queue = get_job_queue()
        self.task_runs = get_task_run_store()
        self._queue_worker_id = uuid.uuid4().hex
        # Background subagents are durable TaskTracker records plus live
        # asyncio handles. The handle map is intentionally separate from
        # active_tasks because these jobs outlive the parent turn.
        self.background_subagent_tasks: Dict[str, asyncio.Task] = {}
        self.background_subagent_sessions: Dict[str, str] = {}
        self.background_subagent_parents: Dict[str, str] = {}
        self.background_subagent_results: Dict[str, str] = {}
        self._recovery_states: Dict[str, RecoveryState] = {}
        # Tool outcomes are scoped by turn.  The legacy list remains for
        # callers/tests that use the batch helper directly, but a concurrent
        # session must never read another session's last batch.
        self._last_tool_outcomes_by_turn: Dict[str, List[ToolOutcome]] = {}

        self._history_dirty: Dict[str, bool] = {}

        self._last_msg_hash: Dict[str, Tuple[int, float]] = {}

        cfg = load_config()
        self.config = cfg
        if cfg.llm.model:
            self.model = cfg.llm.model
        self.profile_registry = get_profile_registry(cfg)
        self.profile_coordinator = ProfileCoordinator(
            self.profile_registry,
            default_model=lambda: getattr(self, "_model", ""),
        )
        self.primary_model = self.model
        self.fallback_models = list(getattr(cfg.llm, "fallback_models", []) or [])
        self.provider_circuit_breaker = ProviderCircuitBreaker()

        self._provider: Tuple = self._resolve_provider()

        self.toolbox = Toolbox(
            allowed_paths=cfg.whitelist.allowed_paths, bus=bus, config=cfg
        )
        self.toolbox.set_agent(self)
        if self.scheduler:
            self.toolbox.set_scheduler(self.scheduler)

        self.browser_search_service = BrowserSearchService(
            manager_provider=lambda session_key: get_browser_manager(
                session_key=session_key, config=self.config
            ),
            report_progress=lambda message: self.toolbox.send_progress(message),
            attach_chat_image=lambda image_url, caption: self.toolbox.attach_chat_image(
                image_url, caption
            ),
        )
        self.browser_search_adapter = BrowserSearchToolAdapter(
            self.browser_search_service
        )

        self.vector_service = get_vector_service(cfg)

        self._tool_registry: Dict[str, Any] = {
            "read_file": self.toolbox.read_file,
            "edit_file": self.toolbox.edit_file,
            "write_file": self.toolbox.write_file,
            "create_spreadsheet": self.toolbox.create_spreadsheet,
            "calculate": self.toolbox.calculate,
            "delete_file": self.toolbox.delete_file,
            "list_dir": self.toolbox.list_dir,
            "search_files": self.toolbox.search_files,
            "verify_files": self.toolbox.verify_files,
            "diagnose_files": self.toolbox.diagnose_files,
            "run_command": self.toolbox.run_command,
            "run_steps": self.toolbox.run_steps,
            "apply_workspace_changeset": self.toolbox.apply_workspace_changeset,
            "memory_search": self.toolbox.memory_search,
            "memory_save": self.toolbox.memory_save,
            "send_media": self.toolbox.send_media,
            "send_voice": self.toolbox.send_voice,
            "generate_image": self.toolbox.generate_image,
            "analyze_video": self.toolbox.analyze_video,
            "send_discord_message": self.toolbox.send_discord_message,
            "send_discord_embed": self.toolbox.send_discord_embed,
            "list_discord_channels": self.toolbox.list_discord_channels,
            "cron_add": self.toolbox.cron_add,
            "cron_list": self.toolbox.cron_list,
            "cron_remove": self.toolbox.cron_remove,
            "create_skill": self.toolbox.create_skill,
            "inspect_skill": self.toolbox.inspect_skill,
            "edit_skill": self.toolbox.edit_skill,
        }

        self.skill_registry = SkillRegistry(skill_dirs=[str(path) for path in get_skill_dirs()], config=cfg)
        self.subagent_registry = SubagentRegistry()
        self.toolbox.set_subagent_registry(self.subagent_registry)
        self.prompt_coordinator = PromptCoordinator(
            config=self.config,
            model=lambda: self.model,
            allowed_paths=lambda: self.toolbox.allowed_paths,
            skill_registry=self.skill_registry,
            subagent_registry=self.subagent_registry,
            get_capability_turn_context=self._get_capability_turn_context,
            capability_catalog_prompt=self._capability_catalog_prompt,
        )

        # ── Sub-module managers ──────────────────────────────────────────
        self.confirm = ConfirmationManager(
            toolbox=self.toolbox,
            truncate_fn=self._truncate_preview,
            safe_json_load_fn=self._safe_json_load,
            pending_confirmations=self._pending_confirmations,
            session_whitelists=self._session_whitelists,
        )
        self.rag = RagEngine(
            truncate_fn=self._truncate_preview,
            safe_json_load_fn=self._safe_json_load,
        )

        self._tool_definitions: Optional[List[Dict]] = None
        self._warmed = False
        self._readiness_started_at = time.perf_counter()
        self._readiness_phase = "created"
        self._readiness_phase_history = ["created"]
        self._readiness_status = "starting"
        self._readiness_degraded_reasons: List[str] = []
        self._readiness_failure_code: Optional[str] = None
        self._readiness_event = asyncio.Event()
        self._initialization_task = asyncio.create_task(
            self._initialize_capabilities()
        )

        # Per-session task capability context.  The latest substantive request
        # remains available when the next utterance is only an acknowledgement
        # or shorthand follow-up ("yes, that one", "sí la tienes", etc.).
        self._session_capability_state: Dict[str, Dict[str, Any]] = {}
        self._capability_snapshot_revision = 0
        self._mcp_snapshot_signature: Tuple[Any, ...] = ()
        self._history_flush_interval = 5.0
        self._last_history_flush: Dict[str, float] = {}
        self._image_input_fallback_sessions: Set[str] = set()
        self._sessions_pending_tool_image_reply: Set[str] = set()
        self._recent_image_attachments: Dict[
            str, Tuple[float, List[Dict[str, Any]]]
        ] = {}
        self._turn_attachments: Dict[str, List[Dict[str, Any]]] = {}
        self.tool_runtime = ToolRuntime(
            toolbox=self.toolbox,
            tool_registry=self._tool_registry,
            tool_cache=self.tool_cache,
            metrics=self.metrics,
            hooks=ToolRuntimeHooks(
                background_task=lambda *args: self._execute_background_task_tool(
                    *args
                ),
                search=self.browser_search_adapter.search_tool,
                browser=self.browser_search_adapter.browser_tool,
                tag_compat=lambda *args: self._execute_tag_compat_tool(*args),
            ),
        )
        self.tool_batch_executor = ToolBatchExecutor()

    def _set_readiness_phase(self, phase: str) -> None:
        self._readiness_phase = phase
        if not self._readiness_phase_history or self._readiness_phase_history[-1] != phase:
            self._readiness_phase_history.append(phase)
        logger.debug(f"Agent readiness phase: {phase}")

    def _add_readiness_degradation(self, reason: str) -> None:
        if reason not in self._readiness_degraded_reasons:
            self._readiness_degraded_reasons.append(reason)

    def get_readiness_status(self) -> Dict[str, Any]:
        ready = self._readiness_status in {"ready", "degraded"}
        return {
            "status": self._readiness_status,
            "phase": self._readiness_phase,
            "ready": ready,
            "elapsed_ms": int(
                max(0.0, time.perf_counter() - self._readiness_started_at) * 1000
            ),
            "degraded_reasons": list(self._readiness_degraded_reasons),
            "failure_code": self._readiness_failure_code,
        }

    async def await_ready(self, timeout: float = _AGENT_READINESS_TIMEOUT_S) -> Dict[str, Any]:
        if not self._readiness_event.is_set():
            try:
                await asyncio.wait_for(self._readiness_event.wait(), timeout=timeout)
            except asyncio.TimeoutError:
                status = self.get_readiness_status()
                return {
                    **status,
                    "status": "timeout",
                    "ready": False,
                    "failure_code": "agent_readiness_timeout",
                }
        return self.get_readiness_status()

    async def _initialize_capabilities(self) -> None:
        try:
            await self._init_skills_and_tools()
            if self._readiness_degraded_reasons:
                self._readiness_status = "degraded"
                self._set_readiness_phase("degraded")
            else:
                self._readiness_status = "ready"
                self._set_readiness_phase("ready")
        except asyncio.CancelledError:
            self._readiness_status = "failed"
            self._readiness_failure_code = "initialization_cancelled"
            self._set_readiness_phase("failed")
            raise
        except Exception:
            self._readiness_status = "failed"
            self._readiness_failure_code = "required_capability_initialization_failed"
            self._set_readiness_phase("failed")
            logger.exception("Required agent capability initialization failed")
        finally:
            self._readiness_event.set()

    async def _init_skills_and_tools(self) -> None:
        """Background: discover skills, build tool definitions, then warm up slow services."""
        self._set_readiness_phase("skills")
        await asyncio.to_thread(self.skill_registry.discover_and_load)
        self._set_readiness_phase("subagents")
        await asyncio.to_thread(self.subagent_registry.discover_and_load)
        asyncio.create_task(self._cleanup_persisted_histories())
        asyncio.create_task(self._reap_temp_voice_files_loop())

        # Initialize MCP servers if available
        self._set_readiness_phase("mcp")
        try:
            from core.mcp_client import get_mcp_manager

            await asyncio.wait_for(get_mcp_manager().initialize(), timeout=8.0)
        except Exception as e:
            logger.error(f"Failed to initialize MCP servers: {e}")
            self._add_readiness_degradation("mcp_unavailable")

        self._set_readiness_phase("tools")
        self._refresh_tool_definitions()
        # Pre-initialize LanceDB and HTTP connection pools so the first user
        # message doesn't pay the cold-start penalty.
        asyncio.create_task(self._warm_up_services())

    async def _warm_up_services(self) -> None:

        if self._warmed:
            return
        self._warmed = True

        logger.info("🔥 Warming up services…")

        try:
            await self.vector_service._ensure_init()
            logger.info("✅ LanceDB pre-initialized.")
        except Exception as e:
            logger.warning(f"⚠ LanceDB warmup failed (non-critical): {e}")

        try:
            emb = await self.vector_service._get_embedding("hi")
            if emb is not None:
                logger.info("✅ Embedding API connection warmed.")
            else:
                logger.warning(
                    "⚠ Embedding warmup failed (check API keys). Using keyword fallback."
                )
        except Exception as e:
            if prompt_module.is_setup_complete():
                logger.warning(f"⚠ Embedding warmup error (non-critical): {e}")
            else:
                logger.debug(f"Embedding warmup skipped/failed during setup: {e}")

        # 3. Warm the chat LLM HTTP connection. OpenAI Responses models reject
        # max_output_tokens values below 16, so use that cross-provider floor.
        try:
            provider = self.llm_client.resolve_provider(
                self.primary_model,
                default_base_url=self.config.llm.base_url,
            )
            await self.llm_client.complete(
                provider,
                ChatRequest(
                    messages=[{"role": "user", "content": "hi"}],
                    max_tokens=_LLM_WARMUP_MAX_TOKENS,
                    session_id="__warmup__",
                ),
            )
            if provider.is_codex:
                logger.info("âœ… Codex connection warmed.")
                return
            logger.info("âœ… LLM connection pool warmed.")
            return
        except Exception as e:
            # Warmup failure is never fatal — log and continue
            if prompt_module.is_setup_complete():
                logger.warning(f"⚠ LLM warmup failed (non-critical): {e}")
            else:
                logger.debug(f"LLM warmup skipped/failed during setup: {e}")

    def _refresh_tool_definitions(self) -> None:
        """Rebuild and cache tool definitions. Call only when skills change."""
        self._tool_definitions = self.toolbox.get_tool_definitions()
        self._get_prompt_coordinator().clear_cache()
        if hasattr(self, "tool_cache"):
            self.tool_cache.clear()
        self._capability_snapshot_revision = int(
            getattr(getattr(self, "skill_registry", None), "capability_revision", 0)
            or 0
        )
        self._mcp_snapshot_signature = self._get_mcp_snapshot_signature()

        logger.debug(
            f"Tool definitions refreshed ({len(self._tool_definitions)} tools)."
        )

    @staticmethod
    def _get_mcp_snapshot_signature() -> Tuple[Any, ...]:
        try:
            from core.mcp_client import get_mcp_manager

            manager = get_mcp_manager()
            tool_names = tuple(
                sorted(
                    str(tool.get("function", {}).get("name") or "")
                    for tool in (manager.get_tools() or [])
                    if isinstance(tool, dict)
                )
            )
            status = tuple(sorted((manager.get_status() or {}).items()))
            return tool_names, status
        except Exception:
            return ()

    def _get_tool_definitions(self) -> List[Dict]:
        skill_revision = int(
            getattr(getattr(self, "skill_registry", None), "capability_revision", 0)
            or 0
        )
        mcp_signature = self._get_mcp_snapshot_signature()
        snapshot_revision = int(
            getattr(self, "_capability_snapshot_revision", 0) or 0
        )
        if (
            self._tool_definitions is None
            or (
                snapshot_revision > 0
                and skill_revision != snapshot_revision
            )
            or (
                snapshot_revision > 0
                and mcp_signature != getattr(self, "_mcp_snapshot_signature", ())
            )
        ):
            self._refresh_tool_definitions()
        return self._tool_definitions

    def _get_capability_service(self) -> CapabilityService:
        service = getattr(self, "capability_service", None)
        if service is None:
            service = CapabilityService(
                get_skill_registry=lambda: getattr(self, "skill_registry", None),
                get_subagent_registry=lambda: getattr(self, "subagent_registry", None),
                get_session_state=lambda: getattr(
                    self, "_session_capability_state", {}
                ),
                get_tool_definitions=lambda: self._get_tool_definitions(),
                tool_definition_names=lambda tools: self._tool_definition_names(tools),
                log_tool_debug=lambda *args, **kwargs: self._log_tool_debug(
                    *args, **kwargs
                ),
                get_capability_turn_context=lambda *args, **kwargs: self._get_capability_turn_context(
                    *args, **kwargs
                ),
                get_tool_definitions_for_turn=lambda *args, **kwargs: self._get_tool_definitions_for_turn(
                    *args, **kwargs
                ),
            )
            self.capability_service = service
        return service

    def _get_capability_turn_context(
        self, session_key: Optional[str], user_text: str
    ) -> Dict[str, Any]:
        return self._get_capability_service().get_capability_turn_context(
            session_key, user_text
        )

    def _capability_catalog_prompt(self) -> str:
        return self._get_capability_service().capability_catalog_prompt()

    def _get_tool_definitions_for_turn(
        self,
        user_text: str = "",
        forced_skill_name: Optional[str] = None,
        session_key: Optional[str] = None,
        attachments: Optional[List[Dict[str, Any]]] = None,
    ) -> List[Dict]:
        return self._get_capability_service().get_tool_definitions_for_turn(
            user_text,
            forced_skill_name=forced_skill_name,
            session_key=session_key,
            attachments=attachments,
        )

    def _filter_tool_definitions_for_subagent(
        self,
        tool_names: Optional[List[str]],
        disallowed_tool_names: Optional[List[str]] = None,
    ) -> List[Dict]:
        tools = list(self._get_tool_definitions())
        if tool_names is not None:
            allowed = {
                normalize_subagent_tool_name(name)
                for name in tool_names
                if normalize_subagent_tool_name(name)
            }
            tools = [
                tool
                for tool in tools
                if tool.get("function", {}).get("name") in allowed
            ]

        if disallowed_tool_names:
            disallowed = {
                normalize_subagent_tool_name(name)
                for name in disallowed_tool_names
                if normalize_subagent_tool_name(name)
            }
            tools = [
                tool
                for tool in tools
                if tool.get("function", {}).get("name") not in disallowed
            ]

        return tools

    def _resolve_provider(
        self,
    ) -> Tuple[str, Optional[str], Optional[str], Optional[str]]:
        """
        Return (model, base_url, api_key, custom_llm_provider).
        Called once at init; call again via set_model() when the model changes.
        """
        provider = self.llm_client.resolve_provider(
            self.model,
            default_base_url=self.config.llm.base_url,
        )
        return (
            provider.model,
            provider.base_url,
            provider.api_key,
            provider.custom_llm_provider,
        )

    def _resolve_provider_chain(
        self,
    ) -> List[Tuple[str, str, Optional[str], Optional[str], Optional[str]]]:
        chain = self.llm_client.resolve_chain(
            self.model,
            getattr(self, "fallback_models", []) or [],
            default_base_url=self.config.llm.base_url,
        )
        return [
            (
                provider.source_model,
                provider.model,
                provider.base_url,
                provider.api_key,
                provider.custom_llm_provider,
            )
            for provider in chain
        ]

    def _get_provider_circuit_breaker(self) -> ProviderCircuitBreaker:
        """Lazily provide a breaker for lightweight test/embedded instances."""
        breaker = getattr(self, "provider_circuit_breaker", None)
        if breaker is None:
            breaker = ProviderCircuitBreaker()
            self.provider_circuit_breaker = breaker
        return breaker

    @staticmethod
    def _provider_circuit_key(
        source_model: str,
        model: str,
        base_url: Optional[str],
        custom_llm_provider: Optional[str],
    ) -> str:
        return "|".join(
            str(value or "")
            for value in (source_model, model, base_url, custom_llm_provider)
        )

    def _provider_circuit_identity(
        self,
        source_model: str,
        model: str,
        base_url: Optional[str],
        api_key: Optional[str],
        custom_llm_provider: Optional[str],
    ) -> Tuple[str, str]:
        return (
            self._provider_circuit_key(
                source_model, model, base_url, custom_llm_provider
            ),
            ProviderCircuitBreaker.credential_fingerprint(api_key),
        )

    def set_model(self, model: str) -> None:
        """Switch the active model and refresh cached provider config."""
        self.model = model
        self.primary_model = model
        self._provider = self._resolve_provider()
        self._get_prompt_coordinator().clear_cache()
        self._image_input_fallback_sessions.clear()

    def _get_current_vector_service(self):
        profile_context = get_current_profile_context()
        if profile_context is not None and profile_context.profile_id != "default":
            return get_profile_vector_service(
                self.config, profile_id=profile_context.profile_id
            )
        return self.vector_service

    def _get_turn_preparation_service(self) -> TurnPreparationService:
        service = getattr(self, "turn_preparation_service", None)
        if service is None:
            service = TurnPreparationService(
                config=self.config,
                casual_words=_CASUAL_WORDS,
                get_vector_service=lambda: self._get_current_vector_service(),
                history_is_loaded=lambda session_key: session_key
                in getattr(self, "history", {}),
                load_history=lambda session_key: self.session_manager.load_history(
                    session_key
                ),
                publish_activity=lambda *args, **kwargs: self._publish_activity(
                    *args, **kwargs
                ),
                build_rag_result_trace=lambda result: self._build_rag_result_trace(
                    result
                ),
                record_rag_trace=lambda session_key, result: self._record_rag_trace(
                    session_key, result
                ),
                record_stage_timing=lambda *args, **kwargs: self._record_stage_timing(
                    *args, **kwargs
                ),
            )
            self.turn_preparation_service = service
        return service

    @staticmethod
    def _tool_cache_scope_args(function_args: dict) -> dict:
        return tool_cache_scope_args(function_args, get_current_profile_id())

    def get_llm_runtime_status(self) -> Dict[str, Any]:
        return {
            "configured_model": self.primary_model,
            "active_model": self.model,
            "fallback_models": list(self.fallback_models),
            "using_fallback": self.model != self.primary_model,
            "provider_circuits": self._get_provider_circuit_breaker().snapshot(),
        }

    @staticmethod
    def _should_failover_model(error: Exception) -> bool:
        if isinstance(
            error,
            (
                AuthenticationError,
                RateLimitError,
                InternalServerError,
                APIConnectionError,
                ServiceUnavailableError,
            ),
        ):
            return True

        text = str(error or "").lower()
        return any(
            marker in text
            for marker in (
                "incorrect api key",
                "invalid api key",
                "authentication",
                "auth failed",
                "rate limit",
                "service unavailable",
                "connection error",
                "timed out",
                "timeout",
                "model not found",
                "does not exist",
                "not available",
                "provider returned an error",
                "no visible response",
                "overloaded",
                "capacity",
            )
        )

    def _image_input_fallback_key(self, session_key: str) -> str:
        return f"{self.model}::{session_key}"

    def _image_inputs_disabled_for_session(self, session_key: str) -> bool:
        return self._image_input_fallback_key(session_key) in (
            self._image_input_fallback_sessions
        )

    def _disable_image_inputs_for_session(self, session_key: str) -> None:
        self._image_input_fallback_sessions.add(
            self._image_input_fallback_key(session_key)
        )

    @staticmethod
    def _render_text_only_message_content(content: Any) -> str:
        if not isinstance(content, list):
            return str(content or "")

        text_parts: List[str] = []
        image_notes: List[str] = []
        for item in content:
            if not isinstance(item, dict):
                continue
            if item.get("type") == "text":
                text = str(item.get("text", "") or "").strip()
                if text:
                    text_parts.append(text)
            elif item.get("type") == "image_url":
                url = str(item.get("image_url", {}).get("url", "") or "").strip()
                note = (
                    "[Image attachment shared, but the current model does not support vision]"
                )
                if url and not url.lower().startswith("data:"):
                    note = f"{note}: {url}"
                image_notes.append(note)

        combined = "\n".join(part for part in text_parts + image_notes if part)
        return combined or "[Image attachment shared, but the current model does not support vision]"

    @staticmethod
    def _join_message_sections(*sections: str) -> str:
        return "\n\n".join(
            section.strip() for section in sections if str(section or "").strip()
        )

    @staticmethod
    def _build_attachment_summary(attachments: List[Dict[str, Any]]) -> str:
        lines: List[str] = []
        image_count = 0
        document_count = 0
        for attachment in attachments:
            if not isinstance(attachment, dict):
                continue
            kind = str(attachment.get("kind") or "attachment").strip().lower()
            name = str(attachment.get("name") or kind or "attachment").strip()
            path = str(attachment.get("path") or "").strip()
            mime_type = str(
                attachment.get("mime_type") or attachment.get("mimeType") or ""
            ).strip()

            if attachment.get("kind") == "image":
                image_count += 1
                note = f"[Attached image {image_count}: {name}]"
                if mime_type:
                    note += f" Type: {mime_type}."
                note += (
                    " Already available in vision context — do not call "
                    "read_file or browser_navigate to view it."
                )
                lines.append(note)
                continue

            document_count += 1
            note = f"[Attached document {document_count}: {name}]"
            if mime_type:
                note += f" Type: {mime_type}."
            if path:
                note += f" Saved as `{path}`."
            if attachment.get("extracted_text"):
                note += " Text was extracted and included below."
            elif attachment.get("extraction_note"):
                note += f" Extraction note: {attachment.get('extraction_note')}."
            lines.append(note)

        if not lines:
            return ""
        header = (
            f"[Discord attachments received: {image_count} image(s), "
            f"{document_count} document(s)]"
        )
        return "\n".join([header] + lines)

    @staticmethod
    def _build_document_attachment_context(attachments: List[Dict[str, Any]]) -> str:
        sections: List[str] = []
        for attachment in attachments:
            if not isinstance(attachment, dict):
                continue
            if attachment.get("kind") != "document":
                continue

            name = str(attachment.get("name") or "document").strip()
            path = str(attachment.get("path") or "").strip()
            extracted_text = str(attachment.get("extracted_text") or "").strip()
            extraction_note = str(attachment.get("extraction_note") or "").strip()

            parts = [f"[Document attachment: {name}]"]
            if path:
                parts.append(f"Saved as: {path}")
            if extracted_text:
                parts.append("Extracted text:")
                parts.append(extracted_text)
            elif extraction_note:
                parts.append(f"Note: {extraction_note}")

            sections.append("\n".join(parts))

        return "\n\n".join(section for section in sections if section.strip())

    @staticmethod
    def _inline_local_tool_image(
        path_value: str,
        *,
        mime_type: str = "",
        name: str = "",
        source: str = "",
    ) -> Optional[Dict[str, str]]:
        raw_path = str(path_value or "").strip()
        if not raw_path:
            return None

        try:
            cwd = Path.cwd().resolve()
            candidate = Path(raw_path)
            resolved = (
                candidate.resolve()
                if candidate.is_absolute()
                else (cwd / candidate).resolve()
            )
            resolved.relative_to(cwd)
        except Exception:
            return None

        if not resolved.exists() or not resolved.is_file():
            return None

        inferred_mime = str(mime_type or "").strip() or (
            mimetypes.guess_type(resolved.name)[0] or ""
        )
        if not inferred_mime.lower().startswith("image/"):
            return None

        try:
            if resolved.stat().st_size > _TOOL_LOCAL_IMAGE_MAX_BYTES:
                return None
            encoded = base64.b64encode(resolved.read_bytes()).decode("ascii")
        except Exception:
            return None

        return {
            "url": f"data:{inferred_mime};base64,{encoded}",
            "name": str(name or resolved.name).strip(),
            "source": str(source or raw_path).strip(),
        }

    @classmethod
    def _extract_local_tool_images(cls, raw_text: str) -> List[Dict[str, str]]:
        raw = str(raw_text or "")
        parsed_candidates: List[Any] = []
        try:
            parsed_candidates.append(json.loads(raw))
        except Exception:
            pass

        if not parsed_candidates:
            for line in raw.splitlines():
                stripped = line.strip()
                if not stripped.startswith("{") or not stripped.endswith("}"):
                    continue
                try:
                    parsed_candidates.append(json.loads(stripped))
                except Exception:
                    continue

        if not parsed_candidates:
            return []

        images: List[Dict[str, str]] = []
        seen_paths: Set[str] = set()

        def _visit(node: Any, source_hint: str = "") -> None:
            if isinstance(node, dict):
                path_value = str(
                    node.get("saved_path")
                    or node.get("path")
                    or node.get("local_path")
                    or node.get("file_path")
                    or ""
                ).strip()
                if path_value and path_value not in seen_paths:
                    seen_paths.add(path_value)
                    image = cls._inline_local_tool_image(
                        path_value,
                        mime_type=str(
                            node.get("mime_type") or node.get("mimeType") or ""
                        ).strip(),
                        name=str(node.get("filename") or node.get("name") or "").strip(),
                        source=source_hint or "tool-downloaded local image",
                    )
                    if image:
                        images.append(image)

                next_source = source_hint
                issue_key = str(node.get("issue_key") or "").strip()
                if issue_key:
                    next_source = f"Jira attachment on {issue_key.upper()}"

                for value in node.values():
                    _visit(value, next_source)
                return

            if isinstance(node, list):
                for item in node:
                    _visit(item, source_hint)

        for parsed in parsed_candidates:
            _visit(parsed)
        return images

    @staticmethod
    def _extract_tool_media_payload(result: Any) -> Tuple[str, List[Dict[str, str]]]:
        raw_text = str(result or "")
        images: List[Dict[str, str]] = []
        summaries: List[str] = []

        cleaned_parts: List[str] = []
        cursor = 0
        while True:
            start = raw_text.find(_TOOL_MEDIA_PAYLOAD_START, cursor)
            if start < 0:
                cleaned_parts.append(raw_text[cursor:])
                break

            cleaned_parts.append(raw_text[cursor:start])
            payload_start = start + len(_TOOL_MEDIA_PAYLOAD_START)
            end = raw_text.find(_TOOL_MEDIA_PAYLOAD_END, payload_start)
            if end < 0:
                cleaned_parts.append(raw_text[start:])
                break

            payload_text = raw_text[payload_start:end].strip()
            try:
                payload = json.loads(payload_text)
            except Exception:
                payload = None

            if isinstance(payload, dict):
                summary = str(
                    payload.get("text") or payload.get("summary") or ""
                ).strip()
                if summary:
                    summaries.append(summary)

                for item in payload.get("images") or []:
                    if not isinstance(item, dict):
                        continue
                    url = str(item.get("url") or "").strip()
                    name = str(item.get("name") or "").strip()
                    source = str(item.get("source") or "").strip()
                    if url:
                        images.append(
                            {
                                "url": url,
                                "name": name,
                                "source": source,
                            }
                        )
                        continue

                    path = str(
                        item.get("saved_path")
                        or item.get("path")
                        or item.get("local_path")
                        or item.get("file_path")
                        or ""
                    ).strip()
                    if path:
                        image = AgentLoop._inline_local_tool_image(
                            path,
                            mime_type=str(
                                item.get("mime_type") or item.get("mimeType") or ""
                            ).strip(),
                            name=name,
                            source=source or "tool-downloaded local image",
                        )
                        if image:
                            images.append(image)

                if summary:
                    cleaned_parts.append(summary)
            else:
                cleaned_parts.append(raw_text[start : end + len(_TOOL_MEDIA_PAYLOAD_END)])

            cursor = end + len(_TOOL_MEDIA_PAYLOAD_END)

        cleaned = "".join(cleaned_parts)
        cleaned = re.sub(r"\n{3,}", "\n\n", cleaned).strip()

        if not images:
            images.extend(AgentLoop._extract_local_tool_images(cleaned or raw_text))

        if not cleaned and summaries:
            cleaned = "\n".join(summaries)
        if not cleaned and images:
            cleaned = f"Tool returned {len(images)} image attachment(s) for inspection."

        deduped_images: List[Dict[str, str]] = []
        seen_urls: Set[str] = set()
        for image in images:
            url = image.get("url", "")
            if not url or url in seen_urls:
                continue
            seen_urls.add(url)
            deduped_images.append(image)

        return cleaned, deduped_images

    @staticmethod
    def _build_tool_image_followup_content(
        function_name: str, images: List[Dict[str, str]]
    ) -> List[Dict[str, Any]]:
        selected = images[:3]
        names = [image.get("name", "").strip() for image in selected if image.get("name")]
        intro = (
            f"Internal tool-fetched image context from `{function_name}`. "
            "These images were retrieved during tool execution for the current task. "
            "Inspect them directly with your own vision before continuing. "
            "Do not treat this as a new user request, and do not call OCR or "
            "file-reading tools unless the user specifically asks for extraction "
            "or the image is unreadable."
        )
        if names:
            intro += f" Retrieved: {', '.join(names)}."
        if len(images) > len(selected):
            intro += f" Showing {len(selected)} of {len(images)} images."

        content: List[Dict[str, Any]] = [{"type": "text", "text": intro}]
        for image in selected:
            content.append(
                {
                    "type": "image_url",
                    "image_url": {"url": image["url"]},
                }
            )
        return content

    @staticmethod
    def _should_suppress_web_final_reply(
        *,
        channel: str,
        any_tool_calls_in_turn: bool,
        iterations_limit_reached: bool,
        web_streamed_reply: bool,
        force_direct_reply: bool,
        reply_to_user: str,
        raw_reply: str,
    ) -> bool:
        if channel != "web":
            return False
        if not any_tool_calls_in_turn:
            return False
        if iterations_limit_reached or not web_streamed_reply or force_direct_reply:
            return False
        normalized_final = AgentLoop._normalize_user_visible_reply_for_compare(
            reply_to_user
        )
        if _GHOST_TAG_RE.search(raw_reply or ""):
            tag_names = "|".join(_GHOST_TAG_NAMES)
            visible_stream = re.sub(
                rf"<(?:{tag_names})[^>]*>.*?</(?:{tag_names})>",
                "",
                raw_reply,
                flags=re.DOTALL | re.IGNORECASE,
            )
            visible_stream = _GHOST_TAG_RE.sub("", visible_stream)
            normalized_visible_stream = re.sub(
                r"\s+", " ", visible_stream
            ).strip()
            return bool(
                normalized_final
                and normalized_visible_stream.count(normalized_final) >= 2
            )
        normalized_streamed = AgentLoop._normalize_user_visible_reply_for_compare(
            raw_reply
        )
        if not normalized_final or not normalized_streamed:
            return False
        return (
            normalized_final == normalized_streamed
            or normalized_final in normalized_streamed
        )

    @staticmethod
    def _resolve_voice_delivery(
        channel: str, voice_cfg: Dict[str, Any], voice_on: bool, has_reply: bool
    ) -> str:
        """Decide how a reply is delivered given the voice config.

        Returns one of: "audio_only", "audio_and_text" (Discord/WhatsApp),
        "web_url" (attach a playable URL to the web text), or "text".
        """
        if not (voice_on and has_reply):
            return "text"
        channels = voice_cfg.get("channels", ["web"])
        if channel in ("discord", "whatsapp") and channel in channels:
            return (
                "audio_and_text"
                if voice_cfg.get("send_text_with_audio", False)
                else "audio_only"
            )
        if channel == "web" and "web" in channels:
            return "web_url"
        return "text"

    async def _reap_temp_voice_files_loop(self) -> None:
        """Periodically delete stale synthesized voice mp3s under temp/.

        Discord/WhatsApp voice sends clean up immediately after sending, but
        the web channel's voice_url leaves a streamed file with no signal for
        when the browser is done playing it. This is that TTL-based cleanup.
        """
        from core.tts import ElevenLabsTTS

        while True:
            try:
                await asyncio.to_thread(ElevenLabsTTS.purge_stale_audio)
            except Exception as e:
                logger.debug(f"[TTS] Temp voice file reaper skipped a pass: {e}")
            await asyncio.sleep(1800)  # every 30 minutes

    async def _synthesize_voice_file(self, text: str) -> Optional[str]:
        """Return a temp mp3 path for `text` if voice is enabled and keyed, else None."""
        try:
            from core.tts import ElevenLabsTTS

            cfg = ElevenLabsTTS.get_voice_config()
            if not cfg.get("enabled", False) or not ElevenLabsTTS.get_api_key():
                return None
            path = await ElevenLabsTTS.synthesize_to_file(text)
            return path or None
        except Exception as e:
            logger.error(f"[TTS] Voice synthesis failed: {e}")
            return None

    def _get_conversation_image_service(self) -> ConversationImageService:
        service = getattr(self, "conversation_image_service", None)
        if service is None:
            service = ConversationImageService(
                recent_attachments=lambda: getattr(
                    self, "_recent_image_attachments", {}
                ),
                histories=lambda: getattr(self, "history", {}),
                mark_dirty=lambda session_key: self._mark_dirty(session_key),
                record_anomaly=lambda *args, **kwargs: self.metrics.record_anomaly(
                    *args, **kwargs
                ),
                current_model=lambda: str(getattr(self, "model", "")),
                render_text_only=lambda content: self._render_text_only_message_content(
                    content
                ),
                recent_reference_ttl_seconds=_RECENT_IMAGE_REFERENCE_TTL_S,
            )
            self.conversation_image_service = service
        return service

    @staticmethod
    def _messages_have_image_inputs(messages: List[Dict[str, Any]]) -> bool:
        return ConversationImageService.messages_have_image_inputs(messages)

    @staticmethod
    def _collect_image_inputs(
        attachments: List[Dict[str, Any]],
        metadata_images: Any = None,
        legacy_image: str = "",
    ) -> List[str]:
        return ConversationImageService.collect_image_inputs(
            attachments, metadata_images, legacy_image
        )

    def _remember_image_attachments(
        self, session_key: str, attachments: List[Dict[str, Any]]
    ) -> None:
        self._get_conversation_image_service().remember_image_attachments(
            session_key, attachments
        )

    def _get_recent_image_attachments(
        self, session_key: str
    ) -> List[Dict[str, Any]]:
        return self._get_conversation_image_service().get_recent_image_attachments(
            session_key
        )

    @staticmethod
    def _message_refers_to_recent_image(content: str) -> bool:
        return ConversationImageService.message_refers_to_recent_image(content)

    @staticmethod
    def _image_safe_history_content(content: Any) -> Any:
        return ConversationImageService.image_safe_history_content(content)

    @classmethod
    def _history_summary_payload(cls, messages: List[Dict[str, Any]]) -> str:
        return ConversationImageService.history_summary_payload(messages)

    @staticmethod
    def _tool_call_id(tool_call: Any) -> str:
        """Return the stable provider ID from a dict or SDK tool-call object."""
        if isinstance(tool_call, dict):
            return str(
                tool_call.get("id") or tool_call.get("tool_call_id") or ""
            ).strip()
        return str(
            getattr(tool_call, "id", None)
            or getattr(tool_call, "tool_call_id", None)
            or ""
        ).strip()

    @classmethod
    def _normalize_tool_history_messages(
        cls,
        messages: List[Dict[str, Any]],
        *,
        tool_output_limit: int = _HISTORY_TOOL_OUTPUT_MAX_CHARS,
    ) -> Tuple[List[Dict[str, Any]], bool]:
        """Repair tool-call/result pairing and cap persisted tool output.

        Providers require every assistant ``tool_calls`` message to be followed
        by one tool result per call.  A process crash, cancellation, or partial
        history write can violate that contract and make every subsequent turn
        fail before LimeBot can recover.  This normalizer inserts deterministic
        synthetic results for missing calls, drops orphan tool rows, and keeps
        all tool content within a bounded character budget.
        """
        normalized: List[Dict[str, Any]] = []
        pending: Dict[str, str] = {}
        changed = False

        def flush_pending() -> None:
            nonlocal changed
            if not pending:
                return
            for tool_call_id, tool_name in pending.items():
                normalized.append(
                    {
                        "role": "tool",
                        "tool_call_id": tool_call_id,
                        "name": tool_name or "tool",
                        "content": _HISTORY_INTERRUPTED_TOOL_MESSAGE,
                    }
                )
            pending.clear()
            changed = True

        for original in messages or []:
            if not isinstance(original, dict):
                changed = True
                continue

            role = str(original.get("role") or "").strip().lower()
            if role == "assistant" and isinstance(original.get("tool_calls"), list):
                # A new assistant turn closes any previous incomplete batch.
                flush_pending()
                message = dict(original)
                valid_calls: List[Dict[str, Any]] = []
                for raw_call in original.get("tool_calls") or []:
                    if isinstance(raw_call, dict):
                        tool_call = dict(raw_call)
                    elif hasattr(raw_call, "model_dump"):
                        tool_call = raw_call.model_dump()
                    else:
                        continue
                    tool_call_id = cls._tool_call_id(tool_call)
                    if not tool_call_id or tool_call_id in pending:
                        changed = True
                        continue
                    function = tool_call.get("function")
                    tool_name = (
                        function.get("name")
                        if isinstance(function, dict)
                        else tool_call.get("name")
                    )
                    pending[tool_call_id] = str(tool_name or "tool")
                    valid_calls.append(tool_call)

                if valid_calls:
                    message["tool_calls"] = valid_calls
                else:
                    message.pop("tool_calls", None)
                    if original.get("tool_calls"):
                        changed = True
                if message != original:
                    changed = True
                normalized.append(message)
                continue

            if role == "tool":
                tool_call_id = str(original.get("tool_call_id") or "").strip()
                if not tool_call_id or tool_call_id not in pending:
                    # Orphan/duplicate tool rows are invalid upstream and are
                    # safer to discard than to let them poison the next call.
                    changed = True
                    continue
                message = dict(original)
                bounded_content = truncate_tool_result(
                    message.get("content", ""), tool_output_limit
                )
                if bounded_content != str(message.get("content", "")):
                    changed = True
                message["content"] = bounded_content
                normalized.append(message)
                pending.pop(tool_call_id, None)
                continue

            flush_pending()
            normalized.append(original)

        flush_pending()
        return normalized, changed

    def _normalize_history_in_place(self, session_key: str) -> List[Dict[str, Any]]:
        """Normalize and persist the in-memory history for one session."""
        history = self.history.get(session_key, [])
        normalized, changed = self._normalize_tool_history_messages(history)
        if changed:
            self._history_replace(session_key, normalized, mark_dirty=True)
        return self.history.get(session_key, normalized)

    def _evict_history_image_inputs(self, session_key: str) -> None:
        self._get_conversation_image_service().evict_history_image_inputs(
            session_key
        )

    def _truncate_history_fallback(
        self, conv: List[Dict[str, Any]], target_tokens: int
    ) -> List[Dict[str, Any]]:
        """Trim old turns without deleting the latest user request."""
        current_tokens = self._estimate_tokens(conv)

        while conv and current_tokens > target_tokens:
            latest_user_index = max(
                (
                    index
                    for index, message in enumerate(conv)
                    if message.get("role") == "user"
                ),
                default=len(conv),
            )
            if latest_user_index <= 0:
                break

            popped = conv.pop(0)
            current_tokens -= self._estimate_tokens([popped])

            if popped.get("role") == "assistant" and popped.get("tool_calls"):
                while conv and conv[0].get("role") == "tool":
                    tool_msg = conv.pop(0)
                    current_tokens -= self._estimate_tokens([tool_msg])
            while conv and conv[0].get("role") == "tool":
                tool_msg = conv.pop(0)
                current_tokens -= self._estimate_tokens([tool_msg])

        return conv

    def _downgrade_image_messages_for_text_model(
        self, messages: List[Dict[str, Any]], session_key: str
    ) -> bool:
        return self._get_conversation_image_service().downgrade_image_messages_for_text_model(
            messages, session_key
        )

    def _should_retry_without_images(
        self, error: Exception, messages: List[Dict[str, Any]]
    ) -> bool:
        return self._get_conversation_image_service().should_retry_without_images(
            error, messages
        )

    async def _get_stable_prompt(
        self, sender_id: str, channel: str, chat_id: str, sender_name: str = ""
    ) -> str:
        """Compatibility boundary for callers that request the stable prompt."""
        return await self._get_prompt_coordinator().get_stable_prompt(
            sender_id, channel, chat_id, sender_name
        )

    def _invalidate_stable_prompt(self, sender_id: str) -> None:
        """Drop cached prompts for this sender. Call after soul/identity updates."""
        self._get_prompt_coordinator().invalidate(sender_id)

    def _get_prompt_coordinator(self) -> PromptCoordinator:
        coordinator = getattr(self, "prompt_coordinator", None)
        if coordinator is None:
            coordinator = PromptCoordinator(
                config=getattr(self, "config", None),
                model=lambda: str(getattr(self, "model", "")),
                allowed_paths=lambda: getattr(
                    getattr(self, "toolbox", None), "allowed_paths", []
                ),
                skill_registry=getattr(self, "skill_registry", None),
                subagent_registry=getattr(self, "subagent_registry", None),
                get_capability_turn_context=getattr(
                    self,
                    "_get_capability_turn_context",
                    lambda _session_key, message: {"routing_text": message},
                ),
                capability_catalog_prompt=getattr(
                    self, "_capability_catalog_prompt", lambda: ""
                ),
            )
            self.prompt_coordinator = coordinator
        return coordinator

    def _log_session_event(self, session_key: str, event: dict) -> None:
        try:
            payload = dict(event or {})
            task_id = _CURRENT_TASK_ID.get()
            if task_id:
                payload.setdefault("task_id", task_id)
            profile_context = get_current_profile_context()
            payload.setdefault(
                "profile_id",
                profile_context.profile_id if profile_context else get_current_profile_id(),
            )
            if profile_context is not None:
                payload.setdefault("profile_name", profile_context.profile.display_name)
            asyncio.create_task(
                self.session_manager.append_event_log(session_key, payload)
            )
        except Exception:
            pass

    async def _build_full_system_prompt(
        self,
        sender_id: str,
        channel: str,
        chat_id: str,
        recalled_context: str = "",
        sender_name: str = "",
        current_message: str = "",
        forced_skill_name: Optional[str] = None,
        ponytail_mode: str = "off",
        session_key: Optional[str] = None,
    ) -> str:
        """Stable (cached) + volatile (per-message: memory + RAG + timestamp)."""
        return await self._get_prompt_coordinator().build_full_prompt(
            sender_id=sender_id,
            channel=channel,
            chat_id=chat_id,
            recalled_context=recalled_context,
            sender_name=sender_name,
            current_message=current_message,
            forced_skill_name=forced_skill_name,
            ponytail_mode=ponytail_mode,
            session_key=session_key,
            # Preserve the wrapper as an interception point for adapters/tests.
            stable_prompt_getter=self._get_stable_prompt,
        )

    def _resolve_skill_invocation(
        self, content: str
    ) -> Tuple[str, Optional[str], Optional[str]]:
        parsed = parse_skill_invocation(content)
        if parsed.kind == "none":
            return content, None, None

        if parsed.kind == "inventory":
            return "what skills do you have right now", None, None

        return self._resolve_requested_skill(
            parsed.requested_name,
            parsed.task,
            raw_content=content,
        )

    def _resolve_requested_skill(
        self, requested_name: str, task: str = "", raw_content: Optional[str] = None
    ) -> Tuple[str, Optional[str], Optional[str]]:
        resolved_name = self.skill_registry.resolve_active_skill_name(
            requested_name
        )
        if not resolved_name:
            active_skills = self.skill_registry.list_active_skill_names()
            if active_skills:
                active_text = ", ".join(f"`{name}`" for name in active_skills)
                error = (
                    f"Unknown or inactive skill `{requested_name}`. "
                    f"Active skills: {active_text}."
                )
            else:
                error = (
                    f"Unknown or inactive skill `{requested_name}`. "
                    "No active skills are enabled right now."
                )
            return raw_content if raw_content is not None else task, None, error

        task = task.strip() or f"Use the {resolved_name} skill."
        return task, resolved_name, None

    async def _cleanup_persisted_histories(self) -> None:
        """Best-effort cleanup of malformed assistant residue in persisted sessions."""
        try:
            summary = await asyncio.to_thread(
                self.session_manager.cleanup_history_artifacts
            )
        except Exception as e:
            logger.debug(f"History cleanup skipped: {e}")
            return

        cleaned_total = summary.get("history_entries", 0) + summary.get(
            "log_entries", 0
        )
        if not cleaned_total:
            return

        logger.info(
            "🧹 Cleaned persisted malformed chat residue: "
            f"{summary.get('history_entries', 0)} history entr"
            f"{'y' if summary.get('history_entries', 0) == 1 else 'ies'}, "
            f"{summary.get('log_entries', 0)} log entr"
            f"{'y' if summary.get('log_entries', 0) == 1 else 'ies'}."
        )
        self.metrics.record_anomaly(
            "system",
            "history_cleanup",
            detail=str(summary),
            count=cleaned_total,
        )

    @staticmethod
    def _with_trace_metadata(
        metadata: Optional[Dict[str, Any]] = None,
        turn_id: Optional[str] = None,
        message_id: Optional[str] = None,
        task_id: Optional[str] = None,
    ) -> Dict[str, Any]:
        payload = dict(metadata or {})
        if turn_id:
            payload.setdefault("turn_id", turn_id)
        if message_id:
            payload.setdefault("message_id", message_id)
        resolved_task_id = task_id or _CURRENT_TASK_ID.get()
        if resolved_task_id:
            payload.setdefault("task_id", resolved_task_id)
        profile_context = get_current_profile_context()
        payload.setdefault(
            "profile_id",
            profile_context.profile_id if profile_context else get_current_profile_id(),
        )
        if profile_context is not None:
            payload.setdefault("profile_name", profile_context.profile.display_name)
        payload.setdefault("event_id", f"evt_{uuid.uuid4().hex[:20]}")
        return payload

    @staticmethod
    def _derive_turn_status(
        task_run: Any,
        *,
        continuation_scheduled: bool = False,
        unresolved_tool_failure: bool = False,
        override: Optional[str] = None,
    ) -> str:
        """Map internal task state to the small, visible turn vocabulary."""

        if override:
            return normalize_turn_status(override, default="failed")
        if continuation_scheduled:
            return "retrying"
        task_status = str(getattr(task_run, "status", "") or "").strip().lower()
        if task_status in {"completed", "retrying", "failed", "blocked", "cancelled"}:
            return task_status
        if unresolved_tool_failure:
            return "failed"
        return "completed"

    async def _publish_turn_terminal(
        self,
        msg: Optional[InboundMessage],
        state: TurnState,
        status: str,
        *,
        error_code: str = "",
    ) -> None:
        """Publish exactly one terminal fence for a visible turn."""

        payload = state.finish(status, error_code=error_code)
        if payload is None:
            return
        payload = self._with_trace_metadata(
            payload,
            turn_id=state.turn_id,
            message_id=state.message_id,
        )
        self._log_session_event(
            state.session_key,
            {
                "type": "turn_terminal",
                "turn_id": state.turn_id,
                "message_id": state.message_id,
                "turn_status": payload.get("turn_status"),
                "terminal_sequence": payload.get("terminal_sequence"),
                "error_code": str(payload.get("error_code") or "")[:160],
            },
        )
        try:
            await self._publish_both(msg, "", payload)
        except Exception:
            # Delivery is best effort; the durable session event above still
            # records the terminal transition for reconnecting clients.
            logger.debug("Could not publish terminal turn event", exc_info=True)

    def _normalize_tool_alias(
        self, function_name: str, function_args: dict, session_key: str
    ) -> tuple[str, dict]:
        """Normalize common alias tools back to canonical runtime tool names."""
        return normalize_tool_alias(
            function_name, function_args, self.metrics.record_anomaly, session_key
        )

    # ── Shared publishing helpers ────────────────────────────────────────

    async def _publish(
        self, msg: Optional[InboundMessage], content: str, metadata: dict
    ) -> None:
        """Publish an OutboundMessage to the originating channel (or 'web')."""
        await self.bus.publish_outbound(
            OutboundMessage(
                channel=msg.channel if msg else "web",
                chat_id=msg.chat_id if msg else "system",
                content=content,
                metadata=metadata,
            )
        )

    async def _publish_both(
        self, msg: Optional[InboundMessage], content: str, metadata: dict
    ) -> None:
        """Publish to the originating channel AND mirror to web if needed."""
        await self._publish(msg, content, metadata)
        if msg and msg.channel != "web":
            await self.bus.publish_outbound(
                OutboundMessage(
                    channel="web",
                    chat_id=msg.chat_id or "system",
                    content=content,
                    metadata=metadata,
                )
            )

    # ── Confirmation embed builder ───────────────────────────────────────

    async def _publish_activity(
        self,
        msg: Optional[InboundMessage],
        text: str,
        turn_id: Optional[str] = None,
        message_id: Optional[str] = None,
    ) -> None:
        await self._publish_both(
            msg,
            "",
            self._with_trace_metadata(
                {"type": "activity", "text": text},
                turn_id=turn_id,
                message_id=message_id,
            ),
        )

    @staticmethod
    def _truncate_preview(text: Any, limit: int = 1200) -> str:
        raw = str(text or "")
        if len(raw) <= limit:
            return raw
        return raw[:limit] + "\n... (truncated)"

    @staticmethod
    def _safe_json_load(raw: Any) -> Any:
        if isinstance(raw, (dict, list)):
            return raw
        if not isinstance(raw, str) or not raw.strip():
            return None
        try:
            return json.loads(raw)
        except Exception:
            try:
                return ast.literal_eval(raw)
            except Exception:
                return None

    @staticmethod
    def _env_truthy(name: str) -> bool:
        value = str(os.getenv(name, "") or "").strip().lower()
        return value in {"1", "true", "yes", "on"}

    def _tool_debug_enabled(self) -> bool:
        return self._env_truthy("LIMEBOT_TOOL_DEBUG")

    @staticmethod
    def _is_fast_casual_turn(content: str) -> bool:
        raw = str(content or "").strip()
        if not raw:
            return True
        lowered = raw.lower()
        if lowered in _CASUAL_WORDS:
            return True
        if len(raw) <= 10:
            return True
        return any(lowered.startswith(prefix) for prefix in _CASUAL_PHRASE_PREFIXES)

    def _should_include_tools_for_turn(
        self,
        content: str,
        session_key: Optional[str] = None,
        msg: Optional[InboundMessage] = None,
    ) -> bool:
        """Expose the registered tool catalog; the model selects what applies."""
        return bool(
            (msg is not None and is_unattended_turn(msg))
            or self._should_include_tools(content)
        )

    @staticmethod
    def _build_provider_config(
        source_model: str,
        model: str,
        base_url: Optional[str],
        api_key: Optional[str],
        custom_llm_provider: Optional[str],
    ) -> ProviderConfig:
        return ProviderConfig(
            source_model=source_model,
            model=model,
            base_url=base_url,
            api_key=api_key,
            custom_llm_provider=custom_llm_provider,
            is_codex=str(source_model or "").startswith("openai-codex/"),
        )

    def _record_stage_timing(
        self,
        session_key: str,
        stage: str,
        started_at: float,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> None:
        try:
            self.metrics.record_stage_timing(
                session_key,
                stage,
                time.perf_counter() - started_at,
                metadata=metadata,
            )
        except Exception:
            pass

    @staticmethod
    def _tool_definition_names(tool_defs: List[Dict[str, Any]]) -> List[str]:
        names: List[str] = []
        for tool in tool_defs or []:
            if not isinstance(tool, dict):
                continue
            function = tool.get("function")
            if not isinstance(function, dict):
                continue
            name = function.get("name")
            if isinstance(name, str) and name:
                names.append(name)
        return names

    def _tool_call_debug_rows(self, tool_calls: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        rows: List[Dict[str, Any]] = []
        for tool_call in tool_calls or []:
            if not isinstance(tool_call, dict):
                continue
            function = tool_call.get("function")
            if not isinstance(function, dict):
                function = {}
            rows.append(
                {
                    "id": tool_call.get("id"),
                    "name": function.get("name"),
                    "arguments": self._tool_debug_preview(
                        function.get("arguments", ""), limit=240
                    ),
                }
            )
        return rows

    @staticmethod
    def _tool_debug_preview(text: Any, limit: int = 400) -> str:
        raw = redact_sensitive_text(text or "")
        raw = raw.replace("\r", "\\r").replace("\n", "\\n")
        if len(raw) <= limit:
            return raw
        return raw[:limit] + "... (truncated)"

    def _log_tool_debug(self, event: str, **fields: Any) -> None:
        if not self._tool_debug_enabled():
            return

        rendered: Dict[str, Any] = {}
        for key, value in fields.items():
            if isinstance(value, str):
                rendered[key] = self._tool_debug_preview(value, limit=900)
            elif isinstance(value, list):
                if all(
                    isinstance(item, (str, int, float, bool, type(None)))
                    for item in value
                ):
                    items = list(value[:40])
                    rendered[key] = [
                        self._tool_debug_preview(item, limit=160)
                        if isinstance(item, str)
                        else item
                        for item in items
                    ]
                    if len(value) > 40:
                        rendered[key].append(f"... (+{len(value) - 40} more)")
                else:
                    rendered[key] = self._tool_debug_preview(
                        json.dumps(value, ensure_ascii=False, default=str),
                        limit=1600,
                    )
            elif isinstance(value, dict):
                rendered[key] = self._tool_debug_preview(
                    json.dumps(value, ensure_ascii=False, default=str),
                    limit=1600,
                )
            else:
                rendered[key] = value

        logger.info(
            f"[TOOL DEBUG] {event} | "
            f"{json.dumps(rendered, ensure_ascii=False, default=str)}"
        )

    def _record_rag_trace(self, session_key: str, trace: Dict[str, Any]) -> None:
        self.rag.record(session_key, trace)

    def get_recent_rag_traces(
        self, session_key: Optional[str] = None, limit: int = 20
    ) -> List[Dict[str, Any]]:
        if not getattr(self, "rag", None):
            return []
        return self.rag.get_recent(session_key, limit) or []

    def _build_rag_result_trace(self, row: Dict[str, Any]) -> Dict[str, Any]:
        return self.rag.build_result_trace(row)

    def _build_write_preview(self, function_args: dict) -> Dict[str, Any]:
        return self.confirm.build_write_preview(function_args)

    def _build_delete_preview(self, function_args: dict) -> Dict[str, Any]:
        return self.confirm.build_delete_preview(function_args)

    @staticmethod
    def _clean_subagent_final_result(result: str) -> str:
        text = str(result or "").strip()
        if not text:
            return ""

        filtered_lines: List[str] = []
        meta_prefixes = (
            "the assistant attempted to",
            "need to produce",
            "need to finish",
            "probably need to",
            "provide final response directly",
            "within limits",
            "got truncated due to",
        )

        for raw_line in text.splitlines():
            line = raw_line.strip()
            lower = line.lower()
            if any(lower.startswith(prefix) for prefix in meta_prefixes):
                continue
            filtered_lines.append(raw_line)

        cleaned = "\n".join(filtered_lines).strip()
        if not cleaned:
            return ""
        return cleaned

    @staticmethod
    def _extract_command_paths(command: str, limit: int = 5) -> List[str]:
        from core.confirmation import ConfirmationManager

        return ConfirmationManager.extract_command_paths(command, limit)

    def _build_command_preview(self, function_args: dict) -> Dict[str, Any]:
        return self.confirm.build_command_preview(function_args)

    def _build_confirmation_preview(
        self, function_name: str, function_args: dict, session_key: str
    ) -> Dict[str, Any]:
        return self.confirm.build_preview(function_name, function_args, session_key)

    @staticmethod
    def _session_whitelist_key(
        function_name: str, function_args: Optional[dict] = None
    ) -> str:
        """Compatibility wrapper for the session approval-key rule."""
        return ToolApprovalPolicy.session_whitelist_key(
            function_name, function_args
        )

    def _get_tool_approval_decision(
        self,
        session_key: str,
        function_name: str,
        is_internal: bool = False,
        is_whatsapp: bool = False,
        function_args: Optional[dict] = None,
        msg: Optional[InboundMessage] = None,
    ) -> Dict[str, Any]:
        """Compatibility wrapper for the extracted approval-policy service."""
        return ToolApprovalPolicy.decide(
            session_key,
            function_name,
            config=getattr(self, "config", None),
            session_whitelists=getattr(self, "session_whitelists", None),
            is_internal=is_internal,
            is_whatsapp=is_whatsapp,
            function_args=function_args,
            msg=msg,
        )

    @staticmethod
    def _approval_audit_preview(preview: Dict[str, Any]) -> Dict[str, Any]:
        """Keep audit metadata useful without persisting commands or file contents."""
        safe: Dict[str, Any] = {"kind": str(preview.get("kind") or "unknown")}
        risk_flags = preview.get("risk_flags")
        if isinstance(risk_flags, list):
            safe["risk_flags"] = [str(flag)[:80] for flag in risk_flags[:10]]
        for key in ("mode", "target_type"):
            if preview.get(key):
                safe[key] = str(preview[key])[:80]
        affected_paths = preview.get("affected_paths")
        if isinstance(affected_paths, list):
            safe["affected_path_count"] = len(affected_paths)
        return safe

    def _build_confirmation_embed(
        self,
        function_name: str,
        function_args: dict,
        session_key: str,
        preview: Optional[Dict[str, Any]] = None,
    ) -> list:
        """Build the embed fields list for a tool confirmation prompt."""
        return self.confirm.build_embed(
            function_name, function_args, session_key, preview
        )

    def _tool_approval_flow_service(self) -> ToolApprovalFlowService:
        """Bind the approval lifecycle to current policy, manager, and delivery ports."""
        return ToolApprovalFlowService(
            confirmation=self.confirm,
            decide=self._get_tool_approval_decision,
            get_profile_context=get_current_profile_context,
            get_profile_id=get_current_profile_id,
            session_whitelist_key=self._session_whitelist_key,
            log_session_event=self._log_session_event,
            approval_audit_preview=self._approval_audit_preview,
            display_tool_args=self._display_tool_args,
            with_trace_metadata=self._with_trace_metadata,
            publish=self._publish,
            publish_web_mirror=self._publish_web_approval,
        )

    def _tool_batch_event_service(self) -> ToolBatchEventService:
        """Bind tool-batch lifecycle events to this loop's delivery and history ports."""
        return ToolBatchEventService(
            publish_both=self._publish_both,
            parse_tool_call=self._parse_tool_call,
            build_preview=self._build_confirmation_preview,
            display_args=self._display_tool_args,
            log_session_event=self._log_session_event,
            sensitive_tools=_SENSITIVE_TOOLS,
            broadcast_max_chars=TOOL_BROADCAST_MAX_CHARS,
        )

    async def _publish_web_approval(
        self, msg: InboundMessage, metadata: Dict[str, Any]
    ) -> None:
        """Mirror a non-web approval card to the web companion."""
        await self.bus.publish_outbound(
            OutboundMessage(
                channel="web",
                chat_id=msg.chat_id or "system",
                content="",
                metadata=metadata,
            )
        )

    def _display_tool_args(
        self,
        function_name: str,
        function_args: Dict[str, Any],
        preview: Optional[Dict[str, Any]] = None,
    ) -> Any:
        """Return UI-safe arguments without weakening the private tool call."""

        if function_name == "edit_skill":
            safe_preview = preview or self._build_confirmation_preview(
                function_name, function_args, "display"
            )
            return {
                "skill_name": str(
                    safe_preview.get("skill")
                    or function_args.get("skill_name")
                    or ""
                )[:120],
                "changes": list(safe_preview.get("changes") or [])[:32],
            }
        return redact_sensitive_value(function_args)

    # ── Stream result unpacker ───────────────────────────────────────────

    @staticmethod
    def _unpack_stream_result(result) -> tuple:
        """Unpack the legacy five-field stream result for existing callers."""
        if isinstance(result, tuple) and len(result) >= 6:
            return (
                result[0],
                result[1],
                result[2],
                result[3],
                result[4],
            )
        if isinstance(result, tuple) and len(result) >= 5:
            return result[0], result[1], result[2], result[3], result[4]
        if isinstance(result, tuple) and len(result) >= 4:
            return result[0], result[1], result[2], result[3], False
        content, tool_calls, usage = result
        return content, tool_calls, usage, False, False

    @staticmethod
    def _unpack_stream_result_with_reasoning(result) -> tuple:
        """Unpack stream output including provider reasoning content."""
        if isinstance(result, tuple) and len(result) >= 6:
            return (
                result[0],
                result[1],
                result[2],
                result[3],
                result[4],
                result[5],
            )
        content, tool_calls, usage, streamed_to_web, streamed_to_discord = (
            AgentLoop._unpack_stream_result(result)
        )
        return content, tool_calls, usage, streamed_to_web, streamed_to_discord, ""

    # ── Overlap deduplication ────────────────────────────────────────────

    @staticmethod
    def _dedup_overlap(accumulated: str, new_content: str) -> str:
        """Return the portion of *new_content* that doesn't overlap with the
        tail of *accumulated*.  Used after tool-call continuations where the
        LLM may repeat previously-streamed text.

        Two-stage approach:
          A) Literal tail-prefix overlap (extended window up to 300 chars).
          B) Paragraph-level dedup — drops paragraphs from *new_content*
             that already appear in *accumulated*.
        """
        acc_s, nxt_s = accumulated.strip(), new_content.strip()
        if not (acc_s and nxt_s):
            return new_content

        # Stage A: tail-prefix overlap (extended window)
        max_overlap = min(len(acc_s), len(nxt_s), 300)
        for length in range(max_overlap, 4, -1):
            suffix = acc_s[-length:]
            if nxt_s.lower().startswith(suffix.lower()):
                match = re.search(re.escape(suffix), new_content, re.IGNORECASE)
                if match:
                    clean = new_content[match.start() + length :].lstrip()
                    return clean if clean.strip() else ""

        # Stage B: paragraph-level dedup
        acc_paras = [p.strip() for p in re.split(r"\n\s*\n", acc_s) if p.strip()]
        nxt_paras = [p.strip() for p in re.split(r"\n\s*\n", nxt_s) if p.strip()]

        if not nxt_paras:
            return ""

        # Build a set of normalized accumulated paragraphs for fast lookup
        acc_set = {p.lower() for p in acc_paras}

        # Keep only paragraphs from new_content that are genuinely new
        novel = [p for p in nxt_paras if p.lower() not in acc_set]

        # If everything was a duplicate, return empty
        if not novel:
            return ""

        # If some but not all were dupes, return only the novel ones
        if len(novel) < len(nxt_paras):
            return "\n\n".join(novel)

        return new_content

    @staticmethod
    def _dedupe_repeated_reply_sections(reply_to_user: str) -> str:
        if not reply_to_user:
            return reply_to_user

        # 1. Exact-half string repetition (glued repeat)
        n_chars = len(reply_to_user)
        if n_chars > 80 and n_chars % 2 == 0:
            half = n_chars // 2
            if reply_to_user[:half] == reply_to_user[half:]:
                logger.info("✂ Self-repetition detected (glued) — trimming duplicate half.")
                return reply_to_user[:half]

        # 2. Paragraph-level repetition
        if len(reply_to_user) > 80:
            _paras = [
                p.strip()
                for p in re.split(r"\n\s*\n", reply_to_user)
                if p.strip()
            ]
            _n = len(_paras)
            # Exact-half repetition (even paragraph count)
            if _n >= 4 and _n % 2 == 0:
                if _paras[: _n // 2] == _paras[_n // 2 :]:
                    logger.info(
                        "✂ Self-repetition detected — trimming duplicate."
                    )
                    return "\n\n".join(_paras[: _n // 2])
            # Trailing-repeat (odd count or partial overlap)
            if _n >= 3:
                _half = _n // 2
                if _half >= 2 and _paras[:_half] == _paras[-_half:]:
                    logger.info(
                        "✂ Self-repetition detected (trailing) — trimming."
                    )
                    return "\n\n".join(_paras[: _n - _half])

        return reply_to_user

    @staticmethod
    def _normalize_user_visible_reply_for_compare(value: str) -> str:
        if not value:
            return ""

        cleaned = _GHOST_TAG_RE.sub("", str(value))
        cleaned = re.sub(
            r"<(?:" + "|".join(_GHOST_TAG_NAMES) + r")[^>]*>.*?</(?:"
            + "|".join(_GHOST_TAG_NAMES) + r")>",
            "",
            cleaned,
            flags=re.DOTALL,
        )
        cleaned = AgentLoop._dedupe_repeated_reply_sections(cleaned.strip())
        return re.sub(r"\s+", " ", cleaned).strip()

    @staticmethod
    def _estimate_tokens(messages: List[Dict]) -> int:
        """
        O(n) token estimate (~4 chars per token, slightly conservative).
        Used in the pruning loop to avoid O(n²) token_counter calls.
        """
        text_chars = 0
        image_tokens = 0
        for m in messages:
            c = m.get("content", "")
            if not isinstance(c, list):
                text_chars += len(str(c))
                continue
            for item in c:
                if isinstance(item, dict) and item.get("type") == "image_url":
                    # Image inputs are billed by dimensions/detail, not by the
                    # character length of a base64 data URL.
                    image_tokens += 1_024
                else:
                    text_chars += len(str(item))
        return (text_chars // 4) + image_tokens

    def _get_task_registry(self) -> ManagedTaskRegistry:
        """Return the live registry, including for lightweight test doubles."""
        registry = getattr(self, "task_registry", None)
        if registry is None:
            registry = ManagedTaskRegistry()
            self.task_registry = registry
        return registry

    async def _persist_managed_task_terminal(self, entry: Any) -> None:
        """Close the durable task projection when a live handle is cancelled early."""
        from core.task_tracker import TaskStatus, get_task_tracker

        if self.active_tasks.get(entry.session_key) is entry.handle:
            self.active_tasks.pop(entry.session_key, None)
        background_tasks = getattr(self, "background_subagent_tasks", {})
        if background_tasks.get(entry.task_id) is entry.handle:
            background_tasks.pop(entry.task_id, None)
            getattr(self, "background_subagent_sessions", {}).pop(
                entry.task_id, None
            )
            getattr(self, "background_subagent_parents", {}).pop(
                entry.task_id, None
            )

        tracker = get_task_tracker()
        tracked = await tracker.get_task(entry.task_id)
        needs_terminal_delivery = bool(
            tracked is not None
            and tracked.status
            not in {
                TaskStatus.COMPLETED.value,
                TaskStatus.FAILED.value,
                TaskStatus.CANCELLED.value,
            }
        )
        if tracked is None or tracked.status in {
            TaskStatus.COMPLETED.value,
            TaskStatus.FAILED.value,
            TaskStatus.CANCELLED.value,
        }:
            return
        await tracker.update_task(
            entry.task_id,
            status=entry.status,
            error=entry.error or None,
            metadata_update={
                "result_preview": str(entry.result or entry.error or "")[:500]
            },
        )
        if needs_terminal_delivery and entry.kind == "inbound_message":
            bus = getattr(self, "bus", None)
            if bus is not None:
                try:
                    await bus.publish_outbound(
                        OutboundMessage(
                            channel=str(entry.metadata.get("channel") or "web"),
                            chat_id=str(entry.metadata.get("chat_id") or "system"),
                            content="",
                            metadata=self._with_trace_metadata(
                                {
                                    "type": "stop_typing",
                                    "task_status": entry.status,
                                    "is_error": entry.status == TaskStatus.FAILED.value,
                                    "is_cancellation": entry.status
                                    == TaskStatus.CANCELLED.value,
                                },
                                task_id=entry.task_id,
                            ),
                        )
                    )
                except Exception:
                    pass

    def _inbound_turn_lifecycle(self) -> InboundTurnLifecycle:
        from core.task_tracker import get_task_tracker

        return InboundTurnLifecycle(
            tracker=get_task_tracker(),
            registry=self._get_task_registry(),
            task_runs=getattr(self, "task_runs", None),
            profile_coordinator=getattr(self, "profile_coordinator", None),
            job_queue=getattr(self, "job_queue", None),
            scheduler=getattr(self, "scheduler", None),
            bus=getattr(self, "bus", None),
            active_tasks=self.active_tasks,
            queue_worker_id=getattr(self, "_queue_worker_id", "agent"),
            process_message=self._process_message,
            trace_metadata=self._with_trace_metadata,
        )

    async def _dispatch_message(self, msg: InboundMessage) -> asyncio.Task:
        """Register an inbound turn through its lifecycle coordinator."""
        return await self._inbound_turn_lifecycle().dispatch(
            msg,
            run_managed_message=self._run_managed_message,
            persist_task_terminal=self._persist_managed_task_terminal,
        )

    async def _run_managed_message(
        self,
        msg: InboundMessage,
        task_id: str,
        start_gate: Optional[asyncio.Event] = None,
    ) -> None:
        await self._inbound_turn_lifecycle().run(msg, task_id, start_gate)

    async def run(self) -> None:
        self._running = True
        logger.info(f"Agent loop started (model: {self.model})")
        while self._running:
            try:
                msg = await self.bus.consume_inbound()
                await self._dispatch_message(msg)
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"Error in agent loop: {e}")

    async def stop(self) -> None:
        self._running = False
        if self._initialization_task and not self._initialization_task.done():
            self._initialization_task.cancel()
            await asyncio.gather(self._initialization_task, return_exceptions=True)

        # A config change restarts the backend.  The managed registry owns both
        # ordinary turns and background subagents, so shutdown can cancel and
        # await them through one lifecycle instead of relying on detached maps.
        current_task = asyncio.current_task()
        await self._get_task_registry().cancel_all(
            exclude={current_task} if current_task is not None else None
        )

        # Keep a compatibility fallback for test doubles or legacy tasks that
        # were inserted directly into the old maps.
        active_task_registry = getattr(self, "active_tasks", {})
        legacy_handles = list(active_task_registry.values()) + list(
            getattr(self, "background_subagent_tasks", {}).values()
        )
        legacy_tasks = [
            task
            for task in legacy_handles
            if task is not current_task and not task.done()
        ]
        for task in legacy_tasks:
            task.cancel()
        if legacy_tasks:
            try:
                await asyncio.wait_for(
                    asyncio.gather(*legacy_tasks, return_exceptions=True),
                    timeout=2.0,
                )
            except asyncio.TimeoutError:
                logger.warning(
                    "Timed out waiting for %d legacy task handles during shutdown.",
                    len(legacy_tasks),
                )
        active_task_registry.clear()
        getattr(self, "background_subagent_tasks", {}).clear()
        getattr(self, "background_subagent_sessions", {}).clear()
        getattr(self, "background_subagent_parents", {}).clear()

        # Persist the latest durable delivery state before bus workers are cancelled.
        try:
            from core.delivery_tracker import get_delivery_tracker

            await asyncio.wait_for(get_delivery_tracker().flush(), timeout=2.0)
        except Exception:
            pass
        # Metrics persistence is best-effort and must not pin the event loop.
        try:
            await asyncio.to_thread(self.metrics.flush, 2.0)
        except Exception:
            pass

    @staticmethod
    def _task_channel_chat(session_key: str) -> Tuple[str, str]:
        channel, separator, chat_id = str(session_key or "").partition(":")
        return (channel if separator else "", chat_id if separator else session_key)

    async def start_background_subagent(
        self,
        parent_session_key: str,
        sub_session_key: str,
        task: str,
        agent_name: Optional[str] = None,
        isolated_workspace: Any = None,
        isolation_mode: str = "none",
    ) -> str:
        """Create and retain a durable, cancellable background subagent job."""
        from core.task_tracker import TaskStatus, TaskType, get_task_tracker

        tracker = get_task_tracker()
        channel, chat_id = self._task_channel_chat(parent_session_key)
        task_id = await tracker.create_task(
            task_type=TaskType.SUBAGENT_JOB.value,
            summary=str(task or "").strip()[:300],
            channel=channel,
            session_key=sub_session_key,
            chat_id=chat_id,
            metadata={
                "background": True,
                "parent_session_key": parent_session_key,
                "sub_session_key": sub_session_key,
                "agent_name": agent_name or "",
                "isolation": isolation_mode,
            },
        )
        await tracker.update_task(task_id, status=TaskStatus.RUNNING.value)
        start_gate = asyncio.Event()
        handle = asyncio.create_task(
            self._run_background_subagent_task(
                task_id,
                parent_session_key,
                sub_session_key,
                task,
                agent_name,
                start_gate,
                isolated_workspace,
                isolation_mode,
            ),
            name=f"limebot-subagent-{task_id}",
        )
        try:
            await self._get_task_registry().register(
                task_id,
                handle,
                kind=TaskType.SUBAGENT_JOB.value,
                session_key=sub_session_key,
                metadata={
                    "background": True,
                    "parent_session_key": parent_session_key,
                    "agent_name": agent_name or "",
                    "isolation": isolation_mode,
                },
                on_terminal=self._persist_managed_task_terminal,
            )
            await self._get_task_registry().mark_running(task_id)
        except BaseException:
            handle.cancel()
            await asyncio.gather(handle, return_exceptions=True)
            raise
        finally:
            start_gate.set()
        if not hasattr(self, "background_subagent_tasks"):
            self.background_subagent_tasks = {}
        if not hasattr(self, "background_subagent_sessions"):
            self.background_subagent_sessions = {}
        if not hasattr(self, "background_subagent_parents"):
            self.background_subagent_parents = {}
        self.background_subagent_tasks[task_id] = handle
        self.background_subagent_sessions[task_id] = sub_session_key
        self.background_subagent_parents[task_id] = parent_session_key
        if not hasattr(self, "background_subagent_results"):
            self.background_subagent_results = {}
        return task_id

    async def _run_background_subagent_task(
        self,
        task_id: str,
        parent_session_key: str,
        sub_session_key: str,
        task: str,
        agent_name: Optional[str],
        start_gate: Optional[asyncio.Event] = None,
        isolated_workspace: Any = None,
        isolation_mode: str = "none",
    ) -> str:
        from core.task_tracker import TaskStatus, get_task_tracker

        tracker = get_task_tracker()
        if start_gate is not None:
            await start_gate.wait()
        task_context_token = _CURRENT_TASK_ID.set(task_id)
        status = TaskStatus.COMPLETED.value
        error = ""
        result = ""
        try:
            result = str(
                await self.run_subagent(
                    parent_session_key,
                    sub_session_key,
                    task,
                    agent_name=agent_name,
                    isolated_workspace=isolated_workspace,
                    isolation_mode=isolation_mode,
                )
                or ""
            )
            if result.startswith("Error"):
                status = TaskStatus.FAILED.value
                error = result[:500]
            return result
        except asyncio.CancelledError:
            status = TaskStatus.CANCELLED.value
            error = "Background subagent cancelled."
            raise
        except Exception as exc:
            status = TaskStatus.FAILED.value
            error = str(exc)[:500]
            logger.error(f"Error in background sub-agent '{sub_session_key}': {exc}")
            return f"Error in sub-agent '{sub_session_key}': {exc}"
        finally:
            # The wrapper is the only completion owner.  A repeated kill/wait or
            # a late cancellation therefore cannot publish duplicate terminal
            # task records.
            try:
                await asyncio.shield(
                    tracker.update_task(
                        task_id,
                        status=status,
                        summary=(result or error or task).strip()[:300],
                        error=error or None,
                        metadata_update={
                            "outcome": status,
                            "result_preview": (result or error).strip()[:500],
                        },
                    )
                )
            except Exception as exc:
                logger.warning(
                    f"Could not finalize background task {task_id}: {exc}"
                )
            await self._get_task_registry().finalize(
                task_id,
                status,
                result=result or None,
                error=error or None,
                metadata_update={"result_preview": (result or error).strip()[:500]},
            )
            current = asyncio.current_task()
            if result or error:
                self.background_subagent_results[task_id] = result or error
                # Keep completed output bounded while TaskTracker retains the
                # durable status and a short preview.
                while len(self.background_subagent_results) > 500:
                    self.background_subagent_results.pop(
                        next(iter(self.background_subagent_results)), None
                    )
            if self.background_subagent_tasks.get(task_id) is current:
                self.background_subagent_tasks.pop(task_id, None)
            self.background_subagent_sessions.pop(task_id, None)
            self.background_subagent_parents.pop(task_id, None)
            if isolated_workspace is not None and not isolated_workspace.is_pending():
                try:
                    await isolated_workspace.cleanup()
                except Exception as exc:
                    logger.warning(
                        "Could not clean up isolated workspace for background task %s: %s",
                        task_id,
                        exc,
                    )
            _CURRENT_TASK_ID.reset(task_context_token)

    async def get_background_subagent_task(self, task_id: str):
        from core.task_tracker import get_task_tracker

        return await get_task_tracker().get_task(task_id)

    async def get_background_subagent_output(
        self, task_id: str, timeout: Optional[float] = None
    ) -> Optional[str]:
        if timeout is not None and float(timeout) > 0:
            task = await self.wait_background_subagent_task(task_id, timeout)
        else:
            task = await self.get_background_subagent_task(task_id)
        if task is None:
            return None
        output = getattr(self, "background_subagent_results", {}).get(task_id)
        if output is None:
            output = str((task.metadata or {}).get("result_preview") or "")
        return (
            f"Task {task.task_id}\n"
            f"Status: {task.status}\n"
            f"Summary: {task.summary}\n"
            f"{output}".strip()
        )

    async def wait_background_subagent_task(
        self, task_id: str, timeout: Optional[float] = None
    ):
        managed = await self._get_task_registry().get(task_id)
        if managed is not None and not managed.terminal:
            await self._get_task_registry().wait(task_id, timeout)
        else:
            handle = self.background_subagent_tasks.get(task_id)
            if handle is not None and not handle.done():
                waiter = asyncio.shield(handle)
                if timeout is None:
                    await waiter
                else:
                    await asyncio.wait_for(
                        waiter, timeout=max(0.0, float(timeout))
                    )
        return await self.get_background_subagent_task(task_id)

    async def kill_background_subagent_task(self, task_id: str):
        from core.task_tracker import TaskStatus, TaskType, get_task_tracker

        tracker = get_task_tracker()
        managed = await self._get_task_registry().get(task_id)
        if managed is not None and not managed.terminal:
            await self._get_task_registry().cancel(task_id)
        else:
            handle = self.background_subagent_tasks.get(task_id)
            if handle is not None and not handle.done():
                handle.cancel()
                try:
                    await asyncio.wait_for(
                        asyncio.gather(handle, return_exceptions=True),
                        timeout=2.0,
                    )
                except asyncio.TimeoutError:
                    logger.warning(
                        "Timed out waiting for background task %s to cancel.",
                        task_id,
                    )
        task = await tracker.get_task(task_id)
        if (
            task is not None
            and task.type == TaskType.SUBAGENT_JOB.value
            and task.status not in {
                TaskStatus.COMPLETED.value,
                TaskStatus.FAILED.value,
                TaskStatus.CANCELLED.value,
            }
        ):
            task = await tracker.cancel_task(task_id)
        return task

    # Short lifecycle aliases mirror Grok's task vocabulary for callers that
    # manage jobs directly instead of going through the REST/tool adapters.
    async def get_task(self, task_id: str):
        return await self.get_background_subagent_task(task_id)

    async def wait_task(self, task_id: str, timeout: Optional[float] = None):
        return await self.wait_background_subagent_task(task_id, timeout)

    async def kill_task(self, task_id: str):
        return await self.kill_background_subagent_task(task_id)

    async def cancel_session(self, session_key: str) -> bool:
        cancelled_any = False
        self._log_session_event(
            session_key,
            {"type": "cancel_requested", "session_key": session_key},
        )

        related_sessions = {session_key}
        for sk, meta in list(getattr(self.session_manager, "sessions", {}).items()):
            if meta.get("parent_id") == session_key:
                related_sessions.add(sk)

        managed_entries = await self._get_task_registry().active_for(
            lambda entry: (
                entry.session_key in related_sessions
                or str(entry.metadata.get("parent_session_key") or "")
                in related_sessions
            )
        )
        for entry in managed_entries:
            if await self._get_task_registry().cancel(entry.task_id):
                cancelled_any = True

        for sk in related_sessions:
            task = self.active_tasks.get(sk)
            if task and not task.done():
                task.cancel()
                logger.info(f"🛑 Cancelled task for {sk}")
                self._log_session_event(
                    sk,
                    {
                        "type": "task_cancelled",
                        "session_key": sk,
                        "parent": session_key,
                    },
                )
                cancelled_any = True

        # A background subagent is keyed by its stable task id rather than by
        # the parent session's short-lived turn handle.
        for task_id, sub_session in list(
            getattr(self, "background_subagent_sessions", {}).items()
        ):
            parent = getattr(self, "background_subagent_parents", {}).get(task_id)
            if parent not in related_sessions and sub_session not in related_sessions:
                continue
            if await self.kill_background_subagent_task(task_id):
                logger.info(f"Cancelled background subagent task {task_id}")
                cancelled_any = True

        for conf_id, conf in self.confirm.release_for_sessions(related_sessions):
            logger.info(
                f"🛑 Released pending confirmation {conf_id} for {conf.get('session_key')}"
            )
            self._log_session_event(
                conf.get("session_key") or session_key,
                {
                    "type": "confirmation_released",
                    "confirmation_id": conf_id,
                    "tool": conf.get("tool"),
                },
            )
            cancelled_any = True

        for run in self.task_runs.list_runs(
            statuses=TASK_RUN_ACTIVE_STATES, limit=500
        ):
            run_session = str((run.metadata or {}).get("session_key") or "")
            if run_session not in related_sessions:
                continue
            self.task_runs.cancel(run.run_id, reason="User cancelled the task.")
            durable_id = str(
                (run.metadata or {}).get("durable_job_id") or ""
            ).strip()
            if durable_id:
                self.job_queue.finish(
                    durable_id, "cancelled", "User cancelled the task."
                )
            cancelled_any = True

        for sk, t in list(self.active_tasks.items()):
            if t.done():
                del self.active_tasks[sk]

        return cancelled_any

    async def confirm_tool(
        self,
        conf_id: str,
        approved: bool,
        session_whitelist: bool = False,
        source: str = "api",
    ) -> bool:
        return self.confirm.resolve(
            conf_id,
            approved,
            session_whitelist=session_whitelist,
            source=source,
            log_event=self._log_session_event,
        )

    def _turn_history_service(self) -> TurnHistoryService:
        service = getattr(self, "turn_history_service", None)
        if service is None:
            service = TurnHistoryService(
                histories=self.history,
                dirty=self._history_dirty,
                last_flush=self._last_history_flush,
                save_history=lambda session_key, entries: self.session_manager.save_history(
                    session_key, entries
                ),
                safe_content=lambda content: self._image_safe_history_content(
                    content
                ),
                flush_interval=lambda: self._history_flush_interval,
            )
            self.turn_history_service = service
        return service

    def _history_append(
        self,
        session_key: str,
        entry: Dict[str, Any],
        *,
        mark_dirty: bool = True,
    ) -> None:
        self._turn_history_service().append(
            session_key, entry, mark_dirty=mark_dirty
        )

    def _history_replace(
        self,
        session_key: str,
        entries: List[Dict[str, Any]],
        *,
        mark_dirty: bool = False,
    ) -> None:
        self._turn_history_service().set_history(
            session_key, entries, mark_dirty=mark_dirty
        )

    def _history_replace_entry(
        self,
        session_key: str,
        index: int,
        entry: Dict[str, Any],
    ) -> None:
        self._turn_history_service().replace_entry(session_key, index, entry)

    def _mark_dirty(self, session_key: str) -> None:
        self._turn_history_service().mark_dirty(session_key)

    async def _flush_history(self, session_key: str, force: bool = False) -> None:
        """Persist history only if it changed since the last flush."""
        await self._turn_history_service().flush(session_key, force=force)

    async def run_subagent(
        self,
        parent_session_key: str,
        sub_session_key: str,
        task: str,
        agent_name: Optional[str] = None,
        isolated_workspace: Any = None,
        isolation_mode: str = "none",
    ) -> str:
        persona_paths = get_current_persona_paths()
        return await run_subagent(
            parent_session_key=parent_session_key,
            sub_session_key=sub_session_key,
            task=task,
            agent_name=agent_name,
            isolated_workspace=isolated_workspace,
            isolation_mode=isolation_mode,
            subagent_registry=self.subagent_registry,
            get_model=lambda: self.model,
            session_manager=self.session_manager,
            filter_tool_definitions=self._filter_tool_definitions_for_subagent,
            call_llm=self._llm_call_with_retry,
            normalize_tool_call=self._normalize_tool_alias,
            execute_tool=self._execute_tool,
            clean_final_result=self._clean_subagent_final_result,
            bus=self.bus,
            soul_file=persona_paths.soul,
            identity_file=persona_paths.identity,
            report_diff_max_chars=_SUBAGENT_REPORT_DIFF_MAX_CHARS,
        )

    def _selected_subagent_for_turn(self) -> str:
        selected = self.subagent_registry.get_default_selection()
        profile_context = get_current_profile_context()
        if profile_context is not None and not profile_allows_tool(
            profile_context.profile, "spawn_agent"
        ):
            return "auto"
        return selected

    @staticmethod
    def _build_session_injected_files(
        sender_id: str, attachments: List[Dict[str, Any]]
    ) -> List[str]:
        persona_paths = get_current_persona_paths()
        injected = [
            "SOUL.md",
            "IDENTITY.md",
            f"memory/{datetime.now().strftime('%Y-%m-%d')}.md",
        ]
        if (persona_paths.users / f"{sender_id}.md").exists():
            injected.append(f"users/{sender_id}.md")
        for attachment in attachments:
            path = str(attachment.get("path") or "").strip()
            if path:
                injected.append(path)
        return injected


    @staticmethod
    def _should_include_tools(content: str) -> bool:
        """
        Keep tool routing simple and predictable: non-empty user turns get tools.
        """
        return bool(str(content or "").strip())

    async def _llm_call_with_retry(
        self,
        messages: List[Dict],
        session_key: str,
        msg: Optional[InboundMessage],
        max_retries: int = 3,
        stream: bool = False,
        include_tools: bool = True,
        tool_context_text: str = "",
        turn_id: Optional[str] = None,
        message_id: Optional[str] = None,
        tool_definitions_override: Optional[List[Dict[str, Any]]] = None,
        model_override: Optional[str] = None,
        tool_choice: Optional[str] = "auto",
    ) -> Any:
        return await llm_call_with_retry(
            messages=messages,
            session_key=session_key,
            msg=msg,
            max_retries=max_retries,
            stream=stream,
            include_tools=include_tools,
            tool_context_text=tool_context_text,
            turn_id=turn_id,
            message_id=message_id,
            tool_definitions_override=tool_definitions_override,
            model_override=model_override,
            tool_choice=tool_choice,
            config=self.config,
            bus=getattr(self, "bus", None),
            llm_client=self.llm_client,
            sanitize_messages=self._sanitize_messages_for_llm,
            get_tool_definitions=self._get_tool_definitions_for_turn,
            resolve_provider_chain=self._resolve_provider_chain,
            get_provider_circuit_breaker=self._get_provider_circuit_breaker,
            provider_circuit_identity=self._provider_circuit_identity,
            build_provider_config=self._build_provider_config,
            log_tool_debug=self._log_tool_debug,
            tool_definition_names=self._tool_definition_names,
            should_failover_model=self._should_failover_model,
            should_retry_without_images=self._should_retry_without_images,
            downgrade_image_messages_for_text_model=self._downgrade_image_messages_for_text_model,
            disable_image_inputs_for_session=self._disable_image_inputs_for_session,
            with_trace_metadata=self._with_trace_metadata,
            get_model=lambda: self.model,
            error_types=ProviderErrorTypes(
                authentication=AuthenticationError,
                rate_limit=RateLimitError,
                internal_server=InternalServerError,
                api_connection=APIConnectionError,
                service_unavailable=ServiceUnavailableError,
            ),
        )


    async def _trim_history(self, session_key: str, max_tokens: int = 12_000) -> None:
        if session_key not in self.history or len(self.history[session_key]) <= 1:
            return

        # Normalize before measuring so an interrupted or oversized tool
        # result cannot consume the entire next model context window.
        self._normalize_history_in_place(session_key)
        history = self.history[session_key]
        system_msg = history[0]
        conv = history[1:]

        for m in conv[:-2]:
            c = m.get("content")
            if isinstance(c, list) and any(
                isinstance(x, dict) and x.get("type") == "image_url" for x in c
            ):
                text = " ".join(
                    x.get("text", "")
                    for x in c
                    if isinstance(x, dict) and x.get("type") == "text"
                )
                m["content"] = text + " [Image evicted]"

        for m in conv[:-2]:
            if m.get("role") == "tool":
                c = str(m.get("content", ""))
                if len(c) > 200 and "[SQUASHED" not in c:
                    name = m.get("name", "tool")
                    m["content"] = (
                        f"[SQUASHED - {name}]\n{c[:80]}…\n…({len(c)} chars)…\n…{c[-80:]}"
                    )

        sys_tokens = self._estimate_tokens([system_msg])
        total_tokens = self._estimate_tokens(conv) + sys_tokens

        if total_tokens <= max_tokens:
            self._history_replace(session_key, [system_msg] + conv)
            self._normalize_history_in_place(session_key)
            return

        logger.info(f"🔄 Token limit ({max_tokens}) reached. Summarising…")

        latest_user_index = max(
            (
                index
                for index, message in enumerate(conv)
                if message.get("role") == "user"
            ),
            default=len(conv),
        )
        if latest_user_index <= 0:
            logger.warning(
                "Current user turn exceeds the history budget; preserving it for the model."
            )
            self._history_replace(session_key, [system_msg] + conv)
            self._normalize_history_in_place(session_key)
            self._mark_dirty(session_key)
            return

        num_to_summarise = min(max(1, len(conv) // 3), latest_user_index)
        to_summarise = conv[:num_to_summarise]
        remaining = conv[num_to_summarise:]

        while remaining and remaining[0].get("role") == "tool":
            to_summarise.append(remaining.pop(0))

        try:
            summary_messages = [
                {
                    "role": "system",
                    "content": "Summarise in ≤200 words: key decisions, user facts, task state.",
                },
                {
                    "role": "user",
                    "content": self._history_summary_payload(to_summarise),
                },
            ]
            provider = self.llm_client.resolve_provider(
                self.model,
                default_base_url=self.config.llm.base_url,
            )
            resp = await self.llm_client.complete(
                provider,
                ChatRequest(
                    messages=summary_messages,
                    max_tokens=300,
                    session_id=f"{session_key}::history-summary",
                ),
            )
            summary_text = resp.choices[0].message.content
            summary_msg = {
                "role": "system",
                "content": f"--- CONTEXT SUMMARY ---\n{summary_text}\n--- END ---",
            }
            idx = next(
                (
                    i
                    for i, m in enumerate(remaining)
                    if m.get("role") == "system"
                    and "CONTEXT SUMMARY" in m.get("content", "")
                ),
                -1,
            )
            if idx != -1:
                remaining[idx] = summary_msg
            else:
                remaining.insert(0, summary_msg)
            conv = remaining
            logger.info(f"✅ Summarised {num_to_summarise} messages.")

        except Exception as e:
            logger.error(f"❌ Summarisation failed: {e}. Falling back to truncation.")

            target_tokens = max_tokens - sys_tokens
            conv = self._truncate_history_fallback(conv, target_tokens)

        self._history_replace(session_key, [system_msg] + conv)
        self._normalize_history_in_place(session_key)
        self._mark_dirty(session_key)

    @staticmethod
    def _artifact_delivery_requested(text: str) -> bool:
        """Return whether the user expects a concrete file/download outcome."""
        return bool(_ARTIFACT_REQUEST_RE.search(str(text or "")))

    @staticmethod
    def _artifact_reserve_tool_definitions(
        tool_definitions: Optional[List[Dict[str, Any]]],
    ) -> Optional[List[Dict[str, Any]]]:
        """Remove open-ended research tools while preserving creation/delivery tools."""
        if not tool_definitions:
            return tool_definitions
        reserved = []
        for definition in tool_definitions:
            name = str(definition.get("function", {}).get("name") or "")
            if name not in _RESEARCH_TOOL_NAMES:
                reserved.append(definition)
        return reserved

    async def _execute_tag_compat_tool(
        self, function_name: str, function_args: Dict[str, Any], session_key: str = "default"
    ) -> str:
        content = ""
        # Check explicit priority keys first
        for key in ["content", "entry", "text", "context", "mood", "soul", "identity", "relationship", "value"]:
            if key in function_args and function_args[key]:
                content = str(function_args[key]).strip()
                break

        # Fallback: find the first non-empty string value in function_args
        if not content:
            for k, v in function_args.items():
                if isinstance(v, str) and v.strip():
                    content = v.strip()
                    break

        if not content:
            return f"Error: '{function_name}' requires content"

        # Resolve sender_id safely
        sender_id = None
        from core.context import tool_context
        ctx = tool_context.get() or {}
        if ctx.get("sender_id"):
            sender_id = ctx["sender_id"]
        elif ":" in session_key:
            sender_id = session_key.split(":", 1)[1]
        
        if not sender_id:
            sender_id = "tool-compat"

        raw_reply = f"<{function_name}>{content}</{function_name}>"
        tag_result = await process_tags(
            raw_reply=raw_reply,
            sender_id=sender_id,
            validate_soul=prompt_module.validate_and_save_soul,
            validate_identity=prompt_module.validate_and_save_identity,
            validate_mood=prompt_module.validate_and_save_mood,
            validate_relationship=prompt_module.validate_and_save_relationships,
            vector_service=self._get_current_vector_service(),
            bus=self.bus,
            msg=None,
            config=self.config,
        )

        if tag_result.soul_updated or tag_result.identity_updated:
            self._invalidate_stable_prompt(sender_id)

        if function_name == "save_memory":
            return "Long-term memory saved."
        if function_name == "log_memory":
            return "Memory logged."
        if function_name == "save_soul":
            return "Soul saved."
        if function_name == "save_identity":
            return "Identity saved."
        if function_name == "save_mood":
            return "Mood saved."
        if function_name == "save_relationship":
            return "Relationship saved."
        if function_name == "save_user":
            return "User profile saved."
        return "Tag action completed."

    async def _execute_background_task_tool(
        self, function_name: str, function_args: Dict[str, Any]
    ) -> str:
        raw_ids = function_args.get("task_ids")
        if raw_ids is None and function_args.get("task_id") is not None:
            raw_ids = [function_args.get("task_id")]
        if isinstance(raw_ids, str):
            raw_ids = [raw_ids]
        task_ids: List[str] = []
        seen: Set[str] = set()
        for value in raw_ids or []:
            task_id = str(value or "").strip()
            if task_id and task_id not in seen:
                task_ids.append(task_id)
                seen.add(task_id)
            if len(task_ids) >= 20:
                break
        if not task_ids:
            return "Error: at least one background task_id is required."

        try:
            timeout_ms = max(0, min(int(function_args.get("timeout_ms") or 0), 300_000))
        except (TypeError, ValueError):
            timeout_ms = 0
        timeout_s = timeout_ms / 1000.0

        if function_name == "kill_task":
            task = await self.kill_background_subagent_task(task_ids[0])
            if task is None:
                return f"Task not found: {task_ids[0]}"
            if task.status == "cancelled":
                return f"Task {task.task_id} killed (cancelled)."
            return f"Task {task.task_id} already exited with status {task.status}."

        outputs: List[str] = []
        for task_id in task_ids:
            try:
                output = await self.get_background_subagent_output(
                    task_id,
                    timeout=timeout_s if timeout_s > 0 else None,
                )
            except asyncio.TimeoutError:
                output = f"Task {task_id}\nStatus: running\nWait timed out."
            if output is None:
                outputs.append(f"Task not found: {task_id}")
            else:
                outputs.append(output)
        return "\n\n".join(outputs)

    @staticmethod
    def _capability_match_score(query: str, name: str, description: str = "") -> int:
        return CapabilityService.capability_match_score(query, name, description)

    def _resolve_capabilities(
        self,
        query: str = "",
        include_disabled: bool = True,
        session_key: Optional[str] = None,
    ) -> Dict[str, Any]:
        return self._get_capability_service().resolve_capabilities(
            query,
            include_disabled=include_disabled,
            session_key=session_key,
        )

    async def _execute_capability_search(
        self, function_args: Dict[str, Any], session_key: str
    ) -> str:
        query = str(function_args.get("query") or "").strip()[:1_000]
        include_disabled = bool(function_args.get("include_disabled", True))
        result = self._resolve_capabilities(
            query,
            include_disabled=include_disabled,
            session_key=session_key,
        )
        return json.dumps(result, ensure_ascii=False, separators=(",", ":"))

    def resolve_capabilities(
        self, text: str = "", session_key: Optional[str] = None
    ) -> Dict[str, Any]:
        """Public read-only resolver used by the dashboard diagnostic endpoint."""
        return self._resolve_capabilities(
            str(text or "")[:1_000],
            include_disabled=True,
            session_key=str(session_key or "").strip()[:180] or None,
        )

    async def _execute_tool(
        self, function_name: str, function_args: dict, session_key: str
    ) -> Any:
        request_context = tool_context.get() or {}
        attachments = request_context.get("attachments") or getattr(
            self, "_turn_attachments", {}
        ).get(session_key)
        context = ToolExecutionContext(
            session_key=session_key,
            user_text=str(request_context.get("user_text") or ""),
            attachments=attachments,
            profile=get_current_profile_context(),
        )
        return await self.tool_runtime.execute(
            function_name, function_args, context
        )

    def _tool_execution_timeout(self, function_name: str) -> Optional[float]:
        """Return the outer LimeBot deadline for a native tool invocation."""
        if function_name == "generate_image":
            # Image providers can legitimately take several minutes, especially
            # for high-quality reference edits and fallback attempts. Their own
            # request timeouts still bound individual network operations.
            return None
        if function_name == "analyze_video":
            return 600.0
        return getattr(self.config, "tool_timeout", 120.0)

    @staticmethod
    def _is_coding_turn(content: str, tool_calls: Optional[List[Dict[str, Any]]] = None) -> bool:
        if _CODING_HINT_RE.search(str(content or "")):
            return True
        for tool_call in tool_calls or []:
            function = tool_call.get("function") if isinstance(tool_call, dict) else {}
            name = str((function or {}).get("name") or "")
            if name in _MUTATION_TOOL_NAMES or name in {"run_command", "run_steps"}:
                return True
        return False

    def _get_tool_outcomes(self, turn_id: Optional[str] = None) -> List[ToolOutcome]:
        """Return outcomes for this turn without crossing session boundaries."""

        if turn_id:
            scoped = self._last_tool_outcomes_by_turn.get(turn_id)
            if scoped is not None:
                return list(scoped)
        return list(getattr(self, "_last_tool_outcomes", []) or [])

    def _tool_recovery_service(self) -> ToolRecoveryService:
        """Bind recovery policy to this loop's current turn state and event ports."""
        return ToolRecoveryService(
            config=self.config,
            get_tool_definitions=self._get_tool_definitions,
            get_tool_outcomes=self._get_tool_outcomes,
            append_history=lambda session_key, entry: self._history_append(
                session_key, entry, mark_dirty=False
            ),
            mark_dirty=self._mark_dirty,
            log_session_event=self._log_session_event,
            log_tool_debug=self._log_tool_debug,
            emit_coding_phase=self._emit_coding_phase,
        )

    def _recovery_tool_definitions(
        self,
        initial: Optional[List[Dict[str, Any]]],
        state: Optional[RecoveryState],
    ) -> Optional[List[Dict[str, Any]]]:
        """Rebuild a recovery catalog from the exact registered tool names."""
        return self._tool_recovery_service().tool_definitions(initial, state)

    @staticmethod
    def _task_run_acceptance_satisfied(
        run: Any,
        outcomes: List[ToolOutcome],
        *,
        coding_turn: bool,
        iterations_limit_reached: bool,
        unresolved_tool_failure: bool,
        history: Optional[List[Dict[str, Any]]] = None,
        session_key: str = "",
        casual_turn: bool = False,
        plan_mode: bool = False,
        pending_workspaces: Optional[List[Any]] = None,
    ) -> bool:
        return acceptance_satisfied(
            run,
            outcomes,
            coding_turn=coding_turn,
            iterations_limit_reached=iterations_limit_reached,
            unresolved_tool_failure=unresolved_tool_failure,
            history=history,
            session_key=session_key,
            casual_turn=casual_turn,
            plan_mode=plan_mode,
            pending_workspaces=pending_workspaces,
        )

    def _task_run_completion_service(self) -> TaskRunCompletionService:
        """Bind durable completion policy to this turn's live task services."""
        service = getattr(self, "task_run_completion_service", None)
        if service is None:
            service = TaskRunCompletionService(
                task_runs=self.task_runs,
                evaluate_invariants=self._evaluate_operator_invariants,
                acceptance_satisfied=self._task_run_acceptance_satisfied,
                is_casual_turn=self._is_fast_casual_turn,
                schedule_continuation=self._schedule_task_run_continuation,
            )
            self.task_run_completion_service = service
        return service

    def _pending_isolated_workspaces(self) -> List[Any]:
        try:
            from core.workspace_isolation import list_pending_workspaces

            return list_pending_workspaces()
        except Exception:
            return []

    def _evaluate_operator_invariants(
        self,
        *,
        session_key: str,
        content: str,
        coding_turn: bool,
        coding_goal: bool,
        plan_mode: bool,
        outcomes: List[ToolOutcome],
        unresolved_tool_failure: bool,
        iterations_limit_reached: bool,
    ) -> Any:
        return evaluate_operator_invariants(
            casual_turn=self._is_fast_casual_turn(content),
            plan_mode=plan_mode,
            coding_turn=coding_turn,
            coding_goal=coding_goal,
            outcomes=outcomes,
            history=self.history.get(session_key, []),
            session_key=session_key,
            pending_workspaces=self._pending_isolated_workspaces(),
            unresolved_tool_failure=unresolved_tool_failure,
            iterations_limit_reached=iterations_limit_reached,
        )

    def _task_run_resume_instruction(self, run: Any) -> str:
        criteria = "\n".join(f"- {item}" for item in run.acceptance_criteria)
        checkpoint = json.dumps(run.checkpoint or {}, ensure_ascii=False, default=str)[:2500]
        return (
            "DURABLE TASK CONTINUATION. Continue the original task; this is not a new request.\n"
            f"Task ID: {run.run_id}\n"
            f"Original goal: {run.goal}\n"
            f"Current phase: {run.phase}\n"
            f"Next action: {run.next_action or 'Inspect the latest evidence and continue.'}\n"
            "Acceptance criteria:\n"
            f"{criteria}\n"
            f"Last checkpoint: {checkpoint}\n"
            f"Last error: {run.last_error or '(none)'}\n"
            "Do not repeat completed side effects. Inspect the latest result first, use the exact "
            "available tool names, make one justified correction, and verify the original goal "
            "before reporting completion."
        )

    def _recovery_tool_contract(self, state: Optional[RecoveryState]) -> str:
        return self._tool_recovery_service().tool_contract(state)

    async def _schedule_task_run_continuation(
        self,
        run: Any,
        msg: InboundMessage,
        *,
        next_action: str,
        phase: str,
        error: str = "",
        corrective_failure: bool = False,
        recovery: Optional[RecoveryState] = None,
    ) -> bool:
        """Persist and enqueue one bounded continuation slice."""
        service = TaskRunContinuationService(
            task_runs=self.task_runs,
            job_queue=self.job_queue,
            bus=self.bus,
            log_session_event=self._log_session_event,
            publish_activity=self._publish_activity,
        )
        return await service.schedule(
            run,
            msg,
            next_action=next_action,
            phase=phase,
            error=error,
            corrective_failure=corrective_failure,
            recovery=recovery,
        )

    async def _emit_coding_phase(
        self,
        phase: str,
        session_key: str,
        msg: Optional[InboundMessage],
        *,
        turn_id: Optional[str] = None,
        message_id: Optional[str] = None,
        outcome: Optional[ToolOutcome] = None,
    ) -> None:
        """Emit a bounded progress event; this deliberately has no terminal signal."""
        if phase not in CODING_PHASES:
            return
        payload: Dict[str, Any] = {"type": "coding_phase", "phase": phase}
        event: Dict[str, Any] = {"type": "coding_phase", "phase": phase}
        if outcome:
            details = {
                "tool": outcome.tool,
                "success": outcome.success,
                "exit_code": outcome.exit_code,
                "timed_out": outcome.timed_out,
                "stalled": outcome.stalled,
                "retry_safe": outcome.retry_safe,
                "failure_fingerprint": outcome.failure_fingerprint,
                "diagnostic_head": outcome.diagnostic_head,
                "diagnostic_tail": outcome.diagnostic_tail,
                "verification_status": outcome.verification_status,
                "verification_detail": outcome.verification_detail,
                "failure_category": outcome.failure_category,
                "progress": outcome.progress,
            }
            payload["outcome"] = details
            event["outcome"] = details
        self._log_session_event(session_key, event)
        await self._publish_both(
            msg,
            "",
            self._with_trace_metadata(payload, turn_id=turn_id, message_id=message_id),
        )

    def _coding_recovery_message(self, outcome: ToolOutcome, attempt: int, budget: int) -> str:
        return self._tool_recovery_service().coding_recovery_message(
            outcome, attempt, budget
        )

    def _tool_recovery_message(
        self,
        outcome: ToolOutcome,
        attempt: int,
        budget: int,
        *,
        repeated: bool = False,
    ) -> str:
        return self._tool_recovery_service().tool_recovery_message(
            outcome, attempt, budget, repeated=repeated
        )

    def _has_successful_mutation_since_previous_verifier_failure(
        self, session_key: str
    ) -> bool:
        """Allow one repeated failure only when an edit succeeded between failures."""
        return self._tool_recovery_service().has_successful_mutation_since_previous_verifier_failure(
            self.history.get(session_key, [])
        )

    def _build_step_cap_fallback(self, session_key: str) -> str:
        return self._tool_recovery_service().build_step_cap_fallback(
            self.history.get(session_key, [])
        )

    async def _queue_tool_recovery(
        self,
        session_key: str,
        msg: Optional[InboundMessage],
        fingerprints: Set[str],
        attempts: int,
        *,
        coding_turn: bool = False,
        allowed_tools: Optional[Set[str]] = None,
        recovery_state: Optional[RecoveryState] = None,
        turn_id: Optional[str] = None,
        message_id: Optional[str] = None,
    ) -> Tuple[int, Optional[str]]:
        """Queue one model-directed recovery step, or return a concrete blocker."""
        result = await self._coordinate_tool_recovery(
            session_key,
            msg,
            fingerprints,
            attempts,
            coding_turn=coding_turn,
            allowed_tools=allowed_tools,
            recovery_state=recovery_state,
            turn_id=turn_id,
            message_id=message_id,
        )
        return result.attempts, result.blocked

    async def _coordinate_tool_recovery(
        self,
        session_key: str,
        msg: Optional[InboundMessage],
        fingerprints: Set[str],
        attempts: int,
        *,
        coding_turn: bool = False,
        allowed_tools: Optional[Set[str]] = None,
        recovery_state: Optional[RecoveryState] = None,
        turn_id: Optional[str] = None,
        message_id: Optional[str] = None,
    ) -> ToolRecoveryResult:
        """Let recovery policy decide whether this turn needs another model step."""
        return await self._tool_recovery_service().queue_recovery(
            ToolRecoveryRequest(
                session_key=session_key,
                fingerprints=fingerprints,
                attempts=attempts,
                message=msg,
                coding_turn=coding_turn,
                allowed_tools=allowed_tools,
                recovery_state=recovery_state,
                turn_id=turn_id,
                message_id=message_id,
            )
        )

    async def _queue_coding_recovery(
        self,
        session_key: str,
        msg: Optional[InboundMessage],
        fingerprints: Set[str],
        attempts: int,
        *,
        turn_id: Optional[str] = None,
        message_id: Optional[str] = None,
    ) -> Tuple[int, Optional[str]]:
        """Compatibility wrapper for coding-specific callers and tests."""
        return await self._queue_tool_recovery(
            session_key,
            msg,
            fingerprints,
            attempts,
            coding_turn=True,
            allowed_tools={"run_command"},
            turn_id=turn_id,
            message_id=message_id,
        )

    def _collect_turn_tool_errors(self, session_key: str) -> List[Tuple[str, str]]:
        """Return this turn's error/rejection/policy-deny tool results."""
        return self._tool_recovery_service().collect_turn_tool_errors(
            self.history.get(session_key, [])
        )

    def _ensure_reply_includes_tool_errors(
        self, reply: str, session_key: str
    ) -> str:
        """Make sure a failed tool's exact text is visible in the user reply."""
        service = self._tool_recovery_service()
        errors = service.collect_turn_tool_errors(self.history.get(session_key, []))
        return service.ensure_reply_includes_tool_errors(reply, errors)

    def _ensure_reply_includes_operator_failures(
        self,
        reply: str,
        session_key: str,
        *,
        verdict: Any = None,
        task_run: Any = None,
        extra_texts: Optional[List[str]] = None,
    ) -> str:
        """No silent fail: tool errors, verify/merge gaps, and blocked runs stay visible."""
        visible = self._ensure_reply_includes_tool_errors(reply, session_key)
        required: List[str] = list(extra_texts or [])
        if task_run is not None and str(getattr(task_run, "status", "") or "") == TASK_RUN_BLOCKED:
            blocked_error = str(getattr(task_run, "last_error", "") or "").strip()
            if blocked_error:
                required.append(blocked_error)
        visible = ensure_visible_failures(visible, verdict, required_texts=required)
        if (
            verdict is not None
            and getattr(verdict, "applies", False)
            and not getattr(verdict, "ok", True)
            and not str(visible or "").strip()
        ):
            visible = format_invariant_next_action(verdict)
        return visible

    def _turn_already_delivered_media(self, turn_id: Optional[str]) -> bool:
        """True when a photo/file already went out for this turn."""
        toolbox = getattr(self, "toolbox", None)
        checker = getattr(toolbox, "media_delivered_this_turn", None)
        return bool(callable(checker) and checker(turn_id))

    def _build_tool_fallback_reply(self, session_key: str, max_items: int = 2) -> str:
        return self._tool_recovery_service().build_tool_fallback_reply(
            self.history.get(session_key, []), max_items=max_items
        )

    @staticmethod
    def _is_read_only_plan_request(content: str, msg: Optional[InboundMessage]) -> bool:
        metadata = getattr(msg, "metadata", {}) if msg else {}
        requested_mode = str((metadata or {}).get("coding_mode") or "").lower()
        return requested_mode == "plan" or str(content or "").strip().lower().startswith("/plan")

    @staticmethod
    def _workspace_id_from_message(msg: Optional[InboundMessage]) -> str:
        metadata = getattr(msg, "metadata", {}) if msg else {}
        return str((metadata or {}).get("workspace_id") or "").strip()

    def _workspace_review_service(self) -> WorkspaceReviewService:
        """Bind review-artifact ownership to the current loop's UI and parser ports."""
        service = getattr(self, "workspace_review_service", None)
        if service is None:
            service = WorkspaceReviewService(
                parse_tool_call=self._parse_tool_call,
                build_confirmation_preview=self._build_confirmation_preview,
                workspace_id_from_message=self._workspace_id_from_message,
                publish_changeset=self._publish_changeset,
                publish_coding_plan=self._publish_coding_plan_artifact,
            )
            self.workspace_review_service = service
        return service

    async def _publish_changeset(
        self,
        changeset: Dict[str, Any],
        msg: Optional[InboundMessage],
        *,
        turn_id: Optional[str] = None,
        message_id: Optional[str] = None,
    ) -> None:
        await self._publish_both(
            msg,
            "",
            self._with_trace_metadata(
                {"type": "changeset", "changeset": changeset},
                turn_id=turn_id,
                message_id=message_id,
            ),
        )

    async def _publish_coding_plan_artifact(
        self,
        plan: Dict[str, Any],
        msg: Optional[InboundMessage],
        *,
        turn_id: Optional[str] = None,
        message_id: Optional[str] = None,
    ) -> None:
        await self._publish_both(
            msg,
            "",
            self._with_trace_metadata(
                {"type": "coding_plan", "plan": plan},
                turn_id=turn_id,
                message_id=message_id,
            ),
        )

    async def _stage_workspace_changeset(
        self,
        tool_calls: List[Dict[str, Any]],
        session_key: str,
        msg: Optional[InboundMessage],
        *,
        turn_id: Optional[str] = None,
        message_id: Optional[str] = None,
    ) -> Optional[Dict[str, Any]]:
        return await self._workspace_review_service().stage_changeset(
            tool_calls,
            session_key,
            msg,
            turn_id=turn_id,
            message_id=message_id,
        )

    async def _refresh_workspace_changeset(
        self,
        session_key: str,
        msg: Optional[InboundMessage],
        outcome: ToolOutcome,
        *,
        blocked: bool = False,
        turn_id: Optional[str] = None,
        message_id: Optional[str] = None,
    ) -> None:
        await self._workspace_review_service().refresh_changeset(
            session_key,
            msg,
            outcome,
            blocked=blocked,
            turn_id=turn_id,
            message_id=message_id,
        )

    async def _persist_coding_plan(
        self,
        plan_text: str,
        msg: Optional[InboundMessage],
        *,
        turn_id: Optional[str] = None,
        message_id: Optional[str] = None,
    ) -> None:
        await self._workspace_review_service().persist_coding_plan(
            plan_text,
            msg,
            turn_id=turn_id,
            message_id=message_id,
        )

    @staticmethod
    def _build_empty_reply_fallback() -> str:
        return (
            "I processed that, but I failed to produce a visible reply. "
            "Please ask again if you still need the answer."
        )

    def _parse_tool_call(
        self, tool_call: dict, session_key: str
    ) -> Tuple[str, str, Dict[str, Any]]:
        tc_id = tool_call.get("id") or f"call_{uuid.uuid4().hex[:8]}"
        tool_call["id"] = tc_id
        function = tool_call.get("function")
        if not isinstance(function, dict):
            function = {"name": "", "arguments": "{}"}
            tool_call["function"] = function

        function_name = function.get("name", "")
        raw_args = function.get("arguments", "{}")

        if isinstance(raw_args, dict):
            function_args = raw_args
            raw_args = json.dumps(raw_args)
            function["arguments"] = raw_args
        elif raw_args in (None, ""):
            raw_args = "{}"
            function["arguments"] = raw_args

        if raw_args == "{}{}":
            logger.warning(f"Malformed args for {function_name} - fixing to {{}}")
            self.metrics.record_anomaly(
                session_key,
                "malformed_tool_args",
                detail=f"{function_name}:double_object",
            )
            raw_args = "{}"
            function["arguments"] = raw_args

        if not isinstance(raw_args, str):
            raw_args_type = type(raw_args).__name__
            function_args = {}
            raw_args = "{}"
            function["arguments"] = raw_args
            logger.error(
                f"Invalid non-string args for '{function_name}'. Using {{}}."
            )
            self.metrics.record_anomaly(
                session_key,
                "invalid_tool_args_type",
                detail=f"{function_name}:{raw_args_type}",
            )
        else:
            raw_args_detail = raw_args
            try:
                function_args = json.loads(raw_args)
            except json.JSONDecodeError:
                function_args = {}
                raw_args = "{}"
                function["arguments"] = raw_args
                logger.error(f"Invalid JSON args for '{function_name}'. Using {{}}.")
                self.metrics.record_anomaly(
                    session_key,
                    "invalid_tool_args_json",
                    detail=f"{function_name}:{raw_args_detail[:120]}",
                )

        if not isinstance(function_args, dict):
            function_args = {}
            raw_args_detail = str(raw_args)
            raw_args = "{}"
            function["arguments"] = raw_args
            logger.error(
                f"Non-object JSON args for '{function_name}'. Using {{}}."
            )
            self.metrics.record_anomaly(
                session_key,
                "non_object_tool_args",
                detail=f"{function_name}:{raw_args_detail[:120]}",
            )

        function_name, function_args = self._normalize_tool_alias(
            function_name, function_args, session_key
        )
        function["name"] = function_name
        function["arguments"] = json.dumps(function_args)
        return tc_id, function_name, function_args

    def _sanitize_messages_for_llm(
        self, messages: List[Dict[str, Any]], session_key: str
    ) -> List[Dict[str, Any]]:
        """Repair tool history and assistant arguments before sending upstream."""
        normalized, changed = self._normalize_tool_history_messages(messages)
        if changed:
            # Preserve the caller's list identity.  Some provider adapters and
            # tests intentionally hold a reference to the session history.
            messages[:] = normalized
            if getattr(self, "history", {}).get(session_key) is messages:
                self._mark_dirty(session_key)
        for message in messages:
            if not isinstance(message, dict):
                continue
            tool_calls = message.get("tool_calls")
            if not isinstance(tool_calls, list):
                continue
            for tool_call in tool_calls:
                if not isinstance(tool_call, dict):
                    continue
                function = tool_call.get("function")
                if not isinstance(function, dict):
                    tool_call["function"] = {"name": "", "arguments": "{}"}
                    function = tool_call["function"]
                function.setdefault("name", "")
                function.setdefault("arguments", "{}")
                self._parse_tool_call(tool_call, session_key)
        return messages

    async def _publish_tool_intents(
        self,
        tool_calls: List[Dict[str, Any]],
        session_key: str,
        msg: Optional[InboundMessage],
        turn_id: Optional[str] = None,
        message_id: Optional[str] = None,
    ) -> None:
        await self._tool_batch_event_service().publish_planned(
            tool_calls,
            ToolBatchEventContext(
                session_key=session_key,
                message=msg,
                turn_id=turn_id,
                message_id=message_id,
            ),
        )

    async def _execute_tool_batch(
        self,
        tool_calls: list,
        session_key: str,
        msg: Optional[InboundMessage],
        turn_id: Optional[str] = None,
        message_id: Optional[str] = None,
        coding_turn: bool = False,
        on_progress_queued: Optional[Callable[[str], None]] = None,
        recovery_state: Optional[RecoveryState] = None,
    ) -> bool:
        """Run reads concurrently, but keep stateful steps in causal model order."""
        any_blocked = False
        batch_tool_outcomes: List[ToolOutcome] = []
        event_service = self._tool_batch_event_service()
        event_context = ToolBatchEventContext(
            session_key=session_key,
            message=msg,
            turn_id=turn_id,
            message_id=message_id,
        )

        async def _run_one(tool_call: dict):
            tc_id = tool_call["id"]
            function_name = tool_call["function"]["name"]
            is_internal = False

            try:
                current_image_attachments = [
                    dict(attachment)
                    for attachment in (
                        (msg.metadata.get("attachments") or []) if msg else []
                    )
                    if isinstance(attachment, dict)
                    and attachment.get("kind") == "image"
                ]
                recent_image_attachments = self._get_recent_image_attachments(
                    session_key
                )
                tool_context.set(
                    {
                        "tc_id": tc_id,
                        "channel": msg.channel if msg else "system",
                        "sender_id": msg.sender_id if msg else "system",
                        "chat_id": msg.chat_id if msg else "system",
                        "turn_id": turn_id or "",
                        "message_id": message_id or "",
                        "user_text": str(msg.content if msg else "")[:4000],
                        "attachments": current_image_attachments,
                        "recent_image_attachments": recent_image_attachments,
                        "auto_reference_images": bool(current_image_attachments)
                        or bool(
                            recent_image_attachments
                            and self._message_refers_to_recent_image(
                                msg.content if msg else ""
                            )
                        ),
                    }
                )

                tc_id, function_name, function_args = self._parse_tool_call(
                    tool_call, session_key
                )

                if recovery_state is not None:
                    gated = recovery_state.gate(function_name, function_args)
                    if gated:
                        return (
                            tc_id,
                            function_name,
                            function_args,
                            f"Error: {gated}",
                            False,
                            is_internal,
                        )

                if coding_turn:
                    await self._emit_coding_phase(
                        tool_phase(function_name, function_args),
                        session_key,
                        msg,
                        turn_id=turn_id,
                        message_id=message_id,
                    )

                logger.info(
                    f"Executing: {function_name}({redact_sensitive_text(function_args)})"
                )

                is_internal = False
                is_whatsapp = (
                    msg is not None and getattr(msg, "channel", "") == "whatsapp"
                )

                if function_name == "run_command":
                    validation_error = self.toolbox.validate_command(
                        str(function_args.get("command") or "")
                    )
                    if validation_error:
                        return (
                            tc_id,
                            function_name,
                            function_args,
                            validation_error,
                            False,
                            is_internal,
                        )
                if function_name == "run_steps":
                    for step in function_args.get("commands") or []:
                        validation_error = self.toolbox.validate_command(str(step or ""))
                        if validation_error:
                            return (
                                tc_id,
                                function_name,
                                function_args,
                                validation_error,
                                False,
                                is_internal,
                            )

                if function_name in _SENSITIVE_TOOLS:
                    approval_result = await self._tool_approval_flow_service().authorize(
                        ToolApprovalRequest(
                            session_key=session_key,
                            tool_call_id=tc_id,
                            function_name=function_name,
                            function_args=function_args,
                            message=msg,
                            turn_id=turn_id,
                            message_id=message_id,
                            is_internal=is_internal,
                            is_whatsapp=is_whatsapp,
                        )
                    )
                    if not approval_result.proceed:
                        return (
                            tc_id,
                            function_name,
                            function_args,
                            approval_result.rejection,
                            False,
                            is_internal,
                        )

                await event_service.publish_running(
                    event_context,
                    tool_call_id=tc_id,
                    function_name=function_name,
                    function_args=function_args,
                    is_whatsapp=is_whatsapp,
                    on_progress_queued=on_progress_queued,
                )

                t0 = time.time()
                try:
                    # Image generation deliberately has no outer LimeBot deadline;
                    # each provider request retains its own transport timeout.
                    tool_timeout = self._tool_execution_timeout(function_name)
                    if tool_timeout and tool_timeout > 0:
                        result = await asyncio.wait_for(
                            self._execute_tool(
                                function_name, function_args, session_key
                            ),
                            timeout=tool_timeout,
                        )
                    else:
                        result = await self._execute_tool(
                            function_name, function_args, session_key
                        )
                except asyncio.TimeoutError:
                    timeout_msg = (
                        f"{int(tool_timeout)}s"
                        if tool_timeout and tool_timeout > 0
                        else "configured limit"
                    )
                    result = (
                        f"Error: Tool '{function_name}' timed out after {timeout_msg}."
                    )
                    logger.error(result)

                self.metrics.record_tool_call(
                    session_key, function_name, time.time() - t0
                )
                durable_job_id = (
                    msg.metadata.get("durable_job_id")
                    if msg is not None and isinstance(msg.metadata, dict)
                    else None
                )
                if durable_job_id and not str(result).startswith(
                    ("Error:", "ACTION CANCELLED:", "ACTION BLOCKED:")
                ):
                    getattr(self, "job_queue", None) and self.job_queue.mark_side_effect(
                        str(durable_job_id), function_name
                    )

            except Exception as e:
                result = str(e)
                function_args = locals().get("function_args", {})
                self.metrics.record_tool_call(session_key, function_name, 0, error=True)

            is_blocked = str(result).startswith("ACTION BLOCKED:")
            event_service.record_finished(
                event_context,
                tool_call_id=tc_id,
                function_name=function_name,
                result=result,
                is_blocked=is_blocked,
                is_internal=is_internal,
            )
            return tc_id, function_name, function_args, result, is_blocked, is_internal

        async def _consume_outcome(i: int, _tool_call: dict, outcome: Any) -> None:
            nonlocal any_blocked
            if isinstance(outcome, asyncio.CancelledError):
                # A cancelled child is a cancelled turn, not an ordinary tool
                # failure.  Re-raise it so _process_message can emit the
                # cancellation terminal event and stop the remaining work.
                raise outcome
            if isinstance(outcome, BaseException):
                logger.error(f"⚠ Tool batch exception: {outcome}")
                # We need to broadcast an error so the UI doesn't get stuck in 'Running'
                # Extract basic info from the original tool_calls list
                fail_tc = tool_calls[i]
                fail_tc_id = fail_tc.get("id", "unknown")
                fail_name = fail_tc.get("function", {}).get("name", "unknown")
                fail_args = {}
                try:
                    fail_args = json.loads(
                        fail_tc.get("function", {}).get("arguments", "{}")
                    )
                except Exception:
                    pass

                await event_service.publish_failure(
                    event_context,
                    tool_call_id=fail_tc_id,
                    function_name=fail_name,
                    function_args=fail_args,
                    exception_type=type(outcome).__name__,
                )
                failure_result = (
                    f"Error executing {fail_name}: {type(outcome).__name__}"
                )
                batch_tool_outcomes.append(
                    build_tool_outcome(fail_name, failure_result)
                )
                if recovery_state is not None:
                    recovery_state.observe(batch_tool_outcomes[-1], fail_args)
                self._history_append(
                    session_key,
                    {
                        "role": "tool",
                        "tool_call_id": fail_tc_id,
                        "name": fail_name,
                        "content": failure_result,
                    }
                )
                return

            tc_id, function_name, function_args, result, is_blocked, is_internal = (
                outcome
            )
            if is_blocked:
                any_blocked = True

            clean_result, tool_images = self._extract_tool_media_payload(result)
            if clean_result:
                result = clean_result

            outcome_details = build_tool_outcome(function_name, result)
            batch_tool_outcomes.append(outcome_details)
            if recovery_state is not None:
                recovery_state.observe(outcome_details, function_args)
            if coding_turn:
                await self._emit_coding_phase(
                    tool_phase(function_name, function_args),
                    session_key,
                    msg,
                    turn_id=turn_id,
                    message_id=message_id,
                    outcome=outcome_details,
                )
                await self._refresh_workspace_changeset(
                    session_key,
                    msg,
                    outcome_details,
                    blocked=is_blocked,
                    turn_id=turn_id,
                    message_id=message_id,
                )

            if not is_internal:
                await event_service.publish_result(
                    event_context,
                    tool_call_id=tc_id,
                    function_name=function_name,
                    function_args=function_args,
                    result=result,
                )

            limit = _TOOL_RESULT_LIMITS.get(function_name, _DEFAULT_TOOL_RESULT_LIMIT)
            str_result = truncate_tool_result(result, limit)

            self._history_append(
                session_key,
                {
                    "role": "tool",
                    "tool_call_id": tc_id,
                    "name": function_name,
                    "content": str_result,
                }
            )

            if tool_images and not is_internal:
                self._history_append(
                    session_key,
                    {
                        "role": "user",
                        "content": self._build_tool_image_followup_content(
                            function_name, tool_images
                        ),
                    }
                )
                self._sessions_pending_tool_image_reply.add(session_key)

        # The executor owns concurrency, barriers, and ordered outcome
        # consumption. `_run_one` supplies the per-call policy and execution;
        # `_consume_outcome` applies each result in model order.
        await self.tool_batch_executor.execute_ordered(
            tool_calls, _run_one, on_outcome=_consume_outcome
        )

        if turn_id:
            self._last_tool_outcomes_by_turn[turn_id] = list(batch_tool_outcomes)
            # Keep only a bounded tail for turns whose caller is interrupted
            # before the outer finally block can clean up.
            if len(self._last_tool_outcomes_by_turn) > 128:
                oldest_turn = next(iter(self._last_tool_outcomes_by_turn))
                if oldest_turn != turn_id:
                    self._last_tool_outcomes_by_turn.pop(oldest_turn, None)
        # Backward-compatible diagnostic surface for direct callers. The turn
        # scoped map above is the authoritative source during real turns.
        self._last_tool_outcomes = list(batch_tool_outcomes)
        return any_blocked

    async def send_tool_progress(
        self, tool_call_id: str, chat_id: str, content: str
    ) -> None:
        ctx = tool_context.get() or {}
        await self.bus.publish_outbound(
            OutboundMessage(
                channel="web",
                chat_id=chat_id,
                content=content,
                metadata={
                    "type": "tool_execution",
                    "status": "progress",
                    "tool_call_id": tool_call_id,
                    "turn_id": ctx.get("turn_id") or None,
                    "message_id": ctx.get("message_id") or None,
                },
            )
        )


    async def _consume_stream(
        self,
        response_stream,
        msg: InboundMessage,
        session_key: str,
        previous_content: str = "",
        turn_id: Optional[str] = None,
        message_id: Optional[str] = None,
        turn_started_at: Optional[float] = None,
        llm_started_at: Optional[float] = None,
        iteration_kind: str = "initial",
        iteration: int = 0,
        on_output_queued: Optional[Callable[[str], None]] = None,
    ):
        return await consume_stream(
            response_stream=response_stream,
            msg=msg,
            session_key=session_key,
            previous_content=previous_content,
            turn_id=turn_id,
            message_id=message_id,
            turn_started_at=turn_started_at,
            llm_started_at=llm_started_at,
            iteration_kind=iteration_kind,
            iteration=iteration,
            on_output_queued=on_output_queued,
            bus=self.bus,
            metrics=self.metrics,
            session_manager=self.session_manager,
            get_model=lambda: self.model,
            log_tool_debug=self._log_tool_debug,
            record_stage_timing=self._record_stage_timing,
            with_trace_metadata=self._with_trace_metadata,
            tool_call_debug_rows=self._tool_call_debug_rows,
        )


    async def _process_message(
        self, msg: InboundMessage, _task_id: Optional[str] = None
    ) -> None:
        session_key = msg.session_key
        turn_id = f"turn_{uuid.uuid4().hex[:12]}"
        assistant_message_id = f"msg_{uuid.uuid4().hex[:12]}"
        turn_state = TurnState(
            turn_id=turn_id,
            message_id=assistant_message_id,
            session_key=session_key,
        )
        turn_started = time.perf_counter()
        tool_batch_duration_s = 0.0
        tool_batch_count = 0
        tool_batch_blocked = False
        unresolved_tool_failure = False
        unresolved_failure_detail = ""
        task_run = None
        task_run_id = ""
        task_run_resume = bool(
            isinstance(getattr(msg, "metadata", None), dict)
            and (
                msg.metadata.get("task_run_resume")
                or msg.metadata.get("task_run_followup")
            )
        )
        task_run_continuation_scheduled = False
        task_run_progress_reply = ""
        iterations_limit_reached = False
        coding_turn = False
        all_tool_outcomes: List[ToolOutcome] = []
        recovery_state: Optional[RecoveryState] = None
        recovery_blocked: Optional[str] = None
        recovery_required = False
        turn_status_override: Optional[str] = None
        turn_error_code = ""
        operator_verdict = None

        def make_output_queued_recorder(
            iteration_kind: str, iteration: int
        ) -> Callable[[str], None]:
            recorded = False

            def record(output_kind: str) -> None:
                nonlocal recorded
                if recorded:
                    return
                recorded = True
                self._record_stage_timing(
                    session_key,
                    "turn_first_output_queued",
                    turn_started,
                    metadata={
                        "iteration_kind": iteration_kind,
                        "iteration": iteration,
                        "delta_kind": output_kind,
                    },
                )

            return record

        # ── Task tracking ────────────────────────────────────────────────
        from core.task_tracker import get_task_tracker
        _tracker = get_task_tracker()
        _msg_task_id = _task_id
        task_context_token = (
            _CURRENT_TASK_ID.set(_task_id) if _task_id else None
        )

        try:
            if _msg_task_id:
                await _tracker.update_task(
                    _msg_task_id,
                    status="running",
                    metadata_update={
                        "turn_id": turn_id,
                        "sender_id": msg.sender_id,
                    },
                )
            else:
                _msg_task_id = await _tracker.create_task(
                    task_type="inbound_message",
                    summary=f"{msg.channel}: {(msg.content or '')[:80]}",
                    channel=msg.channel,
                    session_key=session_key,
                    chat_id=msg.chat_id,
                    metadata={"turn_id": turn_id, "sender_id": msg.sender_id},
                )
                task_context_token = _CURRENT_TASK_ID.set(_msg_task_id)
                await _tracker.update_task(_msg_task_id, status="running")

            durable_id = str(
                (msg.metadata or {}).get("task_run_id")
                or (msg.metadata or {}).get("durable_job_id")
                or _msg_task_id
                or uuid.uuid4().hex
            ).strip()
            task_run_id = durable_id
            try:
                task_run_max_slices = max(
                    1, int(os.getenv("TASK_RUN_MAX_SLICES", "20") or 20)
                )
            except (TypeError, ValueError):
                task_run_max_slices = 20
            try:
                task_run_max_corrective_failures = max(
                    1,
                    int(
                        os.getenv(
                            "TOOL_RECOVERY_MAX_CORRECTIVE_FAILURES", "5"
                        )
                        or 5
                    ),
                )
            except (TypeError, ValueError):
                task_run_max_corrective_failures = 5
            task_run = self.task_runs.create_or_get(
                task_run_id,
                msg.content or "",
                session_key=session_key,
                workspace_id=str((msg.metadata or {}).get("workspace_id") or ""),
                provider=str(self.model or ""),
                metadata={
                    "channel": msg.channel,
                    "chat_id": msg.chat_id,
                    "sender_id": msg.sender_id,
                    "profile_id": (
                        msg.metadata.get("profile_id", "default")
                        if isinstance(msg.metadata, dict)
                        else "default"
                    ),
                    "profile_name": (
                        msg.metadata.get("profile_name", "Default")
                        if isinstance(msg.metadata, dict)
                        else "Default"
                    ),
                    "durable_job_id": str((msg.metadata or {}).get("durable_job_id") or ""),
                },
                max_slices=task_run_max_slices,
                max_corrective_failures=task_run_max_corrective_failures,
            )
            if not task_run_resume and task_run.status in {
                "running",
                "planning",
                "executing",
                "verifying",
                "repairing",
                "retrying",
            }:
                # A durable job can be replayed after a process crash before
                # its continuation metadata reaches the inbound payload.
                task_run_resume = True
            recovery_state = RecoveryState.from_dict(
                (task_run.checkpoint or {}).get("recovery"), task_run.goal
            )
            if task_run_resume and task_run.resumable:
                task_run = self.task_runs.start(task_run.run_id, phase=task_run.phase)
            elif not task_run_resume and task_run.status == "queued":
                task_run = self.task_runs.start(task_run.run_id, phase=TASK_RUN_PLANNING)
            if task_run is not None:
                await _tracker.update_task(
                    _msg_task_id,
                    metadata_update={
                        "task_run_id": task_run.run_id,
                        "task_run_phase": task_run.phase,
                        "task_run_status": task_run.status,
                        "acceptance_criteria": task_run.acceptance_criteria,
                    },
                )

            content = msg.content or ""
            attachments = [
                attachment
                for attachment in (msg.metadata.get("attachments") or [])
                if isinstance(attachment, dict)
            ]
            self._turn_attachments[session_key] = attachments
            self._remember_image_attachments(session_key, attachments)
            self._log_session_event(
                session_key,
                {
                    "type": "inbound_message",
                    "turn_id": turn_id,
                    "channel": msg.channel,
                    "chat_id": msg.chat_id,
                    "sender_id": msg.sender_id,
                    "profile_id": (
                        msg.metadata.get("profile_id", "default")
                        if isinstance(msg.metadata, dict)
                        else "default"
                    ),
                    "profile_name": (
                        msg.metadata.get("profile_name", "Default")
                        if isinstance(msg.metadata, dict)
                        else "Default"
                    ),
                    "is_scheduler": bool(msg.metadata.get("is_scheduler")),
                    "content_preview": content[:500],
                    "attachments": [
                        str(attachment.get("name") or "attachment")
                        for attachment in attachments
                    ],
                },
            )
            if content.startswith("[SCHEDULER] "):
                content = content.replace("[SCHEDULER] ", "").strip()

            if content == "@reflect_and_distill":
                from core.reflection import get_reflection_service

                svc = get_reflection_service(self.bus, self.model)
                reply = await svc.run_reflection_cycle()
                try:
                    tmp = Path("temp_reflect_log.txt")
                    if tmp.exists():
                        tmp.unlink()
                except Exception as e:
                    logger.warning(f"Cleanup error: {e}")

                await process_tags(
                    raw_reply=reply,
                    sender_id=msg.sender_id,
                    validate_soul=prompt_module.validate_and_save_soul,
                    validate_identity=prompt_module.validate_and_save_identity,
                    validate_mood=prompt_module.validate_and_save_mood,
                    validate_relationship=prompt_module.validate_and_save_relationships,
                    vector_service=self._get_current_vector_service(),
                    bus=self.bus,
                    msg=msg,
                    config=self.config,
                )
                await self.bus.publish_outbound(
                    OutboundMessage(
                        channel="web",
                        chat_id="global",
                        content="✨ Background reflection complete.",
                        metadata=self._with_trace_metadata(
                            {"type": "maintenance", "is_maintenance": True},
                            turn_id=turn_id,
                        ),
                    )
                )
                if task_run is not None:
                    self.task_runs.complete(
                        task_run.run_id,
                        result="Background reflection complete.",
                        checkpoint={**dict(task_run.checkpoint or {}), "maintenance": True},
                    )
                return

            if (
                not msg.metadata.get("mentioned")
                and not msg.metadata.get("is_dm")
                and msg.channel == "discord"
            ):
                if task_run is not None:
                    self.task_runs.complete(
                        task_run.run_id,
                        result="Ignored ambient Discord message.",
                        checkpoint={**dict(task_run.checkpoint or {}), "ignored": True},
                    )
                return

            if not (
                msg.metadata.get("is_scheduler") or is_unattended_turn(msg)
            ):
                if not self.get_readiness_status()["ready"]:
                    await self._publish_activity(
                        msg,
                        "Preparing skills and tools...",
                        turn_id=turn_id,
                        message_id=assistant_message_id,
                    )
                readiness = await self.await_ready()
                if not readiness["ready"]:
                    failure_code = readiness.get("failure_code") or "agent_not_ready"
                    if _msg_task_id:
                        await _tracker.complete_task(
                            _msg_task_id, error=failure_code
                        )
                    reply = (
                        "LimeBot is still preparing its skills and tools. Please retry in a moment."
                        if readiness["status"] == "timeout"
                        else "LimeBot could not load its required capabilities. Run `limebot doctor` and check the startup logs."
                    )
                    await self.bus.publish_outbound(
                        OutboundMessage(
                            channel=msg.channel,
                            chat_id=msg.chat_id,
                            content=reply,
                            metadata=self._with_trace_metadata(
                                {
                                    "is_error": True,
                                    "event_kind": "transport_error",
                                    "terminal": True,
                                    "turn_status": "failed",
                                    "reply_to": msg.sender_id,
                                    "error_code": failure_code,
                                },
                                turn_id=turn_id,
                                message_id=assistant_message_id,
                            ),
                        )
                    )
                    if task_run is not None:
                        self.task_runs.block(
                            task_run.run_id,
                            error=failure_code,
                            next_action="Retry after required capabilities are ready.",
                        )
                    return

            sender_id = msg.sender_id
            metadata_skill_name = str(msg.metadata.get("skill_name") or "").strip()
            if metadata_skill_name:
                content, forced_skill_name, skill_error = self._resolve_requested_skill(
                    metadata_skill_name,
                    content,
                    raw_content=content,
                )
            else:
                content, forced_skill_name, skill_error = self._resolve_skill_invocation(
                    content
                )
            if skill_error:
                await self.bus.publish_outbound(
                    OutboundMessage(
                        channel=msg.channel,
                        chat_id=msg.chat_id,
                        content=skill_error,
                        metadata=self._with_trace_metadata(
                            {"reply_to": msg.sender_id, "type": "warning"},
                            turn_id=turn_id,
                            message_id=assistant_message_id,
                        ),
                    )
                )
                if task_run is not None:
                    self.task_runs.block(
                        task_run.run_id,
                        error="Requested skill invocation could not be resolved.",
                        next_action="Use a registered skill name or ask LimeBot to list skills.",
                    )
                return
            normalized = content.strip().lower()
            is_stop_request = normalized in _DENY_WORDS or any(
                normalized.startswith(k + " ") for k in _DENY_WORDS
            )

            dedup_source = None
            if msg.content:
                message_id = (
                    str(msg.metadata.get("message_id") or "").strip()
                    if msg.metadata
                    else ""
                )
                if message_id:
                    dedup_source = f"message:{message_id}"
                else:
                    dedup_source = f"sender:{msg.sender_id}\ncontent:{msg.content}"

            msg_hash = hash(dedup_source) if dedup_source else None
            if msg_hash is not None and not is_stop_request:
                now_ts = asyncio.get_running_loop().time()
                last_seen = self._last_msg_hash.get(session_key)
                if last_seen is not None:
                    last_hash, last_ts = last_seen
                    if msg_hash == last_hash and (now_ts - last_ts) <= 2.0:
                        if (
                            session_key in self.session_locks
                            and self.session_locks[session_key].locked()
                        ):
                            logger.warning(
                                f"♻️ Message '{msg.content[:20]}...' skipped - session {session_key} is currently BUSY processing another task."
                            )
                        else:
                            logger.debug("♻️ Skipping identical duplicate message.")
                        if task_run is not None:
                            self.task_runs.complete(
                                task_run.run_id,
                                result="Duplicate message ignored.",
                                checkpoint={**dict(task_run.checkpoint or {}), "deduplicated": True},
                            )
                        return
                self._last_msg_hash[session_key] = (msg_hash, now_ts)

            # ── Confirmation intercept (WhatsApp / Discord) ──────────────────
            # The web UI resolves confirmations via a REST button click.
            # On other channels the user types a reply — intercept it here
            # before it enters the full agent loop so the asyncio.Event fires
            # and the waiting tool call is unblocked immediately.
            if msg.channel != "web":
                if is_stop_request:
                    cancelled = await self.cancel_session(session_key)
                    if cancelled:
                        await self.bus.publish_outbound(
                            OutboundMessage(
                                channel=msg.channel,
                                chat_id=msg.chat_id,
                                content="🛑 Stopped the current run.",
                                metadata=self._with_trace_metadata(
                                    {"reply_to": msg.sender_id},
                                    turn_id=turn_id,
                                    message_id=assistant_message_id,
                                ),
                            )
                        )
                        if task_run is not None:
                            self.task_runs.cancel(
                                task_run.run_id, reason="User cancelled the task."
                            )
                        return

                pending_for_session = self.confirm.for_session(session_key)
                if pending_for_session:
                    is_approve = normalized in _APPROVE_WORDS or any(
                        normalized.startswith(k + " ") for k in _APPROVE_WORDS
                    )
                    is_deny = normalized in _DENY_WORDS or any(
                        normalized.startswith(k + " ") for k in _DENY_WORDS
                    )
                    if is_approve or is_deny:
                        for conf_id, _ in pending_for_session:
                            await self.confirm_tool(
                                conf_id,
                                approved=is_approve,
                                source=msg.channel,
                            )
                        reply_text = (
                            "✅ Approved — executing..."
                            if is_approve
                            else "❌ Cancelled."
                        )
                        await self.bus.publish_outbound(
                            OutboundMessage(
                                channel=msg.channel,
                                chat_id=msg.chat_id,
                                content=reply_text,
                                metadata=self._with_trace_metadata(
                                    {"reply_to": msg.sender_id},
                                    turn_id=turn_id,
                                    message_id=assistant_message_id,
                                ),
                            )
                        )
                        if task_run is not None:
                            self.task_runs.complete(
                                task_run.run_id,
                                result=reply_text,
                                checkpoint={
                                    **dict(task_run.checkpoint or {}),
                                    "control_message": True,
                                },
                            )
                        return

            attachment_summary = self._build_attachment_summary(attachments)
            document_attachment_context = self._build_document_attachment_context(
                attachments
            )
            user_text_content = self._join_message_sections(
                content, attachment_summary, document_attachment_context
            )
            content = self._join_message_sections(content, attachment_summary) or content

            await self.bus.publish_outbound(
                OutboundMessage(
                    channel=msg.channel,
                    chat_id=msg.chat_id,
                    content="",
                    metadata=self._with_trace_metadata(
                        {"type": "typing"},
                        turn_id=turn_id,
                        message_id=assistant_message_id,
                    ),
                )
            )

            current_task = asyncio.current_task()
            if current_task:
                self.active_tasks[session_key] = current_task

            self.session_locks.setdefault(session_key, asyncio.Lock())

            async with self.session_locks[session_key]:
                try:
                    preparation = await self._get_turn_preparation_service().prepare(
                        msg,
                        content,
                        session_key,
                        turn_id=turn_id,
                        message_id=assistant_message_id,
                    )
                    recalled_context = preparation.recalled_context
                    persisted = preparation.persisted_history

                    sender_name = msg.metadata.get("sender_name", "")
                    ponytail_mode = normalize_ponytail_mode(
                        msg.metadata.get("ponytail_mode")
                    )
                    prompt_stage_started = time.perf_counter()
                    system_prompt = await self._build_full_system_prompt(
                        sender_id,
                        channel=msg.channel,
                        chat_id=msg.chat_id,
                        recalled_context=recalled_context,
                        sender_name=sender_name,
                        current_message=content,
                        forced_skill_name=forced_skill_name,
                        ponytail_mode=ponytail_mode,
                        session_key=session_key,
                    )
                    prompt_stage_metadata = {
                        "has_recalled_context": bool(recalled_context)
                    }
                    if forced_skill_name:
                        prompt_stage_metadata["forced_skill"] = forced_skill_name
                    if ponytail_mode != "off":
                        prompt_stage_metadata["ponytail_mode"] = ponytail_mode
                    self._record_stage_timing(
                        session_key,
                        "prompt_build",
                        prompt_stage_started,
                        metadata=prompt_stage_metadata,
                    )

                    if session_key not in self.history:
                        if persisted:
                            self._history_replace(session_key, persisted)
                            logger.info(
                                f"♻ Restored {session_key} ({len(persisted)} msgs)"
                            )
                        else:
                            self._history_replace(session_key, [
                                {"role": "system", "content": system_prompt}
                            ])

                    self._history_replace_entry(
                        session_key,
                        0,
                        {"role": "system", "content": system_prompt},
                    )

                    image_urls = self._collect_image_inputs(
                        attachments,
                        msg.metadata.get("images") or [],
                        str(msg.metadata.get("image") or "").strip(),
                    )

                    user_text_payload = user_text_content or content or " "
                    if image_urls and self._image_inputs_disabled_for_session(
                        session_key
                    ):
                        image_parts = [
                            {"type": "image_url", "image_url": {"url": image_url}}
                            for image_url in image_urls
                        ]
                        user_msg_content = self._render_text_only_message_content(
                            [{"type": "text", "text": user_text_payload}]
                            + image_parts
                        )
                    else:
                        user_msg_content = (
                            [{"type": "text", "text": user_text_payload}]
                            + [
                                {"type": "image_url", "image_url": {"url": image_url}}
                                for image_url in image_urls
                            ]
                            if image_urls
                            else user_text_payload
                        )
                    if not task_run_resume:
                        self._history_append(
                            session_key,
                            {"role": "user", "content": user_msg_content},
                        )

                        asyncio.create_task(
                            self.session_manager.append_chat_log(
                                session_key,
                                {
                                    "role": "user",
                                    "content": msg.content or "",
                                    "message_id": str(msg.metadata.get("message_id") or "").strip() or None,
                                    "client_message_id": str(msg.metadata.get("client_message_id") or "").strip() or None,
                                    "edited_from_message_id": str(msg.metadata.get("edited_from_message_id") or "").strip() or None,
                                    "image": bool(image_urls),
                                    "images": len(image_urls),
                                    "attachments": [
                                        str(attachment.get("name") or "attachment")
                                        for attachment in attachments
                                    ],
                                },
                            )
                        )

                    injected = self._build_session_injected_files(
                        sender_id, attachments
                    )

                    asyncio.create_task(
                        self.session_manager.update_session(
                            session_key=session_key,
                            model=self.model,
                            origin=msg.channel,
                            injected_files=injected,
                        )
                    )

                    selected_subagent = self._selected_subagent_for_turn()
                    should_route_via_selected_subagent = (
                        selected_subagent != "auto"
                        and not msg.metadata.get("is_report")
                        and not msg.metadata.get("is_confirmation")
                        and not msg.metadata.get("is_scheduler")
                    )
                    if should_route_via_selected_subagent:
                        routed_task = user_text_content or content or " "
                        self._log_session_event(
                            session_key,
                            {
                                "type": "subagent_mode_route",
                                "turn_id": turn_id,
                                "subagent": selected_subagent,
                                "task_preview": routed_task[:500],
                            },
                        )
                        await self._publish_activity(
                            msg,
                            f"Routing through {selected_subagent} mode...",
                            turn_id=turn_id,
                            message_id=assistant_message_id,
                        )
                        await self._trim_history(session_key)
                        asyncio.create_task(self._flush_history(session_key))
                        raw_reply = await self.run_subagent(
                            session_key,
                            f"{session_key}_sub_{uuid.uuid4().hex[:6]}",
                            routed_task,
                            agent_name=selected_subagent,
                        )
                        tag_result = await process_tags(
                            raw_reply=raw_reply,
                            sender_id=sender_id,
                            validate_soul=prompt_module.validate_and_save_soul,
                            validate_identity=prompt_module.validate_and_save_identity,
                            validate_mood=prompt_module.validate_and_save_mood,
                            validate_relationship=prompt_module.validate_and_save_relationships,
                            vector_service=self._get_current_vector_service(),
                            bus=self.bus,
                            msg=msg,
                            config=self.config,
                        )
                        reply_to_user = tag_result.clean_reply or raw_reply
                        self._history_append(
                            session_key,
                            {"role": "assistant", "content": raw_reply},
                        )
                        asyncio.create_task(
                            self.session_manager.append_chat_log(
                                session_key, {"role": "assistant", "content": raw_reply}
                            )
                        )
                        await self.bus.publish_outbound(
                            OutboundMessage(
                                channel=msg.channel,
                                chat_id=msg.chat_id,
                                content="",
                                metadata=self._with_trace_metadata(
                                    {"type": "stop_typing"},
                                    turn_id=turn_id,
                                    message_id=assistant_message_id,
                                ),
                            )
                        )
                        await self.bus.publish_outbound(
                            OutboundMessage(
                                channel=msg.channel,
                                chat_id=msg.chat_id,
                                content=reply_to_user,
                                metadata=self._with_trace_metadata(
                                    {
                                        "reply_to": msg.sender_id,
                                        "event_kind": "assistant_message",
                                        "terminal": True,
                                        "turn_status": "completed",
                                    },
                                    turn_id=turn_id,
                                    message_id=assistant_message_id,
                                ),
                            )
                        )
                        if task_run is not None:
                            self.task_runs.complete(
                                task_run.run_id,
                                result=reply_to_user,
                                checkpoint={
                                    **dict(task_run.checkpoint or {}),
                                    "delegated_subagent": True,
                                },
                            )
                        return

                    await self._trim_history(session_key)
                    asyncio.create_task(self._flush_history(session_key))

                    plan_mode = self._is_read_only_plan_request(content, msg)
                    include_tools = bool(
                        (
                            forced_skill_name
                            or self._should_include_tools_for_turn(
                                content, session_key=session_key, msg=msg
                            )
                        )
                        and not plan_mode
                    )
                    self._log_tool_debug(
                        "tool_gate_decision",
                        session_key=session_key,
                        include_tools=include_tools,
                        content=content,
                    )
                    initial_tool_definitions: Optional[List[Dict[str, Any]]] = None
                    if include_tools:
                        tool_schema_started = time.perf_counter()
                        initial_tool_definitions = self._get_tool_definitions_for_turn(
                            content,
                            forced_skill_name=forced_skill_name,
                            session_key=session_key,
                            attachments=attachments,
                        )
                        initial_tool_definitions = self._recovery_tool_definitions(
                            initial_tool_definitions, recovery_state
                        )
                        self._record_stage_timing(
                            session_key,
                            "tool_schema",
                            tool_schema_started,
                            metadata={
                                "tool_count": len(initial_tool_definitions),
                                "mode": getattr(
                                    getattr(self.config, "ai_harness", None),
                                    "mode",
                                    "balanced",
                                ),
                            },
                        )
                    initial_tool_choice = "auto"
                    llm_first_call_started = time.perf_counter()
                    plan_instruction = (
                        "This is read-only coding Plan mode. Do not call or propose tool "
                        "execution. Return a concise plan with intended files, rationale, "
                        "risks, acceptance checks, and exact verification commands. State "
                        "what still needs inspection instead of claiming an edit was made."
                    )
                    initial_messages = self.history[session_key]
                    if task_run is not None:
                        if task_run_resume:
                            task_run_instruction = self._task_run_resume_instruction(task_run)
                            if msg.metadata.get("task_run_followup"):
                                task_run_instruction += (
                                    "\nUser follow-up correction to apply now: "
                                    + str(msg.content or "")[:2000]
                                )
                        else:
                            criteria = "\n".join(
                                f"- {item}" for item in task_run.acceptance_criteria
                            )
                            task_run_instruction = (
                                "TASK COMPLETION CONTRACT. Work toward the original request and "
                                "do not claim completion from a plan or intention alone. Verify "
                                "state-changing work before reporting success. Acceptance criteria:\n"
                                f"{criteria}"
                            )
                        initial_messages = initial_messages + [
                            {"role": "system", "content": task_run_instruction}
                        ]
                    recovery_contract = self._recovery_tool_contract(recovery_state)
                    if recovery_contract:
                        initial_messages = initial_messages + [
                            {"role": "system", "content": recovery_contract}
                        ]
                    if plan_mode:
                        initial_messages = initial_messages + [
                            {"role": "system", "content": plan_instruction}
                        ]
                    stream = await self._llm_call_with_retry(
                        messages=initial_messages,
                        session_key=session_key,
                        msg=msg,
                        stream=True,
                        include_tools=include_tools,
                        tool_context_text=content,
                        turn_id=turn_id,
                        message_id=assistant_message_id,
                        tool_definitions_override=initial_tool_definitions,
                        tool_choice=initial_tool_choice,
                    )
                    self._record_stage_timing(
                        session_key,
                        "llm_first_call",
                        llm_first_call_started,
                        metadata={
                            "include_tools": include_tools,
                            "tool_count": len(initial_tool_definitions or []),
                            "plan_mode": plan_mode,
                        },
                    )
                    stream_consume_started = time.perf_counter()
                    active_output_queued = make_output_queued_recorder("initial", 0)
                    consume_result = await self._consume_stream(
                        stream,
                        msg,
                        session_key,
                        turn_id=turn_id,
                        message_id=assistant_message_id,
                        turn_started_at=turn_started,
                        llm_started_at=llm_first_call_started,
                        iteration_kind="initial",
                        iteration=0,
                        on_output_queued=active_output_queued,
                    )
                    (
                        full_content,
                        tool_calls,
                        _,
                        streamed_to_web,
                        streamed_to_discord,
                        reasoning_content,
                    ) = (
                        self._unpack_stream_result_with_reasoning(consume_result)
                    )
                    self._record_stage_timing(
                        session_key,
                        "stream_consume",
                        stream_consume_started,
                        metadata={"tool_call_count": len(tool_calls or [])},
                    )
                    accumulated_content = full_content
                    iterations_limit_reached = False
                    web_streamed_reply = bool(streamed_to_web)
                    discord_streamed_reply = bool(streamed_to_discord)
                    force_direct_reply = False
                    any_tool_calls_in_turn = bool(tool_calls)
                    fallback_inserted = False

                    if full_content or tool_calls or reasoning_content:
                        am: Dict = {"role": "assistant", "content": full_content or ""}
                        if tool_calls:
                            am["tool_calls"] = tool_calls
                        if reasoning_content:
                            am["reasoning_content"] = reasoning_content
                        self._history_append(session_key, am)

                    raw_reply = accumulated_content or ""
                    coding_turn = self._is_coding_turn(content, tool_calls)
                    repair_attempts = 0
                    repair_fingerprints: Set[str] = set()
                    recovery_blocked: Optional[str] = None
                    recovery_required = False
                    unresolved_tool_failure = False
                    unresolved_failure_detail = ""

                    if tool_calls:
                        if coding_turn:
                            await self._emit_coding_phase(
                                "inspect", session_key, msg, turn_id=turn_id,
                                message_id=assistant_message_id,
                            )
                            active_output_queued("tool_progress")
                            await self._emit_coding_phase(
                                "plan", session_key, msg, turn_id=turn_id,
                                message_id=assistant_message_id,
                            )
                            await self._stage_workspace_changeset(
                                tool_calls,
                                session_key,
                                msg,
                                turn_id=turn_id,
                                message_id=assistant_message_id,
                            )
                        await self._publish_activity(
                            msg,
                            "Planning tool calls...",
                            turn_id=turn_id,
                            message_id=assistant_message_id,
                        )
                        active_output_queued("tool_progress")
                        await self._publish_tool_intents(
                            tool_calls,
                            session_key,
                            msg,
                            turn_id=turn_id,
                            message_id=assistant_message_id,
                        )
                        if msg.channel == "web":
                            await self.bus.publish_outbound(
                                OutboundMessage(
                                    channel=msg.channel,
                                    chat_id=msg.chat_id,
                                    content="",
                                    metadata=self._with_trace_metadata(
                                        {"type": "stop_typing"},
                                        turn_id=turn_id,
                                        message_id=assistant_message_id,
                                    ),
                                )
                            )

                        await self._publish_activity(
                            msg,
                            "Executing tools...",
                            turn_id=turn_id,
                            message_id=assistant_message_id,
                        )
                        tool_batch_started = time.perf_counter()
                        is_blocked = await self._execute_tool_batch(
                            tool_calls,
                            session_key,
                            msg,
                            turn_id=turn_id,
                            message_id=assistant_message_id,
                            coding_turn=coding_turn,
                            on_progress_queued=active_output_queued,
                            recovery_state=recovery_state,
                        )
                        tool_batch_duration_s += time.perf_counter() - tool_batch_started
                        tool_batch_count += 1
                        tool_batch_blocked = bool(is_blocked)

                        batch_failures = [
                            outcome
                            for outcome in self._get_tool_outcomes(turn_id)
                            if not outcome.success
                        ]
                        all_tool_outcomes.extend(
                            self._get_tool_outcomes(turn_id)
                        )
                        if task_run is not None and recovery_state is not None:
                            task_run = self.task_runs.checkpoint(
                                task_run.run_id,
                                phase=(
                                    TASK_RUN_REPAIRING
                                    if batch_failures
                                    else TASK_RUN_EXECUTING
                                ),
                                status="running",
                                corrective_failures=recovery_state.corrective_failures,
                                checkpoint={
                                    **dict(task_run.checkpoint or {}),
                                    "recovery": recovery_state.to_dict(),
                                    "last_tools": [
                                        {
                                            "tool": item.tool,
                                            "success": item.success,
                                            "failure_category": item.failure_category,
                                            "verification_status": item.verification_status,
                                        }
                                        for item in self._get_tool_outcomes(turn_id)
                                    ],
                                },
                            ) or task_run
                        unresolved_tool_failure = bool(batch_failures)
                        if batch_failures:
                            unresolved_failure_detail = batch_failures[-1].diagnostic_tail
                        recovery_result = await self._coordinate_tool_recovery(
                            session_key,
                            msg,
                            repair_fingerprints,
                            repair_attempts,
                            coding_turn=coding_turn,
                            recovery_state=recovery_state,
                            turn_id=turn_id,
                            message_id=assistant_message_id,
                        )
                        repair_attempts = recovery_result.attempts
                        recovery_blocked = recovery_result.blocked
                        recovery_required = recovery_result.queued

                        if recovery_blocked:
                            raw_reply = recovery_blocked
                            force_direct_reply = True
                        if not recovery_blocked:
                            max_iterations = getattr(self.config, "max_iterations", 30)
                            iteration = 0
                            artifact_requested = self._artifact_delivery_requested(content)
                            artifact_reserve_steps = max(
                                4, min(8, max(1, int(max_iterations)) // 4)
                            )
                            research_actions_used = sum(
                                1
                                for call in tool_calls
                                if str(call.get("function", {}).get("name") or "")
                                in _RESEARCH_TOOL_NAMES
                            )
                            research_action_limit = max(
                                8, min(18, max(1, int(max_iterations)) - 8)
                            )

                            while iteration < max_iterations:
                                iteration += 1

                                if iteration >= max_iterations:
                                    iterations_limit_reached = True
                                    logger.warning(
                                        f"⚠ Max iterations ({max_iterations}) reached."
                                    )
                                    self.metrics.record_anomaly(
                                        session_key,
                                        "tool_iteration_limit_reached",
                                        detail=f"limit={max_iterations}",
                                    )
                                    synthesis_instruction = (
                                        "Tool step cap reached. Produce a concise tools-disabled summary "
                                        "of completed actions, changed paths if known, the last diagnostic, "
                                        "remaining verification, and the safest next action. Never claim success "
                                        "without a passing verification."
                                    )
                                    try:
                                        synthesis_llm_started = time.perf_counter()
                                        active_output_queued = make_output_queued_recorder(
                                            "synthesis", iteration
                                        )
                                        normalized_history = self._normalize_history_in_place(
                                            session_key
                                        )
                                        synthesis_stream = await self._llm_call_with_retry(
                                            messages=normalized_history
                                            + [{"role": "system", "content": synthesis_instruction}],
                                            session_key=session_key,
                                            msg=msg,
                                            stream=True,
                                            include_tools=False,
                                            tool_context_text=content,
                                            turn_id=turn_id,
                                            message_id=assistant_message_id,
                                        )
                                        synthesis_result = await self._consume_stream(
                                            synthesis_stream,
                                            msg,
                                            session_key,
                                            turn_id=turn_id,
                                            message_id=assistant_message_id,
                                            turn_started_at=turn_started,
                                            llm_started_at=synthesis_llm_started,
                                            iteration_kind="synthesis",
                                            iteration=iteration,
                                            on_output_queued=active_output_queued,
                                        )
                                        synthesis_content, _, _, _, _, _ = self._unpack_stream_result_with_reasoning(
                                            synthesis_result
                                        )
                                        raw_reply = str(synthesis_content or "").strip()
                                    except Exception as synthesis_error:
                                        logger.warning(
                                            f"Tool-cap synthesis failed: {synthesis_error}"
                                        )
                                        raw_reply = ""
                                    if not raw_reply:
                                        raw_reply = self._build_step_cap_fallback(session_key)
                                    self._history_append(
                                        session_key,
                                        {
                                            "role": "assistant",
                                            "content": raw_reply,
                                        },
                                    )
                                    break

                                force_tool_image_reply = (
                                    session_key in self._sessions_pending_tool_image_reply
                                )
                                post_tool_llm_started = time.perf_counter()
                                active_output_queued = make_output_queued_recorder(
                                    "post_tool", iteration
                                )
                                normalized_history = self._normalize_history_in_place(
                                    session_key
                                )
                                post_tool_messages = normalized_history
                                post_tool_definitions = self._recovery_tool_definitions(
                                    initial_tool_definitions, recovery_state
                                )
                                recovery_contract = self._recovery_tool_contract(recovery_state)
                                if recovery_contract:
                                    post_tool_messages = post_tool_messages + [
                                        {"role": "system", "content": recovery_contract}
                                    ]
                                if (
                                    artifact_requested
                                    and (
                                        iteration
                                        >= max(1, max_iterations - artifact_reserve_steps)
                                        or research_actions_used >= research_action_limit
                                    )
                                ):
                                    post_tool_messages = normalized_history + [
                                        {
                                            "role": "system",
                                            "content": (
                                                "Artifact completion reserve is active. Stop all open-ended "
                                                "searching and browsing now. Use the evidence already gathered "
                                                "to create, verify, and deliver the requested file. If some facts "
                                                "remain uncertain, put explicit caveats and source URLs in the "
                                                "artifact instead of abandoning delivery. Use the literal value "
                                                "'Unverified' for every unsupported field and do not contradict "
                                                "that status with a numeric claim elsewhere in the same row."
                                            ),
                                        }
                                    ]
                                    post_tool_definitions = (
                                        self._artifact_reserve_tool_definitions(
                                            initial_tool_definitions
                                        )
                                    )
                                nxt_stream = await self._llm_call_with_retry(
                                    messages=post_tool_messages,
                                    session_key=session_key,
                                    msg=msg,
                                    stream=True,
                                    include_tools=not force_tool_image_reply,
                                    tool_context_text=content,
                                    tool_definitions_override=post_tool_definitions,
                                    tool_choice=(
                                        "required"
                                        if recovery_required and not force_tool_image_reply
                                        else "auto"
                                    ),
                                    turn_id=turn_id,
                                    message_id=assistant_message_id,
                                )
                                if force_tool_image_reply:
                                    self._sessions_pending_tool_image_reply.discard(
                                        session_key
                                    )
                                nxt_consume_result = await self._consume_stream(
                                    nxt_stream,
                                    msg,
                                    session_key,
                                    accumulated_content,
                                    turn_id=turn_id,
                                    message_id=assistant_message_id,
                                    turn_started_at=turn_started,
                                    llm_started_at=post_tool_llm_started,
                                    iteration_kind="post_tool",
                                    iteration=iteration,
                                    on_output_queued=active_output_queued,
                                )
                                (
                                    nxt_content,
                                    nxt_tool_calls,
                                    _,
                                    nxt_streamed_to_web,
                                    nxt_streamed_to_discord,
                                    nxt_reasoning_content,
                                ) = (
                                    self._unpack_stream_result_with_reasoning(nxt_consume_result)
                                )
                                web_streamed_reply = web_streamed_reply or bool(
                                    nxt_streamed_to_web
                                )
                                discord_streamed_reply = (
                                    discord_streamed_reply
                                    or bool(nxt_streamed_to_discord)
                                )

                                clean_next = self._dedup_overlap(
                                    accumulated_content, nxt_content
                                )

                                if clean_next:
                                    sep = ""
                                    if (
                                        accumulated_content
                                        and not accumulated_content.endswith(
                                            ("\n", " ")
                                        )
                                    ):
                                        if not clean_next.startswith(("\n", " ")):
                                            if (
                                                clean_next
                                                and clean_next[0] not in ".,!?;:"
                                            ):
                                                sep = " "
                                    accumulated_content += sep + clean_next

                                if not nxt_tool_calls:
                                    raw_reply = accumulated_content
                                    next_assistant_message: Dict[str, Any] = {
                                        "role": "assistant",
                                        "content": nxt_content,
                                    }
                                    if nxt_reasoning_content:
                                        next_assistant_message[
                                            "reasoning_content"
                                        ] = nxt_reasoning_content
                                    self._history_append(
                                        session_key, next_assistant_message
                                    )
                                    break

                                nxt_am: Dict = {
                                    "role": "assistant",
                                    "content": nxt_content or "",
                                }
                                nxt_am["tool_calls"] = nxt_tool_calls
                                if nxt_reasoning_content:
                                    nxt_am["reasoning_content"] = nxt_reasoning_content
                                self._history_append(session_key, nxt_am)

                                research_actions_used += sum(
                                    1
                                    for call in nxt_tool_calls
                                    if str(call.get("function", {}).get("name") or "")
                                    in _RESEARCH_TOOL_NAMES
                                )

                                tool_batch_started = time.perf_counter()
                                nxt_blocked = await self._execute_tool_batch(
                                    nxt_tool_calls,
                                    session_key,
                                    msg,
                                    turn_id=turn_id,
                                    message_id=assistant_message_id,
                                    coding_turn=coding_turn,
                                    on_progress_queued=active_output_queued,
                                    recovery_state=recovery_state,
                                )
                                tool_batch_duration_s += (
                                    time.perf_counter() - tool_batch_started
                                )
                                tool_batch_count += 1
                                tool_batch_blocked = bool(nxt_blocked)

                                batch_failures = [
                                    outcome
                                    for outcome in self._get_tool_outcomes(turn_id)
                                    if not outcome.success
                                ]
                                all_tool_outcomes.extend(
                                    self._get_tool_outcomes(turn_id)
                                )
                                if task_run is not None and recovery_state is not None:
                                    task_run = self.task_runs.checkpoint(
                                        task_run.run_id,
                                        phase=(
                                            TASK_RUN_REPAIRING
                                            if batch_failures
                                            else TASK_RUN_EXECUTING
                                        ),
                                        status="running",
                                        corrective_failures=recovery_state.corrective_failures,
                                        checkpoint={
                                            **dict(task_run.checkpoint or {}),
                                            "recovery": recovery_state.to_dict(),
                                            "last_tools": [
                                                {
                                                    "tool": item.tool,
                                                    "success": item.success,
                                                    "failure_category": item.failure_category,
                                                    "verification_status": item.verification_status,
                                                }
                                                for item in self._get_tool_outcomes(turn_id)
                                            ],
                                        },
                                    ) or task_run
                                unresolved_tool_failure = bool(batch_failures)
                                if batch_failures:
                                    unresolved_failure_detail = batch_failures[-1].diagnostic_tail
                                else:
                                    unresolved_failure_detail = ""
                                recovery_result = await self._coordinate_tool_recovery(
                                    session_key,
                                    msg,
                                    repair_fingerprints,
                                    repair_attempts,
                                    coding_turn=coding_turn,
                                    recovery_state=recovery_state,
                                    turn_id=turn_id,
                                    message_id=assistant_message_id,
                                )
                                repair_attempts = recovery_result.attempts
                                recovery_blocked = recovery_result.blocked
                                recovery_required = recovery_result.queued
                                if recovery_blocked:
                                    raw_reply = recovery_blocked
                                    force_direct_reply = True
                                    break

                                if iteration % _INTERIM_SAVE_EVERY == 0:
                                    await self._flush_history(session_key)

                                if nxt_blocked:
                                    logger.info(
                                        "Tool policy blocked a step; recovery remains active."
                                    )
                            if tool_batch_count:
                                try:
                                    self.metrics.record_stage_timing(
                                        session_key,
                                        "tool_batch",
                                        tool_batch_duration_s,
                                        metadata={
                                            "batch_count": tool_batch_count,
                                            "blocked": tool_batch_blocked,
                                        },
                                    )
                                except Exception:
                                    pass
                        else:
                            logger.info(
                                "Tool recovery stopped after the configured budget."
                            )
                    else:
                        raw_reply = full_content

                    completion = await self._task_run_completion_service().finalize(
                        TaskRunCompletionRequest(
                            task_run=task_run,
                            content=content,
                            coding_turn=coding_turn,
                            plan_mode=plan_mode,
                            outcomes=all_tool_outcomes,
                            unresolved_tool_failure=unresolved_tool_failure,
                            unresolved_failure_detail=unresolved_failure_detail,
                            iterations_limit_reached=iterations_limit_reached,
                            history=self.history.get(session_key, []),
                            session_key=session_key,
                            message=msg,
                            raw_reply=str(raw_reply or ""),
                            recovery_state=recovery_state,
                            pending_workspaces=self._pending_isolated_workspaces(),
                        )
                    )
                    task_run = completion.task_run
                    operator_verdict = completion.operator_verdict
                    task_run_continuation_scheduled = (
                        completion.continuation_scheduled
                    )
                    task_run_progress_reply = completion.progress_reply
                    if completion.turn_status_override:
                        turn_status_override = (
                            turn_status_override or completion.turn_status_override
                        )
                    if (
                        operator_verdict is not None
                        and operator_verdict.applies
                        and not operator_verdict.ok
                    ):
                        force_direct_reply = True
                        if not str(raw_reply or "").strip():
                            raw_reply = format_invariant_next_action(operator_verdict)

                    if plan_mode and raw_reply:
                        await self._persist_coding_plan(
                            raw_reply,
                            msg,
                            turn_id=turn_id,
                            message_id=assistant_message_id,
                        )

                    if (
                        tool_calls
                        and not str(raw_reply or "").strip()
                        and not self._turn_already_delivered_media(turn_id)
                    ):
                        self._log_tool_debug(
                            "tool_fallback_inserted",
                            session_key=session_key,
                            stage="post_initial_tool_batch",
                        )
                        raw_reply = self._build_tool_fallback_reply(session_key)
                        force_direct_reply = True
                        fallback_inserted = True
                        self._history_append(
                            session_key,
                            {"role": "assistant", "content": raw_reply},
                        )

                    if coding_turn and raw_reply and not recovery_blocked:
                        coding_phase = (
                            "blocked"
                            if (
                                (operator_verdict is not None and operator_verdict.applies and not operator_verdict.ok)
                                or (task_run is not None and getattr(task_run, "status", "") == TASK_RUN_BLOCKED)
                            )
                            else "complete"
                            if task_run is None or getattr(task_run, "status", "") == TASK_RUN_COMPLETED
                            else "verify"
                        )
                        await self._emit_coding_phase(
                            coding_phase,
                            session_key,
                            msg,
                            turn_id=turn_id,
                            message_id=assistant_message_id,
                        )

                    tag_result = await process_tags(
                        raw_reply=raw_reply,
                        sender_id=sender_id,
                        validate_soul=prompt_module.validate_and_save_soul,
                        validate_identity=prompt_module.validate_and_save_identity,
                        validate_mood=prompt_module.validate_and_save_mood,
                        validate_relationship=prompt_module.validate_and_save_relationships,
                        vector_service=self._get_current_vector_service(),
                        bus=self.bus,
                        msg=msg,
                        config=self.config,
                    )
                    reply_to_user = tag_result.clean_reply
                    soul_updated = tag_result.soul_updated
                    identity_updated = tag_result.identity_updated

                    if task_run_continuation_scheduled and task_run_progress_reply:
                        reply_to_user = task_run_progress_reply
                        raw_reply = task_run_progress_reply
                        force_direct_reply = True

                    if (
                        any_tool_calls_in_turn
                        and not str(reply_to_user or "").strip()
                        and not self._turn_already_delivered_media(turn_id)
                    ):
                        self._log_tool_debug(
                            "tool_fallback_inserted",
                            session_key=session_key,
                            stage="post_tag_processing",
                        )
                        fallback_reply = self._build_tool_fallback_reply(session_key)
                        reply_to_user = fallback_reply
                        raw_reply = fallback_reply
                        force_direct_reply = True
                        if not fallback_inserted:
                            self._history_append(
                                session_key,
                                {"role": "assistant", "content": fallback_reply},
                            )
                            fallback_inserted = True
                    elif (
                        msg
                        and not str(reply_to_user or "").strip()
                        and not self._turn_already_delivered_media(turn_id)
                    ):
                        logger.warning(
                            "Empty user-visible reply after tag processing; sending fallback."
                        )
                        fallback_reply = self._build_empty_reply_fallback()
                        reply_to_user = fallback_reply
                        raw_reply = fallback_reply
                        force_direct_reply = True

                    # ── Self-repetition dedup ────────────────────────────
                    reply_to_user = self._dedupe_repeated_reply_sections(reply_to_user)
                    reply_to_user = self._ensure_reply_includes_operator_failures(
                        reply_to_user,
                        session_key,
                        verdict=operator_verdict,
                        task_run=task_run,
                        extra_texts=[unresolved_failure_detail]
                        if unresolved_failure_detail
                        else None,
                    )
                    raw_reply = raw_reply or reply_to_user
                    if reply_to_user and not str(raw_reply or "").strip():
                        raw_reply = reply_to_user
                    if any_tool_calls_in_turn and self._collect_turn_tool_errors(
                        session_key
                    ):
                        force_direct_reply = True

                    if soul_updated or identity_updated:
                        self._invalidate_stable_prompt(sender_id)
                        logger.info("🔄 Stable prompt cache invalidated.")

                    if not tool_calls and not (full_content or tool_calls):
                        self._history_append(
                            session_key,
                            {"role": "assistant", "content": raw_reply},
                        )

                    asyncio.create_task(
                        self.session_manager.append_chat_log(
                            session_key, {"role": "assistant", "content": raw_reply}
                        )
                    )

                    if reply_to_user:
                        turn_delivery_status = self._derive_turn_status(
                            task_run,
                            continuation_scheduled=task_run_continuation_scheduled,
                            unresolved_tool_failure=unresolved_tool_failure,
                            override=turn_status_override,
                        )
                        meta: Dict = {
                            "reply_to": msg.sender_id,
                            "event_kind": "assistant_message",
                            "terminal": True,
                            "turn_status": turn_delivery_status,
                        }
                        if identity_updated:
                            meta["identity_updated"] = True
                        if iterations_limit_reached:
                            meta["is_warning"] = True
                        meta = self._with_trace_metadata(
                            meta,
                            turn_id=turn_id,
                            message_id=assistant_message_id,
                        )
                        if task_run_continuation_scheduled:
                            meta["task_status"] = "retrying"
                            meta["task_run_id"] = task_run_id
                            meta["is_warning"] = True
                        elif unresolved_tool_failure:
                            # The assistant may still produce a useful textual
                            # explanation after a tool fails. Keep the durable
                            # task state separate from message presentation so
                            # that this text is not rendered as a transport
                            # error in the chat UI.
                            meta["task_status"] = "failed"
                            meta["is_warning"] = True

                        suppress_web_final_reply = (
                            self._should_suppress_web_final_reply(
                                channel=msg.channel,
                                any_tool_calls_in_turn=any_tool_calls_in_turn,
                                iterations_limit_reached=iterations_limit_reached,
                                web_streamed_reply=web_streamed_reply,
                                force_direct_reply=force_direct_reply,
                                reply_to_user=reply_to_user,
                                raw_reply=raw_reply,
                            )
                        )
                        if suppress_web_final_reply:
                            outbound = OutboundMessage(
                                channel=msg.channel,
                                chat_id=msg.chat_id,
                                content="",
                                metadata=self._with_trace_metadata(
                                    {"type": "stop_typing"},
                                    turn_id=turn_id,
                                    message_id=assistant_message_id,
                                ),
                            )
                        else:
                            from core.tts import ElevenLabsTTS

                            voice_cfg = ElevenLabsTTS.get_voice_config()
                            voice_on = bool(
                                voice_cfg.get("enabled", False)
                                and ElevenLabsTTS.get_api_key()
                            )
                            delivery_mode = self._resolve_voice_delivery(
                                msg.channel, voice_cfg, voice_on, bool(reply_to_user)
                            )
                            outbound = None

                            if delivery_mode in ("audio_only", "audio_and_text"):
                                # Chat channels: send the audio file itself (voice note).
                                audio_path = await self._synthesize_voice_file(
                                    reply_to_user
                                )
                                if audio_path:
                                    file_meta = self._with_trace_metadata(
                                        {
                                            "type": "file",
                                            "file_path": audio_path,
                                            "caption": "",
                                            "cleanup_file": True,
                                            "fallback_text": reply_to_user,
                                        },
                                        turn_id=turn_id,
                                        message_id=assistant_message_id,
                                    )
                                    await self.bus.publish_outbound(
                                        OutboundMessage(
                                            channel=msg.channel,
                                            chat_id=msg.chat_id,
                                            content="",
                                            metadata=file_meta,
                                        )
                                    )
                                    active_output_queued("audio")
                                    # Optionally also send the text as a separate message.
                                    if delivery_mode == "audio_and_text":
                                        outbound = OutboundMessage(
                                            channel=msg.channel,
                                            chat_id=msg.chat_id,
                                            content=reply_to_user,
                                            metadata=meta,
                                        )
                                    # else: audio only — nothing more to send.
                                else:
                                    # Synthesis failed → fall back to text so the user still gets a reply.
                                    outbound = OutboundMessage(
                                        channel=msg.channel,
                                        chat_id=msg.chat_id,
                                        content=reply_to_user,
                                        metadata=meta,
                                    )
                            else:
                                # Web auto-TTS attaches a playable URL alongside the text.
                                if delivery_mode == "web_url":
                                    try:
                                        audio_url = await ElevenLabsTTS.synthesize_and_save(
                                            reply_to_user
                                        )
                                        if audio_url:
                                            meta["voice_url"] = audio_url
                                    except Exception as tts_err:
                                        logger.error(
                                            f"[TTS] Auto synthesis failed: {tts_err}"
                                        )
                                outbound = OutboundMessage(
                                    channel=msg.channel,
                                    chat_id=msg.chat_id,
                                    content=reply_to_user,
                                    metadata=meta,
                                )
                        if outbound is not None:
                            await self.bus.publish_outbound(outbound)
                            if outbound.content:
                                active_output_queued("content")
                            self._log_session_event(
                                session_key,
                                {
                                    "type": "outbound_message",
                                    "turn_id": turn_id,
                                    "channel": outbound.channel,
                                    "chat_id": outbound.chat_id,
                                    "content_preview": (outbound.content or "")[:500],
                                    "metadata_type": outbound.metadata.get("type"),
                                    "event_id": outbound.metadata.get("event_id"),
                                    "task_id": outbound.metadata.get("task_id"),
                                },
                            )

                except asyncio.CancelledError:
                    turn_status_override = "cancelled"
                    logger.warning(f"⚠ Task cancelled for {session_key}")
                    self._log_session_event(
                        session_key,
                        {"type": "turn_cancelled", "turn_id": turn_id},
                    )
                    import contextlib

                    with contextlib.suppress(Exception):
                        await self._publish_both(
                            msg,
                            "",
                            self._with_trace_metadata(
                                {
                                    "type": "cancellation",
                                    "is_cancellation": True,
                                    "terminal": True,
                                    "turn_status": "cancelled",
                                },
                                turn_id=turn_id,
                                message_id=assistant_message_id,
                            ),
                        )
                    raise
                except Exception as e:
                    turn_status_override = "failed"
                    import traceback

                    traceback.print_exc()
                    logger.exception(f"❌ Error processing message: {e}")
                    await _tracker.complete_task(_msg_task_id, error=str(e)) if _msg_task_id else None
                    self._log_session_event(
                        session_key,
                        {
                            "type": "turn_error",
                            "turn_id": turn_id,
                            "error": str(e)[:500],
                        },
                    )
                    error_text = str(e)
                    lower_error = error_text.lower()
                    if (
                        "rate limit" in lower_error
                        or "rate_limit" in lower_error
                        or "usage_limit" in lower_error
                        or "usage limit" in lower_error
                        or "limit has been reached" in lower_error
                        or "limit_reached" in lower_error
                        or "codex provider returned an error" in lower_error
                        or "no visible response" in lower_error
                        or "429" in lower_error
                    ):
                        visible_error = (
                            "⚠ Rate limit or usage limit reached for the active AI model.\n"
                            f"`{error_text}`\n\n"
                            "Your ChatGPT Plus/Pro plan or API quota limit has been hit. Please wait for the limit to reset, "
                            "check your provider's account limits, or configure a fallback API model."
                        )
                    else:
                        visible_error = f"🚫 **Internal error.**\n`{e}`"
                    turn_error_code = (
                        "provider_rate_limit"
                        if "429" in lower_error
                        or "rate limit" in lower_error
                        or "rate_limit" in lower_error
                        else "turn_processing_failed"
                    )
                    await self.bus.publish_outbound(
                        OutboundMessage(
                            channel=msg.channel,
                            chat_id=msg.chat_id,
                            content=visible_error,
                            metadata=self._with_trace_metadata(
                                {
                                    "is_error": True,
                                    "event_kind": "transport_error",
                                    "terminal": True,
                                    "turn_status": "failed",
                                    "error_code": turn_error_code,
                                    "reply_to": msg.sender_id,
                                },
                                turn_id=turn_id,
                                message_id=assistant_message_id,
                            ),
                        )
                    )

        finally:
            self._record_stage_timing(
                session_key,
                "turn_total",
                turn_started,
                metadata={"channel": getattr(msg, "channel", "unknown")},
            )
            if _msg_task_id:
                task = await _tracker.get_task(_msg_task_id)
                if task and task.status == "running":
                    current = asyncio.current_task()
                    is_cancelled = bool(
                        current
                        and getattr(current, "cancelling", lambda: 0)()
                    )
                    if task_run_continuation_scheduled:
                        await _tracker.update_task(
                            _msg_task_id,
                            status="retrying",
                            metadata_update={
                                "task_run_id": task_run_id,
                                "task_run_status": "retrying",
                                "task_run_phase": task_run.phase if task_run else "retrying",
                                "next_action": task_run.next_action if task_run else "",
                            },
                        )
                    elif is_cancelled:
                        await _tracker.update_task(
                            _msg_task_id,
                            status="cancelled",
                            error="Inbound turn cancelled.",
                        )
                    elif unresolved_tool_failure:
                        durable_id = (
                            msg.metadata.get("durable_job_id")
                            if isinstance(getattr(msg, "metadata", None), dict)
                            else None
                        )
                        queue = getattr(self, "job_queue", None)
                        existing = (
                            queue.get(str(durable_id))
                            if durable_id and queue is not None
                            else None
                        )
                        if existing is not None and existing.side_effects:
                            # Writes already landed; a stale/broken follow-up
                            # edit must not mark the durable job failed.
                            await _tracker.complete_task(_msg_task_id)
                        else:
                            await _tracker.update_task(
                                _msg_task_id,
                                status="failed",
                                error=(
                                    "Tool recovery exhausted or ended with an unresolved "
                                    "failure: "
                                    + redact_sensitive_text(unresolved_failure_detail)[:500]
                                ),
                            )
                    elif task_run is not None and task_run.status == TASK_RUN_BLOCKED:
                        await _tracker.update_task(
                            _msg_task_id,
                            status="failed",
                            error=task_run.last_error or "Task is blocked.",
                            metadata_update={
                                "task_run_id": task_run.run_id,
                                "task_run_status": task_run.status,
                                "next_action": task_run.next_action,
                            },
                        )
                    else:
                        await _tracker.complete_task(_msg_task_id)
            current = asyncio.current_task()
            if current and getattr(current, "cancelling", lambda: 0)():
                turn_status_override = "cancelled"
            terminal_status = self._derive_turn_status(
                task_run,
                continuation_scheduled=task_run_continuation_scheduled,
                unresolved_tool_failure=unresolved_tool_failure,
                override=turn_status_override,
            )
            try:
                await self._publish_turn_terminal(
                    msg,
                    turn_state,
                    terminal_status,
                    error_code=turn_error_code,
                )
            except asyncio.CancelledError:
                # Preserve the original cancellation while still allowing the
                # task to unwind cleanly.
                pass
            except Exception:
                logger.debug("Terminal turn event delivery failed", exc_info=True)
            self._evict_history_image_inputs(session_key)
            await self._flush_history(session_key, force=True)
            current_task = asyncio.current_task()
            if self.active_tasks.get(session_key) is current_task:
                self.active_tasks.pop(session_key, None)
            # Always notify frontend that processing is done
            try:
                if msg:
                    await self._publish_both(
                        msg,
                        "",
                        self._with_trace_metadata(
                            {
                                "type": "stop_typing",
                                "terminal": True,
                                "turn_status": terminal_status,
                            },
                            turn_id=turn_id,
                            message_id=assistant_message_id,
                        ),
                    )
            except Exception:
                pass
            self._last_tool_outcomes_by_turn.pop(turn_id, None)
            if task_context_token is not None:
                _CURRENT_TASK_ID.reset(task_context_token)
