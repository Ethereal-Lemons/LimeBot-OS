"""Recover and remove provider-specific tool syntax from assistant text."""

from __future__ import annotations

import ast
import json
import re
import uuid
from typing import Any, Dict

from core.tool_dispatcher import TOOL_NAME_ALIASES

_VISIBLE_PROVIDER_TOOL_CALL_PREFIX_RE = re.compile(
    r"(?:^|\s)to\s*=\s*functions\.(?P<name>[A-Za-z_][\w]*)"
    r"(?:\s+code)?\s*:\s*",
    re.IGNORECASE,
)


def extract_tool_calls(content: str) -> list:
    if not content or not content.strip():
        return []

    default_arg_names = {
        "read_file": "path",
        "inspect_skill": "skill_name",
        "edit_file": "edits",
        "edit_skill": "changes",
        "verify_files": "paths",
        "diagnose_files": "paths",
        "write_file": "content",
        "create_spreadsheet": "path",
        "calculate": "expression",
        "delete_file": "path",
        "list_dir": "path",
        "search_files": "query",
        "run_command": "command",
        "memory_search": "query",
        "memory_save": "content",
        "generate_image": "prompt",
        "google_search": "query",
        "browser_navigate": "url",
        "spawn_agent": "task",
        "send_media": "path",
        "send_voice": "text",
        "send_discord_message": "message",
        "send_discord_embed": "description",
        "cron_remove": "job_id",
        "save_memory": "content",
        "log_memory": "content",
    }

    def _make_tool_call(function_name: str, function_args: Dict[str, Any]) -> list:
        return [
            {
                "id": f"call_{uuid.uuid4().hex[:12]}",
                "type": "function",
                "function": {
                    "name": function_name,
                    "arguments": json.dumps(function_args),
                },
            }
        ]

    def _tool_code_call(tool_expr: str) -> list:
        raw_expr = str(tool_expr or "").strip()
        if not raw_expr:
            return []

        try:
            parsed = ast.parse(raw_expr, mode="eval")
        except Exception:
            return []

        call = parsed.body
        if not isinstance(call, ast.Call):
            return []
        if not isinstance(call.func, ast.Name):
            return []

        canonical_name = TOOL_NAME_ALIASES.get(call.func.id, call.func.id)
        arg_names = {
            "read_file": ["path"],
            "inspect_skill": ["skill_name", "path", "start_line", "end_line"],
            "edit_file": ["path", "edits", "expected_sha256"],
            "edit_skill": ["skill_name", "changes"],
            "verify_files": ["paths", "include_diagnostics", "provider"],
            "diagnose_files": ["paths", "provider", "timeout"],
            "write_file": ["path", "content"],
            "create_spreadsheet": ["path", "sheets", "title"],
            "calculate": ["expression"],
            "delete_file": ["path"],
            "list_dir": ["path"],
            "search_files": ["query", "path"],
            "run_command": ["command"],
            "memory_search": ["query"],
            "memory_save": ["content", "scope"],
            "google_search": ["query"],
            "browser_navigate": ["url"],
            "spawn_agent": ["task"],
            "cron_remove": ["job_id"],
            "save_memory": ["content"],
            "log_memory": ["content"],
        }.get(canonical_name)
        if arg_names is None:
            return []

        function_args: Dict[str, Any] = {}
        try:
            for idx, arg in enumerate(call.args):
                if idx >= len(arg_names):
                    return []
                function_args[arg_names[idx]] = ast.literal_eval(arg)
            for kw in call.keywords:
                if kw.arg is None:
                    return []
                function_args[kw.arg] = ast.literal_eval(kw.value)
        except Exception:
            return []

        return _make_tool_call(canonical_name, function_args)

    def _legacy_xml_tool_call(tag_name: str, inner_content: str) -> list:
        canonical_name = TOOL_NAME_ALIASES.get(tag_name, tag_name)
        raw_content = (inner_content or "").strip()
        if not raw_content:
            return []

        function_args: Dict[str, Any]
        if raw_content.startswith("{") and raw_content.endswith("}"):
            try:
                parsed = json.loads(raw_content)
                if isinstance(parsed, dict):
                    function_args = parsed
                else:
                    function_args = {}
            except Exception:
                function_args = {}
        else:
            default_arg_name = default_arg_names.get(canonical_name)
            if not default_arg_name:
                return []
            function_args = {default_arg_name: raw_content}

        return _make_tool_call(canonical_name, function_args)

    def _xml_attr_tool_call(tag_name: str, raw_attrs: str) -> list:
        canonical_name = TOOL_NAME_ALIASES.get(tag_name, tag_name)
        default_arg_name = default_arg_names.get(canonical_name)
        if not default_arg_name:
            return []

        attrs: Dict[str, Any] = {}
        for match in re.finditer(
            r'([A-Za-z_][\w-]*)\s*=\s*(?:"([^"]*)"|\'([^\']*)\')', raw_attrs or ""
        ):
            attrs[match.group(1)] = (
                match.group(2) if match.group(2) is not None else match.group(3)
            )

        if not attrs:
            return []

        if default_arg_name in attrs:
            function_args = {default_arg_name: attrs[default_arg_name]}
        else:
            first_value = next(iter(attrs.values()))
            function_args = {default_arg_name: first_value}
        return _make_tool_call(canonical_name, function_args)

    def _implicit_tool_call_from_dict(parsed: dict):
        if not isinstance(parsed, dict) or "name" in parsed:
            return []

        if isinstance(parsed.get("prompt"), str) and parsed["prompt"].strip():
            image_keys = {
                "model",
                "size",
                "quality",
                "count",
                "reference_images",
                "use_attached_images",
            }
            if image_keys.intersection(parsed.keys()):
                return _make_tool_call(
                    "generate_image",
                    {
                        key: value
                        for key, value in parsed.items()
                        if key
                        in {
                            "prompt",
                            "model",
                            "size",
                            "quality",
                            "count",
                            "reference_images",
                            "use_attached_images",
                        }
                    },
                )

        if isinstance(parsed.get("url"), str) and parsed["url"].strip():
            return _make_tool_call("browser_navigate", {"url": parsed["url"]})

        if isinstance(parsed.get("query"), str) and parsed["query"].strip():
            return _make_tool_call("google_search", {"query": parsed["query"]})

        cmd = parsed.get("cmd")
        if isinstance(cmd, list):
            cmd_text = " ".join(str(part or "") for part in cmd).strip().lower()
            if re.search(r"\b(ls|dir)\b", cmd_text):
                return _make_tool_call("list_dir", {"path": "."})

        return []

    try:
        cleaned = content
        if "```" in content:
            m = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", content, re.DOTALL)
            if m:
                cleaned = m.group(1)

        s = cleaned.find("{")
        e = cleaned.rfind("}")
        if s != -1 and e >= s:
            json_str = cleaned[s : e + 1]
            parsed = json.loads(json_str)
            lower = json_str.lower()
            if isinstance(parsed, dict) and "name" in parsed and '"name"' in lower and (
                '"arguments"' in lower or '"parameters"' in lower
            ):
                args = parsed.get("arguments", parsed.get("parameters", {}))
                args_str = (
                    json.dumps(args) if isinstance(args, dict) else str(args)
                )
                return [
                    {
                        "id": f"call_{uuid.uuid4().hex[:12]}",
                        "type": "function",
                        "function": {
                            "name": parsed["name"],
                            "arguments": args_str,
                        },
                    }
                ]
            implicit = _implicit_tool_call_from_dict(parsed)
            if implicit:
                return implicit
    except Exception:
        pass

    tool_calls = []
    try:
        pattern = (
            r"<\|tool_call_begin\|>\s*(?:functions\.)?([\w\.]+)(?::\d+)?\s*"
            r"<\|tool_call_argument_begin\|>\s*({.*?})\s*<\|tool_call_end\|>"
        )
        for m in re.finditer(pattern, content, re.DOTALL):
            tool_calls.append(
                {
                    "id": f"call_{uuid.uuid4().hex[:8]}",
                    "type": "function",
                    "function": {"name": m.group(1), "arguments": m.group(2)},
                }
            )
    except Exception:
        pass
    if tool_calls:
        return tool_calls

    # Some OpenAI-compatible bridges serialize a tool call using the
    # provider-facing ``to=functions.name code: {...}`` envelope. Recover
    # it instead of leaking the protocol text into the visible reply.
    try:
        provider_envelope = _VISIBLE_PROVIDER_TOOL_CALL_PREFIX_RE.search(content)
        if provider_envelope:
            raw_tail = content[provider_envelope.end() :].lstrip()
            parsed_args, _ = json.JSONDecoder().raw_decode(raw_tail)
            if isinstance(parsed_args, dict):
                return _make_tool_call(
                    provider_envelope.group("name"), parsed_args
                )
    except Exception:
        pass

    try:
        pipe_tag_pattern = re.compile(
            r"<\|(?P<tag>[A-Za-z_][\w]*)\|>\s*(?P<body>{.*?})\s*<\|/(?P=tag)\|>",
            re.DOTALL,
        )
        for match in pipe_tag_pattern.finditer(content):
            extracted = _legacy_xml_tool_call(
                match.group("tag"), match.group("body")
            )
            if extracted:
                return extracted

        tool_code_pattern = re.compile(
            r"<tool_code>\s*(?P<body>.*?)\s*</tool_code>",
            re.IGNORECASE | re.DOTALL,
        )
        for match in tool_code_pattern.finditer(content):
            extracted = _tool_code_call(match.group("body"))
            if extracted:
                return extracted

        legacy_tag_pattern = re.compile(
            r"<(?P<tag>[A-Za-z_][\w]*)>\s*(?P<body>.*?)\s*</(?P=tag)>",
            re.DOTALL,
        )
        for match in legacy_tag_pattern.finditer(content):
            extracted = _legacy_xml_tool_call(
                match.group("tag"), match.group("body")
            )
            if extracted:
                return extracted

        legacy_attr_tag_pattern = re.compile(
            r"<(?P<tag>[A-Za-z_][\w]*)\s+(?P<attrs>[^<>]*?)\s*/?>",
            re.DOTALL,
        )
        for match in legacy_attr_tag_pattern.finditer(content):
            extracted = _xml_attr_tool_call(
                match.group("tag"), match.group("attrs")
            )
            if extracted:
                return extracted

        bare_call_pattern = re.compile(
            r"\b(?:list_dir|read_file|inspect_skill|edit_file|edit_skill|write_file|delete_file|search_files|verify_files|diagnose_files|"
            r"run_command|memory_search|memory_save|google_search|browser_navigate|"
            r"spawn_agent|send_media|send_voice|generate_image|send_discord_message|send_discord_embed|list_discord_channels|cron_remove|save_memory|log_memory|ls|dir|cat|"
            r"grep|rg|ripgrep|find_files|shell|terminal|exec|bash|"
            r"powershell|cmd)\s*\([^)]*\)"
        )
        for match in bare_call_pattern.finditer(content):
            extracted = _tool_code_call(match.group(0))
            if extracted:
                return extracted
    except Exception:
        pass

    # ── Kimi K2 inline format: functions.tool_name:N{"arg":"val"} ──
    try:
        kimi_pattern = r"(?:^|\s)(?::?functions\.)(\w+)(?::\d+)?\s*(\{[^}]*\})"
        for m in re.finditer(kimi_pattern, content, re.DOTALL):
            try:
                args = json.loads(m.group(2))
            except json.JSONDecodeError:
                continue
            tool_calls.append(
                {
                    "id": f"call_{uuid.uuid4().hex[:8]}",
                    "type": "function",
                    "function": {
                        "name": m.group(1),
                        "arguments": json.dumps(args),
                    },
                }
            )
    except Exception:
        pass

    return tool_calls

