"""
Observability & Metrics — lightweight timing, counters, and structured event logging.

Collects per-session and global metrics without external dependencies.
Data is exposed via `get_snapshot()` for the web dashboard and logged to
`persona/sessions/metrics.jsonl` for post-hoc analysis.
"""

import time
import json
import threading
from pathlib import Path
from dataclasses import dataclass, field, asdict
from typing import Dict, Optional
from contextlib import contextmanager

METRICS_FILE = Path("persona/sessions/metrics.jsonl")


@dataclass
class SessionMetrics:
    """Accumulated metrics for a single session."""

    session_key: str
    llm_calls: int = 0
    tool_calls: int = 0
    tokens_in: int = 0
    tokens_out: int = 0
    total_llm_time_s: float = 0.0
    total_tool_time_s: float = 0.0
    errors: int = 0
    started_at: float = field(default_factory=time.time)


class MetricsCollector:
    """
    Singleton metrics store.
    Thread-safe via a simple lock (asyncio tasks share one thread,
    but SessionManager runs in threads for I/O).
    """

    _instance = None

    def __new__(cls):
        if cls._instance is None:
            cls._instance = super().__new__(cls)
            cls._instance._sessions: Dict[str, SessionMetrics] = {}
            cls._instance._lock = threading.Lock()
            cls._instance._global_llm_calls = 0
            cls._instance._global_tool_calls = 0
            cls._instance._global_errors = 0
            cls._instance._boot_time = time.time()
        return cls._instance

    def _get_session(self, session_key: str) -> SessionMetrics:
        if session_key not in self._sessions:
            self._sessions[session_key] = SessionMetrics(session_key=session_key)
        return self._sessions[session_key]

    def record_llm_call(
        self,
        session_key: str,
        duration_s: float,
        tokens_in: int = 0,
        tokens_out: int = 0,
    ):
        """Record an LLM call with its duration and token usage."""
        with self._lock:
            sm = self._get_session(session_key)
            sm.llm_calls += 1
            sm.total_llm_time_s += duration_s
            sm.tokens_in += tokens_in
            sm.tokens_out += tokens_out
            self._global_llm_calls += 1

    def record_tool_call(
        self, session_key: str, tool_name: str, duration_s: float, error: bool = False
    ):
        """Record a tool execution with timing and error status."""
        with self._lock:
            sm = self._get_session(session_key)
            sm.tool_calls += 1
            sm.total_tool_time_s += duration_s
            self._global_tool_calls += 1
            if error:
                sm.errors += 1
                self._global_errors += 1

        self._log_event(
            {
                "type": "tool_call",
                "session": session_key,
                "tool": tool_name,
                "duration_s": round(duration_s, 3),
                "error": error,
                "ts": time.time(),
            }
        )

    def record_error(self, session_key: str, error_type: str, detail: str = ""):
        """Record a generic error event."""
        with self._lock:
            sm = self._get_session(session_key)
            sm.errors += 1
            self._global_errors += 1

        self._log_event(
            {
                "type": "error",
                "session": session_key,
                "error_type": error_type,
                "detail": detail[:500],
                "ts": time.time(),
            }
        )

    @contextmanager
    def time_llm(self, session_key: str):
        """Context manager that times an LLM call. Caller sets tokens after."""
        start = time.time()
        result = {"tokens_in": 0, "tokens_out": 0}
        try:
            yield result
        finally:
            duration = time.time() - start
            self.record_llm_call(
                session_key,
                duration,
                tokens_in=result.get("tokens_in", 0),
                tokens_out=result.get("tokens_out", 0),
            )

    @contextmanager
    def time_tool(self, session_key: str, tool_name: str):
        """Context manager that times a tool call."""
        start = time.time()
        error_flag = [False]
        try:
            yield error_flag
        except Exception:
            error_flag[0] = True
            raise
        finally:
            duration = time.time() - start
            self.record_tool_call(session_key, tool_name, duration, error=error_flag[0])

    def get_snapshot(self) -> dict:
        """Return a JSON-serializable snapshot of all metrics."""
        with self._lock:
            return {
                "uptime_s": round(time.time() - self._boot_time, 1),
                "global": {
                    "llm_calls": self._global_llm_calls,
                    "tool_calls": self._global_tool_calls,
                    "errors": self._global_errors,
                },
                "sessions": {k: asdict(v) for k, v in self._sessions.items()},
            }

    def get_session_metrics(self, session_key: str) -> Optional[dict]:
        """Return metrics for a specific session."""
        with self._lock:
            sm = self._sessions.get(session_key)
            return asdict(sm) if sm else None

    def _log_event(self, event: dict):
        """Append a structured event to the metrics JSONL file."""
        try:
            METRICS_FILE.parent.mkdir(parents=True, exist_ok=True)
            with open(METRICS_FILE, "a", encoding="utf-8") as f:
                f.write(json.dumps(event) + "\n")
        except Exception:
            pass
