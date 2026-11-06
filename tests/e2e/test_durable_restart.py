"""End-to-end: persist a job, force-kill the worker, resume to success."""

from __future__ import annotations

import os
import signal
import subprocess
import sys
import time
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from core.events import InboundMessage
from core.job_queue import SUCCEEDED, DurableJobQueue


ROOT = Path(__file__).resolve().parents[2]
HARNESS = Path(__file__).resolve().parent / "harness.py"
SOUL_EXAMPLE = ROOT / "persona" / "SOUL.md.example"
IDENTITY_EXAMPLE = ROOT / "persona" / "IDENTITY.md.example"


class TestDurableJobRestartE2E(unittest.TestCase):
    def _prepare_state(self, state: Path) -> None:
        persona = state / "persona"
        persona.mkdir(parents=True, exist_ok=True)
        (state / "data").mkdir(parents=True, exist_ok=True)
        (state / "temp").mkdir(parents=True, exist_ok=True)
        soul = SOUL_EXAMPLE.read_text(encoding="utf-8") if SOUL_EXAMPLE.exists() else (
            "I am LimeBot. I keep durable jobs honest and finish unattended work.\n" * 4
        )
        identity = (
            IDENTITY_EXAMPLE.read_text(encoding="utf-8")
            if IDENTITY_EXAMPLE.exists()
            else "**Name:** LimeBot\n**Style:** concise\n"
        )
        (persona / "SOUL.md").write_text(soul, encoding="utf-8")
        (persona / "IDENTITY.md").write_text(identity, encoding="utf-8")
        (persona / "MEMORY.md").write_text("No long-term notes yet.\n", encoding="utf-8")

    def _env(self, state: Path, *, sleep: str) -> dict:
        env = os.environ.copy()
        env.update(
            {
                "LIMEBOT_STATE_DIR": str(state),
                "LIMEBOT_E2E_READY_FILE": str(state / "ready"),
                "LIMEBOT_TEST_LLM_MODE": "echo",
                "LIMEBOT_TEST_LLM_SLEEP": sleep,
                "LIMEBOT_TEST_LLM_REPLY": "Durable job completed without a human click.",
                "LLM_MODEL": "openai/gpt-test",
                "LLM_EMBEDDING_MODEL": "disabled",
                "OPENAI_API_KEY": "sk-test-e2e",
                "ENABLE_DISCORD": "false",
                "ENABLE_WHATSAPP": "false",
                "ENABLE_TELEGRAM": "false",
                "APPROVAL_POLICY_PROFILE": "manual",
                "UNATTENDED_PATH_ALLOWLIST": str(state / "temp"),
                "PYTHONPATH": str(ROOT),
                "PYTHONUNBUFFERED": "1",
            }
        )
        return env

    def _start(self, state: Path, sleep: str) -> subprocess.Popen:
        ready = state / "ready"
        if ready.exists():
            ready.unlink()
        log_file = (state / f"harness-{time.time_ns()}.log").open("w+b")
        proc = subprocess.Popen(
            [sys.executable, str(HARNESS)],
            cwd=str(ROOT),
            env=self._env(state, sleep=sleep),
            stdout=log_file,
            stderr=subprocess.STDOUT,
        )
        proc._limebot_harness_log = log_file
        deadline = time.time() + 20
        while time.time() < deadline:
            if ready.exists():
                return proc
            if proc.poll() is not None:
                output = self._read_harness_log(proc)
                self._close_harness_log(proc)
                self.fail(f"harness exited early ({proc.returncode}): {output}")
            time.sleep(0.1)
        proc.kill()
        proc.wait(timeout=5)
        output = self._read_harness_log(proc)
        self._close_harness_log(proc)
        self.fail(f"harness did not write ready file: {output}")

    @staticmethod
    def _read_harness_log(process: subprocess.Popen) -> str:
        log_file = getattr(process, "_limebot_harness_log", None)
        if log_file is None or log_file.closed:
            return ""
        log_file.flush()
        log_file.seek(0)
        return log_file.read().decode("utf-8", errors="replace")

    @staticmethod
    def _close_harness_log(process: subprocess.Popen) -> None:
        log_file = getattr(process, "_limebot_harness_log", None)
        if log_file is not None and not log_file.closed:
            log_file.close()

    def _wait_status(
        self,
        db: Path,
        job_id: str,
        status: str,
        timeout: float = 20.0,
        process: subprocess.Popen | None = None,
    ):
        deadline = time.time() + timeout
        last = None
        while time.time() < deadline:
            queue = DurableJobQueue(db)
            job = queue.get(job_id)
            last = job.status if job else None
            if last == status:
                return job
            time.sleep(0.1)
        process_output = ""
        if process is not None:
            if process.poll() is None:
                process.kill()
            try:
                process.wait(timeout=3)
            except subprocess.TimeoutExpired:
                pass
            process_output = self._read_harness_log(process)
            self._close_harness_log(process)
        self.fail(
            f"job {job_id} did not reach {status}; last={last}. "
            f"Harness output: {process_output}"
        )

    def test_worker_kill_resume_reaches_success(self):
        with TemporaryDirectory() as tmp:
            state = Path(tmp)
            self._prepare_state(state)
            db = state / "data" / "jobs.sqlite"
            queue = DurableJobQueue(db)
            job = queue.enqueue(
                InboundMessage(
                    channel="web",
                    sender_id="e2e",
                    chat_id="durable",
                    content="Finish this queued job after restart.",
                    metadata={"durable": True, "unattended": True},
                ),
                kind="manual",
            )

            first = self._start(state, sleep="20")
            try:
                self._wait_status(db, job.id, "running", timeout=15, process=first)
                first.kill()
                first.wait(timeout=5)
                self._close_harness_log(first)
            finally:
                if first.poll() is None:
                    first.kill()
                    first.wait(timeout=5)

            second = self._start(state, sleep="0")
            try:
                finished = self._wait_status(
                    db, job.id, SUCCEEDED, timeout=20, process=second
                )
                self.assertEqual(finished.status, SUCCEEDED)
                self.assertIsNone(finished.last_error)
            finally:
                second.send_signal(signal.SIGTERM)
                try:
                    second.wait(timeout=8)
                except subprocess.TimeoutExpired:
                    second.kill()
                    second.wait(timeout=3)
                self._close_harness_log(second)
