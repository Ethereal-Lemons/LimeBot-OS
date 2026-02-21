"""LimeBot Entry Point."""

import asyncio
import signal
import sys
import os
from loguru import logger

from config import load_config
from core.bus import MessageBus
from core.loop import AgentLoop
from core.scheduler import CronManager
from channels.discord import DiscordChannel
from channels.whatsapp import WhatsAppChannel
from channels.web import WebChannel


logger.remove()
logger.add(sys.stderr, level="INFO")
os.makedirs("logs", exist_ok=True)
logger.add("logs/limebot.log", rotation="1 MB", retention="10 days", level="INFO")


async def main():

    config = load_config()
    logger.info("Starting LimeBot...")

    bus = MessageBus()

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

    web_channel = WebChannel(config, bus)
    web_channel.set_scheduler(scheduler)
    channels.append(web_channel)
    web_channel.set_channels(channels)
    bus.subscribe_outbound(web_channel.name, web_channel.send)
    logger.info("Web channel initialized")

    agent = AgentLoop(bus, model=config.llm.model, scheduler=scheduler)

    for c in channels:
        if hasattr(c, "set_agent"):
            c.set_agent(agent)

    async def init_background_services():
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
