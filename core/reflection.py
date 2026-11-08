from datetime import datetime
from typing import Optional, Any
try:
    from loguru import logger
except Exception:
    import logging

    logger = logging.getLogger(__name__)
from core.events import InboundMessage
from core.llm_client import ChatRequest, LimeLLMClient


from core.paths import MEMORY_DIR, LONG_TERM_MEMORY_FILE, PersonaPaths, get_persona_paths
from core.profile_context import get_current_profile_id


class _LegacyReflectionPaths(PersonaPaths):
    """Preserve default reflection path overrides used by existing callers/tests."""

    @property
    def memory(self):
        return MEMORY_DIR

    @property
    def long_term_memory(self):
        return LONG_TERM_MEMORY_FILE


def _load_config():
    from config import load_config

    return load_config()


class ReflectiveService:
    """
    Handles background distillation of memories and journals.
    """

    def __init__(
        self,
        bus: Any,
        model: str = "gpt-4o",
        persona_paths: Optional[PersonaPaths] = None,
    ):
        self.bus = bus
        self.model = model
        self.persona_paths = persona_paths or _LegacyReflectionPaths(
            root=get_persona_paths("default").root
        )
        self.llm_client = LimeLLMClient()
        self._running = False

    async def trigger_reflection(self):
        """
        Manually trigger the reflection loop by sending a system message into the bus.
        """
        logger.info("Triggering background reflection...")

        msg = InboundMessage(
            channel="system",
            sender_id="maintenance",
            chat_id="global_reflection",
            content="@reflect_and_distill",
            metadata={"is_maintenance": True, "silent": True},
        )
        await self.bus.publish_inbound(msg)

    async def run_reflection_cycle(self):
        """
        The actual logic for distilling today's logs into MEMORY.md.
        Called by AgentLoop when it sees the @reflect_and_distill trigger.
        """
        try:
            cfg = _load_config()
            paths = self.persona_paths

            today_str = datetime.now().strftime("%Y-%m-%d")
            journal_file = paths.memory / f"{today_str}.md"

            if not journal_file.exists():
                logger.info("Reflection skipped: no journal file for today.")
                return "No logs for today."

            journal_content = journal_file.read_text(encoding="utf-8")
            if not journal_content.strip():
                logger.info("Reflection skipped: journal is empty.")
                return "No logs for today."
            long_term_memory = (
                paths.long_term_memory.read_text(encoding="utf-8")
                if paths.long_term_memory.exists()
                else "# Long Term Memory\n"
            )

            prompt = [
                {
                    "role": "system",
                    "content": (
                        "You are the 'Digital Soul' maintenance routine. Your job is to distill daily chat journals (provided as 'TODAY'S LOGS') into a permanent MEMORY.md file.\n"
                        "Rules:\n"
                        "1. Keep critical facts about the user (preferences, names, projects, specific requests).\n"
                        "2. Remove redundant conversation history or minor social banter.\n"
                        "3. Update project statuses mentioned in the journals.\n"
                        "4. Output the ENTIRE updated content of MEMORY.md wrapped in <save_memory> tags.\n"
                        "5. IMPORTANT: Ignore system logs, error messages, or internal bot state unless they represent a core change in user persona or persistence. Exclusively use the provided journal content."
                    ),
                },
                {
                    "role": "user",
                    "content": f"CURRENT MEMORY.md:\n{long_term_memory}\n\nTODAY'S LOGS:\n{journal_content}",
                },
            ]

            logger.info("Generating reflected memory distillation...")
            provider = self.llm_client.resolve_provider(
                self.model, default_base_url=cfg.llm.base_url
            )
            response = await self.llm_client.complete(
                provider,
                ChatRequest(messages=prompt, session_id="reflection-cycle"),
            )

            result_text = response.choices[0].message.content
            return result_text

        except Exception as e:
            logger.error(f"Reflection cycle failed: {e}")
            return f"Error during reflection: {e}"


_reflection_service: Optional[ReflectiveService] = None
_profile_reflection_services: dict[str, ReflectiveService] = {}


def get_reflection_service(
    bus=None, model="gpt-4o", *, profile_id: Optional[str] = None
) -> ReflectiveService:
    global _reflection_service
    normalized_profile_id = str(
        profile_id or get_current_profile_id() or "default"
    ).strip().lower()
    if normalized_profile_id == "default":
        service = _reflection_service
    else:
        service = _profile_reflection_services.get(normalized_profile_id)

    if service is None:
        service = ReflectiveService(
            bus,
            model,
            persona_paths=(
                get_persona_paths(normalized_profile_id)
                if normalized_profile_id != "default"
                else None
            ),
        )
        if normalized_profile_id == "default":
            _reflection_service = service
        else:
            _profile_reflection_services[normalized_profile_id] = service
    else:
        if model and service.model != model:
            logger.info(
                f"ReflectiveService ({normalized_profile_id}): updating model "
                f"{service.model!r} → {model!r}"
            )
            service.model = model

        if bus is not None and service.bus is not bus:
            service.bus = bus
    return service
