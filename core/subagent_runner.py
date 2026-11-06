"""Run delegated subagents and collect their result and workspace report."""

import asyncio
import json
from typing import Any, Callable, Dict, List, Optional

from loguru import logger

from core.events import InboundMessage
from core.operator_invariants import (
    collect_subagent_failures,
    format_subagent_failure_block,
)
from core.redaction import redact_sensitive_text
from core.subagents import normalize_subagent_tool_name


async def run_subagent(
    parent_session_key: str,
    sub_session_key: str,
    task: str,
    agent_name: Optional[str] = None,
    isolated_workspace: Any = None,
    isolation_mode: str = "none",
    *,
    subagent_registry: Any,
    get_model: Callable[[], str],
    session_manager: Any,
    filter_tool_definitions: Callable[..., List[Dict[str, Any]]],
    call_llm: Callable[..., Any],
    normalize_tool_call: Callable[..., Any],
    execute_tool: Callable[..., Any],
    clean_final_result: Callable[[str], str],
    bus: Any,
    soul_file: Any,
    identity_file: Any,
    report_diff_max_chars: int,
) -> str:
    workspace_token = None
    retain_workspace = False
    try:
        if isolated_workspace is not None:
            workspace_token = isolated_workspace.activate()
        logger.info(f"[SUB-AGENT] {sub_session_key} ← {parent_session_key}: {task}")

        subagent_profile = subagent_registry.get_subagent(agent_name)
        if agent_name and not subagent_profile:
            return f"Error: Unknown subagent '{agent_name}'"

        subagent_model = (
            (subagent_profile or {}).get("model") or "inherit"
        ).strip()
        subagent_max_turns = (subagent_profile or {}).get("max_turns") or 10
        allowed_tools = None
        tool_definitions_override = None
        disallowed_tools = {
            normalize_subagent_tool_name(name)
            for name in ((subagent_profile or {}).get("disallowed_tools") or [])
            if normalize_subagent_tool_name(name)
        }
        if subagent_profile:
            tool_definitions_override = filter_tool_definitions(
                subagent_profile.get("tools"),
                subagent_profile.get("disallowed_tools"),
            )
            if subagent_profile.get("tools") is not None:
                allowed_tools = {
                    tool.get("function", {}).get("name")
                    for tool in tool_definitions_override
                    if tool.get("function", {}).get("name")
                }

        session_model = (
            get_model()
            if not subagent_model or subagent_model == "inherit"
            else subagent_model
        )
        await session_manager.update_session(
            session_key=sub_session_key,
            model=session_model,
            origin=f"subagent:{parent_session_key}",
            parent_id=parent_session_key,
            task=task,
            subagent_name=agent_name or "",
        )

        try:
            soul = soul_file.read_text(encoding="utf-8")
            identity = identity_file.read_text(encoding="utf-8")
        except Exception:
            soul = "You are a helpful assistant."
            identity = "Name: LimeBot Sub-Agent"

        profile_lines = []
        if subagent_profile:
            description = (subagent_profile.get("description") or "").strip()
            prompt = (subagent_profile.get("prompt") or "").strip()
            if description:
                profile_lines.append(f"Profile: {description}")
            if prompt:
                profile_lines.append(prompt)
            if allowed_tools is None:
                profile_lines.append("Tool access: inherit the main toolset.")
            else:
                listed_tools = ", ".join(sorted(allowed_tools)) or "none"
                profile_lines.append(
                    f"Tool access: only use these tools: {listed_tools}."
                )
            if disallowed_tools:
                profile_lines.append(
                    "Never use these tools: "
                    + ", ".join(sorted(disallowed_tools))
                    + "."
                )
            if subagent_profile.get("background"):
                profile_lines.append(
                    "This profile is intended for background-friendly work when delegated asynchronously."
                )
            if subagent_max_turns:
                profile_lines.append(
                    f"Complete the task within at most {subagent_max_turns} assistant turns."
                )

        profile_block = "\n".join(line for line in profile_lines if line).strip()
        if profile_block:
            profile_block += "\n"

        workspace_instructions = ""
        if isolated_workspace is not None:
            workspace_instructions = (
                "Workspace isolation: Relative writes go to a temporary copy of the project "
                "and are not merged automatically. You MAY read the same allowed absolute "
                "paths the parent can read (project roots, AGENTS.md, persona/, temp/). "
                "Do not invent 'permission denied' when those files exist. "
                "At the end, report changed files and verification results.\n"
            )

        sub_system = (
            f"{soul}\n\n{identity}\n\n"
            "--- SUB-AGENT INSTRUCTIONS ---\n"
            + (
                f"You are the '{agent_name}' subagent.\n"
                if agent_name and subagent_profile
                else "You are a generic sub-agent.\n"
            )
            + profile_block
            + workspace_instructions
            + f"Primary task: {task}\n"
            + "Work independently, use tools when needed, and return a concise final result.\n" +
            "DO NOT start a conversation — JUST COMPLETE THE TASK.\n"
        )

        sub_history: List[Dict] = [
            {"role": "system", "content": sub_system},
            {"role": "user", "content": f"Task: {task}"},
        ]
        asyncio.create_task(
            session_manager.append_chat_log(
                sub_session_key, {"role": "user", "content": task}
            )
        )

        iteration = 0
        final_result = ""

        while iteration < subagent_max_turns:
            iteration += 1
            logger.info(f"[SUB-AGENT:{sub_session_key}] iteration {iteration}")

            response = await call_llm(
                messages=sub_history,
                session_key=sub_session_key,
                msg=None,
                stream=False,
                tool_context_text=task,
                tool_definitions_override=tool_definitions_override,
                model_override=subagent_model,
            )

            if hasattr(response, "usage"):
                await session_manager.update_session(
                    session_key=sub_session_key,
                    model=session_model,
                    origin=f"subagent:{parent_session_key}",
                    usage=response.usage,
                )

            assistant_msg = response.choices[0].message
            full_content = assistant_msg.content or ""
            tool_calls_raw = assistant_msg.tool_calls

            sub_history.append(assistant_msg.model_dump())
            asyncio.create_task(
                session_manager.append_chat_log(
                    sub_session_key, {"role": "assistant", "content": full_content}
                )
            )

            if not tool_calls_raw:
                final_result = full_content
                break

            for tc in tool_calls_raw:
                tc_id = tc.id
                fn = tc.function.name
                try:
                    args = json.loads(tc.function.arguments) if tc.function.arguments else {}
                    fn, args = normalize_tool_call(
                        fn, args, sub_session_key
                    )
                    logger.info(
                        f"[SUB-AGENT:{sub_session_key}] "
                        f"{fn}({redact_sensitive_text(args)})"
                    )
                    if (allowed_tools is not None and fn not in allowed_tools) or (
                        fn in disallowed_tools
                    ):
                        result = (
                            f"Error: Tool '{fn}' is not allowed for subagent "
                            f"'{agent_name}'."
                        )
                    else:
                        result = await execute_tool(
                            fn, args, sub_session_key
                        )
                except Exception as e:
                    result = f"Error: {e}"

                sub_history.append(
                    {
                        "role": "tool",
                        "tool_call_id": tc_id,
                        "name": fn,
                        "content": str(result),
                    }
                )

        child_failures = collect_subagent_failures(sub_history)
        if child_failures and not str(final_result or "").strip():
            final_result = format_subagent_failure_block(child_failures)

        if not str(final_result or "").strip():
            summary_prompt = (
                "Final response only. Do not call any more tools. "
                "Do not mention truncation, token limits, tool budgets, or that you are an assistant. "
                "Use only the work already completed. "
                "If this was a review or verification task, return a concise report with findings or outcome, "
                "a short rating if appropriate, and 2-4 concrete suggestions. "
                "If anything remains unverified, state that plainly."
            )
            sub_history.append({"role": "system", "content": summary_prompt})
            try:
                summary_response = await call_llm(
                    messages=sub_history,
                    session_key=sub_session_key,
                    msg=None,
                    stream=False,
                    include_tools=False,
                    tool_context_text=task,
                    model_override=subagent_model,
                )
                summary_msg = summary_response.choices[0].message
                final_result = (
                    clean_final_result(summary_msg.content or "")
                    or f"Stopped after reaching max_turns ({subagent_max_turns}) without a final answer."
                )
                sub_history.append(summary_msg.model_dump())
                asyncio.create_task(
                    session_manager.append_chat_log(
                        sub_session_key,
                        {"role": "assistant", "content": final_result},
                    )
                )
            except Exception as summary_error:
                logger.warning(
                    f"[SUB-AGENT:{sub_session_key}] failed to produce final summary after max_turns: {summary_error}"
                )
                final_result = (
                    f"Stopped after reaching max_turns ({subagent_max_turns}) without a final answer."
                )

        final_result = clean_final_result(final_result)
        if child_failures:
            failure_block = format_subagent_failure_block(child_failures)
            if failure_block and failure_block not in str(final_result or ""):
                final_result = (
                    f"{final_result.rstrip()}\n\n{failure_block}"
                    if str(final_result or "").strip()
                    else failure_block
                )
        if not str(final_result or "").strip():
            final_result = (
                format_subagent_failure_block(child_failures)
                if child_failures
                else "Sub-agent finished without a visible result."
            )

        report_title = (
            f"--- SUB-AGENT REPORT ({sub_session_key}) [{agent_name}] ---"
            if agent_name
            else f"--- SUB-AGENT REPORT ({sub_session_key}) ---"
        )
        report = (
            f"{report_title}\n"
            f"Task: {task}\n"
            f"Result:\n{final_result}\n"
        )

        workspace_capture = None
        workspace_report = None
        if isolated_workspace is not None:
            try:
                workspace_capture = await isolated_workspace.capture()
                if workspace_capture.get("status") == "changed":
                    isolated_workspace.retain()
                    retain_workspace = True
                workspace_report = isolated_workspace.report_metadata(
                    workspace_capture
                )
                diff_entries = []
                diff_budget = report_diff_max_chars
                for changed_file in workspace_capture.get("changed_files", []):
                    diff = str(changed_file.get("diff") or "")
                    if not diff or diff_budget <= 0:
                        continue
                    clipped_diff = diff[:diff_budget]
                    diff_entries.append(
                        {
                            "path": changed_file.get("path", ""),
                            "status": changed_file.get("status", "modified"),
                            "diff": clipped_diff,
                        }
                    )
                    diff_budget -= len(clipped_diff)
                if diff_entries:
                    workspace_report["diff"] = diff_entries
                report += (
                    "Workspace changes (not merged; call apply_workspace_changeset "
                    f"with workspace_id={isolated_workspace.workspace_id}):\n"
                    + json.dumps(workspace_report, ensure_ascii=False)
                    + "\n"
                )
            except Exception as workspace_error:
                logger.warning(
                    f"[SUB-AGENT:{sub_session_key}] failed to capture workspace changes: "
                    f"{workspace_error}"
                )

        parts = parent_session_key.split(":", 1)
        if len(parts) == 2:
            report_metadata = {
                "is_report": True,
                "subagent_id": sub_session_key,
                "isolation": isolation_mode,
            }
            if workspace_capture is not None:
                report_metadata["workspace"] = workspace_capture
            await bus.publish_inbound(
                InboundMessage(
                    channel=parts[0],
                    sender_id="system",
                    chat_id=parts[1],
                    content=report,
                    metadata=report_metadata,
                )
            )
        return report

    except Exception as e:
        logger.error(f"Error in sub-agent '{sub_session_key}': {e}")
        return f"Error in sub-agent '{sub_session_key}': {e}"
    finally:
        if workspace_token is not None:
            try:
                isolated_workspace.deactivate(workspace_token)
            except Exception as exc:
                logger.warning(
                    f"Could not restore workspace context for sub-agent '{sub_session_key}': {exc}"
                )
        if isolated_workspace is not None and not retain_workspace:
            try:
                await isolated_workspace.cleanup()
            except Exception as exc:
                logger.warning(
                    f"Could not clean up isolated workspace for sub-agent '{sub_session_key}': {exc}"
                )
