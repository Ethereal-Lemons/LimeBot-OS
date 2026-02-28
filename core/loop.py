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

_BASE_DIR = Path(__file__).resolve().parent.parent
PERSONA_DIR = _BASE_DIR / "persona"
USERS_DIR = PERSONA_DIR / "users"
MEMORY_DIR = PERSONA_DIR / "memory"
SOUL_FILE = PERSONA_DIR / "SOUL.md"
IDENTITY_FILE = PERSONA_DIR / "IDENTITY.md"


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

    async def _init_skills_and_tools(self) -> None:
        """Background: discover skills, build tool definitions, then warm up slow services."""
        await asyncio.to_thread(self.skill_registry.discover_and_load)

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
    ) -> str:
        """Stable (cached) + volatile (per-message: memory + RAG + timestamp)."""
        stable = await self._get_stable_prompt(sender_id, channel, chat_id, sender_name)
        volatile = prompt_module.get_volatile_prompt_suffix(recalled_context)
        return stable + volatile

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

    async def _llm_call_with_retry(
        self,
        messages: List[Dict],
        session_key: str,
        msg: Optional[InboundMessage],
        max_retries: int = 3,
        stream: bool = False,
    ) -> Any:

        model, base_url, api_key, custom_llm_provider = self._provider
        tools = self._get_tool_definitions()

        for attempt in range(max_retries):
            try:
                kwargs: Dict[str, Any] = dict(
                    model=model,
                    messages=messages,
                    tools=tools,
                    tool_choice="auto",
                    stream=stream,
                    base_url=base_url,
                    api_key=api_key,
                    custom_llm_provider=custom_llm_provider,
                )
                if stream:
                    kwargs["stream_options"] = {"include_usage": True}
                return await acompletion(**kwargs)

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
                            metadata={"reply_to": msg.sender_id, "is_warning": True},
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
                                metadata={"reply_to": msg.sender_id, "is_error": True},
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
            return f"Error executing browser tool: {e}"

    async def _execute_tool(
        self, function_name: str, function_args: dict, session_key: str
    ) -> Any:

        cached = self.tool_cache.get(function_name, function_args)
        if cached:
            logger.debug(f"⚡ Cache hit: {function_name}")
            return cached

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
                result = (
                    await handler(**function_args)
                    if handler
                    else f"Error: Unknown tool '{function_name}'"
                )

            is_read_only = function_name in _BROWSER_CACHEABLE or function_name in {
                "read_file",
                "list_dir",
                "memory_search",
            }
            if result and not str(result).startswith("Error:") and is_read_only:
                self.tool_cache.set(function_name, function_args, result)

            return result

        except Exception as e:
            return f"Error executing '{function_name}': {e}"

    async def _execute_tool_batch(
        self,
        tool_calls: list,
        session_key: str,
        msg: Optional[InboundMessage],
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
                    }
                )

                raw_args = tool_call["function"]["arguments"]
                if raw_args == "{}{}":
                    logger.warning(
                        f"⚠ Malformed args for {function_name} — fixing to {{}}"
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

                logger.info(f"Executing: {function_name}({function_args})")

                is_internal = False
                is_whatsapp = (
                    msg is not None and getattr(msg, "channel", "") == "whatsapp"
                )

                sensitive_tools = {
                    "delete_file",
                    "run_command",
                    "write_file",
                    "cron_remove",
                }

                if function_name in sensitive_tools:
                    is_whitelisted = (
                        session_key in self.session_whitelists
                        and function_name in self.session_whitelists[session_key]
                    )
                    if is_whatsapp:
                        # WhatsApp is usually autonomous, but user wants confirmation for these
                        if function_name in {"delete_file"}:
                            is_whitelisted = False
                        else:
                            is_whitelisted = True
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

                        embed_fields = []
                        if function_name == "run_command":
                            cmd = function_args.get("command", "")
                            cwd = function_args.get("cwd", "default")
                            embed_fields.extend(
                                [
                                    {
                                        "name": "Command",
                                        "value": f"```bash\n{cmd}\n```",
                                        "inline": False,
                                    },
                                    {
                                        "name": "Working Directory",
                                        "value": f"`{cwd}`",
                                        "inline": True,
                                    },
                                    {
                                        "name": "Agent",
                                        "value": f"`{session_key}`",
                                        "inline": True,
                                    },
                                ]
                            )
                        elif function_name == "delete_file":
                            path = function_args.get("path", "")
                            embed_fields.extend(
                                [
                                    {
                                        "name": "Target File",
                                        "value": f"`{path}`",
                                        "inline": False,
                                    },
                                    {
                                        "name": "Agent",
                                        "value": f"`{session_key}`",
                                        "inline": True,
                                    },
                                ]
                            )
                        elif function_name == "write_file":
                            path = function_args.get("path", "")
                            content = function_args.get("content", "")
                            preview = (
                                (content[:100] + "...")
                                if len(content) > 100
                                else content
                            )
                            embed_fields.extend(
                                [
                                    {
                                        "name": "Target File",
                                        "value": f"`{path}`",
                                        "inline": False,
                                    },
                                    {
                                        "name": "Content Preview",
                                        "value": f"```\n{preview}\n```",
                                        "inline": False,
                                    },
                                    {
                                        "name": "Agent",
                                        "value": f"`{session_key}`",
                                        "inline": True,
                                    },
                                ]
                            )
                        else:
                            embed_fields.extend(
                                [
                                    {
                                        "name": "Arguments",
                                        "value": f"```json\n{json.dumps(function_args, indent=2)[:500]}\n```",
                                        "inline": False,
                                    },
                                    {
                                        "name": "Agent",
                                        "value": f"`{session_key}`",
                                        "inline": True,
                                    },
                                ]
                            )

                        await self.bus.publish_outbound(
                            OutboundMessage(
                                channel=msg.channel if msg else "web",
                                chat_id=msg.chat_id if msg else "system",
                                content=f"🛠️ Executing {function_name}..."
                                if is_whatsapp
                                else "⏳",
                                metadata={
                                    "type": "tool_execution",
                                    "status": "waiting_confirmation",
                                    "tool": function_name,
                                    "args": function_args,
                                    "tool_call_id": tc_id,
                                    "conf_id": conf_id,
                                    "embed": {
                                        "title": "Exec Approval Required",
                                        "description": "A command needs your approval.",
                                        "color": "#F59E0B",
                                        "fields": embed_fields,
                                        "footer": f"Expires in 300s | ID: {conf_id}",
                                    },
                                },
                            )
                        )
                        if msg and msg.channel != "web":
                            await self.bus.publish_outbound(
                                OutboundMessage(
                                    channel="web",
                                    chat_id=msg.chat_id or "system",
                                    content="",
                                    metadata={
                                        "type": "tool_execution",
                                        "status": "waiting_confirmation",
                                        "tool": function_name,
                                        "args": function_args,
                                        "tool_call_id": tc_id,
                                        "conf_id": conf_id,
                                    },
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

                await self.bus.publish_outbound(
                    OutboundMessage(
                        channel=msg.channel if msg else "web",
                        chat_id=msg.chat_id if msg else "system",
                        content=f"🛠️ Executing {function_name}..."
                        if is_whatsapp
                        else "⏳",
                        metadata={
                            "type": "tool_execution",
                            "status": "running",
                            "tool": function_name,
                            "args": function_args,
                            "tool_call_id": tc_id,
                        },
                    )
                )
                if msg and msg.channel != "web":
                    await self.bus.publish_outbound(
                        OutboundMessage(
                            channel="web",
                            chat_id=msg.chat_id or "system",
                            content="",
                            metadata={
                                "type": "tool_execution",
                                "status": "running",
                                "tool": function_name,
                                "args": function_args,
                                "tool_call_id": tc_id,
                            },
                        )
                    )

                t0 = time.time()
                try:
                    # General safety timeout for ANY tool execution (MCP, Browser, etc.)
                    tool_timeout = getattr(self.config, "tool_timeout", 120.0)
                    if tool_timeout and tool_timeout > 0:
                        result = await asyncio.wait_for(
                            self._execute_tool(function_name, function_args, session_key),
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
                    result = f"Error: Tool '{function_name}' timed out after {timeout_msg}."
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
                    await self.bus.publish_outbound(
                        OutboundMessage(
                            channel=msg.channel if msg else "web",
                            chat_id=msg.chat_id if msg else "system",
                            content=fail_name,
                            metadata={
                                "type": "tool_execution",
                                "tool": fail_name,
                                "status": "error",
                                "args": fail_args,
                                "result": f"Execution failed or was cancelled: {type(outcome).__name__}",
                                "tool_call_id": fail_tc_id,
                            },
                        )
                    )
                    if msg and msg.channel != "web":
                        await self.bus.publish_outbound(
                            OutboundMessage(
                                channel="web",
                                chat_id=msg.chat_id or "system",
                                content=fail_name,
                                metadata={
                                    "type": "tool_execution",
                                    "tool": fail_name,
                                    "status": "error",
                                    "args": fail_args,
                                    "result": f"Execution failed or was cancelled: {type(outcome).__name__}",
                                    "tool_call_id": fail_tc_id,
                                },
                            )
                        )
                except Exception:
                    pass
                continue

            tc_id, function_name, function_args, result, is_blocked, is_internal = (
                outcome
            )
            if is_blocked:
                any_blocked = True

            if not is_internal:
                try:
                    await self.bus.publish_outbound(
                        OutboundMessage(
                            channel=msg.channel if msg else "web",
                            chat_id=msg.chat_id if msg else "system",
                            content="",
                            metadata={
                                "type": "tool_execution",
                                "tool": function_name,
                                "status": "completed"
                                if not str(result).startswith("Error:")
                                else "error",
                                "args": function_args,
                                "result": str(result)[:TOOL_BROADCAST_MAX_CHARS],
                                "tool_call_id": tc_id,
                            },
                        )
                    )
                    if msg and msg.channel != "web":
                        await self.bus.publish_outbound(
                            OutboundMessage(
                                channel="web",
                                chat_id=msg.chat_id or "system",
                                content="",
                                metadata={
                                    "type": "tool_execution",
                                    "tool": function_name,
                                    "status": "completed"
                                    if not str(result).startswith("Error:")
                                    else "error",
                                    "args": function_args,
                                    "result": str(result)[:TOOL_BROADCAST_MAX_CHARS],
                                    "tool_call_id": tc_id,
                                },
                            )
                        )
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
        await self.bus.publish_outbound(
            OutboundMessage(
                channel="web",
                chat_id=chat_id,
                content=content,
                metadata={
                    "type": "tool_execution",
                    "status": "progress",
                    "tool_call_id": tool_call_id,
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

        return tool_calls

    async def _consume_stream(
        self,
        response_stream,
        msg: InboundMessage,
        session_key: str,
        previous_content: str = "",
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
                                            metadata={"type": "chunk"},
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
                                            metadata={"type": "chunk"},
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
                                    for g in [
                                        "save_user",
                                        "save_soul",
                                        "save_memory",
                                        "log_memory",
                                        "save_mood",
                                        "save_relationship",
                                    ]:
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
                                            metadata={"type": "chunk"},
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
                            metadata={"type": "thinking"},
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
                        metadata={"type": "rate_limit_error", "details": str(e)},
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
            await self.bus.publish_outbound(
                OutboundMessage(
                    channel=msg.channel,
                    chat_id=msg.chat_id,
                    content=display_buffer,
                    metadata={"type": "chunk"},
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
                    metadata={"type": "full_content"},
                )
            )

        return full_content, tool_calls, usage

    def _get_memory_context(self) -> str:
        return prompt_module.get_memory_context()

    def _is_setup_complete(self) -> bool:
        return prompt_module.is_setup_complete()

    def _get_system_prompt(
        self, sender_id: str, channel: str = "", chat_id: str = ""
    ) -> str:
        return self._build_full_system_prompt(sender_id, channel, chat_id)

    def _validate_and_save_identity(self, content: str) -> bool:
        return prompt_module.validate_and_save_identity(content)

    def _validate_and_save_soul(self, content: str) -> bool:
        return prompt_module.validate_and_save_soul(content)

    def _validate_and_save_mood(self, content: str) -> bool:
        return prompt_module.validate_and_save_mood(content)

    def _validate_and_save_relationships(self, content: str) -> bool:
        return prompt_module.validate_and_save_relationships(content)

    async def _process_message(self, msg: InboundMessage) -> None:
        session_key = msg.session_key
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
                    validate_soul=self._validate_and_save_soul,
                    validate_identity=self._validate_and_save_identity,
                    validate_mood=self._validate_and_save_mood,
                    validate_relationship=self._validate_and_save_relationships,
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
                        metadata={"is_maintenance": True},
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
                    _APPROVE = frozenset(
                        {
                            "proceed",
                            "yes",
                            "approve",
                            "confirm",
                            "ok",
                            "sure",
                            "y",
                            "go",
                            "run",
                            "do it",
                        }
                    )
                    _DENY = frozenset(
                        {"no", "cancel", "deny", "stop", "reject", "n", "abort", "nope"}
                    )
                    is_approve = normalized in _APPROVE or any(
                        normalized.startswith(k + " ") for k in _APPROVE
                    )
                    is_deny = normalized in _DENY or any(
                        normalized.startswith(k + " ") for k in _DENY
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
                                metadata={"reply_to": msg.sender_id},
                            )
                        )
                        return

            await self.bus.publish_outbound(
                OutboundMessage(
                    channel=msg.channel,
                    chat_id=msg.chat_id,
                    content="",
                    metadata={"type": "typing"},
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

                    # Fast-path: skip RAG for short/casual messages that
                    # won't benefit from memory recall anyway.
                    _CASUAL = frozenset(
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

                    async def _do_rag() -> str:
                        if not (
                            content
                            and len(content) > 10
                            and not content.startswith(("/", "@"))
                        ):
                            return ""
                        # Skip RAG for single casual words
                        if content.strip().lower() in _CASUAL:
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
                        and content.strip().lower() not in _CASUAL
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

                    stream = await self._llm_call_with_retry(
                        messages=self.history[session_key],
                        session_key=session_key,
                        msg=msg,
                        stream=True,
                    )
                    full_content, tool_calls, _ = await self._consume_stream(
                        stream,
                        msg,
                        session_key,
                    )
                    accumulated_content = full_content
                    iterations_limit_reached = False

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
                                metadata={"type": "stop_typing"},
                            )
                        )

                        is_blocked = await self._execute_tool_batch(
                            tool_calls, session_key, msg
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
                                )
                                (
                                    nxt_content,
                                    nxt_tool_calls,
                                    _,
                                ) = await self._consume_stream(
                                    nxt_stream,
                                    msg,
                                    session_key,
                                    accumulated_content,
                                )

                                clean_next = nxt_content
                                acc_s, nxt_s = (
                                    accumulated_content.strip(),
                                    nxt_content.strip(),
                                )

                                if acc_s and nxt_s:
                                    max_overlap = min(len(acc_s), len(nxt_s), 100)
                                    found_overlap = False
                                    for length in range(max_overlap, 4, -1):
                                        suffix = acc_s[-length:]
                                        if nxt_s.lower().startswith(suffix.lower()):
                                            match = re.search(
                                                re.escape(suffix),
                                                nxt_content,
                                                re.IGNORECASE,
                                            )
                                            if match:
                                                idx = match.start()
                                                clean_next = nxt_content[
                                                    idx + length :
                                                ].lstrip()
                                                found_overlap = True
                                                break

                                    if not clean_next.strip() and found_overlap:
                                        clean_next = ""

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
                                    nxt_tool_calls, session_key, msg
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

                    tag_result = await process_tags(
                        raw_reply=raw_reply,
                        sender_id=sender_id,
                        validate_soul=self._validate_and_save_soul,
                        validate_identity=self._validate_and_save_identity,
                        validate_mood=self._validate_and_save_mood,
                        validate_relationship=self._validate_and_save_relationships,
                        vector_service=self.vector_service,
                        bus=self.bus,
                        msg=msg,
                        config=self.config,
                    )
                    reply_to_user = tag_result.clean_reply
                    soul_updated = tag_result.soul_updated
                    identity_updated = tag_result.identity_updated

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

                        if (
                            msg.channel == "web"
                            and tool_calls
                            and not iterations_limit_reached
                        ):
                            outbound = OutboundMessage(
                                channel=msg.channel,
                                chat_id=msg.chat_id,
                                content="",
                                metadata={"type": "stop_typing"},
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
                                metadata={
                                    "type": "cancellation",
                                    "is_cancellation": True,
                                },
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
                            metadata={"is_error": True, "reply_to": msg.sender_id},
                        )
                    )

        finally:
            await self._flush_history(session_key, force=True)
            self.active_tasks.pop(session_key, None)
