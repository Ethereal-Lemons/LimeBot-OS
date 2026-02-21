"""
Toolbox implementation — OS and OS-like capabilities for the agent.
Provides safe, whitelisted, and confirmed interface for file/OS operations.
"""

import asyncio
import shutil
import uuid
from pathlib import Path
from typing import Any, Dict, List, Union
from loguru import logger
from datetime import datetime

from core.tool_defs import build_tool_definitions
from core.vectors import get_vector_service


class Toolbox:
    """
    The Toolbox manages the agent's interaction with the external world.
    It provides a safe, whitelisted, and confirmed interface for file/OS operations.
    """

    def __init__(self, allowed_paths: List[str], bus: Any, config: Any):

        self.allowed_paths = [Path.cwd().resolve()]
        self.bus = bus
        self.config = config
        self.agent = None
        self.scheduler = None
        self.vector_service = get_vector_service(config)

        if allowed_paths:
            for p in allowed_paths:
                try:
                    path = Path(p).resolve()
                    if path not in self.allowed_paths:
                        self.allowed_paths.append(path)
                except Exception as e:
                    logger.warning(f"Could not add allowed path {p}: {e}")

        self.blocked_files = {
            ".env",
            "limebot.json",
            ".git",
            "__pycache__",
            "node_modules",
        }

    def set_agent(self, agent: Any):
        """Set the agent loop instance."""
        self.agent = agent

    def set_scheduler(self, scheduler: Any):
        """Set the scheduler instance."""
        self.scheduler = scheduler

    async def send_progress(self, message: str):
        """Broadcast tool progress if an agent/bus is available."""
        if self.agent and hasattr(self.agent, "send_tool_progress"):
            from core.context import tool_context

            ctx = tool_context.get()
            if ctx and "tc_id" in ctx:
                await self.agent.send_tool_progress(
                    ctx["tc_id"], ctx.get("chat_id", "system"), message
                )
        logger.info(f"🛠️ Tool Progress: {message}")

    def get_tool_definitions(self) -> List[Dict[str, Any]]:
        """Return the tool definitions based on the current config."""
        enabled_skills = []
        if self.config:
            if hasattr(self.config, "skills") and hasattr(
                self.config.skills, "enabled"
            ):
                enabled_skills = self.config.skills.enabled
            elif isinstance(self.config, dict) and "skills" in self.config:
                enabled_skills = self.config["skills"].get("enabled", [])

        # Base tools from tool_defs.py
        tools = build_tool_definitions(enabled_skills)

        # Load ClawHub tools dynamically
        try:
            from skills.clawhub.parser import get_all_gemini_tools

            claw_tools = get_all_gemini_tools()
            for ct in claw_tools:
                tools.append({"type": "function", "function": ct})
                logger.debug(f"Registered ClawHub tool: {ct['name']}")
        except Exception as e:
            logger.warning(f"Failed to load ClawHub tools: {e}")

        return tools

    def _is_path_allowed(self, path_str: Union[str, Path]) -> bool:
        """Enforce whitelist and block sensitive files."""
        try:
            target_path = Path(path_str).resolve()

            name = target_path.name.lower()
            _SENSITIVE_NAMES = frozenset(
                {
                    "limebot.json",
                    "package-lock.json",
                    "config.py",
                    "secrets.py",
                    ".env",
                    ".env.local",
                    ".env.production",
                }
            )
            _SENSITIVE_EXTENSIONS = frozenset({".pem", ".key", ".p12", ".pfx"})

            if (
                name in _SENSITIVE_NAMES
                or name in self.blocked_files
                or name.startswith(".env")
                or target_path.suffix.lower() in _SENSITIVE_EXTENSIONS
            ):
                return False

            for allowed in self.allowed_paths:
                if target_path == allowed or allowed in target_path.parents:
                    return True
            return False
        except Exception:
            return False

    async def read_file(self, path: str) -> str:
        """Read the contents of a file."""
        if not self._is_path_allowed(path):
            return f"Error: Access denied to path '{path}'."
        p = Path(path).resolve()
        if not p.exists():
            return f"Error: File '{path}' does not exist."
        if not p.is_file():
            return f"Error: '{path}' is a directory."
        try:
            content = await asyncio.to_thread(p.read_text, encoding="utf-8")

            if len(content) > 20000:
                return (
                    content[:20000]
                    + f"\n... (Truncated. Total length: {len(content)} chars)"
                )
            return content
        except Exception as e:
            return f"Error reading file: {e}"

    async def write_file(self, path: str, content: str) -> str:
        """Write content to a file."""
        if not self._is_path_allowed(path):
            return f"Error: Access denied to path '{path}'."
        p = Path(path).resolve()
        try:
            await asyncio.to_thread(p.parent.mkdir, parents=True, exist_ok=True)
            await asyncio.to_thread(p.write_text, content, encoding="utf-8")
            return f"Successfully wrote to '{path}'."
        except Exception as e:
            return f"Error writing file: {e}"

    async def delete_file(self, path: str) -> str:
        """Delete a file or directory."""
        if not self._is_path_allowed(path):
            return f"Error: Access denied to path '{path}'."
        p = Path(path).resolve()
        if not p.exists():
            return f"Error: Path '{path}' does not exist."
        try:
            if p.is_file():
                await asyncio.to_thread(p.unlink)
            elif p.is_dir():
                await asyncio.to_thread(shutil.rmtree, p)
            return f"Successfully deleted '{path}'."
        except Exception as e:
            return f"Error deleting: {e}"

    async def list_dir(self, path: str = ".") -> str:
        """List files in a directory."""
        if not self._is_path_allowed(path):
            return f"Error: Access denied to path '{path}'."
        p = Path(path).resolve()
        if not p.exists():
            return f"Error: Directory '{path}' does not exist."
        if not p.is_dir():
            return f"Error: Path '{path}' is not a directory."
        try:
            items = []
            for item in p.iterdir():
                type_str = "DIR" if item.is_dir() else "FILE"
                items.append(f"[{type_str}] {item.name}")
            return "\n".join(sorted(items))
        except Exception as e:
            return f"Error listing directory: {e}"

    async def run_command(self, command: str) -> str:
        """Execute a terminal command with real-time progress updates."""
        import re

        forbidden_regex = r"(\$\(|\`|;|&&|\|\||>|<|\n)"

        if re.search(forbidden_regex, command):
            match = re.search(forbidden_regex, command).group(0)
            return f"Error: Command contains forbidden character/sequence '{match}'."

        if any(
            f in command.lower()
            for f in ["sudo", "chmod", "chown", "ifs=", "pythonpath="]
        ):
            return "Error: Command or environment manipulation forbidden."

        try:
            await self.send_progress(f"💻 Running: {command}")

            import subprocess
            import os
            import time as _time

            kwargs = {
                "stdout": asyncio.subprocess.PIPE,
                "stderr": asyncio.subprocess.PIPE,
                "cwd": str(self.allowed_paths[0]),
            }
            if os.name == "nt":
                kwargs["creationflags"] = subprocess.CREATE_NO_WINDOW

            process = await asyncio.create_subprocess_shell(command, **kwargs)

            full_output = []
            last_activity = _time.monotonic()
            stall_detected = False
            STALL_TIMEOUT = 30
            if self.config:
                try:
                    STALL_TIMEOUT = float(getattr(self.config, "stall_timeout", 30))
                except (ValueError, TypeError):
                    pass
            if STALL_TIMEOUT <= 0:
                STALL_TIMEOUT = None

            async def read_stream(stream, name):
                nonlocal last_activity
                async for line in stream:
                    last_activity = _time.monotonic()
                    line_text = line.decode("utf-8", errors="replace").strip()
                    if line_text:
                        if len(line_text) > 1000:
                            line_text = line_text[:1000] + "... [Line too long]"

                        await self.send_progress(f"[{name}] {line_text}")
                        full_output.append(line_text)

                        await asyncio.sleep(0.01)

            async def _force_kill(proc):
                """Force-kill a process tree (Windows-safe)."""
                import os as _os

                try:
                    if _os.name == "nt":
                        import subprocess as _sp

                        _sp.call(
                            ["taskkill", "/F", "/T", "/PID", str(proc.pid)],
                            stdout=_sp.DEVNULL,
                            stderr=_sp.DEVNULL,
                        )
                    else:
                        proc.kill()
                except Exception:
                    pass

            def _diagnose_stall(cmd):
                """Return a diagnostic hint based on the command that stalled."""
                cl = cmd.lower()
                if "gh " in cl:
                    return (
                        "The GitHub CLI (gh) likely needs authentication. "
                        "Run 'gh auth login' in a terminal first, or the "
                        "command may need '--json' to avoid paging."
                    )
                if "git push" in cl or "git pull" in cl or "git clone" in cl:
                    return (
                        "Git is likely waiting for credentials. Ensure "
                        "SSH keys or a credential helper are configured."
                    )
                if "npm " in cl:
                    return "npm may be prompting for input. Try adding '--yes' or '-y'."
                if "pip " in cl:
                    return "pip may be prompting. Try adding '--no-input' or '-y'."
                if "ssh " in cl or "scp " in cl:
                    return "SSH is likely waiting for a password or key passphrase."
                return (
                    "The command produced no output and is likely waiting "
                    "for interactive input. Retry with non-interactive flags "
                    "(e.g. --yes, --confirm, -y, --no-input)."
                )

            async def stall_watchdog():
                nonlocal stall_detected
                if not STALL_TIMEOUT:
                    return
                while process.returncode is None:
                    await asyncio.sleep(5)
                    idle = _time.monotonic() - last_activity
                    if idle >= STALL_TIMEOUT:
                        stall_detected = True
                        logger.warning(
                            f"Stall detected ({STALL_TIMEOUT}s no output): {command}"
                        )
                        await self.send_progress(
                            f"⚠️ Command stalled — no output for {STALL_TIMEOUT}s. "
                            "Killing process."
                        )
                        await _force_kill(process)
                        return

            timeout_val = 300.0
            if self.config:
                if hasattr(self.config, "command_timeout"):
                    try:
                        timeout_val = float(getattr(self.config, "command_timeout"))
                    except (ValueError, TypeError):
                        pass
                elif isinstance(self.config, dict) and "command_timeout" in self.config:
                    try:
                        timeout_val = float(self.config.get("command_timeout", 300.0))
                    except (ValueError, TypeError):
                        pass
            if timeout_val <= 0:
                timeout_val = None

            try:
                await asyncio.wait_for(
                    asyncio.gather(
                        read_stream(process.stdout, "STDOUT"),
                        read_stream(process.stderr, "STDERR"),
                        stall_watchdog(),
                    ),
                    timeout=timeout_val,
                )
            except asyncio.TimeoutError:
                logger.warning(f"Command timed out after {timeout_val}s: {command}")
                await _force_kill(process)
                full_output.append(
                    f"[TIMEOUT] Command was terminated after {timeout_val} seconds."
                )
            except asyncio.CancelledError:
                logger.warning(f"Command execution cancelled by user: {command}")
                await _force_kill(process)
                raise

            if not stall_detected:
                await process.wait()

            output = "\n".join(full_output)

            output = re.sub(r"\x1b\[[0-9;]*[a-zA-Z]", "", output)

            if stall_detected:
                diagnosis = _diagnose_stall(command)
                output += (
                    f"\n\n[STALL] Command killed after {STALL_TIMEOUT}s of silence.\n"
                    f"Diagnosis: {diagnosis}"
                )

            if not output:
                output = f"Success (Exit Code: {process.returncode}, No output)"
            else:
                output += f"\n\nExit Code: {process.returncode}"

            return output
        except Exception as e:
            return f"Error executing command: {e}"

    async def memory_search(self, query: str) -> str:
        """Search vector memory with relevance scores."""
        if not self.vector_service:
            return "Error: Vector service not available."
        try:
            results = await self.vector_service.search(query, limit=5)
            if not results:
                return "No semantic matches found."

            res = ["Found relevant memories:"]
            for r in results:
                text = r.get("text", "No content")
                score = r.get("score") or r.get("_distance", 0)
                path = r.get("path", "Unknown source")
                res.append(f"- {text}\n  (Source: {path}, Score: {score})")
            return "\n\n".join(res)
        except Exception as e:
            return f"Error searching memory: {e}"

    async def spawn_agent(self, task: str, session_key: str = None) -> str:
        """Spawn a background agent task and wait for report."""
        if not self.agent:
            return "Error: Agent loop not linked to toolbox."

        if not session_key:
            from core.context import tool_context

            ctx = tool_context.get()
            session_key = f"system:{ctx.get('chat_id', 'global')}"

        sub_session_key = f"{session_key}_sub_{uuid.uuid4().hex[:6]}"
        logger.info(f"🚀 Spawning sub-agent '{sub_session_key}' for task: {task}")

        try:
            result = await self.agent.run_subagent(session_key, sub_session_key, task)
            return str(result)
        except Exception as e:
            logger.error(f"Error spawning agent: {e}")
            return f"Error spawning agent: {e}"

    async def cron_add(
        self,
        message: str,
        context: Dict[str, Any],
        time_expr: str = None,
        cron_expr: str = None,
    ) -> str:
        """Add a scheduled task."""
        if not self.scheduler:
            return "Error: Scheduler not available."
        try:
            trigger_time = None
            if time_expr:
                import time as time_module

                delta_seconds = 0
                if time_expr.endswith("s"):
                    delta_seconds = int(time_expr[:-1])
                elif time_expr.endswith("m"):
                    delta_seconds = int(time_expr[:-1]) * 60
                elif time_expr.endswith("h"):
                    delta_seconds = int(time_expr[:-1]) * 3600
                elif time_expr.endswith("d"):
                    delta_seconds = int(time_expr[:-1]) * 86400
                else:
                    return f"Error: Invalid time format '{time_expr}'. Use '10s', '5m', '2h'."
                trigger_time = time_module.time() + delta_seconds

            job_id = await self.scheduler.add_job(
                trigger_time=trigger_time,
                message=message,
                context=context,
                cron_expr=cron_expr,
            )
            return f"Success: Scheduled job {job_id}"
        except Exception as e:
            return f"Error adding cron: {e}"

    async def cron_list(self) -> str:
        """List all pending scheduled tasks."""
        if not self.scheduler:
            return "Error: Scheduler not available."
        try:
            jobs = await self.scheduler.list_jobs()
            if not jobs:
                return "No scheduled tasks."

            res = ["Scheduled Jobs:"]
            for j in jobs:
                trigger_str = (
                    datetime.fromtimestamp(j["trigger"]).strftime("%Y-%m-%d %H:%M:%S")
                    if j.get("trigger")
                    else "N/A"
                )
                recur = f" (RECURS: {j['cron_expr']})" if j.get("cron_expr") else ""
                res.append(f" - [{j['id']}] {trigger_str}{recur}: {j['payload']}")
            return "\n".join(res)
        except Exception as e:
            return f"Error listing cron: {e}"

    async def cron_remove(self, job_id: str) -> str:
        """Remove a scheduled task by ID."""
        if not self.scheduler:
            return "Error: Scheduler not available."
        try:
            if await self.scheduler.remove_job(job_id):
                return f"Success: Removed job {job_id}."
            return f"Error: Job {job_id} not found."
        except Exception as e:
            return f"Error removing cron: {e}"
