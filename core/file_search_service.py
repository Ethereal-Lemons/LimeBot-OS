"""Read-only filename and content search over allowed workspace paths."""

from __future__ import annotations

import asyncio
import json
import os
import shutil
from pathlib import Path
from typing import Any, Callable, Dict, List, Union

from loguru import logger

_RG_EXCLUDE_GLOBS = (
    "!.git/**",
    "!node_modules/**",
    "!__pycache__/**",
    "!*.pyc",
    "!.env*",
    "!limebot.json",
    "!config.py",
    "!secrets.py",
    "!package-lock.json",
    "!*.pem",
    "!*.key",
    "!*.p12",
    "!*.pfx",
)


class FileSearchService:
    """List and search allowed paths while delegating path policy to Toolbox."""

    def __init__(
        self,
        *,
        is_path_allowed: Callable[[Union[str, Path]], bool],
        resolve_tool_path: Callable[[Union[str, Path]], Path],
        to_display_path: Callable[[Path], str],
    ) -> None:
        self.is_path_allowed = is_path_allowed
        self.resolve_tool_path = resolve_tool_path
        self.to_display_path = to_display_path

    def _format_search_results(self, rows: List[Dict[str, Any]], query: str) -> str:
        if not rows:
            return json.dumps({"query": query, "matches": []})

        formatted_rows = []
        for row in rows:
            path = row.get("path", "unknown")
            line = row.get("line")
            text = (row.get("text") or "").replace("\t", " ").strip()
            if len(text) > 220:
                text = text[:220] + "..."
            formatted_rows.append({"path": path, "line": line, "text": text})

        return json.dumps({"query": query, "matches": formatted_rows})

    async def list_directory(
        self,
        path: str = ".",
        limit: int = 200,
        offset: int = 0,
        include_hidden: bool = False,
        sort_by: str = "name",
        descending: bool = False,
        folders_first: bool = True,
    ) -> str:
        """List an allowed directory with bounded pagination and sorting."""
        if not self.is_path_allowed(path):
            return f"Error: Access denied to path '{path}'."
        target = self.resolve_tool_path(path)
        if not target.exists():
            return f"Error: Directory '{path}' does not exist."
        if not target.is_dir():
            return f"Error: Path '{path}' is not a directory."
        try:
            try:
                limit = int(limit)
            except Exception:
                limit = 200
            limit = max(1, min(limit, 1000))

            try:
                offset = int(offset)
            except Exception:
                offset = 0
            offset = max(0, offset)

            sort_by = (sort_by or "name").strip().lower()
            if sort_by not in {"name", "type", "mtime", "size", "none"}:
                return "Error: sort_by must be one of: name, type, mtime, size, none."

            def _scan_dir() -> List[Dict[str, Any]]:
                entries: List[Dict[str, Any]] = []
                with os.scandir(target) as iterator:
                    for entry in iterator:
                        name = entry.name
                        if not include_hidden and name.startswith("."):
                            continue

                        try:
                            is_dir = entry.is_dir(follow_symlinks=False)
                        except Exception:
                            is_dir = False

                        record: Dict[str, Any] = {"name": name, "is_dir": is_dir}
                        if sort_by in {"mtime", "size"}:
                            try:
                                stat_result = entry.stat(follow_symlinks=False)
                                record["mtime"] = stat_result.st_mtime
                                record["size"] = 0 if is_dir else stat_result.st_size
                            except Exception:
                                record["mtime"] = 0
                                record["size"] = 0
                        entries.append(record)
                return entries

            entries = await asyncio.to_thread(_scan_dir)
            entries = [
                entry
                for entry in entries
                if self.is_path_allowed(target / entry["name"])
            ]

            def _key_for(record: Dict[str, Any]):
                if sort_by == "mtime":
                    return record.get("mtime", 0)
                if sort_by == "size":
                    return record.get("size", 0)
                return record["name"].lower()

            if sort_by == "type":
                entries.sort(
                    key=lambda record: (
                        0 if record["is_dir"] else 1,
                        record["name"].lower(),
                    ),
                    reverse=descending,
                )
            elif sort_by != "none":
                if folders_first:
                    directories = [record for record in entries if record["is_dir"]]
                    files = [record for record in entries if not record["is_dir"]]
                    directories.sort(key=_key_for, reverse=descending)
                    files.sort(key=_key_for, reverse=descending)
                    entries = directories + files
                else:
                    entries.sort(key=_key_for, reverse=descending)
            elif folders_first:
                directories = [record for record in entries if record["is_dir"]]
                files = [record for record in entries if not record["is_dir"]]
                entries = directories + files

            total = len(entries)
            page = entries[offset : offset + limit]
            if not page:
                return f"No entries in page (offset={offset}, total={total})."

            start = offset + 1
            end = offset + len(page)
            header = (
                f"Listing '{self.to_display_path(target)}' ({start}-{end} of {total})"
            )
            if end < total:
                header += f" — more entries available (next offset: {end})"

            lines = [header]
            for record in page:
                type_str = "DIR" if record["is_dir"] else "FILE"
                lines.append(f"[{type_str}] {record['name']}")
            return "\n".join(lines)
        except Exception as exc:
            return f"Error listing directory: {exc}"

    async def search_files(
        self,
        query: str,
        path: str = ".",
        file_glob: str = "*",
        mode: str = "content",
        case_sensitive: bool = False,
        max_results: int = 40,
    ) -> str:
        """
        Fast project search for file names or file content.
        Uses ripgrep when available, with a safe Python fallback.
        """
        query = (query or "").strip()
        if not query:
            return "Error: 'query' is required."
        if len(query) > 256:
            return "Error: query is too long (max 256 chars)."

        mode = (mode or "content").strip().lower()
        if mode not in {"content", "name"}:
            return "Error: mode must be either 'content' or 'name'."

        try:
            max_results = max(1, min(int(max_results), 200))
        except Exception:
            max_results = 40

        if not self.is_path_allowed(path):
            return f"Error: Access denied to path '{path}'."

        root = self.resolve_tool_path(path)
        if not root.exists():
            return f"Error: Path '{path}' does not exist."

        try:
            if mode == "name":
                rows = await asyncio.to_thread(
                    self._search_file_names_sync,
                    root,
                    query,
                    file_glob,
                    case_sensitive,
                    max_results,
                )
            else:
                rows = await asyncio.to_thread(
                    self._search_file_content_sync,
                    root,
                    query,
                    file_glob,
                    case_sensitive,
                    max_results,
                )
            return self._format_search_results(rows, query)
        except Exception as e:
            return f"Error searching files: {e}"

    def _search_file_names_sync(
        self,
        root: Path,
        query: str,
        file_glob: str,
        case_sensitive: bool,
        max_results: int,
    ) -> List[Dict[str, Any]]:
        """Sync helper for filename search."""
        if not root.is_dir():
            if root.is_file() and self.is_path_allowed(root):
                name = root.name
                hit = query in name if case_sensitive else query.lower() in name.lower()
                if hit:
                    return [
                        {"path": self.to_display_path(root), "line": None, "text": ""}
                    ]
            return []

        q = query if case_sensitive else query.lower()
        rows: List[Dict[str, Any]] = []

        for dirpath, dirnames, filenames in os.walk(root):
            dirnames[:] = [
                d
                for d in dirnames
                if d
                not in {
                    ".git",
                    "node_modules",
                    "__pycache__",
                    ".venv",
                    "venv",
                    "env",
                    ".mypy_cache",
                    ".vercel",
                    ".next",
                    ".idea",
                    ".vscode",
                }
            ]
            for filename in filenames:
                if len(rows) >= max_results:
                    break
                file_path = Path(dirpath) / filename
                if file_glob and file_glob != "*" and not file_path.match(file_glob):
                    continue
                if not self.is_path_allowed(file_path):
                    continue
                hay = filename if case_sensitive else filename.lower()
                if q in hay:
                    rows.append(
                        {
                            "path": self.to_display_path(file_path),
                            "line": None,
                            "text": "",
                        }
                    )
            if len(rows) >= max_results:
                break
        return rows

    def _search_file_content_sync(
        self,
        root: Path,
        query: str,
        file_glob: str,
        case_sensitive: bool,
        max_results: int,
    ) -> List[Dict[str, Any]]:
        """Sync helper for content search; prefers ripgrep for speed."""
        rg_bin = shutil.which("rg")
        if rg_bin:
            try:
                import subprocess

                cmd = [
                    rg_bin,
                    "--json",
                    "--line-number",
                    "--color",
                    "never",
                    "--max-count",
                    "3",
                ]
                if not case_sensitive:
                    cmd.append("-i")
                if file_glob and file_glob != "*":
                    cmd.extend(["-g", file_glob])
                for g in _RG_EXCLUDE_GLOBS:
                    cmd.extend(["-g", g])
                cmd.extend(["--", query, str(root)])

                completed = subprocess.run(
                    cmd,
                    capture_output=True,
                    text=True,
                    encoding="utf-8",
                    errors="replace",
                    timeout=20,
                )

                # ripgrep exit codes: 0=matches, 1=no matches, >1 error
                if completed.returncode not in (0, 1):
                    raise RuntimeError(completed.stderr.strip() or "ripgrep failed")

                rows: List[Dict[str, Any]] = []
                for line in completed.stdout.splitlines():
                    if len(rows) >= max_results:
                        break
                    try:
                        payload = json.loads(line)
                    except Exception:
                        continue
                    if payload.get("type") != "match":
                        continue
                    data = payload.get("data", {})
                    path_text = (
                        data.get("path", {}).get("text")
                        or data.get("path", {}).get("bytes")
                        or ""
                    )
                    if not path_text:
                        continue
                    file_path = self.resolve_tool_path(path_text)
                    if not self.is_path_allowed(file_path):
                        continue

                    row_text = (data.get("lines", {}).get("text") or "").rstrip("\n")
                    rows.append(
                        {
                            "path": self.to_display_path(file_path),
                            "line": data.get("line_number"),
                            "text": row_text,
                        }
                    )
                return rows
            except Exception as e:
                logger.debug(f"search_files ripgrep path failed; falling back: {e}")

        # Fallback: Python scan
        rows: List[Dict[str, Any]] = []
        q = query if case_sensitive else query.lower()

        if root.is_file():
            candidates = [root]
        else:
            candidates = []
            for dirpath, dirnames, filenames in os.walk(root):
                dirnames[:] = [
                    d
                    for d in dirnames
                    if d
                    not in {
                        ".git",
                        "node_modules",
                        "__pycache__",
                        ".venv",
                        "venv",
                        "env",
                        ".mypy_cache",
                        ".vercel",
                        ".next",
                        ".idea",
                        ".vscode",
                    }
                ]
                for filename in filenames:
                    p = Path(dirpath) / filename
                    if file_glob and file_glob != "*" and not p.match(file_glob):
                        continue
                    candidates.append(p)

        for file_path in candidates:
            if len(rows) >= max_results:
                break
            try:
                if not file_path.is_file() or not self.is_path_allowed(file_path):
                    continue

                # Skip files larger than 5MB to prevent stalling
                try:
                    if file_path.stat().st_size > 5_000_000:
                        continue
                except Exception:
                    pass

                with open(file_path, "r", encoding="utf-8", errors="ignore") as f:
                    for idx, line in enumerate(f, start=1):
                        hay = line if case_sensitive else line.lower()
                        if q in hay:
                            rows.append(
                                {
                                    "path": self.to_display_path(file_path),
                                    "line": idx,
                                    "text": line.rstrip("\n"),
                                }
                            )
                            break
                        if len(rows) >= max_results:
                            break
            except Exception:
                continue
        return rows
