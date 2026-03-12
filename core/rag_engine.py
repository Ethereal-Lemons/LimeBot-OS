"""
core/rag_engine.py
──────────────────
Auto-RAG trace recording and retrieval, extracted from AgentLoop.

Keeps all recent RAG traces in memory per session, capped at
RAG_TRACE_LIMIT entries.  Provides helpers to build human-readable
result trace dicts for the Memory Explorer dashboard.
"""

from typing import Any, Dict, List, Optional


AUTORAG_MIN_SCORE = 0.65
_RAG_TRACE_LIMIT = 20


class RagEngine:
    """Manages Auto-RAG trace recording and retrieval for an AgentLoop."""

    def __init__(
        self, truncate_fn, safe_json_load_fn, trace_limit: int = _RAG_TRACE_LIMIT
    ) -> None:
        """
        Parameters
        ----------
        truncate_fn:
            Reference to AgentLoop._truncate_preview — used when building
            result trace dicts.
        safe_json_load_fn:
            Reference to AgentLoop._safe_json_load.
        trace_limit:
            Maximum number of RAG traces kept per session.
        """
        self._truncate_preview = truncate_fn
        self._safe_json_load = safe_json_load_fn
        self._RAG_TRACE_LIMIT = trace_limit
        self._recent_rag_traces: Dict[str, List[Dict[str, Any]]] = {}

    # ── Trace recording ──────────────────────────────────────────────────

    def record(self, session_key: str, trace: Dict[str, Any]) -> None:
        """Append a RAG trace for *session_key*, evicting oldest if over limit."""
        traces = self._recent_rag_traces.setdefault(session_key, [])
        traces.append(trace)
        if len(traces) > self._RAG_TRACE_LIMIT:
            del traces[: -self._RAG_TRACE_LIMIT]

    # ── Trace retrieval ──────────────────────────────────────────────────

    def get_recent(
        self, session_key: Optional[str] = None, limit: int = 20
    ) -> List[Dict[str, Any]]:
        """Return recent RAG traces, newest-first.

        If *session_key* is provided, return only traces for that session.
        Otherwise merge and sort all sessions by timestamp.
        """
        if session_key:
            rows = list(self._recent_rag_traces.get(session_key, []))[-limit:]
            rows.reverse()
            return rows

        merged: List[Dict[str, Any]] = []
        for key, traces in self._recent_rag_traces.items():
            for trace in traces:
                row = dict(trace)
                row.setdefault("session_key", key)
                merged.append(row)
        merged.sort(key=lambda item: item.get("ts", 0), reverse=True)
        return merged[:limit]

    # ── Result trace builder ─────────────────────────────────────────────

    def build_result_trace(self, row: Dict[str, Any]) -> Dict[str, Any]:
        """Build a human-readable trace dict from a raw vector search row."""
        metadata = self._safe_json_load(row.get("metadata")) or {}
        if not isinstance(metadata, dict):
            metadata = {}

        source = (
            row.get("source")
            or metadata.get("source")
            or metadata.get("path")
            or metadata.get("file")
        )
        return {
            "text": self._truncate_preview(row.get("text", ""), 220),
            "score": row.get("score"),
            "timestamp": row.get("timestamp"),
            "source": source,
            "category": row.get("category") or metadata.get("category"),
        }
