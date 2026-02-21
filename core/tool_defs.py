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

from typing import Any, Dict, List


BASE_TOOLS = [
    {
        "name": "read_file",
        "description": "Read the contents of a file. Use this to examine code or text files.",
        "params": {
            "path": {
                "type": "string",
                "description": "Relative or absolute path to the file.",
            }
        },
        "required": ["path"],
    },
    {
        "name": "write_file",
        "description": "Write text content to a file. Overwrites existing content. Creates directories if needed.",
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
        "description": "Delete a file or directory. REQUIRES CONFIRMATION.",
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
        "description": "List files and subdirectories in a folder.",
        "params": {
            "path": {
                "type": "string",
                "description": "Path to the directory. Defaults to '.' (current directory).",
            }
        },
        "required": ["path"],
    },
    {
        "name": "run_command",
        "description": "Execute a terminal command (e.g., python scripts, git). Use this to run skills.",
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
        "description": "Search through long-term memory and daily logs for specific keywords or information.",
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
        "description": "Spawn a background sub-agent to handle a long or complex task. It will report back when finished.",
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
            "Schedule a reminder or recurring task. Use time_expr for a one-time delay "
            "(e.g. '1h', '30m') or cron_expr for a repeating schedule (e.g. '0 9 * * 1-5')."
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
        "description": "List all pending scheduled tasks.",
        "params": {},
        "required": [],
    },
    {
        "name": "cron_remove",
        "description": "Remove a scheduled task by its ID.",
        "params": {
            "job_id": {
                "type": "string",
                "description": "The job ID to remove (from cron_list).",
            }
        },
        "required": ["job_id"],
    },
]


BROWSER_TOOLS = [
    {
        "name": "browser_navigate",
        "description": "Navigate to a URL and return the page title, URL, and interactive element tree.",
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
        "description": "Click an element by its ID from the accessibility tree (e.g. 'e5', 'e13').",
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
        "description": "Type text into an input field identified by its accessibility tree ID.",
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
        "description": "Get the current page state: title, URL, and all interactive elements with IDs.",
        "params": {},
        "required": [],
    },
    {
        "name": "browser_scroll",
        "description": "Scroll the page up or down to reveal more content.",
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
        "description": "Wait for a number of milliseconds, useful after clicking or typing to let the page update.",
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
        "description": "Press a keyboard key, e.g. 'Enter' to submit a form, 'Escape' to close a modal, 'Tab' to move focus.",
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
        "description": "Navigate to the previous page in browser history.",
        "params": {},
        "required": [],
    },
    {
        "name": "browser_tabs",
        "description": "List all open browser tabs with their index, title, and URL.",
        "params": {},
        "required": [],
    },
    {
        "name": "browser_switch_tab",
        "description": "Switch focus to a specific browser tab by its index (from browser_tabs).",
        "params": {
            "index": {"type": "integer", "description": "Tab index from browser_tabs."}
        },
        "required": ["index"],
    },
    {
        "name": "browser_extract",
        "description": (
            "Extract visible text from the page or a CSS selector. "
            "Use limit=100000 for large tables or long articles."
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
        "description": "Return all visible text on the current page (up to 20,000 chars). Useful for reading articles.",
        "params": {},
        "required": [],
    },
    {
        "name": "browser_list_media",
        "description": "List all significant images on the current page with their URLs and descriptions.",
        "params": {},
        "required": [],
    },
    {
        "name": "google_search",
        "description": "Search Google and return the top results with titles, URLs, and snippets.",
        "params": {"query": {"type": "string", "description": "Search query string."}},
        "required": ["query"],
    },
]


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
