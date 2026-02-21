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

        unsafe_allowed = False
        if self.config:
            unsafe_allowed = getattr(self.config, "allow_unsafe_commands", False)

        if not unsafe_allowed and re.search(forbidden_regex, command):
            match = re.search(forbidden_regex, command).group(0)
            return f"Error: Command contains forbidden character/sequence '{match}'. Enable 'Allow Unsafe Commands' in Config to bypass this restriction."

        if any(
            f in command.lower()
            for f in ["sudo", "chmod", "chown", "ifs=", "pythonpath="]
        ):
            return "Error: Command or environment manipulation forbidden."

        try:
            await self.send_progress(f"💻 Running: {command}")

            import asyncio
            import shlex

            # shlex.split handles quoted arguments correctly
            # e.g., 'python script.py "arg with spaces"' -> ['python', 'script.py', 'arg with spaces']
            args = shlex.split(command)

            process = await asyncio.create_subprocess_exec(
                *args, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE
            )

            stdout, stderr = await process.communicate()
            output = stdout.decode().strip()
            error = stderr.decode().strip()

            result = output
            if error:
                result += f"\nError Output:\n{error}"

            exit_code_str = ""
            if process.returncode != 0:
                exit_code_str = f"\nExit Code: {process.returncode}"

            await self.send_progress("✅ Command finished.")
            return result + exit_code_str

        except Exception as e:
            logger.error(f"Error running command: {e}")
            await self.send_progress(f"❌ Command failed: {e}")
            return f"Error running command: {e}"

    async def google_search(self, query: str) -> str:
        """Perform a Google search."""
        try:
            await self.send_progress(f"🔍 Searching Google: {query}")
            from skills.clawhub.google_search.main import search

            results = await asyncio.to_thread(search, query)
            await self.send_progress("✅ Search complete.")
            return results
        except Exception as e:
            logger.error(f"Search error: {e}")
            return f"Error searching Google: {e}"

    async def browser_navigate(self, url: str) -> str:
        """Navigate to a URL using the browser skill."""
        try:
            await self.send_progress(f"🌐 Navigating to: {url}")
            from skills.browser.main import browser_manager

            result = await browser_manager.navigate(url)
            await self.send_progress("✅ Page loaded.")
            return result
        except Exception as e:
            return f"Error navigating: {e}"

    async def browser_click(self, element_id: str) -> str:
        """Click an element on the current page."""
        try:
            await self.send_progress(f"🖱️ Clicking element: {element_id}")
            from skills.browser.main import browser_manager

            result = await browser_manager.click(element_id)
            await self.send_progress("✅ Clicked.")
            return result
        except Exception as e:
            return f"Error clicking: {e}"

    async def browser_type(self, element_id: str, text: str) -> str:
        """Type text into an element."""
        try:
            await self.send_progress(f"⌨️ Typing into {element_id}")
            from skills.browser.main import browser_manager

            result = await browser_manager.type_text(element_id, text)
            await self.send_progress("✅ Typed.")
            return result
        except Exception as e:
            return f"Error typing: {e}"

    async def browser_scroll(self, direction: str = "down") -> str:
        """Scroll the page."""
        try:
            await self.send_progress(f"📜 Scrolling {direction}")
            from skills.browser.main import browser_manager

            result = await browser_manager.scroll(direction)
            await self.send_progress("✅ Scrolled.")
            return result
        except Exception as e:
            return f"Error scrolling: {e}"

    async def browser_extract(self, selector: str = "body") -> str:
        """Extract text from the page."""
        try:
            await self.send_progress("📄 Extracting page content...")
            from skills.browser.main import browser_manager

            result = await browser_manager.extract_text(selector)
            await self.send_progress("✅ Extracted.")
            return result
        except Exception as e:
            return f"Error extracting: {e}"

    async def memory_search(self, query: str) -> str:
        """Search through long-term memory."""
        if not self.vector_service:
            return "Memory service unavailable."
        try:
            await self.send_progress(f"🧠 Searching memory for: {query}")
            results = await self.vector_service.search_memory(query)
            await self.send_progress("✅ Memory search complete.")
            return results
        except Exception as e:
            return f"Error searching memory: {e}"
