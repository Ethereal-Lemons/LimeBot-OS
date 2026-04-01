import asyncio
import ast
import json
import os
import re
import time
import uuid
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Set, Tuple

from core.confirmation import (
    ConfirmationManager,
    SENSITIVE_TOOLS,
    APPROVE_WORDS,
    DENY_WORDS,
)
from core.rag_engine import RagEngine, AUTORAG_MIN_SCORE
from core.tool_dispatcher import (
    normalize_tool_alias,
    TOOL_RESULT_LIMITS,
    DEFAULT_TOOL_RESULT_LIMIT,
    BROWSER_CACHEABLE,
    TAG_COMPAT_TOOLS,
    TOOL_NAME_ALIASES,
)

from litellm import (
    acompletion,
    completion,
    RateLimitError,
    InternalServerError,
    APIConnectionError,
    ServiceUnavailableError,
    AuthenticationError,
)
from loguru import logger

from config import load_config
from core.browser import get_browser_manager
from core.bus import MessageBus
from core.cache import ToolCache
from core.context import tool_context
from core.events import InboundMessage, OutboundMessage
from core import prompt as prompt_module
from core.metrics import MetricsCollector
from core.session_manager import SessionManager
from core.skills import SkillRegistry
from core.subagents import SubagentRegistry, normalize_subagent_tool_name
from core.tag_parser import process_tags
from core.tool_defs import shortlist_tool_definitions
from core.tools import Toolbox
from core.vectors import get_vector_service


TOOL_BROADCAST_MAX_CHARS = 500


# Tool result limits and browser cacheability are now in core/tool_dispatcher.py
# and imported at the top of this file as TOOL_RESULT_LIMITS, DEFAULT_TOOL_RESULT_LIMIT,
# BROWSER_CACHEABLE, TAG_COMPAT_TOOLS.
# AUTORAG_MIN_SCORE is imported from core/rag_engine.py.
# SENSITIVE_TOOLS, APPROVE_WORDS, DENY_WORDS come from core/confirmation.py.

_INTERIM_SAVE_EVERY = 5

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

_GHOST_TAG_RE = re.compile(
    r"</?(?:save_user|save_soul|save_identity|save_memory"
    r"|log_memory|save_mood|save_relationship"
    r"|save_memory|discord_send|discord_embed)>",
    re.IGNORECASE,
)

_GHOST_TAG_NAMES = (
    "save_user",
    "save_soul",
    "save_memory",
    "log_memory",
    "save_mood",
    "save_relationship",
)

# Backward-compat aliases for code that still uses the underscore-prefixed names
_TOOL_RESULT_LIMITS = TOOL_RESULT_LIMITS
_DEFAULT_TOOL_RESULT_LIMIT = DEFAULT_TOOL_RESULT_LIMIT
_BROWSER_CACHEABLE = BROWSER_CACHEABLE
_TAG_COMPAT_TOOLS = TAG_COMPAT_TOOLS
_SENSITIVE_TOOLS = SENSITIVE_TOOLS
_APPROVE_WORDS = APPROVE_WORDS
_DENY_WORDS = DENY_WORDS

from core.paths import PERSONA_DIR, USERS_DIR, MEMORY_DIR, SOUL_FILE, IDENTITY_FILE


