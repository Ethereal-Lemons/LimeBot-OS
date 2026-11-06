"""Validated local registry for named agent profiles."""

from __future__ import annotations

import json
import os
import re
import tempfile
import threading
import time
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Any, Optional

from core.runtime_paths import get_data_dir


PROFILE_ID_RE = re.compile(r"^[a-z0-9](?:[a-z0-9-]{0,38}[a-z0-9])?$")
PROFILE_STORE_VERSION = 1
PROFILE_SHARED_WORKSPACE_TOOLS = frozenset(
    {"run_command", "run_steps", "spawn_agent", "apply_workspace_changeset"}
)


class ProfileError(ValueError):
    """Invalid profile data or a profile registry operation."""


def namespace_session_key(profile_id: str, legacy_key: str) -> str:
    """Prefix non-default conversations while preserving legacy default keys."""
    normalized = str(profile_id or "default").strip().lower()
    if not PROFILE_ID_RE.fullmatch(normalized):
        raise ProfileError("profile_id must be a lowercase slug")
    clean_key = str(legacy_key or "")
    return clean_key if normalized == "default" else f"profile__{normalized}__{clean_key}"


@dataclass(frozen=True)
class AgentProfilePolicy:
    """Optional restrictions layered over LimeBot's global safety policy.

    None values inherit the current global policy. An empty allowed-tools
    tuple explicitly denies every optional tool. Profile approval settings can
    make the global policy stricter but cannot weaken it.
    """

    allowed_tools: Optional[tuple[str, ...]] = None
    allowed_paths: Optional[tuple[str, ...]] = None
    approval_policy: Optional[str] = None

    @classmethod
    def from_dict(cls, value: Any) -> "AgentProfilePolicy":
        if value is None:
            return cls()
        if not isinstance(value, dict):
            raise ProfileError("profile policy must be an object")

        raw_tools = value.get("allowed_tools")
        if raw_tools is not None and not isinstance(raw_tools, list):
            raise ProfileError("allowed_tools must be a list or null")
        tools = None if raw_tools is None else _clean_string_tuple(raw_tools, "allowed_tools")
        raw_paths = value.get("allowed_paths")
        if raw_paths is not None and not isinstance(raw_paths, list):
            raise ProfileError("allowed_paths must be a list or null")
        paths = (
            None
            if raw_paths is None
            else _clean_string_tuple(raw_paths, "allowed_paths")
        )
        approval_policy = value.get("approval_policy")
        if approval_policy is not None:
            approval_policy = str(approval_policy).strip().lower()
            if approval_policy not in {"inherit", "manual", "session", "review", "autonomous"}:
                raise ProfileError("unsupported approval policy")
            if approval_policy == "inherit":
                approval_policy = None
        return cls(allowed_tools=tools, allowed_paths=paths, approval_policy=approval_policy)

    def to_dict(self) -> dict[str, Any]:
        return {
            "allowed_tools": (
                list(self.allowed_tools) if self.allowed_tools is not None else None
            ),
            "allowed_paths": (
                list(self.allowed_paths) if self.allowed_paths is not None else None
            ),
            "approval_policy": self.approval_policy,
        }


@dataclass(frozen=True)
class AgentProfile:
    """One named persona. Credentials remain in LimeBot's existing config."""

    profile_id: str
    display_name: str
    model: Optional[str] = None
    archived: bool = False
    policy: AgentProfilePolicy = field(default_factory=AgentProfilePolicy)
    created_at: float = field(default_factory=time.time)
    updated_at: float = field(default_factory=time.time)

    def __post_init__(self) -> None:
        profile_id = str(self.profile_id or "").strip().lower()
        display_name = str(self.display_name or "").strip()
        model = _clean_optional(self.model, "model", maximum=200)
        if not PROFILE_ID_RE.fullmatch(profile_id):
            raise ProfileError("profile_id must be a lowercase slug (1-40 characters)")
        if not display_name or len(display_name) > 80:
            raise ProfileError("display_name must contain 1-80 characters")
        if not isinstance(self.policy, AgentProfilePolicy):
            raise ProfileError("policy must be an AgentProfilePolicy")
        object.__setattr__(self, "profile_id", profile_id)
        object.__setattr__(self, "display_name", display_name)
        object.__setattr__(self, "model", model)
        object.__setattr__(self, "archived", bool(self.archived))
        object.__setattr__(self, "created_at", float(self.created_at))
        object.__setattr__(self, "updated_at", float(self.updated_at))

    @classmethod
    def from_dict(cls, value: Any) -> "AgentProfile":
        if not isinstance(value, dict):
            raise ProfileError("profile must be an object")
        try:
            return cls(
                profile_id=value.get("profile_id", value.get("id", "")),
                display_name=value.get("display_name", value.get("name", "")),
                model=value.get("model"),
                archived=value.get("archived", False),
                policy=AgentProfilePolicy.from_dict(value.get("policy")),
                created_at=value.get("created_at", time.time()),
                updated_at=value.get("updated_at", time.time()),
            )
        except (TypeError, ValueError) as error:
            if isinstance(error, ProfileError):
                raise
            raise ProfileError("profile fields are invalid") from error

    def to_dict(self) -> dict[str, Any]:
        return {
            "profile_id": self.profile_id,
            "display_name": self.display_name,
            "model": self.model,
            "archived": self.archived,
            "policy": self.policy.to_dict(),
            "created_at": self.created_at,
            "updated_at": self.updated_at,
        }


