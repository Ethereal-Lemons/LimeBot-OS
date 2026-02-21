"""
Skill Installer — install, uninstall, update, and list external skills.

Skills can be installed from Git repos (GitHub, GitLab, etc.) or created locally.
Installed skill metadata is tracked in limebot.json under skills.installed.

Usage (CLI):
    python -m core.skill_installer install <repo_url_or_shorthand> [--ref branch] [--name override]
    python -m core.skill_installer uninstall <skill_name>
    python -m core.skill_installer update <skill_name>
    python -m core.skill_installer list
    python -m core.skill_installer enable <skill_name>
    python -m core.skill_installer disable <skill_name>

Shorthand:
    owner/repo  →  expands to https://github.com/owner/repo
"""

import json
import re
import shutil
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from loguru import logger

SKILLS_DIR = Path("skills")
CLAW_SKILLS_DIR = SKILLS_DIR / "clawhub" / "installed"
CONFIG_FILE = Path("limebot.json")


_SKILL_NAME_RE = re.compile(r"^[a-zA-Z0-9_-]{1,64}$")


def _is_valid_skill_name(name: str) -> bool:
    return bool(_SKILL_NAME_RE.match(name))


class SkillInstaller:
    """Manages external skill installation, updates, and removal."""

    def __init__(self):
        self.config = self._load_config()

    def _load_config(self) -> dict:
        if CONFIG_FILE.exists():
            try:
                return json.loads(CONFIG_FILE.read_text(encoding="utf-8"))
            except Exception as e:
                logger.warning(f"Failed to load {CONFIG_FILE}, using defaults: {e}")
        return {"skills": {"enabled": [], "installed": {}}}

    def _save_config(self) -> None:
        self.config.setdefault("skills", {})
        self.config["skills"].setdefault("enabled", [])
        self.config["skills"].setdefault("installed", {})
        try:
            CONFIG_FILE.write_text(
                json.dumps(self.config, indent=2, ensure_ascii=False), encoding="utf-8"
            )
        except Exception as e:
            logger.error(f"Failed to save config to {CONFIG_FILE}: {e}")

    @staticmethod
    def _expand_repo_url(repo_url: str) -> str:
        """Expand 'owner/repo' shorthand to a full GitHub URL."""
        url = repo_url.strip()
        if re.match(r"^[\w.-]+/[\w.-]+$", url):
            return f"https://github.com/{url}"
        return url

    @staticmethod
    def _resolve_name(repo_url: str) -> str:
        """Derive skill name from a repo URL."""
        clean = repo_url.rstrip("/")
        if clean.endswith(".git"):
            clean = clean[:-4]
        basename = clean.split("/")[-1]

        for prefix in ("limebot-skill-", "lime-skill-", "lb-skill-"):
            if basename.startswith(prefix):
                return basename[len(prefix) :]
        return basename

    @staticmethod
    def _read_metadata(skill_md: Path) -> dict:
        """Read metadata (version, description) from SKILL.md frontmatter."""
        meta: dict = {"version": "unknown", "description": ""}
        if not skill_md.exists():
            return meta

        try:
            content = skill_md.read_text(encoding="utf-8")
            if content.startswith("---"):
                parts = content.split("---", 2)
                if len(parts) >= 2:
                    for line in parts[1].splitlines():
                        if ":" in line:
                            key, val = line.split(":", 1)
                            key = key.strip()
                            val = val.strip().strip('"').strip("'")
                            if key == "version":
                                meta["version"] = val
                            elif key == "description":
                                meta["description"] = val
        except Exception as e:
            logger.warning(f"Could not read metadata from {skill_md}: {e}")
        return meta

    @staticmethod
    def _install_deps(target_dir: Path) -> list[str]:
        """Install Python and/or Node.js dependencies. Returns log lines."""
        logs: list[str] = []

        reqs_file = target_dir / "requirements.txt"
        if reqs_file.exists():
            logger.info("Installing Python dependencies...")
            pip_result = subprocess.run(
                [
                    sys.executable,
                    "-m",
                    "pip",
                    "install",
                    "-r",
                    str(reqs_file),
                    "--quiet",
                ],
                capture_output=True,
                text=True,
                timeout=120,
            )
            if pip_result.returncode != 0:
                logger.warning(f"pip errors:\n{pip_result.stderr.strip()}")
                logs.append(f"pip: {pip_result.stderr.strip()}")
            else:
                logs.append("pip: dependencies installed")

        pkg_json = target_dir / "package.json"
        if pkg_json.exists():
            logger.info("Installing Node.js dependencies...")
            npm_cmd = "npm.cmd" if sys.platform == "win32" else "npm"
            npm_result = subprocess.run(
                [npm_cmd, "install"],
                cwd=str(target_dir),
                capture_output=True,
                text=True,
                timeout=120,
            )
            if npm_result.returncode != 0:
                logger.warning(f"npm errors:\n{npm_result.stderr.strip()}")
                logs.append(f"npm: {npm_result.stderr.strip()}")
            else:
                logs.append("npm: dependencies installed")

        return logs

    def _clone_repo(
        self, repo_url: str, target_dir: Path, ref: str, explicit_ref: bool
    ) -> Optional[str]:
        """Clone a repo. Returns None on success, error message on failure."""
        try:
            cmd = ["git", "clone", "--depth=1"]
            if explicit_ref:
                cmd += ["--branch", ref]
            cmd += [repo_url, str(target_dir)]

            result = subprocess.run(cmd, capture_output=True, text=True, timeout=60)
            if result.returncode == 0:
                return None

            if not explicit_ref:
                return f"Git clone failed: {result.stderr.strip()}"

            logger.info(f"Branch '{ref}' not found, retrying with default branch...")
            shutil.rmtree(target_dir, ignore_errors=True)
            fallback = subprocess.run(
                ["git", "clone", "--depth=1", repo_url, str(target_dir)],
                capture_output=True,
                text=True,
                timeout=60,
            )
            if fallback.returncode == 0:
                return None
            return f"Git clone failed: {fallback.stderr.strip()}"

        except FileNotFoundError:
            return "Git is not installed or not in PATH."
        except subprocess.TimeoutExpired:
            shutil.rmtree(target_dir, ignore_errors=True)
            return "Clone timed out after 60 seconds."

    def install(
        self,
        repo_url: str,
        ref: str = "main",
        name: Optional[str] = None,
        explicit_ref: bool = False,
    ) -> dict:
        """Clone a skill repo and register it."""
        repo_url = self._expand_repo_url(repo_url)
        skill_name = name or self._resolve_name(repo_url)

        if not _is_valid_skill_name(skill_name):
            return {
                "status": "error",
                "message": f"Invalid skill name '{skill_name}'. Only alphanumeric, hyphens, and underscores are allowed.",
            }

        SKILLS_DIR.mkdir(parents=True, exist_ok=True)
        target_dir = SKILLS_DIR / skill_name

        try:
            resolved = target_dir.resolve()
            if not str(resolved).startswith(str(SKILLS_DIR.resolve())):
                return {
                    "status": "error",
                    "message": "Path traversal detected in skill name.",
                }
        except Exception as e:
            return {"status": "error", "message": f"Path resolution error: {e}"}

        if target_dir.exists():
            return {
                "status": "error",
                "message": f"Skill '{skill_name}' is already installed. Use 'update' to upgrade it.",
            }

        logger.info(f"Cloning {repo_url} → skills/{skill_name}...")
        error = self._clone_repo(repo_url, target_dir, ref, explicit_ref)
        if error:
            return {"status": "error", "message": error}

        skill_md = target_dir / "SKILL.md"
        if not skill_md.exists():
            shutil.rmtree(target_dir, ignore_errors=True)
            return {
                "status": "error",
                "message": f"Invalid skill: no SKILL.md found in {repo_url}. Cleaned up.",
            }

        meta = self._read_metadata(skill_md)
        version = meta.get("version", "unknown")
        description = meta.get("description", "")

        dep_logs = self._install_deps(target_dir)

        installed = self.config.setdefault("skills", {}).setdefault("installed", {})
        installed[skill_name] = {
            "repo": repo_url,
            "ref": ref,
            "version": version,
            "installed_at": datetime.now(timezone.utc).isoformat(),
            "source": "git",
        }
        enabled: list = self.config["skills"].setdefault("enabled", [])
        if skill_name not in enabled:
            enabled.append(skill_name)
        self._save_config()

        logger.success(f"Skill '{skill_name}' installed successfully!")
        return {
            "status": "success",
            "message": f"Skill '{skill_name}' installed and enabled. Restart LimeBot to activate.",
            "skill_name": skill_name,
            "version": version,
            "description": description,
            "deps": dep_logs,
        }

    def uninstall(self, skill_name: str, force: bool = False) -> dict:
        """Remove an installed skill."""

        if not _is_valid_skill_name(skill_name):
            return {"status": "error", "message": f"Invalid skill name '{skill_name}'."}

        installed = self.config.get("skills", {}).get("installed", {})
        if skill_name not in installed and not force:
            return {
                "status": "error",
                "message": f"Skill '{skill_name}' is not installed.",
            }

        target_dir = SKILLS_DIR / skill_name
        if target_dir.exists():
            shutil.rmtree(target_dir, ignore_errors=True)

        if skill_name in installed:
            del installed[skill_name]

        enabled: list = self.config["skills"].setdefault("enabled", [])
        if skill_name in enabled:
            enabled.remove(skill_name)

        self._save_config()
        logger.success(f"Skill '{skill_name}' uninstalled.")
        return {"status": "success", "message": f"Skill '{skill_name}' removed."}

    def update(self, skill_name: str) -> dict:
        """Pull latest changes for a skill and re-install deps."""
        installed = self.config.get("skills", {}).get("installed", {})
        if skill_name not in installed:
            return {
                "status": "error",
                "message": f"Skill '{skill_name}' is not installed.",
            }

        target_dir = SKILLS_DIR / skill_name
        if not target_dir.exists():
            return {
                "status": "error",
                "message": f"Skill directory for '{skill_name}' is missing.",
            }

        logger.info(f"Updating skill '{skill_name}'...")
        try:
            result = subprocess.run(
                ["git", "pull"],
                cwd=target_dir,
                capture_output=True,
                text=True,
                timeout=60,
            )
            if result.returncode != 0:
                return {
                    "status": "error",
                    "message": f"Git pull failed: {result.stderr.strip()}",
                }
        except subprocess.TimeoutExpired:
            return {"status": "error", "message": "Update timed out."}

        dep_logs = self._install_deps(target_dir)

        skill_md = target_dir / "SKILL.md"
        meta = self._read_metadata(skill_md)
        version = meta.get("version", "unknown")
        installed[skill_name]["version"] = version
        installed[skill_name]["updated_at"] = datetime.now(timezone.utc).isoformat()
        self._save_config()

        logger.success(f"Skill '{skill_name}' updated to version {version}.")
        return {
            "status": "success",
            "message": f"Skill updated to {version}. Restart to apply.",
            "version": version,
            "deps": dep_logs,
        }

    def enable(self, skill_name: str) -> dict:
        """Enable an installed skill."""
        enabled: list = self.config["skills"].setdefault("enabled", [])
        if skill_name not in enabled:
            enabled.append(skill_name)
            self._save_config()
        return {"status": "success", "message": f"Skill '{skill_name}' enabled."}

    def disable(self, skill_name: str) -> dict:
        """Disable an installed skill."""
        enabled: list = self.config["skills"].setdefault("enabled", [])
        if skill_name in enabled:
            enabled.remove(skill_name)
            self._save_config()
        return {"status": "success", "message": f"Skill '{skill_name}' disabled."}

    def list_skills(self) -> dict:
        """List all skills with their source and status."""
        enabled: list = self.config.get("skills", {}).get("enabled", [])
        installed: dict = self.config.get("skills", {}).get("installed", {})
        skills = []

        # List LimeBot Skills
        if SKILLS_DIR.exists():
            for folder in sorted(SKILLS_DIR.iterdir()):
                if not folder.is_dir() or folder.name.startswith(("__", ".")):
                    continue

                # Skip the clawhub directory itself as it's a manager
                if folder.name == "clawhub":
                    continue

                skill_md = folder / "SKILL.md"
                # If SKILL.md is missing, we assume it's a core skill like 'browser' if it has python files
                # or just use folder name as description

                meta = self._read_metadata(skill_md)
                name = folder.name
                entry = installed.get(name, {})
                version = entry.get("version") or meta.get("version") or ""
                repo = entry.get("repo", "")

                # Core skills might not be in 'installed' map if they come pre-packaged
                source = entry.get("source", "limebot")

                skills.append(
                    {
                        "name": name,
                        "id": name,
                        "type": "limebot",
                        "source": source,
                        "enabled": name in enabled,
                        "active": name in enabled,
                        "version": version,
                        "description": meta.get("description", "Core LimeBot Skill"),
                        "repo": repo,
                    }
                )

        # List ClawHub Skills
        if CLAW_SKILLS_DIR.exists():
            for folder in sorted(CLAW_SKILLS_DIR.iterdir()):
                if not folder.is_dir() or folder.name.startswith(("__", ".")):
                    continue

                skill_md = folder / "SKILL.md"
                meta = self._read_metadata(skill_md)
                name = folder.name

                is_enabled = name in enabled

                skills.append(
                    {
                        "name": name,
                        "id": name,
                        "type": "clawhub",
                        "source": "clawhub",
                        "enabled": is_enabled,
                        "active": is_enabled,
                        "version": meta.get("version", "1.0.0"),
                        "description": meta.get("description", "ClawHub Skill"),
                        "repo": "",  # Managed by clawhub CLI
                    }
                )

        return {"skills": skills}


