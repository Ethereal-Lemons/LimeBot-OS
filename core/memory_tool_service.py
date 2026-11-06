"""Memory tool operations over the profile-scoped Markdown source of truth."""

from __future__ import annotations

import asyncio
import os
import uuid
from datetime import datetime
from pathlib import Path
from typing import Any, Optional

from loguru import logger


class MemoryToolService:
    """Implement memory search and save with an explicit storage boundary."""

    async def search(self, query: str, vector_service: Any) -> str:
        if not vector_service:
            return "Error: Vector service not available."
        try:
            results = await vector_service.search(query, limit=5) or []
            if not results:
                return "No matching memory found in MEMORY.md or the Markdown journals."

            mode = getattr(vector_service, "_last_search_mode", None)
            if mode == "vector":
                lines = ["Found relevant memories (semantic vector search):"]
            else:
                lines = ["Found relevant memories (Markdown fallback):"]
            for result in results:
                text = result.get("text", "No content")
                score = result.get("score")
                if score is None:
                    score = result.get("_distance", 0)
                path = result.get("path") or result.get("source") or "Unknown source"
                lines.append(f"- {text}\n  (Source: {path}, Score: {score})")
            return "\n\n".join(lines)
        except Exception as error:
            return f"Error searching memory: {error}"

    async def save(
        self,
        content: str,
        scope: str,
        *,
        memory_dir: Path,
        long_term_memory_file: Path,
        vector_service: Optional[Any],
    ) -> str:
        """Persist the note before queuing optional vector indexing."""
        entry = str(content or "").strip()
        if not entry:
            return "Error: memory content cannot be empty."
        if len(entry) > 4_000:
            return "Error: memory content is too long (maximum 4000 characters)."

        normalized_scope = str(scope or "journal").strip().lower()
        if normalized_scope not in {"journal", "long_term"}:
            return "Error: scope must be 'journal' or 'long_term'."

        now = datetime.now()
        try:
            def atomic_write_text(target: Path, payload: str) -> None:
                temporary = target.with_name(f".{target.name}.{uuid.uuid4().hex}.tmp")
                try:
                    temporary.write_text(payload, encoding="utf-8")
                    os.replace(temporary, target)
                finally:
                    if temporary.exists():
                        temporary.unlink(missing_ok=True)

            if normalized_scope == "journal":
                await asyncio.to_thread(memory_dir.mkdir, parents=True, exist_ok=True)
                memory_file = memory_dir / f"{now:%Y-%m-%d}.md"
                line = f"- **[{now:%H:%M}]** {entry}"

                def append_if_new() -> bool:
                    existing = (
                        memory_file.read_text(encoding="utf-8")
                        if memory_file.exists()
                        else ""
                    )
                    if line in existing:
                        return False
                    prefix = "" if not existing or existing.endswith("\n") else "\n"
                    atomic_write_text(memory_file, f"{existing}{prefix}{line}\n")
                    return True

                written = await asyncio.to_thread(append_if_new)
                category = "journal"
                display_path = f"memory/{memory_file.name}"
            else:
                await asyncio.to_thread(
                    long_term_memory_file.parent.mkdir,
                    parents=True,
                    exist_ok=True,
                )
                memory_file = long_term_memory_file
                bullet = f"- {entry}"

                def append_long_term_if_new() -> bool:
                    existing = (
                        memory_file.read_text(encoding="utf-8").rstrip()
                        if memory_file.exists()
                        else "# Long-Term Memory"
                    )
                    if bullet in existing:
                        return False
                    separator = "\n" if existing.endswith("\n") else "\n\n"
                    atomic_write_text(memory_file, f"{existing}{separator}{bullet}\n")
                    return True

                written = await asyncio.to_thread(append_long_term_if_new)
                category = "long_term"
                display_path = "MEMORY.md"

            if written and vector_service:
                try:
                    asyncio.create_task(vector_service.add_entry(entry, category=category))
                except RuntimeError:
                    # Markdown remains the source of truth if indexing is unavailable.
                    pass
            status = "saved" if written else "already present"
            return f"Memory {status} in {display_path}."
        except Exception as error:
            logger.error(f"Error saving memory: {error}")
            return f"Error saving memory: {error}"
