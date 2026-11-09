"""Retry LLM requests and coordinate provider failover."""

import asyncio
from dataclasses import dataclass
from typing import Any, Callable, Dict, List, Optional, Set, Tuple

from loguru import logger

from core.events import InboundMessage, OutboundMessage
from core.llm_client import ChatRequest
from core.prompt_prefix import relocate_per_message_context
from core.provider_circuit_breaker import ProviderCircuitOpenError


@dataclass(frozen=True)
class ProviderErrorTypes:
    authentication: type[BaseException]
    rate_limit: type[BaseException]
    internal_server: type[BaseException]
    api_connection: type[BaseException]
    service_unavailable: type[BaseException]

    @property
    def transient(self) -> Tuple[type[BaseException], ...]:
        return (
            self.rate_limit,
            self.internal_server,
            self.api_connection,
            self.service_unavailable,
        )


async def llm_call_with_retry(
    messages: List[Dict],
    session_key: str,
    msg: Optional[InboundMessage],
    max_retries: int = 3,
    stream: bool = False,
    include_tools: bool = True,
    tool_context_text: str = "",
    turn_id: Optional[str] = None,
    message_id: Optional[str] = None,
    tool_definitions_override: Optional[List[Dict[str, Any]]] = None,
    model_override: Optional[str] = None,
    tool_choice: Optional[str] = "auto",
    *,
    config: Any,
    bus: Any,
    llm_client: Any,
    sanitize_messages: Callable[..., List[Dict]],
    get_tool_definitions: Callable[..., List[Dict[str, Any]]],
    resolve_provider_chain: Callable[..., Any],
    get_provider_circuit_breaker: Callable[..., Any],
    provider_circuit_identity: Callable[..., Any],
    build_provider_config: Callable[..., Any],
    log_tool_debug: Callable[..., None],
    tool_definition_names: Callable[..., List[str]],
    should_failover_model: Callable[[Exception], bool],
    should_retry_without_images: Callable[..., bool],
    downgrade_image_messages_for_text_model: Callable[..., bool],
    disable_image_inputs_for_session: Callable[[str], None],
    with_trace_metadata: Callable[..., Dict[str, Any]],
    get_model: Callable[[], str],
    error_types: ProviderErrorTypes,
) -> Any:

    try:
        max_retries = max(1, int(max_retries or 1))
    except (TypeError, ValueError):
        max_retries = 1
    messages = sanitize_messages(messages, session_key)
    if not include_tools:
        tools = []
    elif tool_definitions_override is not None:
        tools = tool_definitions_override
    else:
        tools = get_tool_definitions(
            tool_context_text, session_key=session_key
        )
    if model_override and model_override != "inherit":
        override_provider = llm_client.resolve_provider(
            model_override,
            default_base_url=config.llm.base_url,
        )
        provider_chain = [
            (
                override_provider.source_model,
                override_provider.model,
                override_provider.base_url,
                override_provider.api_key,
                override_provider.custom_llm_provider,
            )
        ]
    else:
        provider_chain = resolve_provider_chain()
    if not provider_chain:
        raise RuntimeError("No LLM provider is configured")

    breaker = get_provider_circuit_breaker()

    def _select_provider(start_index: int):
        for index in range(start_index, len(provider_chain)):
            candidate = provider_chain[index]
            circuit_key, credential_fingerprint = provider_circuit_identity(
                *candidate
            )
            if breaker.allow(
                circuit_key,
                credential_fingerprint=credential_fingerprint,
            ):
                return index, candidate, circuit_key, credential_fingerprint
        return None

    selection = _select_provider(0)
    if selection is None:
        raise ProviderCircuitOpenError("all configured providers")
    selected_index, candidate, circuit_key, credential_fingerprint = selection
    (
        active_source_model,
        model,
        base_url,
        api_key,
        custom_llm_provider,
    ) = candidate
    log_tool_debug(
        "llm_call_prepare",
        session_key=session_key,
        model=active_source_model,
        stream=stream,
        include_tools=include_tools,
        tool_context=tool_context_text,
        message_count=len(messages),
        tool_count=len(tools),
        tool_names=tool_definition_names(tools),
        last_message=messages[-1].get("content", "") if messages else "",
        fallback_chain=[item[0] for item in provider_chain],
        tool_choice=tool_choice,
    )
    qwen_auth_failover_attempted: Set[str] = set()
    image_fallback_attempted = False
    model_failover_announced = False
    last_provider_error: Optional[Exception] = None

    for attempt in range(max_retries):
        try:
            provider = build_provider_config(
                active_source_model,
                model,
                base_url,
                api_key,
                custom_llm_provider,
            )
            llm_timeout = float(getattr(config, "command_timeout", 300.0) or 0)
            llm_call = llm_client.complete(
                provider,
                ChatRequest(
                    messages=relocate_per_message_context(messages),
                    tools=tools or None,
                    stream=stream,
                    session_id=session_key,
                    tool_choice=tool_choice,
                ),
            )
            if llm_timeout > 0:
                response = await asyncio.wait_for(llm_call, timeout=llm_timeout)
            else:
                response = await llm_call
            breaker.record_success(
                circuit_key,
                credential_fingerprint=credential_fingerprint,
            )
            return response
        except error_types.authentication as e:
            last_provider_error = e
            failed_source_model = active_source_model
            breaker.record_failure(
                circuit_key,
                credential_fingerprint=credential_fingerprint,
                authentication=True,
            )
            log_tool_debug(
                "llm_call_error",
                session_key=session_key,
                attempt=attempt + 1,
                source_model=active_source_model,
                error_type=type(e).__name__,
                error=str(e),
            )
            selection = _select_provider(selected_index + 1)
            if selection:
                selected_index, candidate, circuit_key, credential_fingerprint = selection
                (
                    active_source_model,
                    model,
                    base_url,
                    api_key,
                    custom_llm_provider,
                ) = candidate
                qwen_auth_failover_attempted.clear()
                logger.warning(
                    f"LLM auth failed for '{failed_source_model}'; switching to fallback '{active_source_model}'."
                )
                if msg and not model_failover_announced:
                    await bus.publish_outbound(
                        OutboundMessage(
                            content=f"Switching AI provider to fallback model `{active_source_model}`...",
                            channel=msg.channel,
                            chat_id=msg.chat_id,
                            metadata=with_trace_metadata(
                                {
                                    "reply_to": msg.sender_id,
                                    "is_warning": True,
                                    "event_kind": "turn_progress",
                                    "terminal": False,
                                },
                                turn_id=turn_id,
                                message_id=message_id,
                            ),
                        )
                    )
                    model_failover_announced = True
                continue
            # DashScope keys can be region-scoped. If auth fails on one endpoint,
            # try other official compatible endpoints before failing.
            current_base = (base_url or "").lower()
            is_qwen = (active_source_model or "").startswith(
                "qwen/"
            ) or "dashscope" in current_base
            if is_qwen:
                qwen_auth_failover_attempted.add(current_base)
                fallback_bases = [
                    "https://dashscope-intl.aliyuncs.com/compatible-mode/v1",
                    "https://dashscope-us.aliyuncs.com/compatible-mode/v1",
                    "https://dashscope.aliyuncs.com/compatible-mode/v1",
                ]
                next_base = next(
                    (
                        url
                        for url in fallback_bases
                        if url.lower() not in qwen_auth_failover_attempted
                    ),
                    None,
                )
                if next_base:
                    base_url = next_base
                    logger.warning(
                        f"Qwen auth failed; retrying with alternate endpoint: {base_url}"
                    )
                    continue
            raise

        except error_types.transient as e:
            last_provider_error = e
            failed_source_model = active_source_model
            breaker.record_failure(
                circuit_key,
                credential_fingerprint=credential_fingerprint,
            )
            log_tool_debug(
                "llm_call_error",
                session_key=session_key,
                attempt=attempt + 1,
                source_model=active_source_model,
                error_type=type(e).__name__,
                error=str(e),
            )
            is_500 = isinstance(e, error_types.internal_server)
            is_conn = isinstance(e, (error_types.api_connection, error_types.service_unavailable))
            wait_time = (2**attempt) * 5
            selection = _select_provider(selected_index + 1)
            if selection:
                selected_index, candidate, circuit_key, credential_fingerprint = selection
                (
                    active_source_model,
                    model,
                    base_url,
                    api_key,
                    custom_llm_provider,
                ) = candidate
                qwen_auth_failover_attempted.clear()
                logger.warning(
                    f"LLM provider '{failed_source_model}' failed; switching to fallback '{active_source_model}'."
                )
                if msg and not model_failover_announced:
                    err_str = str(e).lower()
                    if (
                        "rate limit" in err_str
                        or "rate_limit" in err_str
                        or "usage_limit" in err_str
                        or "usage limit" in err_str
                        or "limit has been reached" in err_str
                        or "limit_reached" in err_str
                        or "codex provider returned an error" in err_str
                        or "no visible response" in err_str
                        or isinstance(e, error_types.rate_limit)
                        or "RateLimit" in type(e).__name__
                    ):
                        reason = "rate limit or usage limit"
                    elif is_conn:
                        reason = "connection error"
                    elif is_500:
                        reason = "server error"
                    else:
                        reason = "temporary error"
                    await bus.publish_outbound(
                        OutboundMessage(
                            content=f"Primary AI model hit a {reason}, switching to fallback model `{active_source_model}`...",
                            channel=msg.channel,
                            chat_id=msg.chat_id,
                            metadata=with_trace_metadata(
                                {
                                    "reply_to": msg.sender_id,
                                    "is_warning": True,
                                    "event_kind": "turn_progress",
                                    "terminal": False,
                                },
                                turn_id=turn_id,
                                message_id=message_id,
                            ),
                        )
                    )
                    model_failover_announced = True
                continue
            if is_conn:
                error_type = "Connection error"
            elif is_500:
                error_type = "Server error (500)"
            else:
                error_type = "Rate limit"
            logger.warning(
                f"⚠ {error_type} attempt {attempt + 1}/{max_retries}. Waiting {wait_time}s…"
            )

            if attempt == 0 and msg:
                if is_conn:
                    text = "⏳ Connection lost — retrying when network is back…"
                elif is_500:
                    text = "⏳ AI service unstable — retrying…"
                else:
                    text = "⏳ Rate limit hit — retrying…"
                await bus.publish_outbound(
                    OutboundMessage(
                        content=text,
                        channel=msg.channel,
                        chat_id=msg.chat_id,
                        metadata=with_trace_metadata(
                            {
                                "reply_to": msg.sender_id,
                                "is_warning": True,
                                "event_kind": "turn_progress",
                                "terminal": False,
                            },
                            turn_id=turn_id,
                            message_id=message_id,
                        ),
                    )
                )

            # The outer turn controller owns the single terminal error
            # message. Emitting one here and then raising produced two
            # visible failures for the same provider outage.
            if attempt >= max_retries - 1:
                raise
            await asyncio.sleep(wait_time)
            continue
        except asyncio.CancelledError:
            breaker.record_aborted(
                circuit_key,
                credential_fingerprint=credential_fingerprint,
            )
            raise
        except asyncio.TimeoutError as e:
            breaker.record_failure(
                circuit_key,
                credential_fingerprint=credential_fingerprint,
            )
            log_tool_debug(
                "llm_call_error",
                session_key=session_key,
                attempt=attempt + 1,
                source_model=active_source_model,
                error_type=type(e).__name__,
                error=f"Timed out after {llm_timeout:g}s",
            )
            raise TimeoutError(
                f"AI provider timed out after {llm_timeout:g}s. Try again or switch models."
            ) from e
        except Exception as e:
            last_provider_error = e
            failed_source_model = active_source_model
            auth_like_error = any(
                marker in str(e or "").lower()
                for marker in ("incorrect api key", "invalid api key", "authentication", "auth failed")
            )
            if auth_like_error:
                breaker.record_failure(
                    circuit_key,
                    credential_fingerprint=credential_fingerprint,
                    authentication=True,
                )
            elif should_failover_model(e):
                breaker.record_failure(
                    circuit_key,
                    credential_fingerprint=credential_fingerprint,
                )
            log_tool_debug(
                "llm_call_error",
                session_key=session_key,
                attempt=attempt + 1,
                source_model=active_source_model,
                error_type=type(e).__name__,
                error=str(e),
            )
            failover_worthy = should_failover_model(e)
            selection = (
                _select_provider(selected_index + 1)
                if failover_worthy
                else None
            )
            if selection and failover_worthy:
                selected_index, candidate, circuit_key, credential_fingerprint = selection
                (
                    active_source_model,
                    model,
                    base_url,
                    api_key,
                    custom_llm_provider,
                ) = candidate
                qwen_auth_failover_attempted.clear()
                logger.warning(
                    f"LLM call failed for '{failed_source_model}'; switching to fallback '{active_source_model}'."
                )
                if msg and not model_failover_announced:
                    err_msg = str(e).lower()
                    if (
                        "rate limit" in err_msg
                        or "rate_limit" in err_msg
                        or "usage_limit" in err_msg
                        or "usage limit" in err_msg
                        or "limit has been reached" in err_msg
                        or "limit_reached" in err_msg
                        or "codex provider returned an error" in err_msg
                        or "no visible response" in err_msg
                        or "429" in err_msg
                    ):
                        reason_phrase = "rate limit or usage limit being reached"
                    elif "auth" in err_msg or "api key" in err_msg or "authentication" in err_msg:
                        reason_phrase = "authentication failure"
                    else:
                        reason_phrase = "an error"
                    await bus.publish_outbound(
                        OutboundMessage(
                            content=f"Primary AI model failed due to {reason_phrase}, switching to fallback model `{active_source_model}`...",
                            channel=msg.channel,
                            chat_id=msg.chat_id,
                            metadata=with_trace_metadata(
                                {
                                    "reply_to": msg.sender_id,
                                    "is_warning": True,
                                    "event_kind": "turn_progress",
                                    "terminal": False,
                                },
                                turn_id=turn_id,
                                message_id=message_id,
                            ),
                        )
                    )
                    model_failover_announced = True
                continue
            if (
                not image_fallback_attempted
                and should_retry_without_images(e, messages)
                and downgrade_image_messages_for_text_model(
                    messages, session_key
                )
            ):
                image_fallback_attempted = True
                disable_image_inputs_for_session(session_key)
                logger.warning(
                    f"Model '{get_model()}' rejected image input for {session_key}; retrying without multimodal content."
                )
                continue
            raise

    # A provider-specific compatibility branch can exhaust the retry loop
    # via ``continue`` on its final attempt. Never let that silently return
    # ``None`` to the turn controller; preserve the concrete provider error
    # so recovery and the terminal event have evidence.
    if last_provider_error is not None:
        raise last_provider_error
    raise RuntimeError("LLM provider call ended without a response")