def trim_leading_structural_lines(content: str) -> str:
    """Drop leading fence/bracket residue that sometimes leaks before tool calls."""
    if not content:
        return ""

    lines = content.splitlines(keepends=True)
    while lines:
        stripped = lines[0].strip()
        if not stripped:
            lines.pop(0)
            continue
        if re.fullmatch(r"[`{}\[\],:;]+", stripped):
            lines.pop(0)
            continue
        break
    return "".join(lines)

def sanitize_tool_call_content(content: str) -> str:
    """Keep only meaningful prose when the model mixes text with tool syntax."""
    if not content:
        return ""

    cleaned = content
    marker_positions = []
    legacy_tag_pattern = (
        r"<(?:read_file|inspect_skill|edit_file|edit_skill|write_file|delete_file|list_dir|search_files|verify_files|diagnose_files|run_command|"
        r"memory_search|memory_save|google_search|browser_navigate|spawn_agent|send_media|send_voice|generate_image|send_discord_message|send_discord_embed|list_discord_channels|"
        r"save_memory|log_memory|ls|dir|list_files|cat|open_file|show_file|"
        r"grep|rg|ripgrep|find_files|shell|terminal|exec|bash|powershell|cmd)>"
    )
    tool_code_pattern = r"<tool_code>"

    inline_match = re.search(r"(?::?functions\.\w+(?::\d+)?\s*\{)", cleaned)
    if inline_match:
        marker_positions.append(inline_match.start())

    visible_provider_match = _VISIBLE_PROVIDER_TOOL_CALL_PREFIX_RE.search(cleaned)
    if visible_provider_match:
        marker_positions.append(visible_provider_match.start())

    block_match = re.search(r"<\|tool_call_begin\|>", cleaned)
    if block_match:
        marker_positions.append(block_match.start())

    pipe_tag_match = re.search(r"<\|[A-Za-z_][\w]*\|>", cleaned)
    if pipe_tag_match:
        marker_positions.append(pipe_tag_match.start())

    json_tool_match = re.search(
        r'\{\s*"name"\s*:\s*"[^"]+"\s*,\s*"(?:arguments|parameters)"\s*:\s*\{',
        cleaned,
        flags=re.DOTALL,
    )
    if json_tool_match:
        marker_positions.append(json_tool_match.start())

    image_args_match = re.search(
        r'\{\s*"prompt"\s*:\s*".*?"\s*,\s*"(?:model|size|quality|count)"\s*:',
        cleaned,
        flags=re.DOTALL,
    )
    if image_args_match:
        marker_positions.append(image_args_match.start())

    legacy_tag_match = re.search(legacy_tag_pattern, cleaned, flags=re.IGNORECASE)
    if legacy_tag_match:
        marker_positions.append(legacy_tag_match.start())
    tool_code_match = re.search(tool_code_pattern, cleaned, flags=re.IGNORECASE)
    if tool_code_match:
        marker_positions.append(tool_code_match.start())

    if marker_positions:
        cleaned = cleaned[: min(marker_positions)]

    cleaned = re.sub(
        r"```(?:json)?\s*\{.*?\}\s*```",
        "",
        cleaned,
        flags=re.DOTALL | re.IGNORECASE,
    )
    cleaned = re.sub(
        r"<\|tool_call_begin\|>.*?<\|tool_call_end\|>",
        "",
        cleaned,
        flags=re.DOTALL,
    )
    cleaned = re.sub(
        r"<\|[A-Za-z_][\w]*\|>.*?<\|/[A-Za-z_][\w]*\|>",
        "",
        cleaned,
        flags=re.DOTALL,
    )
    cleaned = re.sub(
        r"(?::?functions\.\w+(?::\d+)?\s*\{[^}]*\})",
        "",
        cleaned,
    )
    cleaned = re.sub(
        legacy_tag_pattern + r".*?</(?:read_file|inspect_skill|edit_file|edit_skill|write_file|delete_file|list_dir|search_files|verify_files|diagnose_files|run_command|memory_search|memory_save|google_search|browser_navigate|spawn_agent|send_media|send_voice|generate_image|send_discord_message|send_discord_embed|list_discord_channels|save_memory|log_memory|ls|dir|list_files|cat|open_file|show_file|grep|rg|ripgrep|find_files|shell|terminal|exec|bash|powershell|cmd)>",
        "",
        cleaned,
        flags=re.DOTALL | re.IGNORECASE,
    )
    cleaned = re.sub(
        r"<tool_code>.*?</tool_code>",
        "",
        cleaned,
        flags=re.DOTALL | re.IGNORECASE,
    )
    cleaned = re.sub(
        r'^\s*\{\s*"path"\s*:\s*".*?"\s*\}\s*$',
        "",
        cleaned,
        flags=re.MULTILINE,
    )
    # Strip bare JSON tool-call objects: {"name": "...", "arguments|parameters": {...}}
    cleaned = re.sub(
        r'\{\s*"name"\s*:\s*"[^"]+"\s*,\s*"(?:arguments|parameters)"\s*:\s*\{[^}]*\}\s*\}',
        "",
        cleaned,
    )
    cleaned = re.sub(
        r'\{\s*"prompt"\s*:\s*".*?"\s*,\s*"(?:model|size|quality|count)"\s*:.*?\}\s*$',
        "",
        cleaned,
        flags=re.DOTALL,
    )
    cleaned = trim_leading_structural_lines(cleaned)
    cleaned_lines = []
    for line in cleaned.splitlines():
        stripped = line.strip()
        if stripped and re.fullmatch(r"[`{}\[\],:;]+", stripped):
            continue
        cleaned_lines.append(line)
    cleaned = "\n".join(cleaned_lines).strip()

    try:
        parsed = json.loads(cleaned)
        if isinstance(parsed, dict):
            if isinstance(parsed.get("prompt"), str) and {
                "model",
                "size",
                "quality",
                "count",
            }.intersection(parsed.keys()):
                return ""
            if isinstance(parsed.get("url"), str) and parsed["url"].strip():
                return ""
            if isinstance(parsed.get("query"), str) and parsed["query"].strip():
                return ""
    except Exception:
        pass

    if not re.search(r"[A-Za-z0-9]", cleaned):
        return ""
    return cleaned
