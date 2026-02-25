import asyncio
import os
import time
import uuid
import unittest
from pathlib import Path


def _make_memory_files(memory_dir: Path, file_count: int, lines_per_file: int) -> list[Path]:
    memory_dir.mkdir(parents=True, exist_ok=True)
    created = []
    token = uuid.uuid4().hex[:8]
    payload = "\n".join(
        f"alpha beta gamma line {i}" for i in range(lines_per_file)
    )
    for i in range(file_count):
        path = memory_dir / f"perf_{token}_{i}.md"
        path.write_text(payload, encoding="utf-8")
        created.append(path)
    return created


async def _measure_loop_latency(duration_s: float, interval_s: float) -> float:
    start = time.perf_counter()
    expected = start
    max_delay = 0.0

    while (time.perf_counter() - start) < duration_s:
        expected += interval_s
        await asyncio.sleep(interval_s)
        actual = time.perf_counter()
        delay = actual - expected
        if delay > max_delay:
            max_delay = delay

    return max_delay


class TestLoopLatency(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        try:
            import loguru  # noqa: F401
        except Exception:
            raise unittest.SkipTest("Missing dependencies (loguru).")

        from core.vectors import VectorService

        self.VectorService = VectorService
        self.memory_dir = Path("persona") / "memory"
        self.created_files = _make_memory_files(
            self.memory_dir, file_count=120, lines_per_file=50
        )

    async def asyncTearDown(self):
        for path in self.created_files:
            try:
                path.unlink()
            except Exception:
                pass

    async def test_rag_grep_does_not_block_event_loop(self):
        if os.getenv("LIMEBOT_SKIP_PERF"):
            self.skipTest("Set LIMEBOT_SKIP_PERF to skip performance tests.")

        service = self.VectorService()

        latency_task = asyncio.create_task(
            _measure_loop_latency(duration_s=0.5, interval_s=0.01)
        )

        await service.search_grep("alpha beta gamma", limit=5)

        max_delay = await latency_task

        self.assertLess(
            max_delay,
            0.2,
            f"Event loop delay too high: {max_delay:.3f}s",
        )
