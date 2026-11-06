"""Bounded file writes, exact edits, deletion, and verification."""

from __future__ import annotations

import asyncio
import hashlib
import json
import re
import subprocess
import shutil
from pathlib import Path
from typing import Any, Callable, Dict, List, Union

from loguru import logger

from core.file_edits import EditValidationError, apply_text_edits, unified_text_diff
from core.redaction import redact_sensitive_text


class FileMutationService:
    """Own file mutation and verification behavior; delegate policy to Toolbox."""

    def __init__(
        self,
        *,
        is_path_allowed: Callable[[Union[str, Path]], bool],
        resolve_tool_path: Callable[..., Path],
        is_persona_managed_path: Callable[[Path], bool],
        persona_write_error: Callable[[], str],
        to_display_path: Callable[[Path], str],
        atomic_write_text_if_unchanged: Callable[[Path, str, str], None],
        verify_text_syntax: Callable[[Path, str], Dict[str, Any]],
        verify_git_diff_check: Callable[[Path], Dict[str, Any]],
        verify_files: Callable[..., Any],
        diagnose_files: Callable[..., Any],
        active_tool_root: Callable[[], Path],
        sanitized_env: Callable[[], Dict[str, str]],
    ) -> None:
        self.is_path_allowed = is_path_allowed
        self.resolve_tool_path = resolve_tool_path
        self.is_persona_managed_path = is_persona_managed_path
        self.persona_write_error = persona_write_error
        self.to_display_path = to_display_path
        self.atomic_write_text_if_unchanged = atomic_write_text_if_unchanged
        self._verify_text_syntax = verify_text_syntax
        self._verify_git_diff_check = verify_git_diff_check
        self._verify_files = verify_files
        self._diagnose_files = diagnose_files
        self.active_tool_root = active_tool_root
        self.sanitized_env = sanitized_env

    async def write_file(self, path: str, content: str) -> str:
        """Write content to a file after applying Toolbox path policy."""
        if not self.is_path_allowed(path):
            return f"Error: Access denied to path '{path}'."
        target = self.resolve_tool_path(path, for_write=True)
        if self.is_persona_managed_path(target):
            return self.persona_write_error()

        try:
            await asyncio.to_thread(target.parent.mkdir, parents=True, exist_ok=True)
            await asyncio.to_thread(target.write_text, content, encoding="utf-8")
            return f"Successfully wrote to '{path}'."
        except Exception as exc:
            return f"Error writing file: {exc}"

    async def edit_file(
        self,
        path: str,
        edits: Any,
        expected_sha256: str,
    ) -> str:
        """Apply exact, hash-guarded text edits atomically."""
        if not self.is_path_allowed(path):
            return f"Error: Access denied to path '{path}'."

        target = self.resolve_tool_path(path, for_write=True)
        if self.is_persona_managed_path(target):
            return self.persona_write_error()
        if not target.exists():
            return f"Error: File '{path}' does not exist; use write_file to create it."
        if not target.is_file():
            return f"Error: '{path}' is not a file."

        expected = str(expected_sha256 or "").strip().lower()
        if not re.fullmatch(r"[0-9a-f]{64}", expected):
            return (
                "Error: edit_file requires expected_sha256 from "
                "read_file(include_hash=true)."
            )

        try:
            size = target.stat().st_size
            if size > 5 * 1024 * 1024:
                return "Error: edit_file only accepts text files up to 5 MiB."

            original_bytes = await asyncio.to_thread(target.read_bytes)
            current_sha256 = hashlib.sha256(original_bytes).hexdigest()
            original = original_bytes.decode("utf-8")
            if current_sha256 != expected:
                from core.file_edits import intended_edits_already_present

                if intended_edits_already_present(original, edits):
                    return json.dumps(
                        {
                            "status": "already_applied",
                            "path": self.to_display_path(target),
                            "replacements": 0,
                            "sha256_before": current_sha256,
                            "sha256_after": current_sha256,
                            "detail": "Skipped stale edit; the intended text is already on disk.",
                        },
                        ensure_ascii=False,
                    )
                return (
                    "Error: Stale edit rejected. The file's SHA-256 is "
                    f"{current_sha256}, not {expected}; re-read the file and retry."
                )
        except UnicodeDecodeError:
            return "Error: edit_file only supports UTF-8 text files."
        except OSError as exc:
            return f"Error reading file for edit: {exc}"

        try:
            updated, replacement_count = apply_text_edits(original, edits)
        except EditValidationError as exc:
            from core.file_edits import intended_edits_already_present

            if intended_edits_already_present(original, edits):
                current_sha256 = hashlib.sha256(original.encode("utf-8")).hexdigest()
                return json.dumps(
                    {
                        "status": "already_applied",
                        "path": self.to_display_path(target),
                        "replacements": 0,
                        "sha256_before": current_sha256,
                        "sha256_after": current_sha256,
                        "detail": "Skipped unusable edit; the intended text is already on disk.",
                    },
                    ensure_ascii=False,
                )
            return f"Error: Edit rejected: {exc}"

        if replacement_count == 0 or updated == original:
            current_sha256 = hashlib.sha256(original.encode("utf-8")).hexdigest()
            return json.dumps(
                {
                    "status": "already_applied",
                    "path": self.to_display_path(target),
                    "replacements": 0,
                    "sha256_before": current_sha256,
                    "sha256_after": current_sha256,
                    "detail": "No file change; intended text is already on disk.",
                },
                ensure_ascii=False,
            )

        diff = unified_text_diff(
            original,
            updated,
            fromfile=f"{self.to_display_path(target)} (before)",
            tofile=f"{self.to_display_path(target)} (after)",
        )
        try:
            await asyncio.to_thread(
                self.atomic_write_text_if_unchanged,
                target,
                expected,
                updated,
            )
        except EditValidationError as exc:
            return f"Error: Edit rejected: {exc}"
        except OSError as exc:
            return f"Error writing file edit: {exc}"

        after_sha256 = hashlib.sha256(updated.encode("utf-8")).hexdigest()
        diff_lines = diff.splitlines()
        added_lines = sum(
            1
            for line in diff_lines
            if line.startswith("+") and not line.startswith("+++")
        )
        removed_lines = sum(
            1
            for line in diff_lines
            if line.startswith("-") and not line.startswith("---")
        )
        verification_raw = await self._verify_files([str(target)])
        try:
            verification: Any = json.loads(verification_raw)
        except (TypeError, json.JSONDecodeError):
            verification = {"status": "unavailable", "detail": verification_raw}
        return json.dumps(
            {
                "status": "applied",
                "path": self.to_display_path(target),
                "replacements": replacement_count,
                "added_lines": added_lines,
                "removed_lines": removed_lines,
                "sha256_before": expected,
                "sha256_after": after_sha256,
                "diff": diff,
                "verification": verification,
            },
            ensure_ascii=False,
        )

    @staticmethod
    def verify_text_syntax(path: Path, text: str) -> Dict[str, Any]:
        suffix = path.suffix.lower()
        try:
            if suffix in {".py", ".pyi"}:
                compile(text, str(path), "exec")
                return {"name": "python_syntax", "status": "passed"}
            if suffix == ".json":
                json.loads(text)
                return {"name": "json_syntax", "status": "passed"}
            if suffix == ".toml":
                try:
                    import tomllib
                except ImportError:
                    return {"name": "toml_syntax", "status": "skipped"}
                tomllib.loads(text)
                return {"name": "toml_syntax", "status": "passed"}
        except SyntaxError as exc:
            line = f" line {exc.lineno}" if exc.lineno else ""
            return {
                "name": f"{suffix.lstrip('.') or 'text'}_syntax",
                "status": "failed",
                "detail": f"{exc.msg}{line}",
            }
        except (ValueError, TypeError) as exc:
            return {
                "name": f"{suffix.lstrip('.') or 'text'}_syntax",
                "status": "failed",
                "detail": str(exc),
            }
        return {"name": "syntax", "status": "skipped"}

    @staticmethod
    def verify_git_diff_check_sync(path: Path) -> Dict[str, Any]:
        root = Path.cwd().resolve()
        try:
            relative = path.resolve().relative_to(root)
        except ValueError:
            return {
                "name": "git_diff_check",
                "status": "skipped",
                "detail": "target is outside the project root",
            }

        try:
            completed = subprocess.run(
                ["git", "diff", "--check", "--", str(relative)],
                cwd=str(root),
                capture_output=True,
                text=True,
                timeout=10,
                check=False,
            )
        except (OSError, subprocess.SubprocessError) as exc:
            return {
                "name": "git_diff_check",
                "status": "skipped",
                "detail": f"git diff check unavailable: {exc}",
            }

        if completed.returncode == 0:
            return {"name": "git_diff_check", "status": "passed"}
        detail = (completed.stdout or completed.stderr or "git diff --check failed").strip()
        return {
            "name": "git_diff_check",
            "status": "failed",
            "detail": redact_sensitive_text(detail[:1_200]),
        }

    async def verify_files(
        self,
        paths: List[str],
        include_diagnostics: bool = False,
        provider: str = "auto",
    ) -> str:
        """Run bounded, read-only checks for changed files."""
        if not isinstance(paths, list) or not paths:
            return "Error: verify_files requires a non-empty paths array."
        if len(paths) > 32:
            return "Error: verify_files accepts at most 32 paths."

        file_results: List[Dict[str, Any]] = []
        for raw_path in paths:
            display_input = str(raw_path or "")
            if not self.is_path_allowed(display_input):
                file_results.append(
                    {"path": display_input, "status": "failed", "errors": ["access denied"]}
                )
                continue
            target = self.resolve_tool_path(display_input)
            if not target.exists() or not target.is_file():
                file_results.append(
                    {
                        "path": self.to_display_path(target),
                        "status": "failed",
                        "errors": ["file does not exist or is not a regular file"],
                    }
                )
                continue

            try:
                if target.stat().st_size > 5 * 1024 * 1024:
                    raise ValueError("file exceeds the 5 MiB verification limit")
                raw_bytes = await asyncio.to_thread(target.read_bytes)
                text = raw_bytes.decode("utf-8")
            except UnicodeDecodeError:
                file_results.append(
                    {
                        "path": self.to_display_path(target),
                        "status": "failed",
                        "errors": ["file is not valid UTF-8 text"],
                    }
                )
                continue
            except (OSError, ValueError) as exc:
                file_results.append(
                    {
                        "path": self.to_display_path(target),
                        "status": "failed",
                        "errors": [str(exc)],
                    }
                )
                continue

            checks: List[Dict[str, Any]] = []
            marker_lines = [
                index
                for index, line in enumerate(text.splitlines(), start=1)
                if re.match(r"^\s*(?:<<<<<<<|=======|>>>>>>>)", line)
            ]
            if marker_lines:
                checks.append(
                    {
                        "name": "conflict_markers",
                        "status": "failed",
                        "detail": f"marker(s) at line(s) {', '.join(map(str, marker_lines[:8]))}",
                    }
                )
            else:
                checks.append({"name": "conflict_markers", "status": "passed"})
            checks.append(self._verify_text_syntax(target, text))
            checks.append(await asyncio.to_thread(self._verify_git_diff_check, target))
            failures = [check for check in checks if check.get("status") == "failed"]
            file_results.append(
                {
                    "path": self.to_display_path(target),
                    "status": "failed" if failures else "passed",
                    "checks": checks,
                }
            )

        overall = (
            "failed"
            if any(row.get("status") == "failed" for row in file_results)
            else "passed"
        )
        payload: Dict[str, Any] = {"status": overall, "files": file_results}
        diagnostics_requested = include_diagnostics is True or (
            isinstance(include_diagnostics, str)
            and include_diagnostics.strip().lower() in {"1", "true", "yes"}
        )
        if diagnostics_requested:
            diagnostics_raw = await self._diagnose_files(paths, provider=provider)
            try:
                diagnostics = json.loads(diagnostics_raw)
            except (TypeError, json.JSONDecodeError):
                diagnostics = {"status": "failed", "detail": diagnostics_raw}
            payload["diagnostics"] = diagnostics
            if diagnostics.get("status") == "failed":
                payload["status"] = "failed"
        return json.dumps(payload, ensure_ascii=False)

    async def diagnose_files(
        self,
        paths: List[str],
        provider: str = "auto",
        timeout: float = 45,
    ) -> str:
        """Use installed optional lint and type-check providers when available."""
        if not isinstance(paths, list) or not paths:
            return "Error: diagnose_files requires a non-empty paths array."
        if len(paths) > 32:
            return "Error: diagnose_files accepts at most 32 paths."

        targets: List[Path] = []
        invalid_paths: List[Dict[str, str]] = []
        for raw_path in paths:
            display_input = str(raw_path or "")
            if not self.is_path_allowed(display_input):
                invalid_paths.append({"path": display_input, "detail": "access denied"})
                continue
            target = self.resolve_tool_path(display_input)
            if not target.exists() or not target.is_file():
                invalid_paths.append(
                    {
                        "path": self.to_display_path(target),
                        "detail": "file does not exist or is not a regular file",
                    }
                )
                continue
            targets.append(target)

        if not targets:
            return json.dumps(
                {
                    "status": "failed",
                    "provider": str(provider or "auto"),
                    "results": [],
                    "invalid_paths": invalid_paths,
                },
                ensure_ascii=False,
            )

        try:
            timeout_value = max(1.0, min(float(timeout), 120.0))
        except (TypeError, ValueError):
            timeout_value = 45.0

        try:
            from core.diagnostics import run_optional_diagnostics

            payload = await run_optional_diagnostics(
                targets,
                self.active_tool_root(),
                provider=provider,
                env=self.sanitized_env(),
                timeout=timeout_value,
            )
        except Exception as exc:
            logger.warning(f"Optional diagnostics failed to start: {exc}")
            payload = {
                "status": "failed",
                "provider": str(provider or "auto"),
                "results": [],
                "detail": str(exc),
            }

        payload["paths"] = [self.to_display_path(target) for target in targets]
        if invalid_paths:
            payload["invalid_paths"] = invalid_paths
            payload["status"] = "failed"
        for result in payload.get("results", []):
            for key in ("detail", "output"):
                if key in result:
                    result[key] = redact_sensitive_text(str(result[key])[:8_000])
        return json.dumps(payload, ensure_ascii=False)

    async def delete_file(self, path: str) -> str:
        """Delete a file or directory after applying Toolbox path policy."""
        if not self.is_path_allowed(path):
            return f"Error: Access denied to path '{path}'."
        target = self.resolve_tool_path(path, for_write=True)
        if self.is_persona_managed_path(target):
            return (
                "Error: Direct deletion of state-managed files under 'persona/' is blocked. "
                "Please use the appropriate XML tags to manage your state."
            )

        if not target.exists():
            return f"Error: Path '{path}' does not exist."
        try:
            if target.is_file():
                await asyncio.to_thread(target.unlink)
            elif target.is_dir():
                await asyncio.to_thread(shutil.rmtree, target)
            return f"Successfully deleted '{path}'."
        except Exception as exc:
            return f"Error deleting: {exc}"
