"""Prepare recalled context and persisted history before model inference."""

from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass
from typing import Any, Awaitable, Callable, Collection, Dict

from loguru import logger

from core.events import InboundMessage
from core.rag_engine import AUTORAG_MIN_SCORE


@dataclass(frozen=True)
class TurnPreparationResult:
    recalled_context: str
    persisted_history: Any


class TurnPreparationService:
    """Run bounded memory recall and history loading before prompt creation."""

    def __init__(
        self,
        *,
        config: Any,
        casual_words: Collection[str],
        get_vector_service: Callable[[], Any],
        history_is_loaded: Callable[[str], bool],
        load_history: Callable[[str], Awaitable[Any]],
        publish_activity: Callable[..., Awaitable[Any]],
        build_rag_result_trace: Callable[[Dict[str, Any]], Dict[str, Any]],
        record_rag_trace: Callable[[str, Dict[str, Any]], None],
        record_stage_timing: Callable[..., None],
    ) -> None:
        self.config = config
        self.casual_words = casual_words
        self._get_vector_service = get_vector_service
        self._history_is_loaded = history_is_loaded
        self._load_history = load_history
        self._publish_activity = publish_activity
        self._build_rag_result_trace = build_rag_result_trace
        self._record_rag_trace = record_rag_trace
        self._record_stage_timing = record_stage_timing

    async def prepare(
        self,
        msg: InboundMessage,
        content: str,
        session_key: str,
        *,
        turn_id: str,
        message_id: str,
    ) -> TurnPreparationResult:
        rag_timeout_s = getattr(
            getattr(self.config, "ai_harness", None), "rag_timeout_s", 0.2
        )
        rag_stage_started = time.perf_counter()
        history_stage_started = time.perf_counter()

        async def do_rag() -> Dict[str, Any]:
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
            if content.strip().lower() in self.casual_words:
                return trace
            try:
                results: list = []
                vector_service = self._get_vector_service()
                if vector_service.has_semantic_candidate():
                    try:
                        semantic_results = (
                            await vector_service.search_semantic(content, limit=3)
                            or []
                        )
                        results = [
                            result
                            for result in semantic_results
                            if result.get("score", 1.0) >= AUTORAG_MIN_SCORE
                        ]
                        if results:
                            trace["mode"] = "vector"
                    except Exception as exc:
                        logger.warning(f"Semantic search failed: {exc}")
                if not results:
                    results = await vector_service.search_grep(content, limit=3) or []
                    if results:
                        trace["mode"] = "grep_fallback"
                if results:
                    seen, lines = set(), []
                    for result in results:
                        text = result["text"].strip()
                        if text not in seen:
                            seen.add(text)
                            lines.append(
                                f"- {text} (Date: {result.get('timestamp', 'unknown')})"
                            )
                    if lines:
                        trace["status"] = "recalled"
                        trace["results"] = [
                            self._build_rag_result_trace(result)
                            for result in results
                        ]
                        trace["recalled_context"] = "\n".join(lines)
                        logger.info(f"Auto-RAG: {len(lines)} memories recalled.")
                        return trace
                trace["status"] = "miss"
            except Exception as exc:
                trace["status"] = "error"
                trace["error"] = str(exc)
                logger.warning(f"Auto-RAG failed: {exc}")
            return trace

        async def load_if_needed() -> Any:
            if self._history_is_loaded(session_key):
                return None
            return await self._load_history(session_key)

        rag_needed = (
            bool(content)
            and len(content) > 10
            and not content.startswith(("/", "@"))
            and content.strip().lower() not in self.casual_words
        )
        rag_task = (
            asyncio.create_task(asyncio.wait_for(do_rag(), timeout=rag_timeout_s))
            if rag_needed
            else None
        )
        history_task = asyncio.create_task(load_if_needed())

        await self._publish_activity(
            msg,
            "Recalling memory and loading history...",
            turn_id=turn_id,
            message_id=message_id,
        )

        rag_stage_metadata = {
            "status": "skipped",
            "mode": "none",
            "timeout_s": rag_timeout_s,
        }
        if rag_task is not None:
            try:
                rag_result = await rag_task
                recalled_context = rag_result.get("recalled_context", "")
                self._record_rag_trace(session_key, rag_result)
                rag_stage_metadata = {
                    "status": rag_result.get("status", "completed"),
                    "mode": rag_result.get("mode", "unknown"),
                    "timeout_s": rag_timeout_s,
                }
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
                rag_stage_metadata = {
                    "status": "timeout",
                    "mode": "timeout",
                    "timeout_s": rag_timeout_s,
                }
                logger.debug("Auto-RAG timeout - skipping.")
            except Exception as exc:
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
                        "error": str(exc),
                    },
                )
                rag_stage_metadata = {
                    "status": "error",
                    "mode": "error",
                    "timeout_s": rag_timeout_s,
                }
                logger.warning(f"Auto-RAG error: {exc}")
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

        self._record_stage_timing(
            session_key,
            "rag",
            rag_stage_started,
            metadata=rag_stage_metadata,
        )
        persisted_history = await history_task
        self._record_stage_timing(
            session_key,
            "history_load",
            history_stage_started,
            metadata={"restored": bool(persisted_history)},
        )
        return TurnPreparationResult(
            recalled_context=recalled_context,
            persisted_history=persisted_history,
        )
