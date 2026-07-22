import asyncio
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from core.vectors import EmbeddingCandidate, VectorService


def _service(tmp_path: Path) -> VectorService:
    service = VectorService(
        db_path=str(tmp_path / "vectors"),
        candidate_models=[EmbeddingCandidate("disabled", "test")],
    )
    service._config = SimpleNamespace(llm=SimpleNamespace())
    return service


def test_markdown_fallback_searches_long_term_and_daily_memory(tmp_path):
    memory_dir = tmp_path / "memory"
    memory_dir.mkdir()
    long_term = tmp_path / "MEMORY.md"
    long_term.write_text(
        "# Long-Term Memory\n- Example User prefers concise answers.\n- Guatemala está in Central America.\n",
        encoding="utf-8",
    )
    (memory_dir / "2026-07-21.md").write_text(
        "- The universidad report is pending.\n",
        encoding="utf-8",
    )

    service = _service(tmp_path)
    with patch("core.vectors.LONG_TERM_MEMORY_FILE", long_term), patch(
        "core.vectors.MEMORY_DIR", memory_dir
    ):
        results = asyncio.run(service.search("universidad report", limit=5))
        long_term_results = asyncio.run(
            service.search_grep("Example User concise", limit=5)
        )
        accent_results = asyncio.run(service.search_grep("Guatemala esta", limit=5))

    assert results
    assert results[0]["text"] == "- The universidad report is pending."
    assert results[0]["source"] == "memory/2026-07-21.md"
    assert long_term_results
    assert long_term_results[0]["source"] == "MEMORY.md"
    assert accent_results
    assert accent_results[0]["text"] == "- Guatemala está in Central America."
    assert service.get_embedding_status()["last_search_mode"] == "markdown"


def test_markdown_fallback_cache_invalidates_when_memory_changes(tmp_path):
    memory_dir = tmp_path / "memory"
    memory_dir.mkdir()
    long_term = tmp_path / "MEMORY.md"
    long_term.write_text("# Long-Term Memory\n", encoding="utf-8")
    journal = memory_dir / "2026-07-21.md"
    journal.write_text("- first note\n", encoding="utf-8")

    service = _service(tmp_path)
    with patch("core.vectors.LONG_TERM_MEMORY_FILE", long_term), patch(
        "core.vectors.MEMORY_DIR", memory_dir
    ):
        assert asyncio.run(service.search_grep("first note", limit=5))
        journal.write_text("- second note with a different length\n", encoding="utf-8")
        results = asyncio.run(service.search_grep("second note", limit=5))

    assert results
    assert results[0]["text"] == "- second note with a different length"


def test_memory_search_tool_explains_markdown_fallback(tmp_path):
    from core.tools import Toolbox

    memory_dir = tmp_path / "memory"
    memory_dir.mkdir()
    long_term = tmp_path / "MEMORY.md"
    long_term.write_text("- Project LimeBot uses Markdown memory.\n", encoding="utf-8")

    service = _service(tmp_path)
    toolbox = object.__new__(Toolbox)
    toolbox.vector_service = service

    with patch("core.vectors.LONG_TERM_MEMORY_FILE", long_term), patch(
        "core.vectors.MEMORY_DIR", memory_dir
    ):
        output = asyncio.run(toolbox.memory_search("LimeBot Markdown"))

    assert "Markdown fallback" in output
    assert "MEMORY.md" in output
    assert "Project LimeBot uses Markdown memory" in output


def test_markdown_listing_reads_the_same_source_used_by_the_explorer(tmp_path):
    memory_dir = tmp_path / "memory"
    memory_dir.mkdir()
    long_term = tmp_path / "MEMORY.md"
    long_term.write_text(
        "# Long-Term Memory\n"
        "(This file will be automatically populated as you learn about yourself.)\n"
        "- A durable project fact.\n",
        encoding="utf-8",
    )
    (memory_dir / "2026-07-21.md").write_text(
        "- A dated journal event.\n", encoding="utf-8"
    )

    service = _service(tmp_path)
    with patch("core.vectors.LONG_TERM_MEMORY_FILE", long_term), patch(
        "core.vectors.MEMORY_DIR", memory_dir
    ):
        entries = asyncio.run(service.get_markdown_entries())

    assert [entry["source"] for entry in entries] == [
        "MEMORY.md",
        "memory/2026-07-21.md",
    ]
    assert all("automatically populated" not in entry["text"] for entry in entries)
    assert all(entry["id"] for entry in entries)


def test_native_memory_save_writes_without_embeddings(tmp_path):
    from core.tools import Toolbox

    memory_dir = tmp_path / "memory"
    long_term = tmp_path / "MEMORY.md"
    toolbox = object.__new__(Toolbox)
    toolbox.vector_service = None

    with patch("core.tools.MEMORY_DIR", memory_dir), patch(
        "core.tools.LONG_TERM_MEMORY_FILE", long_term
    ):
        journal_result = asyncio.run(
            toolbox.memory_save("The user prefers concise status updates.")
        )
        long_term_result = asyncio.run(
            toolbox.memory_save(
                "The project uses Markdown as the memory source of truth.",
                scope="long_term",
            )
        )
        duplicate_result = asyncio.run(
            toolbox.memory_save(
                "The project uses Markdown as the memory source of truth.",
                scope="long_term",
            )
        )

    assert "saved" in journal_result
    assert "saved" in long_term_result
    assert "already present" in duplicate_result
    assert "concise status updates" in next(memory_dir.glob("*.md")).read_text(
        encoding="utf-8"
    )
    assert "Markdown as the memory source" in long_term.read_text(encoding="utf-8")


def test_web_memory_route_falls_back_when_vector_index_is_empty():
    from fastapi.testclient import TestClient
    from channels.web import WebChannel
    from core.bus import MessageBus

    service = SimpleNamespace(
        is_enabled=True,
        get_all=AsyncMock(return_value=[]),
        get_markdown_entries=AsyncMock(
            return_value=[
                {
                    "id": "memory-1",
                    "text": "A Markdown memory",
                    "source": "MEMORY.md",
                    "path": "MEMORY.md",
                }
            ]
        ),
    )
    config = SimpleNamespace(
        whitelist=SimpleNamespace(api_key=None, allowed_paths=[]),
        web=SimpleNamespace(port=8000, allowed_origins=[]),
        llm=SimpleNamespace(model="openai/gpt-4o", base_url=""),
    )
    with patch("core.vectors.get_vector_service", return_value=service):
        channel = WebChannel(config=config, bus=MessageBus())
        response = TestClient(channel.app).get("/api/memory")

    assert response.status_code == 200
    payload = response.json()
    assert payload["mode"] == "grep_fallback"
    assert payload["reason"] == "vector_index_empty"
    assert payload["notice"] == "Vector index is empty; showing Markdown memory files."
    assert payload["memories"][0]["source"] == "MEMORY.md"
