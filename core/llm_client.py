"""Small OpenAI-compatible chat client for LimeBot.

LimeBot's canonical internal chat format is OpenAI-compatible messages plus
OpenAI function tool schemas. LiteLLM handles provider-specific execution for
most providers. core.codex_bridge is the adapter for openai-codex/*.
"""

from __future__ import annotations

import asyncio
import base64
import copy
from dataclasses import dataclass
import logging
import mimetypes
from pathlib import Path
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)


def _resolve_local_image_urls(messages: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Scan messages for local image URLs (e.g. starting with '/temp/') and resolve them to base64 data URLs.
    
    This avoids LiteLLM/OpenAI throwing 'Invalid URL format' for relative/absolute local file paths.
    """
    copied_messages = copy.deepcopy(messages)
    for msg in copied_messages:
        if not isinstance(msg, dict):
            continue
        content = msg.get("content")
        if isinstance(content, list):
            for part in content:
                if isinstance(part, dict) and part.get("type") == "image_url":
                    image_url_obj = part.get("image_url")
                    if isinstance(image_url_obj, dict):
                        url = image_url_obj.get("url")
                        if isinstance(url, str) and (url.startswith("/temp/") or url.startswith("temp/")):
                            # Remove leading slash to make it relative to current working directory
                            local_path = Path.cwd() / url.lstrip('/')
                            if local_path.exists() and local_path.is_file():
                                try:
                                    mime_type, _ = mimetypes.guess_type(local_path)
                                    if not mime_type:
                                        mime_type = "image/png"
                                    data = local_path.read_bytes()
                                    encoded = base64.b64encode(data).decode("utf-8")
                                    image_url_obj["url"] = f"data:{mime_type};base64,{encoded}"
                                except Exception as e:
                                    logger.error(f"Error encoding local image {local_path}: {e}")
                            else:
                                logger.warning(f"Local image path {local_path} does not exist or is not a file.")
    return copied_messages

try:
    from litellm import acompletion
except Exception:
    acompletion = None

from core.codex_bridge import (
    complete_codex_response,
    is_codex_model_name,
    stream_codex_response,
)
from core.llm_utils import build_provider_chain, resolve_provider_config


@dataclass(frozen=True)
class ProviderConfig:
    source_model: str
    model: str
    base_url: Optional[str]
    api_key: Optional[str]
    custom_llm_provider: Optional[str]
    is_codex: bool = False


@dataclass(frozen=True)
class ChatRequest:
    messages: List[Dict[str, Any]]
    tools: Optional[List[Dict[str, Any]]] = None
    stream: bool = False
    max_tokens: Optional[int] = None
    session_id: Optional[str] = None
    tool_choice: Optional[str] = "auto"


class LimeLLMClient:
    @staticmethod
    def _provider_from_mapping(
        source_model: str, provider_cfg: Dict[str, Any]
    ) -> ProviderConfig:
        return ProviderConfig(
            source_model=source_model,
            model=provider_cfg["model"],
            base_url=provider_cfg["base_url"],
            api_key=provider_cfg["api_key"],
            custom_llm_provider=provider_cfg["custom_llm_provider"],
            is_codex=is_codex_model_name(source_model),
        )

    def resolve_provider(
        self, model: str, default_base_url: Optional[str] = None
    ) -> ProviderConfig:
        provider_cfg = resolve_provider_config(model, default_base_url=default_base_url)
        return self._provider_from_mapping(model, provider_cfg)

    def resolve_chain(
        self,
        primary_model: str,
        fallback_models: List[str],
        default_base_url: Optional[str] = None,
    ) -> List[ProviderConfig]:
        chain = build_provider_chain(
            primary_model,
            fallback_models,
            default_base_url=default_base_url,
        )
        return [
            self._provider_from_mapping(source_model, provider_cfg)
            for source_model, provider_cfg in chain
        ]

    async def complete(self, provider: ProviderConfig, request: ChatRequest) -> Any:
        messages = _resolve_local_image_urls(request.messages)
        if provider.is_codex:
            helper = stream_codex_response if request.stream else complete_codex_response
            return await asyncio.to_thread(
                helper,
                provider.source_model,
                messages,
                request.tools,
                request.session_id,
            )

        if acompletion is None:
            raise RuntimeError("litellm is not installed")

        kwargs: Dict[str, Any] = {
            "model": provider.model,
            "messages": messages,
            "stream": request.stream,
            "base_url": provider.base_url,
            "api_key": provider.api_key,
            "custom_llm_provider": provider.custom_llm_provider,
        }

        if request.tools:
            kwargs["tools"] = request.tools
            if request.tool_choice is not None:
                kwargs["tool_choice"] = request.tool_choice
        if request.stream:
            kwargs["stream_options"] = {"include_usage": True}
        if request.max_tokens is not None:
            kwargs["max_tokens"] = request.max_tokens

        return await acompletion(**kwargs)
