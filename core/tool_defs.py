"""
Declarative tool definitions — compact, data-driven tool schema registry.

Each tool is defined as a simple dict with name, description, and params.
build_tool_definitions() inflates them into the full OpenAI-compatible JSON
schema format that LiteLLM expects.

To add a new tool:
  1. Add a dict to BASE_TOOLS, BROWSER_TOOLS, or a new list.
  2. Add the handler to _tool_registry in loop.py.
  Done — no 20-line JSON blob needed.
"""

import re
from typing import Any, Dict, List


BASE_TOOLS = [
    {
        "name": "read_file",
        "description": "Read a known file path. Use this after you already know the exact file to inspect. Prefer search_files first if you do not know where the file is. Supports line ranges and bounded reads, including .docx and .pdf text extraction. Example: read_file(path='core/loop.py', start_line=1, end_line=80).",
        "params": {
            "path": {
                "type": "string",
                "description": "Relative or absolute path to the file.",
            },
            "max_chars": {
                "type": "integer",
                "description": "Maximum characters to return (default 20000, max 200000).",
            },
            "start_line": {
                "type": "integer",
                "description": "Optional 1-based starting line number.",
            },
            "end_line": {
                "type": "integer",
                "description": "Optional 1-based ending line number (inclusive).",
            },
        },
        "required": ["path"],
    },
    {
        "name": "write_file",
        "description": "Write or overwrite a file when the user clearly asked to create or edit one. Do not use this just to explore or inspect. Creates directories if needed. To start a new skill scaffold, prefer create_skill instead.",
        "params": {
            "path": {
                "type": "string",
                "description": "Relative or absolute path to the file.",
            },
            "content": {
                "type": "string",
                "description": "The full text content to write.",
            },
        },
        "required": ["path", "content"],
    },
    {
        "name": "delete_file",
        "description": "Delete a file or directory only when the user explicitly wants removal. Never use for cleanup by default. Requires confirmation.",
        "params": {
            "path": {
                "type": "string",
                "description": "Path to the file or directory to delete.",
            }
        },
        "required": ["path"],
    },
    {
        "name": "list_dir",
        "description": "List a directory when you need to inspect folder structure or browse candidates. Prefer this for broad exploration; prefer read_file for a known file and search_files for text/name lookup.",
        "params": {
            "path": {
                "type": "string",
                "description": "Path to the directory. Defaults to '.' (current directory).",
            },
            "limit": {
                "type": "integer",
                "description": "Maximum entries to return (default 200, max 1000).",
            },
            "offset": {
                "type": "integer",
                "description": "Pagination offset (default 0).",
            },
            "include_hidden": {
                "type": "boolean",
                "description": "Include dotfiles and hidden entries (default false).",
            },
            "sort_by": {
                "type": "string",
                "enum": ["name", "type", "mtime", "size", "none"],
                "description": "Sort strategy (default name).",
            },
            "descending": {
                "type": "boolean",
                "description": "Sort descending when true (default false).",
            },
            "folders_first": {
                "type": "boolean",
                "description": "Show folders before files when applicable (default true).",
            }
        },
        "required": ["path"],
    },
    {
        "name": "search_files",
        "description": "Fast repo search. Use this first when you need to locate code, config, symbols, or filenames but do not know the exact path yet. Use mode='content' for text inside files and mode='name' for filenames. After finding a path, switch to read_file or list_dir. Example: search_files(query='verify_auth', mode='content').",
        "params": {
            "query": {
                "type": "string",
                "description": "Search text to look for (required).",
            },
            "path": {
                "type": "string",
                "description": "Root path to search. Defaults to current project directory.",
            },
            "file_glob": {
                "type": "string",
                "description": "Optional filename glob filter (e.g. '*.py', '*.md').",
            },
            "mode": {
                "type": "string",
                "enum": ["content", "name"],
                "description": "Search content lines or file names.",
            },
            "case_sensitive": {
                "type": "boolean",
                "description": "Set true for case-sensitive search. Default false.",
            },
            "max_results": {
                "type": "integer",
                "description": "Maximum matches to return (default 40, max 200).",
            },
        },
        "required": ["query"],
    },
    {
        "name": "run_command",
        "description": "Execute a shell command only when native tools are insufficient or the user explicitly wants command execution. Prefer read_file, list_dir, search_files, or browser tools first when they can answer the request. Use this for git, test runs, scripts, or skill commands. Example: run_command(command='pytest tests/test_setup_prompt.py').",
        "params": {
            "command": {
                "type": "string",
                "description": "The shell command to execute.",
            }
        },
        "required": ["command"],
    },
    {
        "name": "memory_search",
        "description": "Search stored memory when the user asks what was remembered before or you need recalled facts from past logs. Do not use it for normal file/code search.",
        "params": {
            "query": {
                "type": "string",
                "description": "Keywords or phrase to search for in memory.",
            }
        },
        "required": ["query"],
    },
    {
        "name": "spawn_agent",
        "description": "Delegate a long, parallelizable, or self-contained task to a sub-agent. Prefer direct tools first for short tasks. Use this when the work can proceed independently and report back.",
        "params": {
            "task": {
                "type": "string",
                "description": "Full description of the task for the sub-agent.",
            }
        },
        "required": ["task"],
    },
    {
        "name": "cron_add",
        "description": (
            "Schedule a reminder or recurring task when the user asks to be reminded later or to automate something on a schedule. "
            "Use time_expr for a one-time delay (e.g. '1h', '30m') or cron_expr for repeating schedules (e.g. '0 9 * * 1-5')."
        ),
        "params": {
            "time_expr": {
                "type": "string",
                "description": "Relative delay: '10s', '5m', '2h', '1d'.",
            },
            "cron_expr": {
                "type": "string",
                "description": "Cron expression for repeating jobs, e.g. '0 9 * * 1-5' for weekdays at 9 AM.",
            },
            "message": {
                "type": "string",
                "description": "The reminder message or task description.",
            },
            "context": {
                "type": "object",
                "description": "Delivery context. YOU MUST pass your current channel, chat_id, and sender_id.",
                "properties": {
                    "channel": {"type": "string"},
                    "chat_id": {"type": "string"},
                    "sender_id": {"type": "string"},
                },
                "required": ["channel", "chat_id"],
            },
        },
        "required": ["message", "context"],
    },
    {
        "name": "cron_list",
        "description": "List pending scheduled jobs when the user asks what reminders or automations already exist.",
        "params": {},
        "required": [],
    },
    {
        "name": "cron_remove",
        "description": "Remove a scheduled job by ID after the user asks to cancel or delete a reminder.",
        "params": {
            "job_id": {
                "type": "string",
                "description": "The job ID to remove (from cron_list).",
            }
        },
        "required": ["job_id"],
    },
    {
        "name": "create_skill",
        "description": "Create a new LimeBot skill scaffold inside skills/. Prefer this over write_file when the user wants a new skill, because it creates the correct structure and avoids polluting the codebase. Example: create_skill(name='weather_check', description='Fetch and summarize weather').",
        "params": {
            "name": {
                "type": "string",
                "description": "The name of the skill (e.g. 'weather_check'). Use snake_case.",
            },
            "description": {
                "type": "string",
                "description": "Brief summary of what the skill does.",
            },
        },
        "required": ["name", "description"],
    },
]


