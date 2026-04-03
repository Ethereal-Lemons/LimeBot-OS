"""
core/tool_dispatcher.py
───────────────────────
Tool routing, alias normalization, and browser/tag execution helpers,
extracted from AgentLoop.

The ToolDispatcher keeps the constants and routing logic that previously
lived at module level and in AgentLoop methods, making loop.py significantly
leaner.
"""

from __future__ import annotations

import re
from typing import Any, Dict, Tuple


# ── Module-level constants (re-exported so loop.py can import them) ───────

TOOL_RESULT_LIMITS: Dict[str, int] = {
    "read_file": 8_000,
    "search_files": 5_000,
    "memory_search": 3_000,
    "browser_extract": 5_000,
    "browser_get_page_text": 5_000,
    "browser_snapshot": 3_000,
    "google_search": 2_000,
    "run_command": 2_000,
    "browser_list_media": 1_000,
    "list_dir": 500,
}
DEFAULT_TOOL_RESULT_LIMIT = 2_000

# Browser tools operate on mutable, session-local state — caching is unsafe.
BROWSER_CACHEABLE: frozenset = frozenset()

TAG_COMPAT_TOOLS = frozenset({"save_memory", "log_memory"})

TOOL_INTENT_RE = re.compile(
    r"\b("
    # Exact tool names
    r"read_file|write_file|delete_file|list_dir|search_files|run_command|memory_search|"
    r"cron_add|cron_list|cron_remove|spawn_agent|"
    # Browser tool names
    r"browser_navigate|browser_click|browser_type|browser_snapshot|browser_scroll|"
    r"browser_wait|browser_press_key|browser_go_back|browser_tabs|browser_switch_tab|"
    r"browser_extract|browser_get_page_text|browser_list_media|google_search|"
    # System command keywords
    r"ls|pwd|cat|grep|find|mkdir|rm|cp|mv|npm|pip|python|bash|powershell|terminal|"
    r"command|directory|folder|path|cron|schedule|skill|"
    # Action verbs that imply tool usage
    r"search|browse|download|upload|install|uninstall|deploy|execute|"
    r"open|save|fetch|scrape|navigate|lookup|look\s+up|"
    # Object nouns that imply tool-backed actions
    r"image|photo|picture|screenshot|website|webpage|internet|"
    r"url|http|www|\.com|\.org|\.net|"
    r"file|code|script|project|repo|"
    r"reminder|alarm|timer|notify"
    r")\b",
    re.IGNORECASE,
)

# Canonical aliases for tool names the LLM sometimes uses
TOOL_NAME_ALIASES: Dict[str, str] = {
    "ls": "list_dir",
    "dir": "list_dir",
    "list_files": "list_dir",
    "cat": "read_file",
    "open_file": "read_file",
    "show_file": "read_file",
    "grep": "search_files",
    "rg": "search_files",
    "ripgrep": "search_files",
    "find_files": "search_files",
    "shell": "run_command",
    "terminal": "run_command",
    "exec": "run_command",
    "bash": "run_command",
    "powershell": "run_command",
    "cmd": "run_command",
}

FILESYSTEM_ALIAS_ACTIONS: Dict[str, str] = {
    "list": "list_dir",
    "read": "read_file",
    "write": "write_file",
    "delete": "delete_file",
    "find": "search_files",
    "search": "search_files",
}


