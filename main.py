"""LimeBot Entry Point."""

import asyncio
import signal
import sys
import os
from pathlib import Path
import json
import time
from loguru import logger

from config import load_config
from core.bus import MessageBus
from core.loop import AgentLoop
from core.scheduler import CronManager
from core.session_manager import SessionManager
from channels.discord import DiscordChannel
from channels.whatsapp import WhatsAppChannel
from channels.web import WebChannel


logger.remove()
logger.add(
    sys.stderr,
    level="DEBUG",
    filter=lambda r: (
        r["level"].no >= 10
        and (r["level"].no >= 20 or "core.loop" in r["name"] or "⏱" in r["message"])
    ),
)
os.makedirs("logs", exist_ok=True)
logger.add(
    "logs/limebot.log",
    rotation="1 MB",
    retention="10 days",
    level="DEBUG",
    filter=lambda r: (
        r["level"].no >= 20 or "core.loop" in r["name"] or "⏱" in r["message"]
    ),
)

BOOT_PATH = Path("persona") / "BOOT.md"
BOOT_STATE_PATH = Path("data") / "boot_state.json"
BOOT_DEBOUNCE_SECONDS = 120


def _load_boot_state() -> dict:
    if not BOOT_STATE_PATH.exists():
        return {}
    try:
        return json.loads(BOOT_STATE_PATH.read_text(encoding="utf-8"))
    except Exception:
        return {}


def _save_boot_state(state: dict) -> None:
    try:
        BOOT_STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
        BOOT_STATE_PATH.write_text(json.dumps(state, indent=2), encoding="utf-8")
    except Exception as e:
        logger.error(f"[BOOT] Failed to save boot state: {e}")


def _parse_boot_content(raw: str) -> tuple[str, bool]:
    lines = [line.rstrip() for line in raw.splitlines()]
    while lines and not lines[0].strip():
        lines.pop(0)
    once = False
    if lines and lines[0].strip().lower() == "@once":
        once = True
        lines = lines[1:]
    content = "\n".join(lines).strip()
    return content, once


def _channel_ready_status(channel) -> bool:
    name = getattr(channel, "name", "")
    if name == "discord" and hasattr(channel, "client"):
        return bool(channel.client.is_ready())
    if name == "whatsapp":
        return bool(getattr(channel, "_connected", False))
    if name == "web":
        return True
    return True


async def _wait_for_channels_ready(channels: list, timeout: float = 15.0) -> dict[str, bool]:
    """Wait briefly for core channels to be ready; returns readiness map."""
    deadline = time.time() + timeout
    while time.time() < deadline:
        status = {
            getattr(c, "name", "unknown"): _channel_ready_status(c) for c in channels
        }
        if all(status.values()):
            return status
        await asyncio.sleep(0.25)
    return {getattr(c, "name", "unknown"): _channel_ready_status(c) for c in channels}


async def _run_boot_hook(bus: MessageBus, channels: list, model: str) -> None:
    """If persona/BOOT.md exists, enqueue it as a high-priority startup task."""
    if not BOOT_PATH.exists():
        return

    try:
        raw = BOOT_PATH.read_text(encoding="utf-8")
    except Exception as e:
        logger.error(f"[BOOT] Failed to read {BOOT_PATH}: {e}")
        return

    content, once = _parse_boot_content(raw)
    if not content:
        logger.info("[BOOT] BOOT.md is empty; skipping.")
        return

    state = _load_boot_state()
    last_run = state.get("last_run_ts", 0)
    now = time.time()
    if now - last_run < BOOT_DEBOUNCE_SECONDS:
        logger.info("[BOOT] Debounced BOOT.md (recent run).")
        return

    status = await _wait_for_channels_ready(channels)

    from core.events import InboundMessage

    await bus.publish_inbound(
        InboundMessage(
            channel="web",
            sender_id="boot",
            chat_id="system",
            content=content,
            metadata={"source": "boot_md"},
        )
    )
    state["last_run_ts"] = now
    _save_boot_state(state)

    if once:
        try:
            BOOT_PATH.write_text("", encoding="utf-8")
            logger.info("[BOOT] @once detected — BOOT.md cleared after enqueue.")
        except Exception as e:
            logger.error(f"[BOOT] Failed to clear BOOT.md: {e}")

    ready_parts = ", ".join(
        f"{k}={'ready' if v else 'not-ready'}" for k, v in status.items()
    )
    logger.info(f"[BOOT] Boot complete (model={model}; {ready_parts}).")
    logger.info("[BOOT] BOOT.md queued for processing.")


