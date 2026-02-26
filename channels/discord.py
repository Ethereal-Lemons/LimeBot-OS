"""Discord channel implementation."""

import asyncio
import json
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
        self._style_overrides = getattr(self.config, "style_overrides", {}) or {}
        self._signature = getattr(self.config, "signature", "") or ""
        self._emoji_set = getattr(self.config, "emoji_set", ["🍋", "⚙️", "✨"])
        self._verbosity_limits = getattr(
            self.config,
            "verbosity_limits",
            {"short": 600, "medium": 1800, "long": 4000},
        )
        self._tone_prefixes = getattr(
            self.config,
            "tone_prefixes",
            {
                "neutral": "",
                "friendly": "Hey!",
                "direct": "Heads up:",
                "formal": "Note:",
            },
        )
        self._embed_theme = getattr(self.config, "embed_theme", {}) or {}
        self._nickname_templates = getattr(self.config, "nickname_templates", {}) or {}
        self._avatar_overrides = getattr(self.config, "avatar_overrides", {}) or {}

        self._register_events()

    def set_agent(self, agent) -> None:
        self.agent = agent

    def _get_style_for_target(self, target) -> dict:
        style = {}
        overrides = self._style_overrides or {}
        default = overrides.get("default") or {}
        style.update(default if isinstance(default, dict) else {})

        guild_id = None
        channel_id = None
        try:
            channel_id = str(getattr(target, "id", "") or "")
            guild_id = (
                str(getattr(target, "guild", None).id)
                if getattr(target, "guild", None)
                else None
            )
        except Exception:
            guild_id = None

        guilds = overrides.get("guilds") or {}
        channels = overrides.get("channels") or {}
        if guild_id and isinstance(guilds, dict) and guild_id in guilds:
            if isinstance(guilds[guild_id], dict):
                style.update(guilds[guild_id])
        if channel_id and isinstance(channels, dict) and channel_id in channels:
            if isinstance(channels[channel_id], dict):
                style.update(channels[channel_id])
        return style

    def _apply_style(self, content: str, target) -> str:
        if not content:
            return content

        import re

        style = self._get_style_for_target(target)
        text = content.strip()

        prefix = style.get("prefix") or ""
        tone = style.get("tone")
        if not prefix and tone:
            prefix = self._tone_prefixes.get(tone, "")
        if prefix:
            text = f"{prefix} {text}"

        emoji_usage = (style.get("emoji_usage") or "light").lower()
        emoji_set = style.get("emoji_set") or self._emoji_set
        emoji = emoji_set[0] if emoji_set else "🍋"

        if emoji_usage == "none":
            text = re.sub(
                r"[\U0001F300-\U0001FAFF\U00002700-\U000027BF\U0001F1E6-\U0001F1FF]+",
                "",
                text,
            ).strip()
        elif emoji_usage in ("light", "heavy"):
            if emoji not in text:
                if emoji_usage == "heavy":
                    text = f"{emoji} {text} {emoji}"
                else:
                    text = f"{text} {emoji}"

        max_len = style.get("max_length")
        if not max_len:
            verbosity = (style.get("verbosity") or "medium").lower()
            max_len = self._verbosity_limits.get(verbosity)
        if isinstance(max_len, int) and max_len > 0 and len(text) > max_len:
            text = text[: max_len - 1].rstrip() + "…"

        signature = style.get("signature")
        if signature is None:
            signature = self._signature
        if signature:
            text = f"{text}\n— {signature}"

        suffix = style.get("suffix") or ""
        if suffix:
            text = f"{text}\n{suffix}"

        return text

    def _get_theme_color(self, target, fallback: int) -> int:
        theme = self._embed_theme or {}
        default = theme.get("default")
        guilds = theme.get("guilds") or {}
        color_str = default
        try:
            guild_id = (
                str(getattr(target, "guild", None).id)
                if getattr(target, "guild", None)
                else None
            )
            if guild_id and guild_id in guilds:
                color_str = guilds[guild_id]
        except Exception:
            color_str = default

        if isinstance(color_str, str) and color_str.startswith("#"):
            try:
                return int(color_str.lstrip("#"), 16)
            except ValueError:
                return fallback
        if isinstance(color_str, int):
            return color_str
        return fallback

    async def _apply_guild_profile_overrides(self) -> None:
        if not self.client.user:
            return

        async def _fetch_avatar_bytes(url: str) -> bytes | None:
            import urllib.request
            import asyncio as _asyncio

            def _fetch():
                with urllib.request.urlopen(url, timeout=10) as resp:
                    return resp.read()

            try:
                return await _asyncio.to_thread(_fetch)
            except Exception:
                return None

        nick_cfg = self._nickname_templates or {}
        avatar_cfg = self._avatar_overrides or {}

        for guild in self.client.guilds:
            try:
                member = guild.get_member(self.client.user.id)
                if not member:
                    member = await guild.fetch_member(self.client.user.id)
            except Exception:
                member = None

            nick_template = None
            if isinstance(nick_cfg, dict):
                nick_template = nick_cfg.get("guilds", {}).get(str(guild.id)) or nick_cfg.get("default")
            if nick_template and member:
                nickname = nick_template.format(
                    guild=guild.name, bot=self.client.user.name
                )
                try:
                    await member.edit(nick=nickname)
                    logger.info(f"[Discord] Set nickname in '{guild.name}' to '{nickname}'")
                except Exception as e:
                    logger.warning(f"[Discord] Failed to set nickname in '{guild.name}': {e}")

            avatar_url = None
            if isinstance(avatar_cfg, dict):
                avatar_url = avatar_cfg.get("guilds", {}).get(str(guild.id))
            if avatar_url:
                avatar_bytes = await _fetch_avatar_bytes(avatar_url)
                if avatar_bytes:
                    try:
                        if member and hasattr(member, "edit"):
                            await member.edit(avatar=avatar_bytes)
                            logger.info(f"[Discord] Set guild avatar in '{guild.name}'")
                        else:
                            await self.client.user.edit(avatar=avatar_bytes)
                            logger.info("[Discord] Set global avatar (fallback).")
                    except Exception as e:
                        logger.warning(f"[Discord] Failed to set avatar in '{guild.name}': {e}")

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
            await self._apply_guild_profile_overrides()

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
            embed = self._build_tool_embed(
                target=target,
                status=status,
                tool_name=tool_name,
                args=metadata.get("args"),
                result=None,
            )
            message = await target.send(embed=embed)
            if tc_id:
                self._tool_messages[tc_id] = message
        elif status == "completed":
            if tc_id and tc_id in self._tool_messages:
                message = self._tool_messages.pop(tc_id)
                embed = self._build_tool_embed(
                    target=target,
                    status=status,
                    tool_name=tool_name,
                    args=metadata.get("args"),
                    result=metadata.get("result", ""),
                )
                await message.edit(embed=embed)
        elif status == "error":
            if tc_id and tc_id in self._tool_messages:
                message = self._tool_messages.pop(tc_id)
                embed = self._build_tool_embed(
                    target=target,
                    status=status,
                    tool_name=tool_name,
                    args=metadata.get("args"),
                    result=metadata.get("result", "Unknown error"),
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
            elif msg_type == "notification":
                if metadata.get("kind") == "github_pr" and metadata.get("data"):
                    await self._send_github_pr_embed(target, msg.content, metadata)
                else:
                    await self._send_text(target, msg.content)
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

        content = self._apply_style(content, target)

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

    def _apply_embed_footer(self, embed: discord.Embed, target) -> None:
        style = self._get_style_for_target(target)
        signature = style.get("signature")
        if signature is None:
            signature = self._signature
        if not signature:
            return
        if embed.footer and embed.footer.text:
            embed.set_footer(text=f"{embed.footer.text} • {signature}")
        else:
            embed.set_footer(text=signature)

    def _build_tool_embed(
        self, target, status: str, tool_name: str, args: dict | None, result: str | None
    ) -> discord.Embed:
        status = status or "running"
        status_title = {
            "running": "🛠️ Tool Running",
            "completed": "✅ Tool Completed",
            "error": "❌ Tool Failed",
        }.get(status, "🛠️ Tool Update")

        base_color = 0x5865F2
        if status == "completed":
            base_color = 0x57F287
        elif status == "error":
            base_color = 0xED4245
        color = self._get_theme_color(target, base_color)

        embed = discord.Embed(
            title=f"{status_title}: `{tool_name}`",
            color=color,
        )
        if self.client.user:
            try:
                embed.set_author(
                    name="LimeBot Tools",
                    icon_url=self.client.user.display_avatar.url,
                )
            except Exception:
                pass

        if args:
            try:
                args_preview = json.dumps(args, ensure_ascii=False)[:800]
            except Exception:
                args_preview = str(args)[:800]
            embed.add_field(name="Args", value=f"```\n{args_preview}\n```", inline=False)

        if result:
            result_preview = str(result)[:900]
            embed.add_field(
                name="Result",
                value=f"```\n{result_preview}\n```",
                inline=False,
            )

        self._apply_embed_footer(embed, target)
        return embed

    async def _send_github_pr_embed(self, target, content: str, metadata: dict) -> None:
        data = metadata.get("data") or {}
        title = data.get("title") or "Pull Request"
        url = data.get("url") or ""
        repo = data.get("repo") or ""
        labels = data.get("labels") or []
        reviewers = data.get("reviewers") or []
        head = data.get("head") or ""
        base = data.get("base") or ""

        desc = content or "GitHub PR created."
        if url:
            desc = f"[Open PR]({url})"

        embed = discord.Embed(
            title=f"✅ PR Created: {title}",
            description=desc,
            color=self._get_theme_color(target, 0x57F287),
        )
        if repo:
            embed.add_field(name="Repo", value=repo, inline=True)
        if head:
            embed.add_field(name="Head", value=head, inline=True)
        if base:
            embed.add_field(name="Base", value=base, inline=True)
        if labels:
            embed.add_field(name="Labels", value=", ".join(labels), inline=False)
        if reviewers:
            embed.add_field(name="Reviewers", value=", ".join(reviewers), inline=False)
        if url:
            embed.url = url

        self._apply_embed_footer(embed, target)
        await target.send(embed=embed)

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
        color_int = self._get_theme_color(target, color_int)

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

        self._apply_embed_footer(embed, target)

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
