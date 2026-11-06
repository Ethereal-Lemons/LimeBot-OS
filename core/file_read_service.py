"""Bounded reading and text extraction for allowed local files."""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Callable, List, Optional, Union

from loguru import logger


class FileReadService:
    """Read files while delegating path policy and hashing to Toolbox."""

    def __init__(
        self,
        *,
        is_path_allowed: Callable[[Union[str, Path]], bool],
        resolve_tool_path: Callable[[Union[str, Path]], Path],
        sha256_file_sync: Callable[[Path], str],
    ) -> None:
        self.is_path_allowed = is_path_allowed
        self.resolve_tool_path = resolve_tool_path
        self.sha256_file_sync = sha256_file_sync

    @staticmethod
    def _slice_text_for_read(
        text: str,
        max_chars: int,
        start_line: Optional[int] = None,
        end_line: Optional[int] = None,
    ) -> str:
        """Apply optional line slicing + char truncation to extracted text."""
        if start_line is not None or end_line is not None:
            lines = text.splitlines(keepends=True)
            start_idx = max((start_line or 1) - 1, 0)
            end_idx = end_line if end_line is not None else len(lines)
            text = "".join(lines[start_idx:end_idx])

        if len(text) > max_chars:
            return text[:max_chars] + f"\n... (Truncated at {max_chars} chars)"
        return text

    @staticmethod
    def _extract_docx_text(path: Path) -> str:
        """Extract paragraph and table text from a DOCX file."""
        try:
            from docx import Document
        except Exception as e:
            raise RuntimeError(
                "DOCX support requires 'python-docx'. Install dependencies and retry."
            ) from e

        doc = Document(str(path))
        chunks: List[str] = []

        for paragraph in doc.paragraphs:
            text = paragraph.text.strip()
            if text:
                chunks.append(text)

        for table in doc.tables:
            for row in table.rows:
                cells = [cell.text.strip() for cell in row.cells if cell.text.strip()]
                if cells:
                    chunks.append(" | ".join(cells))

        if chunks:
            return "\n".join(chunks)
        return "No extractable text found in DOCX."

    @staticmethod
    def _extract_pdf_text(path: Path) -> str:
        """Extract text from each page of a PDF."""
        try:
            from pypdf import PdfReader
        except Exception as e:
            raise RuntimeError(
                "PDF support requires 'pypdf'. Install dependencies and retry."
            ) from e

        reader = PdfReader(str(path))
        if reader.is_encrypted:
            try:
                reader.decrypt("")
            except Exception as e:
                raise RuntimeError(
                    "PDF is encrypted and cannot be read without a password."
                ) from e

        chunks: List[str] = []
        for idx, page in enumerate(reader.pages, start=1):
            try:
                page_text = (page.extract_text() or "").strip()
            except Exception as e:
                logger.warning(f"Failed extracting text from PDF page {idx}: {e}")
                continue
            if page_text:
                chunks.append(f"[Page {idx}]\n{page_text}")

        if chunks:
            return "\n\n".join(chunks)
        return "No extractable text found in PDF."

    async def read_file(
        self,
        path: str,
        max_chars: int = 20_000,
        start_line: int = None,
        end_line: int = None,
        include_hash: bool = False,
    ) -> str:
        """
        Read file contents with optional line-range slicing.
        Uses bounded reads to avoid loading huge files into memory.
        """
        if not self.is_path_allowed(path):
            return f"Error: Access denied to path '{path}'."
        p = self.resolve_tool_path(path)
        if not p.exists():
            return f"Error: File '{path}' does not exist."
        if not p.is_file():
            return f"Error: '{path}' is a directory."
        try:
            try:
                max_chars = int(max_chars)
            except Exception:
                max_chars = 20_000
            max_chars = max(200, min(max_chars, 200_000))
            include_hash = include_hash is True or (
                isinstance(include_hash, str)
                and include_hash.strip().lower() in {"1", "true", "yes"}
            )
            file_hash = (
                await asyncio.to_thread(self.sha256_file_sync, p)
                if include_hash
                else None
            )

            def _with_metadata(text: str) -> str:
                if not file_hash:
                    return text
                return f"[File SHA-256: {file_hash}]\n{text}"

            has_range = start_line is not None or end_line is not None
            if has_range:
                if start_line is None:
                    start_line = 1
                try:
                    start_line = int(start_line)
                    end_line = int(end_line) if end_line is not None else None
                except Exception:
                    return "Error: start_line/end_line must be integers."

                if start_line < 1:
                    return "Error: start_line must be >= 1."
                if end_line is not None and end_line < start_line:
                    return "Error: end_line must be >= start_line."

            if p.suffix.lower() in {".docx", ".pdf"}:
                def _read_rich_document() -> str:
                    if p.suffix.lower() == ".docx":
                        extracted = self._extract_docx_text(p)
                    else:
                        extracted = self._extract_pdf_text(p)
                    return self._slice_text_for_read(
                        extracted,
                        max_chars=max_chars,
                        start_line=start_line,
                        end_line=end_line,
                    )

                return _with_metadata(await asyncio.to_thread(_read_rich_document))

            if has_range:
                def _read_line_range() -> str:
                    collected: List[str] = []
                    total = 0
                    truncated = False

                    with open(p, "r", encoding="utf-8", errors="replace") as f:
                        for idx, line in enumerate(f, start=1):
                            if idx < start_line:
                                continue
                            if end_line is not None and idx > end_line:
                                break

                            if total + len(line) > max_chars:
                                remaining = max_chars - total
                                if remaining > 0:
                                    collected.append(line[:remaining])
                                truncated = True
                                break

                            collected.append(line)
                            total += len(line)

                    output = "".join(collected)
                    if truncated:
                        output += f"\n... (Truncated at {max_chars} chars)"
                    return output

                return _with_metadata(await asyncio.to_thread(_read_line_range))

            def _read_bounded() -> str:
                with open(p, "r", encoding="utf-8", errors="replace") as f:
                    chunk = f.read(max_chars + 1)
                if len(chunk) > max_chars:
                    return chunk[:max_chars] + f"\n... (Truncated at {max_chars} chars)"
                return chunk

            return _with_metadata(await asyncio.to_thread(_read_bounded))
        except Exception as e:
            return f"Error reading file: {e}"
