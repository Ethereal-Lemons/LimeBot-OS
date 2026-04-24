from __future__ import annotations

import json
import shutil
import subprocess
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional


def is_codex_model_name(model: str | None) -> bool:
    return str(model or "").strip().startswith("openai-codex/")


def normalize_codex_model_id(model: str) -> str:
    return str(model or "").strip().removeprefix("openai-codex/")


def _node_executable() -> str:
    return shutil.which("node") or "node"


def _tool_arguments_to_object(arguments: Any) -> Dict[str, Any]:
    if isinstance(arguments, dict):
        return arguments
    raw = str(arguments or "").strip()
    if not raw:
        return {}
    try:
        parsed = json.loads(raw)
        return parsed if isinstance(parsed, dict) else {}
    except Exception:
        return {}


def _data_url_to_image_block(url: str) -> Optional[Dict[str, str]]:
    raw = str(url or "").strip()
    if not raw.startswith("data:image/") or ";base64," not in raw:
        return None
    header, data = raw.split(";base64,", 1)
    mime_type = header.removeprefix("data:").strip() or "image/png"
    data = data.strip()
    if not data:
        return None
    return {"type": "image", "data": data, "mimeType": mime_type}


def _content_blocks_to_text(content: Any) -> str:
    if isinstance(content, str):
        return content
    if not isinstance(content, list):
        return str(content or "")

    parts: List[str] = []
    for item in content:
        if not isinstance(item, dict):
            continue
        item_type = str(item.get("type") or "").strip()
        if item_type == "text":
            text = str(item.get("text") or "").strip()
            if text:
                parts.append(text)
        elif item_type == "image_url":
            url = str(item.get("image_url", {}).get("url") or "").strip()
            note = "[Image attachment omitted for Codex bridge]"
            if url:
                note = f"{note}: {url}"
            parts.append(note)
    return "\n".join(parts).strip()


def _content_blocks_to_codex_user_content(content: Any) -> str | List[Dict[str, str]]:
    if isinstance(content, str):
        return content
    if not isinstance(content, list):
        return str(content or "")

    blocks: List[Dict[str, str]] = []
    fallback_text_parts: List[str] = []
    for item in content:
        if not isinstance(item, dict):
            continue
        item_type = str(item.get("type") or "").strip()
        if item_type == "text":
            text = str(item.get("text") or "").strip()
            if text:
                blocks.append({"type": "text", "text": text})
                fallback_text_parts.append(text)
            continue
        if item_type == "image_url":
            url = str(item.get("image_url", {}).get("url") or "").strip()
            image_block = _data_url_to_image_block(url)
            if image_block:
                blocks.append(image_block)
            elif url:
                fallback_text_parts.append(
                    f"[Image attachment available by URL, but not inlined]: {url}"
                )

    if any(block.get("type") == "image" for block in blocks):
        return blocks
    return "\n".join(part for part in fallback_text_parts if part).strip()


def build_codex_context(
    messages: List[Dict[str, Any]],
    tools: Optional[List[Dict[str, Any]]] = None,
) -> Dict[str, Any]:
    system_parts: List[str] = []
    codex_messages: List[Dict[str, Any]] = []
    timestamp_ms = int(time.time() * 1000)

    for message in messages or []:
        if not isinstance(message, dict):
            continue
        role = str(message.get("role") or "").strip()
        content = message.get("content")

        if role == "system":
            text = _content_blocks_to_text(content)
            if text:
                system_parts.append(text)
            continue

        if role == "user":
            user_content = _content_blocks_to_codex_user_content(content)
            codex_messages.append(
                {
                    "role": "user",
                    "content": user_content,
                    "timestamp": timestamp_ms,
                }
            )
            continue

        if role == "assistant":
            assistant_blocks: List[Dict[str, Any]] = []
            text = _content_blocks_to_text(content)
            if text:
                assistant_blocks.append({"type": "text", "text": text})

            tool_calls = message.get("tool_calls")
            if isinstance(tool_calls, list):
                for tool_call in tool_calls:
                    if not isinstance(tool_call, dict):
                        continue
                    function = tool_call.get("function") or {}
                    name = str(function.get("name") or "").strip()
                    if not name:
                        continue
                    assistant_blocks.append(
                        {
                            "type": "toolCall",
                            "id": str(tool_call.get("id") or ""),
                            "name": name,
                            "arguments": _tool_arguments_to_object(
                                function.get("arguments")
                            ),
                        }
                    )

            codex_messages.append(
                {
                    "role": "assistant",
                    "content": assistant_blocks,
                    "api": "openai-codex-responses",
                    "provider": "openai-codex",
                    "model": "",
                    "usage": {
                        "input": 0,
                        "output": 0,
                        "cacheRead": 0,
                        "cacheWrite": 0,
                        "totalTokens": 0,
                        "cost": {
                            "input": 0,
                            "output": 0,
                            "cacheRead": 0,
                            "cacheWrite": 0,
                            "total": 0,
                        },
                    },
                    "stopReason": "stop",
                    "timestamp": timestamp_ms,
                }
            )
            continue

        if role == "tool":
            codex_messages.append(
                {
                    "role": "toolResult",
                    "toolCallId": str(message.get("tool_call_id") or ""),
                    "toolName": str(message.get("name") or ""),
                    "content": [{"type": "text", "text": _content_blocks_to_text(content)}],
                    "isError": False,
                    "timestamp": timestamp_ms,
                }
            )

    normalized_tools: List[Dict[str, Any]] = []
    for tool in tools or []:
        if not isinstance(tool, dict):
            continue
        function = tool.get("function") if tool.get("type") == "function" else tool
        if not isinstance(function, dict):
            continue
        name = str(function.get("name") or "").strip()
        if not name:
            continue
        normalized_tools.append(
            {
                "name": name,
                "description": str(function.get("description") or ""),
                "parameters": function.get("parameters") or {"type": "object", "properties": {}},
            }
        )

    return {
        "systemPrompt": "\n\n".join(part for part in system_parts if part).strip(),
        "messages": codex_messages,
        "tools": normalized_tools or None,
    }