def main() -> None:
    """CLI entrypoint: python -m core.skill_installer <action> [args]"""
    if len(sys.argv) < 2:
        print(
            "Usage: python -m core.skill_installer <install|uninstall|update|list> [args]\n\n"
            "Commands:\n"
            "  install <repo_url> [--ref branch] [--name override]\n"
            "  uninstall <skill_name> [--force]\n"
            "  update <skill_name>\n"
            "  list"
        )
        sys.exit(1)

    installer = SkillInstaller()
    action = sys.argv[1].lower()

    if action == "install":
        if len(sys.argv) < 3:
            print("Error: provide a repo URL (or owner/repo shorthand)")
            sys.exit(1)
        repo_url = sys.argv[2]
        ref = "main"
        name = None
        explicit_ref = False
        args = sys.argv[3:]
        for i, arg in enumerate(args):
            if arg == "--ref" and i + 1 < len(args):
                ref = args[i + 1]
                explicit_ref = True
            elif arg == "--name" and i + 1 < len(args):
                name = args[i + 1]

        result = installer.install(
            repo_url, ref=ref, name=name, explicit_ref=explicit_ref
        )

    elif action == "uninstall":
        if len(sys.argv) < 3:
            print("Error: provide a skill name")
            sys.exit(1)
        force = "--force" in sys.argv
        result = installer.uninstall(sys.argv[2], force=force)

    elif action == "update":
        if len(sys.argv) < 3:
            print("Error: provide a skill name")
            sys.exit(1)
        result = installer.update(sys.argv[2])

    elif action == "list":
        result = installer.list_skills()

    elif action == "enable":
        if len(sys.argv) < 3:
            print("Error: provide a skill name")
            sys.exit(1)
        result = installer.enable(sys.argv[2])

    elif action == "disable":
        if len(sys.argv) < 3:
            print("Error: provide a skill name")
            sys.exit(1)
        result = installer.disable(sys.argv[2])

    else:
        print(f"Unknown command: {action}")
        sys.exit(1)

    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
