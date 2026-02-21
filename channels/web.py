"""Web channel implementation using FastAPI and WebSockets."""

import asyncio
import json
import os
import time
from typing import Any

import uvicorn
from fastapi import (
    FastAPI,
    WebSocket,
    WebSocketDisconnect,
    Request,
    Header,
    Depends,
    HTTPException,
    status,
)
from fastapi.staticfiles import StaticFiles
from fastapi.middleware.cors import CORSMiddleware
from litellm import completion
from loguru import logger

from channels.base import BaseChannel
from core.bus import MessageBus
from core.events import OutboundMessage
from core.session_manager import SessionManager

_CONTACTS_PATH_REL = ("data", "contacts.json")


def _contacts_path():
    from pathlib import Path

    return Path.cwd().joinpath(*_CONTACTS_PATH_REL)


def _load_contacts() -> dict:
    p = _contacts_path()
    if not p.exists():
        return {"allowed": [], "pending": [], "blocked": []}
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except Exception as e:
        logger.error(f"Error loading contacts: {e}")
        return {"allowed": [], "pending": [], "blocked": []}


def _save_contacts(contacts: dict) -> None:
    p = _contacts_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    try:
        p.write_text(json.dumps(contacts, indent=2), encoding="utf-8")
    except Exception as e:
        logger.error(f"Error saving contacts: {e}")


