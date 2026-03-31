"""Web channel implementation using FastAPI and WebSockets."""

import asyncio
import base64
import binascii
import json
import os
import re
import socket
import time
from typing import Any, Optional
from urllib.parse import unquote_to_bytes

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
from core.tools import Toolbox

_CONTACTS_PATH_REL = ("data", "contacts.json")
_MAX_WEB_ATTACHMENT_BYTES = 8 * 1024 * 1024
_DOCUMENT_EXTENSIONS = frozenset({".pdf", ".doc", ".docx"})
_DOCUMENT_MIME_TYPES = frozenset(
    {
        "application/pdf",
        "application/msword",
        "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    }
)


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
        self.allowed_origins = self._get_allowed_origins()

        async def verify_api_key(request: Request, x_api_key: str = Header(None)):
            if not self._is_auth_required():
                return True
            internal_key = getattr(self.config.whitelist, "api_key", None)
            if x_api_key != internal_key:
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

    def _is_auth_required(self) -> bool:
        internal_key = getattr(self.config.whitelist, "api_key", None)
        if not internal_key:
            return False

        try:
            from core.prompt import is_setup_complete

            return bool(is_setup_complete())
        except Exception as e:
            logger.warning(f"Error checking setup state for auth gate: {e}")
            return False

    def _get_allowed_origins(self) -> list[str]:
        origins = getattr(self.config.web, "allowed_origins", None) or []
        return [origin for origin in origins if origin != "*"]

    @staticmethod
    async def _read_text(path, encoding: str = "utf-8") -> str:
        return await asyncio.to_thread(path.read_text, encoding=encoding)

    @staticmethod
    async def _write_text(path, content: str, encoding: str = "utf-8") -> None:
        await asyncio.to_thread(path.write_text, content, encoding=encoding)

    @staticmethod
    async def _read_json(path, default: Any = None) -> Any:
        if not path.exists():
            return default
        raw = await asyncio.to_thread(path.read_text, encoding="utf-8")
        return json.loads(raw)

    @staticmethod
    async def _write_json(path, data: Any) -> None:
        payload = await asyncio.to_thread(json.dumps, data, indent=2)
        await asyncio.to_thread(path.write_text, payload, encoding="utf-8")

    @staticmethod
    async def _tail_log_file(path, lines: int) -> list[str]:
        def _read_tail() -> list[str]:
            chunk_size = 8192
            result_lines: list[str] = []
            with path.open("rb") as f:
                f.seek(0, 2)
                remaining = f.tell()
                buffer = b""
                while remaining > 0 and len(result_lines) <= lines:
                    read_size = min(chunk_size, remaining)
                    remaining -= read_size
                    f.seek(remaining)
                    buffer = f.read(read_size) + buffer
                    result_lines = buffer.decode("utf-8", errors="replace").splitlines()
            return result_lines[-lines:]

        return await asyncio.to_thread(_read_tail)

    @staticmethod
    async def _load_contacts_async() -> dict:
        return await asyncio.to_thread(_load_contacts)

    @staticmethod
    async def _save_contacts_async(contacts: dict) -> None:
        await asyncio.to_thread(_save_contacts, contacts)

    @staticmethod
    def _sanitize_upload_component(value: str) -> str:
        safe = re.sub(r"[^A-Za-z0-9._-]+", "_", str(value or "upload")).strip("._")
        return safe or "upload"

    @staticmethod
    def _decode_data_url(data_url: str) -> tuple[str, bytes]:
        if not isinstance(data_url, str) or not data_url.startswith("data:"):
            raise ValueError("Attachment payload must be a valid data URL.")
        if "," not in data_url:
            raise ValueError("Attachment payload is malformed.")

        header, payload = data_url.split(",", 1)
        mime_type = header[5:].split(";", 1)[0].strip().lower()
        try:
            if ";base64" in header.lower():
                content = base64.b64decode(payload, validate=True)
            else:
                content = unquote_to_bytes(payload)
        except (binascii.Error, ValueError) as e:
            raise ValueError("Attachment payload could not be decoded.") from e

        return mime_type or "application/octet-stream", content

    @staticmethod
    def _extract_web_document_text(path) -> tuple[str, str | None]:
        suffix = path.suffix.lower()
        if suffix == ".doc":
            return (
                "",
                "Legacy .doc files are stored, but automatic text extraction is only available for .docx and .pdf.",
            )

        try:
            if suffix == ".docx":
                extracted = Toolbox._extract_docx_text(path)
            elif suffix == ".pdf":
                extracted = Toolbox._extract_pdf_text(path)
            else:
                return "", None
            return Toolbox._slice_text_for_read(extracted, max_chars=12_000), None
        except Exception as e:
            return "", str(e)

    async def _normalize_web_attachments(
        self, chat_id: str, raw_attachments: Any
    ) -> tuple[list[dict[str, Any]], str | None]:
        if not isinstance(raw_attachments, list):
            return [], None

        from pathlib import Path

        safe_chat_id = self._sanitize_upload_component(chat_id)
        temp_dir = (Path.cwd() / "temp").resolve()
        upload_dir = temp_dir / "web_uploads" / safe_chat_id
        await asyncio.to_thread(upload_dir.mkdir, parents=True, exist_ok=True)

        attachments: list[dict[str, Any]] = []
        first_image_data_url: str | None = None

        for index, item in enumerate(raw_attachments[:4]):
            if not isinstance(item, dict):
                continue

            data_url = item.get("data_url") or item.get("url")
            if not data_url:
                continue

            mime_type, blob = self._decode_data_url(str(data_url))
            provided_mime = (
                str(item.get("mimeType") or item.get("mime_type") or item.get("type") or "")
                .strip()
                .lower()
            )
            if provided_mime:
                mime_type = provided_mime

            if len(blob) > _MAX_WEB_ATTACHMENT_BYTES:
                raise ValueError("Attachments are limited to 8 MB.")

            original_name = str(item.get("name") or f"attachment-{index + 1}").strip()
            original_name = Path(original_name).name or f"attachment-{index + 1}"
            suffix = Path(original_name).suffix.lower()
            is_image = mime_type.startswith("image/")
            is_document = mime_type in _DOCUMENT_MIME_TYPES or suffix in _DOCUMENT_EXTENSIONS

            if not is_image and not is_document:
                raise ValueError("Only images, PDF, DOC, and DOCX files are supported in web chat.")

            if not suffix:
                if mime_type == "application/pdf":
                    suffix = ".pdf"
                elif mime_type == "application/msword":
                    suffix = ".doc"
                elif (
                    mime_type
                    == "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
                ):
                    suffix = ".docx"
                elif is_image:
                    suffix = ".png"

            stem = self._sanitize_upload_component(Path(original_name).stem)
            stored_name = f"{int(time.time() * 1000)}_{index}_{stem}{suffix}"
            saved_path = upload_dir / stored_name
            await asyncio.to_thread(saved_path.write_bytes, blob)

            relative_path = saved_path.relative_to(Path.cwd()).as_posix()
            public_url = f"/temp/{saved_path.relative_to(temp_dir).as_posix()}"
            attachment: dict[str, Any] = {
                "name": original_name,
                "kind": "image" if is_image else "document",
                "mime_type": mime_type,
                "mimeType": mime_type,
                "path": relative_path,
                "url": public_url,
            }

            if is_image:
                first_image_data_url = first_image_data_url or str(data_url)
            else:
                extracted_text, extraction_note = self._extract_web_document_text(
                    saved_path
                )
                if extracted_text:
                    attachment["extracted_text"] = extracted_text
                if extraction_note:
                    attachment["extraction_note"] = extraction_note

            attachments.append(attachment)

        return attachments, first_image_data_url

    @staticmethod
    async def _close_websocket_safely(
        websocket: WebSocket, code: int = status.WS_1008_POLICY_VIOLATION
    ) -> None:
        try:
            await websocket.close(code=code)
        except RuntimeError:
            pass
        except Exception as e:
            logger.debug(f"WebSocket close skipped: {e}")

    async def _authenticate_websocket(self, websocket: WebSocket) -> bool:
        if not self._is_auth_required():
            return True
        internal_key = getattr(self.config.whitelist, "api_key", None)

        async def _send_auth_ok() -> bool:
            try:
                await websocket.send_text(json.dumps({"type": "auth_ok"}))
                return True
            except WebSocketDisconnect:
                logger.info(f"WebSocket disconnected during auth from {websocket.client}")
                return False
            except RuntimeError as e:
                logger.info(
                    f"WebSocket closed before auth completed from {websocket.client}: {e}"
                )
                return False

        header_key = websocket.headers.get("x-api-key")
        if header_key == internal_key:
            return await _send_auth_ok()

        query_key = websocket.query_params.get("api_key")
        if query_key == internal_key:
            return await _send_auth_ok()

        try:
            auth_frame = await asyncio.wait_for(websocket.receive_text(), timeout=5)
            payload = json.loads(auth_frame)
        except asyncio.TimeoutError:
            logger.warning(f"WebSocket rejected: auth timeout from {websocket.client}")
            await self._close_websocket_safely(websocket)
            return False
        except WebSocketDisconnect:
            logger.info(f"WebSocket disconnected before auth from {websocket.client}")
            return False
        except json.JSONDecodeError:
            logger.warning(f"WebSocket rejected: invalid auth payload from {websocket.client}")
            await self._close_websocket_safely(websocket)
            return False
        except Exception:
            logger.warning(f"WebSocket rejected: invalid auth payload from {websocket.client}")
            await self._close_websocket_safely(websocket)
            return False

        if payload.get("type") != "auth" or payload.get("api_key") != internal_key:
            logger.warning(f"WebSocket rejected: bad API key from {websocket.client}")
            await self._close_websocket_safely(websocket)
            return False

        return await _send_auth_ok()

    def _setup_routes(self):
        self.app.add_middleware(
            CORSMiddleware,
            allow_origins=self.allowed_origins,
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

            return await asyncio.to_thread(get_identity_data)

        @self.app.get("/api/persona", dependencies=[Depends(self.verify_auth)])
        async def get_persona():
            from core.prompt import get_identity_data, SOUL_FILE, MOOD_FILE, USERS_DIR
            import re

            result = await asyncio.to_thread(get_identity_data)
            result["soul_summary"] = ""
            if SOUL_FILE.exists():
                try:
                    soul_content = await self._read_text(SOUL_FILE)
                    lines = soul_content.strip().split("\n")
                    if lines and lines[0].startswith("#"):
                        lines = lines[1:]
                    result["soul_summary"] = " ".join(lines)[:300].strip()
                except Exception as e:
                    logger.error(f"Error reading soul summary: {e}")

            result["mood"] = ""
            if MOOD_FILE.exists():
                result["mood"] = (await self._read_text(MOOD_FILE)).strip()

            from config import load_config

            cfg = await asyncio.to_thread(load_config)
            result["enable_dynamic_personality"] = getattr(
                cfg.llm, "enable_dynamic_personality", False
            )

            relationships = []
            if USERS_DIR.exists():
                for user_file in USERS_DIR.glob("*.md"):
                    try:
                        content = await self._read_text(user_file)
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
                await self._write_text(identity_file, "\n".join(lines))

                mood_value = data.get("mood")
                if mood_value:
                    tmp_mood = mood_file.with_suffix(".tmp")
                    await self._write_text(tmp_mood, mood_value)
                    await asyncio.to_thread(tmp_mood.replace, mood_file)

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
                                identity_content = await self._read_text(item)
                            elif item.name.lower() == "soul.md":
                                soul_content = await self._read_text(item)
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
                    updated_files.append(await asyncio.to_thread(
                        safe_update, "IDENTITY.md", identity_match.group(1)
                    )
                    )
                if soul_match:
                    updated_files.append(
                        await asyncio.to_thread(
                            safe_update, "SOUL.md", soul_match.group(1)
                        )
                    )

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
                    "id": "gemini/gemini-3.1-flash-lite-preview",
                    "name": "Gemini 3.1 Flash-Lite (Preview)",
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
                    "id": "nvidia/openai/gpt-oss-120b",
                    "name": "GPT-OSS 120B",
                    "provider": "nvidia",
                },
                {
                    "id": "nvidia/openai/gpt-oss-20b",
                    "name": "GPT-OSS 20B",
                    "provider": "nvidia",
                },
                {
                    "id": "nvidia/z-ai/glm4.7",
                    "name": "GLM 4.7",
                    "provider": "nvidia",
                },
                {
                    "id": "nvidia/moonshotai/kimi-k2-instruct",
                    "name": "Kimi K2 Instruct",
                    "provider": "nvidia",
                },
                {
                    "id": "nvidia/moonshotai/kimi-k2-thinking",
                    "name": "Kimi K2 Thinking",
                    "provider": "nvidia",
                },
                {
                    "id": "nvidia/moonshotai/kimi-k2.5",
                    "name": "Kimi K2.5",
                    "provider": "nvidia",
                },
                {
                    "id": "nvidia/meta/llama-4-scout-17b-16e-instruct",
                    "name": "Llama 4 Scout",
                    "provider": "nvidia",
                },
                {
                    "id": "nvidia/meta/llama-4-maverick-17b-128e-instruct",
                    "name": "Llama 4 Maverick",
                    "provider": "nvidia",
                },
                {
                    "id": "nvidia/qwen/qwen3-next-80b-a3b-instruct",
                    "name": "Qwen 3 Next 80B",
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
                    "id": "nvidia/deepseek-ai/deepseek-v3.2",
                    "name": "DeepSeek V3.2",
                    "provider": "nvidia",
                },
                {
                    "id": "nvidia/qwen/qwen3-next-80b-a3b-thinking",
                    "name": "Qwen 3 Next 80B Thinking",
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
            from config import load_config

            cfg = load_config()
            model = cfg.llm.model
            start = time.time()
            runtime = (
                self.agent.get_llm_runtime_status()
                if getattr(self, "agent", None)
                and hasattr(self.agent, "get_llm_runtime_status")
                else {
                    "configured_model": model,
                    "active_model": model,
                    "fallback_models": getattr(cfg.llm, "fallback_models", []),
                    "using_fallback": False,
                }
            )
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
                    **runtime,
                }
            except Exception as e:
                error_msg = str(e)
                health_status = "Quota Exceeded" if "429" in error_msg else "Error"
                return {
                    "status": health_status,
                    "latency_ms": int((time.time() - start) * 1000),
                    "model": model,
                    "error": error_msg,
                    **runtime,
                }

        @self.app.get("/api/llm/runtime", dependencies=[Depends(self.verify_auth)])
        async def get_llm_runtime():
            from config import load_config

            cfg = load_config()
            if getattr(self, "agent", None) and hasattr(
                self.agent, "get_llm_runtime_status"
            ):
                return self.agent.get_llm_runtime_status()

            return {
                "configured_model": cfg.llm.model,
                "active_model": cfg.llm.model,
                "fallback_models": getattr(cfg.llm, "fallback_models", []),
                "using_fallback": False,
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
                "ENABLE_TELEGRAM": str(cfg.telegram.enabled).lower(),
                "TELEGRAM_BOT_TOKEN": cfg.telegram.token,
                "TELEGRAM_API_BASE": cfg.telegram.api_base,
                "TELEGRAM_ALLOW_FROM": ",".join(cfg.telegram.allow_from),
                "TELEGRAM_ALLOW_CHATS": ",".join(cfg.telegram.allow_chats),
                "TELEGRAM_POLL_TIMEOUT": str(cfg.telegram.poll_timeout),
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
                "WEB_ALLOWED_ORIGINS": ",".join(
                    getattr(cfg.web, "allowed_origins", [])
                ),
                "LLM_PROXY_URL": getattr(cfg.llm, "proxy_url", ""),
                "BROWSER_MODE": getattr(cfg.browser, "mode", "isolated"),
                "BROWSER_CHANNEL": getattr(cfg.browser, "channel", ""),
                "BROWSER_CDP_URL": getattr(cfg.browser, "cdp_url", ""),
                "BROWSER_USER_DATA_DIR": getattr(cfg.browser, "user_data_dir", ""),
                "BROWSER_PROFILE_DIRECTORY": getattr(
                    cfg.browser, "profile_directory", ""
                ),
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
                data = await self._read_json(cfg_path, default={})
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
                    data = await self._read_json(cfg_path, default={})
                discord_cfg = payload.get("discord")
                if not isinstance(discord_cfg, dict):
                    return {"error": "discord config must be an object"}
                data["discord"] = discord_cfg
                await self._write_json(cfg_path, data)
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
                cfg_json_file = Path("limebot.json")

                if "LLM_MODEL" in new_env:
                    model_value = str(new_env["LLM_MODEL"] or "").strip()
                    if not model_value:
                        return {"error": "LLM_MODEL cannot be empty."}
                    new_env["LLM_MODEL"] = model_value

                    try:
                        cfg_json = (
                            await self._read_json(cfg_json_file, default={})
                            if cfg_json_file.exists()
                            else {}
                        )
                        if not isinstance(cfg_json, dict):
                            cfg_json = {}
                        llm_cfg = cfg_json.get("llm")
                        if isinstance(llm_cfg, dict) and "model" in llm_cfg:
                            llm_cfg.pop("model", None)
                            if llm_cfg:
                                cfg_json["llm"] = llm_cfg
                            else:
                                cfg_json.pop("llm", None)
                            await self._write_json(cfg_json_file, cfg_json)
                    except Exception as e:
                        logger.warning(
                            f"Failed to remove deprecated llm.model from limebot.json: {e}"
                        )

                if "ALLOWED_PATHS" in new_env:
                    paths_data = new_env.pop("ALLOWED_PATHS")
                    paths_file = Path("allowed_paths.txt")
                    if isinstance(paths_data, list):
                        paths = [str(p).strip() for p in paths_data if str(p).strip()]
                    else:
                        paths = [
                            p.strip() for p in str(paths_data).split(",") if p.strip()
                        ]
                    await self._write_text(paths_file, "\n".join(paths))

                current_lines = (
                    (await self._read_text(env_file)).splitlines()
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

                await self._write_text(env_file, "\n".join(final_lines))

                # Update os.environ so the child process inherits
                # the correct values (load_dotenv uses override=False,
                # so it won't re-read .env values that already exist
                # in the inherited environment).
                for key, val in new_env.items():
                    os.environ[key] = str(val)

                # Clear the cached config so it's rebuilt on next access
                from config import reload_config

                reload_config()

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
            return await self._read_json(CONFIG_PATH, default={"mcpServers": {}})

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
                await self._write_json(CONFIG_PATH, data)
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
                from core.prompt import get_setup_state

                cfg = load_config()
                model = cfg.llm.model
                api_key = get_api_key_for_model(model)
                persona_state = get_setup_state()

                is_local = (
                    model and ("ollama" in model or "local" in model)
                ) or cfg.llm.base_url

                missing = []
                if not model:
                    missing.append("LLM_MODEL")
                if not api_key and not is_local:
                    missing.append("API_KEY")

                return {
                    "configured": len(missing) == 0,
                    "missing_keys": missing,
                    "persona_ready": persona_state["complete"],
                    "persona_missing": persona_state["missing"],
                    "setup_required": len(missing) > 0 or not persona_state["complete"],
                    "auth_required": self._is_auth_required(),
                }
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
                return {"logs": await self._tail_log_file(log_file, lines)}
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
                                content = await self._read_text(file_path)
                            except Exception:
                                continue

                            for line_no, raw_line in enumerate(
                                content.splitlines(), start=1
                            ):
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

        @self.app.get("/api/memory/debug", dependencies=[Depends(self.verify_auth)])
        async def get_memory_debug(session_key: Optional[str] = None, limit: int = 20):
            if not getattr(self, "agent", None):
                return {"traces": []}
            try:
                limit = max(1, min(int(limit), 100))
            except Exception:
                limit = 20
            return {
                "traces": self.agent.get_recent_rag_traces(
                    session_key=session_key, limit=limit
                )
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

        async def _reload_subagents() -> list[dict]:
            from core.subagents import SubagentRegistry

            if hasattr(self, "agent") and self.agent:
                registry = self.agent.subagent_registry
                await asyncio.to_thread(registry.discover_and_load)
                if hasattr(self.agent, "_refresh_tool_definitions"):
                    self.agent._refresh_tool_definitions()
                return registry.list_definitions()

            registry = SubagentRegistry()
            await asyncio.to_thread(registry.discover_and_load)
            return registry.list_definitions()

        @self.app.get("/api/subagents", dependencies=[Depends(self.verify_auth)])
        async def list_subagents():
            subagents = await _reload_subagents()
            return {"subagents": subagents}

        @self.app.post("/api/subagents", dependencies=[Depends(self.verify_auth)])
        async def create_subagent(request: Request):
            from core.subagents import SubagentRegistry

            body = await request.json()
            registry = (
                self.agent.subagent_registry
                if hasattr(self, "agent") and self.agent
                else SubagentRegistry()
            )
            try:
                saved = await asyncio.to_thread(
                    registry.save_subagent,
                    name=body.get("name", ""),
                    description=body.get("description", ""),
                    prompt=body.get("prompt", ""),
                    tools=body.get("tools"),
                    model=body.get("model", "inherit"),
                    location=body.get("location", "project"),
                )
            except ValueError as e:
                raise HTTPException(status_code=400, detail=str(e))
            subagents = await _reload_subagents()
            return {"status": "success", "subagent": saved, "subagents": subagents}

        @self.app.put(
            "/api/subagents/{subagent_id:path}",
            dependencies=[Depends(self.verify_auth)],
        )
        async def update_subagent(subagent_id: str, request: Request):
            from core.subagents import SubagentRegistry

            body = await request.json()
            registry = (
                self.agent.subagent_registry
                if hasattr(self, "agent") and self.agent
                else SubagentRegistry()
            )
            try:
                saved = await asyncio.to_thread(
                    registry.save_subagent,
                    name=body.get("name", ""),
                    description=body.get("description", ""),
                    prompt=body.get("prompt", ""),
                    tools=body.get("tools"),
                    model=body.get("model", "inherit"),
                    location=body.get("location", "project"),
                    subagent_id=subagent_id,
                )
            except ValueError as e:
                raise HTTPException(status_code=400, detail=str(e))
            subagents = await _reload_subagents()
            return {"status": "success", "subagent": saved, "subagents": subagents}

        @self.app.delete(
            "/api/subagents/{subagent_id:path}",
            dependencies=[Depends(self.verify_auth)],
        )
        async def delete_subagent(subagent_id: str):
            from core.subagents import SubagentRegistry

            registry = (
                self.agent.subagent_registry
                if hasattr(self, "agent") and self.agent
                else SubagentRegistry()
            )
            try:
                await asyncio.to_thread(registry.delete_subagent, subagent_id)
            except ValueError as e:
                raise HTTPException(status_code=400, detail=str(e))
            subagents = await _reload_subagents()
            return {"status": "success", "subagents": subagents}

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
                    await self._write_text(log_file, "")
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
            return await self._load_contacts_async()

        @self.app.post(
            "/api/whatsapp/contacts/approve", dependencies=[Depends(self.verify_auth)]
        )
        async def approve_whatsapp_contact(request: Request):
            data = await request.json()
            chat_id = data.get("chat_id")
            if not chat_id:
                return {"status": "error", "message": "Missing chat_id"}
            contacts = await self._load_contacts_async()
            if chat_id in contacts.get("pending", []):
                contacts["pending"].remove(chat_id)
            if chat_id in contacts.get("blocked", []):
                contacts["blocked"].remove(chat_id)
            if chat_id not in contacts.get("allowed", []):
                contacts.setdefault("allowed", []).append(chat_id)
            await self._save_contacts_async(contacts)
            return {"status": "success", "contacts": contacts}

        @self.app.post(
            "/api/whatsapp/contacts/deny", dependencies=[Depends(self.verify_auth)]
        )
        async def deny_whatsapp_contact(request: Request):
            data = await request.json()
            chat_id = data.get("chat_id")
            if not chat_id:
                return {"status": "error", "message": "Missing chat_id"}
            contacts = await self._load_contacts_async()
            if chat_id in contacts.get("pending", []):
                contacts["pending"].remove(chat_id)
            if chat_id in contacts.get("allowed", []):
                contacts["allowed"].remove(chat_id)
            if chat_id not in contacts.get("blocked", []):
                contacts.setdefault("blocked", []).append(chat_id)
            await self._save_contacts_async(contacts)
            return {"status": "success", "contacts": contacts}

        @self.app.post(
            "/api/whatsapp/contacts/unallow", dependencies=[Depends(self.verify_auth)]
        )
        async def unallow_whatsapp_contact(request: Request):
            data = await request.json()
            chat_id = data.get("chat_id")
            if not chat_id:
                return {"status": "error", "message": "Missing chat_id"}
            contacts = await self._load_contacts_async()
            if chat_id in contacts.get("allowed", []):
                contacts["allowed"].remove(chat_id)
            if chat_id not in contacts.get("pending", []):
                contacts.setdefault("pending", []).append(chat_id)
            await self._save_contacts_async(contacts)
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

        @self.app.patch(
            "/api/cron/jobs/{job_id}", dependencies=[Depends(self.verify_auth)]
        )
        async def update_cron_job(job_id: str, data: dict):
            if not self.scheduler:
                raise HTTPException(status_code=503, detail="Scheduler not initialized")

            if "active" not in data:
                raise HTTPException(status_code=400, detail="Missing active flag")

            updated = await self.scheduler.set_job_active(
                job_id, bool(data.get("active"))
            )
            if updated:
                return {"status": "success", "job": updated}
            raise HTTPException(status_code=404, detail="Job not found")

        @self.app.websocket("/ws")
        async def websocket_root(websocket: WebSocket):
            await self._websocket_handler(websocket)

        @self.app.websocket("/ws/client")
        async def websocket_client(websocket: WebSocket):
            await self._websocket_handler(websocket)

    async def _websocket_handler(self, websocket: WebSocket) -> None:
        """Shared handler for all WebSocket connections."""
        await websocket.accept()
        if not await self._authenticate_websocket(websocket):
            return
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
                attachment_paths: list[str] = []
                if raw_attachments := msg.get("attachments"):
                    try:
                        attachments, first_image_data_url = (
                            await self._normalize_web_attachments(
                                str(chat_id), raw_attachments
                            )
                        )
                    except ValueError as e:
                        await websocket.send_text(
                            json.dumps(
                                {
                                    "type": "message",
                                    "content": str(e),
                                    "sender": "bot",
                                    "chat_id": chat_id,
                                    "metadata": {"is_error": True},
                                }
                            )
                        )
                        continue

                    if attachments:
                        metadata["attachments"] = attachments
                        attachment_paths = [
                            str(a.get("path"))
                            for a in attachments
                            if a.get("path")
                        ]
                    if first_image_data_url:
                        metadata["image"] = first_image_data_url
                elif image_data := msg.get("image"):
                    metadata["image"] = image_data

                await self._handle_message(
                    sender_id=sender_id,
                    chat_id=chat_id,
                    content=content,
                    media=attachment_paths,
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
        prefer_base_port_only = os.environ.pop("LIMEBOT_SOFT_RESTART", "") == "1"
        base_port_wait_seconds = 12.0 if prefer_base_port_only else 5.0
        base_port_retry_interval = 0.5

        max_retries = 10

        async def _wait_for_preferred_port(port: int) -> bool:
            deadline = asyncio.get_running_loop().time() + base_port_wait_seconds
            while True:
                try:
                    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
                        s.bind(("0.0.0.0", port))
                    return True
                except OSError:
                    if asyncio.get_running_loop().time() >= deadline:
                        return False
                    logger.warning(
                        f"Port {port} is still in use; waiting before fallback..."
                    )
                    await asyncio.sleep(base_port_retry_interval)

        if not await _wait_for_preferred_port(base_port):
            if prefer_base_port_only:
                logger.error(
                    f"Configured web port {base_port} did not become available after "
                    f"{base_port_wait_seconds:.1f}s during soft restart."
                )
                raise OSError(f"Port {base_port} unavailable after restart wait")
            logger.warning(
                f"Configured web port {base_port} did not become available after "
                f"{base_port_wait_seconds:.1f}s; trying fallback ports."
            )

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
                # Write the actual port so skills can discover it
                try:
                    from pathlib import Path

                    port_file = Path("data/.backend_port")
                    port_file.parent.mkdir(parents=True, exist_ok=True)
                    port_file.write_text(str(current_port), encoding="utf-8")
                except Exception:
                    pass
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
                "turn_id": metadata.get("turn_id"),
                "message_id": metadata.get("message_id"),
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
