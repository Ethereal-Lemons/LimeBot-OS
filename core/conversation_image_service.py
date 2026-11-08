"""Manage image context attached to conversation turns and history."""

from __future__ import annotations

import json
import re
import time
from pathlib import Path
from typing import Any, Callable, Dict, List, MutableMapping, Tuple


class ConversationImageService:
    """Keep model-facing image inputs separate from persisted chat history."""

    def __init__(
        self,
        *,
        recent_attachments: Callable[
            [], MutableMapping[str, Tuple[float, List[Dict[str, Any]]]]
        ],
        histories: Callable[[], MutableMapping[str, List[Dict[str, Any]]]],
        mark_dirty: Callable[[str], None],
        record_anomaly: Callable[..., Any],
        current_model: Callable[[], str],
        render_text_only: Callable[[Any], Any],
        recent_reference_ttl_seconds: float = 30 * 60,
    ) -> None:
        self._recent_attachments = recent_attachments
        self._histories = histories
        self._mark_dirty = mark_dirty
        self._record_anomaly = record_anomaly
        self._current_model = current_model
        self._render_text_only = render_text_only
        self._recent_reference_ttl_seconds = recent_reference_ttl_seconds

    @staticmethod
    def messages_have_image_inputs(messages: List[Dict[str, Any]]) -> bool:
        for message in messages:
            content = message.get("content") if isinstance(message, dict) else None
            if not isinstance(content, list):
                continue
            for item in content:
                if isinstance(item, dict) and item.get("type") == "image_url":
                    return True
        return False

    @staticmethod
    def collect_image_inputs(
        attachments: List[Dict[str, Any]],
        metadata_images: Any = None,
        legacy_image: str = "",
    ) -> List[str]:
        """Prefer inline image data without adding its CDN URL a second time."""
        image_inputs: List[str] = []
        represented_urls: set[str] = set()

        for attachment in attachments:
            if attachment.get("kind") != "image":
                continue
            remote_url = str(attachment.get("url") or "").strip()
            preferred = str(attachment.get("data_url") or remote_url or "").strip()
            if preferred and preferred not in image_inputs:
                image_inputs.append(preferred)
            if remote_url:
                represented_urls.add(remote_url)

        extra_images = (
            [metadata_images]
            if isinstance(metadata_images, str)
            else list(metadata_images or [])
        )
        for candidate in [*extra_images, legacy_image]:
            value = str(candidate or "").strip()
            if value and value not in represented_urls and value not in image_inputs:
                image_inputs.append(value)

        return image_inputs

    def remember_image_attachments(
        self, session_key: str, attachments: List[Dict[str, Any]]
    ) -> None:
        images = [
            dict(attachment)
            for attachment in attachments
            if attachment.get("kind") == "image"
            and str(attachment.get("path") or "").strip()
        ][:4]
        if images:
            self._recent_attachments()[session_key] = (time.time(), images)

    def get_recent_image_attachments(self, session_key: str) -> List[Dict[str, Any]]:
        recent = self._recent_attachments()
        remembered = recent.get(session_key)
        if not remembered:
            return []
        remembered_at, attachments = remembered
        if time.time() - remembered_at > self._recent_reference_ttl_seconds:
            recent.pop(session_key, None)
            return []
        usable = [
            dict(attachment)
            for attachment in attachments
            if str(attachment.get("path") or "").strip()
            and Path(str(attachment.get("path"))).is_file()
        ]
        if not usable:
            recent.pop(session_key, None)
        return usable

    @staticmethod
    def message_refers_to_recent_image(content: str) -> bool:
        return bool(
            re.search(
                r"\b(?:image|photo|picture|attachment|reference|same|this|that|"
                r"imagen|foto|adjunt[oa]|referencia|misma|esta|esa|"
                r"generate\s+it|make\s+it|generala|gen[eé]rala|creala|cr[eé]ala|hazla)\b",
                str(content or ""),
                re.IGNORECASE,
            )
        )

    @staticmethod
    def image_safe_history_content(content: Any) -> Any:
        """Replace image bytes and expiring URLs with a durable small marker."""
        if not isinstance(content, list):
            return content
        text_parts = [
            str(item.get("text") or "").strip()
            for item in content
            if isinstance(item, dict) and item.get("type") == "text"
        ]
        image_count = sum(
            1
            for item in content
            if isinstance(item, dict) and item.get("type") == "image_url"
        )
        if not image_count:
            return content
        marker = (
            "[Image attachment processed]"
            if image_count == 1
            else f"[{image_count} image attachments processed]"
        )
        return " ".join([part for part in text_parts if part] + [marker]).strip()

    @classmethod
    def history_summary_payload(cls, messages: List[Dict[str, Any]]) -> str:
        """Serialize history for summarization without embedding image payloads."""
        safe_messages = []
        for message in messages:
            safe_message = dict(message)
            safe_message["content"] = cls.image_safe_history_content(
                message.get("content")
            )
            safe_messages.append(safe_message)
        return json.dumps(safe_messages, ensure_ascii=False, default=str)

    def evict_history_image_inputs(self, session_key: str) -> None:
        changed = False
        history = self._histories().get(session_key, [])
        for message in history:
            original_content = message.get("content")
            safe_content = self.image_safe_history_content(original_content)
            if safe_content is not original_content:
                message["content"] = safe_content
                changed = True
        if changed:
            self._mark_dirty(session_key)

    def downgrade_image_messages_for_text_model(
        self, messages: List[Dict[str, Any]], session_key: str
    ) -> bool:
        changed = False
        for message in messages:
            if not isinstance(message, dict):
                continue
            content = message.get("content")
            if not isinstance(content, list):
                continue
            if not any(
                isinstance(item, dict) and item.get("type") == "image_url"
                for item in content
            ):
                continue
            message["content"] = self._render_text_only(content)
            changed = True

        if changed:
            self._record_anomaly(
                session_key,
                "image_input_downgraded_for_text_model",
                detail=self._current_model(),
            )
        return changed

    def should_retry_without_images(
        self, error: Exception, messages: List[Dict[str, Any]]
    ) -> bool:
        if not self.messages_have_image_inputs(messages):
            return False
        error_text = str(error).lower()
        return any(
            phrase in error_text
            for phrase in (
                "not a multimodal model",
                "does not support vision",
                "does not support image",
                "doesn't support vision",
                "doesn't support image",
            )
        )