class AgentLoop:
    """Agent loop supporting interactive persona setup and user context."""

    def __init__(
        self,
        bus: MessageBus,
        model: str = "gpt-3.5-turbo",
        scheduler: Any = None,
        session_manager: Optional[SessionManager] = None,
    ):
        self.bus = bus
        self.model = model
        self.scheduler = scheduler
        self._running = False

        for d in (PERSONA_DIR, USERS_DIR, MEMORY_DIR):
            d.mkdir(exist_ok=True)

        self.history: Dict[str, List[Dict]] = {}
        self.session_locks: Dict[str, asyncio.Lock] = {}
        self.session_whitelists: Dict[str, Set[str]] = {}

        self.session_manager = session_manager or SessionManager()
        self.metrics = MetricsCollector()
        self.tool_cache = ToolCache()
        self.pending_confirmations: Dict[str, Dict[str, Any]] = {}
        self.active_tasks: Dict[str, asyncio.Task] = {}

        self._history_dirty: Dict[str, bool] = {}

        self._last_msg_hash: Dict[str, Tuple[int, float]] = {}

        cfg = load_config()
        self.config = cfg
        if cfg.llm.model:
            self.model = cfg.llm.model
        self.primary_model = self.model
        self.fallback_models = list(getattr(cfg.llm, "fallback_models", []) or [])

        self._provider: Tuple = self._resolve_provider()

        self.toolbox = Toolbox(
            allowed_paths=cfg.whitelist.allowed_paths, bus=bus, config=cfg
        )
        self.toolbox.set_agent(self)
        if self.scheduler:
            self.toolbox.set_scheduler(self.scheduler)

        self.vector_service = get_vector_service(cfg)

        self._tool_registry: Dict[str, Any] = {
            "read_file": self.toolbox.read_file,
            "write_file": self.toolbox.write_file,
            "delete_file": self.toolbox.delete_file,
            "list_dir": self.toolbox.list_dir,
            "search_files": self.toolbox.search_files,
            "run_command": self.toolbox.run_command,
            "memory_search": self.toolbox.memory_search,
            "cron_add": self.toolbox.cron_add,
            "cron_list": self.toolbox.cron_list,
            "cron_remove": self.toolbox.cron_remove,
            "create_skill": self.toolbox.create_skill,
        }

        self.skill_registry = SkillRegistry(skill_dirs=["./skills"], config=cfg)
        self.subagent_registry = SubagentRegistry()
        self.toolbox.set_subagent_registry(self.subagent_registry)

        # ── Sub-module managers ──────────────────────────────────────────
        self.confirm = ConfirmationManager(
            toolbox=self.toolbox,
            truncate_fn=self._truncate_preview,
            safe_json_load_fn=self._safe_json_load,
        )
        self.rag = RagEngine(
            truncate_fn=self._truncate_preview,
            safe_json_load_fn=self._safe_json_load,
        )

        self._tool_definitions: Optional[List[Dict]] = None
        self._warmed = False
        asyncio.create_task(self._init_skills_and_tools())

        self._stable_prompt_cache: Dict[str, Tuple[str, float]] = {}
        self._STABLE_PROMPT_TTL = 30.0
        self._history_flush_interval = 5.0
        self._last_history_flush: Dict[str, float] = {}
        self._image_input_fallback_sessions: Set[str] = set()

    async def _init_skills_and_tools(self) -> None:
        """Background: discover skills, build tool definitions, then warm up slow services."""
        await asyncio.to_thread(self.skill_registry.discover_and_load)
        await asyncio.to_thread(self.subagent_registry.discover_and_load)
        asyncio.create_task(self._cleanup_persisted_histories())

        # Initialize MCP servers if available
        try:
            from core.mcp_client import get_mcp_manager

            await get_mcp_manager().initialize()
        except Exception as e:
            logger.error(f"Failed to initialize MCP servers: {e}")

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

        # 3. Warm the chat LLM HTTP connection with a minimal 1-token call
        try:
            model, base_url, api_key, custom_llm_provider = self._provider
            await acompletion(
                model=model,
                messages=[{"role": "user", "content": "hi"}],
                max_tokens=1,
                stream=False,
                base_url=base_url,
                api_key=api_key,
                custom_llm_provider=custom_llm_provider,
            )
            logger.info("✅ LLM connection pool warmed.")
        except Exception as e:
            # Warmup failure is never fatal — log and continue
            if prompt_module.is_setup_complete():
                logger.warning(f"⚠ LLM warmup failed (non-critical): {e}")
            else:
                logger.debug(f"LLM warmup skipped/failed during setup: {e}")

    def _refresh_tool_definitions(self) -> None:
        """Rebuild and cache tool definitions. Call only when skills change."""
        self._tool_definitions = self.toolbox.get_tool_definitions()

        logger.debug(
            f"Tool definitions refreshed ({len(self._tool_definitions)} tools)."
        )

    def _get_tool_definitions(self) -> List[Dict]:
        if self._tool_definitions is None:
            self._refresh_tool_definitions()
        return self._tool_definitions

    def _get_tool_definitions_for_turn(self, user_text: str = "") -> List[Dict]:
        all_tools = self._get_tool_definitions()
        if self._tool_shortlist_enabled():
            selected = shortlist_tool_definitions(all_tools, user_text)
            strategy = "shortlist_env_opt_in"
        else:
            selected = list(all_tools)
            strategy = "full_schema_default"

        all_names = self._tool_definition_names(all_tools)
        selected_names = self._tool_definition_names(selected)
        self._log_tool_debug(
            "tool_schema_selection",
            strategy=strategy,
            user_text=user_text,
            total_tool_count=len(all_names),
            total_tools=all_names,
            selected_tool_count=len(selected_names),
            selected_tools=selected_names,
        )
        return selected

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
        from core.llm_utils import resolve_provider_config

        p_cfg = resolve_provider_config(
            self.model, default_base_url=self.config.llm.base_url
        )
        return (
            p_cfg["model"],
            p_cfg["base_url"],
            p_cfg["api_key"],
            p_cfg["custom_llm_provider"],
        )

    def _resolve_provider_chain(
        self,
    ) -> List[Tuple[str, str, Optional[str], Optional[str], Optional[str]]]:
        from core.llm_utils import build_provider_chain

        chain = build_provider_chain(
            self.model,
            getattr(self, "fallback_models", []) or [],
            default_base_url=self.config.llm.base_url,
        )
        return [
            (
                source_model,
                cfg["model"],
                cfg["base_url"],
                cfg["api_key"],
                cfg["custom_llm_provider"],
            )
            for source_model, cfg in chain
        ]

    def set_model(self, model: str) -> None:
        """Switch the active model and refresh cached provider config."""
        self.model = model
        self.primary_model = model
        self._provider = self._resolve_provider()
        self._stable_prompt_cache.clear()
        self._image_input_fallback_sessions.clear()

    def get_llm_runtime_status(self) -> Dict[str, Any]:
        return {
            "configured_model": self.primary_model,
            "active_model": self.model,
            "fallback_models": list(self.fallback_models),
            "using_fallback": self.model != self.primary_model,
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
                if url:
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
        for attachment in attachments:
            if not isinstance(attachment, dict):
                continue
            if attachment.get("kind") == "image":
                continue

            name = str(attachment.get("name") or "document").strip()
            path = str(attachment.get("path") or "").strip()
            note = f"[Attached document: {name}]"
            if path:
                note += f" Saved as `{path}`."
            lines.append(note)

        return "\n".join(lines)

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
        return str(reply_to_user or "").strip() == str(raw_reply or "").strip()

    @staticmethod
    def _messages_have_image_inputs(messages: List[Dict[str, Any]]) -> bool:
        for message in messages:
            content = message.get("content") if isinstance(message, dict) else None
            if not isinstance(content, list):
                continue
            for item in content:
                if isinstance(item, dict) and item.get("type") == "image_url":
                    return True
        return False

    def _downgrade_image_messages_for_text_model(
        self, messages: List[Dict[str, Any]], session_key: str
    ) -> bool:
        changed = False
        for message in messages:
            if not isinstance(message, dict):
                continue
            content = message.get("content")
            if not isinstance(content, list):
                continue
            if not any(
                isinstance(item, dict) and item.get("type") == "image_url"
                for item in content
            ):
                continue
            message["content"] = self._render_text_only_message_content(content)
            changed = True

        if changed:
            self.metrics.record_anomaly(
                session_key,
                "image_input_downgraded_for_text_model",
                detail=self.model,
            )
        return changed

    def _should_retry_without_images(
        self, error: Exception, messages: List[Dict[str, Any]]
    ) -> bool:
        if not self._messages_have_image_inputs(messages):
            return False
        error_text = str(error).lower()
        return any(
            phrase in error_text
            for phrase in (
                "not a multimodal model",
                "does not support vision",
                "does not support image",
                "doesn't support vision",
                "doesn't support image",
            )
        )

    async def _get_stable_prompt(
        self, sender_id: str, channel: str, chat_id: str, sender_name: str = ""
    ) -> str:
        """
        Return the rarely-changing part of the system prompt, cached per
        (sender_id, channel) for _STABLE_PROMPT_TTL seconds.
        """
        key = f"{sender_id}:{channel}"
        cached = self._stable_prompt_cache.get(key)
        now = time.monotonic()

        if cached and now < cached[1]:
            return cached[0]

        try:
            soul = (
                await asyncio.to_thread(SOUL_FILE.read_text, encoding="utf-8")
                if SOUL_FILE.exists()
                else ""
            )
            identity_raw = (
                await asyncio.to_thread(IDENTITY_FILE.read_text, encoding="utf-8")
                if IDENTITY_FILE.exists()
                else ""
            )
        except Exception as e:
            logger.warning(f"Error reading persona files: {e}")
            soul = identity_raw = ""

        if not prompt_module.is_setup_complete(
            soul_content=soul, identity_content=identity_raw
        ):
            return prompt_module.get_setup_prompt(
                soul_content=soul, identity_content=identity_raw
            )

        stable = prompt_module.build_stable_system_prompt(
            sender_id=sender_id,
            channel=channel,
            chat_id=chat_id,
            model=self.model,
            allowed_paths=self.toolbox.allowed_paths,
            skill_registry=self.skill_registry,
            config=self.config,
            soul=soul,
            identity_raw=identity_raw,
            sender_name=sender_name,
        )
        self._stable_prompt_cache[key] = (stable, now + self._STABLE_PROMPT_TTL)
        return stable

    def _invalidate_stable_prompt(self, sender_id: str) -> None:
        """Drop cached prompts for this sender. Call after soul/identity updates."""
        for key in [
            k for k in self._stable_prompt_cache if k.startswith(f"{sender_id}:")
        ]:
            del self._stable_prompt_cache[key]

    def _log_session_event(self, session_key: str, event: dict) -> None:
        try:
            asyncio.create_task(
                self.session_manager.append_event_log(session_key, event)
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
    ) -> str:
        """Stable (cached) + volatile (per-message: memory + RAG + timestamp)."""
        stable = await self._get_stable_prompt(sender_id, channel, chat_id, sender_name)
        skills_docs = self.skill_registry.get_relevant_prompt_additions(current_message)
        subagent_docs = self.subagent_registry.get_prompt_additions(current_message)
        include_private_memory = prompt_module.should_load_private_context(
            sender_id, channel, self.config
        )
        volatile = prompt_module.get_volatile_prompt_suffix(
            recalled_context,
            include_private_memory=include_private_memory,
            current_message=current_message,
        )
        return (
            stable
            + (skills_docs + "\n" if skills_docs else "")
            + (subagent_docs + "\n" if subagent_docs else "")
            + volatile
        )

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
    ) -> Dict[str, Any]:
        payload = dict(metadata or {})
        if turn_id:
            payload.setdefault("turn_id", turn_id)
        if message_id:
            payload.setdefault("message_id", message_id)
        return payload

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

    def _tool_shortlist_enabled(self) -> bool:
        return self._env_truthy("LIMEBOT_ENABLE_TOOL_SHORTLIST")

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
        raw = str(text or "")
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

    # ── Stream result unpacker ───────────────────────────────────────────

    @staticmethod
    def _unpack_stream_result(result) -> tuple:
        """Unpack _consume_stream result into (content, tool_calls, usage, streamed_to_web)."""
        if isinstance(result, tuple) and len(result) >= 4:
            return result[0], result[1], result[2], result[3]
        content, tool_calls, usage = result
        return content, tool_calls, usage, False

    # ── Overlap deduplication ────────────────────────────────────────────

    @staticmethod
    def _dedup_overlap(accumulated: str, new_content: str) -> str:
        """Return the portion of *new_content* that doesn't overlap with the
        tail of *accumulated*.  Used after tool-call continuations where the
        LLM may repeat previously-streamed text."""
        acc_s, nxt_s = accumulated.strip(), new_content.strip()
        if not (acc_s and nxt_s):
            return new_content

        max_overlap = min(len(acc_s), len(nxt_s), 100)
        for length in range(max_overlap, 4, -1):
            suffix = acc_s[-length:]
            if nxt_s.lower().startswith(suffix.lower()):
                match = re.search(re.escape(suffix), new_content, re.IGNORECASE)
                if match:
                    clean = new_content[match.start() + length :].lstrip()
                    return clean if clean.strip() else ""
        return new_content

    @staticmethod
    def _estimate_tokens(messages: List[Dict]) -> int:
        """
        O(n) token estimate (~4 chars per token, slightly conservative).
        Used in the pruning loop to avoid O(n²) token_counter calls.
        """
        total = 0
        for m in messages:
            c = m.get("content", "")
            total += sum(len(str(i)) for i in c) if isinstance(c, list) else len(str(c))
        return total // 4

    async def run(self) -> None:
        self._running = True
        logger.info(f"Agent loop started (model: {self.model})")
        while self._running:
            try:
                msg = await self.bus.consume_inbound()
                asyncio.create_task(self._process_message(msg))
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"Error in agent loop: {e}")

    async def stop(self) -> None:
        self._running = False

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

        for conf_id, conf in list(self.pending_confirmations.items()):
            if conf.get("session_key") not in related_sessions:
                continue
            conf["approved"] = False
            event = conf.get("event")
            if event:
                event.set()
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

        for sk, t in list(self.active_tasks.items()):
            if t.done():
                del self.active_tasks[sk]

        return cancelled_any

    async def confirm_tool(
        self, conf_id: str, approved: bool, session_whitelist: bool = False
    ) -> bool:
        if conf_id in self.pending_confirmations:
            conf = self.pending_confirmations[conf_id]
            self._log_session_event(
                conf.get("session_key", "unknown"),
                {
                    "type": "confirmation_resolved",
                    "confirmation_id": conf_id,
                    "approved": approved,
                    "tool": conf.get("tool"),
                    "session_whitelist": session_whitelist,
                },
            )
            conf["approved"] = approved
            if approved and session_whitelist:
                sk = conf["session_key"]
                self.session_whitelists.setdefault(sk, set()).add(conf["tool"])
                logger.info(f"🔓 Added {conf['tool']} to whitelist for {sk}")
            conf["event"].set()
            logger.info(f"✅ Tool {conf_id} {'approved' if approved else 'denied'}")
            return True
        logger.warning(f"⚠️ Confirmation {conf_id} not found or expired.")
        return False

    def _mark_dirty(self, session_key: str) -> None:
        self._history_dirty[session_key] = True

    async def _flush_history(self, session_key: str, force: bool = False) -> None:
        """Persist history only if it changed since the last flush."""
        if not (self._history_dirty.get(session_key) and session_key in self.history):
            return

        now = time.monotonic()
        last = self._last_history_flush.get(session_key, 0.0)
        if not force and (now - last) < self._history_flush_interval:
            return

        await self.session_manager.save_history(session_key, self.history[session_key])
        self._history_dirty[session_key] = False
        self._last_history_flush[session_key] = now

    async def run_subagent(
        self,
        parent_session_key: str,
        sub_session_key: str,
        task: str,
        agent_name: Optional[str] = None,
    ) -> str:
        try:
            logger.info(f"[SUB-AGENT] {sub_session_key} ← {parent_session_key}: {task}")

            subagent_profile = self.subagent_registry.get_subagent(agent_name)
            if agent_name and not subagent_profile:
                return f"Error: Unknown subagent '{agent_name}'"

            subagent_model = (
                (subagent_profile or {}).get("model") or "inherit"
            ).strip()
            subagent_max_turns = (subagent_profile or {}).get("max_turns") or 10
            allowed_tools = None
            tool_definitions_override = None
            disallowed_tools = {
                normalize_subagent_tool_name(name)
                for name in ((subagent_profile or {}).get("disallowed_tools") or [])
                if normalize_subagent_tool_name(name)
            }
            if subagent_profile:
                tool_definitions_override = self._filter_tool_definitions_for_subagent(
                    subagent_profile.get("tools"),
                    subagent_profile.get("disallowed_tools"),
                )
                if subagent_profile.get("tools") is not None:
                    allowed_tools = {
                        tool.get("function", {}).get("name")
                        for tool in tool_definitions_override
                        if tool.get("function", {}).get("name")
                    }

            session_model = (
                self.model
                if not subagent_model or subagent_model == "inherit"
                else subagent_model
            )
            await self.session_manager.update_session(
                session_key=sub_session_key,
                model=session_model,
                origin=f"subagent:{parent_session_key}",
                parent_id=parent_session_key,
                task=task,
                subagent_name=agent_name or "",
            )

            try:
                soul = SOUL_FILE.read_text(encoding="utf-8")
                identity = IDENTITY_FILE.read_text(encoding="utf-8")
            except Exception:
                soul = "You are a helpful assistant."
                identity = "Name: LimeBot Sub-Agent"

            profile_lines = []
            if subagent_profile:
                description = (subagent_profile.get("description") or "").strip()
                prompt = (subagent_profile.get("prompt") or "").strip()
                if description:
                    profile_lines.append(f"Profile: {description}")
                if prompt:
                    profile_lines.append(prompt)
                if allowed_tools is None:
                    profile_lines.append("Tool access: inherit the main toolset.")
                else:
                    listed_tools = ", ".join(sorted(allowed_tools)) or "none"
                    profile_lines.append(
                        f"Tool access: only use these tools: {listed_tools}."
                    )
                if disallowed_tools:
                    profile_lines.append(
                        "Never use these tools: "
                        + ", ".join(sorted(disallowed_tools))
                        + "."
                    )
                if subagent_profile.get("background"):
                    profile_lines.append(
                        "This profile is intended for background-friendly work when delegated asynchronously."
                    )
                if subagent_max_turns:
                    profile_lines.append(
                        f"Complete the task within at most {subagent_max_turns} assistant turns."
                    )

            profile_block = "\n".join(line for line in profile_lines if line).strip()
            if profile_block:
                profile_block += "\n"

            sub_system = (
                f"{soul}\n\n{identity}\n\n"
                "--- SUB-AGENT INSTRUCTIONS ---\n"
                + (
                    f"You are the '{agent_name}' subagent.\n"
                    if agent_name and subagent_profile
                    else "You are a generic sub-agent.\n"
                )
                + profile_block
                + f"Primary task: {task}\n"
                + "Work independently, use tools when needed, and return a concise final result.\n" +
                "DO NOT start a conversation — JUST COMPLETE THE TASK.\n"
            )

            sub_history: List[Dict] = [
                {"role": "system", "content": sub_system},
                {"role": "user", "content": f"Task: {task}"},
            ]
            asyncio.create_task(
                self.session_manager.append_chat_log(
                    sub_session_key, {"role": "user", "content": task}
                )
            )

            iteration = 0
            final_result = ""

            while iteration < subagent_max_turns:
                iteration += 1
                logger.info(f"[SUB-AGENT:{sub_session_key}] iteration {iteration}")

                response = await self._llm_call_with_retry(
                    messages=sub_history,
                    session_key=sub_session_key,
                    msg=None,
                    stream=False,
                    tool_context_text=task,
                    tool_definitions_override=tool_definitions_override,
                    model_override=subagent_model,
                )

                if hasattr(response, "usage"):
                    await self.session_manager.update_session(
                        session_key=sub_session_key,
                        model=session_model,
                        origin=f"subagent:{parent_session_key}",
                        usage=response.usage,
                    )

                assistant_msg = response.choices[0].message
                full_content = assistant_msg.content or ""
                tool_calls_raw = assistant_msg.tool_calls

                sub_history.append(assistant_msg.model_dump())
                asyncio.create_task(
                    self.session_manager.append_chat_log(
                        sub_session_key, {"role": "assistant", "content": full_content}
                    )
                )

                if not tool_calls_raw:
                    final_result = full_content
                    break

                for tc in tool_calls_raw:
                    tc_id = tc.id
                    fn = tc.function.name
                    try:
                        args = json.loads(tc.function.arguments) if tc.function.arguments else {}
                        fn, args = self._normalize_tool_alias(
                            fn, args, sub_session_key
                        )
                        logger.info(f"[SUB-AGENT:{sub_session_key}] {fn}({args})")
                        if (allowed_tools is not None and fn not in allowed_tools) or (
                            fn in disallowed_tools
                        ):
                            result = (
                                f"Error: Tool '{fn}' is not allowed for subagent "
                                f"'{agent_name}'."
                            )
                        else:
                            result = await self._execute_tool(
                                fn, args, sub_session_key
                            )
                    except Exception as e:
                        result = f"Error: {e}"

                    sub_history.append(
                        {
                            "role": "tool",
                            "tool_call_id": tc_id,
                            "name": fn,
                            "content": str(result),
                        }
                    )

            if not str(final_result or "").strip():
                summary_prompt = (
                    "Final response only. Do not call any more tools. "
                    "Do not mention truncation, token limits, tool budgets, or that you are an assistant. "
                    "Use only the work already completed. "
                    "If this was a review or verification task, return a concise report with findings or outcome, "
                    "a short rating if appropriate, and 2-4 concrete suggestions. "
                    "If anything remains unverified, state that plainly."
                )
                sub_history.append({"role": "system", "content": summary_prompt})
                try:
                    summary_response = await self._llm_call_with_retry(
                        messages=sub_history,
                        session_key=sub_session_key,
                        msg=None,
                        stream=False,
                        include_tools=False,
                        tool_context_text=task,
                        model_override=subagent_model,
                    )
                    summary_msg = summary_response.choices[0].message
                    final_result = (
                        self._clean_subagent_final_result(summary_msg.content or "")
                        or f"Stopped after reaching max_turns ({subagent_max_turns}) without a final answer."
                    )
                    sub_history.append(summary_msg.model_dump())
                    asyncio.create_task(
                        self.session_manager.append_chat_log(
                            sub_session_key,
                            {"role": "assistant", "content": final_result},
                        )
                    )
                except Exception as summary_error:
                    logger.warning(
                        f"[SUB-AGENT:{sub_session_key}] failed to produce final summary after max_turns: {summary_error}"
                    )
                    final_result = (
                        f"Stopped after reaching max_turns ({subagent_max_turns}) without a final answer."
                    )

            final_result = self._clean_subagent_final_result(final_result)

            report_title = (
                f"--- SUB-AGENT REPORT ({sub_session_key}) [{agent_name}] ---"
                if agent_name
                else f"--- SUB-AGENT REPORT ({sub_session_key}) ---"
            )
            report = (
                f"{report_title}\n"
                f"Task: {task}\n"
                f"Result:\n{final_result or '(Silently completed)'}\n"
            )

            parts = parent_session_key.split(":", 1)
            if len(parts) == 2:
                await self.bus.publish_inbound(
                    InboundMessage(
                        channel=parts[0],
                        sender_id="system",
                        chat_id=parts[1],
                        content=report,
                        metadata={"is_report": True, "subagent_id": sub_session_key},
                    )
                )
            return report

        except Exception as e:
            logger.error(f"Error in sub-agent '{sub_session_key}': {e}")
            return f"Error in sub-agent '{sub_session_key}': {e}"

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
    ) -> Any:

        messages = self._sanitize_messages_for_llm(messages, session_key)
        if not include_tools:
            tools = []
        elif tool_definitions_override is not None:
            tools = tool_definitions_override
        else:
            tools = self._get_tool_definitions_for_turn(tool_context_text)
        if model_override and model_override != "inherit":
            from core.llm_utils import resolve_provider_config

            cfg = resolve_provider_config(
                model_override, default_base_url=self.config.llm.base_url
            )
            provider_chain = [
                (
                    model_override,
                    cfg["model"],
                    cfg["base_url"],
                    cfg["api_key"],
                    cfg["custom_llm_provider"],
                )
            ]
        else:
            provider_chain = self._resolve_provider_chain()
        selected_index = 0
        active_source_model, model, base_url, api_key, custom_llm_provider = (
            provider_chain[selected_index]
        )
        self._log_tool_debug(
            "llm_call_prepare",
            session_key=session_key,
            model=active_source_model,
            stream=stream,
            include_tools=include_tools,
            tool_context=tool_context_text,
            message_count=len(messages),
            tool_count=len(tools),
            tool_names=self._tool_definition_names(tools),
            last_message=messages[-1].get("content", "") if messages else "",
            fallback_chain=[item[0] for item in provider_chain],
        )
        qwen_auth_failover_attempted: Set[str] = set()
        image_fallback_attempted = False
        model_failover_announced = False

        for attempt in range(max_retries):
            try:
                kwargs: Dict[str, Any] = dict(
                    model=model,
                    messages=messages,
                    stream=stream,
                    base_url=base_url,
                    api_key=api_key,
                    custom_llm_provider=custom_llm_provider,
                )
                if tools:
                    kwargs["tools"] = tools
                    kwargs["tool_choice"] = "auto"
                if stream:
                    kwargs["stream_options"] = {"include_usage": True}
                return await acompletion(**kwargs)

            except AuthenticationError as e:
                self._log_tool_debug(
                    "llm_call_error",
                    session_key=session_key,
                    attempt=attempt + 1,
                    source_model=active_source_model,
                    error_type=type(e).__name__,
                    error=str(e),
                )
                next_provider = provider_chain[selected_index + 1 : selected_index + 2]
                if next_provider:
                    selected_index += 1
                    (
                        active_source_model,
                        model,
                        base_url,
                        api_key,
                        custom_llm_provider,
                    ) = provider_chain[selected_index]
                    self.model = active_source_model
                    self._provider = (
                        model,
                        base_url,
                        api_key,
                        custom_llm_provider,
                    )
                    qwen_auth_failover_attempted.clear()
                    logger.warning(
                        f"LLM auth failed for '{provider_chain[selected_index - 1][0]}'; switching to fallback '{active_source_model}'."
                    )
                    if msg and not model_failover_announced:
                        await self.bus.publish_outbound(
                            OutboundMessage(
                                content=f"Switching AI provider to fallback model `{active_source_model}`...",
                                channel=msg.channel,
                                chat_id=msg.chat_id,
                                metadata=self._with_trace_metadata(
                                    {"reply_to": msg.sender_id, "is_warning": True},
                                    turn_id=turn_id,
                                    message_id=message_id,
                                ),
                            )
                        )
                        model_failover_announced = True
                    continue
                # DashScope keys can be region-scoped. If auth fails on one endpoint,
                # try other official compatible endpoints before failing.
                current_base = (base_url or "").lower()
                is_qwen = (active_source_model or "").startswith(
                    "qwen/"
                ) or "dashscope" in current_base
                if is_qwen:
                    qwen_auth_failover_attempted.add(current_base)
                    fallback_bases = [
                        "https://dashscope-intl.aliyuncs.com/compatible-mode/v1",
                        "https://dashscope-us.aliyuncs.com/compatible-mode/v1",
                        "https://dashscope.aliyuncs.com/compatible-mode/v1",
                    ]
                    next_base = next(
                        (
                            url
                            for url in fallback_bases
                            if url.lower() not in qwen_auth_failover_attempted
                        ),
                        None,
                    )
                    if next_base:
                        base_url = next_base
                        logger.warning(
                            f"Qwen auth failed; retrying with alternate endpoint: {base_url}"
                        )
                        continue
                raise

            except (
                RateLimitError,
                InternalServerError,
                APIConnectionError,
                ServiceUnavailableError,
            ) as e:
                self._log_tool_debug(
                    "llm_call_error",
                    session_key=session_key,
                    attempt=attempt + 1,
                    source_model=active_source_model,
                    error_type=type(e).__name__,
                    error=str(e),
                )
                is_500 = isinstance(e, InternalServerError)
                is_conn = isinstance(e, (APIConnectionError, ServiceUnavailableError))
                wait_time = (2**attempt) * 5
                next_provider = provider_chain[selected_index + 1 : selected_index + 2]
                if next_provider:
                    selected_index += 1
                    (
                        active_source_model,
                        model,
                        base_url,
                        api_key,
                        custom_llm_provider,
                    ) = provider_chain[selected_index]
                    self.model = active_source_model
                    self._provider = (
                        model,
                        base_url,
                        api_key,
                        custom_llm_provider,
                    )
                    qwen_auth_failover_attempted.clear()
                    logger.warning(
                        f"LLM provider '{provider_chain[selected_index - 1][0]}' failed; switching to fallback '{active_source_model}'."
                    )
                    if msg and not model_failover_announced:
                        await self.bus.publish_outbound(
                            OutboundMessage(
                                content=f"Primary AI is unavailable, switching to fallback model `{active_source_model}`...",
                                channel=msg.channel,
                                chat_id=msg.chat_id,
                                metadata=self._with_trace_metadata(
                                    {"reply_to": msg.sender_id, "is_warning": True},
                                    turn_id=turn_id,
                                    message_id=message_id,
                                ),
                            )
                        )
                        model_failover_announced = True
                    continue
                if is_conn:
                    error_type = "Connection error"
                elif is_500:
                    error_type = "Server error (500)"
                else:
                    error_type = "Rate limit"
                logger.warning(
                    f"⚠ {error_type} attempt {attempt + 1}/{max_retries}. Waiting {wait_time}s…"
                )

                if attempt == 0 and msg:
                    if is_conn:
                        text = "⏳ Connection lost — retrying when network is back…"
                    elif is_500:
                        text = "⏳ AI service unstable — retrying…"
                    else:
                        text = "⏳ Rate limit hit — retrying…"
                    await self.bus.publish_outbound(
                        OutboundMessage(
                            content=text,
                            channel=msg.channel,
                            chat_id=msg.chat_id,
                            metadata=self._with_trace_metadata(
                                {"reply_to": msg.sender_id, "is_warning": True},
                                turn_id=turn_id,
                                message_id=message_id,
                            ),
                        )
                    )

                await asyncio.sleep(wait_time)

                if attempt == max_retries - 1:
                    if is_conn:
                        content = "❌ Cannot reach AI service. Check your internet connection."
                    elif is_500:
                        content = "❌ AI service experiencing errors. Try again in a few minutes."
                    else:
                        content = "❌ API rate limit exceeded. Please wait a minute."
                    if msg:
                        await self.bus.publish_outbound(
                            OutboundMessage(
                                content=content,
                                channel=msg.channel,
                                chat_id=msg.chat_id,
                                metadata=self._with_trace_metadata(
                                    {"reply_to": msg.sender_id, "is_error": True},
                                    turn_id=turn_id,
                                    message_id=message_id,
                                ),
                            )
                        )
                raise
            except Exception as e:
                self._log_tool_debug(
                    "llm_call_error",
                    session_key=session_key,
                    attempt=attempt + 1,
                    source_model=active_source_model,
                    error_type=type(e).__name__,
                    error=str(e),
                )
                next_provider = provider_chain[selected_index + 1 : selected_index + 2]
                if next_provider and self._should_failover_model(e):
                    selected_index += 1
                    (
                        active_source_model,
                        model,
                        base_url,
                        api_key,
                        custom_llm_provider,
                    ) = provider_chain[selected_index]
                    self.model = active_source_model
                    self._provider = (
                        model,
                        base_url,
                        api_key,
                        custom_llm_provider,
                    )
                    qwen_auth_failover_attempted.clear()
                    logger.warning(
                        f"LLM call failed for '{provider_chain[selected_index - 1][0]}'; switching to fallback '{active_source_model}'."
                    )
                    if msg and not model_failover_announced:
                        await self.bus.publish_outbound(
                            OutboundMessage(
                                content=f"Retrying with fallback model `{active_source_model}`...",
                                channel=msg.channel,
                                chat_id=msg.chat_id,
                                metadata=self._with_trace_metadata(
                                    {"reply_to": msg.sender_id, "is_warning": True},
                                    turn_id=turn_id,
                                    message_id=message_id,
                                ),
                            )
                        )
                        model_failover_announced = True
                    continue
                if (
                    not image_fallback_attempted
                    and self._should_retry_without_images(e, messages)
                    and self._downgrade_image_messages_for_text_model(
                        messages, session_key
                    )
                ):
                    image_fallback_attempted = True
                    self._disable_image_inputs_for_session(session_key)
                    logger.warning(
                        f"Model '{self.model}' rejected image input for {session_key}; retrying without multimodal content."
                    )
                    continue
                raise

    async def _trim_history(self, session_key: str, max_tokens: int = 12_000) -> None:
        if session_key not in self.history or len(self.history[session_key]) <= 1:
            return

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
            self.history[session_key] = [system_msg] + conv
            return

        logger.info(f"🔄 Token limit ({max_tokens}) reached. Summarising…")

        num_to_summarise = max(1, len(conv) // 3)
        to_summarise = conv[:num_to_summarise]
        remaining = conv[num_to_summarise:]

        while remaining and remaining[0].get("role") == "tool":
            to_summarise.append(remaining.pop(0))

        try:
            resp = await asyncio.to_thread(
                completion,
                model=self.model,
                messages=[
                    {
                        "role": "system",
                        "content": "Summarise in ≤200 words: key decisions, user facts, task state.",
                    },
                    {"role": "user", "content": json.dumps(to_summarise)},
                ],
                max_tokens=300,
                base_url=self.config.llm.base_url,
                api_key=self.config.llm.api_key,
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
            current_tokens = self._estimate_tokens(conv)

            while conv and current_tokens > target_tokens:
                popped = conv.pop(0)
                current_tokens -= self._estimate_tokens([popped])

                if popped.get("role") == "assistant" and popped.get("tool_calls"):
                    while conv and conv[0].get("role") == "tool":
                        tool_msg = conv.pop(0)
                        current_tokens -= self._estimate_tokens([tool_msg])
                while conv and conv[0].get("role") == "tool":
                    tool_msg = conv.pop(0)
                    current_tokens -= self._estimate_tokens([tool_msg])

        self.history[session_key] = [system_msg] + conv
        self._mark_dirty(session_key)

    async def _execute_browser_tool(
        self, function_name: str, args: Dict[str, Any], session_key: str
    ) -> Any:
        try:
            browser = await get_browser_manager(
                session_key=session_key, config=self.config
            )
            on_progress = self.toolbox.send_progress

            dispatch: Dict[str, Any] = {
                "browser_navigate": lambda: browser.navigate(
                    args.get("url", ""), on_progress=on_progress
                ),
                "browser_click": lambda: browser.click(args.get("element_id", "")),
                "browser_type": lambda: browser.type_text(
                    args.get("element_id", ""), args.get("text", "")
                ),
                "browser_snapshot": lambda: browser.snapshot(),
                "browser_scroll": lambda: browser.scroll(
                    args.get("direction", "down"), args.get("amount", 500)
                ),
                "browser_wait": lambda: browser.wait(args.get("ms", 1000)),
                "browser_press_key": lambda: browser.press_key(
                    args.get("key", "Enter")
                ),
                "browser_go_back": lambda: browser.go_back(),
                "browser_tabs": lambda: browser.list_tabs(),
                "browser_switch_tab": lambda: browser.switch_tab(args.get("index", 0)),
                "browser_extract": lambda: browser.extract(
                    args.get("selector", "body"), limit=args.get("limit", 5000)
                ),
                "browser_extract_large": lambda: browser.extract(
                    args.get("selector", "body"), limit=100_000
                ),
                "browser_get_page_text": lambda: browser.get_page_text(),
                "browser_list_media": lambda: browser.list_media(
                    on_progress=on_progress
                ),
                "google_search": lambda: browser.google_search(
                    args.get("query", ""), on_progress=on_progress
                ),
            }

            handler = dispatch.get(function_name)
            if handler is None:
                return f"Error: Unknown browser tool '{function_name}'"

            result = await handler()

            if isinstance(result, dict):
                if result.get("success"):
                    parts = []
                    for key, label in [
                        ("query", "**Search Query:**"),
                        ("results_summary", "**Results:**"),
                        ("title", "**Page:**"),
                        ("url", "**URL:**"),
                        ("note", "**Note:**"),
                        ("warning", "**Warning:**"),
                        ("message", None),
                        ("elements", "**Elements:**"),
                        ("media_summary", None),
                    ]:
                        val = result.get(key)
                        if val:
                            parts.append(f"{label}\n{val}" if label else val)
                    if result.get("text"):
                        parts.append(f"**Content:**\n{result['text'][:2000]}")
                    return "\n".join(parts) if parts else "Success"
                return f"Error: {result.get('error', 'Unknown error')}"

            if isinstance(result, list):
                return "Open Tabs:\n" + "".join(
                    f"{t['index']}: {t['title']} ({t['url']}) {'[ACTIVE]' if t.get('active') else ''}\n"
                    for t in result
                )

            return str(result)

        except Exception as e:
            logger.exception(
                f"Browser tool execution failed: {function_name} args={args}"
            )
            return f"Error executing browser tool: {e}"

    async def _execute_tag_compat_tool(
        self, function_name: str, function_args: Dict[str, Any]
    ) -> str:
        content = str(
            function_args.get("content")
            or function_args.get("entry")
            or function_args.get("text")
            or ""
        ).strip()
        if not content:
            return f"Error: '{function_name}' requires 'content'"

        raw_reply = f"<{function_name}>{content}</{function_name}>"
        await process_tags(
            raw_reply=raw_reply,
            sender_id="tool-compat",
            validate_soul=prompt_module.validate_and_save_soul,
            validate_identity=prompt_module.validate_and_save_identity,
            validate_mood=prompt_module.validate_and_save_mood,
            validate_relationship=prompt_module.validate_and_save_relationships,
            vector_service=self.vector_service,
            bus=self.bus,
            msg=None,
            config=self.config,
        )

        if function_name == "save_memory":
            return "Long-term memory saved."
        if function_name == "log_memory":
            return "Memory logged."
        return "Tag action completed."

    async def _execute_tool(
        self, function_name: str, function_args: dict, session_key: str
    ) -> Any:

        cached = self.tool_cache.get(function_name, function_args)
        if cached:
            logger.debug(f"⚡ Cache hit: {function_name}")
            return cached

        # ── Intercept echo-as-memory ─────────────────────────────────

        if function_name == "run_command":
            cmd = function_args.get("command", "")
            echo_match = re.match(r'^echo\s+["\'](.+?)["\']\s*$', cmd, re.DOTALL)
            if echo_match:
                entry = echo_match.group(1).strip()
                if len(entry) > 10:
                    from datetime import datetime

                    memory_dir = Path("persona/memory")
                    memory_dir.mkdir(parents=True, exist_ok=True)
                    today = datetime.now().strftime("%Y-%m-%d")
                    mem_file = memory_dir / f"{today}.md"
                    log_line = f"\n- **[{datetime.now().strftime('%H:%M')}]** {entry}"
                    try:
                        with open(mem_file, "a", encoding="utf-8") as f:
                            f.write(log_line)

                        if self.vector_service:
                            try:
                                asyncio.create_task(
                                    self.vector_service.add_entry(
                                        entry, category="journal"
                                    )
                                )
                            except Exception:
                                pass  # embeddings unsupported — grep fallback
                        logger.info(f"📝 Redirected echo→log_memory: {entry[:80]}")
                        return f"Memory saved: {entry}"
                    except Exception as e:
                        logger.error(f"Error in echo→log_memory redirect: {e}")

        try:
            if function_name.startswith("clawhub_"):
                skill_slug = function_name[len("clawhub_") :]
                # We need to map back to the original folder name if it was slugified.
                # For now assume the folder name is the slug retrieved from parser.get_installed_skills()
                args = function_args.get("args", "")
                from skills.clawhub.parser import run_skill

                result = await asyncio.to_thread(run_skill, skill_slug, args)
            elif function_name == "spawn_agent":
                result = await self.toolbox.spawn_agent(
                    session_key=session_key, **function_args
                )
            elif (
                function_name.startswith("browser_") or function_name == "google_search"
            ):
                result = await self._execute_browser_tool(
                    function_name, function_args, session_key
                )
            elif function_name.startswith("mcp_"):
                from core.mcp_client import get_mcp_manager

                result = await get_mcp_manager().execute_tool(
                    function_name, function_args
                )
            elif function_name in _TAG_COMPAT_TOOLS:
                result = await self._execute_tag_compat_tool(
                    function_name, function_args
                )
            else:
                handler = self._tool_registry.get(function_name)
                if handler:
                    result = await handler(**function_args)
                else:
                    self.metrics.record_anomaly(
                        session_key,
                        "unknown_tool",
                        detail=f"{function_name}({function_args})",
                    )
                    result = f"Error: Unknown tool '{function_name}'"

            is_read_only = function_name in _BROWSER_CACHEABLE or function_name in {
                "read_file",
                "list_dir",
                "search_files",
                "memory_search",
            }
            if result and not str(result).startswith("Error:") and is_read_only:
                self.tool_cache.set(function_name, function_args, result)

            if (
                function_name
                in {
                    "write_file",
                    "delete_file",
                    "create_skill",
                    "run_command",
                }
                and result
                and not str(result).startswith("Error:")
            ):
                # File/system mutations can stale read/list/search caches.
                self.tool_cache.clear()

            return result

        except Exception as e:
            return f"Error executing '{function_name}': {e}"

    @staticmethod
    def _extract_run_command_exit_code(result: Any) -> Optional[int]:
        match = re.search(r"Exit Code:\s*(-?\d+)", str(result))
        if not match:
            return None
        try:
            return int(match.group(1))
        except (TypeError, ValueError):
            return None

    def _is_tool_result_error(self, function_name: str, result: Any) -> bool:
        text = str(result or "")

        if text.startswith("Error:") or text.startswith("Error executing"):
            return True
        if text.startswith("ACTION CANCELLED:") or text.startswith("ACTION BLOCKED:"):
            return True
        if "[TIMEOUT]" in text or "[STALL]" in text:
            return True

        if function_name == "run_command":
            exit_code = self._extract_run_command_exit_code(text)
            if exit_code not in (None, 0):
                return True

        return False

    def _build_tool_fallback_reply(self, session_key: str, max_items: int = 2) -> str:
        tool_rows: List[Tuple[str, str]] = []
        for entry in reversed(self.history.get(session_key, [])):
            if entry.get("role") != "tool":
                continue
            name = str(entry.get("name") or "tool")
            content = str(entry.get("content") or "").strip()
            tool_rows.append((name, content))
            if len(tool_rows) >= max_items:
                break

        if not tool_rows:
            return "I finished running the tool, but I don't have a follow-up response yet."

        tool_rows.reverse()
        failed_rows: List[Tuple[str, str]] = []
        for name, content in tool_rows:
            if not self._is_tool_result_error(name, content):
                continue
            first_line = next(
                (ln.strip() for ln in content.splitlines() if ln.strip()),
                "Tool execution failed.",
            )
            if len(first_line) > 180:
                first_line = first_line[:180] + "..."
            failed_rows.append((name, first_line))

        if failed_rows:
            lines = "\n".join([f"- `{name}`: {line}" for name, line in failed_rows])
            return "I ran the requested tool(s), but they failed:\n" + lines

        recent_tools = ", ".join(f"`{name}`" for name, _ in tool_rows)
        return (
            "I finished the requested tool step(s) successfully, but I did not produce "
            f"a natural-language wrap-up. Recent steps: {recent_tools}."
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
        """Repair assistant tool calls in-place before sending history upstream."""
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
        for tool_call in tool_calls:
            tc_id, function_name, function_args = self._parse_tool_call(
                tool_call, session_key
            )
            preview = self._build_confirmation_preview(
                function_name, function_args, session_key
            )
            await self._publish_both(
                msg,
                "",
                {
                    "type": "tool_execution",
                    "status": "planned",
                    "tool": function_name,
                    "args": function_args,
                    "preview": preview,
                    "tool_call_id": tc_id,
                    "turn_id": turn_id,
                    "message_id": message_id,
                },
            )

    async def _execute_tool_batch(
        self,
        tool_calls: list,
        session_key: str,
        msg: Optional[InboundMessage],
        turn_id: Optional[str] = None,
        message_id: Optional[str] = None,
    ) -> bool:
        """Run all tools in parallel. Returns True if any was blocked."""
        any_blocked = False

        async def _run_one(tool_call: dict):
            tc_id = tool_call["id"]
            function_name = tool_call["function"]["name"]
            is_internal = False

            try:
                tool_context.set(
                    {
                        "tc_id": tc_id,
                        "chat_id": msg.chat_id if msg else "system",
                        "turn_id": turn_id or "",
                        "message_id": message_id or "",
                    }
                )

                tc_id, function_name, function_args = self._parse_tool_call(
                    tool_call, session_key
                )

                logger.info(f"Executing: {function_name}({function_args})")

                is_internal = False
                is_whatsapp = (
                    msg is not None and getattr(msg, "channel", "") == "whatsapp"
                )

                if function_name in _SENSITIVE_TOOLS:
                    is_whitelisted = (
                        session_key in self.session_whitelists
                        and function_name in self.session_whitelists[session_key]
                    )
                    if is_whatsapp:
                        is_whitelisted = function_name != "delete_file"
                    elif getattr(self.config, "autonomous_mode", False) or is_internal:
                        is_whitelisted = True

                    if not is_whitelisted:
                        conf_id = f"conf_{uuid.uuid4().hex[:8]}"
                        event = asyncio.Event()
                        confirmation_preview = self._build_confirmation_preview(
                            function_name, function_args, session_key
                        )
                        self.pending_confirmations[conf_id] = {
                            "event": event,
                            "approved": False,
                            "session_key": session_key,
                            "tool": function_name,
                            "preview": confirmation_preview,
                        }
                        self._log_session_event(
                            session_key,
                            {
                                "type": "confirmation_requested",
                                "confirmation_id": conf_id,
                                "tool": function_name,
                                "args": function_args,
                            },
                        )

                        embed_fields = self._build_confirmation_embed(
                            function_name,
                            function_args,
                            session_key,
                            preview=confirmation_preview,
                        )

                        tool_content = (
                            f"🛠️ Executing {function_name}..." if is_whatsapp else "⏳"
                        )
                        conf_meta = {
                            "type": "tool_execution",
                            "status": "waiting_confirmation",
                            "tool": function_name,
                            "args": function_args,
                            "preview": confirmation_preview,
                            "tool_call_id": tc_id,
                            "conf_id": conf_id,
                            "turn_id": turn_id,
                            "message_id": message_id,
                            "embed": {
                                "title": "Exec Approval Required",
                                "description": "A command needs your approval.",
                                "color": "#F59E0B",
                                "fields": embed_fields,
                                "footer": f"Expires in 300s | ID: {conf_id}",
                            },
                        }
                        await self._publish(msg, tool_content, conf_meta)
                        if msg and msg.channel != "web":
                            web_meta = {
                                k: v for k, v in conf_meta.items() if k != "embed"
                            }
                            await self.bus.publish_outbound(
                                OutboundMessage(
                                    channel="web",
                                    chat_id=msg.chat_id or "system",
                                    content="",
                                    metadata=web_meta,
                                )
                            )

                        try:
                            await asyncio.wait_for(event.wait(), timeout=300)
                            if not self.pending_confirmations[conf_id]["approved"]:
                                return (
                                    tc_id,
                                    function_name,
                                    function_args,
                                    "ACTION CANCELLED: User denied.",
                                    False,
                                    is_internal,
                                )
                        except (asyncio.TimeoutError, asyncio.CancelledError):
                            return (
                                tc_id,
                                function_name,
                                function_args,
                                "ACTION CANCELLED: Timed out.",
                                False,
                                is_internal,
                            )
                        finally:
                            self.pending_confirmations.pop(conf_id, None)

                tool_content = (
                    f"🛠️ Executing {function_name}..." if is_whatsapp else "⏳"
                )
                run_meta = {
                    "type": "tool_execution",
                    "status": "running",
                    "tool": function_name,
                    "args": function_args,
                    "tool_call_id": tc_id,
                    "turn_id": turn_id,
                    "message_id": message_id,
                }
                await self._publish_both(msg, tool_content, run_meta)
                self._log_session_event(
                    session_key,
                    {
                        "type": "tool_started",
                        "tool": function_name,
                        "tool_call_id": tc_id,
                        "args": function_args,
                    },
                )

                t0 = time.time()
                try:
                    # General safety timeout for ANY tool execution (MCP, Browser, etc.)
                    tool_timeout = getattr(self.config, "tool_timeout", 120.0)
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

            except Exception as e:
                result = str(e)
                function_args = locals().get("function_args", {})
                self.metrics.record_tool_call(session_key, function_name, 0, error=True)

            is_blocked = str(result).startswith("ACTION BLOCKED:")
            self._log_session_event(
                session_key,
                {
                    "type": "tool_finished",
                    "tool": function_name,
                    "tool_call_id": tc_id,
                    "blocked": is_blocked,
                    "is_internal": is_internal,
                    "result_preview": str(result)[:400],
                },
            )
            return tc_id, function_name, function_args, result, is_blocked, is_internal

        outcomes = await asyncio.gather(
            *[_run_one(tc) for tc in tool_calls], return_exceptions=True
        )

        for i, outcome in enumerate(outcomes):
            if isinstance(outcome, Exception):
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

                try:
                    err_meta = {
                        "type": "tool_execution",
                        "tool": fail_name,
                        "status": "error",
                        "args": fail_args,
                        "result": f"Execution failed or was cancelled: {type(outcome).__name__}",
                        "tool_call_id": fail_tc_id,
                        "turn_id": turn_id,
                        "message_id": message_id,
                    }
                    await self._publish_both(msg, fail_name, err_meta)
                except Exception:
                    pass
                continue

            tc_id, function_name, function_args, result, is_blocked, is_internal = (
                outcome
            )
            if is_blocked:
                any_blocked = True

            if not is_internal:
                tool_status = (
                    "error"
                    if self._is_tool_result_error(function_name, result)
                    else "completed"
                )
                try:
                    done_meta = {
                        "type": "tool_execution",
                        "tool": function_name,
                        "status": tool_status,
                        "args": function_args,
                        "result": str(result)[:TOOL_BROADCAST_MAX_CHARS],
                        "tool_call_id": tc_id,
                        "turn_id": turn_id,
                        "message_id": message_id,
                    }
                    await self._publish_both(msg, "", done_meta)
                except Exception:
                    pass

            limit = _TOOL_RESULT_LIMITS.get(function_name, _DEFAULT_TOOL_RESULT_LIMIT)
            str_result = str(result)
            if len(str_result) > limit:
                str_result = (
                    str_result[:limit]
                    + f"… (truncated {len(str_result) - limit} chars)"
                )

            self.history[session_key].append(
                {
                    "role": "tool",
                    "tool_call_id": tc_id,
                    "name": function_name,
                    "content": str_result,
                }
            )
            self._mark_dirty(session_key)

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

    def _extract_tool_from_content(self, content: str) -> list:
        if not content or not content.strip():
            return []

        default_arg_names = {
            "read_file": "path",
            "write_file": "content",
            "delete_file": "path",
            "list_dir": "path",
            "search_files": "query",
            "run_command": "command",
            "memory_search": "query",
            "google_search": "query",
            "browser_navigate": "url",
            "spawn_agent": "task",
            "cron_remove": "job_id",
            "save_memory": "content",
            "log_memory": "content",
        }

        def _make_tool_call(function_name: str, function_args: Dict[str, Any]) -> list:
            return [
                {
                    "id": f"call_{uuid.uuid4().hex[:12]}",
                    "type": "function",
                    "function": {
                        "name": function_name,
                        "arguments": json.dumps(function_args),
                    },
                }
            ]

        def _tool_code_call(tool_expr: str) -> list:
            raw_expr = str(tool_expr or "").strip()
            if not raw_expr:
                return []

            try:
                parsed = ast.parse(raw_expr, mode="eval")
            except Exception:
                return []

            call = parsed.body
            if not isinstance(call, ast.Call):
                return []
            if not isinstance(call.func, ast.Name):
                return []

            canonical_name = TOOL_NAME_ALIASES.get(call.func.id, call.func.id)
            arg_names = {
                "read_file": ["path"],
                "write_file": ["path", "content"],
                "delete_file": ["path"],
                "list_dir": ["path"],
                "search_files": ["query", "path"],
                "run_command": ["command"],
                "memory_search": ["query"],
                "google_search": ["query"],
                "browser_navigate": ["url"],
                "spawn_agent": ["task"],
                "cron_remove": ["job_id"],
                "save_memory": ["content"],
                "log_memory": ["content"],
            }.get(canonical_name)
            if arg_names is None:
                return []

            function_args: Dict[str, Any] = {}
            try:
                for idx, arg in enumerate(call.args):
                    if idx >= len(arg_names):
                        return []
                    function_args[arg_names[idx]] = ast.literal_eval(arg)
                for kw in call.keywords:
                    if kw.arg is None:
                        return []
                    function_args[kw.arg] = ast.literal_eval(kw.value)
            except Exception:
                return []

            return _make_tool_call(canonical_name, function_args)

        def _legacy_xml_tool_call(tag_name: str, inner_content: str) -> list:
            canonical_name = TOOL_NAME_ALIASES.get(tag_name, tag_name)
            raw_content = (inner_content or "").strip()
            if not raw_content:
                return []

            function_args: Dict[str, Any]
            if raw_content.startswith("{") and raw_content.endswith("}"):
                try:
                    parsed = json.loads(raw_content)
                    if isinstance(parsed, dict):
                        function_args = parsed
                    else:
                        function_args = {}
                except Exception:
                    function_args = {}
            else:
                default_arg_name = default_arg_names.get(canonical_name)
                if not default_arg_name:
                    return []
                function_args = {default_arg_name: raw_content}

            return _make_tool_call(canonical_name, function_args)

        def _xml_attr_tool_call(tag_name: str, raw_attrs: str) -> list:
            canonical_name = TOOL_NAME_ALIASES.get(tag_name, tag_name)
            default_arg_name = default_arg_names.get(canonical_name)
            if not default_arg_name:
                return []

            attrs: Dict[str, Any] = {}
            for match in re.finditer(
                r'([A-Za-z_][\w-]*)\s*=\s*(?:"([^"]*)"|\'([^\']*)\')', raw_attrs or ""
            ):
                attrs[match.group(1)] = (
                    match.group(2) if match.group(2) is not None else match.group(3)
                )

            if not attrs:
                return []

            if default_arg_name in attrs:
                function_args = {default_arg_name: attrs[default_arg_name]}
            else:
                first_value = next(iter(attrs.values()))
                function_args = {default_arg_name: first_value}
            return _make_tool_call(canonical_name, function_args)

        def _implicit_tool_call_from_dict(parsed: dict):
            if not isinstance(parsed, dict) or "name" in parsed:
                return []

            if isinstance(parsed.get("url"), str) and parsed["url"].strip():
                return _make_tool_call("browser_navigate", {"url": parsed["url"]})

            if isinstance(parsed.get("query"), str) and parsed["query"].strip():
                return _make_tool_call("google_search", {"query": parsed["query"]})

            cmd = parsed.get("cmd")
            if isinstance(cmd, list):
                cmd_text = " ".join(str(part or "") for part in cmd).strip().lower()
                if re.search(r"\b(ls|dir)\b", cmd_text):
                    return _make_tool_call("list_dir", {"path": "."})

            return []

        try:
            cleaned = content
            if "```" in content:
                m = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", content, re.DOTALL)
                if m:
                    cleaned = m.group(1)

            s = cleaned.find("{")
            e = cleaned.rfind("}")
            if s != -1 and e >= s:
                json_str = cleaned[s : e + 1]
                parsed = json.loads(json_str)
                lower = json_str.lower()
                if isinstance(parsed, dict) and "name" in parsed and '"name"' in lower and (
                    '"arguments"' in lower or '"parameters"' in lower
                ):
                    args = parsed.get("arguments", parsed.get("parameters", {}))
                    args_str = (
                        json.dumps(args) if isinstance(args, dict) else str(args)
                    )
                    return [
                        {
                            "id": f"call_{uuid.uuid4().hex[:12]}",
                            "type": "function",
                            "function": {
                                "name": parsed["name"],
                                "arguments": args_str,
                            },
                        }
                    ]
                implicit = _implicit_tool_call_from_dict(parsed)
                if implicit:
                    return implicit
        except Exception:
            pass

        tool_calls = []
        try:
            pattern = (
                r"<\|tool_call_begin\|>\s*(?:functions\.)?([\w\.]+)(?::\d+)?\s*"
                r"<\|tool_call_argument_begin\|>\s*({.*?})\s*<\|tool_call_end\|>"
            )
            for m in re.finditer(pattern, content, re.DOTALL):
                tool_calls.append(
                    {
                        "id": f"call_{uuid.uuid4().hex[:8]}",
                        "type": "function",
                        "function": {"name": m.group(1), "arguments": m.group(2)},
                    }
                )
        except Exception:
            pass
        if tool_calls:
            return tool_calls

        try:
            pipe_tag_pattern = re.compile(
                r"<\|(?P<tag>[A-Za-z_][\w]*)\|>\s*(?P<body>{.*?})\s*<\|/(?P=tag)\|>",
                re.DOTALL,
            )
            for match in pipe_tag_pattern.finditer(content):
                extracted = _legacy_xml_tool_call(
                    match.group("tag"), match.group("body")
                )
                if extracted:
                    return extracted

            tool_code_pattern = re.compile(
                r"<tool_code>\s*(?P<body>.*?)\s*</tool_code>",
                re.IGNORECASE | re.DOTALL,
            )
            for match in tool_code_pattern.finditer(content):
                extracted = _tool_code_call(match.group("body"))
                if extracted:
                    return extracted

            legacy_tag_pattern = re.compile(
                r"<(?P<tag>[A-Za-z_][\w]*)>\s*(?P<body>.*?)\s*</(?P=tag)>",
                re.DOTALL,
            )
            for match in legacy_tag_pattern.finditer(content):
                extracted = _legacy_xml_tool_call(
                    match.group("tag"), match.group("body")
                )
                if extracted:
                    return extracted

            legacy_attr_tag_pattern = re.compile(
                r"<(?P<tag>[A-Za-z_][\w]*)\s+(?P<attrs>[^<>]*?)\s*/?>",
                re.DOTALL,
            )
            for match in legacy_attr_tag_pattern.finditer(content):
                extracted = _xml_attr_tool_call(
                    match.group("tag"), match.group("attrs")
                )
                if extracted:
                    return extracted

            bare_call_pattern = re.compile(
                r"\b(?:list_dir|read_file|write_file|delete_file|search_files|"
                r"run_command|memory_search|google_search|browser_navigate|"
                r"spawn_agent|cron_remove|save_memory|log_memory|ls|dir|cat|"
                r"grep|rg|ripgrep|find_files|shell|terminal|exec|bash|"
                r"powershell|cmd)\s*\([^)]*\)"
            )
            for match in bare_call_pattern.finditer(content):
                extracted = _tool_code_call(match.group(0))
                if extracted:
                    return extracted
        except Exception:
            pass

        # ── Kimi K2 inline format: functions.tool_name:N{"arg":"val"} ──
        try:
            kimi_pattern = r"(?:^|\s)(?::?functions\.)(\w+)(?::\d+)?\s*(\{[^}]*\})"
            for m in re.finditer(kimi_pattern, content, re.DOTALL):
                try:
                    args = json.loads(m.group(2))
                except json.JSONDecodeError:
                    continue
                tool_calls.append(
                    {
                        "id": f"call_{uuid.uuid4().hex[:8]}",
                        "type": "function",
                        "function": {
                            "name": m.group(1),
                            "arguments": json.dumps(args),
                        },
                    }
                )
        except Exception:
            pass

        return tool_calls

    @staticmethod
    def _trim_leading_structural_lines(content: str) -> str:
        """Drop leading fence/bracket residue that sometimes leaks before tool calls."""
        if not content:
            return ""

        lines = content.splitlines(keepends=True)
        while lines:
            stripped = lines[0].strip()
            if not stripped:
                lines.pop(0)
                continue
            if re.fullmatch(r"[`{}\[\],:;]+", stripped):
                lines.pop(0)
                continue
            break
        return "".join(lines)

    def _sanitize_tool_call_content(self, content: str) -> str:
        """Keep only meaningful prose when the model mixes text with tool syntax."""
        if not content:
            return ""

        cleaned = content
        marker_positions = []
        legacy_tag_pattern = (
            r"<(?:read_file|write_file|delete_file|list_dir|search_files|run_command|"
            r"memory_search|google_search|browser_navigate|spawn_agent|"
            r"save_memory|log_memory|ls|dir|list_files|cat|open_file|show_file|"
            r"grep|rg|ripgrep|find_files|shell|terminal|exec|bash|powershell|cmd)>"
        )
        tool_code_pattern = r"<tool_code>"

        inline_match = re.search(r"(?::?functions\.\w+(?::\d+)?\s*\{)", cleaned)
        if inline_match:
            marker_positions.append(inline_match.start())

        block_match = re.search(r"<\|tool_call_begin\|>", cleaned)
        if block_match:
            marker_positions.append(block_match.start())

        pipe_tag_match = re.search(r"<\|[A-Za-z_][\w]*\|>", cleaned)
        if pipe_tag_match:
            marker_positions.append(pipe_tag_match.start())

        json_tool_match = re.search(
            r'\{\s*"name"\s*:\s*"[^"]+"\s*,\s*"(?:arguments|parameters)"\s*:\s*\{',
            cleaned,
            flags=re.DOTALL,
        )
        if json_tool_match:
            marker_positions.append(json_tool_match.start())

        legacy_tag_match = re.search(legacy_tag_pattern, cleaned, flags=re.IGNORECASE)
        if legacy_tag_match:
            marker_positions.append(legacy_tag_match.start())
        tool_code_match = re.search(tool_code_pattern, cleaned, flags=re.IGNORECASE)
        if tool_code_match:
            marker_positions.append(tool_code_match.start())

        if marker_positions:
            cleaned = cleaned[: min(marker_positions)]

        cleaned = re.sub(
            r"```(?:json)?\s*\{.*?\}\s*```",
            "",
            cleaned,
            flags=re.DOTALL | re.IGNORECASE,
        )
        cleaned = re.sub(
            r"<\|tool_call_begin\|>.*?<\|tool_call_end\|>",
            "",
            cleaned,
            flags=re.DOTALL,
        )
        cleaned = re.sub(
            r"<\|[A-Za-z_][\w]*\|>.*?<\|/[A-Za-z_][\w]*\|>",
            "",
            cleaned,
            flags=re.DOTALL,
        )
        cleaned = re.sub(
            r"(?::?functions\.\w+(?::\d+)?\s*\{[^}]*\})",
            "",
            cleaned,
        )
        cleaned = re.sub(
            legacy_tag_pattern + r".*?</(?:read_file|write_file|delete_file|list_dir|search_files|run_command|memory_search|google_search|browser_navigate|spawn_agent|save_memory|log_memory|ls|dir|list_files|cat|open_file|show_file|grep|rg|ripgrep|find_files|shell|terminal|exec|bash|powershell|cmd)>",
            "",
            cleaned,
            flags=re.DOTALL | re.IGNORECASE,
        )
        cleaned = re.sub(
            r"<tool_code>.*?</tool_code>",
            "",
            cleaned,
            flags=re.DOTALL | re.IGNORECASE,
        )
        cleaned = re.sub(
            r'^\s*\{\s*"path"\s*:\s*".*?"\s*\}\s*$',
            "",
            cleaned,
            flags=re.MULTILINE,
        )
        # Strip bare JSON tool-call objects: {"name": "...", "arguments": {...}}
        cleaned = re.sub(
            r'\{\s*"name"\s*:\s*"[^"]+"\s*,\s*"arguments"\s*:\s*\{[^}]*\}\s*\}',
            "",
            cleaned,
        )
        cleaned = self._trim_leading_structural_lines(cleaned)
        cleaned_lines = []
        for line in cleaned.splitlines():
            stripped = line.strip()
            if stripped and re.fullmatch(r"[`{}\[\],:;]+", stripped):
                continue
            cleaned_lines.append(line)
        cleaned = "\n".join(cleaned_lines).strip()

        try:
            parsed = json.loads(cleaned)
            if isinstance(parsed, dict):
                if isinstance(parsed.get("url"), str) and parsed["url"].strip():
                    return ""
                if isinstance(parsed.get("query"), str) and parsed["query"].strip():
                    return ""
        except Exception:
            pass

        if not re.search(r"[A-Za-z0-9]", cleaned):
            return ""
        return cleaned

    async def _consume_stream(
        self,
        response_stream,
        msg: InboundMessage,
        session_key: str,
        previous_content: str = "",
        turn_id: Optional[str] = None,
        message_id: Optional[str] = None,
    ):
        full_content: str = ""
        tool_calls: list = []
        usage = None
        is_potential_json: bool = False
        streamed_any: bool = False
        display_buffer: str = ""
        ghost_active: Optional[str] = None
        match_index = 0
        dedup_active = bool(previous_content)
        streamed_to_web: bool = False
        streamed_to_discord: bool = False
        last_flush = time.monotonic()
        flush_interval_s = 0.08
        flush_min_chars = 256
        last_discord_flush = time.monotonic()
        discord_flush_interval_s = 0.45
        discord_flush_min_chars = 48
        last_discord_content = ""
        thinking_buffer: str = ""
        extracted_from = "provider_tool_calls"
        chunk_index = 0

        try:
            async for chunk in response_stream:
                chunk_index += 1
                if hasattr(chunk, "usage") and chunk.usage:
                    usage = chunk.usage

                delta = chunk.choices[0].delta

                if hasattr(delta, "content") and delta.content:
                    content_chunk = delta.content
                    self._log_tool_debug(
                        "stream_chunk_content",
                        session_key=session_key,
                        chunk_index=chunk_index,
                        delta_content=content_chunk,
                    )

                    if full_content and content_chunk == full_content:
                        continue
                    if len(content_chunk) > 5 and full_content.startswith(
                        content_chunk
                    ):
                        continue
                    if full_content and content_chunk.startswith(full_content):
                        content_chunk = content_chunk[len(full_content) :]

                    to_stream = content_chunk
                    if dedup_active:
                        remaining_prev = previous_content[match_index:]
                        if not remaining_prev:
                            dedup_active = False
                        else:
                            common = 0
                            for i in range(
                                min(len(content_chunk), len(remaining_prev))
                            ):
                                if content_chunk[i] == remaining_prev[i]:
                                    common += 1
                                else:
                                    break
                            if common > 0:
                                match_index += common
                                to_stream = content_chunk[common:]
                                if common < len(content_chunk):
                                    dedup_active = False
                            else:
                                dedup_active = False

                    full_content += content_chunk

                    if not streamed_any:
                        stripped = full_content.strip()
                        if not stripped:
                            continue
                        is_potential_json = stripped.startswith(
                            "{"
                        ) or stripped.startswith("```")
                        streamed_any = True

                    if msg.channel == "web" and not is_potential_json and to_stream:
                        display_buffer += to_stream
                        display_buffer = self._trim_leading_structural_lines(
                            display_buffer
                        )
                        if not display_buffer.strip():
                            continue

                        now = time.monotonic()
                        should_flush = (
                            len(display_buffer) >= flush_min_chars
                            or (now - last_flush) >= flush_interval_s
                        )

                        while display_buffer and should_flush:
                            if not ghost_active:
                                tag_start = display_buffer.find("<")
                                if tag_start == -1:
                                    await self.bus.publish_outbound(
                                        OutboundMessage(
                                            channel=msg.channel,
                                            chat_id=msg.chat_id,
                                            content=display_buffer,
                                            metadata=self._with_trace_metadata(
                                                {"type": "chunk"},
                                                turn_id=turn_id,
                                                message_id=message_id,
                                            ),
                                        )
                                    )
                                    display_buffer = ""
                                    last_flush = time.monotonic()
                                    break

                                if tag_start > 0:
                                    await self.bus.publish_outbound(
                                        OutboundMessage(
                                            channel=msg.channel,
                                            chat_id=msg.chat_id,
                                            content=display_buffer[:tag_start],
                                            metadata=self._with_trace_metadata(
                                                {"type": "chunk"},
                                                turn_id=turn_id,
                                                message_id=message_id,
                                            ),
                                        )
                                    )
                                    streamed_to_web = True
                                    last_flush = time.monotonic()
                                    display_buffer = display_buffer[tag_start:]

                                tag_end = display_buffer.find(">")
                                if tag_end == -1:
                                    break

                                tag_content = display_buffer[: tag_end + 1]
                                found_ghost = None
                                if not tag_content.startswith("</"):
                                    for g in _GHOST_TAG_NAMES:
                                        if tag_content.startswith(f"<{g}"):
                                            found_ghost = g
                                            break

                                if found_ghost:
                                    ghost_active = found_ghost
                                    await self.bus.publish_outbound(
                                        OutboundMessage(
                                            channel=msg.channel,
                                            chat_id=msg.chat_id,
                                            content="",
                                            metadata={
                                                "type": "activity",
                                                "text": f"🧠 Processing {found_ghost}…",
                                            },
                                        )
                                    )
                                    display_buffer = display_buffer[tag_end + 1 :]
                                elif tag_content.startswith("</"):
                                    display_buffer = display_buffer[tag_end + 1 :]
                                else:
                                    await self.bus.publish_outbound(
                                        OutboundMessage(
                                            channel=msg.channel,
                                            chat_id=msg.chat_id,
                                            content=tag_content,
                                            metadata=self._with_trace_metadata(
                                                {"type": "chunk"},
                                                turn_id=turn_id,
                                                message_id=message_id,
                                            ),
                                        )
                                    )
                                    streamed_to_web = True
                                    display_buffer = display_buffer[tag_end + 1 :]
                            else:
                                closing = f"</{ghost_active}>"
                                close_idx = display_buffer.find(closing)
                                if close_idx == -1:
                                    display_buffer = ""
                                    break
                                ghost_active = None
                                await self.bus.publish_outbound(
                                    OutboundMessage(
                                        channel=msg.channel,
                                        chat_id=msg.chat_id,
                                        content="",
                                        metadata={"type": "activity", "text": ""},
                                    )
                                )
                                display_buffer = display_buffer[
                                    close_idx + len(closing) :
                                ]

                    if (
                        msg.channel == "discord"
                        and not is_potential_json
                        and full_content.strip()
                    ):
                        now = time.monotonic()
                        should_flush_discord = (
                            len(full_content) - len(last_discord_content)
                            >= discord_flush_min_chars
                            or (now - last_discord_flush) >= discord_flush_interval_s
                        )
                        if should_flush_discord and full_content != last_discord_content:
                            await self.bus.publish_outbound(
                                OutboundMessage(
                                    channel=msg.channel,
                                    chat_id=msg.chat_id,
                                    content=full_content,
                                    metadata=self._with_trace_metadata(
                                        {"type": "full_content"},
                                        turn_id=turn_id,
                                        message_id=message_id,
                                    ),
                                )
                            )
                            streamed_to_discord = True
                            last_discord_flush = now
                            last_discord_content = full_content

                thinking = getattr(delta, "reasoning_content", None) or getattr(
                    delta, "thinking", None
                )
                if thinking:
                    thinking_buffer += thinking
                    self._log_tool_debug(
                        "stream_chunk_thinking",
                        session_key=session_key,
                        chunk_index=chunk_index,
                        delta_thinking=thinking,
                    )
                if thinking and msg.channel == "web":
                    await self.bus.publish_outbound(
                        OutboundMessage(
                            channel=msg.channel,
                            chat_id=msg.chat_id,
                            content=thinking,
                            metadata=self._with_trace_metadata(
                                {"type": "thinking"},
                                turn_id=turn_id,
                                message_id=message_id,
                            ),
                        )
                    )

                if hasattr(delta, "tool_calls") and delta.tool_calls:
                    self._log_tool_debug(
                        "stream_chunk_tool_delta",
                        session_key=session_key,
                        chunk_index=chunk_index,
                        tool_delta=str(delta.tool_calls),
                    )
                    for tc_chunk in delta.tool_calls:
                        while len(tool_calls) <= tc_chunk.index:
                            tool_calls.append(
                                {
                                    "id": "",
                                    "type": "function",
                                    "function": {"name": "", "arguments": ""},
                                }
                            )
                        tc = tool_calls[tc_chunk.index]
                        if tc_chunk.id:
                            tc["id"] = tc_chunk.id
                        if tc_chunk.function.name:
                            tc["function"]["name"] += tc_chunk.function.name
                        if tc_chunk.function.arguments:
                            tc["function"]["arguments"] += tc_chunk.function.arguments

        except (RateLimitError, Exception) as e:
            if "429" in str(e) or "RateLimitError" in type(e).__name__:
                logger.warning(f"⚠ Rate limit during streaming: {e}")
                await self.bus.publish_outbound(
                    OutboundMessage(
                        channel=msg.channel,
                        chat_id=msg.chat_id,
                        content="",
                        metadata=self._with_trace_metadata(
                            {"type": "rate_limit_error", "details": str(e)},
                            turn_id=turn_id,
                            message_id=message_id,
                        ),
                    )
                )
            else:
                raise

        if usage:
            await self.session_manager.update_session(
                session_key=session_key,
                model=self.model,
                origin=msg.channel,
                usage=usage,
            )

        if display_buffer and msg.channel == "web":
            # ── Scrub any ghost / orphan tags that survived streaming ──
            if ghost_active:
                # Stream ended mid-ghost — drop everything up to (and
                # including) the closing tag, or the whole buffer if
                # the closing tag never arrived.
                closing = f"</{ghost_active}>"
                close_idx = display_buffer.find(closing)
                if close_idx != -1:
                    display_buffer = display_buffer[close_idx + len(closing) :]
                else:
                    display_buffer = ""
                ghost_active = None

            display_buffer = self._trim_leading_structural_lines(display_buffer)

            # Strip any leftover opening or closing ghost tags.
            display_buffer = _GHOST_TAG_RE.sub("", display_buffer).strip()

            if display_buffer:
                await self.bus.publish_outbound(
                    OutboundMessage(
                        channel=msg.channel,
                        chat_id=msg.chat_id,
                        content=display_buffer,
                        metadata=self._with_trace_metadata(
                            {"type": "chunk"},
                            turn_id=turn_id,
                            message_id=message_id,
                        ),
                    )
                )
                streamed_to_web = True
            display_buffer = ""

        if not tool_calls and full_content:
            extracted = self._extract_tool_from_content(full_content)
            if extracted:
                extracted_from = "assistant_content"
                tool_calls = extracted
                clean_content = re.sub(
                    r"```(?:json)?\s*\{.*?\}(?:\s*```)?",
                    "",
                    full_content,
                    flags=re.DOTALL,
                ).strip()
                # Also strip Kimi K2 inline tool syntax: functions.name:N{...}
                clean_content = re.sub(
                    r"(?::?functions\.\w+(?::\d+)?\s*\{[^}]*\})",
                    "",
                    clean_content,
                ).strip()
                clean_content = re.sub(
                    r"<(?:read_file|write_file|delete_file|list_dir|search_files|run_command|memory_search|google_search|browser_navigate|spawn_agent|save_memory|log_memory|ls|dir|list_files|cat|open_file|show_file|grep|rg|ripgrep|find_files|shell|terminal|exec|bash|powershell|cmd)>.*?</(?:read_file|write_file|delete_file|list_dir|search_files|run_command|memory_search|google_search|browser_navigate|spawn_agent|save_memory|log_memory|ls|dir|list_files|cat|open_file|show_file|grep|rg|ripgrep|find_files|shell|terminal|exec|bash|powershell|cmd)>",
                    "",
                    clean_content,
                    flags=re.DOTALL | re.IGNORECASE,
                ).strip()
                clean_content = re.sub(
                    r"<tool_code>.*?</tool_code>",
                    "",
                    clean_content,
                    flags=re.DOTALL | re.IGNORECASE,
                ).strip()
                if clean_content:
                    full_content = clean_content
                logger.info(f"✨ Extracted tool: {tool_calls[0]['function']['name']}")

        if not tool_calls and thinking_buffer:
            extracted = self._extract_tool_from_content(thinking_buffer)
            if extracted:
                extracted_from = "reasoning_content"
                tool_calls = extracted
                logger.info(
                    f"✨ Extracted tool from reasoning: {tool_calls[0]['function']['name']}"
                )

        valid_tcs = []
        for tc in tool_calls:
            if not tc.get("id"):
                tc["id"] = f"call_{uuid.uuid4().hex[:8]}"
            if not tc.get("type"):
                tc["type"] = "function"

            fn = tc.get("function", {})
            if fn.get("name"):
                # Fix common LLM hallucination: double JSON objects in arguments
                args = fn.get("arguments", "")
                if args.startswith("{") and "}{" in args:
                    try:
                        idx = args.find("}{")
                        json.loads(args[: idx + 1])
                        fn["arguments"] = args[: idx + 1]
                    except Exception:
                        pass
                valid_tcs.append(tc)
        tool_calls = valid_tcs

        if tool_calls and full_content:
            pre_sanitize = full_content
            full_content = self._sanitize_tool_call_content(full_content)
            if pre_sanitize.strip() and pre_sanitize != full_content:
                self.metrics.record_anomaly(
                    session_key,
                    "tool_call_residue_stripped",
                    detail=pre_sanitize[:160],
                )
                self._log_tool_debug(
                    "tool_residue_stripped",
                    session_key=session_key,
                    before=pre_sanitize,
                    after=full_content,
                )
                if msg.channel == "web" and streamed_to_web:
                    await self.bus.publish_outbound(
                        OutboundMessage(
                            channel=msg.channel,
                            chat_id=msg.chat_id,
                            content=full_content,
                            metadata=self._with_trace_metadata(
                                {"type": "full_content"},
                                turn_id=turn_id,
                                message_id=message_id,
                            ),
                        )
                    )
                elif msg.channel == "discord" and streamed_to_discord:
                    await self.bus.publish_outbound(
                        OutboundMessage(
                            channel=msg.channel,
                            chat_id=msg.chat_id,
                            content=full_content,
                            metadata=self._with_trace_metadata(
                                {"type": "full_content"},
                                turn_id=turn_id,
                                message_id=message_id,
                            ),
                        )
                    )

        if (
            is_potential_json
            and not tool_calls
            and full_content.strip()
            and msg.channel == "web"
            and not streamed_to_web
        ):
            await self.bus.publish_outbound(
                OutboundMessage(
                    channel=msg.channel,
                    chat_id=msg.chat_id,
                    content=full_content,
                    metadata=self._with_trace_metadata(
                        {"type": "full_content"},
                        turn_id=turn_id,
                        message_id=message_id,
                    ),
                )
            )

        self._log_tool_debug(
            "stream_summary",
            session_key=session_key,
            raw_content=full_content,
            thinking=thinking_buffer,
            tool_calls=self._tool_call_debug_rows(tool_calls),
            extracted_from=extracted_from if tool_calls else "none",
            streamed_to_web=streamed_to_web,
            streamed_to_discord=streamed_to_discord,
            usage=str(usage) if usage else "",
        )

        return full_content, tool_calls, usage, streamed_to_web

    async def _process_message(self, msg: InboundMessage) -> None:
        session_key = msg.session_key
        turn_id = f"turn_{uuid.uuid4().hex[:12]}"
        assistant_message_id = f"msg_{uuid.uuid4().hex[:12]}"
        try:
            content = msg.content or ""
            attachments = [
                attachment
                for attachment in (msg.metadata.get("attachments") or [])
                if isinstance(attachment, dict)
            ]
            self._log_session_event(
                session_key,
                {
                    "type": "inbound_message",
                    "turn_id": turn_id,
                    "channel": msg.channel,
                    "chat_id": msg.chat_id,
                    "sender_id": msg.sender_id,
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
                    vector_service=self.vector_service,
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
                return

            if (
                not msg.metadata.get("mentioned")
                and not msg.metadata.get("is_dm")
                and msg.channel == "discord"
            ):
                return

            sender_id = msg.sender_id
            normalized = content.strip().lower()
            is_stop_request = normalized in _DENY_WORDS or any(
                normalized.startswith(k + " ") for k in _DENY_WORDS
            )

            msg_hash = hash(msg.content) if msg.content else None
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
                        return

                pending_for_session = [
                    (cid, c)
                    for cid, c in self.pending_confirmations.items()
                    if c["session_key"] == session_key
                ]
                if pending_for_session:
                    is_approve = normalized in _APPROVE_WORDS or any(
                        normalized.startswith(k + " ") for k in _APPROVE_WORDS
                    )
                    is_deny = normalized in _DENY_WORDS or any(
                        normalized.startswith(k + " ") for k in _DENY_WORDS
                    )
                    if is_approve or is_deny:
                        for conf_id, _ in pending_for_session:
                            await self.confirm_tool(conf_id, approved=is_approve)
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
                    # ── Parallel: Auto-RAG + history load ───────────────────

                    await self._publish_activity(
                        msg,
                        "Recalling memory and loading history...",
                        turn_id=turn_id,
                        message_id=assistant_message_id,
                    )

                    _RAG_TIMEOUT = 0.2

                    # Fast-path: skip RAG for short/casual messages.
                    async def _do_rag() -> Dict[str, Any]:
                        trace: Dict[str, Any] = {
                            "ts": time.time(),
                            "query": content,
                            "status": "skipped",
                            "mode": "none",
                            "results": [],
                            "recalled_context": "",
                        }
                        if not (
                            content
                            and len(content) > 10
                            and not content.startswith(("/", "@"))
                        ):
                            return trace
                        # Skip RAG for single casual words
                        if content.strip().lower() in _CASUAL_WORDS:
                            return trace
                        try:
                            results: list = []
                            resolved_key = self.vector_service._resolve_api_key(
                                self.config
                            )
                            provider = self.vector_service._get_provider()
                            if provider in ("ollama", "local") or resolved_key:
                                try:
                                    semantic_results = (
                                        await self.vector_service.search(
                                            content, limit=3
                                        )
                                        or []
                                    )
                                    results = [
                                        r
                                        for r in semantic_results
                                        if r.get("score", 1.0) >= AUTORAG_MIN_SCORE
                                    ]
                                    if results:
                                        trace["mode"] = "vector"
                                except Exception as e:
                                    logger.warning(f"Semantic search failed: {e}")
                            if not results:
                                results = (
                                    await self.vector_service.search_grep(
                                        content, limit=3
                                    )
                                    or []
                                )
                                if results:
                                    trace["mode"] = "grep_fallback"
                            if results:
                                seen, lines = set(), []
                                for r in results:
                                    text = r["text"].strip()
                                    if text not in seen:
                                        seen.add(text)
                                        lines.append(
                                            f"- {text} (Date: {r.get('timestamp', 'unknown')})"
                                        )
                                if lines:
                                    trace["status"] = "recalled"
                                    trace["results"] = [
                                        self._build_rag_result_trace(r) for r in results
                                    ]
                                    trace["recalled_context"] = "\n".join(lines)
                                    logger.info(
                                        f"Auto-RAG: {len(lines)} memories recalled."
                                    )
                                    return trace
                            trace["status"] = "miss"
                        except Exception as e:
                            trace["status"] = "error"
                            trace["error"] = str(e)
                            logger.warning(f"Auto-RAG failed: {e}")
                        return trace

                    async def _do_history_load():
                        if session_key not in self.history:
                            return await self.session_manager.load_history(session_key)
                        return None

                    _rag_needed = (
                        bool(content)
                        and len(content) > 10
                        and not content.startswith(("/", "@"))
                        and content.strip().lower() not in _CASUAL_WORDS
                    )

                    if _rag_needed:
                        rag_coro = asyncio.wait_for(_do_rag(), timeout=_RAG_TIMEOUT)
                        rag_task = asyncio.create_task(rag_coro)
                    else:
                        rag_task = None

                    hist_task = asyncio.create_task(_do_history_load())

                    if rag_task is not None:
                        try:
                            rag_result = await rag_task
                            recalled_context = rag_result.get("recalled_context", "")
                            self._record_rag_trace(session_key, rag_result)
                        except asyncio.TimeoutError:
                            recalled_context = ""
                            self._record_rag_trace(
                                session_key,
                                {
                                    "ts": time.time(),
                                    "query": content,
                                    "status": "timeout",
                                    "mode": "timeout",
                                    "results": [],
                                    "recalled_context": "",
                                },
                            )
                            logger.debug("Auto-RAG timeout - skipping.")
                        except Exception as e:
                            recalled_context = ""
                            self._record_rag_trace(
                                session_key,
                                {
                                    "ts": time.time(),
                                    "query": content,
                                    "status": "error",
                                    "mode": "error",
                                    "results": [],
                                    "recalled_context": "",
                                    "error": str(e),
                                },
                            )
                            logger.warning(f"Auto-RAG error: {e}")
                    else:
                        recalled_context = ""
                        self._record_rag_trace(
                            session_key,
                            {
                                "ts": time.time(),
                                "query": content,
                                "status": "skipped",
                                "mode": "none",
                                "results": [],
                                "recalled_context": "",
                            },
                        )
                        logger.debug("Auto-RAG skipped (short/casual message).")

                    persisted = await hist_task

                    sender_name = msg.metadata.get("sender_name", "")
                    system_prompt = await self._build_full_system_prompt(
                        sender_id,
                        channel=msg.channel,
                        chat_id=msg.chat_id,
                        recalled_context=recalled_context,
                        sender_name=sender_name,
                        current_message=content,
                    )

                    if session_key not in self.history:
                        if persisted:
                            self.history[session_key] = persisted
                            logger.info(
                                f"♻ Restored {session_key} ({len(persisted)} msgs)"
                            )
                        else:
                            self.history[session_key] = [
                                {"role": "system", "content": system_prompt}
                            ]

                    self.history[session_key][0] = {
                        "role": "system",
                        "content": system_prompt,
                    }

                    image_data = msg.metadata.get("image")
                    user_text_payload = user_text_content or content or " "
                    if image_data and self._image_inputs_disabled_for_session(
                        session_key
                    ):
                        user_msg_content = self._render_text_only_message_content(
                            [
                                {"type": "text", "text": user_text_payload},
                                {
                                    "type": "image_url",
                                    "image_url": {"url": image_data},
                                },
                            ]
                        )
                    else:
                        user_msg_content = (
                            [
                                {"type": "text", "text": user_text_payload},
                                {
                                    "type": "image_url",
                                    "image_url": {"url": image_data},
                                },
                            ]
                            if image_data
                            else user_text_payload
                        )
                    self.history[session_key].append(
                        {"role": "user", "content": user_msg_content}
                    )
                    self._mark_dirty(session_key)

                    asyncio.create_task(
                        self.session_manager.append_chat_log(
                            session_key,
                            {
                                "role": "user",
                                "content": msg.content or "",
                                "image": bool(image_data),
                                "attachments": [
                                    str(attachment.get("name") or "attachment")
                                    for attachment in attachments
                                ],
                            },
                        )
                    )

                    injected = [
                        "SOUL.md",
                        "IDENTITY.md",
                        f"memory/{datetime.now().strftime('%Y-%m-%d')}.md",
                    ]
                    if (USERS_DIR / f"{sender_id}.md").exists():
                        injected.append(f"users/{sender_id}.md")
                    for attachment in attachments:
                        path = str(attachment.get("path") or "").strip()
                        if path:
                            injected.append(path)

                    asyncio.create_task(
                        self.session_manager.update_session(
                            session_key=session_key,
                            model=self.model,
                            origin=msg.channel,
                            injected_files=injected,
                        )
                    )

                    selected_subagent = self.subagent_registry.get_default_selection()
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
                            vector_service=self.vector_service,
                            bus=self.bus,
                            msg=msg,
                            config=self.config,
                        )
                        reply_to_user = tag_result.clean_reply or raw_reply
                        self.history[session_key].append(
                            {"role": "assistant", "content": raw_reply}
                        )
                        self._mark_dirty(session_key)
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
                                    {"reply_to": msg.sender_id},
                                    turn_id=turn_id,
                                    message_id=assistant_message_id,
                                ),
                            )
                        )
                        return

                    await self._trim_history(session_key)
                    asyncio.create_task(self._flush_history(session_key))

                    include_tools = self._should_include_tools(content)
                    self._log_tool_debug(
                        "tool_gate_decision",
                        session_key=session_key,
                        include_tools=include_tools,
                        content=content,
                    )
                    stream = await self._llm_call_with_retry(
                        messages=self.history[session_key],
                        session_key=session_key,
                        msg=msg,
                        stream=True,
                        include_tools=include_tools,
                        tool_context_text=content,
                        turn_id=turn_id,
                        message_id=assistant_message_id,
                    )
                    consume_result = await self._consume_stream(
                        stream,
                        msg,
                        session_key,
                        turn_id=turn_id,
                        message_id=assistant_message_id,
                    )
                    full_content, tool_calls, _, streamed_to_web = (
                        self._unpack_stream_result(consume_result)
                    )
                    accumulated_content = full_content
                    iterations_limit_reached = False
                    web_streamed_reply = bool(streamed_to_web)
                    force_direct_reply = False
                    any_tool_calls_in_turn = bool(tool_calls)
                    fallback_inserted = False

                    if full_content or tool_calls:
                        am: Dict = {"role": "assistant", "content": full_content or ""}
                        if tool_calls:
                            am["tool_calls"] = tool_calls
                        self.history[session_key].append(am)
                        self._mark_dirty(session_key)

                    raw_reply = accumulated_content or ""

                    if tool_calls:
                        await self._publish_activity(
                            msg,
                            "Planning tool calls...",
                            turn_id=turn_id,
                            message_id=assistant_message_id,
                        )
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
                        is_blocked = await self._execute_tool_batch(
                            tool_calls,
                            session_key,
                            msg,
                            turn_id=turn_id,
                            message_id=assistant_message_id,
                        )

                        if not is_blocked:
                            max_iterations = getattr(self.config, "max_iterations", 30)
                            iteration = 0

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
                                    raw_reply = (
                                        "⚠️ **Action Limit Reached**\n"
                                        f"I've hit my step limit ({max_iterations}). Progress saved — how to proceed?"
                                    )
                                    self.history[session_key].append(
                                        {
                                            "role": "assistant",
                                            "content": raw_reply,
                                        }
                                    )
                                    self._mark_dirty(session_key)
                                    break

                                nxt_stream = await self._llm_call_with_retry(
                                    messages=self.history[session_key],
                                    session_key=session_key,
                                    msg=msg,
                                    stream=True,
                                    include_tools=True,
                                    tool_context_text=content,
                                    turn_id=turn_id,
                                    message_id=assistant_message_id,
                                )
                                nxt_consume_result = await self._consume_stream(
                                    nxt_stream,
                                    msg,
                                    session_key,
                                    accumulated_content,
                                    turn_id=turn_id,
                                    message_id=assistant_message_id,
                                )
                                nxt_content, nxt_tool_calls, _, nxt_streamed_to_web = (
                                    self._unpack_stream_result(nxt_consume_result)
                                )
                                web_streamed_reply = web_streamed_reply or bool(
                                    nxt_streamed_to_web
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
                                    self.history[session_key].append(
                                        {
                                            "role": "assistant",
                                            "content": nxt_content,
                                        }
                                    )
                                    self._mark_dirty(session_key)
                                    break

                                nxt_am: Dict = {
                                    "role": "assistant",
                                    "content": nxt_content or "",
                                }
                                nxt_am["tool_calls"] = nxt_tool_calls
                                self.history[session_key].append(nxt_am)
                                self._mark_dirty(session_key)

                                nxt_blocked = await self._execute_tool_batch(
                                    nxt_tool_calls,
                                    session_key,
                                    msg,
                                    turn_id=turn_id,
                                    message_id=assistant_message_id,
                                )

                                if iteration % _INTERIM_SAVE_EVERY == 0:
                                    await self._flush_history(session_key)

                                if nxt_blocked:
                                    logger.info("🛑 Tool blocked — stopping loop.")
                                    raw_reply = accumulated_content
                                    break
                        else:
                            logger.info("🛑 Tool blocked — skipping tool loop.")
                    else:
                        raw_reply = full_content

                    if tool_calls and not str(raw_reply or "").strip():
                        self._log_tool_debug(
                            "tool_fallback_inserted",
                            session_key=session_key,
                            stage="post_initial_tool_batch",
                        )
                        raw_reply = self._build_tool_fallback_reply(session_key)
                        force_direct_reply = True
                        fallback_inserted = True
                        self.history[session_key].append(
                            {"role": "assistant", "content": raw_reply}
                        )
                        self._mark_dirty(session_key)

                    tag_result = await process_tags(
                        raw_reply=raw_reply,
                        sender_id=sender_id,
                        validate_soul=prompt_module.validate_and_save_soul,
                        validate_identity=prompt_module.validate_and_save_identity,
                        validate_mood=prompt_module.validate_and_save_mood,
                        validate_relationship=prompt_module.validate_and_save_relationships,
                        vector_service=self.vector_service,
                        bus=self.bus,
                        msg=msg,
                        config=self.config,
                    )
                    reply_to_user = tag_result.clean_reply
                    soul_updated = tag_result.soul_updated
                    identity_updated = tag_result.identity_updated

                    if any_tool_calls_in_turn and not str(reply_to_user or "").strip():
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
                            self.history[session_key].append(
                                {"role": "assistant", "content": fallback_reply}
                            )
                            self._mark_dirty(session_key)
                            fallback_inserted = True
                    elif msg and not str(reply_to_user or "").strip():
                        logger.warning(
                            "Empty user-visible reply after tag processing; sending fallback."
                        )
                        fallback_reply = self._build_empty_reply_fallback()
                        reply_to_user = fallback_reply
                        raw_reply = fallback_reply
                        force_direct_reply = True

                    # ── Self-repetition dedup ────────────────────────────
                    # Some models (e.g. Kimi K2) repeat their entire
                    # response within a single generation.  Detect and
                    # strip the duplicate half by comparing paragraphs.
                    if reply_to_user and len(reply_to_user) > 80:
                        _paras = [
                            p.strip()
                            for p in re.split(r"\n\s*\n", reply_to_user)
                            if p.strip()
                        ]
                        _n = len(_paras)
                        if _n >= 4 and _n % 2 == 0:
                            if _paras[: _n // 2] == _paras[_n // 2 :]:
                                logger.info(
                                    "✂ Self-repetition detected — trimming duplicate."
                                )
                                reply_to_user = "\n\n".join(_paras[: _n // 2])

                    if soul_updated or identity_updated:
                        self._invalidate_stable_prompt(sender_id)
                        logger.info("🔄 Stable prompt cache invalidated.")

                    if not tool_calls:
                        self.history[session_key].append(
                            {"role": "assistant", "content": raw_reply}
                        )
                        self._mark_dirty(session_key)

                    asyncio.create_task(
                        self.session_manager.append_chat_log(
                            session_key, {"role": "assistant", "content": raw_reply}
                        )
                    )

                    if reply_to_user:
                        meta: Dict = {"reply_to": msg.sender_id}
                        if identity_updated:
                            meta["identity_updated"] = True
                        if iterations_limit_reached:
                            meta["is_warning"] = True
                        meta = self._with_trace_metadata(
                            meta,
                            turn_id=turn_id,
                            message_id=assistant_message_id,
                        )

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
                            outbound = OutboundMessage(
                                channel=msg.channel,
                                chat_id=msg.chat_id,
                                content=reply_to_user,
                                metadata=meta,
                            )
                        await self.bus.publish_outbound(outbound)
                        self._log_session_event(
                            session_key,
                            {
                                "type": "outbound_message",
                                "turn_id": turn_id,
                                "channel": outbound.channel,
                                "chat_id": outbound.chat_id,
                                "content_preview": (outbound.content or "")[:500],
                                "metadata_type": outbound.metadata.get("type"),
                            },
                        )

                except asyncio.CancelledError:
                    logger.warning(f"⚠ Task cancelled for {session_key}")
                    self._log_session_event(
                        session_key,
                        {"type": "turn_cancelled", "turn_id": turn_id},
                    )
                    import contextlib

                    with contextlib.suppress(Exception):
                        await self.bus.publish_outbound(
                            OutboundMessage(
                                channel=msg.channel if msg else "web",
                                chat_id=msg.chat_id if msg else "system",
                                content="",
                                metadata=self._with_trace_metadata(
                                    {
                                        "type": "cancellation",
                                        "is_cancellation": True,
                                    },
                                    turn_id=turn_id,
                                    message_id=assistant_message_id,
                                ),
                            )
                        )
                    raise
                except Exception as e:
                    import traceback

                    traceback.print_exc()
                    logger.exception(f"❌ Error processing message: {e}")
                    self._log_session_event(
                        session_key,
                        {
                            "type": "turn_error",
                            "turn_id": turn_id,
                            "error": str(e)[:500],
                        },
                    )
                    await self.bus.publish_outbound(
                        OutboundMessage(
                            channel=msg.channel,
                            chat_id=msg.chat_id,
                            content=f"🚫 **Internal error.**\n`{e}`",
                            metadata=self._with_trace_metadata(
                                {"is_error": True, "reply_to": msg.sender_id},
                                turn_id=turn_id,
                                message_id=assistant_message_id,
                            ),
                        )
                    )

        finally:
            await self._flush_history(session_key, force=True)
            self.active_tasks.pop(session_key, None)
            # Always notify frontend that processing is done
            try:
                if msg:
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
            except Exception:
                pass
