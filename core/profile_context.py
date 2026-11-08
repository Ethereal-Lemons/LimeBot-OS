"""Per-turn profile state carried safely through concurrent asyncio tasks."""

from __future__ import annotations

from contextlib import contextmanager
from contextvars import ContextVar, Token
from dataclasses import dataclass
from typing import Any, Callable, Iterator, Optional

from core.paths import PersonaPaths, get_persona_paths
from core.profiles import AgentProfile, ProfileRegistry


@dataclass(frozen=True)
class ProfileRuntimeContext:
    profile: AgentProfile
    model: str
    paths: PersonaPaths

    @property
    def profile_id(self) -> str:
        return self.profile.profile_id


_CURRENT_PROFILE: ContextVar[Optional[ProfileRuntimeContext]] = ContextVar(
    "limebot_current_profile", default=None
)


def get_current_profile_context() -> Optional[ProfileRuntimeContext]:
    return _CURRENT_PROFILE.get()


def get_current_profile_id() -> str:
    context = get_current_profile_context()
    return context.profile_id if context is not None else "default"


def get_current_model(default: Optional[str] = None) -> Optional[str]:
    context = get_current_profile_context()
    return context.model if context is not None else default


def get_current_persona_paths() -> PersonaPaths:
    context = get_current_profile_context()
    return context.paths if context is not None else get_persona_paths("default")


@contextmanager
def activate_profile(
    profile: AgentProfile, *, default_model: Optional[str] = None
) -> Iterator[ProfileRuntimeContext]:
    """Bind profile identity/model/paths to the current task until it exits."""
    model = profile.model or str(default_model or "").strip() or "gpt-3.5-turbo"
    context = ProfileRuntimeContext(
        profile=profile,
        model=model,
        paths=get_persona_paths(profile.profile_id),
    )
    token: Token[Optional[ProfileRuntimeContext]] = _CURRENT_PROFILE.set(context)
    try:
        yield context
    finally:
        _CURRENT_PROFILE.reset(token)


class ProfileCoordinator:
    """Resolve inbound profile identity and bind it to one managed turn."""

    def __init__(
        self,
        registry: ProfileRegistry,
        *,
        default_model: Callable[[], Optional[str]],
    ):
        self.registry = registry
        self._default_model = default_model

    @staticmethod
    def requested_profile_id(message: Any) -> str:
        metadata = getattr(message, "metadata", None)
        if not isinstance(metadata, dict):
            return "default"
        return str(metadata.get("profile_id") or "default").strip().lower()

    def resolve_message_profile(self, message: Any) -> AgentProfile:
        profile_id = self.requested_profile_id(message)
        profile = self.registry.get_profile(profile_id)
        metadata = getattr(message, "metadata", None)
        if isinstance(metadata, dict):
            metadata["profile_id"] = profile.profile_id
            metadata["profile_name"] = profile.display_name
        return profile

    @contextmanager
    def activate_message(self, message: Any) -> Iterator[ProfileRuntimeContext]:
        profile = self.resolve_message_profile(message)
        with activate_profile(profile, default_model=self._default_model()) as context:
            yield context
