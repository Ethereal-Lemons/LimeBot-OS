"""Filesystem path resolution, allowlist policy, and outbound sharing rules."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Callable, FrozenSet, List, Optional, Set, Tuple, Union

from core.context import workspace_context
from core.profile_context import ProfileRuntimeContext


_SENSITIVE_NAMES = frozenset(
    {
        "limebot.json",
        "package-lock.json",
        "config.py",
        "secrets.py",
        ".env",
        ".env.local",
        ".env.production",
    }
)
_SENSITIVE_EXTENSIONS = frozenset({".pem", ".key", ".p12", ".pfx"})
_OUTBOUND_FORBIDDEN_FILENAMES = frozenset(
    {
        "identity.md",
        "soul.md",
        "memory.md",
        ".env",
        "limebot.json",
        "id_rsa",
        "agents.md",
    }
)


@dataclass(frozen=True)
class ToolPathPolicySnapshot:
    allowed_roots: Tuple[Path, ...]
    blocked_names: FrozenSet[str]
    workspace_root: Optional[Path]
    source_root: Optional[Path]
    persona_root: Path
    profile: Optional[ProfileRuntimeContext]


class ToolPathPolicy:
    """Resolve tool paths against the current workspace and profile allowlists."""

    def __init__(
        self,
        *,
        get_allowed_paths: Callable[[], List[Path]],
        get_blocked_files: Callable[[], Set[str]],
        get_active_workspace_paths: Callable[
            [], Tuple[Optional[Path], Optional[Path]]
        ],
        get_profile_context: Callable[[], Optional[ProfileRuntimeContext]],
        get_persona_root: Callable[[], Path],
    ) -> None:
        self._get_allowed_paths = get_allowed_paths
        self._get_blocked_files = get_blocked_files
        self._get_active_workspace_paths = get_active_workspace_paths
        self._get_profile_context = get_profile_context
        self._get_persona_root = get_persona_root

    @staticmethod
    def active_workspace_paths() -> Tuple[Optional[Path], Optional[Path]]:
        context = workspace_context.get() or {}
        if not isinstance(context, dict):
            return None, None
        raw_root = str(context.get("root") or "").strip()
        raw_source_root = str(context.get("source_root") or "").strip()
        if not raw_root or not raw_source_root:
            return None, None
        try:
            root = Path(raw_root).resolve()
            source_root = Path(raw_source_root).resolve()
        except (OSError, ValueError):
            return None, None
        if not root.is_dir() or not source_root.is_dir():
            return None, None
        return root, source_root

    def _snapshot(self) -> ToolPathPolicySnapshot:
        workspace_root, source_root = self._get_active_workspace_paths()
        return ToolPathPolicySnapshot(
            allowed_roots=tuple(self._get_allowed_paths()),
            blocked_names=frozenset(self._get_blocked_files()),
            workspace_root=workspace_root,
            source_root=source_root,
            persona_root=self._get_persona_root().resolve(),
            profile=self._get_profile_context(),
        )

    @staticmethod
    def _resolve(
        path_str: Union[str, Path],
        *,
        for_write: bool,
        workspace_paths: Tuple[Optional[Path], Optional[Path]],
    ) -> Path:
        """Map source-root paths into an active isolated workspace as needed."""
        raw = Path(path_str).expanduser()
        workspace_root, source_root = workspace_paths
        if workspace_root is None or source_root is None:
            return raw.resolve()

        if raw.is_absolute():
            resolved = raw.resolve()
            if resolved == workspace_root or workspace_root in resolved.parents:
                return resolved
            try:
                relative = resolved.relative_to(source_root)
            except ValueError:
                return resolved
            remapped = (workspace_root / relative).resolve()
            if for_write:
                return remapped
            if remapped.exists() or not resolved.exists():
                return remapped
            return resolved

        # Relative paths that spell a source-root path retain the same
        # read/write remapping behavior as absolute source-root paths.
        try:
            resolved = raw.resolve()
            relative = resolved.relative_to(source_root)
        except (OSError, ValueError):
            relative = None
        if relative is not None:
            remapped = (workspace_root / relative).resolve()
            if for_write:
                return remapped
            if remapped.exists() or not resolved.exists():
                return remapped
            return resolved
        return (workspace_root / raw).resolve()

    def resolve_tool_path(
        self, path_str: Union[str, Path], *, for_write: bool = False
    ) -> Path:
        """Resolve a model-provided path in the active workspace when present."""
        return self._resolve(
            path_str,
            for_write=for_write,
            workspace_paths=self._get_active_workspace_paths(),
        )

    def active_tool_root(self) -> Path:
        workspace_root, _ = self._get_active_workspace_paths()
        allowed_paths = self._get_allowed_paths()
        return workspace_root or (allowed_paths[0] if allowed_paths else Path.cwd())

    @staticmethod
    def _is_within(path: Path, root: Path) -> bool:
        return path == root or root in path.parents

    def _is_allowed(
        self, path_str: Union[str, Path], snapshot: ToolPathPolicySnapshot
    ) -> bool:
        target_path = self._resolve(
            path_str,
            for_write=False,
            workspace_paths=(snapshot.workspace_root, snapshot.source_root),
        )
        name = target_path.name.lower()
        if (
            name in _SENSITIVE_NAMES
            or name in snapshot.blocked_names
            or name.startswith(".env")
            or target_path.suffix.lower() in _SENSITIVE_EXTENSIONS
        ):
            return False

        workspace_root = snapshot.workspace_root
        source_root = snapshot.source_root
        profile_context = snapshot.profile
        protected_profile_roots = (
            [snapshot.persona_root]
            if profile_context is not None and profile_context.profile_id != "default"
            else [(snapshot.persona_root / "profiles").resolve()]
        )
        own_profile_roots: List[Path] = []
        if workspace_root is not None and source_root is not None:
            try:
                relative_profiles = protected_profile_roots[0].relative_to(source_root)
                protected_profile_roots.append((workspace_root / relative_profiles).resolve())
            except ValueError:
                pass
        if profile_context is not None and profile_context.profile_id != "default":
            own_profile_roots.append(profile_context.paths.root.resolve())
            if workspace_root is not None and source_root is not None:
                try:
                    relative_profile = profile_context.paths.root.resolve().relative_to(
                        source_root
                    )
                    own_profile_roots.append((workspace_root / relative_profile).resolve())
                except ValueError:
                    pass

        in_profile_namespace = any(
            self._is_within(target_path, root) for root in protected_profile_roots
        )
        if in_profile_namespace and not any(
            self._is_within(target_path, root) for root in own_profile_roots
        ):
            return False

        globally_allowed = bool(
            workspace_root is not None
            and (
                self._is_within(target_path, workspace_root)
                or (
                    source_root is not None
                    and self._is_within(target_path, source_root)
                )
            )
        ) or any(self._is_within(target_path, allowed) for allowed in snapshot.allowed_roots)
        if not globally_allowed:
            return False

        policy_paths = (
            profile_context.profile.policy.allowed_paths
            if profile_context is not None
            else None
        )
        if policy_paths is not None:
            allowed_by_profile = False
            for allowed_path in policy_paths:
                try:
                    allowed_root = Path(allowed_path).expanduser().resolve()
                except (OSError, ValueError):
                    continue
                if self._is_within(target_path, allowed_root):
                    allowed_by_profile = True
                    break
            if not allowed_by_profile:
                return False
        return True

    def is_path_allowed(self, path_str: Union[str, Path]) -> bool:
        """Return false for malformed paths, sensitive files, or paths outside policy."""
        try:
            return self._is_allowed(path_str, self._snapshot())
        except Exception:
            return False

    def to_display_path(self, path: Path) -> str:
        """Prefer project-relative paths in tool responses."""
        try:
            resolved = path.resolve()
            workspace_root, source_root = self._get_active_workspace_paths()
            if workspace_root is not None and source_root is not None:
                try:
                    resolved = source_root / resolved.relative_to(workspace_root)
                except ValueError:
                    pass
            return str(resolved.relative_to(Path.cwd().resolve()))
        except Exception:
            return str(path.resolve())

    def is_persona_managed_path(self, path: Path) -> bool:
        try:
            resolved = path.resolve()
            persona_root = self._get_persona_root().resolve()
            persona_roots = [persona_root]
            workspace_root, source_root = self._get_active_workspace_paths()
            if workspace_root is not None and source_root is not None:
                try:
                    persona_roots.append(
                        (
                            workspace_root
                            / persona_root.relative_to(source_root)
                        ).resolve()
                    )
                except ValueError:
                    pass
            return any(self._is_within(resolved, root) for root in persona_roots)
        except Exception:
            return False

    def is_path_shareable(
        self, path_str: Union[str, Path]
    ) -> Tuple[bool, Optional[str], Optional[Path]]:
        """Validate that a path is allowed, exists, and contains shareable data."""
        if not self.is_path_allowed(path_str):
            return False, f"Access denied to path '{path_str}'.", None
        try:
            path = self.resolve_tool_path(path_str)
        except Exception:
            return False, f"Invalid path '{path_str}'.", None
        if not path.exists():
            return False, f"File '{path_str}' does not exist.", None
        if not path.is_file():
            return False, f"'{path_str}' is not a file.", None

        lowered_name = path.name.lower()
        if lowered_name in _OUTBOUND_FORBIDDEN_FILENAMES or lowered_name.startswith(".env"):
            return False, f"Blocked sensitive file '{path.name}'.", None
        if "persona" in {part.lower() for part in path.parts}:
            return False, "Blocked files inside the persona directory.", None
        return True, None, path
