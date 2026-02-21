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

                if data.get("emojis"):
                    lines.append(f"*   **Reaction Emojis:** {data['emojis']}")

                identity_file.write_text("\n".join(lines), encoding="utf-8")

                if data.get("mood"):
                    mood_file.write_text(data["mood"], encoding="utf-8")

                if "enable_dynamic_personality" in data:
                    from config import load_config, save_config

                    config = load_config()
                    config.llm.enable_dynamic_personality = data[
                        "enable_dynamic_personality"
                    ]
                    save_config(config)

                return {"status": "ok"}
            except Exception as e:
                logger.error(f"Error updating persona: {e}")
                raise HTTPException(status_code=500, detail=str(e))

        @self.app.get("/api/logs", dependencies=[Depends(self.verify_auth)])
        async def get_logs(limit: int = 100):
            from pathlib import Path
            import re

            log_file = Path("logs/app.log")
            if not log_file.exists():
                return []
            try:
                logs = []
                with open(log_file, "r", encoding="utf-8") as f:
                    # Read from the end of file for efficiency with large logs
                    lines = f.readlines()
                    for line in reversed(lines):
                        if len(logs) >= limit:
                            break
                        # Parse standard log format
                        match = re.match(
                            r"(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}\.\d{3}) \| (\w+)\s+\| (.*)",
                            line,
                        )
                        if match:
                            logs.append(
                                {
                                    "timestamp": match.group(1),
                                    "level": match.group(2),
                                    "message": match.group(3),
                                }
                            )
                return logs
            except Exception as e:
                logger.error(f"Error reading logs: {e}")
                return []

        @self.app.get("/api/config", dependencies=[Depends(self.verify_auth)])
        async def get_config():
            try:
                from config import load_config
                from dataclasses import asdict

                cfg = load_config()
                config_dict = asdict(cfg)

                # Convert sets to lists for JSON serialization
                if "whitelist" in config_dict:
                    for field in ["users", "channels"]:
                        if isinstance(config_dict["whitelist"].get(field), set):
                            config_dict["whitelist"][field] = list(
                                config_dict["whitelist"][field]
                            )

                return config_dict
            except Exception as e:
                logger.error(f"Error getting config: {e}")
                raise HTTPException(status_code=500, detail=str(e))

        @self.app.post("/api/config", dependencies=[Depends(self.verify_auth)])
        async def update_config(data: dict):
            try:
                from config import load_config, save_config
                from core.config_definitions import (
                    AppConfig,
                    LLMConfig,
                    DiscordConfig,
                    WhatsappConfig,
                    WebConfig,
                    WhitelistConfig,
                )

                current_config = load_config()

                # Update LLM config
                if "llm" in data:
                    llm_data = data["llm"]
                    current_config.llm = LLMConfig(
                        provider=llm_data.get("provider", current_config.llm.provider),
                        model=llm_data.get("model", current_config.llm.model),
                        temperature=float(
                            llm_data.get("temperature", current_config.llm.temperature)
                        ),
                        max_tokens=int(
                            llm_data.get("max_tokens", current_config.llm.max_tokens)
                        ),
                        system_prompt_path=llm_data.get(
                            "system_prompt_path", current_config.llm.system_prompt_path
                        ),
                        enable_dynamic_personality=llm_data.get(
                            "enable_dynamic_personality",
                            current_config.llm.enable_dynamic_personality,
                        ),
                    )

                # Update Discord config
                if "discord" in data:
                    discord_data = data["discord"]
                    current_config.discord = DiscordConfig(
                        enabled=discord_data.get(
                            "enabled", current_config.discord.enabled
                        ),
                        token=discord_data.get("token", current_config.discord.token),
                    )

                # Update Whatsapp config
                if "whatsapp" in data:
                    whatsapp_data = data["whatsapp"]
                    current_config.whatsapp = WhatsappConfig(
                        enabled=whatsapp_data.get(
                            "enabled", current_config.whatsapp.enabled
                        ),
                        api_url=whatsapp_data.get(
                            "api_url", current_config.whatsapp.api_url
                        ),
                        session_id=whatsapp_data.get(
                            "session_id", current_config.whatsapp.session_id
                        ),
                    )

                # Update Web config
                if "web" in data:
                    web_data = data["web"]
                    current_config.web = WebConfig(
                        enabled=web_data.get("enabled", current_config.web.enabled),
                        host=web_data.get("host", current_config.web.host),
                        port=int(web_data.get("port", current_config.web.port)),
                    )

                # Update Whitelist config
                if "whitelist" in data:
                    whitelist_data = data["whitelist"]
                    current_config.whitelist = WhitelistConfig(
                        enabled=whitelist_data.get(
                            "enabled", current_config.whitelist.enabled
                        ),
                        users=set(
                            whitelist_data.get("users", current_config.whitelist.users)
                        ),
                        channels=set(
                            whitelist_data.get(
                                "channels", current_config.whitelist.channels
                            )
                        ),
                        api_key=whitelist_data.get(
                            "api_key", current_config.whitelist.api_key
                        ),
                        allow_unsafe_scripts=whitelist_data.get(
                            "allow_unsafe_scripts",
                            current_config.whitelist.allow_unsafe_scripts,
                        ),
                    )

                save_config(current_config)
                return {"status": "ok", "message": "Configuration updated successfully"}
            except Exception as e:
                logger.error(f"Error updating config: {e}")
                raise HTTPException(status_code=500, detail=str(e))

        @self.app.get("/api/providers/models", dependencies=[Depends(self.verify_auth)])
        async def get_provider_models(provider: str):
            """Fetch available models for a given provider using litellm."""
            try:
                # 1. Check cache first
                cache_key = provider.lower()
                now = time.time()
                if (
                    cache_key in self._provider_models_cache
                    and (now - self._provider_models_last_update.get(cache_key, 0))
                    < 3600  # 1 hour cache
                ):
                    return {"models": self._provider_models_cache[cache_key]}

                # 2. Fetch from litellm (this is tricky as litellm doesn't have a unified "list models" API for all providers without keys)
                # However, for the purpose of the UI settings, we might just return a static curated list
                # or try to probe if possible.
                # For now, we will return a curated list of popular models per provider to ensure UI responsiveness.

                models = []
                if provider == "openai":
                    models = [
                        "gpt-4o",
                        "gpt-4-turbo",
                        "gpt-4",
                        "gpt-3.5-turbo",
                        "gpt-4o-mini",
                    ]
                elif provider == "anthropic":
                    models = [
                        "claude-3-5-sonnet-20240620",
                        "claude-3-opus-20240229",
                        "claude-3-sonnet-20240229",
                        "claude-3-haiku-20240307",
                    ]
                elif provider == "gemini":
                    models = [
                        "gemini/gemini-1.5-pro",
                        "gemini/gemini-1.5-flash",
                        "gemini/gemini-pro",
                    ]
                elif provider == "groq":
                    models = [
                        "groq/llama3-8b-8192",
                        "groq/llama3-70b-8192",
                        "groq/mixtral-8x7b-32768",
                    ]
                elif provider == "ollama":
                    # For Ollama, we can actually try to hit the local endpoint if it's running
                    try:
                        import requests

                        resp = requests.get("http://localhost:11434/api/tags", timeout=1)
                        if resp.status_code == 200:
                            data = resp.json()
                            models = [
                                f"ollama/{m['name']}" for m in data.get("models", [])
                            ]
                    except:
                        models = ["ollama/llama3", "ollama/mistral"]  # Fallbacks

                else:
                    models = ["gpt-3.5-turbo"]  # Default fallback

                # Update cache
                self._provider_models_cache[cache_key] = models
                self._provider_models_last_update[cache_key] = now

                return {"models": models}

            except Exception as e:
                logger.error(f"Error fetching models for {provider}: {e}")
                return {"models": []}

        @self.app.websocket("/ws/{client_id}")
        async def websocket_endpoint(websocket: WebSocket, client_id: str):
            await self.session_manager.connect(websocket, client_id)
            try:
                # Send initial connection success message
                await self.session_manager.send_json(
                    client_id,
                    {
                        "type": "system",
                        "content": "Connected to LimeBot",
                        "timestamp": time.time(),
                    },
                )

                # Send history
                history = self.session_manager.get_history(client_id)
                for msg in history:
                    await self.session_manager.send_json(client_id, msg)

                while True:
                    data = await websocket.receive_text()
                    try:
                        payload = json.loads(data)
                        message_type = payload.get("type")
                        content = payload.get("content")

                        if message_type == "ping":
                            await self.session_manager.send_json(
                                client_id, {"type": "pong", "timestamp": time.time()}
                            )
                            continue

                        if message_type == "message":
                            # Use current persona info
                            from core.prompt import get_identity_data

                            identity = get_identity_data()

                            # 1. User message (echo back immediately for UI optimism)
                            user_msg_obj = {
                                "role": "user",
                                "content": content,
                                "timestamp": time.time(),
                                "type": "message",
                            }
                            await self.session_manager.send_json(
                                client_id, user_msg_obj
                            )
                            self.session_manager.add_to_history(client_id, user_msg_obj)

                            # 2. Show typing indicator
                            await self.session_manager.send_json(
                                client_id,
                                {
                                    "type": "typing",
                                    "status": "start",
                                    "sender": identity.get("name", "LimeBot"),
                                },
                            )

                            # 3. Publish to bus
                            # Include the client_id in the metadata so we know where to reply
                            event = OutboundMessage(
                                id=f"web-{client_id}-{int(time.time())}",
                                source="web",
                                content=content,
                                sender_id=client_id,  # Important: treat client_id as the user ID
                                sender_name="User",  # You could enhance this with a display name
                                metadata={"client_id": client_id},
                            )
                            await self.bus.publish(event)

                    except json.JSONDecodeError:
                        logger.error("Invalid JSON received")

            except WebSocketDisconnect:
                self.session_manager.disconnect(client_id)
            except Exception as e:
                logger.error(f"WebSocket error: {e}")
                self.session_manager.disconnect(client_id)

    async def start(self):
        config = uvicorn.Config(
            self.app,
            host=self.config.web.host,
            port=self.config.web.port,
            log_level="info",
        )
        self.server = uvicorn.Server(config)
        logger.info(
            f"Starting Web Channel on {self.config.web.host}:{self.config.web.port}"
        )
        await self.server.serve()

    async def stop(self):
        if self.server:
            self.server.should_exit = True

    async def send(self, message: str, recipient_id: str = None) -> None:
        """
        Send a message to a specific web client.
        """
        # In the web channel, recipient_id should match the client_id
        if recipient_id:
            from core.prompt import get_identity_data

            identity = get_identity_data()

            # Stop typing indicator
            await self.session_manager.send_json(
                recipient_id,
                {
                    "type": "typing",
                    "status": "stop",
                    "sender": identity.get("name", "LimeBot"),
                },
            )

            # Send the actual response
            msg_obj = {
                "role": "assistant",
                "content": message,
                "timestamp": time.time(),
                "type": "message",
                "sender": identity.get("name", "LimeBot"),
                "avatar": identity.get("pfp_url", ""),
            }
            await self.session_manager.send_json(recipient_id, msg_obj)
            self.session_manager.add_to_history(recipient_id, msg_obj)
        else:
            logger.warning("WebChannel.send called without recipient_id")