async def main():

    config = load_config()
    logger.info("Starting LimeBot...")

    bus = MessageBus()
    session_manager = SessionManager()
    scheduler = CronManager(bus)

    channels = []

    if config.discord.enabled and config.discord.token:
        discord_channel = DiscordChannel(config.discord, bus)
        channels.append(discord_channel)
        bus.subscribe_outbound(discord_channel.name, discord_channel.send)
        logger.info("Discord channel initialized")

    if config.whatsapp.enabled:
        whatsapp_channel = WhatsAppChannel(config.whatsapp, bus)
        channels.append(whatsapp_channel)
        bus.subscribe_outbound(whatsapp_channel.name, whatsapp_channel.send)
        logger.info("WhatsApp channel initialized")
    else:
        logger.info("WhatsApp channel disabled by config")

    web_channel = WebChannel(config, bus, session_manager=session_manager)
    web_channel.set_scheduler(scheduler)
    channels.append(web_channel)
    web_channel.set_channels(channels)
    bus.subscribe_outbound(web_channel.name, web_channel.send)
    logger.info("Web channel initialized")

    agent = AgentLoop(
        bus,
        model=config.llm.model,
        scheduler=scheduler,
        session_manager=session_manager,
    )
    from core import prompt as prompt_module
    if prompt_module.is_setup_complete():
        asyncio.create_task(agent._warm_up_services())

    for c in channels:
        if hasattr(c, "set_agent"):
            c.set_agent(agent)

    async def init_background_services():
        from core.reflection import get_reflection_service
        from core.mcp_client import get_mcp_manager

        # Initialize MCP Manager
        mcp_manager = get_mcp_manager()
        asyncio.create_task(mcp_manager.initialize())

        from core.reflection import get_reflection_service

        get_reflection_service(bus, model=config.llm.model)

        existing_jobs = await scheduler.list_jobs()
        if not any(j.get("payload") == "@reflect_and_distill" for j in existing_jobs):
            await scheduler.add_job(
                trigger_time=None,
                message="@reflect_and_distill",
                context={
                    "channel": "system",
                    "chat_id": "global_reflection",
                    "sender_id": "maintenance",
                },
                cron_expr="0 */4 * * *",
            )
            logger.info("Reflective background task scheduled")

        system_channels = []
        if config.discord.enabled:
            discord_chat_id = (
                config.discord.allow_channels[0]
                if config.discord.allow_channels
                else "primary"
            )
            system_channels.append({"channel": "discord", "chat_id": discord_chat_id})

        if config.whatsapp.enabled:
            system_channels.append({"channel": "whatsapp", "chat_id": "primary"})

        if system_channels and getattr(config.llm, "enable_dynamic_personality", False):
            await scheduler.register_system_jobs(system_channels)
            logger.info("Proactive system jobs registered")
        elif system_channels:
            logger.info(
                "Dynamic personality disabled; skipping proactive system jobs registration"
            )

    asyncio.create_task(init_background_services())

    tasks = []

    tasks.append(asyncio.create_task(scheduler.run()))
    tasks.append(asyncio.create_task(bus.dispatch_outbound()))
    tasks.append(asyncio.create_task(agent.run()))
    tasks.append(asyncio.create_task(_run_boot_hook(bus, channels, config.llm.model)))

    for channel in channels:
        tasks.append(asyncio.create_task(channel.start()))

    loop = asyncio.get_running_loop()
    stop_event = asyncio.Event()

    def signal_handler():
        logger.info("Shutdown signal received")
        stop_event.set()

    if sys.platform != "win32":
        for sig in (signal.SIGINT, signal.SIGTERM):
            loop.add_signal_handler(sig, signal_handler)
    else:

        async def wakeup():
            while not stop_event.is_set():
                await asyncio.sleep(1)

        tasks.append(asyncio.create_task(wakeup()))

    await stop_event.wait()

    logger.info("Shutting down...")

    bus.stop()
    await agent.stop()

    for channel in channels:
        await channel.stop()

    for task in tasks:
        task.cancel()

    await asyncio.gather(*tasks, return_exceptions=True)
    logger.info("LimeBot stopped")


if __name__ == "__main__":
    try:
        if sys.platform == "win32":
            asyncio.set_event_loop_policy(asyncio.WindowsProactorEventLoopPolicy())

        asyncio.run(main())
    except KeyboardInterrupt:
        pass
    except Exception:
        import traceback

        traceback.print_exc()
        logger.exception("Fatal error during startup")
        sys.exit(1)
