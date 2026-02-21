"""Discord channel implementation."""

import asyncio
import aiohttp
import discord
from discord import app_commands
import random
from typing import Any

from core.bus import MessageBus
from core.events import OutboundMessage, InboundMessage
from channels.base import BaseChannel
from loguru import logger


_MAX_MESSAGE_LEN = 2000
_CHUNK_SIZE = 1900


class ToolConfirmationView(discord.ui.View):
    def __init__(
        self, conf_id: str, chat_id: str, bus: MessageBus, config: Any, agent=None
    ):
        super().__init__(timeout=None)
        self.conf_id = conf_id
        self.chat_id = chat_id
        self.bus = bus
        self.config = config
        self.agent = agent

    @discord.ui.button(label="Approve", style=discord.ButtonStyle.success, emoji="✅")
    async def approve_button(
        self, interaction: discord.Interaction, button: discord.ui.Button
    ):
        await self._respond(interaction, True)

    @discord.ui.button(label="Deny", style=discord.ButtonStyle.danger, emoji="⛔")
    async def deny_button(
        self, interaction: discord.Interaction, button: discord.ui.Button
    ):
        await self._respond(interaction, False)

    async def _respond(self, interaction: discord.Interaction, approved: bool):
        for child in self.children:
            child.disabled = True

        embed = interaction.message.embeds[0] if interaction.message.embeds else None

        success = True
        err_msg = None

        if hasattr(self, "agent") and self.agent:
            try:
                success = await self.agent.confirm_tool(self.conf_id, approved, False)
                if not success:
                    err_msg = "Confirmation request not found or expired."
            except Exception as e:
                logger.error(f"[Discord] Failed to confirm tool natively: {e}")
                err_msg = f"Internal error: {e}"
                success = False
        else:
            api_key = getattr(self.config.whitelist, "api_key", None)
            port = getattr(self.config, "port", 8000)

            url = f"http://127.0.0.1:{port}/api/confirm-tool"
            headers = {"X-API-Key": api_key} if api_key else {}
            payload = {
                "conf_id": self.conf_id,
                "approved": approved,
                "session_whitelist": False,
            }

            try:
                async with aiohttp.ClientSession() as session:
                    res = await session.post(url, json=payload, headers=headers)
                    if res.status != 200:
                        success = False
                        err_msg = f"HTTP Error {res.status}"
            except Exception as e:
                logger.error(f"[Discord] Failed to confirm tool via API: {e}")
                success = False
                err_msg = str(e)

        if embed:
            if success:
                embed.color = 0x57F287 if approved else 0xED4245
                embed.title = (
                    f"Exec Approval Resolved - {'Approved' if approved else 'Denied'}"
                )
            else:
                embed.color = 0xED4245
                embed.title = "Exec Approval Failed"

        await interaction.response.edit_message(embed=embed, view=None)

        if not success:
            try:
                await interaction.followup.send(
                    f"⚠️ Failed to process approval: {err_msg}", ephemeral=True
                )
            except Exception:
                pass
            return

        # Post the outcome back to the agent bus context
        msg_content = (
            f"[CONFIRMATION_APPROVED:{self.conf_id}]"
            if approved
            else "User denied the action."
        )
        await self.bus.publish_inbound(
            InboundMessage(
                channel="discord",
                sender_id=str(interaction.user.id),
                chat_id=self.chat_id,
                content=msg_content,
                metadata={"source": "discord", "is_confirmation": True},
            )
        )


_ACTIVITY_MAP = {
    "watching": discord.ActivityType.watching,
    "listening": discord.ActivityType.listening,
    "competing": discord.ActivityType.competing,
}

_STATUS_MAP = {
    "online": discord.Status.online,
    "idle": discord.Status.idle,
    "dnd": discord.Status.dnd,
    "invisible": discord.Status.invisible,
}


