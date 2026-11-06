"""Image-generation workflow, provider fallback, and local result delivery."""

from __future__ import annotations

import asyncio
import base64
import json
import uuid
from datetime import datetime
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional


class ImageGenerationService:
    """Coordinate reference validation, provider fallback, saving, and delivery."""

    def __init__(
        self,
        *,
        get_config: Callable[[], Any],
        is_negated_prompt: Callable[[str], bool],
        resolve_references: Callable[..., Any],
        model_candidates: Callable[[str], List[str]],
        is_gemini_model: Callable[[str], bool],
        is_openai_model: Callable[[str], bool],
        generate_gemini: Callable[..., Any],
        generate_codex: Callable[..., Any],
        generate_litellm: Callable[..., Any],
        generate_openai_edit: Callable[..., Any],
        image_extension: Callable[[str], str],
        is_path_allowed: Callable[[Path], bool],
        output_directory: Callable[[], Path],
        display_path: Callable[[Path], str],
        publish_preview: Callable[[List[Path], str], Any],
        log_warning: Callable[[str], None],
    ) -> None:
        self._get_config = get_config
        self._is_negated_prompt = is_negated_prompt
        self._resolve_references = resolve_references
        self._model_candidates = model_candidates
        self._is_gemini_model = is_gemini_model
        self._is_openai_model = is_openai_model
        self._generate_gemini = generate_gemini
        self._generate_codex = generate_codex
        self._generate_litellm = generate_litellm
        self._generate_openai_edit = generate_openai_edit
        self._image_extension = image_extension
        self._is_path_allowed = is_path_allowed
        self._output_directory = output_directory
        self._display_path = display_path
        self._publish_preview = publish_preview
        self._log_warning = log_warning

    async def generate_image(
        self,
        prompt: str,
        model: str = "",
        size: str = "",
        quality: str = "",
        count: int = 1,
        reference_images: Optional[List[str]] = None,
        use_attached_images: Optional[bool] = None,
    ) -> str:
        prompt = str(prompt or "").strip()
        if not prompt:
            return "Error: prompt is required."
        if self._is_negated_prompt(prompt):
            return (
                "ACTION BLOCKED: The image prompt explicitly says that no image "
                "should be generated. Continue without calling generate_image."
            )

        config = self._get_config()
        image_config = getattr(config, "image_generation", None)
        requested_model = model or getattr(image_config, "model", "") or ""
        size = str(size or getattr(image_config, "size", "") or "1024x1024").strip()
        quality = (
            str(quality or getattr(image_config, "quality", "") or "auto")
            .strip()
            .lower()
        )
        try:
            count = max(1, min(int(count or 1), 4))
        except Exception:
            count = 1

        try:
            references, reference_requested = await self._resolve_references(
                reference_images, use_attached_images
            )
        except (OSError, ValueError) as exc:
            return f"Error: {exc}"
        if reference_requested and not references:
            return (
                "Error: The requested reference image is no longer available. "
                "Attach it again and retry."
            )

        candidates = self._model_candidates(requested_model)
        if not candidates:
            return (
                "Error: no image generation backend is configured. "
                "Configure Codex OAuth, OPENAI_API_KEY, or GEMINI_API_KEY."
            )

        errors: List[str] = []
        images: List[Dict[str, str]] = []
        used_model = ""
        for candidate in candidates:
            try:
                if self._is_gemini_model(candidate):
                    candidate_images = await self._generate_gemini(
                        prompt, candidate, count, size, references
                    )
                elif candidate.startswith("openai-codex/"):
                    candidate_images = await self._generate_codex(
                        prompt, candidate, count, size, quality, references
                    )
                elif self._is_openai_model(candidate):
                    if references:
                        candidate_images = await self._generate_openai_edit(
                            prompt,
                            candidate,
                            count,
                            size,
                            quality,
                            references,
                        )
                    else:
                        candidate_images = await self._generate_litellm(
                            prompt, candidate, count, size, quality
                        )
                else:
                    candidate_images = await self._generate_codex(
                        prompt, candidate, count, size, quality, references
                    )

                if candidate_images:
                    images = candidate_images
                    used_model = candidate
                    break
                errors.append(f"{candidate}: returned no image data")
            except Exception as exc:
                self._log_warning(
                    f"Image generation attempt failed with {candidate}: {exc}"
                )
                errors.append(f"{candidate}: {exc}")

        if not images:
            return (
                "Error: image generation failed for all configured backends. "
                + "; ".join(errors[:4])
            )

        output_dir = self._output_directory()
        if not self._is_path_allowed(output_dir):
            return "Error: Generated image output directory is not allowed."
        await asyncio.to_thread(output_dir.mkdir, parents=True, exist_ok=True)

        saved: List[Path] = []
        for index, image in enumerate(images[:count], start=1):
            mime_type = image.get("mime_type") or "image/png"
            extension = self._image_extension(mime_type)
            file_name = (
                f"generated_{datetime.now().strftime('%Y%m%d_%H%M%S')}_"
                f"{uuid.uuid4().hex[:8]}_{index}{extension}"
            )
            output_path = output_dir / file_name
            blob = base64.b64decode(image["b64"])
            await asyncio.to_thread(output_path.write_bytes, blob)
            saved.append(output_path)

        display_paths = [self._display_path(path) for path in saved]
        caption = f"Generated image: {display_paths[0]}"
        await self._publish_preview(saved, caption)

        return json.dumps(
            {
                "status": "ok",
                "model": used_model,
                "count": len(saved),
                "reference_count": len(references),
                "paths": display_paths,
                "note": "Generated images were saved locally and sent to the active chat when supported.",
            }
        )
