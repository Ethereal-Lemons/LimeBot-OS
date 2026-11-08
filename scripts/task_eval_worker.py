"""Private offline evaluator worker; runtime imports happen after isolation."""

from __future__ import annotations

import asyncio
import inspect
import json
import os
from pathlib import Path, PurePosixPath
import re
import sys
import subprocess
import time
from types import SimpleNamespace
from typing import Any


_OFFLINE_TOOLS = frozenset(
    {
        "calculate",
        "edit_file",
        "list_dir",
        "memory_search",
        "memory_save",
        "read_file",
        "search_files",
        "verify_files",
        "write_file",
    }
)
_MUTATING_TOOLS = frozenset({"edit_file", "write_file"})
_NETWORK_TAG = re.compile(
    r"<\s*/?\s*(?:discord_send|discord_embed)\b", re.IGNORECASE
)


class _ReplayStream:
    def __init__(self, turn: dict[str, Any], on_complete):
        self._started = time.perf_counter()
        self._on_complete = on_complete
        self._measured = False
        tool_chunks = []
        for index, call in enumerate(turn.get("tool_calls") or []):
            tool_chunks.append(
                SimpleNamespace(
                    index=index,
                    id=f"eval_call_{index}",
                    type="function",
                    function=SimpleNamespace(
                        name=str(call["name"]),
                        arguments=json.dumps(
                            call.get("arguments") or {},
                            ensure_ascii=False,
                            separators=(",", ":"),
                        ),
                    ),
                )
            )
        content = str(turn.get("content") or "")
        delta = SimpleNamespace(content=content or None, tool_calls=tool_chunks or None)
        self._chunks = [
            SimpleNamespace(
                choices=[SimpleNamespace(delta=delta)],
                usage=None,
            )
        ]
        self._index = 0

    def __aiter__(self):
        return self

    async def __anext__(self):
        if self._index >= len(self._chunks):
            if not self._measured:
                self._measured = True
                self._on_complete(int((time.perf_counter() - self._started) * 1000))
            raise StopAsyncIteration
        chunk = self._chunks[self._index]
        self._index += 1
        return chunk


def _seed_state(state_dir: Path, case: dict[str, Any]) -> None:
    persona = state_dir / "persona"
    (persona / "users").mkdir(parents=True, exist_ok=True)
    (persona / "memory").mkdir(parents=True, exist_ok=True)
    (state_dir / "data").mkdir(parents=True, exist_ok=True)
    (state_dir / "temp").mkdir(parents=True, exist_ok=True)
    (state_dir / "allowed_paths.txt").write_text("", encoding="utf-8")
    (state_dir / ".env").write_text("", encoding="utf-8")
    (persona / "SOUL.md").write_text(
        "I am the LimeBot evaluation soul. My core values are to follow the "
        "user's local task, use only the supplied synthetic fixtures, respect "
        "every safety boundary, and report outcomes clearly and honestly.\n",
        encoding="utf-8",
    )
    (persona / "IDENTITY.md").write_text(
        "**Name:** LimeBot Evaluation\n**Style:** concise, factual, careful\n",
        encoding="utf-8",
    )
    memory = str(case.get("memory") or "Synthetic evaluation memory is empty.")
    (persona / "MEMORY.md").write_text(memory, encoding="utf-8")


def _safe_workspace_path(project_dir: Path, value: str) -> Path:
    normalized = str(value).replace("\\", "/")
    relative = PurePosixPath(normalized)
    if relative.is_absolute() or not relative.parts or ".." in relative.parts:
        raise ValueError("unsafe evaluation path")
    if relative.parts[0] != "workspace":
        raise ValueError("evaluation files must stay under workspace/")
    target = project_dir.joinpath(*relative.parts).resolve()
    root = (project_dir / "workspace").resolve()
    if target != root and root not in target.parents:
        raise ValueError("evaluation path escaped workspace")
    return target


def _write_fixtures(project_dir: Path, case: dict[str, Any]) -> None:
    for turn in case.get("replay") or []:
        if _NETWORK_TAG.search(str(turn.get("content") or "")):
            raise ValueError("network delivery tags are disabled offline")
    for fixture in case.get("fixtures") or []:
        target = _safe_workspace_path(project_dir, str(fixture["path"]))
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(str(fixture.get("content") or "").encode("utf-8"))
    (project_dir / "workspace").mkdir(exist_ok=True)