class DiscordChannel(BaseChannel):
    """Discord channel implementation."""

    name = "discord"

    def __init__(self, config: Any, bus: MessageBus):
        super().__init__(config, bus)

        intents = discord.Intents.default()
        intents.message_content = True

        self.client = discord.Client(intents=intents)
        self.tree = discord.app_commands.CommandTree(self.client)
        self.token: str | None = getattr(self.config, "token", None)

        raw_channels = getattr(self.config, "allow_channels", [])
        self._allowed_channels: set[str] = set(raw_channels)
        self._tool_messages: dict[str, discord.Message] = {}
        self.agent = None

        self._register_events()

    def set_agent(self, agent) -> None:
        self.agent = agent

    def _register_events(self) -> None:
        """Register discord.py event handlers."""

        @self.tree.command(
            name="status", description="Check LimeBot system health and uptime."
        )
        async def cmd_status(interaction: discord.Interaction):
            from time import time
            import psutil

            uptime = int(time() - psutil.boot_time())
            embed = discord.Embed(title="🟢 System Online", color=0x57F287)
            embed.add_field(name="Uptime", value=f"{uptime // 60} minutes", inline=True)
            embed.add_field(name="CPU", value=f"{psutil.cpu_percent()}%", inline=True)
            embed.add_field(
                name="RAM", value=f"{psutil.virtual_memory().percent}%", inline=True
            )
            await interaction.response.send_message(embed=embed)

        @self.tree.command(
            name="persona", description="View the currently active bot personality."
        )
        async def cmd_persona(interaction: discord.Interaction):
            from core.prompt import get_identity_data

            data = get_identity_data()
            embed = discord.Embed(
                title=f"🎭 Active Identity: {data.get('name', 'LimeBot')}",
                color=0x3498DB,
            )
            desc = data.get("identity", "Default system prompt.")
            embed.description = (
                f"```\n{desc[:4000]}...\n```"
                if len(desc) > 4000
                else f"```\n{desc}\n```"
            )
            await interaction.response.send_message(embed=embed)

        @self.tree.command(
            name="clear_memory",
            description="Wipe temporary session history for this chat.",
        )
        async def cmd_clear(interaction: discord.Interaction):
            if not self.agent:
                await interaction.response.send_message(
                    "❌ Agent runtime not linked.", ephemeral=True
                )
                return

            from core.session_manager import get_session_manager

            sm = get_session_manager()
            await sm.clear_session(str(interaction.channel_id))
            await interaction.response.send_message(
                "🧠 Chat session history has been cleared.", ephemeral=False
            )

        @self.client.event
        async def on_ready():
            logger.info(
                f"[Discord] Logged in as {self.client.user} (ID: {self.client.user.id})"
            )
            try:
                # Global sync — registers /status, /persona, /clear_memory worldwide.
                synced = await self.tree.sync()
                logger.info(f"[Discord] Synced {len(synced)} global slash command(s).")
            except Exception as e:
                logger.error(f"[Discord] Failed to sync global slash commands: {e}")

            for guild in self.client.guilds:
                try:
                    self.tree.copy_global_to(guild=guild)
                    await self.tree.sync(guild=guild)
                    logger.debug(
                        f"[Discord] Cleared guild command cache for '{guild.name}'."
                    )
                except Exception as e:
                    logger.warning(
                        f"[Discord] Guild sync failed for '{guild.name}': {e}"
                    )

            await self._set_presence()

        @self.client.event
        async def on_message(message: discord.Message):
            await self._on_message(message)

        @self.client.event
        async def on_disconnect():
            logger.warning("[Discord] Disconnected from gateway.")

        @self.client.event
        async def on_resumed():
            logger.info("[Discord] Session resumed.")

        @self.client.event
        async def on_error(event: str, *args, **kwargs):
            logger.exception(f"[Discord] Unhandled error in event '{event}'")

        @self.tree.error
        async def on_app_command_error(
            interaction: discord.Interaction,
            error: app_commands.AppCommandError,
        ):

            if isinstance(error, app_commands.CommandInvokeError):
                original = error.original
                if isinstance(original, discord.NotFound) and original.code == 10062:
                    logger.debug(
                        f"[Discord] Stale interaction for '{interaction.command and interaction.command.name}' "
                        "ignored (10062 — likely an evicted voice skill command)."
                    )
                    return
            # All other command errors — try to tell the user, log the full trace
            logger.exception(
                f"[Discord] Slash command error in '{interaction.command and interaction.command.name}': {error}"
            )
            msg = "Something went wrong. Please try again."
            try:
                if interaction.response.is_done():
                    await interaction.followup.send(msg, ephemeral=True)
                else:
                    await interaction.response.send_message(msg, ephemeral=True)
            except Exception:
                pass

    async def _set_presence(self) -> None:
        """Set bot activity and status from config."""
        activity_type = getattr(self.config, "activity_type", "playing").lower()
        activity_text = getattr(self.config, "activity_text", "LimeBot")
        status_str = getattr(self.config, "status", "online").lower()

        status = _STATUS_MAP.get(status_str, discord.Status.online)
        if status_str not in _STATUS_MAP:
            logger.warning(
                f"[Discord] Unknown status '{status_str}', defaulting to 'online'."
            )

        if activity_type in _ACTIVITY_MAP:
            activity = discord.Activity(
                type=_ACTIVITY_MAP[activity_type], name=activity_text
            )
        else:
            if activity_type != "playing":
                logger.warning(
                    f"[Discord] Unknown activity_type '{activity_type}', defaulting to 'playing'."
                )
            activity = discord.Game(name=activity_text)

        await self.client.change_presence(activity=activity, status=status)
        logger.info(
            f"[Discord] Presence set: {activity_type} '{activity_text}' | status: {status_str}"
        )

    async def _on_message(self, message: discord.Message) -> None:
        """Handle an incoming Discord message."""

        if message.author.id == self.client.user.id:
            return

        if message.author.bot:
            return

        sender_id = str(message.author.id)
        chat_id = str(message.channel.id)
        is_dm = isinstance(message.channel, discord.DMChannel)
        is_mentioned = self.client.user in message.mentions or is_dm

        if not self.is_allowed(sender_id):
            return

        if (
            self._allowed_channels
            and not is_dm
            and chat_id not in self._allowed_channels
        ):
            return

        content_parts = [message.content] if message.content else []
        attachments = [a.url for a in message.attachments]
        content = (
            "\n".join(content_parts + attachments) if attachments else message.content
        )

        if not content:
            return

        await self._handle_message(
            sender_id=sender_id,
            chat_id=chat_id,
            content=content,
            metadata={
                "author": message.author.name,
                "author_display": message.author.display_name,
                "channel_name": getattr(message.channel, "name", "DM"),
                "guild_id": str(message.guild.id) if message.guild else None,
                "mentioned": is_mentioned,
                "is_dm": is_dm,
                "message_id": str(message.id),
            },
        )

        if random.random() < 0.2:
            reaction = await self._pick_reaction(content)
            if reaction:
                try:
                    await message.add_reaction(reaction)
                    logger.info(
                        f"[Discord] Added reaction {reaction} to message {message.id}"
                    )
                except Exception as e:
                    logger.warning(f"[Discord] Failed to add reaction: {e}")

    async def _pick_reaction(self, content: str) -> str | None:
        """Analyze message content and pick a reaction emoji if sentiment match is found."""
        from core.prompt import get_identity_data

        identity = get_identity_data()
        raw_emojis = identity.get("reaction_emojis", "")
        if not raw_emojis:
            return None

        buckets = {}
        for bucket_str in raw_emojis.split(";"):
            if ":" in bucket_str:
                label, emojis = bucket_str.split(":", 1)
                buckets[label.strip().lower()] = [e.strip() for e in emojis.split(",")]

        if not buckets:
            return None

        content_lower = content.lower()

        keywords = {
            "happy": [
                "happy",
                "lol",
                "haha",
                "yay",
                "good",
                "nice",
                "great",
                "awesome",
                "perfect",
            ],
            "sad": ["sad", "sorry", "rip", "bad", "unfortunate", "oh no", "cry"],
            "love": [
                "love",
                "heart",
                "amazing",
                "beautiful",
                "thanks",
                "thank you",
                "ty",
            ],
            "wow": ["wow", "pog", "incredible", "omg", "whoa", "crazy", "shocking"],
            "confused": ["what", "huh", "confused", "question", "idk", "strange"],
            "angry": ["angry", "mad", "hate", "stop", "no", "fail", "broken"],
            "confirm": ["ok", "agree", "sure", "yes", "done"],
        }

        for label, words in keywords.items():
            if any(word in content_lower for word in words):
                if label in buckets:
                    return random.choice(buckets[label])

        return None

    async def start(self) -> None:
        """Start the Discord bot."""
        if not self.token:
            logger.warning("[Discord] No token configured, skipping Discord channel.")
            return

        logger.info("[Discord] Starting...")
        try:
            await self.client.start(self.token)
        except discord.LoginFailure:
            logger.error("[Discord] Login failed — check your bot token.")
        except discord.PrivilegedIntentsRequired:
            logger.error(
                "[Discord] Privileged intents are required but not enabled in the Developer Portal."
            )
        except Exception as e:
            logger.exception(f"[Discord] Unexpected error during start: {e}")

    async def stop(self) -> None:
        """Stop the Discord bot gracefully."""
        if not self.client.is_closed():
            await self.client.close()
            logger.info("[Discord] Client closed.")

    async def send(self, msg: OutboundMessage) -> None:
        """Schedule a message send without blocking the caller."""
        asyncio.create_task(self._send_impl(msg))

    async def _handle_tool_execution(self, target, metadata: dict) -> None:
        tc_id = metadata.get("tool_call_id")
        status = metadata.get("status")
        tool_name = metadata.get("tool", "unknown")

        if status == "running":
            embed = discord.Embed(
                title=f"🛠️ Tool Running: `{tool_name}`",
                description="Executing with arguments...",
                color=0x95A5A6,
            )
            message = await target.send(embed=embed)
            if tc_id:
                self._tool_messages[tc_id] = message
        elif status == "completed":
            if tc_id and tc_id in self._tool_messages:
                message = self._tool_messages.pop(tc_id)
                result = metadata.get("result", "")
                embed = discord.Embed(
                    title=f"✅ Tool Completed: `{tool_name}`",
                    description=f"```\n{result[:1500]}\n```",
                    color=0x57F287,
                )
                await message.edit(embed=embed)
        elif status == "error":
            if tc_id and tc_id in self._tool_messages:
                message = self._tool_messages.pop(tc_id)
                result = metadata.get("result", "Unknown error")
                embed = discord.Embed(
                    title=f"❌ Tool Error: `{tool_name}`",
                    description=f"```\n{result[:1500]}\n```",
                    color=0xED4245,
                )
                await message.edit(embed=embed)

    async def _send_impl(self, msg: OutboundMessage) -> None:
        """Core send logic: resolve target, then dispatch based on message type."""
        target = await self._resolve_target(msg.chat_id)
        if target is None:
            return

        metadata = msg.metadata or {}
        msg_type = metadata.get("type")

        # Basic Auto-Threading Support
        try:
            if msg_type == "tool_execution" or metadata.get("is_thought"):
                message_id = metadata.get("message_id")

                # If target is a standard TextChannel, we can spawn threads off messages
                if isinstance(target, discord.TextChannel) and message_id:
                    try:
                        root_msg = await target.fetch_message(int(message_id))
                        # If a thread doesn't already exist on this message, create it
                        if not root_msg.thread:
                            target = await root_msg.create_thread(
                                name="⚙️ Processing Task...", auto_archive_duration=60
                            )
                        else:
                            # Rebind target to the existing thread
                            target = root_msg.thread
                    except discord.NotFound:
                        pass
                    except discord.HTTPException as e:
                        logger.warning(
                            f"[Discord] Thread creation failed, falling back to main channel: {e}"
                        )
        except Exception as e:
            logger.error(f"[Discord] Error during auto-threading evaluation: {e}")

        try:
            if msg_type == "tool_execution":
                status = metadata.get("status")
                if status == "waiting_confirmation" and metadata.get("embed"):
                    await self._send_embed(
                        target, metadata["embed"], metadata, msg.chat_id
                    )
                else:
                    await self._handle_tool_execution(target, metadata)
            elif msg_type == "typing":
                await self._send_typing(target)
            elif msg_type == "file":
                await self._send_file(target, metadata)
            elif metadata.get("embed"):
                await self._send_embed(target, metadata["embed"], metadata, msg.chat_id)
            else:
                await self._send_text(target, msg.content)
        except discord.Forbidden:
            logger.error(
                f"[Discord] Missing permissions to send to {_target_name(target)}."
            )
        except discord.HTTPException as e:
            logger.error(
                f"[Discord] HTTP error while sending to {_target_name(target)}: {e}"
            )
        except Exception as e:
            logger.exception(
                f"[Discord] Unexpected error sending to {_target_name(target)}: {e}"
            )

    async def _resolve_target(
        self, chat_id: str
    ) -> discord.TextChannel | discord.DMChannel | discord.User | None:
        """Resolve a channel ID or user ID to a sendable target."""
        try:
            target_int = int(chat_id)
        except ValueError:
            logger.error(f"[Discord] Invalid chat_id '{chat_id}': must be numeric.")
            return None

        target = self.client.get_channel(target_int)
        if target:
            return target

        try:
            return await self.client.fetch_channel(target_int)
        except discord.NotFound:
            pass
        except discord.Forbidden:
            logger.error(f"[Discord] No access to channel {chat_id}.")
            return None

        try:
            return await self.client.fetch_user(target_int)
        except discord.NotFound:
            logger.error(f"[Discord] No channel or user found for ID {chat_id}.")
        except Exception as e:
            logger.error(f"[Discord] Failed to resolve target {chat_id}: {e}")

        return None

    async def _send_typing(self, target) -> None:
        async with target.typing():
            await asyncio.sleep(0)

    async def _send_text(self, target, content: str) -> None:
        """Send text, splitting at word boundaries if it exceeds the Discord limit."""
        if not content:
            logger.warning(
                f"[Discord] Attempted to send empty message to {_target_name(target)}, skipping."
            )
            return

        import re
        from pathlib import Path

        # Extract markdown images: ![alt](path)
        img_pattern = re.compile(r"!\[([^\]]*)\]\(([^)]+)\)")

        matches = img_pattern.findall(content)
        files_to_send = []

        for alt, path in matches:
            p = Path(path)
            if p.exists() and p.is_file():
                files_to_send.append(discord.File(p))
                # Remove the markdown tag from text
                content = content.replace(f"![{alt}]({path})", "").strip()

        if content:
            chunks = _split_message(content)
            for i, chunk in enumerate(chunks, start=1):
                # Send files along with the last chunk
                if i == len(chunks) and files_to_send:
                    await target.send(chunk, files=files_to_send)
                    files_to_send = []
                else:
                    await target.send(chunk)
                if len(chunks) > 1:
                    logger.debug(
                        f"[Discord] Sent chunk {i}/{len(chunks)} to {_target_name(target)}"
                    )

            logger.info(
                f"[Discord] Message sent to {_target_name(target)} ({len(chunks)} chunk(s))"
            )
        elif files_to_send:
            # Only sending files, no text
            await target.send(files=files_to_send)
            logger.info(
                f"[Discord] Sent {len(files_to_send)} file(s) to {_target_name(target)}"
            )

    async def _send_embed(
        self, target, embed_data: dict, metadata: dict, chat_id: str
    ) -> None:
        color_str = embed_data.get("color", "#5865F2")
        try:
            color_int = int(color_str.lstrip("#"), 16)
        except ValueError:
            logger.warning(
                f"[Discord] Invalid embed color '{color_str}', using default."
            )
            color_int = 0x5865F2

        embed = discord.Embed(
            title=embed_data.get("title", ""),
            description=embed_data.get("description", ""),
            color=color_int,
        )

        if footer := embed_data.get("footer"):
            embed.set_footer(text=footer)
        if image_url := embed_data.get("image"):
            embed.set_image(url=image_url)
        if thumbnail_url := embed_data.get("thumbnail"):
            embed.set_thumbnail(url=thumbnail_url)
        for field in embed_data.get("fields", []):
            embed.add_field(
                name=field.get("name", ""),
                value=field.get("value", ""),
                inline=field.get("inline", False),
            )

        conf_id = metadata.get("conf_id")
        view = (
            ToolConfirmationView(
                conf_id, chat_id, self.bus, self.config, getattr(self, "agent", None)
            )
            if conf_id
            else None
        )

        await target.send(embed=embed, view=view)
        logger.info(f"[Discord] Embed sent to {_target_name(target)}")

    async def _send_file(self, target, metadata: dict) -> None:
        """Send a file attachment."""
        from pathlib import Path

        file_path = metadata.get("file_path")
        if not file_path:
            logger.error(
                "[Discord] 'file' message type missing 'file_path' in metadata."
            )
            return

        p = Path(file_path)
        if not p.exists():
            logger.error(f"[Discord] File not found: {file_path}")
            return

        caption = metadata.get("caption", "")
        await target.send(content=caption or None, file=discord.File(p))
        logger.info(f"[Discord] File '{p.name}' sent to {_target_name(target)}")


def _target_name(target) -> str:
    """Return a human-readable name for a send target."""
    return (
        getattr(target, "name", None)
        or getattr(target, "display_name", None)
        or str(getattr(target, "id", "?"))
    )


def _split_message(content: str, chunk_size: int = _CHUNK_SIZE) -> list[str]:
    """
    Split a long message into chunks, preferring word boundaries.
    Falls back to hard splits only when a single word exceeds chunk_size.
    """
    if len(content) <= _MAX_MESSAGE_LEN:
        return [content]

    chunks = []
    while content:
        if len(content) <= chunk_size:
            chunks.append(content)
            break

        split_at = content.rfind("\n", 0, chunk_size)
        if split_at == -1:
            split_at = content.rfind(" ", 0, chunk_size)
        if split_at == -1:
            split_at = chunk_size

        chunks.append(content[:split_at].rstrip())
        content = content[split_at:].lstrip()

    return chunks
