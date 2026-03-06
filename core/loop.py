import asyncio
import json
import re
import time
import uuid
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Set, Tuple

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
from core.tag_parser import process_tags
from core.tools import Toolbox
from core.vectors import get_vector_service


TOOL_BROADCAST_MAX_CHARS = 500


_TOOL_RESULT_LIMITS: Dict[str, int] = {
    "read_file": 8_000,
    "search_files": 5_000,
    "memory_search": 3_000,
    "browser_extract": 5_000,
    "browser_get_page_text": 5_000,
    "browser_snapshot": 3_000,
    "google_search": 2_000,
    "run_command": 2_000,
    "browser_list_media": 1_000,
    "list_dir": 500,
}
_DEFAULT_TOOL_RESULT_LIMIT = 2_000


AUTORAG_MIN_SCORE = 0.65


_INTERIM_SAVE_EVERY = 5


_BROWSER_CACHEABLE = frozenset(
    {
        "google_search",
        "browser_extract",
        "browser_extract_large",
        "browser_snapshot",
        "browser_list_media",
        "browser_get_page_text",
    }
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

_APPROVE_WORDS = frozenset(
    {"proceed", "yes", "approve", "confirm", "ok", "sure", "y", "go", "run", "do it"}
)

_DENY_WORDS = frozenset(
    {"no", "cancel", "deny", "stop", "reject", "n", "abort", "nope"}
)

_SENSITIVE_TOOLS = frozenset(
    {"delete_file", "run_command", "write_file", "cron_remove"}
)

_TOOL_INTENT_RE = re.compile(
    r"\b("
    r"read_file|write_file|delete_file|list_dir|search_files|run_command|memory_search|"
    r"cron_add|cron_list|cron_remove|spawn_agent|"
    r"browser_navigate|browser_click|browser_type|browser_snapshot|browser_scroll|"
    r"browser_wait|browser_press_key|browser_go_back|browser_tabs|browser_switch_tab|"
    r"browser_extract|browser_get_page_text|browser_list_media|google_search|"
    r"ls|pwd|cat|grep|find|mkdir|rm|cp|mv|npm|pip|python|bash|powershell|terminal|"
    r"command|directory|folder|path|cron|schedule|skill"
    r")\b",
    re.IGNORECASE,
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

        self._tool_definitions: Optional[List[Dict]] = None
        self._warmed = False
        asyncio.create_task(self._init_skills_and_tools())

        self._stable_prompt_cache: Dict[str, Tuple[str, float]] = {}
        self._STABLE_PROMPT_TTL = 30.0
        self._history_flush_interval = 5.0
        self._last_history_flush: Dict[str, float] = {}
        self._filesystem_alias_actions = {
            "list": "list_dir",
            "read": "read_file",
            "write": "write_file",
            "delete": "delete_file",
            "find": "search_files",
            "search": "search_files",
        }

    async def _init_skills_and_tools(self) -> None:
        """Background: discover skills, build tool definitions, then warm up slow services."""
        await asyncio.to_thread(self.skill_registry.discover_and_load)
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

    def set_model(self, model: str) -> None:
        """Switch the active model and refresh cached provider config."""
        self.model = model
        self._provider = self._resolve_provider()
        self._stable_prompt_cache.clear()

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
        volatile = prompt_module.get_volatile_prompt_suffix(recalled_context)
        return stable + (skills_docs + "\n" if skills_docs else "") + volatile

    async def _cleanup_persisted_histories(self) -> None:
        """Best-effort cleanup of malformed assistant residue in persisted sessions."""
        try:
            summary = await asyncio.to_thread(
                self.session_manager.cleanup_history_artifacts
            )
        except Exception as e:
            logger.debug(f"History cleanup skipped: {e}")
            return

        cleaned_total = (
            summary.get("history_entries", 0) + summary.get("log_entries", 0)
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
        normalized_name = function_name
        normalized_args = dict(function_args or {})

        if function_name != "filesystem":
            return normalized_name, normalized_args

        action = str(normalized_args.pop("action", "") or "").strip().lower()
        if not action:
            self.metrics.record_anomaly(
                session_key, "filesystem_alias_missing_action", detail=str(function_args)
            )
            return function_name, function_args

        mapped_name = self._filesystem_alias_actions.get(action)
        if not mapped_name:
            self.metrics.record_anomaly(
                session_key,
                "filesystem_alias_unsupported",
                detail=f"action={action}",
            )
            return function_name, function_args

        self.metrics.record_anomaly(
            session_key,
            "filesystem_alias_normalized",
            detail=f"{action}->{mapped_name}",
        )

        if mapped_name == "list_dir":
            normalized_args = {
                "path": normalized_args.get("path", "."),
                **{
                    k: v
                    for k, v in normalized_args.items()
                    if k
                    in {
                        "limit",
                        "offset",
                        "include_hidden",
                        "sort_by",
                        "descending",
                        "folders_first",
                    }
                },
            }
        elif mapped_name == "read_file":
            normalized_args = {
                "path": normalized_args.get("path", ""),
                **{
                    k: v
                    for k, v in normalized_args.items()
                    if k in {"max_chars", "start_line", "end_line"}
                },
            }
        elif mapped_name == "write_file":
            normalized_args = {
                "path": normalized_args.get("path", ""),
                "content": normalized_args.get("content", ""),
            }
        elif mapped_name == "delete_file":
            normalized_args = {"path": normalized_args.get("path", "")}
        elif mapped_name == "search_files":
            normalized_args = {
                "query": normalized_args.get("query")
                or normalized_args.get("pattern")
                or "",
                "path": normalized_args.get("path", "."),
                "mode": normalized_args.get("mode", "content"),
                **{
                    k: v
                    for k, v in normalized_args.items()
                    if k in {"file_glob", "case_sensitive", "max_results"}
                },
            }

        return mapped_name, normalized_args

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

    @staticmethod
    def _build_confirmation_embed(
        function_name: str, function_args: dict, session_key: str
    ) -> list:
        """Build the embed fields list for a tool confirmation prompt."""
        if function_name == "run_command":
            return [
                {
                    "name": "Command",
                    "value": f"```bash\n{function_args.get('command', '')}\n```",
                    "inline": False,
                },
                {
                    "name": "Working Directory",
                    "value": f"`{function_args.get('cwd', 'default')}`",
                    "inline": True,
                },
                {"name": "Agent", "value": f"`{session_key}`", "inline": True},
            ]
        if function_name == "delete_file":
            return [
                {
                    "name": "Target File",
                    "value": f"`{function_args.get('path', '')}`",
                    "inline": False,
                },
                {"name": "Agent", "value": f"`{session_key}`", "inline": True},
            ]
        if function_name == "write_file":
            raw = function_args.get("content", "")
            preview = (raw[:100] + "...") if len(raw) > 100 else raw
            return [
                {
                    "name": "Target File",
                    "value": f"`{function_args.get('path', '')}`",
                    "inline": False,
                },
                {
                    "name": "Content Preview",
                    "value": f"```\n{preview}\n```",
                    "inline": False,
                },
                {"name": "Agent", "value": f"`{session_key}`", "inline": True},
            ]
        # Generic fallback
        return [
            {
                "name": "Arguments",
                "value": f"```json\n{json.dumps(function_args, indent=2)[:500]}\n```",
                "inline": False,
            },
            {"name": "Agent", "value": f"`{session_key}`", "inline": True},
        ]

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
        if session_key in self.active_tasks:
            task = self.active_tasks[session_key]
            if not task.done():
                task.cancel()
                logger.info(f"🛑 Cancelled task for {session_key}")
                if task.done():
                    del self.active_tasks[session_key]
                return True
        for sk, t in list(self.active_tasks.items()):
            if t.done():
                del self.active_tasks[sk]
        return False

    async def confirm_tool(
        self, conf_id: str, approved: bool, session_whitelist: bool = False
    ) -> bool:
        if conf_id in self.pending_confirmations:
            conf = self.pending_confirmations[conf_id]
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
        self, parent_session_key: str, sub_session_key: str, task: str
    ) -> str:
        try:
            logger.info(f"[SUB-AGENT] {sub_session_key} ← {parent_session_key}: {task}")

            await self.session_manager.update_session(
                session_key=sub_session_key,
                model=self.model,
                origin=f"subagent:{parent_session_key}",
                parent_id=parent_session_key,
                task=task,
            )

            try:
                soul = SOUL_FILE.read_text(encoding="utf-8")
                identity = IDENTITY_FILE.read_text(encoding="utf-8")
            except Exception:
                soul = "You are a helpful assistant."
                identity = "Name: LimeBot Sub-Agent"

            sub_system = (
                f"{soul}\n\n{identity}\n\n"
                "--- SUB-AGENT INSTRUCTIONS ---\n"
                f"You are a sub-agent spawned to complete: '{task}'.\n"
                "Use all available tools. Deliver a clear result when done.\n"
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

            while iteration < 10:
                iteration += 1
                logger.info(f"[SUB-AGENT:{sub_session_key}] iteration {iteration}")

                response = await self._llm_call_with_retry(
                    messages=sub_history,
                    session_key=sub_session_key,
                    msg=None,
                    stream=False,
                )

                if hasattr(response, "usage"):
                    await self.session_manager.update_session(
                        session_key=sub_session_key,
                        model=self.model,
                        origin=f"subagent:{parent_session_key}",
                        usage=response.usage,
                    )

                assistant_msg = response.choices[0].message
                full_content = assistant_msg.content or ""
                tool_calls_raw = assistant_msg.tool_calls

                sub_history.append(assistant_msg.model_dump())
                self.session_manager.append_chat_log(
                    sub_session_key, {"role": "assistant", "content": full_content}
                )

                if not tool_calls_raw:
                    final_result = full_content
                    break

                for tc in tool_calls_raw:
                    tc_id = tc.id
                    fn = tc.function.name
                    try:
                        args = json.loads(tc.function.arguments)
                        logger.info(f"[SUB-AGENT:{sub_session_key}] {fn}({args})")
                        result = await self._execute_tool(fn, args, sub_session_key)
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

            report = (
                f"--- SUB-AGENT REPORT ({sub_session_key}) ---\n"
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
        Return True when the user message likely needs tool/function calling.
        Keeps casual conversation turns lightweight by skipping tool schemas.
        """
        text = (content or "").strip()
        if not text:
            return False

        lowered = text.lower()
        if text.startswith(("/", "@")):
            return True
        if _TOOL_INTENT_RE.search(text):
            return True
        if any(token in lowered for token in ("./", "../", ".py", ".ts", ".md", ".json")):
            return True
        if any(token in text for token in ("\\", "/", "$", ">>", "->")) and len(text) > 12:
            return True
        return False

    async def _llm_call_with_retry(
        self,
        messages: List[Dict],
        session_key: str,
        msg: Optional[InboundMessage],
        max_retries: int = 3,
        stream: bool = False,
        include_tools: bool = True,
        turn_id: Optional[str] = None,
        message_id: Optional[str] = None,
    ) -> Any:

        model, base_url, api_key, custom_llm_provider = self._provider
        tools = self._get_tool_definitions() if include_tools else []
        qwen_auth_failover_attempted: Set[str] = set()

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
                # DashScope keys can be region-scoped. If auth fails on one endpoint,
                # try other official compatible endpoints before failing.
                current_base = (base_url or "").lower()
                is_qwen = (self.model or "").startswith(
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
                is_500 = isinstance(e, InternalServerError)
                is_conn = isinstance(e, (APIConnectionError, ServiceUnavailableError))
                wait_time = (2**attempt) * 5
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
        self, function_name: str, args: Dict[str, Any]
    ) -> Any:
        try:
            browser = await get_browser_manager()
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
            logger.exception(f"Browser tool execution failed: {function_name} args={args}")
            return f"Error executing browser tool: {e}"

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
                result = await self._execute_browser_tool(function_name, function_args)
            elif function_name.startswith("mcp_"):
                from core.mcp_client import get_mcp_manager

                result = await get_mcp_manager().execute_tool(
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

            if function_name in {
                "write_file",
                "delete_file",
                "create_skill",
                "run_command",
            } and result and not str(result).startswith("Error:"):
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

        return "Tool execution finished, but I did not generate a follow-up message. Ask me to summarize the result and I will continue."

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

                raw_args = tool_call["function"]["arguments"]
                if raw_args == "{}{}":
                    logger.warning(
                        f"⚠ Malformed args for {function_name} — fixing to {{}}"
                    )
                    self.metrics.record_anomaly(
                        session_key,
                        "malformed_tool_args",
                        detail=f"{function_name}:double_object",
                    )
                    raw_args = "{}"
                    tool_call["function"]["arguments"] = raw_args

                try:
                    function_args = json.loads(raw_args)
                except json.JSONDecodeError:
                    function_args = {}
                    logger.error(
                        f"⚠ Invalid JSON args for '{function_name}'. Using {{}}."
                    )
                    self.metrics.record_anomaly(
                        session_key,
                        "invalid_tool_args_json",
                        detail=f"{function_name}:{raw_args[:120]}",
                    )

                function_name, function_args = self._normalize_tool_alias(
                    function_name, function_args, session_key
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
                        self.pending_confirmations[conf_id] = {
                            "event": event,
                            "approved": False,
                            "session_key": session_key,
                            "tool": function_name,
                        }

                        embed_fields = self._build_confirmation_embed(
                            function_name, function_args, session_key
                        )

                        tool_content = (
                            f"🛠️ Executing {function_name}..." if is_whatsapp else "⏳"
                        )
                        conf_meta = {
                            "type": "tool_execution",
                            "status": "waiting_confirmation",
                            "tool": function_name,
                            "args": function_args,
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
                lower = json_str.lower()
                if '"name"' in lower and (
                    '"arguments"' in lower or '"parameters"' in lower
                ):
                    parsed = json.loads(json_str)
                    if isinstance(parsed, dict) and "name" in parsed:
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
            r"(?::?functions\.\w+(?::\d+)?\s*\{[^}]*\})",
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
        last_flush = time.monotonic()
        flush_interval_s = 0.08
        flush_min_chars = 256

        try:
            async for chunk in response_stream:
                if hasattr(chunk, "usage") and chunk.usage:
                    usage = chunk.usage

                delta = chunk.choices[0].delta

                if hasattr(delta, "content") and delta.content:
                    content_chunk = delta.content

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

                thinking = getattr(delta, "reasoning_content", None) or getattr(
                    delta, "thinking", None
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
                if clean_content:
                    full_content = clean_content
                logger.info(f"✨ Extracted tool: {tool_calls[0]['function']['name']}")

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

        return full_content, tool_calls, usage, streamed_to_web

    async def _process_message(self, msg: InboundMessage) -> None:
        session_key = msg.session_key
        turn_id = f"turn_{uuid.uuid4().hex[:12]}"
        assistant_message_id = f"msg_{uuid.uuid4().hex[:12]}"
        try:
            content = msg.content or ""
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

            msg_hash = hash(msg.content) if msg.content else None
            if msg_hash is not None:
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
                pending_for_session = [
                    (cid, c)
                    for cid, c in self.pending_confirmations.items()
                    if c["session_key"] == session_key
                ]
                if pending_for_session:
                    normalized = content.strip().lower()
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

                    _RAG_TIMEOUT = 0.2

                    # Fast-path: skip RAG for short/casual messages.
                    async def _do_rag() -> str:
                        if not (
                            content
                            and len(content) > 10
                            and not content.startswith(("/", "@"))
                        ):
                            return ""
                        # Skip RAG for single casual words
                        if content.strip().lower() in _CASUAL_WORDS:
                            return ""
                        try:
                            results: list = []
                            resolved_key = self.vector_service._resolve_api_key(
                                self.config
                            )
                            provider = self.vector_service._get_provider()
                            if provider in ("ollama", "local") or resolved_key:
                                try:
                                    semantic_results = await self.vector_service.search(
                                        content, limit=3
                                    )
                                    results = [
                                        r
                                        for r in semantic_results
                                        if r.get("score", 1.0) >= AUTORAG_MIN_SCORE
                                    ]
                                except Exception as e:
                                    logger.warning(f"Semantic search failed: {e}")
                            if not results:
                                results = await self.vector_service.search_grep(
                                    content, limit=3
                                )
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
                                    logger.info(
                                        f"🧠 Auto-RAG: {len(lines)} memories recalled."
                                    )
                                    return "\n".join(lines)
                        except Exception as e:
                            logger.warning(f"⚠ Auto-RAG failed: {e}")
                        return ""

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
                            recalled_context = await rag_task
                        except asyncio.TimeoutError:
                            recalled_context = ""
                            logger.debug("⚡ Auto-RAG timeout — skipping.")
                        except Exception as e:
                            recalled_context = ""
                            logger.warning(f"⚠ Auto-RAG error: {e}")
                    else:
                        recalled_context = ""
                        logger.debug("⚡ Auto-RAG skipped (short/casual message).")

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
                    user_msg_content = (
                        [
                            {"type": "text", "text": msg.content or " "},
                            {"type": "image_url", "image_url": {"url": image_data}},
                        ]
                        if image_data
                        else msg.content
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
                                "content": content,
                                "image": bool(image_data),
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

                    asyncio.create_task(
                        self.session_manager.update_session(
                            session_key=session_key,
                            model=self.model,
                            origin=msg.channel,
                            injected_files=injected,
                        )
                    )

                    await self._trim_history(session_key)
                    asyncio.create_task(self._flush_history(session_key))

                    include_tools = self._should_include_tools(content)
                    stream = await self._llm_call_with_retry(
                        messages=self.history[session_key],
                        session_key=session_key,
                        msg=msg,
                        stream=True,
                        include_tools=include_tools,
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

                    if full_content or tool_calls:
                        am: Dict = {"role": "assistant", "content": full_content or ""}
                        if tool_calls:
                            am["tool_calls"] = tool_calls
                        self.history[session_key].append(am)
                        self._mark_dirty(session_key)

                    raw_reply = accumulated_content or ""

                    if tool_calls:
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
                        raw_reply = self._build_tool_fallback_reply(session_key)
                        force_direct_reply = True
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
                            msg.channel == "web"
                            and tool_calls
                            and not iterations_limit_reached
                            and web_streamed_reply
                            and not force_direct_reply
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

                except asyncio.CancelledError:
                    logger.warning(f"⚠ Task cancelled for {session_key}")
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