def _initialize_fixture_repository(project_dir: Path) -> None:
    marker = project_dir / "workspace" / ".eval-baseline"
    marker.write_bytes(b"synthetic evaluation baseline\n")
    commands = (
        ["git", "init", "--quiet"],
        ["git", "add", "--", "workspace"],
        [
            "git",
            "-c",
            "user.name=Offline Evaluation",
            "-c",
            "user.email=evaluation@localhost",
            "commit",
            "--quiet",
            "-m",
            "Synthetic evaluation fixture baseline",
        ],
    )
    for command in commands:
        completed = subprocess.run(
            command,
            cwd=project_dir,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            timeout=10,
            check=False,
        )
        if completed.returncode != 0:
            raise RuntimeError("could not prepare isolated fixture repository")


def _filter_tools(agent) -> None:
    original_get = agent._get_tool_definitions

    def get_offline_tool_definitions():
        return [
            tool
            for tool in (original_get() or [])
            if str((tool.get("function") or {}).get("name") or "")
            in _OFFLINE_TOOLS
        ]

    agent._get_tool_definitions = get_offline_tool_definitions
    original_execute = agent._execute_tool
    observed_tools: list[str] = []
    failures = 0
    failure_categories: set[str] = set()
    tool_ms = 0
    verification_status = "missing"

    async def execute_offline_tool(name: str, arguments: dict, session_key: str):
        nonlocal failures, tool_ms, verification_status
        if name not in _OFFLINE_TOOLS:
            failures += 1
            failure_categories.add("policy_denied")
            return "Error: tool is disabled in offline evaluation"
        if name in _MUTATING_TOOLS:
            raw_path = str((arguments or {}).get("path") or "")
            try:
                target = Path(raw_path)
                if target.is_absolute() or ".." in target.parts:
                    raise ValueError
                normalized = raw_path.replace("\\", "/")
                if not normalized.startswith("workspace/"):
                    raise ValueError
            except Exception:
                failures += 1
                failure_categories.add("policy_denied")
                observed_tools.append(name)
                return "Error: file mutation is limited to workspace/"
        started = time.perf_counter()
        observed_tools.append(name)
        try:
            result = await original_execute(name, arguments, session_key)
            if str(result or "").startswith("Error:"):
                failures += 1
                failure_categories.add("tool_error")
            if name == "verify_files":
                try:
                    parsed = json.loads(str(result or ""))
                    status = str(parsed.get("status") or "").lower()
                    if status in {"passed", "failed"}:
                        verification_status = status
                except (TypeError, json.JSONDecodeError):
                    verification_status = "failed"
            return result
        except Exception:
            failures += 1
            failure_categories.add("tool_error")
            raise
        finally:
            tool_ms += int((time.perf_counter() - started) * 1000)

    agent._execute_tool = execute_offline_tool
    agent._evaluation_tool_names = observed_tools
    agent._evaluation_tool_failures = lambda: failures
    agent._evaluation_failure_categories = lambda: failure_categories
    agent._evaluation_tool_time_ms = lambda: tool_ms
    agent._evaluation_verification_status = lambda: verification_status


def _verification(task_run, observed_status: str) -> str:
    if task_run is None:
        return "missing"
    state = str((task_run.checkpoint or {}).get("verification_state") or "")
    if state == "passed":
        return "passed"
    return observed_status if observed_status in {"passed", "failed"} else "missing"


