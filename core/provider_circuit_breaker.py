"""Recoverable per-provider circuit breakers for LLM failover.

The breaker deliberately keeps authentication failures open until the provider
credential/configuration fingerprint changes. Transient failures use a bounded
failure window and a single half-open probe after the recovery timeout.
"""

from __future__ import annotations

import hashlib
import time
from collections import deque
from dataclasses import dataclass, field
from typing import Deque, Dict, Optional


@dataclass
class _Circuit:
    credential_fingerprint: str = ""
    failures: Deque[float] = field(default_factory=deque)
    state: str = "closed"
    opened_at: float = 0.0
    probe_in_flight: bool = False
    auth_blocked: bool = False


class ProviderCircuitOpenError(RuntimeError):
    """Raised when no request may currently be sent to a provider."""

    def __init__(self, provider_key: str):
        self.provider_key = provider_key
        super().__init__(f"Provider circuit is open for {provider_key}")


class ProviderCircuitBreaker:
    """Small in-memory breaker keyed by provider/model identity."""

    def __init__(
        self,
        *,
        failure_threshold: int = 3,
        failure_window_s: float = 60.0,
        recovery_timeout_s: float = 30.0,
    ) -> None:
        self.failure_threshold = max(1, int(failure_threshold))
        self.failure_window_s = max(1.0, float(failure_window_s))
        self.recovery_timeout_s = max(0.1, float(recovery_timeout_s))
        self._circuits: Dict[str, _Circuit] = {}

    @staticmethod
    def credential_fingerprint(api_key: Optional[str]) -> str:
        value = str(api_key or "")
        return hashlib.sha256(value.encode("utf-8")).hexdigest()[:16]

    def _get(self, provider_key: str, credential_fingerprint: str) -> _Circuit:
        circuit = self._circuits.get(provider_key)
        if circuit is None or circuit.credential_fingerprint != credential_fingerprint:
            circuit = _Circuit(credential_fingerprint=credential_fingerprint)
            self._circuits[provider_key] = circuit
        return circuit

    def allow(
        self,
        provider_key: str,
        *,
        credential_fingerprint: str,
        now: Optional[float] = None,
    ) -> bool:
        now = time.monotonic() if now is None else float(now)
        circuit = self._get(provider_key, credential_fingerprint)
        if circuit.auth_blocked:
            return False
        if circuit.state == "open":
            if now - circuit.opened_at < self.recovery_timeout_s:
                return False
            if circuit.probe_in_flight:
                return False
            circuit.state = "half_open"
            circuit.probe_in_flight = True
            return True
        if circuit.state == "half_open":
            if circuit.probe_in_flight:
                return False
            circuit.probe_in_flight = True
        return True

    def record_success(
        self,
        provider_key: str,
        *,
        credential_fingerprint: str,
    ) -> None:
        circuit = self._get(provider_key, credential_fingerprint)
        circuit.state = "closed"
        circuit.opened_at = 0.0
        circuit.probe_in_flight = False
        circuit.auth_blocked = False
        circuit.failures.clear()

    def record_failure(
        self,
        provider_key: str,
        *,
        credential_fingerprint: str,
        authentication: bool = False,
        now: Optional[float] = None,
    ) -> None:
        now = time.monotonic() if now is None else float(now)
        circuit = self._get(provider_key, credential_fingerprint)
        circuit.probe_in_flight = False
        if authentication:
            circuit.auth_blocked = True
            circuit.state = "open"
            circuit.opened_at = now
            return

        cutoff = now - self.failure_window_s
        while circuit.failures and circuit.failures[0] < cutoff:
            circuit.failures.popleft()
        circuit.failures.append(now)
        if circuit.state == "half_open" or len(circuit.failures) >= self.failure_threshold:
            circuit.state = "open"
            circuit.opened_at = now

    def record_aborted(
        self,
        provider_key: str,
        *,
        credential_fingerprint: str,
        now: Optional[float] = None,
    ) -> None:
        """Release a claimed half-open probe when its request is cancelled."""
        now = time.monotonic() if now is None else float(now)
        circuit = self._get(provider_key, credential_fingerprint)
        circuit.probe_in_flight = False
        if circuit.state == "half_open":
            circuit.state = "open"
            circuit.opened_at = now

    def snapshot(self, *, now: Optional[float] = None) -> Dict[str, Dict[str, object]]:
        now = time.monotonic() if now is None else float(now)
        result: Dict[str, Dict[str, object]] = {}
        for provider_key, circuit in self._circuits.items():
            state = circuit.state
            if state == "open" and not circuit.auth_blocked:
                if now - circuit.opened_at >= self.recovery_timeout_s:
                    state = "half_open"
            result[provider_key] = {
                "state": state,
                "authentication_blocked": circuit.auth_blocked,
                "failures": len(circuit.failures),
                "probe_in_flight": circuit.probe_in_flight,
            }
        return result