class WebChannel(BaseChannel):
    """
    Web channel that serves a WebSocket endpoint via FastAPI.
    """

    name = "web"

    def __init__(self, config: Any, bus: MessageBus):
        super().__init__(config, bus)
        self.app = FastAPI()
        self.server = None
        self.session_manager = SessionManager()

        async def verify_api_key(request: Request, x_api_key: str = Header(None)):
            internal_key = getattr(self.config.whitelist, "api_key", None)
            if not internal_key:
                return True
            provided_key = x_api_key or request.query_params.get("api_key")
            if provided_key != internal_key:
                raise HTTPException(
                    status_code=status.HTTP_401_UNAUTHORIZED,
                    detail="Invalid or missing API Key",
                )
            return True

        self.verify_auth = verify_api_key
        self._setup_routes()

        self.active_connections: set[WebSocket] = set()

        self.channels = []
        self._whatsapp_qr: str | None = None
        self.start_time = time.time()
        self.scheduler = None

        self._provider_models_cache: dict[str, list] = {}
        self._provider_models_last_update: dict[str, float] = {}

    def set_scheduler(self, scheduler: Any):
        self.scheduler = scheduler

    def set_agent(self, agent: Any):
        self.agent = agent

    def set_channels(self, channels: list):
        self.channels = channels

    def _setup_routes(self):
        self.app.add_middleware(
            CORSMiddleware,
            allow_origins=["*"],
            allow_credentials=True,
            allow_methods=["*"],
            allow_headers=["*"],
        )

        from pathlib import Path

        temp_dir = Path("temp")
        temp_dir.mkdir(exist_ok=True)
        self.app.mount("/temp", StaticFiles(directory="temp"), name="temp")

        @self.app.get("/api/identity")
        async def get_identity():
            from core.prompt import get_identity_data

            return get_identity_data()

        @self.app.get("/api/persona", dependencies=[Depends(self.verify_auth)])
        async def get_persona():
            from core.prompt import get_identity_data, SOUL_FILE, MOOD_FILE, USERS_DIR
            import re

            result = get_identity_data()
            result["soul_summary"] = ""
            if SOUL_FILE.exists():
                try:
                    soul_content = SOUL_FILE.read_text(encoding="utf-8")
                    lines = soul_content.strip().split("\n")
                    if lines and lines[0].startswith("#"):
                        lines = lines[1:]
                    result["soul_summary"] = " ".join(lines)[:300].strip()
                except Exception as e:
                    logger.error(f"Error reading soul summary: {e}")

            result["mood"] = ""
            if MOOD_FILE.exists():
                result["mood"] = MOOD_FILE.read_text(encoding="utf-8").strip()

            from config import load_config

            cfg = load_config()
            result["enable_dynamic_personality"] = getattr(
                cfg.llm, "enable_dynamic_personality", False
            )

            relationships = []
            if USERS_DIR.exists():
                for user_file in USERS_DIR.glob("*.md"):
                    try:
                        content = user_file.read_text(encoding="utf-8")
                        name_match = re.search(
                            r"\*\*Preferred Name:\*\*\s*(.*)", content, re.IGNORECASE
                        )
                        affinity_match = re.search(
                            r"\*\*Affinity Score:\*\*\s*(.*)", content, re.IGNORECASE
                        )
                        level_match = re.search(
                            r"\*\*Relationship Level:\*\*\s*(.*)",
                            content,
                            re.IGNORECASE,
                        )

                        relationships.append(
                            {
                                "id": user_file.stem,
                                "name": name_match.group(1).strip()
                                if name_match
                                else user_file.stem,
                                "affinity": int(affinity_match.group(1).strip())
                                if affinity_match
                                and affinity_match.group(1).strip().isdigit()
                                else 0,
                                "level": level_match.group(1).strip()
                                if level_match
                                else "Stranger",
                            }
                        )
                    except Exception as e:
                        logger.warning(
                            f"Error parsing user profile {user_file.name}: {e}"
                        )

            result["relationships"] = sorted(
                relationships, key=lambda x: x["affinity"], reverse=True
            )
            return result

        @self.app.put("/api/persona", dependencies=[Depends(self.verify_auth)])
        async def update_persona(data: dict):
            try:
                from pathlib import Path

                persona_dir = Path("persona")
                identity_file = persona_dir / "IDENTITY.md"
                mood_file = persona_dir / "MOOD.md"

                lines = ["# IDENTITY.md - Who I Am", ""]
                if data.get("name"):
                    lines.append(f"*   **Name:** {data['name']}")
                if data.get("emoji"):
                    lines.append(f"*   **Emoji:** {data['emoji']}")
                if data.get("pfp_url"):
                    lines.append(f"*   **Pfp_URL:** {data['pfp_url']}")
                if data.get("style"):
                    lines.append(f"*   **Style:** {data['style']}")
                if data.get("catchphrases"):
                    lines.append(f"*   **Catchphrases:** {data['catchphrases']}")
                if data.get("interests"):
                    lines.append(f"*   **Interests:** {data['interests']}")
                if data.get("birthday"):
                    lines.append(f"*   **Birthday:** {data['birthday']}")
                if data.get("discord_style"):
                    lines.append(f"*   **Discord Style:** {data['discord_style']}")
                if data.get("whatsapp_style"):
                    lines.append(f"*   **WhatsApp Style:** {data['whatsapp_style']}")
                if data.get("web_style"):
                    lines.append(f"*   **Web Style:** {data['web_style']}")
                if data.get("reaction_emojis"):
                    lines.append(f"*   **Reaction Emojis:** {data['reaction_emojis']}")
                lines.append("")
                identity_file.write_text("\n".join(lines), encoding="utf-8")

                mood_value = data.get("mood")
                if mood_value:
                    tmp_mood = mood_file.with_suffix(".tmp")
                    tmp_mood.write_text(mood_value, encoding="utf-8")
                    tmp_mood.replace(mood_file)

                logger.info(f"Persona updated: {data.get('name')}")
                return {"status": "success", "message": "Persona updated"}
            except Exception as e:
                logger.error(f"Error updating persona: {e}")
                return {"error": str(e)}

        @self.app.get("/api/persona/export", dependencies=[Depends(self.verify_auth)])
        async def export_persona():
            try:
                from pathlib import Path

                root_dir = Path(__file__).parent.parent
                persona_dir = root_dir / "persona"
                identity_content = ""
                soul_content = ""
                if persona_dir.exists():
                    for item in persona_dir.iterdir():
                        if item.is_file():
                            if item.name.lower() == "identity.md":
                                identity_content = item.read_text(encoding="utf-8")
                            elif item.name.lower() == "soul.md":
                                soul_content = item.read_text(encoding="utf-8")
                export_data = (
                    "<!-- SECTION: IDENTITY -->\n"
                    f"{identity_content}\n\n"
                    "<!-- SECTION: SOUL -->\n"
                    f"{soul_content}\n"
                )
                logger.info(
                    f"Persona export: identity={len(identity_content)}ch, soul={len(soul_content)}ch"
                )
                return {"filename": "limebot_persona.md", "content": export_data}
            except Exception as e:
                logger.error(f"Error exporting persona: {e}")
                return {"error": str(e)}

        @self.app.post("/api/persona/import", dependencies=[Depends(self.verify_auth)])
        async def import_persona(data: dict):
            try:
                import re
                import shutil
                from pathlib import Path

                content = data.get("content", "")
                if not content:
                    raise ValueError("No content provided")

                identity_match = re.search(
                    r"<!-- SECTION: IDENTITY -->\s*(.*?)\s*(?=<!-- SECTION: SOUL -->|$)",
                    content,
                    re.DOTALL,
                )
                soul_match = re.search(
                    r"<!-- SECTION: SOUL -->\s*(.*)", content, re.DOTALL
                )

                if not identity_match and not soul_match:
                    raise ValueError(
                        "Invalid persona file format. Missing proper section headers."
                    )

                root_dir = Path(__file__).parent.parent
                persona_dir = root_dir / "persona"
                persona_dir.mkdir(exist_ok=True)
                timestamp = int(time.time())

                def safe_update(filename, new_content):
                    target_path = persona_dir / filename
                    existing_path = next(
                        (
                            item
                            for item in persona_dir.iterdir()
                            if item.name.lower() == filename.lower()
                        ),
                        None,
                    )
                    if existing_path and existing_path.exists():
                        shutil.copy(
                            existing_path,
                            existing_path.with_suffix(f".md.{timestamp}.bak"),
                        )
                        target_path = existing_path
                    target_path.write_text(new_content.strip() + "\n", encoding="utf-8")
                    return target_path.name

                updated_files = []
                if identity_match:
                    updated_files.append(
                        safe_update("IDENTITY.md", identity_match.group(1))
                    )
                if soul_match:
                    updated_files.append(safe_update("SOUL.md", soul_match.group(1)))

                logger.info(f"Persona imported. Updated: {', '.join(updated_files)}")
                return {
                    "status": "success",
                    "message": f"Persona imported ({', '.join(updated_files)}). Backups created.",
                }
            except Exception as e:
                logger.error(f"Error importing persona: {e}")
                return {"error": str(e)}

        @self.app.get("/api/instances", dependencies=[Depends(self.verify_auth)])
        async def get_instances():
            sessions = self.session_manager.get_sessions()
            return list(sessions.values())

        @self.app.delete(
            "/api/instances/{instance_id}", dependencies=[Depends(self.verify_auth)]
        )
        async def delete_instance(instance_id: str):
            success = await self.session_manager.delete_session(instance_id)
            if success:
                return {
                    "status": "success",
                    "message": f"Instance {instance_id} deleted",
                }
            raise HTTPException(status_code=404, detail="Instance not found")

        @self.app.get("/api/sessions", dependencies=[Depends(self.verify_auth)])
        async def get_sessions():
            return await get_instances()

        @self.app.get("/api/llm/models")
        async def get_llm_models():
            models = [
                {
                    "id": "gemini/gemini-3.1-pro-preview",
                    "name": "Gemini 3.1 Pro (Preview)",
                    "provider": "gemini",
                },
                {
                    "id": "gemini/gemini-3-pro-preview",
                    "name": "Gemini 3 Pro (Preview)",
                    "provider": "gemini",
                },
                {
                    "id": "gemini/gemini-3-flash-preview",
                    "name": "Gemini 3 Flash (Preview)",
                    "provider": "gemini",
                },
                {
                    "id": "gemini/gemini-2.5-pro",
                    "name": "Gemini 2.5 Pro",
                    "provider": "gemini",
                },
                {
                    "id": "gemini/gemini-2.5-flash",
                    "name": "Gemini 2.5 Flash",
                    "provider": "gemini",
                },
                {
                    "id": "gemini/gemini-2.5-flash-lite",
                    "name": "Gemini 2.5 Flash-Lite",
                    "provider": "gemini",
                },
                {
                    "id": "gemini/gemini-2.0-flash",
                    "name": "Gemini 2.0 Flash",
                    "provider": "gemini",
                },
                {
                    "id": "gemini/gemini-2.0-flash-lite",
                    "name": "Gemini 2.0 Flash-Lite",
                    "provider": "gemini",
                },
                {
                    "id": "gemini/gemini-1.5-pro",
                    "name": "Gemini 1.5 Pro",
                    "provider": "gemini",
                },
                {
                    "id": "gemini/gemini-1.5-flash",
                    "name": "Gemini 1.5 Flash",
                    "provider": "gemini",
                },
                {"id": "openai/gpt-4o", "name": "GPT-4o", "provider": "openai"},
                {
                    "id": "openai/gpt-4o-mini",
                    "name": "GPT-4o Mini",
                    "provider": "openai",
                },
                {
                    "id": "anthropic/claude-3-5-sonnet-20241022",
                    "name": "Claude 3.5 Sonnet",
                    "provider": "anthropic",
                },
                {
                    "id": "anthropic/claude-3-opus-20240229",
                    "name": "Claude 3 Opus",
                    "provider": "anthropic",
                },
                {"id": "xai/grok-2", "name": "Grok 2", "provider": "xai"},
                {"id": "xai/grok-beta", "name": "Grok Beta", "provider": "xai"},
                {
                    "id": "deepseek/deepseek-chat",
                    "name": "DeepSeek Chat",
                    "provider": "deepseek",
                },
            ]

            from core.llm_utils import (
                fetch_openai_compatible_models,
                fetch_anthropic_models,
            )

            api_keys = {
                "nvidia": os.getenv("NVIDIA_API_KEY"),
                "xai": os.getenv("XAI_API_KEY"),
                "anthropic": os.getenv("ANTHROPIC_API_KEY"),
                "deepseek": os.getenv("DEEPSEEK_API_KEY"),
                "openai": os.getenv("OPENAI_API_KEY"),
            }

            current_time = time.time()

            async def update_provider_cache(provider, fetch_func, *args):
                last = self._provider_models_last_update.get(provider, 0)
                if current_time - last > 3600:
                    try:
                        fetched = await fetch_func(*args)
                        if fetched:
                            self._provider_models_cache[provider] = fetched
                            self._provider_models_last_update[provider] = current_time
                            logger.info(
                                f"Updated model cache for {provider}: {len(fetched)} models"
                            )
                    except Exception as e:
                        logger.warning(f"Failed to update {provider} models: {e}")

            if api_keys["nvidia"]:
                await update_provider_cache(
                    "nvidia",
                    fetch_openai_compatible_models,
                    api_keys["nvidia"],
                    "https://integrate.api.nvidia.com/v1",
                    "nvidia",
                    True,
                )
            if api_keys["xai"]:
                await update_provider_cache(
                    "xai",
                    fetch_openai_compatible_models,
                    api_keys["xai"],
                    "https://api.x.ai/v1",
                    "xai",
                    True,
                )
            if api_keys["anthropic"]:
                await update_provider_cache(
                    "anthropic",
                    fetch_anthropic_models,
                    api_keys["anthropic"],
                )

            existing_ids = {m["id"] for m in models}
            for cached_models in self._provider_models_cache.values():
                for cm in cached_models:
                    if cm["id"] not in existing_ids:
                        models.append(cm)
                        existing_ids.add(cm["id"])

            return {"models": models}

        @self.app.get("/api/llm/health", dependencies=[Depends(self.verify_auth)])
        async def check_llm_health():
            model = os.getenv("LLM_MODEL", "gemini/gemini-2.0-flash")
            start = time.time()
            try:
                from config import load_config

                cfg = load_config()
                await asyncio.to_thread(
                    completion,
                    model=model,
                    messages=[{"role": "user", "content": "hi"}],
                    max_tokens=5,
                    api_key=cfg.llm.api_key,
                )
                latency = int((time.time() - start) * 1000)
                return {
                    "status": "Healthy",
                    "latency_ms": latency,
                    "model": model,
                    "quota_remaining": "Unknown",
                }
            except Exception as e:
                error_msg = str(e)
                health_status = "Quota Exceeded" if "429" in error_msg else "Error"
                return {
                    "status": health_status,
                    "latency_ms": int((time.time() - start) * 1000),
                    "model": model,
                    "error": error_msg,
                }

        @self.app.get("/api/config", dependencies=[Depends(self.verify_auth)])
        async def get_config():
            from config import load_config

            cfg = load_config()
            env_dict = {
                "LLM_MODEL": cfg.llm.model,
                "GEMINI_API_KEY": cfg.llm.api_key,
                "DISCORD_TOKEN": cfg.discord.token,
                "DISCORD_ALLOW_FROM": ",".join(cfg.discord.allow_from),
                "DISCORD_ALLOW_CHANNELS": ",".join(cfg.discord.allow_channels),
                "DISCORD_ACTIVITY_TYPE": cfg.discord.activity_type,
                "DISCORD_ACTIVITY_TEXT": cfg.discord.activity_text,
                "DISCORD_STATUS": cfg.discord.status,
                "WHATSAPP_ALLOW_FROM": ",".join(cfg.whatsapp.allow_from),
                "ENABLE_WHATSAPP": str(cfg.whatsapp.enabled).lower(),
                "WHATSAPP_BRIDGE_URL": cfg.whatsapp.bridge_url,
                "ALLOWED_PATHS": cfg.whitelist.allowed_paths,
                "APP_API_KEY": cfg.whitelist.api_key,
                "ENABLE_DYNAMIC_PERSONALITY": str(
                    getattr(cfg.llm, "enable_dynamic_personality", False)
                ).lower(),
                "MAX_ITERATIONS": str(getattr(cfg, "max_iterations", 30)),
                "COMMAND_TIMEOUT": str(getattr(cfg, "command_timeout", 300.0)),
                "STALL_TIMEOUT": str(getattr(cfg, "stall_timeout", 30)),
                "PERSONALITY_WHITELIST": ",".join(
                    getattr(cfg, "personality_whitelist", [])
                ),
                "AUTONOMOUS_MODE": str(getattr(cfg, "autonomous_mode", False)).lower(),
            }
            for key in [
                "OPENAI_API_KEY",
                "ANTHROPIC_API_KEY",
                "XAI_API_KEY",
                "DEEPSEEK_API_KEY",
                "MISTRAL_API_KEY",
                "NVIDIA_API_KEY",
                "LLM_BASE_URL",
            ]:
                val = os.getenv(key)
                if val:
                    env_dict[key] = val
            return {"env": env_dict}

        @self.app.post("/api/config", dependencies=[Depends(self.verify_auth)])
        async def update_config(data: dict):
            """Update .env file and restart to apply changes."""
            try:
                from pathlib import Path

                env_file = Path(".env")
                new_env = data.get("env", {})

                if "ALLOWED_PATHS" in new_env:
                    paths_data = new_env.pop("ALLOWED_PATHS")
                    paths_file = Path("allowed_paths.txt")
                    if isinstance(paths_data, list):
                        paths = [str(p).strip() for p in paths_data if str(p).strip()]
                    else:
                        paths = [
                            p.strip() for p in str(paths_data).split(",") if p.strip()
                        ]
                    paths_file.write_text("\n".join(paths), encoding="utf-8")

                current_lines = (
                    env_file.read_text(encoding="utf-8").splitlines()
                    if env_file.exists()
                    else []
                )
                final_lines = []
                processed_keys: set[str] = set()

                for line in current_lines:
                    stripped = line.strip()
                    if "=" in stripped and not stripped.startswith("#"):
                        key = stripped.split("=", 1)[0].strip()
                        if key == "ALLOWED_PATHS":
                            continue
                        if key in new_env:
                            final_lines.append(f"{key}={new_env[key]}")
                            processed_keys.add(key)
                        else:
                            final_lines.append(line)
                    else:
                        final_lines.append(line)

                for key, val in new_env.items():
                    if key not in processed_keys:
                        final_lines.append(f"{key}={val}")

                env_file.write_text("\n".join(final_lines), encoding="utf-8")
                logger.info("Configuration saved. Restarting...")
                asyncio.get_running_loop().call_later(1.0, _spawn_restart)
                return {
                    "status": "updated",
                    "message": "Configuration saved. Restarting...",
                }
            except Exception as e:
                logger.error(f"Error updating config: {e}")
                return {"error": str(e)}

        @self.app.get("/api/setup/status")
        async def get_setup_status():
            try:
                from pathlib import Path

                env_file = Path(".env")
                if not env_file.exists():
                    return {
                        "configured": False,
                        "missing_keys": ["LLM_MODEL", "GEMINI_API_KEY"],
                    }

                content = env_file.read_text(encoding="utf-8")

                defined_keys: set[str] = set()
                for line in content.splitlines():
                    line = line.strip()
                    if line and not line.startswith("#") and "=" in line:
                        defined_keys.add(line.split("=", 1)[0].strip())

                missing = [
                    k for k in ["LLM_MODEL", "GEMINI_API_KEY"] if k not in defined_keys
                ]

                return {"configured": len(missing) == 0, "missing_keys": missing}
            except Exception as e:
                logger.error(f"Error checking setup status: {e}")
                return {"configured": False, "error": str(e)}

        @self.app.get("/api/setup/tailscale")
        async def get_tailscale_status():
            try:
                import socket
                import psutil

                interfaces = []
                tailscale_ip = None
                for interface, addrs in psutil.net_if_addrs().items():
                    for addr in addrs:
                        if addr.family == socket.AF_INET:
                            is_tailscale = "Tailscale" in interface or (
                                addr.address.startswith("100.")
                                and not addr.address.startswith("100.64")
                            )
                            if is_tailscale:
                                tailscale_ip = addr.address
                            interfaces.append(
                                {
                                    "name": interface,
                                    "ip": addr.address,
                                    "is_tailscale": is_tailscale,
                                }
                            )
                return {
                    "interfaces": interfaces,
                    "tailscale_ip": tailscale_ip,
                    "has_tailscale": tailscale_ip is not None,
                }
            except Exception as e:
                logger.error(f"Error checking tailscale: {e}")
                return {"error": str(e)}

        @self.app.get("/api/stats", dependencies=[Depends(self.verify_auth)])
        async def get_stats():
            uptime = int(time.time() - self.start_time)
            channel_stats = []
            for ch in self.channels:
                ch_status = "Connected"
                if hasattr(ch, "client") and hasattr(ch.client, "is_ready"):
                    if not ch.client.is_ready():
                        ch_status = "Connecting..."
                channel_stats.append(
                    {
                        "name": ch.name,
                        "type": ch.__class__.__name__,
                        "status": ch_status,
                    }
                )

            sessions = self.session_manager.get_sessions()
            subagents = [s for s in sessions.values() if s.get("parent_id")]
            return {
                "uptime": uptime,
                "gateway_url": "ws://localhost:8000/ws",
                "channels": channel_stats,
                "sessions": len(sessions),
                "sessions_count": len(sessions),
                "instances_count": len(
                    [s for s in sessions.values() if not s.get("parent_id")]
                ),
                "subagents_count": len(subagents),
                "cron_status": "Enabled",
            }

        @self.app.get("/api/metrics", dependencies=[Depends(self.verify_auth)])
        async def get_metrics():
            from core.metrics import MetricsCollector

            return MetricsCollector().get_snapshot()

        @self.app.get("/api/logs", dependencies=[Depends(self.verify_auth)])
        async def get_logs(lines: int = 100):
            """Return the last N lines of logs."""
            try:
                from pathlib import Path

                log_file = Path("logs/limebot.log")
                if not log_file.exists():
                    return {"logs": ["No logs found."]}

                chunk_size = 8192
                result_lines: list[str] = []
                with log_file.open("rb") as f:
                    f.seek(0, 2)
                    remaining = f.tell()
                    buffer = b""
                    while remaining > 0 and len(result_lines) <= lines:
                        read_size = min(chunk_size, remaining)
                        remaining -= read_size
                        f.seek(remaining)
                        buffer = f.read(read_size) + buffer
                        result_lines = buffer.decode(
                            "utf-8", errors="replace"
                        ).splitlines()

                return {"logs": result_lines[-lines:]}
            except Exception as e:
                logger.error(f"Error reading logs: {e}")
                return {"logs": [f"Error reading logs: {e}"]}

        @self.app.get("/api/memory", dependencies=[Depends(self.verify_auth)])
        async def get_memory():
            from core.vectors import get_vector_service

            vector_service = get_vector_service()
            if not vector_service.is_enabled:
                return {"enabled": False, "memories": []}

            try:
                memories = await vector_service.get_all(limit=100)
                return {"enabled": True, "memories": memories}
            except Exception as e:
                logger.error(f"Error reading memory: {e}")
                return {"enabled": True, "error": str(e), "memories": []}

        @self.app.delete(
            "/api/memory/{entry_id}", dependencies=[Depends(self.verify_auth)]
        )
        async def delete_memory(entry_id: str):
            from core.vectors import get_vector_service

            vector_service = get_vector_service()
            if not vector_service.is_enabled:
                raise HTTPException(status_code=400, detail="Memory system disabled")

            success = await vector_service.delete_entry(entry_id)
            if success:
                return {"status": "success", "message": f"Memory {entry_id} deleted"}
            raise HTTPException(status_code=500, detail="Failed to delete memory")

        @self.app.get("/api/skills", dependencies=[Depends(self.verify_auth)])
        async def list_skills():
            from core.skill_installer import SkillInstaller

            return SkillInstaller().list_skills()

        @self.app.post("/api/skills/install", dependencies=[Depends(self.verify_auth)])
        async def install_skill(request: Request):
            from core.skill_installer import SkillInstaller

            body = await request.json()
            repo_url = body.get("repo_url", "")
            if not repo_url:
                return {"status": "error", "message": "repo_url is required"}
            return SkillInstaller().install(
                repo_url, ref=body.get("ref", "main"), name=body.get("name")
            )

        @self.app.delete(
            "/api/skills/{skill_name}", dependencies=[Depends(self.verify_auth)]
        )
        async def uninstall_skill(skill_name: str, force: bool = False):
            from core.skill_installer import SkillInstaller

            return SkillInstaller().uninstall(skill_name, force=force)

        @self.app.post(
            "/api/skills/{skill_name}/update", dependencies=[Depends(self.verify_auth)]
        )
        async def update_skill(skill_name: str):
            from core.skill_installer import SkillInstaller

            return SkillInstaller().update(skill_name)

        @self.app.post(
            "/api/skills/{skill_name}/toggle", dependencies=[Depends(self.verify_auth)]
        )
        async def toggle_skill(skill_name: str, request: Request):
            """Enable or disable a skill. Triggers a restart to apply changes."""
            from core.skill_installer import SkillInstaller

            body = await request.json()
            installer = SkillInstaller()
            result = (
                installer.enable(skill_name)
                if body.get("enable")
                else installer.disable(skill_name)
            )
            if result.get("status") == "success":
                logger.info(
                    f"Skill '{skill_name}' toggled to {body.get('enable')}. Restarting..."
                )
                asyncio.get_running_loop().call_later(1.0, _spawn_restart)
            return result

        @self.app.post("/api/skill/{skill_name}/{action}")
        async def skill_api(skill_name: str, action: str, request: Request):
            from core.skills import SkillAPI, SkillRegistry

            try:
                data = await request.json()
            except Exception:
                data = {}
            if not hasattr(self, "_skill_api"):
                self._skill_registry = SkillRegistry(
                    skill_dirs=["./skills"], config={"skills": {"entries": {}}}
                )
                self._skill_registry.discover_and_load()
                self._skill_api = SkillAPI(
                    registry=self._skill_registry, bus=self.bus, channels=self.channels
                )
            return await self._skill_api.handle_request(
                skill_name=skill_name, action=action, data=data or {}
            )

        @self.app.post("/api/control/restart", dependencies=[Depends(self.verify_auth)])
        async def restart_backend():
            logger.warning("Restart requested via API...")

            async def _restart():
                await asyncio.sleep(1)

                os.execl(os.sys.executable, os.sys.executable, *os.sys.argv)

            asyncio.create_task(_restart())
            return {"status": "restarting", "message": "Backend is restarting..."}

        @self.app.post(
            "/api/control/clear-cache", dependencies=[Depends(self.verify_auth)]
        )
        async def clear_cache():
            try:
                if (
                    hasattr(self, "agent")
                    and self.agent
                    and hasattr(self.agent, "tool_cache")
                ):
                    self.agent.tool_cache.clear()
                    logger.info("Tool cache cleared via API.")
                    return {"status": "success", "message": "Cache cleared."}
                return {"status": "error", "message": "Cache not available."}
            except Exception as e:
                logger.error(f"Error clearing cache: {e}")
                return {"status": "error", "message": str(e)}

        @self.app.post(
            "/api/control/clear-logs", dependencies=[Depends(self.verify_auth)]
        )
        async def clear_logs():
            try:
                from pathlib import Path

                log_file = Path("logs/limebot.log")
                if log_file.exists():
                    log_file.write_text("")
                logger.info("Logs cleared via API.")
                return {"status": "success", "message": "Logs cleared."}
            except Exception as e:
                logger.error(f"Error clearing logs: {e}")
                return {"status": "error", "message": str(e)}

        @self.app.post(
            "/api/control/shutdown", dependencies=[Depends(self.verify_auth)]
        )
        async def shutdown_backend():
            import signal

            logger.warning("Shutdown requested via API...")

            async def _shutdown():
                await asyncio.sleep(0.5)
                if os.name == "nt":
                    os._exit(0)
                else:
                    os.kill(os.getpid(), signal.SIGINT)

            asyncio.create_task(_shutdown())
            return {"status": "shutting_down", "message": "Backend is shutting down..."}

        @self.app.post("/api/confirm-tool", dependencies=[Depends(self.verify_auth)])
        async def confirm_tool(data: dict):
            conf_id = data.get("conf_id")
            if not conf_id:
                raise HTTPException(status_code=400, detail="conf_id is required")
            if not hasattr(self, "agent"):
                raise HTTPException(status_code=500, detail="Agent not initialized")
            approved = data.get("approved", False)
            session_whitelist = data.get("session_whitelist", False)
            success = await self.agent.confirm_tool(
                conf_id, approved, session_whitelist
            )
            if success:
                return {
                    "status": "success",
                    "message": f"Tool {conf_id} {'approved' if approved else 'denied'}",
                }
            raise HTTPException(
                status_code=404, detail="Confirmation request not found or expired"
            )

        @self.app.post(
            "/api/chat/{chat_id}/stop", dependencies=[Depends(self.verify_auth)]
        )
        async def stop_generation(chat_id: str):
            if not hasattr(self, "agent") or not self.agent:
                raise HTTPException(status_code=503, detail="Agent not initialized")

            session_key = f"web_{chat_id}"
            for char in ["/", "\\", ":", "*", "?", '"', "<", ">", "|"]:
                session_key = session_key.replace(char, "_")

            success = await self.agent.cancel_session(session_key)
            if success:
                return {"status": "success", "message": "Stopped generation"}
            return {"status": "ignored", "message": "No active task found to stop"}

        @self.app.post(
            "/api/whatsapp/send_file", dependencies=[Depends(self.verify_auth)]
        )
        async def send_whatsapp_file_api(to: str, file_path: str, caption: str = None):
            from channels.whatsapp import WhatsAppChannel

            wa_channel = next(
                (c for c in self.channels if isinstance(c, WhatsAppChannel)), None
            )
            if not wa_channel:
                return {"status": "error", "message": "WhatsApp channel not active"}

            try:
                success = await wa_channel.send_file(to, file_path, caption)
                return (
                    {"status": "success", "message": "File sent"}
                    if success
                    else {"status": "error", "message": "Failed to send file"}
                )
            except Exception as e:
                logger.error(f"API send_file error: {e}")
                return {"status": "error", "message": str(e)}

        @self.app.get(
            "/api/whatsapp/contacts", dependencies=[Depends(self.verify_auth)]
        )
        async def get_whatsapp_contacts():
            return _load_contacts()

        @self.app.post(
            "/api/whatsapp/contacts/approve", dependencies=[Depends(self.verify_auth)]
        )
        async def approve_whatsapp_contact(request: Request):
            data = await request.json()
            chat_id = data.get("chat_id")
            if not chat_id:
                return {"status": "error", "message": "Missing chat_id"}
            contacts = _load_contacts()
            if chat_id in contacts.get("pending", []):
                contacts["pending"].remove(chat_id)
            if chat_id in contacts.get("blocked", []):
                contacts["blocked"].remove(chat_id)
            if chat_id not in contacts.get("allowed", []):
                contacts.setdefault("allowed", []).append(chat_id)
            _save_contacts(contacts)
            return {"status": "success", "contacts": contacts}

        @self.app.post(
            "/api/whatsapp/contacts/deny", dependencies=[Depends(self.verify_auth)]
        )
        async def deny_whatsapp_contact(request: Request):
            data = await request.json()
            chat_id = data.get("chat_id")
            if not chat_id:
                return {"status": "error", "message": "Missing chat_id"}
            contacts = _load_contacts()
            if chat_id in contacts.get("pending", []):
                contacts["pending"].remove(chat_id)
            if chat_id in contacts.get("allowed", []):
                contacts["allowed"].remove(chat_id)
            if chat_id not in contacts.get("blocked", []):
                contacts.setdefault("blocked", []).append(chat_id)
            _save_contacts(contacts)
            return {"status": "success", "contacts": contacts}

        @self.app.post(
            "/api/whatsapp/contacts/unallow", dependencies=[Depends(self.verify_auth)]
        )
        async def unallow_whatsapp_contact(request: Request):
            data = await request.json()
            chat_id = data.get("chat_id")
            if not chat_id:
                return {"status": "error", "message": "Missing chat_id"}
            contacts = _load_contacts()
            if chat_id in contacts.get("allowed", []):
                contacts["allowed"].remove(chat_id)
            if chat_id not in contacts.get("pending", []):
                contacts.setdefault("pending", []).append(chat_id)
            _save_contacts(contacts)
            return {"status": "success", "contacts": contacts}

        @self.app.post("/api/whatsapp/reset", dependencies=[Depends(self.verify_auth)])
        async def reset_whatsapp_session():
            from channels.whatsapp import WhatsAppChannel

            wa_channel = next(
                (c for c in self.channels if isinstance(c, WhatsAppChannel)), None
            )
            if not wa_channel:
                return {"status": "error", "message": "WhatsApp channel not active"}
            try:
                success = await wa_channel.reset_session()
                if success:
                    self._whatsapp_qr = None
                    return {
                        "status": "success",
                        "message": "WhatsApp session reset initiated. Check UI for new QR code.",
                    }
                return {"status": "error", "message": "Failed to reset session"}
            except Exception as e:
                logger.error(f"Error resetting WhatsApp session: {e}")
                return {"status": "error", "message": str(e)}

        @self.app.get("/api/cron/jobs", dependencies=[Depends(self.verify_auth)])
        async def get_cron_jobs():
            if not self.scheduler:
                return []
            return await self.scheduler.list_jobs()

        @self.app.post("/api/cron/jobs", dependencies=[Depends(self.verify_auth)])
        async def add_cron_job(data: dict):
            if not self.scheduler:
                raise HTTPException(status_code=503, detail="Scheduler not initialized")

            import re as _re

            time_expr = data.get("time_expr")
            cron_expr = data.get("cron_expr")
            message = data.get("message")

            if not message:
                raise HTTPException(status_code=400, detail="Missing message")
            if not time_expr and not cron_expr:
                raise HTTPException(
                    status_code=400, detail="Missing time_expr or cron_expr"
                )

            trigger_time = None
            if time_expr:
                multipliers = {"s": 1, "m": 60, "h": 3600, "d": 86400}
                suffix = time_expr[-1]
                if suffix not in multipliers:
                    raise HTTPException(
                        status_code=400,
                        detail="Invalid time format. Use '10s', '5m', '2h', '1d'.",
                    )
                match = _re.search(r"\d+", time_expr)
                if not match:
                    raise HTTPException(
                        status_code=400, detail="Could not parse time amount."
                    )
                trigger_time = time.time() + int(match.group()) * multipliers[suffix]

            context = data.get("context", {})
            if not context.get("channel"):
                context.update(
                    {"channel": "web", "chat_id": "manual_entry", "sender_id": "user"}
                )

            try:
                job_id = await self.scheduler.add_job(
                    trigger_time,
                    message,
                    context,
                    cron_expr=cron_expr,
                    tz_offset=data.get("tz_offset"),
                )
                return {
                    "status": "success",
                    "job_id": job_id,
                    "trigger_time": trigger_time,
                    "cron_expr": cron_expr,
                }
            except Exception as e:
                logger.error(f"Error adding job: {e}")
                raise HTTPException(status_code=500, detail=str(e))

        @self.app.delete(
            "/api/cron/jobs/{job_id}", dependencies=[Depends(self.verify_auth)]
        )
        async def delete_cron_job(job_id: str):
            if not self.scheduler:
                raise HTTPException(status_code=503, detail="Scheduler not initialized")
            success = await self.scheduler.remove_job(job_id)
            if success:
                return {"status": "success", "message": "Job deleted"}
            raise HTTPException(status_code=404, detail="Job not found")

        @self.app.websocket("/ws")
        async def websocket_root(websocket: WebSocket):
            await self._websocket_handler(websocket)

        @self.app.websocket("/ws/client")
        async def websocket_client(websocket: WebSocket):
            await self._websocket_handler(websocket)

    async def _websocket_handler(self, websocket: WebSocket) -> None:
        """Shared handler for all WebSocket connections."""
        internal_key = getattr(self.config.whitelist, "api_key", None)
        if internal_key:
            api_key = websocket.query_params.get("api_key")
            if api_key != internal_key:
                logger.warning(
                    f"WebSocket rejected: bad API key from {websocket.client}"
                )
                await websocket.close(code=status.WS_1008_POLICY_VIOLATION)
                return

        await websocket.accept()
        self.active_connections.add(websocket)
        logger.info(f"Web client connected ({websocket.url.path})")

        if self._whatsapp_qr:
            try:
                await websocket.send_text(
                    json.dumps(
                        {
                            "type": "whatsapp_qr",
                            "content": "WhatsApp QR Code",
                            "sender": "bot",
                            "chat_id": "system",
                            "metadata": {
                                "type": "whatsapp_qr",
                                "qr": self._whatsapp_qr,
                            },
                        }
                    )
                )
            except Exception as e:
                logger.error(f"Error sending cached QR: {e}")

        try:
            while True:
                data = await websocket.receive_text()
                try:
                    msg = json.loads(data)
                except json.JSONDecodeError:
                    logger.warning("Received invalid JSON from web client")
                    continue

                if msg.get("type") == "confirmation_response":
                    conf_id = msg.get("confirmation_id")
                    approved = msg.get("approved", False)
                    chat_id = msg.get("chat_id", "web-chat")
                    content = (
                        f"[CONFIRMATION_APPROVED:{conf_id}]"
                        if approved
                        else "User denied the action."
                    )
                    await self._handle_message(
                        sender_id="web-user",
                        chat_id=chat_id,
                        content=content,
                        metadata={"source": "web", "is_confirmation": True},
                    )
                    continue

                chat_id = msg.get("chat_id") or msg.get("sessionId") or "web-chat"
                content = msg.get("content", "")
                # Allow callers (e.g. the discord-voice skill) to supply their
                # own sender_id so each user gets a separate LimeBot profile.
                sender_id = msg.get("sender_id") or "web-user"
                sender_name = msg.get("sender_name") or ""

                metadata = {"source": "web"}
                if sender_name:
                    metadata["sender_name"] = sender_name
                if image_data := msg.get("image"):
                    metadata["image"] = image_data

                await self._handle_message(
                    sender_id=sender_id,
                    chat_id=chat_id,
                    content=content,
                    metadata=metadata,
                )

        except WebSocketDisconnect:
            pass
        finally:
            self.active_connections.discard(websocket)
            logger.info("Web client disconnected")

    async def start(self) -> None:
        try:
            config = uvicorn.Config(
                self.app, host="0.0.0.0", port=8000, log_level="info"
            )
            self.server = uvicorn.Server(config)
            logger.info("Web channel starting on port 8000")
            await self.server.serve()
        except Exception as e:
            logger.exception(f"CRITICAL: WebChannel failed to start: {e}")

    async def stop(self) -> None:
        if self.server:
            self.server.should_exit = True

    async def send(self, msg: OutboundMessage) -> None:
        if not self.active_connections:
            return

        metadata = msg.metadata or {}
        msg_type = metadata.get("type", "message")

        if msg_type == "whatsapp_qr":
            self._whatsapp_qr = metadata.get("qr")
            logger.info(
                f"Cached WhatsApp QR (len={len(self._whatsapp_qr) if self._whatsapp_qr else 0})"
            )
        elif msg_type == "whatsapp_status" and metadata.get("status") == "connected":
            if self._whatsapp_qr is not None:
                self._whatsapp_qr = None
                logger.info("Cleared WhatsApp QR cache (connected)")

        payload = json.dumps(
            {
                "type": msg_type,
                "content": msg.content,
                "sender": "bot",
                "chat_id": msg.chat_id,
                "metadata": metadata,
            }
        )

        dead: set[WebSocket] = set()

        async def _safe_send(conn: WebSocket):
            try:
                # Use a timeout to ensure a hung connection doesn't block the dispatch loop
                await asyncio.wait_for(conn.send_text(payload), timeout=2.0)
            except Exception:
                dead.add(conn)

        if self.active_connections:
            await asyncio.gather(
                *(_safe_send(c) for c in list(self.active_connections))
            )

        for conn in dead:
            self.active_connections.discard(conn)


def _spawn_restart() -> None:
    """Spawn a new process then exit — used by call_later callbacks."""
    import subprocess
    import sys

    subprocess.Popen([sys.executable] + sys.argv)
    os._exit(0)