BROWSER_TOOLS = [
    {
        "name": "browser_navigate",
        "description": "Open a webpage when you have a URL or need to start browser automation on a site. Usually the first browser step. Follow with browser_snapshot, browser_click, browser_type, or browser_extract. Example: browser_navigate(url='https://example.com').",
        "params": {
            "url": {
                "type": "string",
                "description": "The full URL to navigate to, including https://.",
            }
        },
        "required": ["url"],
    },
    {
        "name": "browser_click",
        "description": "Click an interactive element from the latest browser snapshot. Use only after browser_snapshot or browser_navigate returned element IDs.",
        "params": {
            "element_id": {
                "type": "string",
                "description": "Element ID from the last snapshot (e.g. 'e5').",
            }
        },
        "required": ["element_id"],
    },
    {
        "name": "browser_type",
        "description": "Type into a known browser input element. Use only after browser_snapshot or browser_navigate identified the correct element ID.",
        "params": {
            "element_id": {
                "type": "string",
                "description": "Element ID of the input field (e.g. 'e5').",
            },
            "text": {"type": "string", "description": "Text to type into the field."},
        },
        "required": ["element_id", "text"],
    },
    {
        "name": "browser_snapshot",
        "description": "Inspect the current browser page and get the interactive element tree. Use this after navigation and before clicking or typing when you need fresh element IDs.",
        "params": {},
        "required": [],
    },
    {
        "name": "browser_scroll",
        "description": "Scroll the current page to reveal more content when a snapshot or extract did not show everything you need.",
        "params": {
            "direction": {
                "type": "string",
                "enum": ["up", "down"],
                "description": "Direction to scroll.",
            },
            "amount": {
                "type": "integer",
                "description": "Pixels to scroll (default: 500).",
            },
        },
        "required": ["direction"],
    },
    {
        "name": "browser_wait",
        "description": "Pause briefly after browser actions when the page needs time to update, load results, or render new elements.",
        "params": {
            "ms": {
                "type": "integer",
                "description": "Milliseconds to wait (default: 1000, max: 30000).",
            }
        },
        "required": [],
    },
    {
        "name": "browser_press_key",
        "description": "Press a key in the browser for form submission or UI control, such as Enter, Escape, Tab, or arrows.",
        "params": {
            "key": {
                "type": "string",
                "description": "Key name (e.g. 'Enter', 'Escape', 'Tab', 'ArrowDown').",
            }
        },
        "required": ["key"],
    },
    {
        "name": "browser_go_back",
        "description": "Go back one page in browser history when navigation went to the wrong place or you need the previous page again.",
        "params": {},
        "required": [],
    },
    {
        "name": "browser_tabs",
        "description": "List open browser tabs when the site spawned multiple pages and you need to inspect or switch between them.",
        "params": {},
        "required": [],
    },
    {
        "name": "browser_switch_tab",
        "description": "Switch to a specific browser tab after browser_tabs identified the correct index.",
        "params": {
            "index": {"type": "integer", "description": "Tab index from browser_tabs."}
        },
        "required": ["index"],
    },
    {
        "name": "browser_extract",
        "description": (
            "Extract visible text from the page or a CSS selector when you need page content, article text, or table data. "
            "Prefer this over browser_snapshot for reading content. Use limit=100000 for large tables or long articles."
        ),
        "params": {
            "selector": {
                "type": "string",
                "description": "CSS selector to extract from (default: 'body').",
            },
            "limit": {
                "type": "integer",
                "description": "Max characters to return (default: 5000, max: 100000).",
            },
        },
        "required": [],
    },
    {
        "name": "browser_get_page_text",
        "description": "Return all visible page text for reading-heavy tasks. Prefer browser_extract if you can target a specific selector.",
        "params": {},
        "required": [],
    },
    {
        "name": "browser_list_media",
        "description": "List major images/media on the current page when the user wants photos, assets, or visual content from a site.",
        "params": {},
        "required": [],
    },
    {
        "name": "google_search",
        "description": "Use web search when you need to discover pages, not when you already have a URL. A common first step before browser_navigate. Example: google_search(query='LimeBot Discord bot docs').",
        "params": {"query": {"type": "string", "description": "Search query string."}},
        "required": ["query"],
    },
]


