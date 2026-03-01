"""Web channel implementation using FastAPI and WebSockets."""

import asyncio
import json
import os
import socket
import time
from typing import Any, Optional

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

    def __init__(
        self,
        config: Any,
        bus: MessageBus,
        session_manager: Optional[SessionManager] = None,
    ):
        super().__init__(config, bus)
        self.app = FastAPI()
        self.server = None
        self.session_manager = session_manager or SessionManager()

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
        self.actual_port = getattr(self.config.web, "port", 8000)
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
                lines.append(f"*   **Name:** {data.get('name', '')}")
                lines.append(f"*   **Emoji:** {data.get('emoji', '')}")
                lines.append(f"*   **Pfp_URL:** {data.get('pfp_url', '')}")
                lines.append(f"*   **Style:** {data.get('style', '')}")
                lines.append(f"*   **Catchphrases:** {data.get('catchphrases', '')}")
                lines.append(f"*   **Interests:** {data.get('interests', '')}")
                lines.append(f"*   **Birthday:** {data.get('birthday', '')}")
                lines.append(f"*   **Discord Style:** {data.get('discord_style', '')}")
                lines.append(
                    f"*   **WhatsApp Style:** {data.get('whatsapp_style', '')}"
                )
                lines.append(f"*   **Web Style:** {data.get('web_style', '')}")
                lines.append(
                    f"*   **Reaction Emojis:** {data.get('reaction_emojis', '')}"
                )
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

        @self.app.post(
            "/api/instances/delete-batch", dependencies=[Depends(self.verify_auth)]
        )
        async def delete_instances_batch(request: Request):
            data = await request.json()
            ids = data.get("ids", [])
            if not ids:
                return {"status": "success", "deleted": 0}

            count = await self.session_manager.delete_sessions(ids)
            return {
                "status": "success",
                "message": f"Deleted {count} instances",
                "deleted": count,
            }

        @self.app.get("/api/sessions", dependencies=[Depends(self.verify_auth)])
        async def get_sessions():
            return await get_instances()

        @self.app.get("/api/llm/models")
        async def get_llm_models():
            models = [
                # ── Google Gemini ─────────────────────────────────────────────
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
                    "id": "gemini/gemini-2.5-flash-lite-preview-06-17",
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
                # ── OpenAI ────────────────────────────────────────────────────
                {
                    "id": "openai/gpt-4o",
                    "name": "GPT-4o",
                    "provider": "openai",
                },
                {
                    "id": "openai/gpt-4o-mini",
                    "name": "GPT-4o Mini",
                    "provider": "openai",
                },
                {
                    "id": "openai/o3-mini",
                    "name": "o3-mini",
                    "provider": "openai",
                },
                {
                    "id": "openai/o1",
                    "name": "o1",
                    "provider": "openai",
                },
                # ── Anthropic ─────────────────────────────────────────────────
                {
                    "id": "anthropic/claude-3-7-sonnet-20250219",
                    "name": "Claude 3.7 Sonnet",
                    "provider": "anthropic",
                },
                {
                    "id": "anthropic/claude-3-5-sonnet-20241022",
                    "name": "Claude 3.5 Sonnet",
                    "provider": "anthropic",
                },
                {
                    "id": "anthropic/claude-3-5-haiku-20241022",
                    "name": "Claude 3.5 Haiku",
                    "provider": "anthropic",
                },
                {
                    "id": "anthropic/claude-3-opus-20240229",
                    "name": "Claude 3 Opus",
                    "provider": "anthropic",
                },
                # ── xAI Grok ─────────────────────────────────────────────────
                {
                    "id": "xai/grok-4",
                    "name": "Grok 4",
                    "provider": "xai",
                },
                {
                    "id": "xai/grok-4-fast-reasoning",
                    "name": "Grok 4 Fast (Reasoning)",
                    "provider": "xai",
                },
                {
                    "id": "xai/grok-3",
                    "name": "Grok 3",
                    "provider": "xai",
                },
                {
                    "id": "xai/grok-3-mini",
                    "name": "Grok 3 Mini",
                    "provider": "xai",
                },
                {
                    "id": "xai/grok-2-1212",
                    "name": "Grok 2",
                    "provider": "xai",
                },
                # ── DeepSeek ──────────────────────────────────────────────────
                {
                    "id": "deepseek/deepseek-v3.2",
                    "name": "DeepSeek V3.2",
                    "provider": "deepseek",
                },
                {
                    "id": "deepseek/deepseek-chat",
                    "name": "DeepSeek V3",
                    "provider": "deepseek",
                },
                {
                    "id": "deepseek/deepseek-reasoner",
                    "name": "DeepSeek R1",
                    "provider": "deepseek",
                },
                {
                    "id": "qwen/qwen-plus",
                    "name": "Qwen Plus",
                    "provider": "qwen",
                },
                {
                    "id": "qwen/qwen-max",
                    "name": "Qwen Max",
                    "provider": "qwen",
                },
                {
                    "id": "qwen/qwen-flash",
                    "name": "Qwen Flash",
                    "provider": "qwen",
                },
                # ── NVIDIA NIM (static fallbacks — dynamic list fetched below) ─
                {
                    "id": "nvidia/gpt-oss/120b",
                    "name": "GPT-OSS 120B",
                    "provider": "nvidia",
                },
                {
                    "id": "nvidia/gpt-oss/20b",
                    "name": "GPT-OSS 20B",
                    "provider": "nvidia",
                },
                {
                    "id": "nvidia/glm/4.7",
                    "name": "GLM 4.7",
                    "provider": "nvidia",
                },
                {
                    "id": "nvidia/moonshotai/kimi-k2.5",
                    "name": "Kimi K2.5",
                    "provider": "nvidia",
                },
                {
                    "id": "nvidia/llama/4-scout",
                    "name": "Llama 4 Scout",
                    "provider": "nvidia",
                },
                {
                    "id": "nvidia/llama/4-maverick",
                    "name": "Llama 4 Maverick",
                    "provider": "nvidia",
                },
                {
                    "id": "nvidia/qwen/3-30b-a3b-instruct",
                    "name": "Qwen 3 30B",
                    "provider": "nvidia",
                },
                {
                    "id": "nvidia/meta/llama-3.1-405b-instruct",
                    "name": "Llama 3.1 405B Instruct",
                    "provider": "nvidia",
                },
                {
                    "id": "nvidia/meta/llama-3.3-70b-instruct",
                    "name": "Llama 3.3 70B Instruct",
                    "provider": "nvidia",
                },
                {
                    "id": "nvidia/mistralai/mixtral-8x22b-instruct-v0.1",
                    "name": "Mixtral 8x22B Instruct",
                    "provider": "nvidia",
                },
                {
                    "id": "nvidia/mistralai/mistral-large-2-instruct",
                    "name": "Mistral Large 2",
                    "provider": "nvidia",
                },
                {
                    "id": "nvidia/ministral/14b-reasoning",
                    "name": "Ministral 14B",
                    "provider": "nvidia",
                },
                {
                    "id": "nvidia/qwen/qwen2.5-72b-instruct",
                    "name": "Qwen 2.5 72B Instruct",
                    "provider": "nvidia",
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
                "qwen": os.getenv("DASHSCOPE_API_KEY"),
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
            if api_keys["qwen"]:
                await update_provider_cache(
                    "qwen",
                    fetch_openai_compatible_models,
                    api_keys["qwen"],
                    os.getenv("LLM_BASE_URL")
                    or os.getenv("DASHSCOPE_BASE_URL")
                    or "https://dashscope-intl.aliyuncs.com/compatible-mode/v1",
                    "qwen",
                    True,
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
                from core.llm_utils import resolve_provider_config

                p_cfg = resolve_provider_config(
                    model, default_base_url=self.config.llm.base_url
                )

                await asyncio.to_thread(
                    completion,
                    model=p_cfg["model"],
                    messages=[{"role": "user", "content": "hi"}],
                    max_tokens=5,
                    api_key=p_cfg["api_key"],
                    base_url=p_cfg["base_url"],
                    custom_llm_provider=p_cfg["custom_llm_provider"],
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
                "GEMINI_API_KEY": os.getenv("GEMINI_API_KEY", ""),
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
                "ALLOW_UNSAFE_COMMANDS": str(
                    getattr(cfg, "allow_unsafe_commands", False)
                ).lower(),
                "WEB_PORT": str(getattr(cfg.web, "port", 8000)),
                "LLM_PROXY_URL": getattr(cfg.llm, "proxy_url", ""),
            }
            for key in [
                "OPENAI_API_KEY",
                "ANTHROPIC_API_KEY",
                "XAI_API_KEY",
                "DEEPSEEK_API_KEY",
                "NVIDIA_API_KEY",
                "DASHSCOPE_API_KEY",
                "LLM_BASE_URL",
                "LLM_PROXY_URL",
            ]:
                val = os.getenv(key)
                if val:
                    env_dict[key] = val
            return {"env": env_dict}

        @self.app.get("/api/discord/config", dependencies=[Depends(self.verify_auth)])
        async def get_discord_config():
            from pathlib import Path

            cfg_path = Path("limebot.json")
            if not cfg_path.exists():
                return {"discord": {}}
            try:
                data = json.loads(cfg_path.read_text(encoding="utf-8"))
            except Exception as e:
                logger.error(f"Error reading limebot.json: {e}")
                return {"discord": {}}
            return {"discord": data.get("discord", {})}

        @self.app.post("/api/discord/config", dependencies=[Depends(self.verify_auth)])
        async def update_discord_config(payload: dict):
            from pathlib import Path

            cfg_path = Path("limebot.json")
            try:
                data = {}
                if cfg_path.exists():
                    data = json.loads(cfg_path.read_text(encoding="utf-8"))
                discord_cfg = payload.get("discord")
                if not isinstance(discord_cfg, dict):
                    return {"error": "discord config must be an object"}
                data["discord"] = discord_cfg
                cfg_path.write_text(json.dumps(data, indent=2), encoding="utf-8")
                logger.info("Discord UI config saved. Restarting...")
                asyncio.get_running_loop().call_later(1.0, _spawn_restart)
                return {
                    "status": "updated",
                    "message": "Discord configuration saved. Restarting...",
                }
            except Exception as e:
                logger.error(f"Error saving discord config: {e}")
                return {"error": str(e)}

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

        @self.app.get("/api/mcp/config", dependencies=[Depends(self.verify_auth)])
        async def get_mcp_config():
            from core.mcp_client import CONFIG_PATH
            import json

            if not CONFIG_PATH.exists():
                return {"mcpServers": {}}
            return json.loads(CONFIG_PATH.read_text(encoding="utf-8"))

        @self.app.post("/api/mcp/config", dependencies=[Depends(self.verify_auth)])
        async def update_mcp_config(data: dict):
            from core.mcp_client import (
                CONFIG_PATH,
                get_mcp_manager,
                validate_mcp_config,
            )
            import json

            try:
                ok, err = validate_mcp_config(data)
                if not ok:
                    return {"error": err}
                CONFIG_PATH.write_text(json.dumps(data, indent=2), encoding="utf-8")
                # Re-initialize MCP manager to apply changes
                mcp_manager = get_mcp_manager()
                asyncio.create_task(mcp_manager.initialize())
                return {"status": "success", "message": "MCP configuration updated"}
            except Exception as e:
                logger.error(f"Error updating MCP config: {e}")
                return {"error": str(e)}

        @self.app.get("/api/mcp/status", dependencies=[Depends(self.verify_auth)])
        async def get_mcp_status():
            from core.mcp_client import get_mcp_manager

            manager = get_mcp_manager()
            return {"status": manager.get_status()}

        @self.app.get("/api/setup/status")
        async def get_setup_status():
            try:
                from config import load_config
                from core.llm_utils import get_api_key_for_model

                cfg = load_config()
                model = cfg.llm.model
                api_key = get_api_key_for_model(model)

                is_local = (
                    model
                    and ("ollama" in model or "local" in model)
                ) or cfg.llm.base_url

                missing = []
                if not model:
                    missing.append("LLM_MODEL")
                if not api_key and not is_local:
                    missing.append("API_KEY")

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
                "gateway_url": f"ws://localhost:{self.actual_port}/ws",
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
                # Fallback mode: expose line-level memories from persona journals
                # so users can still inspect long-term context without vectors.
                from pathlib import Path
                import hashlib

                try:
                    memory_dir = Path("persona/memory")
                    if not memory_dir.exists():
                        memory_dir = Path(__file__).parent.parent / "persona" / "memory"

                    fallback_memories = []
                    if memory_dir.exists():
                        for file_path in sorted(memory_dir.glob("*.md"), reverse=True):
                            try:
                                content = file_path.read_text(encoding="utf-8")
                            except Exception:
                                continue

                            for line_no, raw_line in enumerate(content.splitlines(), start=1):
                                line = raw_line.strip()
                                if not line or line.startswith("#"):
                                    continue
                                line = line.lstrip("- ").strip()
                                if len(line) < 3:
                                    continue

                                mem_id = hashlib.sha1(
                                    f"{file_path.name}:{line_no}:{line}".encode("utf-8")
                                ).hexdigest()[:16]
                                fallback_memories.append(
                                    {
                                        "id": mem_id,
                                        "text": line,
                                        "category": "Journal",
                                        "timestamp": file_path.stem,
                                        "path": file_path.name,
                                        "source": file_path.name,
                                    }
                                )

                    return {
                        "enabled": False,
                        "mode": "grep_fallback",
                        "read_only": True,
                        "notice": "Using grep as fallback.",
                        "memories": fallback_memories[:500],
                    }
                except Exception as e:
                    logger.error(f"Error reading fallback memory: {e}")
                    return {
                        "enabled": False,
                        "mode": "grep_fallback",
                        "read_only": True,
                        "notice": "Using grep as fallback.",
                        "error": str(e),
                        "memories": [],
                    }

            try:
                memories = await vector_service.get_all(limit=100)
                return {
                    "enabled": True,
                    "mode": "vector",
                    "read_only": False,
                    "memories": memories,
                }
            except Exception as e:
                logger.error(f"Error reading memory: {e}")
                return {
                    "enabled": True,
                    "mode": "vector",
                    "read_only": False,
                    "error": str(e),
                    "memories": [],
                }

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

        @self.app.post("/api/notify", dependencies=[Depends(self.verify_auth)])
        async def notify(request: Request):
            """
            Send a notification to one or more channels (web/discord).
            Payload:
              - channels: "web" | "discord" | ["web","discord"]
              - content: message text
              - web_chat_id: optional (default "system")
              - discord_channel_ids: optional list or single id
              - kind: optional string (e.g., "github_pr")
              - data: optional object for structured notifications
            """
            data = await request.json()
            channels = data.get("channels") or []
            if isinstance(channels, str):
                channels = [channels]
            content = data.get("content", "").strip()
            if not content:
                raise HTTPException(status_code=400, detail="content is required")

            web_chat_id = data.get("web_chat_id", "system")
            discord_ids = data.get("discord_channel_ids") or []
            if isinstance(discord_ids, str):
                discord_ids = [discord_ids]

            meta = {
                "type": "notification",
                "kind": data.get("kind"),
                "data": data.get("data"),
            }

            if "web" in channels:
                await self.bus.publish_outbound(
                    OutboundMessage(
                        channel="web",
                        chat_id=web_chat_id,
                        content=content,
                        metadata=meta,
                    )
                )

            if "discord" in channels and discord_ids:
                for chan_id in discord_ids:
                    await self.bus.publish_outbound(
                        OutboundMessage(
                            channel="discord",
                            chat_id=str(chan_id),
                            content=content,
                            metadata=meta,
                        )
                    )

            return {"status": "success", "channels": channels}

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
                os.environ["LIMEBOT_SOFT_RESTART"] = "1"
                os.execl(os.sys.executable, os.sys.executable, *os.sys.argv)

            asyncio.create_task(_restart())
            return {"status": "restarting", "message": "Backend is restarting..."}

        @self.app.post(
            "/api/control/clear-cache", dependencies=[Depends(self.verify_auth)]
        )
        async def clear_cache():
            try:
                cleared = []
                if hasattr(self, "_provider_models_cache"):
                    self._provider_models_cache.clear()
                    self._provider_models_last_update.clear()
                    cleared.append("provider_models")

                if hasattr(self, "agent") and self.agent:
                    if hasattr(self.agent, "tool_cache"):
                        self.agent.tool_cache.clear()
                        cleared.append("tool_cache")
                    if hasattr(self.agent, "_stable_prompt_cache"):
                        self.agent._stable_prompt_cache.clear()
                        cleared.append("stable_prompt_cache")
                    if (
                        hasattr(self.agent, "vector_service")
                        and self.agent.vector_service
                    ):
                        if hasattr(self.agent.vector_service, "_emb_cache"):
                            self.agent.vector_service._emb_cache.clear()
                            cleared.append("embedding_cache")
                        if hasattr(self.agent.vector_service, "_grep_cache"):
                            self.agent.vector_service._grep_cache.clear()
                            cleared.append("grep_cache")

                if not cleared:
                    return {"status": "error", "message": "No cache sources available."}

                logger.info(f"Caches cleared via API: {', '.join(cleared)}")
                return {
                    "status": "success",
                    "message": f"Cleared: {', '.join(cleared)}",
                }
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
                # Allow callers (e.g. specialized skills) to supply their
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

        # Safely get port from config, defaulting to 8000
        web_config = getattr(self.config, "web", None)
        base_port = getattr(web_config, "port", 8000) if web_config else 8000

        max_retries = 10

        for port_offset in range(max_retries):
            current_port = base_port + port_offset
            try:
                # First try to see if we can bind a socket to this port
                with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
                    s.bind(("0.0.0.0", current_port))

                # If we got here, the port is likely available
                config = uvicorn.Config(
                    self.app, host="0.0.0.0", port=current_port, log_level="info"
                )
                self.server = uvicorn.Server(config)
                self.actual_port = current_port
                logger.info(f"Web channel starting on port {current_port}")

                try:
                    await self.server.serve()
                    return
                except (OSError, SystemExit) as e:
                    if isinstance(e, SystemExit) and e.code != 0:
                        logger.warning(
                            f"Uvicorn failed to start on port {current_port}, likely bind conflict."
                        )
                    else:
                        logger.warning(f"OS error on port {current_port}: {e}")

                    if port_offset < max_retries - 1:
                        logger.warning("Retrying next port...")
                        continue
                    else:
                        raise

            except OSError:
                if port_offset < max_retries - 1:
                    logger.warning(
                        f"Port {current_port} is in use (socket bind failed), trying {current_port + 1}..."
                    )
                    continue
                else:
                    logger.error(
                        f"Failed to find an available port after {max_retries} attempts."
                    )
                    raise
            except Exception as e:
                logger.exception(f"CRITICAL: WebChannel failed to start: {e}")
                break

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

    os.environ["LIMEBOT_SOFT_RESTART"] = "1"
    subprocess.Popen([sys.executable] + sys.argv)
    os._exit(0)
