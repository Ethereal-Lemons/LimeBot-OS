"""Low-overhead metrics with bounded, ordered background JSONL persistence."""

import json
import queue
import threading
import time
from contextlib import contextmanager
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Dict, Optional

from core.redaction import redact_sensitive_text

METRICS_FILE = Path("persona/sessions/metrics.jsonl")
_METRICS_QUEUE_SIZE = 2048


@dataclass
class SessionMetrics:
    session_key: str
    llm_calls: int = 0
    tool_calls: int = 0
    tokens_in: int = 0
    tokens_out: int = 0
    total_llm_time_s: float = 0.0
    total_tool_time_s: float = 0.0
    errors: int = 0
    anomalies: Dict[str, int] = field(default_factory=dict)
    started_at: float = field(default_factory=time.time)


@dataclass
class _FlushMarker:
    completed: threading.Event


class MetricsCollector:
    """Thread-safe counters whose event logging never waits for filesystem I/O."""

    _instance = None

    def __new__(cls):
        if cls._instance is None:
            instance = super().__new__(cls)
            instance._sessions = {}
            instance._lock = threading.Lock()
            instance._global_llm_calls = 0
            instance._global_tool_calls = 0
            instance._global_errors = 0
            instance._global_anomalies = {}
            instance._boot_time = time.time()
            instance._dropped_events = 0
            instance._closed = False
            instance._events = queue.Queue(maxsize=_METRICS_QUEUE_SIZE)
            instance._writer = threading.Thread(
                target=instance._writer_loop,
                name="limebot-metrics-writer",
                daemon=True,
            )
            instance._writer.start()
            cls._instance = instance
        return cls._instance

    def _get_session(self, session_key: str) -> SessionMetrics:
        if session_key not in self._sessions:
            self._sessions[session_key] = SessionMetrics(session_key=session_key)
        return self._sessions[session_key]

    def record_llm_call(self, session_key: str, duration_s: float, tokens_in: int = 0, tokens_out: int = 0):
        with self._lock:
            metrics = self._get_session(session_key)
            metrics.llm_calls += 1
            metrics.total_llm_time_s += duration_s
            metrics.tokens_in += tokens_in
            metrics.tokens_out += tokens_out
            self._global_llm_calls += 1

    def record_tool_call(self, session_key: str, tool_name: str, duration_s: float, error: bool = False):
        with self._lock:
            metrics = self._get_session(session_key)
            metrics.tool_calls += 1
            metrics.total_tool_time_s += duration_s
            self._global_tool_calls += 1
            if error:
                metrics.errors += 1
                self._global_errors += 1
        self._log_event({"type": "tool_call", "session": session_key, "tool": tool_name, "duration_s": round(duration_s, 3), "error": error, "ts": time.time()})

    def record_error(self, session_key: str, error_type: str, detail: str = ""):
        with self._lock:
            metrics = self._get_session(session_key)
            metrics.errors += 1
            self._global_errors += 1
        self._log_event({"type": "error", "session": session_key, "error_type": error_type, "detail": redact_sensitive_text(detail)[:500], "ts": time.time()})

    def record_anomaly(self, session_key: str, anomaly_type: str, detail: str = "", count: int = 1):
        with self._lock:
            metrics = self._get_session(session_key)
            metrics.anomalies[anomaly_type] = metrics.anomalies.get(anomaly_type, 0) + count
            self._global_anomalies[anomaly_type] = self._global_anomalies.get(anomaly_type, 0) + count
        self._log_event({"type": "anomaly", "session": session_key, "anomaly_type": anomaly_type, "detail": redact_sensitive_text(detail)[:500], "count": count, "ts": time.time()})

    def record_stage_timing(self, session_key: str, stage: str, duration_s: float, metadata: Optional[dict] = None):
        event = {"type": "stage_timing", "session": session_key, "stage": str(stage or "").strip() or "unknown", "duration_s": round(float(duration_s), 3), "ts": time.time()}
        if metadata is not None:
            event["metadata"] = self._normalize_metadata_value(metadata)
        self._log_event(event)

    @contextmanager
    def time_llm(self, session_key: str):
        start = time.time()
        result = {"tokens_in": 0, "tokens_out": 0}
        try:
            yield result
        finally:
            self.record_llm_call(session_key, time.time() - start, result.get("tokens_in", 0), result.get("tokens_out", 0))

    @contextmanager
    def time_tool(self, session_key: str, tool_name: str):
        start = time.time()
        error_flag = [False]
        try:
            yield error_flag
        except Exception:
            error_flag[0] = True
            raise
        finally:
            self.record_tool_call(session_key, tool_name, time.time() - start, error=error_flag[0])

    def get_snapshot(self) -> dict:
        with self._lock:
            return {
                "uptime_s": round(time.time() - self._boot_time, 1),
                "global": {
                    "llm_calls": self._global_llm_calls,
                    "tool_calls": self._global_tool_calls,
                    "errors": self._global_errors,
                    "anomalies": dict(self._global_anomalies),
                    "dropped_events": self._dropped_events,
                },
                "sessions": {key: asdict(value) for key, value in self._sessions.items()},
            }

    def get_session_metrics(self, session_key: str) -> Optional[dict]:
        with self._lock:
            metrics = self._sessions.get(session_key)
            return asdict(metrics) if metrics else None

    def _normalize_metadata_value(self, value: Any, depth: int = 0) -> Any:
        if depth >= 4:
            return "<truncated>"
        if value is None or isinstance(value, (bool, int, float)):
            return value
        if isinstance(value, str):
            return value[:500]
        if isinstance(value, dict):
            normalized = {}
            for index, (key, nested) in enumerate(value.items()):
                if index >= 20:
                    normalized["__truncated__"] = max(len(value) - 20, 0)
                    break
                normalized[str(key)[:100]] = self._normalize_metadata_value(nested, depth + 1)
            return normalized
        if isinstance(value, (list, tuple, set)):
            items = list(value)
            normalized = [self._normalize_metadata_value(item, depth + 1) for item in items[:20]]
            if len(items) > 20:
                normalized.append(f"<truncated:{len(items) - 20}>")
            return normalized
        return str(value)[:500]

    def _log_event(self, event: dict) -> None:
        if self._closed:
            return
        try:
            payload = json.dumps(self._normalize_metadata_value(event), separators=(",", ":"))
            self._events.put_nowait(payload)
        except queue.Full:
            with self._lock:
                self._dropped_events += 1
        except Exception:
            # Metrics must never recursively log or affect the product path.
            pass

    @staticmethod
    def _append_batch(payloads: list[str]) -> None:
        METRICS_FILE.parent.mkdir(parents=True, exist_ok=True)
        with METRICS_FILE.open("a", encoding="utf-8") as handle:
            handle.write("\n".join(payloads) + "\n")

    def _writer_loop(self) -> None:
        batch: list[str] = []
        while True:
            item = self._events.get()
            if item is None:
                if batch:
                    self._write_safely(batch)
                return
            if isinstance(item, _FlushMarker):
                if batch:
                    self._write_safely(batch)
                    batch = []
                item.completed.set()
                continue
            batch.append(item)
            if len(batch) >= 32 or self._events.empty():
                self._write_safely(batch)
                batch = []

    def _write_safely(self, payloads: list[str]) -> None:
        try:
            self._append_batch(payloads)
        except Exception:
            pass

    def flush(self, timeout: float = 2.0) -> bool:
        if self._closed or not self._writer.is_alive():
            return self._events.empty()
        marker = _FlushMarker(threading.Event())
        try:
            self._events.put(marker, timeout=max(0.0, timeout))
        except queue.Full:
            return False
        return marker.completed.wait(max(0.0, timeout))

    def close(self, timeout: float = 2.0) -> bool:
        if self._closed:
            return not self._writer.is_alive()
        self.flush(timeout)
        self._closed = True
        try:
            self._events.put(None, timeout=max(0.0, timeout))
        except queue.Full:
            return False
        self._writer.join(max(0.0, timeout))
        return not self._writer.is_alive()
