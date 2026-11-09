"""Private live-evaluation worker: one real-model task inside a disposable project.

Runs in its own process with a scrubbed environment (provider credentials only).
The parent passes a request file and reads back a small JSON result; prompts,
replies and file contents are never written to the result.
"""

from __future__ import annotations

import asyncio
import json
import os
from pathlib import Path
import sys
import time
from typing import Any


async def _run(payload: dict[str, Any]) -> dict[str, Any]:
    project_dir = Path(payload["project_dir"]).resolve()
    state_dir = Path(payload["state_dir"]).resolve()
    os.chdir(project_dir)
    sys.path.insert(0, str(project_dir))

    # The offline worker lives next to this file and already knows how to seed
    # an isolated persona/state directory and a throwaway git baseline.
    import task_eval_worker as seed

    from core.live_eval import (
        LIVE_SENSITIVE_TOOLS,
        LIVE_TOOL_ALLOWLIST,
        MUTATING_TOOLS,
        BudgetedLLMClient,
        assemble_reply,
        command_allowed,
        evaluate_checks,
        is_workspace_path,
        live_case_from_dict,
        tool_counter,
        write_fixture_files,
    )
    from core.bus import MessageBus
    from core.events import InboundMessage
    from core.llm_client import LimeLLMClient
    from core.loop import AgentLoop
    from core.task_eval import FixtureFile

    raw_case = payload["case"]
    for key in ("fixtures", "hidden_fixtures", "solution"):
        raw_case[key] = [
            {"path": item["path"], "content": item["content"]}
            for item in raw_case.get(key) or []
        ]
    case = live_case_from_dict(
        {
            **raw_case,
            "checks": {
                "files": [dict(item) for item in raw_case.get("file_checks") or []],
                "reply": [
                    {"id": item["id"], "any_of": list(item["any_of"])}
                    for item in raw_case.get("reply_checks") or []
                ],
                "commands": [
                    {
                        "id": item["id"],
                        "argv": list(item["argv"]),
                        "timeout": item.get("timeout") or 30,
                        "stdout_contains": item.get("stdout_contains"),
                    }
                    for item in raw_case.get("command_checks") or []
                ],
            },
        }
    )

    seed._seed_state(state_dir, {"memory": ""})
    write_fixture_files(
        project_dir, tuple(FixtureFile(f.path, f.content) for f in case.fixtures)
    )
    seed._initialize_fixture_repository(project_dir)

    client = BudgetedLLMClient(
        LimeLLMClient(), max_calls=case.max_model_calls, max_tokens=case.max_tokens
    )
    model = os.environ.get("LLM_MODEL") or ""
    bus = MessageBus()
    agent = AgentLoop(bus, model=model, llm_client=client)

    original_definitions = agent._get_tool_definitions
    agent._get_tool_definitions = lambda: [
        tool
        for tool in (original_definitions() or [])
        if str((tool.get("function") or {}).get("name") or "") in LIVE_TOOL_ALLOWLIST
    ]
    original_execute = agent._execute_tool
    observed: list[str] = []
    trace: list[str] = []
    failures = 0

    async def guarded_execute(name: str, arguments: dict, session_key: str):
        nonlocal failures
        arguments = arguments or {}
        if name not in LIVE_TOOL_ALLOWLIST:
            failures += 1
            return "Error: tool is disabled in this evaluation"
        observed.append(name)
        if name in MUTATING_TOOLS and not is_workspace_path(
            str(arguments.get("path") or ""), project_dir
        ):
            failures += 1
            return "Error: file changes are limited to the workspace/ folder"
        if name == "run_command" and not command_allowed(str(arguments.get("command") or "")):
            failures += 1
            return "Error: only python, pytest and read-only git commands are allowed here"
        if name == "run_steps":
            steps = arguments.get("commands") or []
            if not isinstance(steps, list) or not all(
                command_allowed(str(step)) for step in steps
            ):
                failures += 1
                return "Error: only python, pytest and read-only git commands are allowed here"
        result = await original_execute(name, arguments, session_key)
        if str(result or "").startswith("Error:"):
            failures += 1
        if os.environ.get("LIMEBOT_EVAL_KEEP_REPLIES"):
            trace.append(
                f"{name} {json.dumps(arguments, ensure_ascii=False)[:160]} -> "
                f"{len(str(result or ''))} chars: {str(result or '')[:100]!r}"
            )
        return result

    agent._execute_tool = guarded_execute

    # A persona that fails validation switches LimeBot into its first-run
    # interview, and the model then asks onboarding questions instead of working.
    from core.prompt import get_setup_state

    if not get_setup_state().get("complete"):
        await agent.stop()
        return {"worker_code": "persona_setup_incomplete"}

    ready = await agent.await_ready(timeout=30)
    if not ready.get("ready"):
        await agent.stop()
        return {"worker_code": "agent_not_ready"}

    run_id = str(payload["run_id"])
    chat_id = f"{case.id}-{run_id[-8:]}"
    message = InboundMessage(
        channel="web",
        sender_id="live-evaluator",
        chat_id=chat_id,
        content=case.prompt,
        metadata={"task_run_id": run_id, "workspace_id": "evaluation"},
    )

    collected: list[Any] = []
    saw_terminal_reply = asyncio.Event()

    async def drain_outbound() -> None:
        while True:
            item = await bus.outbound.get()
            collected.append(item)
            meta = getattr(item, "metadata", None) or {}
            if meta.get("event_kind") == "assistant_message" and meta.get("terminal"):
                saw_terminal_reply.set()

    async def resolve_approvals() -> None:
        while True:
            for conf_id, confirmation in list(agent.pending_confirmations.items()):
                await agent.confirm_tool(
                    conf_id,
                    str(confirmation.get("tool") or "") in LIVE_SENSITIVE_TOOLS,
                    source="web",
                )
            await asyncio.sleep(0.05)

    drain_task = asyncio.create_task(drain_outbound())
    approval_task = asyncio.create_task(resolve_approvals())
    runner_task = asyncio.create_task(agent.run())
    error = ""
    try:
        await bus.publish_inbound(message)
        deadline = time.monotonic() + case.timeout_seconds
        while time.monotonic() < deadline:
            task_run = agent.task_runs.get(run_id)
            if task_run is not None and task_run.terminal:
                break
            if saw_terminal_reply.is_set() and task_run is None:
                break
            if client.exceeded:
                error = "budget_exceeded"
                break
            await asyncio.sleep(0.1)
        else:
            error = "turn_timeout"
    except Exception:
        error = "turn_error"
    finally:
        for task in (drain_task, approval_task):
            task.cancel()
        await asyncio.gather(drain_task, approval_task, return_exceptions=True)
        bus.stop()
        runner_task.cancel()
        await asyncio.gather(runner_task, return_exceptions=True)

    task_run = agent.task_runs.get(run_id)
    status = str(getattr(task_run, "status", "") or "")
    if status not in {"completed", "blocked", "cancelled"}:
        if error:
            status = error
        elif saw_terminal_reply.is_set():
            status = "completed"
        else:
            status = "incomplete"

    reply_text = assemble_reply(collected)
    # Hidden fixtures (for example the real test suite) appear only now, so the
    # model could not have read or special-cased them.
    write_fixture_files(
        project_dir, tuple(FixtureFile(f.path, f.content) for f in case.hidden_fixtures)
    )
    checks = evaluate_checks(case, project_dir, reply_text)

    keep_dir = os.environ.get("LIMEBOT_EVAL_KEEP_REPLIES")
    if keep_dir:
        # Opt-in, local only: lets you read what the model said when a run fails.
        # Reports never contain this text.
        target = Path(keep_dir)
        target.mkdir(parents=True, exist_ok=True)
        (target / f"{case.id}-{run_id[-8:]}.txt").write_text(reply_text, encoding="utf-8")
        (target / f"{case.id}-{run_id[-8:]}.trace.txt").write_text(
            "\n".join(trace), encoding="utf-8"
        )

    result = {
        "worker_code": "ok",
        "status": status,
        "checks": checks,
        "tool_counts": tool_counter(observed),
        "tool_failures": failures,
        "model_calls": client.calls,
        "tokens_in": client.tokens_in,
        "tokens_out": client.tokens_out,
        "budget_exceeded": bool(client.exceeded),
    }
    await agent.stop()
    return result


def main() -> int:
    if len(sys.argv) != 2:
        return 2
    payload: dict[str, Any] = {}
    try:
        payload = json.loads(Path(sys.argv[1]).read_text(encoding="utf-8"))
        os.environ["LIMEBOT_STATE_DIR"] = str(Path(payload["state_dir"]).resolve())
        result = asyncio.run(_run(payload))
    except BaseException:
        result = {"worker_code": "worker_failed"}
    result_path = payload.get("result_path")
    if not result_path:
        return 3
    Path(result_path).write_text(json.dumps(result, separators=(",", ":")), encoding="utf-8")
    return 0 if result.get("worker_code") == "ok" else 1


if __name__ == "__main__":
    raise SystemExit(main())
