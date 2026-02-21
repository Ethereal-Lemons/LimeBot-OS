"""
Skill Executor - Runs skill scripts safely.
"""

import subprocess
from pathlib import Path
from typing import Optional

from .registry import SkillRegistry


class SkillExecutor:
    """
    Executes skill scripts with proper environment and working directory.
    """

    def __init__(self, registry: SkillRegistry, workspace_root: str = "."):
        """
        Initialize the executor.

        Args:
            registry: The skill registry
            workspace_root: Root directory for command execution
        """
        self.registry = registry
        self.workspace_root = Path(workspace_root).absolute()

    def execute(
        self, command: str, skill_name: Optional[str] = None, timeout: int = 300
    ) -> str:
        """
        Execute a command, optionally with skill-specific environment.

        Args:
            command: The command to execute
            skill_name: Optional skill name for environment injection
            timeout: Command timeout in seconds

        Returns:
            Command output (stdout + stderr)
        """

        if skill_name:
            env = self.registry.get_skill_env(skill_name)
            skill = self.registry.get_skill(skill_name)
            cwd = skill["base_dir"] if skill else str(self.workspace_root)
        else:
            env = None
            cwd = str(self.workspace_root)

        try:
            print(f"[Executor] Running: {command[:80]}...")
            result = subprocess.run(
                command,
                shell=True,
                cwd=cwd,
                env=env,
                capture_output=True,
                text=True,
                timeout=timeout,
            )

            output = result.stdout
            if result.stderr:
                output += f"\nSTDERR:\n{result.stderr}"

            return output.strip() or "(Command executed with no output)"

        except subprocess.TimeoutExpired:
            return f"Error: Command timed out after {timeout} seconds"
        except Exception as e:
            return f"Error executing command: {e}"

    def detect_skill_from_command(self, command: str) -> Optional[str]:
        """
        Try to detect which skill a command belongs to based on the path.

        Args:
            command: The command string

        Returns:
            Skill name if detected, None otherwise
        """
        for name, skill in self.registry.skills.items():
            base_dir = skill["base_dir"]
            if base_dir in command or f"skills/{name}" in command:
                return name
        return None