async def _run(payload: dict[str, Any]) -> dict[str, Any]:
    project_dir = Path(payload["project_dir"]).resolve()
    state_dir = Path(payload["state_dir"]).resolve()
    case = payload["case"]
    _seed_state(state_dir, case)
    _write_fixtures(project_dir, case)
    _initialize_fixture_repository(project_dir)
    os.chdir(project_dir)
    sys.path.insert(0, str(project_dir))

    from core.bus import MessageBus
    from core.events import InboundMessage
    from core.llm_client import ChatRequest, LimeLLMClient
    from core.loop import AgentLoop

    class ReplayClient(LimeLLMClient):
        def __init__(self, turns: list[dict[str, Any]]):
            self._turns = turns
            self._index = 0
            self.calls = 0
            self.time_ms = 0

        async def complete(self, provider, request: ChatRequest):
            if request.session_id == "__warmup__":
                return SimpleNamespace(
                    choices=[
                        SimpleNamespace(
                            message=SimpleNamespace(
                                content="Evaluation warmup.", tool_calls=None
                            )
                        )
                    ],
                    usage=None,
                )
            if self._index >= len(self._turns):
                raise RuntimeError("evaluation replay exhausted")
            turn = self._turns[self._index]
            self._index += 1
            self.calls += 1
            return _ReplayStream(turn, lambda elapsed: setattr(
                self, "time_ms", self.time_ms + elapsed
            ))

    client = ReplayClient(case.get("replay") or [])
    bus = MessageBus()
    if "llm_client" in inspect.signature(AgentLoop.__init__).parameters:
        agent = AgentLoop(bus, model="openai/gpt-evaluation", llm_client=client)
    else:
        agent = AgentLoop(bus, model="openai/gpt-evaluation")
        agent.llm_client = client
    _filter_tools(agent)
    ready = await agent.await_ready(timeout=25)
    if not ready.get("ready"):
        await agent.stop()
        return {"worker_code": "agent_not_ready"}

    case_id = str(case["id"])
    run_id = str(payload["task_run_id"])
    message = InboundMessage(
        channel="web",
        sender_id="offline-evaluator",
        chat_id=f"{case_id}-{run_id[-8:]}",
        content=str(case["prompt"]),
        metadata={"task_run_id": run_id, "workspace_id": "evaluation"},
    )
    error = ""
    approved_tools = set(map(str, case.get("approvals") or []))
    stop_approvals = asyncio.Event()

    async def resolve_approvals() -> None:
        while not stop_approvals.is_set():
            for conf_id, confirmation in list(agent.pending_confirmations.items()):
                await agent.confirm_tool(
                    conf_id,
                    str(confirmation.get("tool") or "") in approved_tools,
                    source="web",
                )
            await asyncio.sleep(0.01)

    approval_task = asyncio.create_task(resolve_approvals())
    runner_task = asyncio.create_task(agent.run())
    try:
        await bus.publish_inbound(message)
        deadline = time.monotonic() + int(case.get("timeout_seconds") or 90)
        while time.monotonic() < deadline:
            task_run = agent.task_runs.get(run_id)
            if task_run is not None and task_run.terminal:
                break
            await asyncio.sleep(0.05)
        else:
            error = "turn_timeout"
    except Exception:
        error = "turn_error"
    finally:
        stop_approvals.set()
        approval_task.cancel()
        await asyncio.gather(approval_task, return_exceptions=True)
        bus.stop()
        runner_task.cancel()
        await asyncio.gather(runner_task, return_exceptions=True)

    task_run = agent.task_runs.get(run_id)
    tool_names = list(getattr(agent, "_evaluation_tool_names", []))
    status = str(getattr(task_run, "status", "incomplete"))
    if status not in {"completed", "blocked", "cancelled"}:
        status = error or "incomplete"
    verification = _verification(
        task_run, str(agent._evaluation_verification_status())
    )

    outbound = []
    while not bus.outbound.empty():
        try:
            outbound.append(bus.outbound.get_nowait())
        except Exception:
            break
    visible_text = "\n".join(str(item.content or "") for item in outbound)
    reply_checks = {
        str(check["id"]): str(check["contains"]).casefold() in visible_text.casefold()
        for check in ((case.get("expectations") or {}).get("reply_checks") or [])
    }
    file_checks = {}
    for check in ((case.get("expectations") or {}).get("file_checks") or []):
        try:
            content = _safe_workspace_path(project_dir, str(check["path"])).read_text(
                encoding="utf-8"
            )
            if "equals" in check:
                file_checks[str(check["id"])] = content == str(check["equals"])
            else:
                file_checks[str(check["id"])] = str(check.get("contains") or "") in content
        except (OSError, ValueError):
            file_checks[str(check["id"])] = False

    result = {
        "worker_code": "ok",
        "status": status,
        "verification": verification,
        "reply_checks": reply_checks,
        "file_checks": file_checks,
        "tool_names": sorted(set(tool_names)),
        "tool_failures": int(agent._evaluation_tool_failures()),
        "failure_categories": sorted(agent._evaluation_failure_categories()),
        "tool_calls": len(tool_names),
        "tool_time_ms": int(agent._evaluation_tool_time_ms()),
        "model_calls": client.calls,
        "model_time_ms": client.time_ms,
    }
    await agent.stop()
    return result


def main() -> int:
    if len(sys.argv) != 2:
        return 2
    request_path = Path(sys.argv[1]).resolve()
    try:
        payload = json.loads(request_path.read_text(encoding="utf-8"))
        project_dir = Path(payload["project_dir"]).resolve()
        state_dir = Path(payload["state_dir"]).resolve()
        os.environ["LIMEBOT_STATE_DIR"] = str(state_dir)
        os.environ["LLM_EMBEDDING_MODEL"] = "disabled"
        os.environ["LIMEBOT_EVAL_OFFLINE"] = "1"
        os.chdir(project_dir)
        sys.path.insert(0, str(project_dir))
        result = asyncio.run(_run(payload))
    except BaseException:
        result = {"worker_code": "worker_failed"}
    result_path = Path(payload.get("result_path", "")) if "payload" in locals() else None
    if result_path is None:
        return 3
    result_path.write_text(json.dumps(result, separators=(",", ":")), encoding="utf-8")
    return 0 if result.get("worker_code") == "ok" else 1


if __name__ == "__main__":
    raise SystemExit(main())
