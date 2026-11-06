"""Provider selection and API calls for image generation."""

from __future__ import annotations

import base64
import json
import mimetypes
import os
import re
from typing import Any, Callable, Dict, List, Optional


class ImageProviderService:
    """Resolve image backends and return provider image data."""

    def __init__(
        self,
        *,
        get_config: Callable[[], Any],
        get_codex_account_id: Callable[[str], str],
    ) -> None:
        self.get_config = get_config
        self.get_codex_account_id = get_codex_account_id

    @property
    def config(self) -> Any:
        return self.get_config()

    @staticmethod
    def _normalize_image_model(model: str) -> str:
        return str(model or "").strip()

    @staticmethod
    def _is_gemini_image_model(model: str) -> bool:
        normalized = str(model or "").strip()
        bare = normalized.split("/", 1)[-1]
        return normalized.startswith(("gemini/", "google/")) and (
            "flash-image" in bare
            or "pro-image" in bare
            or "image-preview" in bare
        )

    @staticmethod
    def _is_openai_image_model(model: str) -> bool:
        bare = str(model or "").strip().split("/", 1)[-1]
        return (
            bare.startswith(("gpt-image-", "dall-e-"))
            or bare == "chatgpt-image-latest"
        )

    @staticmethod
    def _is_negated_image_generation_prompt(prompt: str) -> bool:
        """Detect explicit instructions saying that no image should be made."""
        normalized = str(prompt or "").strip().lower()
        patterns = (
            r"\bno\s+image\s+generation\s+(?:is\s+)?needed\b",
            r"\b(?:do\s+not|don't|dont)\s+(?:generate|create|draw|render)\s+"
            r"(?:an?\s+)?(?:image|picture|photo)\b",
            r"\bno\s+(?:es\s+)?necesari[oa]\s+(?:generar|crear|dibujar|renderizar)\s+"
            r"(?:una?\s+)?(?:imagen|foto)\b",
        )
        return any(re.search(pattern, normalized) for pattern in patterns)

    @staticmethod
    def _image_extension_for_mime(mime_type: str) -> str:
        normalized = (mime_type or "image/png").split(";", 1)[0].strip().lower()
        if normalized == "image/jpeg":
            return ".jpg"
        if normalized == "image/webp":
            return ".webp"
        return mimetypes.guess_extension(normalized) or ".png"

    @staticmethod
    def _gemini_aspect_ratio(size: str) -> str:
        normalized = str(size or "").strip().lower()
        if not normalized:
            return ""
        if ":" in normalized and "x" not in normalized:
            return normalized
        ratios = {
            "1024x1024": "1:1",
            "1536x1024": "3:2",
            "1024x1536": "2:3",
            "1792x1024": "16:9",
            "1024x1792": "9:16",
        }
        return ratios.get(normalized, "")

    @staticmethod
    def _codex_account_id(token: str) -> str:
        try:
            parts = str(token or "").split(".")
            if len(parts) != 3:
                return ""
            payload = parts[1] + "=" * (-len(parts[1]) % 4)
            claims = json.loads(base64.urlsafe_b64decode(payload.encode("ascii")))
            auth = claims.get("https://api.openai.com/auth") or {}
            return str(auth.get("chatgpt_account_id") or "").strip()
        except Exception:
            return ""

    @staticmethod
    def _codex_image_model(model: str) -> str:
        normalized = str(model or "").strip()
        if normalized.startswith("openai-codex/"):
            bare = normalized.removeprefix("openai-codex/")
            if bare and not bare.startswith(("gpt-image-", "dall-e-")):
                return bare
        return "gpt-5.4-mini"

    def _image_model_candidates(self, requested_model: str) -> List[str]:
        requested = self._normalize_image_model(requested_model)
        image_cfg = getattr(self.config, "image_generation", None)
        configured = self._normalize_image_model(getattr(image_cfg, "model", ""))
        primary_chat = str(getattr(getattr(self.config, "llm", None), "model", "") or "")
        codex_model = (
            primary_chat
            if primary_chat.startswith("openai-codex/")
            else "openai-codex/gpt-5.4-mini"
        )

        raw_candidates = [requested, configured, codex_model, "openai/gpt-image-2"]
        candidates: List[str] = []
        for candidate in raw_candidates:
            candidate = str(candidate or "").strip()
            if not candidate or candidate in candidates:
                continue
            if self._is_gemini_image_model(candidate) and not os.getenv("GEMINI_API_KEY"):
                continue
            if candidate.startswith("openai/") and not os.getenv("OPENAI_API_KEY"):
                continue
            candidates.append(candidate)
        return candidates

    async def _generate_codex_image(
        self,
        prompt: str,
        model: str,
        count: int,
        size: str,
        quality: str,
        references: Optional[List[Dict[str, Any]]] = None,
    ) -> List[Dict[str, str]]:
        from core.oauth_profiles import resolve_codex_oauth_api_key

        import httpx

        token = resolve_codex_oauth_api_key()
        account_id = self.get_codex_account_id(token)
        if not account_id:
            raise RuntimeError("Codex OAuth token did not include a ChatGPT account id.")

        prompt_hints = []
        if size:
            prompt_hints.append(f"Output size/aspect request: {size}.")
        if quality and quality != "auto":
            prompt_hints.append(f"Quality request: {quality}.")
        full_prompt = prompt
        if prompt_hints:
            full_prompt = f"{prompt}\n\n" + "\n".join(prompt_hints)

        input_content: List[Dict[str, Any]] = [
            {"type": "input_text", "text": full_prompt}
        ]
        input_content.extend(
            {
                "type": "input_image",
                "image_url": reference["data_url"],
            }
            for reference in (references or [])
        )

        payload = {
            "model": self._codex_image_model(model),
            "instructions": (
                "Generate exactly one image using the hosted image_generation tool. "
                "Return no text unless required."
            ),
            "store": False,
            "stream": True,
            "input": [
                {
                    "role": "user",
                    "content": input_content,
                }
            ],
            "tools": [{"type": "image_generation"}],
            "tool_choice": {"type": "image_generation"},
        }
        headers = {
            "Authorization": f"Bearer {token}",
            "chatgpt-account-id": account_id,
            "originator": "limebot",
            "OpenAI-Beta": "responses=experimental",
            "accept": "text/event-stream",
            "content-type": "application/json",
        }

        images: List[Dict[str, str]] = []
        async with httpx.AsyncClient(timeout=180.0) as client:
            for _ in range(count):
                async with client.stream(
                    "POST",
                    "https://chatgpt.com/backend-api/codex/responses",
                    headers=headers,
                    json=payload,
                ) as response:
                    if response.status_code >= 400:
                        detail = await response.aread()
                        text = detail.decode("utf-8", errors="replace").strip()
                        raise RuntimeError(
                            f"Codex image generation failed ({response.status_code}): {text}"
                        )

                    async for line in response.aiter_lines():
                        if not line.startswith("data:"):
                            continue
                        raw = line.removeprefix("data:").strip()
                        if not raw or raw == "[DONE]":
                            continue
                        try:
                            event = json.loads(raw)
                        except Exception:
                            continue
                        if event.get("type") == "response.failed":
                            error = (event.get("response") or {}).get("error") or {}
                            message = error.get("message") or json.dumps(event)[:500]
                            raise RuntimeError(f"Codex image generation failed: {message}")
                        item = event.get("item") or {}
                        if item.get("type") != "image_generation_call":
                            continue
                        b64 = item.get("result") or item.get("b64_json")
                        if b64:
                            images.append({"b64": b64, "mime_type": "image/png"})
                            break
        return images

    async def _generate_gemini_image(
        self,
        prompt: str,
        model: str,
        count: int,
        size: str,
        references: Optional[List[Dict[str, Any]]] = None,
    ) -> List[Dict[str, str]]:
        from core.llm_utils import get_api_key_for_model

        import httpx

        api_key = get_api_key_for_model(model)
        if not api_key:
            raise RuntimeError("GEMINI_API_KEY is not configured.")

        bare_model = model.split("/", 1)[-1]
        generation_config: Dict[str, Any] = {"responseModalities": ["Image"]}
        aspect_ratio = self._gemini_aspect_ratio(size)
        if aspect_ratio:
            generation_config["responseFormat"] = {
                "image": {"aspectRatio": aspect_ratio}
            }

        url = (
            "https://generativelanguage.googleapis.com/v1/models/"
            f"{bare_model}:generateContent"
        )
        parts: List[Dict[str, Any]] = [{"text": prompt}]
        parts.extend(
            {
                "inlineData": {
                    "mimeType": reference["mime_type"],
                    "data": base64.b64encode(reference["bytes"]).decode("ascii"),
                }
            }
            for reference in (references or [])
        )
        payload = {
            "contents": [{"parts": parts}],
            "generationConfig": generation_config,
        }
        images: List[Dict[str, str]] = []
        async with httpx.AsyncClient(timeout=120.0) as client:
            for _ in range(count):
                response = await client.post(
                    url,
                    params={"key": api_key},
                    json=payload,
                )
                response.raise_for_status()
                data = response.json()
                for candidate in data.get("candidates", []) or []:
                    content = candidate.get("content", {}) or {}
                    for part in content.get("parts", []) or []:
                        inline = part.get("inlineData") or part.get("inline_data")
                        if not isinstance(inline, dict):
                            continue
                        b64 = inline.get("data")
                        if not b64:
                            continue
                        images.append(
                            {
                                "b64": b64,
                                "mime_type": inline.get("mimeType")
                                or inline.get("mime_type")
                                or "image/png",
                            }
                        )
        return images

    async def _generate_litellm_image(
        self,
        prompt: str,
        model: str,
        count: int,
        size: str,
        quality: str,
    ) -> List[Dict[str, str]]:
        from core.llm_utils import get_api_key_for_model
        from litellm import aimage_generation

        import httpx

        api_key = get_api_key_for_model(model)
        litellm_model = model
        if model.startswith("openai/"):
            litellm_model = model.removeprefix("openai/")

        kwargs: Dict[str, Any] = {
            "prompt": prompt,
            "model": litellm_model,
            "n": count,
            "size": size,
            "timeout": 180,
        }
        if api_key:
            kwargs["api_key"] = api_key
        if quality and quality != "auto":
            kwargs["quality"] = quality
        if not model.startswith(("gemini/", "google/")):
            bare = str(model or "").split("/", 1)[-1]
            if not (
                bare.startswith("gpt-image-")
                or bare == "chatgpt-image-latest"
            ):
                kwargs["response_format"] = "b64_json"

        response = await aimage_generation(**kwargs)
        data = getattr(response, "data", None)
        if data is None and isinstance(response, dict):
            data = response.get("data")

        images: List[Dict[str, str]] = []
        for item in data or []:
            b64 = getattr(item, "b64_json", None)
            url = getattr(item, "url", None)
            if isinstance(item, dict):
                b64 = item.get("b64_json") or item.get("b64")
                url = item.get("url")
            if b64:
                images.append({"b64": b64, "mime_type": "image/png"})
                continue
            if url:
                async with httpx.AsyncClient(timeout=120.0) as client:
                    downloaded = await client.get(url)
                    downloaded.raise_for_status()
                    mime_type = downloaded.headers.get("content-type", "image/png")
                    images.append(
                        {
                            "b64": base64.b64encode(downloaded.content).decode("ascii"),
                            "mime_type": mime_type,
                        }
                    )
        return images

    async def _generate_openai_image_edit(
        self,
        prompt: str,
        model: str,
        count: int,
        size: str,
        quality: str,
        references: List[Dict[str, Any]],
    ) -> List[Dict[str, str]]:
        """Call the native Images edit endpoint with one or more references."""
        from core.llm_utils import get_api_key_for_model

        import httpx

        api_key = get_api_key_for_model(model)
        if not api_key:
            raise RuntimeError("OPENAI_API_KEY is not configured for image editing.")

        bare_model = model.removeprefix("openai/")
        form: Dict[str, str] = {
            "model": bare_model,
            "prompt": prompt,
            "n": str(count),
            "size": size,
        }
        if quality and quality != "auto":
            form["quality"] = quality
        files = [
            (
                "image[]",
                (
                    reference["name"],
                    reference["bytes"],
                    reference["mime_type"],
                ),
            )
            for reference in references
        ]
        async with httpx.AsyncClient(timeout=180.0) as client:
            response = await client.post(
                "https://api.openai.com/v1/images/edits",
                headers={"Authorization": f"Bearer {api_key}"},
                data=form,
                files=files,
            )
            if response.status_code >= 400:
                try:
                    error = response.json().get("error") or {}
                    detail = str(error.get("message") or "Image edit request failed.")
                except Exception:
                    detail = "Image edit request failed."
                raise RuntimeError(
                    f"OpenAI image edit failed ({response.status_code}): {detail[:500]}"
                )
            payload = response.json()

        images: List[Dict[str, str]] = []
        for item in payload.get("data", []) or []:
            b64 = item.get("b64_json") if isinstance(item, dict) else None
            url = item.get("url") if isinstance(item, dict) else None
            if b64:
                images.append({"b64": b64, "mime_type": "image/png"})
                continue
            if url:
                async with httpx.AsyncClient(timeout=120.0) as client:
                    downloaded = await client.get(url)
                    downloaded.raise_for_status()
                    images.append(
                        {
                            "b64": base64.b64encode(downloaded.content).decode(
                                "ascii"
                            ),
                            "mime_type": downloaded.headers.get(
                                "content-type", "image/png"
                            ),
                        }
                    )
        return images