_TOOL_FAMILIES = {
    "read_file": "filesystem",
    "write_file": "filesystem",
    "delete_file": "filesystem",
    "list_dir": "filesystem",
    "search_files": "filesystem",
    "create_skill": "filesystem",
    "run_command": "command",
    "memory_search": "memory",
    "spawn_agent": "agent",
    "cron_add": "scheduler",
    "cron_list": "scheduler",
    "cron_remove": "scheduler",
    "google_search": "browser",
    "browser_navigate": "browser",
    "browser_click": "browser",
    "browser_type": "browser",
    "browser_snapshot": "browser",
    "browser_scroll": "browser",
    "browser_wait": "browser",
    "browser_press_key": "browser",
    "browser_go_back": "browser",
    "browser_tabs": "browser",
    "browser_switch_tab": "browser",
    "browser_extract": "browser",
    "browser_get_page_text": "browser",
    "browser_list_media": "browser",
}

_FAMILY_HINTS = {
    "filesystem": {
        "file",
        "files",
        "folder",
        "folders",
        "directory",
        "directories",
        "path",
        "paths",
        "repo",
        "repository",
        "code",
        "project",
        "source",
        "read",
        "write",
        "find",
        "search",
    },
    "command": {
        "command",
        "terminal",
        "shell",
        "script",
        "scripts",
        "bash",
        "powershell",
        "python",
        "pytest",
        "git",
        "npm",
        "node",
        "pip",
        "exec",
        "run",
    },
    "browser": {
        "web",
        "website",
        "browser",
        "page",
        "pages",
        "url",
        "search",
        "google",
        "click",
        "form",
        "scrape",
        "article",
        "open",
        "navigate",
    },
    "scheduler": {
        "remind",
        "reminder",
        "schedule",
        "scheduled",
        "cron",
        "tomorrow",
        "later",
        "daily",
        "weekly",
        "monthly",
        "every",
    },
    "memory": {
        "memory",
        "remember",
        "remembered",
        "recall",
        "history",
        "journal",
        "past",
    },
    "agent": {
        "delegate",
        "delegated",
        "background",
        "parallel",
        "subagent",
        "complex",
        "long",
    },
}

