import json
import time
import os
import shutil
import asyncio
from pathlib import Path
from typing import Dict, Any, Optional
from loguru import logger


PERSONA_DIR = Path("persona")
SESSION_DIR = PERSONA_DIR / "sessions"
SESSION_FILE = SESSION_DIR / "sessions.json"
LOGS_DIR = SESSION_DIR / "logs"
SKILLS_DIR = Path("skills")


class SessionManager:
    def __init__(self):

        SESSION_DIR.mkdir(parents=True, exist_ok=True)
        LOGS_DIR.mkdir(parents=True, exist_ok=True)

        self.sessions: Dict[str, Any] = {}
        self._lock = asyncio.Lock()

        self.sessions = self._load_sessions()

    def _load_sessions(self) -> Dict[str, Any]:
        """Load sessions from JSON file."""
        if SESSION_FILE.exists():
            try:
                return json.loads(SESSION_FILE.read_text(encoding="utf-8"))
            except Exception as e:
                logger.error(f"Error loading sessions: {e}")
                return {}
        return {}

    async def _save_sessions(self):
        """Persist sessions to JSON file using an atomic write pattern (Flaw #1)."""
        async with self._lock:
            try:
                temp_file = SESSION_FILE.with_suffix(".tmp")
                content = json.dumps(self.sessions, indent=2)
                await asyncio.to_thread(temp_file.write_text, content, encoding="utf-8")
                await asyncio.to_thread(shutil.move, str(temp_file), str(SESSION_FILE))
            except Exception as e:
                logger.error(f"Error saving sessions: {e}")

    async def update_session(
        self,
        session_key: str,
        model: str,
        origin: str,
        usage: Optional[Any] = None,
        injected_files: Optional[list] = None,
        parent_id: Optional[str] = None,
        task: Optional[str] = None,
    ):

        current_time = time.time()

        if session_key not in self.sessions:
            self.sessions[session_key] = {
                "id": session_key,
                "created_at": current_time,
                "last_active": current_time,
                "origin": origin,
                "model": model,
                "total_tokens": {"input": 0, "output": 0, "total": 0},
                "skills": self._get_skills(),
                "injected_files": injected_files or [],
                "history_file": f"persona/sessions/logs/{session_key}.jsonl",
                "parent_id": parent_id,
                "task": task,
            }

        session = self.sessions[session_key]
        session["last_active"] = current_time
        session["model"] = model

        if parent_id:
            session["parent_id"] = parent_id
        if task:
            session["task"] = task

        if injected_files:
            existing = set(session.get("injected_files", []))
            for f in injected_files:
                existing.add(f)
            session["injected_files"] = sorted(list(existing))

        if usage:
            p_tokens = 0
            c_tokens = 0
            t_tokens = 0

            if isinstance(usage, dict):
                p_tokens = usage.get("prompt_tokens", 0)
                c_tokens = usage.get("completion_tokens", 0)
                t_tokens = usage.get("total_tokens", 0)
            else:
                p_tokens = getattr(usage, "prompt_tokens", 0)
                c_tokens = getattr(usage, "completion_tokens", 0)
                t_tokens = getattr(usage, "total_tokens", 0)

            if "total_tokens" not in session:
                session["total_tokens"] = {"input": 0, "output": 0, "total": 0}

            session["total_tokens"]["input"] += p_tokens
            session["total_tokens"]["output"] += c_tokens
            session["total_tokens"]["total"] += t_tokens

        asyncio.create_task(self._save_sessions())

    def append_chat_log(self, session_key: str, message: Dict[str, Any]):
        """Append a message to the session's JSONL log."""
        log_file = LOGS_DIR / f"{session_key}.jsonl"
        try:
            with open(log_file, "a", encoding="utf-8") as f:
                msg_with_time = message.copy()
                msg_with_time["timestamp"] = time.time()
                f.write(json.dumps(msg_with_time) + "\n")
        except Exception as e:
            logger.error(f"Error appending chat log: {e}")

    async def save_history(self, session_key: str, history: list):
        """Persist full conversation history to disk (JSON)."""
        history_dir = SESSION_DIR / "history"
        history_dir.mkdir(parents=True, exist_ok=True)
        history_file = history_dir / f"{session_key}.json"
        try:
            content = json.dumps(history, ensure_ascii=False, indent=2, default=str)
            await asyncio.to_thread(history_file.write_text, content, encoding="utf-8")
        except Exception as e:
            logger.error(f"Error saving history for {session_key}: {e}")

    async def load_history(self, session_key: str) -> list:
        """Load conversation history from disk. Returns [] if not found."""
        history_file = SESSION_DIR / "history" / f"{session_key}.json"
        if history_file.exists():
            try:
                content = await asyncio.to_thread(
                    history_file.read_text, encoding="utf-8"
                )
                return json.loads(content)
            except Exception as e:
                logger.debug(f"Could not load history for {session_key}: {e}")
        return []

    def delete_history(self, session_key: str):
        """Remove persisted history file for a session."""
        history_file = SESSION_DIR / "history" / f"{session_key}.json"
        if history_file.exists():
            try:
                os.remove(history_file)
            except Exception:
                pass

    async def delete_session(self, session_key: str) -> bool:
        """Delete a session and its logs."""
        async with self._lock:
            self.sessions = self._load_sessions()

            if session_key in self.sessions:
                del self.sessions[session_key]

                temp_file = SESSION_FILE.with_suffix(".tmp")
                content = json.dumps(self.sessions, indent=2)
                await asyncio.to_thread(temp_file.write_text, content, encoding="utf-8")
                await asyncio.to_thread(shutil.move, str(temp_file), str(SESSION_FILE))

                log_file = LOGS_DIR / f"{session_key}.jsonl"
                if log_file.exists():
                    try:
                        await asyncio.to_thread(os.remove, log_file)
                    except Exception as e:
                        logger.error(f"Error deleting log file {log_file}: {e}")

                self.delete_history(session_key)
                return True
        return False

    def get_sessions(self) -> Dict[str, Any]:
        """Return all sessions (reloaded from disk)."""
        self.sessions = self._load_sessions()
        current_skills = self._get_skills()
        for session in self.sessions.values():
            session["skills"] = current_skills
        return self.sessions

    def _get_skills(self) -> list:
        """Scan skills directory for available skills, excluding disabled ones."""
        skills = []

        enabled = []
        try:
            config_path = Path("limebot.json")
            if config_path.exists():
                data = json.loads(config_path.read_text(encoding="utf-8"))
                enabled = data.get("skills", {}).get("enabled", [])
        except Exception:
            pass

        if SKILLS_DIR.exists():
            for item in SKILLS_DIR.iterdir():
                if item.is_dir() and not item.name.startswith("__"):
                    if item.name in enabled:
                        skills.append(item.name)
        return sorted(skills)
