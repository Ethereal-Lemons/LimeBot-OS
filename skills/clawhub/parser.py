import os
import subprocess
import sys
from pathlib import Path


CLAW_DIR = Path(__file__).parent
SKILLS_DIR = CLAW_DIR / "installed"


def get_npx_cmd():
    return "npx.cmd" if sys.platform == "win32" else "npx"


def install_skill(skill_name: str):
    """Installs a skill using npx clawhub directly into LimeBot's folder."""
    os.makedirs(SKILLS_DIR, exist_ok=True)
    try:
        # We use --workdir to tell clawhub NOT to go to the global .openclaw folder.
        # This keeps the skills inside the project.
        cmd = [
            get_npx_cmd(),
            "clawhub",
            "install",
            skill_name,
            "--workdir",
            str(CLAW_DIR),
            "--dir",
            "installed",
        ]

        result = subprocess.run(
            cmd,
            cwd=str(CLAW_DIR),  # Run it from the local clawhub skill folder
            capture_output=True,
            text=True,
            check=True,
        )
        return {"status": "success", "log": result.stdout}
    except subprocess.CalledProcessError as e:
        return {"status": "error", "log": f"{e.stderr}\n{e.stdout}"}


def get_installed_skills():
    """Lists all skills currently downloaded."""
    if not SKILLS_DIR.exists():
        return []

    return [d.name for d in SKILLS_DIR.iterdir() if d.is_dir()]


def parse_skill_manifest(skill_path: Path):
    skill_md = skill_path / "SKILL.md"
    if not skill_md.exists():
        return None

    try:
        content = skill_md.read_text(encoding="utf-8")
        metadata = {}
        if content.startswith("---"):
            parts = content.split("---", 2)
            if len(parts) >= 2:
                for line in parts[1].splitlines():
                    if ":" in line:
                        k, v = line.split(":", 1)
                        metadata[k.strip()] = v.strip().strip('"').strip("'")

        tool_name = (
            metadata.get("name", skill_path.name)
            .replace("-", "_")
            .replace("@", "")
            .replace("/", "_")
        )
        tool_description = metadata.get("description", "A ClawHub skill.")

        return {
            "name": f"clawhub_{tool_name}",
            "description": tool_description,
            "parameters": {
                "type": "OBJECT",
                "properties": {
                    "args": {
                        "type": "STRING",
                        "description": "Arguments to pass to the CLI tool (JSON format or string).",
                    }
                },
                "required": ["args"],
            },
        }
    except Exception as e:
        print(f"Error parsing {skill_md}: {e}")
        return None


def get_all_gemini_tools():
    tools = []
    for skill_name in get_installed_skills():
        skill_path = SKILLS_DIR / skill_name
        tool_def = parse_skill_manifest(skill_path)
        if tool_def:
            tools.append(tool_def)
    return tools


def run_skill(skill_name: str, args: str):
    skill_path = SKILLS_DIR / skill_name
    if not skill_path.exists():
        return f"Error: Skill {skill_name} not found."

    try:
        cmd = [get_npx_cmd(), "clawhub", "run", skill_name]
        if args:
            cmd.append(args)

        result = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
        return result.stdout or result.stderr
    except Exception as e:
        return str(e)