def profile_allows_tool(profile: AgentProfile, tool_name: str) -> bool:
    """Apply a profile's capability policy without weakening path boundaries."""
    policy = profile.policy
    name = str(tool_name or "")
    if policy.allowed_tools is not None and name not in policy.allowed_tools:
        return False
    if name in PROFILE_SHARED_WORKSPACE_TOOLS:
        if policy.allowed_paths is not None:
            return False
        if profile.profile_id != "default":
            return policy.allowed_tools is not None and name in policy.allowed_tools
    return True


def _clean_optional(value: Any, field_name: str, *, maximum: int) -> Optional[str]:
    if value is None:
        return None
    normalized = str(value).strip()
    if not normalized:
        return None
    if len(normalized) > maximum:
        raise ProfileError(f"{field_name} must be at most {maximum} characters")
    return normalized


def _clean_string_tuple(value: list[Any], field_name: str) -> tuple[str, ...]:
    cleaned = []
    for item in value:
        text = str(item or "").strip()
        if not text:
            continue
        if len(text) > 240:
            raise ProfileError(f"{field_name} entries must be at most 240 characters")
        if text not in cleaned:
            cleaned.append(text)
        if len(cleaned) > 256:
            raise ProfileError(f"{field_name} supports at most 256 entries")
    return tuple(cleaned)