def normalize_tool_alias(
    function_name: str,
    function_args: dict,
    record_anomaly_fn,
    session_key: str,
) -> Tuple[str, dict]:
    """Normalize common alias tools back to canonical runtime tool names.

    Parameters
    ----------
    function_name:
        Raw tool name received from the LLM.
    function_args:
        Raw argument dict.
    record_anomaly_fn:
        Callable matching ``MetricsCollector.record_anomaly`` signature,
        used to log alias normalization events.
    session_key:
        Current session ID, forwarded to the anomaly recorder.

    Returns
    -------
    (canonical_name, normalized_args)
    """
    normalized_name = TOOL_NAME_ALIASES.get(function_name, function_name)
    if normalized_name == function_name and function_name.endswith("json"):
        trimmed_name = function_name[: -len("json")]
        normalized_name = TOOL_NAME_ALIASES.get(trimmed_name, trimmed_name)
    normalized_args = dict(function_args or {})

    if normalized_name != function_name:
        record_anomaly_fn(
            session_key,
            "tool_alias_normalized",
            detail=f"{function_name}->{normalized_name}",
        )

        if normalized_name == "list_dir":
            normalized_args = {
                "path": normalized_args.get("path")
                or normalized_args.get("directory")
                or normalized_args.get("cwd")
                or ".",
                **{
                    k: v
                    for k, v in normalized_args.items()
                    if k
                    in {
                        "limit",
                        "offset",
                        "include_hidden",
                        "sort_by",
                        "descending",
                        "folders_first",
                    }
                },
            }
        elif normalized_name == "read_file":
            normalized_args = {
                "path": normalized_args.get("path")
                or normalized_args.get("file")
                or normalized_args.get("filename")
                or "",
                "start_line": normalized_args.get("start_line")
                or normalized_args.get("line_start"),
                "end_line": normalized_args.get("end_line")
                or normalized_args.get("line_end"),
                **{
                    k: v
                    for k, v in normalized_args.items()
                    if k in {"max_chars"}
                },
            }
        elif normalized_name == "search_files":
            normalized_args = {
                "query": normalized_args.get("query")
                or normalized_args.get("pattern")
                or normalized_args.get("text")
                or normalized_args.get("name")
                or "",
                "path": normalized_args.get("path", "."),
                "mode": normalized_args.get("mode", "content"),
                **{
                    k: v
                    for k, v in normalized_args.items()
                    if k in {"file_glob", "case_sensitive", "max_results"}
                },
            }
        elif normalized_name == "run_command":
            normalized_args = {
                "command": normalized_args.get("command")
                or normalized_args.get("cmd")
                or normalized_args.get("script")
                or "",
            }

    if normalized_name != "filesystem":
        return normalized_name, normalized_args

    # ── filesystem alias dispatch ─────────────────────────────────────
    action = str(normalized_args.pop("action", "") or "").strip().lower()
    if not action:
        record_anomaly_fn(
            session_key, "filesystem_alias_missing_action", detail=str(function_args)
        )
        return function_name, function_args

    mapped_name = FILESYSTEM_ALIAS_ACTIONS.get(action)
    if not mapped_name:
        record_anomaly_fn(
            session_key, "filesystem_alias_unsupported", detail=f"action={action}"
        )
        return function_name, function_args

    record_anomaly_fn(
        session_key, "filesystem_alias_normalized", detail=f"{action}->{mapped_name}"
    )

    if mapped_name == "list_dir":
        normalized_args = {
            "path": normalized_args.get("path", "."),
            **{
                k: v
                for k, v in normalized_args.items()
                if k
                in {
                    "limit",
                    "offset",
                    "include_hidden",
                    "sort_by",
                    "descending",
                    "folders_first",
                }
            },
        }
    elif mapped_name == "read_file":
        normalized_args = {
            "path": normalized_args.get("path", ""),
            **{
                k: v
                for k, v in normalized_args.items()
                if k in {"max_chars", "start_line", "end_line"}
            },
        }
    elif mapped_name == "write_file":
        normalized_args = {
            "path": normalized_args.get("path", ""),
            "content": normalized_args.get("content", ""),
        }
    elif mapped_name == "delete_file":
        normalized_args = {"path": normalized_args.get("path", "")}
    elif mapped_name == "search_files":
        normalized_args = {
            "query": normalized_args.get("query")
            or normalized_args.get("pattern")
            or "",
            "path": normalized_args.get("path", "."),
            "mode": normalized_args.get("mode", "content"),
            **{
                k: v
                for k, v in normalized_args.items()
                if k in {"file_glob", "case_sensitive", "max_results"}
            },
        }

    return mapped_name, normalized_args
