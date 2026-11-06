"""Policy checks for one-shot shell commands."""

from __future__ import annotations

import os
import re
from pathlib import Path
from typing import Optional


def _has_unquoted_semicolon(command: str) -> bool:
    """Return True when a semicolon can act as a shell command separator."""
    in_single = False
    in_double = False
    escaped = False

    for char in command or "":
        if escaped:
            escaped = False
            continue
        if char == "\\":
            escaped = True
            continue
        if char == "'" and not in_double:
            in_single = not in_single
            continue
        if char == '"' and not in_single:
            in_double = not in_double
            continue
        if char == ";" and not in_single and not in_double:
            return True
    return False


def _long_running_command_hint(command: str, project_root: Path) -> Optional[str]:
    """Reject the project service entrypoint when used with the one-shot tool."""
    tokens = [
        (match.group(1) or match.group(2) or match.group(3) or "").strip()
        for match in re.finditer(
            r'''"([^"]+)"|'([^']+)'|([^\s]+)''', str(command or "")
        )
    ]
    if not tokens:
        return None

    root_main = (project_root / "main.py").resolve()
    for index, token in enumerate(tokens):
        if not token.lower().endswith("main.py"):
            continue
        preceding = tokens[:index]
        if not any(
            Path(item).name.lower() in {"python", "python3", "py", "python.exe"}
            or Path(item).name.lower().startswith("python")
            for item in preceding[-3:]
        ):
            continue
        try:
            candidate = Path(token)
            if not candidate.is_absolute():
                candidate = project_root / candidate
            if candidate.resolve() != root_main:
                continue
        except (OSError, ValueError):
            continue
        return (
            "Error: This command starts LimeBot's long-running backend, so "
            "run_command will never finish. Use `limebot start`/`npm start` "
            "to launch the service. For one-shot skill scripts, invoke the "
            "skill entrypoint instead of `main.py`."
        )
    return None


def validate_shell_command(
    command: str,
    *,
    allow_unsafe_commands: bool,
    workspace_root: Optional[Path],
    source_root: Optional[Path],
    project_root: Path,
    cwd: Path,
) -> Optional[str]:
    """Return the existing LimeBot command-policy error, if any."""
    command = str(command or "").strip()
    if not command:
        return "Error: Command is required."

    isolated = workspace_root is not None and source_root is not None
    # Isolated clones may chain/redirect because writes stay in the copy.
    # Live chat still blocks && || redirects. Bare `|` is allowed on both
    # paths except when piped into an interpreter.
    forbidden_regex = r"(\$\(|\`|\n)" if isolated else r"(\$\(|\`|&&|\|\||>|<|\n)"
    pseudo_call_match = re.match(
        r"^\s*([A-Za-z_][\w\.]*)\s*\((.*)\)\s*$", command, re.DOTALL
    )

    if not allow_unsafe_commands and not isolated and re.search(
        r"^\s*cd\s+/d\s+.+&&", command, re.IGNORECASE
    ):
        return (
            "Error: Chained shell commands are blocked. LimeBot already runs "
            "commands from the project directory; run only the intended command "
            "(for example, python skills/docx-creator/scripts/create_docx.py)."
        )

    long_running_hint = _long_running_command_hint(command, project_root)
    if long_running_hint:
        return long_running_hint

    if isolated:
        normalized_command = os.path.normcase(command).replace("/", "\\")
        normalized_source_root = os.path.normcase(str(source_root)).replace(
            "/", "\\"
        ).rstrip("\\")
        source_path_spellings = {normalized_source_root}
        try:
            relative_source = source_root.relative_to(cwd)
            source_path_spellings.add(
                os.path.normcase(str(relative_source)).replace("/", "\\")
            )
        except ValueError:
            pass
        if any(
            spelling and spelling in normalized_command
            for spelling in source_path_spellings
        ):
            return (
                "Error: Isolated sub-agent commands cannot address the live project "
                "by absolute path. Use paths relative to the temporary workspace."
            )
        if re.search(r"(^|[\s\"'])\.\.(?:[\\/]|$)", command):
            return (
                "Error: Isolated sub-agent commands cannot escape the temporary "
                "workspace with parent-directory paths."
            )

    if pseudo_call_match:
        call_name = pseudo_call_match.group(1)
        return (
            f"Error: '{call_name}(...)' looks like a pseudo tool or skill-manual example, "
            "not a shell command. SKILL.md examples are documentation only. "
            "Use an explicit CLI command with run_command instead, such as "
            "`python skills/<skill>/main.py ...`."
        )

    if not allow_unsafe_commands and re.search(forbidden_regex, command):
        match = re.search(forbidden_regex, command).group(0)
        if isolated:
            return (
                f"Error: Isolated-workspace command contains forbidden "
                f"character/sequence '{match}'. Backticks, $(), and newlines "
                "stay blocked even inside a copy."
            )
        return (
            f"Error: Command contains forbidden character/sequence '{match}'. "
            "Live chat blocks &&, ||, redirects, backticks, $(), and newlines. "
            "Use run_steps for a sequential unittest then py_compile chain, or "
            "spawn_agent(isolation='copy') if you need shell chaining inside a "
            "clone. Enable 'Allow Unsafe Commands' in Config to bypass this "
            "restriction."
        )

    if not allow_unsafe_commands and _has_unquoted_semicolon(command):
        return "Error: Command contains forbidden character/sequence ';'. Enable 'Allow Unsafe Commands' in Config to bypass this restriction."

    # A bare pipe supports text filters, but must not feed an interpreter.
    if not allow_unsafe_commands and re.search(
        r"\|\s*(sh|bash|zsh|dash|ksh|fish|python[0-9.]*|perl|ruby|node|xargs)\b",
        command,
    ):
        return (
            "Error: Piping command output directly into an interpreter "
            "(e.g. '| sh', '| bash', '| python') is blocked to prevent "
            "remote code execution. Download and inspect the script first, "
            "or enable 'Allow Unsafe Commands' in Config."
        )

    lowered_command = command.lower()
    if "pythonpath=" in lowered_command:
        return (
            "Error: Environment assignment 'PYTHONPATH=' is blocked by LimeBot "
            "command policy (validate_command). This is a host policy check, "
            "not an OS or runtime environment rejection. Remove the assignment "
            "from the command; do not claim the environment blocked it."
        )
    if "ifs=" in lowered_command:
        return (
            "Error: Environment assignment 'IFS=' is blocked by LimeBot "
            "command policy (validate_command). This is a host policy check, "
            "not an OS or runtime environment rejection. Remove the assignment "
            "from the command; do not claim the environment blocked it."
        )

    if not allow_unsafe_commands and any(
        forbidden in lowered_command for forbidden in ("sudo", "chmod", "chown")
    ):
        return (
            "Error: Privileged commands are blocked. "
            "Enable 'Allow Unsafe Commands' in Config to allow them."
        )
    return None