class ProfileRegistry:
    """Atomic, idempotent profile store with a compatibility default row."""

    def __init__(
        self,
        path: Optional[Path] = None,
        *,
        configured_model: Optional[str] = None,
        default_name: str = "LimeBot",
    ):
        self.path = Path(path) if path is not None else get_data_dir() / "profiles.json"
        self.configured_model = _clean_optional(configured_model, "model", maximum=200)
        self.default_name = str(default_name or "LimeBot").strip()[:80] or "LimeBot"
        self._lock = threading.RLock()
        self._profiles: dict[str, AgentProfile] = {}
        self._load_or_migrate()

    def _default_profile(self) -> AgentProfile:
        return AgentProfile(profile_id="default", display_name=self.default_name)

    def _load_or_migrate(self) -> None:
        with self._lock:
            if not self.path.exists():
                self._profiles = {"default": self._default_profile()}
                self._write_locked()
                return

            try:
                document = json.loads(self.path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError) as error:
                raise ProfileError(f"could not read profile registry: {error}") from error
            if not isinstance(document, dict) or not isinstance(
                document.get("profiles"), list
            ):
                raise ProfileError("profile registry has an unsupported shape")
            version = document.get("version", 0)
            if (
                isinstance(version, bool)
                or not isinstance(version, int)
                or version not in {0, PROFILE_STORE_VERSION}
            ):
                raise ProfileError(
                    f"unsupported profile registry version: {version!r}"
                )

            profiles: dict[str, AgentProfile] = {}
            for item in document["profiles"]:
                profile = AgentProfile.from_dict(item)
                if profile.profile_id in profiles:
                    raise ProfileError(f"duplicate profile id: {profile.profile_id}")
                profiles[profile.profile_id] = profile

            default_was_missing = "default" not in profiles
            if default_was_missing:
                profiles["default"] = self._default_profile()
            if profiles["default"].archived:
                profiles["default"] = replace(
                    profiles["default"], archived=False, updated_at=time.time()
                )
            self._profiles = profiles
            persisted_default = next(
                (
                    item
                    for item in document["profiles"]
                    if isinstance(item, dict)
                    and item.get("profile_id", item.get("id")) == "default"
                ),
                None,
            )
            if (
                version != PROFILE_STORE_VERSION
                or default_was_missing
                or profiles["default"].to_dict() != persisted_default
            ):
                self._write_locked()

    def _write_locked(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "version": PROFILE_STORE_VERSION,
            "profiles": [
                profile.to_dict()
                for profile in sorted(
                    self._profiles.values(), key=lambda value: value.profile_id
                )
            ],
        }
        descriptor, temporary_name = tempfile.mkstemp(
            prefix=f".{self.path.name}.", suffix=".tmp", dir=self.path.parent
        )
        temporary = Path(temporary_name)
        try:
            with os.fdopen(descriptor, "w", encoding="utf-8", newline="\n") as stream:
                json.dump(payload, stream, indent=2, ensure_ascii=False)
                stream.write("\n")
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary, self.path)
        finally:
            temporary.unlink(missing_ok=True)

    @staticmethod
    def _slug(value: str) -> str:
        normalized = re.sub(r"[^a-z0-9]+", "-", str(value or "").strip().lower())
        return normalized.strip("-")[:40].strip("-") or "agent"

    def list_profiles(self, *, include_archived: bool = False) -> list[AgentProfile]:
        with self._lock:
            profiles = sorted(
                self._profiles.values(),
                key=lambda profile: (profile.archived, profile.display_name.casefold()),
            )
            if not include_archived:
                profiles = [profile for profile in profiles if not profile.archived]
            return list(profiles)

    def get_profile(
        self, profile_id: str = "default", *, include_archived: bool = False
    ) -> AgentProfile:
        normalized = str(profile_id or "default").strip().lower()
        if not PROFILE_ID_RE.fullmatch(normalized):
            raise ProfileError("profile_id must be a lowercase slug")
        with self._lock:
            profile = self._profiles.get(normalized)
            if profile is None:
                raise ProfileError(f"unknown profile: {normalized}")
            if profile.archived and not include_archived:
                raise ProfileError(f"profile is archived: {normalized}")
            return profile

    def resolve_model(self, profile_id: str = "default") -> str:
        profile = self.get_profile(profile_id)
        return profile.model or self.configured_model or "gpt-3.5-turbo"

    def create_profile(
        self,
        display_name: str,
        *,
        profile_id: Optional[str] = None,
        model: Optional[str] = None,
        policy: Optional[AgentProfilePolicy] = None,
    ) -> AgentProfile:
        clean_name = str(display_name or "").strip()
        clean_id = str(profile_id or self._slug(clean_name)).strip().lower()
        now = time.time()
        profile = AgentProfile(
            profile_id=clean_id,
            display_name=clean_name,
            model=model,
            policy=policy or AgentProfilePolicy(),
            created_at=now,
            updated_at=now,
        )
        with self._lock:
            if clean_id in self._profiles:
                raise ProfileError(f"profile already exists: {clean_id}")
            self._profiles[clean_id] = profile
            self._write_locked()
        return profile

    def update_profile(self, profile_id: str, **changes: Any) -> AgentProfile:
        allowed = {"display_name", "model", "policy"}
        unknown = set(changes) - allowed
        if unknown:
            raise ProfileError(f"unsupported profile fields: {', '.join(sorted(unknown))}")
        normalized = str(profile_id or "").strip().lower()
        with self._lock:
            current = self.get_profile(normalized)
            if "policy" in changes and isinstance(changes["policy"], dict):
                changes["policy"] = AgentProfilePolicy.from_dict(changes["policy"])
            updated = replace(current, **changes, updated_at=time.time())
            self._profiles[normalized] = updated
            self._write_locked()
            return updated

    def archive_profile(self, profile_id: str) -> AgentProfile:
        normalized = str(profile_id or "").strip().lower()
        if normalized == "default":
            raise ProfileError("the default profile cannot be archived")
        with self._lock:
            current = self.get_profile(normalized, include_archived=True)
            if current.archived:
                return current
            archived = replace(current, archived=True, updated_at=time.time())
            self._profiles[normalized] = archived
            self._write_locked()
            return archived

    def unarchive_profile(self, profile_id: str) -> AgentProfile:
        normalized = str(profile_id or "").strip().lower()
        with self._lock:
            current = self.get_profile(normalized, include_archived=True)
            if not current.archived:
                return current
            active = replace(current, archived=False, updated_at=time.time())
            self._profiles[normalized] = active
            self._write_locked()
            return active


_registry: Optional[ProfileRegistry] = None
_registry_lock = threading.Lock()


def get_profile_registry(config: Any = None) -> ProfileRegistry:
    """Return the process registry, keeping the default model as inheritance."""
    global _registry
    if _registry is None:
        with _registry_lock:
            if _registry is None:
                llm_config = getattr(config, "llm", None)
                _registry = ProfileRegistry(
                    configured_model=getattr(llm_config, "model", None)
                )
    return _registry