def _run_codex_bridge(payload: Dict[str, Any]) -> Dict[str, Any]:
    script_path = Path.cwd() / "scripts" / "codex-chat.mjs"
    if not script_path.exists():
        raise RuntimeError(f"Codex chat helper not found at {script_path}")

    proc = subprocess.run(
        [_node_executable(), str(script_path), "complete", "--json"],
        cwd=Path.cwd(),
        input=json.dumps(payload),
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=False,
    )
    stdout = (proc.stdout or "").strip()
    stderr = (proc.stderr or "").strip()
    if proc.returncode != 0:
        detail = stderr or stdout or "unknown Codex bridge failure"
        raise RuntimeError(detail)

    try:
        parsed = json.loads(stdout)
    except Exception as exc:
        raise RuntimeError("Codex chat helper returned invalid JSON.") from exc

    if isinstance(parsed, dict) and parsed.get("error"):
        raise RuntimeError(str(parsed["error"]))
    if not isinstance(parsed, dict):
        raise RuntimeError("Codex chat helper returned an unexpected payload.")
    return parsed


def _build_usage(payload: Dict[str, Any]) -> Dict[str, int]:
    usage = payload.get("usage") if isinstance(payload.get("usage"), dict) else {}
    prompt_tokens = int(usage.get("input") or 0)
    completion_tokens = int(usage.get("output") or 0)
    total_tokens = int(usage.get("totalTokens") or (prompt_tokens + completion_tokens))
    return {
        "prompt_tokens": prompt_tokens,
        "completion_tokens": completion_tokens,
        "total_tokens": total_tokens,
    }


@dataclass
class CodexFunctionCall:
    name: str
    arguments: str


@dataclass
class CodexToolCall:
    id: str
    function: CodexFunctionCall
    type: str = "function"


class CodexAssistantMessage:
    def __init__(
        self,
        content: str,
        tool_calls: Optional[List[CodexToolCall]] = None,
        reasoning_content: str = "",
    ) -> None:
        self.role = "assistant"
        self.content = content
        self.tool_calls = tool_calls or []
        self.reasoning_content = reasoning_content

    def model_dump(self) -> Dict[str, Any]:
        payload: Dict[str, Any] = {"role": "assistant", "content": self.content}
        if self.tool_calls:
            payload["tool_calls"] = [
                {
                    "id": tool_call.id,
                    "type": tool_call.type,
                    "function": {
                        "name": tool_call.function.name,
                        "arguments": tool_call.function.arguments,
                    },
                }
                for tool_call in self.tool_calls
            ]
        return payload


class CodexResponse:
    def __init__(self, message: CodexAssistantMessage, usage: Dict[str, int]) -> None:
        self.choices = [type("Choice", (), {"message": message})()]
        self.usage = usage


class CodexToolCallDelta:
    def __init__(self, index: int, tool_call: CodexToolCall) -> None:
        self.index = index
        self.id = tool_call.id
        self.function = type(
            "FunctionDelta",
            (),
            {
                "name": tool_call.function.name,
                "arguments": tool_call.function.arguments,
            },
        )()


class CodexSyntheticStream:
    def __init__(self, response: CodexResponse) -> None:
        self._response = response
        self._done = False

    def __aiter__(self):
        return self

    async def __anext__(self):
        if self._done:
            raise StopAsyncIteration
        self._done = True
        message = self._response.choices[0].message
        delta = type(
            "Delta",
            (),
            {
                "content": message.content or None,
                "tool_calls": [
                    CodexToolCallDelta(index, tool_call)
                    for index, tool_call in enumerate(message.tool_calls)
                ]
                or None,
                "reasoning_content": message.reasoning_content or None,
                "thinking": None,
            },
        )()
        choice = type("Choice", (), {"delta": delta})()
        return type("Chunk", (), {"choices": [choice], "usage": self._response.usage})()


def complete_codex_response(
    model: str,
    messages: List[Dict[str, Any]],
    tools: Optional[List[Dict[str, Any]]] = None,
    session_id: Optional[str] = None,
) -> CodexResponse:
    payload = {
        "model": normalize_codex_model_id(model),
        "context": build_codex_context(messages, tools),
        "sessionId": session_id,
    }
    result = _run_codex_bridge(payload)

    tool_calls: List[CodexToolCall] = []
    for tool_call in result.get("toolCalls") or []:
        if not isinstance(tool_call, dict):
            continue
        tool_calls.append(
            CodexToolCall(
                id=str(tool_call.get("id") or ""),
                function=CodexFunctionCall(
                    name=str(tool_call.get("name") or ""),
                    arguments=json.dumps(tool_call.get("arguments") or {}),
                ),
            )
        )

    message = CodexAssistantMessage(
        content=str(result.get("text") or ""),
        tool_calls=tool_calls,
        reasoning_content=str(result.get("thinking") or ""),
    )
    return CodexResponse(message=message, usage=_build_usage(result))


def stream_codex_response(
    model: str,
    messages: List[Dict[str, Any]],
    tools: Optional[List[Dict[str, Any]]] = None,
    session_id: Optional[str] = None,
) -> CodexSyntheticStream:
    return CodexSyntheticStream(
        complete_codex_response(
            model=model,
            messages=messages,
            tools=tools,
            session_id=session_id,
        )
    )
