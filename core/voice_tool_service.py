"""On-demand voice message synthesis and channel delivery."""

from __future__ import annotations

from typing import Any, Callable


class VoiceToolService:
    """Synthesize and publish voice messages for supported chat channels."""

    def __init__(self, *, get_bus: Callable[[], Any]) -> None:
        self._get_bus = get_bus

    async def send_voice(self, text: str, channel: str = "") -> str:
        """Speak text aloud in the current web, Discord, or WhatsApp chat."""
        from core.context import tool_context
        from core.events import OutboundMessage

        try:
            from core.tts import ElevenLabsTTS
        except Exception as exc:  # pragma: no cover - defensive import guard
            return f"Error: voice synthesis is unavailable ({exc})."

        ctx = tool_context.get() or {}
        ctx_channel = (ctx.get("channel") or "").strip().lower()
        target = (str(channel or "").strip().lower()) or ctx_channel
        chat_id = (ctx.get("chat_id") or "").strip()

        if target not in {"discord", "whatsapp", "web"}:
            return (
                "Error: send_voice is only available in web, Discord, or WhatsApp "
                "conversations."
            )
        if not chat_id:
            return "Error: Missing current chat context for voice delivery."

        spoken = str(text or "").strip()
        if not spoken:
            return "Error: Text to speak is required."

        if not ElevenLabsTTS.get_api_key():
            return (
                "Error: ElevenLabs API key is not configured. Add it under "
                "Settings → Credentials to enable voice."
            )

        if target == "web":
            audio_url = await ElevenLabsTTS.synthesize_and_save(spoken)
            if not audio_url:
                return "Error: Voice synthesis failed."
            await self._get_bus().publish_outbound(
                OutboundMessage(
                    channel="web",
                    chat_id=chat_id,
                    content="",
                    metadata={"voice_url": audio_url},
                )
            )
            return "Sent a voice message to the web chat."

        audio_path = await ElevenLabsTTS.synthesize_to_file(spoken)
        if not audio_path:
            return "Error: Voice synthesis failed."
        await self._get_bus().publish_outbound(
            OutboundMessage(
                channel=target,
                chat_id=chat_id,
                content="",
                metadata={
                    "type": "file",
                    "file_path": audio_path,
                    "caption": "",
                    "cleanup_file": True,
                },
            )
        )
        return f"Sent a voice message to the current {target} chat."
