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

import copy
import os
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
            "include_hash": {
                "type": "boolean",
                "description": "Include the current raw-file SHA-256 so a later edit_file call can reject stale edits.",
            },
        },
        "required": ["path"],
    },
    {
        "name": "inspect_skill",
        "description": (
            "Inspect a registered LimeBot skill before repairing it. Returns a bounded file "
            "inventory, selected UTF-8 content, source provenance, and whether the skill is "
            "editable. Paths are relative to the skill and credentials/absolute runtime paths "
            "are redacted. Use this before edit_skill or retrying a failed skill command."
        ),
        "params": {
            "skill_name": {
                "type": "string",
                "description": "The exact registered skill name, such as 'jira'.",
            },
            "path": {
                "type": "string",
                "description": "Optional relative file inside the skill; defaults to SKILL.md.",
            },
            "start_line": {
                "type": "integer",
                "description": "Optional 1-based first line of the selected file.",
            },
            "end_line": {
                "type": "integer",
                "description": "Optional inclusive last line of the selected file.",
            },
        },
        "required": ["skill_name"],
    },
    {
        "name": "edit_file",
        "description": (
            "Apply one or more exact, reviewable text edits to an existing UTF-8 source file. "
            "Use read_file(..., include_hash=true) first, then pass that SHA-256 as "
            "expected_sha256. Each edit must contain old_text and new_text; old_text must "
            "match exactly. The operation is preflighted and atomic: stale hashes, missing "
            "anchors, overlapping edits, and no-op edits are rejected without changing the file. "
            "Use write_file for intentional full replacement or creating a new file."
        ),
        "params": {
            "path": {
                "type": "string",
                "description": "Relative or absolute path to an existing UTF-8 text file.",
            },
            "edits": {
                "type": "array",
                "description": "Exact non-overlapping edits, applied against one file snapshot.",
                "items": {
                    "type": "object",
                    "properties": {
                        "old_text": {
                            "type": "string",
                            "description": "Exact text to replace, including whitespace when relevant.",
                        },
                        "new_text": {
                            "type": "string",
                            "description": "Replacement text.",
                        },
                        "occurrence": {
                            "type": "integer",
                            "description": "1-based match occurrence when old_text appears more than once; defaults to 1.",
                        },
                        "replace_all": {
                            "type": "boolean",
                            "description": "Replace every exact match instead of one occurrence; defaults to false.",
                        },
                    },
                    "required": ["old_text", "new_text"],
                },
            },
            "expected_sha256": {
                "type": "string",
                "description": "SHA-256 from read_file(include_hash=true). Required for stale-edit protection when editing an existing file.",
            },
        },
        "required": ["path", "edits", "expected_sha256"],
    },
    {
        "name": "edit_skill",
        "description": (
            "Repair one local or legacy-local skill in one transaction; bundled and Git-managed "
            "skills are read-only. Call inspect_skill first. replace needs exact old_text "
            "(optionally occurrence or replace_all), create needs content, delete removes a file. "
            "Edits are validated, applied atomically, reloaded, and rolled back on failure."
        ),
        "params": {
            "skill_name": {
                "type": "string",
                "description": "The exact registered local skill name.",
            },
            "changes": {
                "type": "array",
                "description": "One or more transactional file operations inside the skill.",
                "items": {
                    "type": "object",
                    "properties": {
                        "operation": {
                            "type": "string",
                            "enum": ["replace", "create", "delete"],
                            "description": "Operation to apply.",
                        },
                        "path": {
                            "type": "string",
                            "description": "Relative UTF-8 file path inside the skill.",
                        },
                        "old_text": {
                            "type": "string",
                            "description": "Exact text required for replace.",
                        },
                        "new_text": {
                            "type": "string",
                            "description": "Exact replacement text for replace.",
                        },
                        "content": {
                            "type": "string",
                            "description": "Complete UTF-8 text for create.",
                        },
                        "occurrence": {
                            "type": "integer",
                            "description": "1-based occurrence for replace; defaults to 1.",
                        },
                        "replace_all": {
                            "type": "boolean",
                            "description": "Replace every exact match instead of one.",
                        },
                        "expected_sha256": {
                            "type": "string",
                            "description": "Optional hash from inspect/read output for stale protection.",
                        },
                    },
                    "required": ["operation", "path"],
                },
            },
        },
        "required": ["skill_name", "changes"],
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
        "name": "create_spreadsheet",
        "description": (
            "Create a real, styled Microsoft Excel .xlsx workbook without writing a script. "
            "Use this whenever the user asks for Excel or XLSX. Put column headings in the "
            "first row of each sheet; strings beginning with '=' become Excel formulas. "
            "After success, call send_media with the same path to deliver the workbook."
        ),
        "params": {
            "path": {
                "type": "string",
                "description": "Destination path ending in .xlsx.",
            },
            "title": {
                "type": "string",
                "description": "Optional workbook title metadata.",
            },
            "sheets": {
                "type": "array",
                "description": "One to twenty worksheets, each with a name and rectangular row arrays.",
                "items": {
                    "type": "object",
                    "properties": {
                        "name": {"type": "string"},
                        "rows": {
                            "type": "array",
                            "items": {
                                "type": "array",
                                "items": {
                                    "type": ["string", "number", "boolean", "null"]
                                },
                            },
                        },
                    },
                    "required": ["name", "rows"],
                },
            },
        },
        "required": ["path", "sheets"],
    },
    {
        "name": "calculate",
        "description": (
            "Evaluate arithmetic exactly and safely. Use this for prices, totals, percentages, "
            "unit conversions, and comparisons instead of mental math or run_command. "
            "Supports +, -, *, /, //, %, parentheses, and bounded powers."
        ),
        "params": {
            "expression": {
                "type": "string",
                "description": "Arithmetic expression, for example 0.0104*730*12.",
            }
        },
        "required": ["expression"],
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
        "name": "verify_files",
        "description": (
            "Run safe, read-only checks on one or more changed files. This checks UTF-8 "
            "decoding, conflict markers, Python/JSON/TOML syntax when applicable, and "
            "git diff whitespace errors. Use it after editing and before claiming a coding "
            "task is complete; it does not run arbitrary commands or tests."
        ),
        "params": {
            "paths": {
                "type": "array",
                "description": "One or more existing files to verify.",
                "items": {"type": "string"},
            },
            "include_diagnostics": {
                "type": "boolean",
                "description": "Also run an installed optional linter/type checker when true.",
            },
            "provider": {
                "type": "string",
                "enum": ["auto", "ruff", "pyright", "eslint", "tsc", "none"],
                "description": "Optional diagnostics provider used when include_diagnostics is true.",
            },
        },
        "required": ["paths"],
    },
    {
        "name": "diagnose_files",
        "description": (
            "Run optional project diagnostics for changed source files using an installed "
            "Ruff, Pyright, ESLint, or TypeScript compiler. This uses direct subprocess "
            "arguments, never requires an LSP server, and returns skipped when no matching "
            "provider is installed. Use provider='auto' unless the user names a tool."
        ),
        "params": {
            "paths": {
                "type": "array",
                "description": "One or more existing source files to diagnose.",
                "items": {"type": "string"},
            },
            "provider": {
                "type": "string",
                "enum": ["auto", "ruff", "pyright", "eslint", "tsc", "none"],
                "description": "Optional diagnostics provider; defaults to auto.",
            },
            "timeout": {
                "type": "number",
                "description": "Maximum provider runtime in seconds (default 45, max 120).",
            },
        },
        "required": ["paths"],
    },
    {
        "name": "run_command",
        "description": (
            "Execute one shell command only when native tools are insufficient or the user explicitly "
            "wants command execution. Commands run from the project root on "
            f"{'Windows' if os.name == 'nt' else 'a POSIX system'}. Live chat blocks heredocs, "
            "newlines, &&, ||, semicolon chaining, redirects, backticks, and $(). Bare `|` is "
            "allowed except when piped into an interpreter. For unittest then py_compile, use "
            "run_steps instead of &&. Isolated spawn_agent copies may use && / || / | / redirects. "
            "Prefer read_file, list_dir, search_files, calculate, create_spreadsheet, or browser "
            "tools first."
        ),
        "params": {
            "command": {
                "type": "string",
                "description": "The shell command to execute.",
            }
        },
        "required": ["command"],
    },
    {
        "name": "run_steps",
        "description": (
            "Run two or more shell commands in order in the active workspace. This is the safe "
            "replacement for && chains such as unittest then py_compile. Each step is validated "
            "and executed separately; later steps do not run after a rejection or nonzero exit."
        ),
        "params": {
            "commands": {
                "type": "array",
                "description": "Shell commands to run in order.",
                "items": {"type": "string"},
            }
        },
        "required": ["commands"],
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
        "name": "memory_save",
        "description": (
            "Persist a fact or event the user explicitly asks LimeBot to remember. "
            "This writes the Markdown source of truth and works without embeddings. "
            "Use scope='journal' for an append-only dated event (default), or "
            "scope='long_term' for a durable preference, identity fact, project, or relationship."
        ),
        "params": {
            "content": {
                "type": "string",
                "description": "The concise fact or event to remember.",
            },
            "scope": {
                "type": "string",
                "enum": ["journal", "long_term"],
                "description": "Where to persist it. Defaults to 'journal'.",
            },
        },
        "required": ["content"],
    },
    {
        "name": "spawn_agent",
        "description": (
            "Delegate a long, parallelizable, or specialized task to a sub-agent. "
            "Prefer direct tools for tiny tasks. Do not use this to send a photo into "
            "the current chat — that is web_search(kind=\"images\"). "
            "Use spawn_agent when the work clearly matches a specialist such as codebase "
            "exploration, review, or verification."
        ),
        "params": {
            "task": {
                "type": "string",
                "description": "Full description of the task for the sub-agent.",
            },
            "background": {
                "type": "boolean",
                "description": "If true, start the sub-agent in the background and let it report back later instead of waiting for its result now.",
            },
            "isolation": {
                "type": "string",
                "enum": ["auto", "copy", "none"],
                "description": (
                    "Workspace mode. 'auto' isolates coding, repository, review and verification "
                    "work in a temporary copy; 'copy' always isolates edits and keeps a changeset "
                    "until apply_workspace_changeset merges it; 'none' uses the live workspace "
                    "(read-only or explicitly shared tasks only)."
                ),
            },
        },
        "required": ["task"],
    },
    {
        "name": "apply_workspace_changeset",
        "description": (
            "Apply a retained spawn_agent(isolation='copy') changeset to the live tree using "
            "hash-guarded edit_file / write_file, then delete leftover clones "
            "(temp/bakeoff-*-isolated and /tmp/limebot-subagent-*). Live files stay unchanged "
            "until this tool runs. Pass the workspace_id from the sub-agent report."
        ),
        "params": {
            "workspace_id": {
                "type": "string",
                "description": "workspace_id from the isolated sub-agent report.",
            },
            "changeset": {
                "type": "object",
                "description": "Optional structured capture with changed_files if the clone is already gone.",
            },
        },
        "required": [],
    },
    {
        "name": "get_task_output",
        "description": "Get status and output for a background sub-agent by task ID. Pass timeout_ms > 0 to wait; omit it or use 0 for a non-blocking poll.",
        "params": {
            "task_ids": {
                "type": "array",
                "items": {"type": "string"},
                "description": "One or more stable background task IDs.",
            },
            "timeout_ms": {
                "type": "integer",
                "description": "Optional maximum wait in milliseconds; 0 means poll.",
            },
        },
        "required": ["task_ids"],
    },
    {
        "name": "wait_tasks",
        "description": "Wait for one or more background sub-agent tasks to finish. This is a compatibility alias for get_task_output with a positive timeout.",
        "params": {
            "task_ids": {
                "type": "array",
                "items": {"type": "string"},
                "description": "Stable background task IDs.",
            },
            "timeout_ms": {
                "type": "integer",
                "description": "Maximum wait in milliseconds.",
            },
        },
        "required": ["task_ids"],
    },
    {
        "name": "kill_task",
        "description": "Cancel a running background sub-agent by its stable task ID. Completed tasks are reported as already exited.",
        "params": {
            "task_id": {
                "type": "string",
                "description": "Stable background task ID returned by spawn_agent.",
            }
        },
        "required": ["task_id"],
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
            "tz": {
                "type": "string",
                "description": "Optional IANA timezone for cron_expr schedules, e.g. 'America/El_Salvador'.",
            },
            "message": {
                "type": "string",
                "description": "The reminder message or task description.",
            },
            "name": {
                "type": "string",
                "description": "Optional short human-readable job name, e.g. 'Daily finance brief'.",
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
    {
        "name": "send_media",
        "description": (
            "Deliver a local file into the current web, Discord, or WhatsApp chat. "
            "Accepts a local file path OR a remote http(s) URL (downloaded first, SSRF-guarded). "
            "Use this when you have a specific file or URL to deliver. For open-ended photo searches in a web chat, "
            "web_search(kind=\"images\") can find a photo and the host attaches its result. "
            "Use it once per artifact; never resend the same path to narrate progress."
        ),
        "params": {
            "path": {
                "type": "string",
                "description": "Local file path OR a remote http(s) URL to fetch and send.",
            },
            "caption": {
                "type": "string",
                "description": "Optional caption to send alongside the file.",
            },
        },
        "required": ["path"],
    },
    {
        "name": "send_voice",
        "description": "Speak text aloud and send it as a voice message in the current chat. Delivers an audio file on Discord/WhatsApp (no text needed) or an inline playable clip on web. Use this when the user asks for a voice message/voice note. Requires an ElevenLabs API key. Example: send_voice(text='Hey! Here is your reminder.').",
        "params": {
            "text": {
                "type": "string",
                "description": "The text to speak aloud.",
            },
            "channel": {
                "type": "string",
                "description": "Optional target channel (web, discord, whatsapp). Defaults to the current chat's channel.",
            },
        },
        "required": ["text"],
    },
    {
        "name": "generate_image",
        "description": (
            "Generate or edit a NEW image. Use only when the user explicitly asks to create, draw, "
            "render, generate, or transform a picture. Do NOT use this to download, find, or send an "
            "existing photo — that is web_search(kind=\"images\"). Images merely embedded in a document "
            "are not a generation request. Never call this with a prompt saying generation is "
            "unnecessary. Images attached "
            "to the current message are used automatically as references, including follow-ups such "
            "as 'make it' or 'generate it'. Saves files locally and sends the image to the active chat "
            "when supported."
        ),
        "params": {
            "prompt": {
                "type": "string",
                "description": "Detailed image prompt describing the desired visual result.",
            },
            "model": {
                "type": "string",
                "description": (
                    "Optional image backend, e.g. openai/gpt-image-2 or gemini/gemini-3-pro-image. "
                    "Omit to use the configured default."
                ),
            },
            "size": {
                "type": "string",
                "description": "Optional output size or aspect ratio, e.g. 1024x1024, 1024x1536, 1536x1024, 16:9.",
            },
            "quality": {
                "type": "string",
                "description": "Optional quality hint such as auto, low, medium, or high.",
            },
            "count": {
                "type": "integer",
                "description": "Number of images to generate. Defaults to 1; currently capped at 4.",
            },
            "reference_images": {
                "type": "array",
                "items": {"type": "string"},
                "description": (
                    "Optional allowed local image paths as references. Usually omit: "
                    "chat uploads are attached automatically."
                ),
            },
            "use_attached_images": {
                "type": "boolean",
                "description": (
                    "Use images from the current or previous chat turn as references. True for "
                    "'this image', 'same image', 'make it'; false only for an explicit fresh text-only image."
                ),
            },
        },
        "required": ["prompt"],
    },
    {
        "name": "send_discord_message",
        "description": (
            "Send a plain Discord message to a server channel or directly to a user DM. "
            "Use channel_id for public/server channels, user_id for DMs, or omit both to reply in the current Discord chat."
        ),
        "params": {
            "message": {
                "type": "string",
                "description": "Message text to send.",
            },
            "channel_id": {
                "type": "string",
                "description": "Optional numeric Discord channel ID for a public/server channel target.",
            },
            "user_id": {
                "type": "string",
                "description": "Optional numeric Discord user ID for a direct message target.",
            },
        },
        "required": ["message"],
    },
    {
        "name": "send_discord_embed",
        "description": "Send a native Discord embed. Use this for structured Discord output instead of faking an embed with plain text. Defaults to the current Discord chat when used from Discord; otherwise pass channel_id or user_id explicitly.",
        "params": {
            "title": {
                "type": "string",
                "description": "Optional embed title.",
            },
            "description": {
                "type": "string",
                "description": "Optional embed description.",
            },
            "color": {
                "type": "string",
                "description": "Optional hex color like #5865F2.",
            },
            "footer": {
                "type": "string",
                "description": "Optional footer text.",
            },
            "image": {
                "type": "string",
                "description": "Optional image URL.",
            },
            "thumbnail": {
                "type": "string",
                "description": "Optional thumbnail URL.",
            },
            "fields": {
                "type": "array",
                "description": "Optional embed fields.",
                "items": {
                    "type": "object",
                    "properties": {
                        "name": {"type": "string"},
                        "value": {"type": "string"},
                        "inline": {"type": "boolean"},
                    },
                    "required": ["name", "value"],
                },
            },
            "channel_id": {
                "type": "string",
                "description": "Optional numeric Discord channel ID. Required outside Discord chats.",
            },
            "user_id": {
                "type": "string",
                "description": "Optional numeric Discord user ID for a direct message target.",
            },
        },
        "required": [],
    },
    {
        "name": "list_discord_channels",
        "description": "List the Discord guilds and text channels LimeBot can currently access. Use this to discover channel IDs before sending to a specific Discord channel.",
        "params": {},
        "required": [],
    },
    {
        "name": "voice_list_members",
        "description": "During a Discord voice conversation, list who is in the voice channel LimeBot is in, plus the server's other voice channels and who is in them. Use it when asked who is in the call or which channels exist.",
        "params": {},
        "required": [],
    },
    {
        "name": "voice_move_members",
        "description": "During a Discord voice conversation, move people from the current voice channel to another voice channel in the same server, for example when asked to move everyone to another channel. LimeBot follows if everyone moves. Only the person who started the voice session or a moderator may use it.",
        "params": {
            "channel": {"type": "string", "description": "Target voice channel name or numeric id."},
            "members": {
                "type": "array",
                "items": {"type": "string"},
                "description": "Names or ids of the people to move. Omit or pass [\"everyone\"] to move everyone in the channel.",
            },
        },
        "required": ["channel"],
    },
    {
        "name": "voice_set_listening",
        "description": "During a Discord voice conversation, stop or resume listening. While listening is off, nothing is captured or sent for transcription. Turning it back on is only possible with the /voice_unmute command, so tell the user that when you stop listening. Only the person who started the voice session or a moderator may use it.",
        "params": {
            "enabled": {"type": "boolean", "description": "false to stop listening, true to resume."},
        },
        "required": ["enabled"],
    },
    {
        "name": "analyze_video",
        "description": "Analyze one allowed local video or public HTTP(S) video. Returns a bounded transcript and up to three timestamped contact sheets. Use transcript for audio-only questions, efficient for quick visual scans, and balanced for summaries, UI recordings, ads, and visual questions.",
        "params": {
            "source": {"type": "string", "description": "Allowed local video path or public HTTP(S) video URL."},
            "question": {"type": "string", "description": "What to inspect or answer about the video."},
            "detail": {"type": "string", "enum": ["transcript", "efficient", "balanced"], "description": "Analysis detail; defaults to balanced."},
            "start": {"type": "string", "description": "Optional range start as SS, MM:SS, or HH:MM:SS."},
            "end": {"type": "string", "description": "Optional range end as SS, MM:SS, or HH:MM:SS."},
            "max_frames": {"type": "integer", "minimum": 1, "maximum": 100, "description": "Candidate-frame cap from 1 to 100."},
            "resolution": {"type": "integer", "enum": [512, 1024], "description": "Frame tile resolution; use 1024 only for small on-screen text."},
        },
        "required": ["source"],
    },
]


BROWSER_TOOLS = [
    {
        "name": "browser_navigate",
        "description": (
            "Open a specific webpage. You can open pages. Use this whenever the user "
            "names a URL or asks to open, visit, or go to a page. Do not use this to "
            "search Google or find a photo — call web_search for lookup only. Follow with "
            "browser_act(action='snapshot') then click/type/download, or browser_extract."
        ),
        "params": {
            "url": {
                "type": "string",
                "description": "The full URL to navigate to, including https://.",
            }
        },
        "required": ["url"],
    },
    {
        "name": "browser_act",
        "description": (
            "Act on the current page after browser_navigate. action='snapshot' returns "
            "interactive element IDs. Then click, type, scroll, wait, press a key, go back, "
            "list/switch tabs, or download a file. Do not use this to search the web."
        ),
        "params": {
            "action": {
                "type": "string",
                "enum": [
                    "snapshot",
                    "click",
                    "type",
                    "scroll",
                    "wait",
                    "press",
                    "back",
                    "tabs",
                    "switch_tab",
                    "download",
                ],
                "description": "What to do on the current page.",
            },
            "element_id": {
                "type": "string",
                "description": "Element ID from snapshot (click, type, download).",
            },
            "text": {"type": "string", "description": "Text to type when action='type'."},
            "key": {
                "type": "string",
                "description": "Key name when action='press', e.g. Enter or Escape.",
            },
            "direction": {
                "type": "string",
                "enum": ["up", "down"],
                "description": "Scroll direction when action='scroll'.",
            },
            "amount": {
                "type": "integer",
                "description": "Pixels to scroll (default 500).",
            },
            "ms": {
                "type": "integer",
                "description": "Milliseconds to wait when action='wait'.",
            },
            "index": {
                "type": "integer",
                "description": "Tab index when action='switch_tab'.",
            },
            "url": {
                "type": "string",
                "description": "Direct file URL when action='download' without an element.",
            },
            "filename": {
                "type": "string",
                "description": "Optional filename when action='download'.",
            },
            "dest": {
                "type": "string",
                "description": "Optional allowlisted destination when action='download'.",
            },
            "timeout_ms": {
                "type": "integer",
                "description": "Download wait in milliseconds (default 30000, max 1800000).",
            },
        },
        "required": ["action"],
    },
    {
        "name": "browser_extract",
        "description": (
            "Read visible content from the current page. mode='text' returns page or "
            "selector text with HTML tables as compact rows first (so Active Python "
            "Releases and similar tables are not truncated). mode='media' lists images "
            "on the page. Do not use this to search."
        ),
        "params": {
            "mode": {
                "type": "string",
                "enum": ["text", "media"],
                "description": "text (default) or media.",
            },
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
]


# Host-owned search. One model tool; Playwright is an internal fetcher.
SEARCH_TOOLS = [
    {
        "name": "web_search",
        "description": (
            "Live search for lookup queries (facts, headlines, existing photos). Use kind='images' "
            "to find existing photos, with attach_to_chat=true when the image should appear in this "
            "chat; kind='news' for recent news. If the user named a page URL or asked to "
            "open/visit/go to a page, call browser_navigate instead. Do not open a search engine "
            "with browser tools. "
            "Example: web_search(query='Rosé BLACKPINK', kind='images', attach_to_chat=true)."
        ),
        "params": {
            "query": {"type": "string", "description": "Search query string."},
            "count": {
                "type": "integer",
                "description": "Number of results to return (default 8, max 20).",
            },
            "kind": {
                "type": "string",
                "enum": ["web", "news", "images"],
                "description": "'web' (default), 'news', or 'images'.",
            },
            "attach_to_chat": {
                "type": "boolean",
                "description": (
                    "For image results, set true when the user expects an image in "
                    "this chat; set false when they only need search results. "
                    "If omitted for an image search, the host attaches the first result."
                ),
            },
        },
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


def _build_spawn_agent_definition(
    available_agents: Dict[str, str] | None = None,
) -> Dict[str, Any]:
    base = next(
        copy.deepcopy(tool_def)
        for tool_def in BASE_TOOLS
        if tool_def["name"] == "spawn_agent"
    )
    description = (
        "Optional named subagent profile to use. "
        "Choose one when the task clearly matches that specialist's description. "
        "If omitted, LimeBot uses the generic built-in worker."
    )
    if available_agents:
        summary = "; ".join(
            f"{name}: {text}"
            for name, text in sorted(available_agents.items())
        )
        description += f" Available subagents: {summary}"
        base["params"]["agent"] = {
            "type": "string",
            "description": description,
            "enum": sorted(available_agents),
        }
    else:
        base["params"]["agent"] = {
            "type": "string",
            "description": description,
        }
    base["params"]["background"] = {
        "type": "boolean",
        "description": (
            "Optional background override. If true, start the subagent and return "
            "immediately. If omitted, the subagent profile decides."
        ),
    }
    return base


def build_tool_definitions(
    enabled_skills: List[str],
    available_agents: Dict[str, str] | None = None,
    search_available: bool = False,
) -> List[Dict[str, Any]]:
    """
    Build the full list of tool definitions for the LLM.

    Args:
        enabled_skills: List of enabled skill names from config.
        available_agents: Named subagent profiles for spawn_agent.
        search_available: Unused; search and browser tools are always registered.
            Missing Playwright fails at execution with the browser install hint.

    Returns:
        List of OpenAI-compatible tool definition dicts.
    """
    tools: List[Dict[str, Any]] = []
    for tool_def in BASE_TOOLS:
        if tool_def["name"] == "spawn_agent":
            tools.append(
                _inflate_tool(
                    _build_spawn_agent_definition(available_agents=available_agents)
                )
            )
        else:
            tools.append(_inflate_tool(tool_def))

    _ = search_available
    _ = enabled_skills
    # Search and browser tools stay registered even when the browser skill is
    # off. Missing Playwright fails at execution with BROWSER_INSTALL_HINT.
    tools.extend(_inflate_tool(t) for t in SEARCH_TOOLS)
    tools.extend(_inflate_tool(t) for t in BROWSER_TOOLS)

    return tools