_MANDATORY_FAMILY_TOOLS = {
    "filesystem": {"search_files", "read_file", "list_dir"},
    "command": {"run_command"},
    "browser": {
        "google_search",
        "browser_navigate",
        "browser_snapshot",
        "browser_click",
        "browser_type",
        "browser_wait",
        "browser_extract",
    },
    "scheduler": {"cron_add", "cron_list", "cron_remove"},
    "memory": {"memory_search"},
    "agent": {"spawn_agent"},
}

_TOOL_HINTS = {
    "read_file": {"read", "open", "show", "file", "contents", "content"},
    "write_file": {"write", "edit", "save", "create", "overwrite", "file"},
    "delete_file": {"delete", "remove", "erase", "cleanup"},
    "list_dir": {"list", "dir", "directory", "folder", "files", "browse"},
    "search_files": {"search", "find", "grep", "rg", "ripgrep", "match", "locate"},
    "run_command": {"run", "command", "terminal", "shell", "script", "git", "pytest", "npm", "python"},
    "memory_search": {"memory", "remember", "recall", "history", "journal"},
    "spawn_agent": {"delegate", "background", "subagent", "parallel"},
    "cron_add": {"remind", "schedule", "later", "daily", "weekly", "every"},
    "cron_list": {"scheduled", "reminders", "jobs", "cron"},
    "cron_remove": {"cancel", "remove", "delete", "scheduled", "reminder"},
    "create_skill": {"skill", "scaffold", "template"},
    "google_search": {"google", "search", "web", "website", "results"},
    "browser_navigate": {"url", "open", "visit", "navigate", "website", "web"},
    "browser_snapshot": {"snapshot", "page", "elements", "buttons", "form"},
    "browser_click": {"click", "press", "tap", "select"},
    "browser_type": {"type", "enter", "fill", "input", "search"},
    "browser_wait": {"wait", "loading", "load"},
    "browser_extract": {"extract", "article", "text", "table", "content", "scrape"},
    "browser_get_page_text": {"read", "text", "page", "article"},
    "browser_list_media": {"image", "images", "photo", "photos", "media"},
}


