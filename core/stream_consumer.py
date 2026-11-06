"""Consume streamed LLM output and translate it into LimeBot events."""

import json
import re
import time
import uuid
from typing import Any, Callable, Dict, List, Optional

from loguru import logger

from core.events import InboundMessage, OutboundMessage
from core.tool_call_parser import (
    extract_tool_calls,
    sanitize_tool_call_content,
    trim_leading_structural_lines,
)


_GHOST_TAG_RE = re.compile(
    r"</?(?:save_user|save_soul|save_identity|save_memory"
    r"|log_memory|save_mood|save_relationship"
    r"|save_memory|discord_send|discord_embed)>",
    re.IGNORECASE,
)

_GHOST_TAG_NAMES = (
    "save_user",
    "save_soul",
    "save_identity",
    "save_memory",
    "log_memory",
    "save_mood",
    "save_relationship",
    "discord_send",
    "discord_embed",
)


async def consume_stream(
    response_stream,
    msg: InboundMessage,
    session_key: str,
    previous_content: str = "",
    turn_id: Optional[str] = None,
    message_id: Optional[str] = None,
    turn_started_at: Optional[float] = None,
    llm_started_at: Optional[float] = None,
    iteration_kind: str = "initial",
    iteration: int = 0,
    on_output_queued: Optional[Callable[[str], None]] = None,
    *,
    bus: Any,
    metrics: Any,
    session_manager: Any,
    get_model: Callable[[], str],
    log_tool_debug: Callable[..., None],
    record_stage_timing: Callable[..., None],
    with_trace_metadata: Callable[..., Dict[str, Any]],
    tool_call_debug_rows: Callable[[List[Dict[str, Any]]], List[Dict[str, Any]]],
):
    full_content: str = ""
    tool_calls: list = []
    usage = None
    is_potential_json: bool = False
    streamed_any: bool = False
    display_buffer: str = ""
    ghost_active: Optional[str] = None
    match_index = 0
    dedup_active = bool(previous_content)
    streamed_to_web: bool = False
    streamed_to_discord: bool = False
    last_flush = time.monotonic()
    flush_interval_s = 0.08
    flush_min_chars = 256
    thinking_buffer: str = ""
    extracted_from = "provider_tool_calls"
    chunk_index = 0
    provider_first_delta_recorded = False

    def first_delta_metadata(delta_kind: str) -> Dict[str, Any]:
        return {
            "iteration_kind": iteration_kind,
            "iteration": iteration,
            "delta_kind": delta_kind,
        }

    def record_provider_first_delta(delta_kind: str) -> None:
        nonlocal provider_first_delta_recorded
        if provider_first_delta_recorded or llm_started_at is None:
            return
        provider_first_delta_recorded = True
        record_stage_timing(
            session_key,
            "provider_first_delta",
            llm_started_at,
            metadata=first_delta_metadata(delta_kind),
        )

    try:
        async for chunk in response_stream:
            chunk_index += 1
            if hasattr(chunk, "usage") and chunk.usage:
                usage = chunk.usage

            delta = chunk.choices[0].delta

            if hasattr(delta, "content") and delta.content:
                record_provider_first_delta("content")
                content_chunk = delta.content
                log_tool_debug(
                    "stream_chunk_content",
                    session_key=session_key,
                    chunk_index=chunk_index,
                    delta_content=content_chunk,
                )

                if full_content and content_chunk == full_content:
                    continue
                if len(content_chunk) > 5 and full_content.startswith(
                    content_chunk
                ):
                    continue
                if full_content and content_chunk.startswith(full_content):
                    content_chunk = content_chunk[len(full_content) :]

                to_stream = content_chunk
                if dedup_active:
                    remaining_prev = previous_content[match_index:]
                    if not remaining_prev:
                        dedup_active = False
                    else:
                        common = 0
                        for i in range(
                            min(len(content_chunk), len(remaining_prev))
                        ):
                            if content_chunk[i] == remaining_prev[i]:
                                common += 1
                            else:
                                break
                        if common > 0:
                            match_index += common
                            to_stream = content_chunk[common:]
                            if common < len(content_chunk):
                                dedup_active = False
                        else:
                            dedup_active = False

                full_content += content_chunk

                if not streamed_any:
                    stripped = full_content.strip()
                    if not stripped:
                        continue
                    is_potential_json = stripped.startswith(
                        "{"
                    ) or stripped.startswith("```")
                    streamed_any = True

                if msg.channel == "web" and not is_potential_json and to_stream:
                    display_buffer += to_stream
                    display_buffer = trim_leading_structural_lines(
                        display_buffer
                    )
                    if not display_buffer.strip():
                        continue

                    now = time.monotonic()
                    should_flush = (
                        len(display_buffer) >= flush_min_chars
                        or (now - last_flush) >= flush_interval_s
                    )

                    while display_buffer and should_flush:
                        if not ghost_active:
                            tag_start = display_buffer.find("<")
                            if tag_start == -1:
                                await bus.publish_outbound(
                                    OutboundMessage(
                                        channel=msg.channel,
                                        chat_id=msg.chat_id,
                                        content=display_buffer,
                                        metadata=with_trace_metadata(
                                            {"type": "chunk"},
                                            turn_id=turn_id,
                                            message_id=message_id,
                                        ),
                                    )
                                )
                                if on_output_queued:
                                    on_output_queued("content")
                                display_buffer = ""
                                last_flush = time.monotonic()
                                break

                            if tag_start > 0:
                                await bus.publish_outbound(
                                    OutboundMessage(
                                        channel=msg.channel,
                                        chat_id=msg.chat_id,
                                        content=display_buffer[:tag_start],
                                        metadata=with_trace_metadata(
                                            {"type": "chunk"},
                                            turn_id=turn_id,
                                            message_id=message_id,
                                        ),
                                    )
                                )
                                if on_output_queued:
                                    on_output_queued("content")
                                streamed_to_web = True
                                last_flush = time.monotonic()
                                display_buffer = display_buffer[tag_start:]

                            tag_end = display_buffer.find(">")
                            if tag_end == -1:
                                break

                            tag_content = display_buffer[: tag_end + 1]
                            found_ghost = None
                            if not tag_content.startswith("</"):
                                for g in _GHOST_TAG_NAMES:
                                    if tag_content.startswith(f"<{g}"):
                                        found_ghost = g
                                        break

                            if found_ghost:
                                ghost_active = found_ghost
                                await bus.publish_outbound(
                                    OutboundMessage(
                                        channel=msg.channel,
                                        chat_id=msg.chat_id,
                                        content="",
                                        metadata={
                                            "type": "activity",
                                            "text": f"🧠 Processing {found_ghost}…",
                                        },
                                    )
                                )
                                display_buffer = display_buffer[tag_end + 1 :]
                            elif tag_content.startswith("</"):
                                display_buffer = display_buffer[tag_end + 1 :]
                            else:
                                await bus.publish_outbound(
                                    OutboundMessage(
                                        channel=msg.channel,
                                        chat_id=msg.chat_id,
                                        content=tag_content,
                                        metadata=with_trace_metadata(
                                            {"type": "chunk"},
                                            turn_id=turn_id,
                                            message_id=message_id,
                                        ),
                                    )
                                )
                                if on_output_queued:
                                    on_output_queued("content")
                                streamed_to_web = True
                                display_buffer = display_buffer[tag_end + 1 :]
                        else:
                            closing = f"</{ghost_active}>"
                            close_idx = display_buffer.find(closing)
                            if close_idx == -1:
                                display_buffer = ""
                                break
                            ghost_active = None
                            await bus.publish_outbound(
                                OutboundMessage(
                                    channel=msg.channel,
                                    chat_id=msg.chat_id,
                                    content="",
                                    metadata={"type": "activity", "text": ""},
                                )
                            )
                            display_buffer = display_buffer[
                                close_idx + len(closing) :
                            ]

            # Discord intentionally buffers provider deltas. Its typing
            # indicator stays active and the normal delivery path sends
            # only the completed response. Web remains token-streamed.
            thinking = getattr(delta, "reasoning_content", None) or getattr(
                delta, "thinking", None
            )
            if thinking:
                thinking_buffer += thinking
                log_tool_debug(
                    "stream_chunk_thinking",
                    session_key=session_key,
                    chunk_index=chunk_index,
                    delta_thinking=thinking,
                )
            if thinking and msg.channel == "web":
                await bus.publish_outbound(
                    OutboundMessage(
                        channel=msg.channel,
                        chat_id=msg.chat_id,
                        content=thinking,
                        metadata=with_trace_metadata(
                            {"type": "thinking"},
                            turn_id=turn_id,
                            message_id=message_id,
                        ),
                    )
                )

            if hasattr(delta, "tool_calls") and delta.tool_calls:
                record_provider_first_delta("tool_call")
                log_tool_debug(
                    "stream_chunk_tool_delta",
                    session_key=session_key,
                    chunk_index=chunk_index,
                    tool_delta=str(delta.tool_calls),
                )
                for tc_chunk in delta.tool_calls:
                    while len(tool_calls) <= tc_chunk.index:
                        tool_calls.append(
                            {
                                "id": "",
                                "type": "function",
                                "function": {"name": "", "arguments": ""},
                            }
                        )
                    tc = tool_calls[tc_chunk.index]
                    if tc_chunk.id:
                        tc["id"] = tc_chunk.id
                    if tc_chunk.function.name:
                        tc["function"]["name"] += tc_chunk.function.name
                    if tc_chunk.function.arguments:
                        tc["function"]["arguments"] += tc_chunk.function.arguments

    except Exception as e:
        err_msg_lower = str(e).lower()
        if (
            "429" in err_msg_lower
            or "RateLimitError" in type(e).__name__
            or "rate limit" in err_msg_lower
            or "rate_limit" in err_msg_lower
            or "usage_limit" in err_msg_lower
            or "usage limit" in err_msg_lower
            or "limit has been reached" in err_msg_lower
            or "limit_reached" in err_msg_lower
            or "codex provider returned an error" in err_msg_lower
            or "no visible response" in err_msg_lower
        ):
            logger.warning(f"⚠ Rate limit during streaming: {e}")
            rate_limit_notice = "\n\n[⚠ Generation stopped due to rate limit/usage limit reached]"
            display_buffer += rate_limit_notice
            full_content += rate_limit_notice
            await bus.publish_outbound(
                OutboundMessage(
                    channel=msg.channel,
                    chat_id=msg.chat_id,
                    content="",
                    metadata=with_trace_metadata(
                        {"type": "rate_limit_error", "details": str(e)},
                        turn_id=turn_id,
                        message_id=message_id,
                    ),
                )
            )
        else:
            raise

    if usage:
        await session_manager.update_session(
            session_key=session_key,
            model=get_model(),
            origin=msg.channel,
            usage=usage,
        )

    if display_buffer and msg.channel == "web":
        # ── Scrub any ghost / orphan tags that survived streaming ──
        if ghost_active:
            # Stream ended mid-ghost — drop everything up to (and
            # including) the closing tag, or the whole buffer if
            # the closing tag never arrived.
            closing = f"</{ghost_active}>"
            close_idx = display_buffer.find(closing)
            if close_idx != -1:
                display_buffer = display_buffer[close_idx + len(closing) :]
            else:
                display_buffer = ""
            ghost_active = None

        display_buffer = trim_leading_structural_lines(display_buffer)

        # Strip any leftover opening or closing ghost tags.
        display_buffer = _GHOST_TAG_RE.sub("", display_buffer).strip()

        if display_buffer:
            await bus.publish_outbound(
                OutboundMessage(
                    channel=msg.channel,
                    chat_id=msg.chat_id,
                    content=display_buffer,
                    metadata=with_trace_metadata(
                        {"type": "chunk"},
                        turn_id=turn_id,
                        message_id=message_id,
                    ),
                )
            )
            if on_output_queued:
                on_output_queued("content")
            streamed_to_web = True
        display_buffer = ""

    if not tool_calls and full_content:
        extracted = extract_tool_calls(full_content)
        if extracted:
            extracted_from = "assistant_content"
            tool_calls = extracted
            clean_content = re.sub(
                r"```(?:json)?\s*\{.*?\}(?:\s*```)?",
                "",
                full_content,
                flags=re.DOTALL,
            ).strip()
            # Also strip Kimi K2 inline tool syntax: functions.name:N{...}
            clean_content = re.sub(
                r"(?::?functions\.\w+(?::\d+)?\s*\{[^}]*\})",
                "",
                clean_content,
            ).strip()
            clean_content = re.sub(
                r"<(?:read_file|inspect_skill|edit_file|edit_skill|write_file|delete_file|list_dir|search_files|verify_files|diagnose_files|run_command|memory_search|memory_save|google_search|browser_navigate|spawn_agent|send_media|send_voice|generate_image|send_discord_message|send_discord_embed|list_discord_channels|save_memory|log_memory|ls|dir|list_files|cat|open_file|show_file|grep|rg|ripgrep|find_files|shell|terminal|exec|bash|powershell|cmd)>.*?</(?:read_file|inspect_skill|edit_file|edit_skill|write_file|delete_file|list_dir|search_files|verify_files|diagnose_files|run_command|memory_search|memory_save|google_search|browser_navigate|spawn_agent|send_media|send_voice|generate_image|send_discord_message|send_discord_embed|list_discord_channels|save_memory|log_memory|ls|dir|list_files|cat|open_file|show_file|grep|rg|ripgrep|find_files|shell|terminal|exec|bash|powershell|cmd)>",
                "",
                clean_content,
                flags=re.DOTALL | re.IGNORECASE,
            ).strip()
            clean_content = re.sub(
                r"<tool_code>.*?</tool_code>",
                "",
                clean_content,
                flags=re.DOTALL | re.IGNORECASE,
            ).strip()
            if clean_content:
                full_content = clean_content
            logger.info(f"✨ Extracted tool: {tool_calls[0]['function']['name']}")

    if not tool_calls and thinking_buffer:
        extracted = extract_tool_calls(thinking_buffer)
        if extracted:
            extracted_from = "reasoning_content"
            tool_calls = extracted
            logger.info(
                f"✨ Extracted tool from reasoning: {tool_calls[0]['function']['name']}"
            )

    valid_tcs = []
    for tc in tool_calls:
        if not tc.get("id"):
            tc["id"] = f"call_{uuid.uuid4().hex[:8]}"
        if not tc.get("type"):
            tc["type"] = "function"

        fn = tc.get("function", {})
        if fn.get("name"):
            # Fix common LLM hallucination: double JSON objects in arguments
            args = fn.get("arguments", "")
            if args.startswith("{") and "}{" in args:
                try:
                    idx = args.find("}{")
                    json.loads(args[: idx + 1])
                    fn["arguments"] = args[: idx + 1]
                except Exception:
                    pass
            valid_tcs.append(tc)
    tool_calls = valid_tcs

    if tool_calls and full_content:
        pre_sanitize = full_content
        full_content = sanitize_tool_call_content(full_content)
        if pre_sanitize.strip() and pre_sanitize != full_content:
            metrics.record_anomaly(
                session_key,
                "tool_call_residue_stripped",
                detail=pre_sanitize[:160],
            )
            log_tool_debug(
                "tool_residue_stripped",
                session_key=session_key,
                before=pre_sanitize,
                after=full_content,
            )
            if msg.channel == "web" and streamed_to_web:
                await bus.publish_outbound(
                    OutboundMessage(
                        channel=msg.channel,
                        chat_id=msg.chat_id,
                        content=full_content,
                        metadata=with_trace_metadata(
                            {"type": "full_content"},
                            turn_id=turn_id,
                            message_id=message_id,
                        ),
                    )
                )

    if (
        is_potential_json
        and not tool_calls
        and full_content.strip()
        and msg.channel == "web"
        and not streamed_to_web
    ):
        await bus.publish_outbound(
            OutboundMessage(
                channel=msg.channel,
                chat_id=msg.chat_id,
                content=full_content,
                metadata=with_trace_metadata(
                    {"type": "full_content"},
                    turn_id=turn_id,
                    message_id=message_id,
                ),
            )
        )

    log_tool_debug(
        "stream_summary",
        session_key=session_key,
        raw_content=full_content,
        thinking=thinking_buffer,
        tool_calls=tool_call_debug_rows(tool_calls),
        extracted_from=extracted_from if tool_calls else "none",
        streamed_to_web=streamed_to_web,
        streamed_to_discord=streamed_to_discord,
        usage=str(usage) if usage else "",
    )

    return (
        full_content,
        tool_calls,
        usage,
        streamed_to_web,
        streamed_to_discord,
        thinking_buffer,
    )
