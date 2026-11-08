"""Shell process execution with bounded streaming and cancellation."""

from __future__ import annotations

import asyncio
import os
import re
import socket
import subprocess
import sys
from pathlib import Path
from typing import Any, Awaitable, Callable, Dict, Optional

from loguru import logger

from core.redaction import redact_sensitive_text

_DEFAULT_RUN_COMMAND_MAX_SECONDS = 180.0


class CommandExecutionService:
    """Execute validated commands using explicit Toolbox policy callbacks."""

    def __init__(
        self,
        *,
        validate_command: Callable[[str], Optional[str]],
        get_command_environment: Callable[[str], Dict[str, str]],
        send_progress: Callable[[str], Awaitable[Any]],
        get_active_tool_root: Callable[[], Path],
        detect_skill_path: Callable[[str], Any],
        get_config: Callable[[], Any],
    ) -> None:
        self.validate_command = validate_command
        self.get_command_environment = get_command_environment
        self.send_progress = send_progress
        self.get_active_tool_root = get_active_tool_root
        self.detect_skill_path = detect_skill_path
        self.get_config = get_config

    @staticmethod
    def preferred_python_executable() -> str:
        """Prefer a project virtual environment, then the running interpreter."""
        cwd = Path.cwd().resolve()
        if os.name == "nt":
            candidates = (
                cwd / ".venv" / "Scripts" / "python.exe",
                cwd / "venv" / "Scripts" / "python.exe",
            )
        else:
            candidates = (
                cwd / ".venv" / "bin" / "python",
                cwd / "venv" / "bin" / "python",
            )
        for candidate in candidates:
            try:
                if candidate.exists():
                    return str(candidate)
            except Exception:
                continue
        return str(Path(sys.executable).resolve())

    @staticmethod
    def windows_browser_binary(browser_name: str) -> Optional[Path]:
        candidates = {
            "opera": [
                Path.home() / "AppData" / "Local" / "Programs" / "Opera GX" / "opera.exe",
                Path.home() / "AppData" / "Local" / "Programs" / "Opera" / "opera.exe",
            ],
            "msedge": [
                Path(r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe"),
                Path(r"C:\Program Files\Microsoft\Edge\Application\msedge.exe"),
            ],
            "chrome": [
                Path(r"C:\Program Files\Google\Chrome\Application\chrome.exe"),
                Path(r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe"),
                Path.home()
                / "AppData"
                / "Local"
                / "Google"
                / "Chrome"
                / "Application"
                / "chrome.exe",
            ],
        }
        for candidate in candidates.get(browser_name, []):
            if candidate.exists():
                return candidate
        return None

    @staticmethod
    def is_local_port_in_use(port: int) -> bool:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
            sock.settimeout(0.25)
            return sock.connect_ex(("127.0.0.1", port)) == 0

    @staticmethod
    def extract_remote_debug_port(command: str) -> Optional[int]:
        match = re.search(
            r"--remote-debugging-port=(\d+)", command or "", re.IGNORECASE
        )
        return int(match.group(1)) if match else None

    @staticmethod
    def is_windows_process_running(image_name: str) -> bool:
        if os.name != "nt":
            return False
        try:
            result = subprocess.run(
                ["tasklist", "/FI", f"IMAGENAME eq {image_name}", "/FO", "CSV", "/NH"],
                capture_output=True,
                text=True,
                timeout=5,
            )
        except Exception:
            return False
        output = (result.stdout or "").strip().lower()
        return bool(
            output
            and "no tasks are running" not in output
            and image_name.lower() in output
        )

    @staticmethod
    def is_browser_remote_debug_launch(command: str) -> bool:
        lowered = (command or "").lower()
        return "--remote-debugging-port=" in lowered and any(
            token in lowered for token in ("opera.exe", "msedge.exe", "chrome.exe")
        )

    def normalize_browser_launch_command(
        self, command: str
    ) -> tuple[str, Optional[str]]:
        stripped = (command or "").strip()
        lowered = stripped.lower()
        if os.name != "nt" or "--remote-debugging-port=" not in lowered:
            return command, None

        match = re.search(r"--remote-debugging-port=(\d+)", stripped, re.IGNORECASE)
        port = int(match.group(1)) if match else 9222

        browser_name = None
        if re.match(r"^(?:start\s+)?opera(?:\s|$)", lowered):
            browser_name = "opera"
        elif re.match(r"^(?:start\s+)?msedge(?:\s|$)", lowered):
            browser_name = "msedge"
        elif re.match(r"^(?:start\s+)?chrome(?:\s|$)", lowered):
            browser_name = "chrome"

        if not browser_name:
            return command, None

        if self.is_local_port_in_use(port):
            return (
                command,
                f"Error: Port {port} is already in use. Close the browser currently exposing that CDP port or choose a different port before launching {browser_name}.",
            )

        if browser_name == "opera" and "--user-data-dir=" not in lowered:
            if self.is_windows_process_running("opera.exe"):
                return (
                    command,
                    "Error: Opera is already running. Close all Opera windows before launching it with --remote-debugging-port if you want LimeBot to attach to that session.",
                )

        browser_binary = self.windows_browser_binary(browser_name)
        if not browser_binary:
            return (
                command,
                f"Error: Could not find a local {browser_name} binary to launch with remote debugging.",
            )

        normalized = re.sub(
            r"^(?:start\s+)?(?:opera|msedge|chrome)\b",
            lambda _match: f'"{browser_binary}"',
            stripped,
            count=1,
            flags=re.IGNORECASE,
        )
        return normalized, None

    async def run_command(self, command: str) -> str:
        """Execute a terminal command with real-time progress updates."""
        command = str(command or "").strip()
        validation_error = self.validate_command(command)
        if validation_error:
            return validation_error

        try:
            command, browser_launch_error = self.normalize_browser_launch_command(command)
            if browser_launch_error:
                return browser_launch_error

            # Rewrite bare pip/python commands to use the running interpreter
            # so packages always install into the correct venv.
            _this_python = self.preferred_python_executable()
            _rewrites = [
                ("pip install ", f'"{_this_python}" -m pip install '),
                ("pip3 install ", f'"{_this_python}" -m pip install '),
                ("pip uninstall ", f'"{_this_python}" -m pip uninstall '),
                ("pip3 uninstall ", f'"{_this_python}" -m pip uninstall '),
                ("python3 ", f'"{_this_python}" '),
                ("python ", f'"{_this_python}" '),
            ]
            for _old, _new in _rewrites:
                if command.startswith(_old) or command.startswith(_old.capitalize()):
                    command = _new + command[len(_old) :]
                    break

            command_env = self.get_command_environment(command)

            await self.send_progress(f"💻 Running: {command}")

            import os
            import time as _time

            if self.is_browser_remote_debug_launch(command):
                port = self.extract_remote_debug_port(command) or 9222
                creationflags = 0
                if os.name == "nt":
                    creationflags = (
                        subprocess.DETACHED_PROCESS
                        | subprocess.CREATE_NEW_PROCESS_GROUP
                    )

                subprocess.Popen(
                    command,
                    shell=True,
                    cwd=str(self.get_active_tool_root()),
                    stdin=subprocess.DEVNULL,
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                    creationflags=creationflags,
                    close_fds=True,
                    env=command_env,
                )

                deadline = _time.monotonic() + 10
                while _time.monotonic() < deadline:
                    if self.is_local_port_in_use(port):
                        return (
                            f"Success: Browser launched for CDP attach on "
                            f"http://127.0.0.1:{port}"
                        )
                    await asyncio.sleep(0.25)

                return (
                    f"Error: Browser launch command was started, but CDP port {port} "
                    "did not become available within 10 seconds."
                )

            kwargs = {
                "stdin": asyncio.subprocess.DEVNULL,
                "stdout": asyncio.subprocess.PIPE,
                "stderr": asyncio.subprocess.PIPE,
                "cwd": str(self.get_active_tool_root()),
                "env": command_env,
            }
            if os.name == "nt":
                kwargs["creationflags"] = subprocess.CREATE_NO_WINDOW

            process = await asyncio.create_subprocess_shell(command, **kwargs)

            full_output = []
            last_activity = _time.monotonic()
            stall_detected = False
            STALL_TIMEOUT = 0
            if self.get_config():
                try:
                    STALL_TIMEOUT = float(getattr(self.get_config(), "stall_timeout", 0))
                except (ValueError, TypeError):
                    pass

            # Bypass stall watchdog for installation/download/update commands
            is_install_cmd = any(
                keyword in command.lower()
                for keyword in ("install", "download", "setup", "update", "upgrade", "clone", "pull")
            )
            if is_install_cmd:
                STALL_TIMEOUT = None

            if STALL_TIMEOUT is not None and STALL_TIMEOUT <= 0:
                STALL_TIMEOUT = None

            last_progress = _time.monotonic()

            async def read_stream(stream, name):
                nonlocal last_activity, last_progress
                async for line in stream:
                    last_activity = _time.monotonic()
                    line_text = line.decode("utf-8", errors="replace").strip()
                    if line_text:
                        if len(line_text) > 1000:
                            line_text = line_text[:1000] + "... [Line too long]"

                        now = _time.monotonic()
                        if now - last_progress >= 0.02:
                            await self.send_progress(f"[{name}] {line_text}")
                            last_progress = now
                        full_output.append(line_text)

            async def _force_kill(proc):
                """Force-kill a process tree (Windows-safe)."""
                import os as _os

                try:
                    if _os.name == "nt":
                        import subprocess as _sp

                        await asyncio.to_thread(
                            _sp.call,
                            ["taskkill", "/F", "/T", "/PID", str(proc.pid)],
                            stdout=_sp.DEVNULL,
                            stderr=_sp.DEVNULL,
                        )
                    proc.kill()
                except Exception:
                    pass
                try:
                    await asyncio.wait_for(proc.wait(), timeout=3)
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
                    await asyncio.sleep(min(1.0, max(0.1, STALL_TIMEOUT / 4)))
                    idle = _time.monotonic() - last_activity
                    if idle >= STALL_TIMEOUT:
                        stall_detected = True
                        logger.warning(
                            f"Stall detected ({STALL_TIMEOUT}s no output): "
                            f"{redact_sensitive_text(command)}"
                        )
                        await self.send_progress(
                            f"⚠️ Command stalled — no output for {STALL_TIMEOUT}s. "
                            "Killing process."
                        )
                        await _force_kill(process)
                        return

            timeout_val = 0
            if self.get_config():
                if hasattr(self.get_config(), "command_timeout"):
                    try:
                        timeout_val = float(getattr(self.get_config(), "command_timeout"))
                    except (ValueError, TypeError):
                        pass
                elif isinstance(self.get_config(), dict) and "command_timeout" in self.get_config():
                    try:
                        timeout_val = float(self.get_config().get("command_timeout", 0))
                    except (ValueError, TypeError):
                        pass

            # Installation/download/update commands may exceed the configured
            # one-shot timeout, but still receive the hard safety cap below.
            if is_install_cmd:
                timeout_val = None

            if timeout_val is None or timeout_val <= 0:
                try:
                    timeout_val = float(
                        getattr(
                            self.get_config(),
                            "run_command_max_seconds",
                            _DEFAULT_RUN_COMMAND_MAX_SECONDS,
                        )
                    )
                except (AttributeError, TypeError, ValueError):
                    timeout_val = _DEFAULT_RUN_COMMAND_MAX_SECONDS
                if timeout_val <= 0:
                    timeout_val = _DEFAULT_RUN_COMMAND_MAX_SECONDS

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
                logger.warning(
                    f"Command timed out after {timeout_val}s: "
                    f"{redact_sensitive_text(command)}"
                )
                await _force_kill(process)
                full_output.append(
                    f"[TIMEOUT] Command was terminated after {timeout_val} seconds."
                )
            except asyncio.CancelledError:
                logger.warning(
                    "Command execution cancelled by user: "
                    f"{redact_sensitive_text(command)}"
                )
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

            exit_code = process.returncode
            if not output:
                output = f"Success (Exit Code: {exit_code}, No output)"
            else:
                output += f"\n\nExit Code: {exit_code}"

            if exit_code not in (0, None):
                output = f"Error: Command failed with exit code {exit_code}.\n{output}"

            try:
                skill_info = self.detect_skill_path(command)
                if skill_info and process.returncode not in (0, None):
                    from core.skill_installer import SkillInstaller

                    skill_dir = skill_info["path"]
                    if skill_dir.exists():
                        installer = SkillInstaller()
                        meta = installer._read_metadata(skill_dir / "SKILL.md")
                        deps_ok, missing, _required = installer._evaluate_skill_deps(
                            skill_dir, meta
                        )
                        if not deps_ok:
                            logger.warning(
                                f"Skill '{skill_info['name']}' failed; missing dependencies detected: {missing}"
                            )
                            output += (
                                "\n\n[SKILL_DEPS_MISSING] "
                                f"Skill '{skill_info['name']}' is missing dependencies. "
                                f"python={missing.get('python', [])}, "
                                f"node={missing.get('node', [])}, "
                                f"binaries={missing.get('binaries', [])}. "
                                "Install dependencies and retry."
                            )
            except Exception as e:
                logger.debug(f"Dependency check skipped: {e}")

            return output
        except Exception as e:
            return f"Error executing command: {e}"