def _tokenize(text: str) -> set[str]:
    return {
        token
        for token in re.findall(r"[a-z0-9_./:-]+", (text or "").lower())
        if len(token) >= 2
    }


def shortlist_tool_definitions(
    tool_defs: List[Dict[str, Any]], user_text: str, max_tools: int = 12
) -> List[Dict[str, Any]]:
    """Return a coherent subset of tools for the current user turn."""
    text = (user_text or "").strip()
    if not text or len(tool_defs) <= max_tools:
        return tool_defs

    lowered = text.lower()
    tokens = _tokenize(text)
    selected_families = set()

    for family, hints in _FAMILY_HINTS.items():
        if tokens & hints:
            selected_families.add(family)

    if any(marker in lowered for marker in ("http://", "https://", "www.")):
        selected_families.add("browser")
    if any(
        marker in lowered
        for marker in (".py", ".ts", ".js", ".md", ".json", "./", "../", ".\\", "..\\")
    ):
        selected_families.add("filesystem")
    if any(marker in lowered for marker in ("git ", "pytest", "npm ", "python ", "bash", "powershell", "cmd ")):
        selected_families.add("command")

    mandatory = set()
    for family in selected_families:
        mandatory.update(_MANDATORY_FAMILY_TOOLS.get(family, set()))

    scored: list[tuple[int, str, Dict[str, Any]]] = []
    for tool in tool_defs:
        function = tool.get("function", {})
        name = function.get("name", "")
        family = _TOOL_FAMILIES.get(name, "other")
        hints = set(_TOOL_HINTS.get(name, set()))
        hints.update(_tokenize(name.replace("_", " ")))
        score = len(tokens & hints)

        if family in selected_families:
            score += 8
        if name in mandatory:
            score += 20
        if name in lowered:
            score += 50

        if name == "run_command" and "command" not in selected_families and selected_families:
            score -= 10

        scored.append((score, name, tool))

    scored.sort(key=lambda item: (-item[0], item[1]))

    if scored and scored[0][0] <= 0:
        return tool_defs

    selected_names = []
    for _, name, _ in scored:
        if name in mandatory and name not in selected_names:
            selected_names.append(name)

    for score, name, _ in scored:
        if score <= 0:
            continue
        if name not in selected_names:
            selected_names.append(name)
        if len(selected_names) >= max_tools:
            break

    selected_set = set(selected_names)
    shortlisted = [
        tool for tool in tool_defs if tool.get("function", {}).get("name") in selected_set
    ]
    return shortlisted or tool_defs


def _expand_param(name: str, schema) -> dict:
    """Expand shorthand param ('string') into a full JSON Schema property dict."""
    if isinstance(schema, str):
        return {"type": schema, "description": f"The {name}."}
    return schema


def _inflate_tool(tool_def: dict) -> dict:
    """Inflate a compact tool definition into the OpenAI function-calling format."""
    properties = {
        name: _expand_param(name, schema)
        for name, schema in tool_def.get("params", {}).items()
    }
    return {
        "type": "function",
        "function": {
            "name": tool_def["name"],
            "description": tool_def["description"],
            "parameters": {
                "type": "object",
                "properties": properties,
                "required": tool_def.get("required", []),
            },
        },
    }


def build_tool_definitions(enabled_skills: List[str]) -> List[Dict[str, Any]]:
    """
    Build the full list of tool definitions for the LLM.

    Args:
        enabled_skills: List of enabled skill names from config.

    Returns:
        List of OpenAI-compatible tool definition dicts.
    """
    tools = [_inflate_tool(t) for t in BASE_TOOLS]

    if "browser" in enabled_skills:
        tools.extend(_inflate_tool(t) for t in BROWSER_TOOLS)

    return tools
