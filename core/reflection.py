import asyncio
from pathlib import Path
from datetime import datetime
from typing import Optional, Any
from litellm import completion
from loguru import logger
from config import load_config
from core.events import InboundMessage


_BASE_DIR = Path(__file__).resolve().parent.parent
PERSONA_DIR = _BASE_DIR / "persona"
MEMORY_DIR = PERSONA_DIR / "memory"
LONG_TERM_MEMORY_FILE = PERSONA_DIR / "MEMORY.md"


class ReflectiveService:
    """
    Handles background distillation of memories and journals.
    """

    def __init__(self, bus: Any, model: str = "gpt-4o"):
        self.bus = bus
        self.model = model
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
            cfg = load_config()

            today_str = datetime.now().strftime("%Y-%m-%d")
            journal_file = MEMORY_DIR / f"{today_str}.md"

            journal_content = (
                journal_file.read_text(encoding="utf-8")
                if journal_file.exists()
                else "No logs for today."
            )
            long_term_memory = (
                LONG_TERM_MEMORY_FILE.read_text(encoding="utf-8")
                if LONG_TERM_MEMORY_FILE.exists()
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
            response = await asyncio.to_thread(
                completion, model=self.model, messages=prompt, base_url=cfg.llm.base_url, api_key=cfg.llm.api_key
            )

            result_text = response.choices[0].message.content
            return result_text

        except Exception as e:
            logger.error(f"Reflection cycle failed: {e}")
            return f"Error during reflection: {e}"


_reflection_service: Optional[ReflectiveService] = None


def get_reflection_service(bus=None, model="gpt-4o") -> ReflectiveService:

    global _reflection_service
    if _reflection_service is None:
        _reflection_service = ReflectiveService(bus, model)
    else:
        if model and _reflection_service.model != model:
            logger.info(
                f"ReflectiveService: updating model {_reflection_service.model!r} → {model!r}"
            )
            _reflection_service.model = model

        if bus is not None and _reflection_service.bus is not bus:
            _reflection_service.bus = bus
    return _reflection_service
