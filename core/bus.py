"""Async message queue for decoupled channel-agent communication."""

import asyncio
from typing import Callable, Awaitable

from loguru import logger
from core.events import InboundMessage, OutboundMessage


class MessageBus:
    """
    Async message bus that decouples chat channels from the agent core.

    Channels push messages to the inbound queue, and the agent processes
    them and pushes responses to the outbound queue.
    """

    def __init__(self):
        self.inbound: asyncio.Queue[InboundMessage] = asyncio.Queue()
        self.outbound: asyncio.Queue[OutboundMessage] = asyncio.Queue()
        self._outbound_subscribers: dict[
            str, list[Callable[[OutboundMessage], Awaitable[None]]]
        ] = {}
        self._running = False

    async def publish_inbound(self, msg: InboundMessage) -> None:
        """Publish a message from a channel to the agent."""
        await self.inbound.put(msg)

    async def consume_inbound(self) -> InboundMessage:
        """Consume the next inbound message (blocks until available)."""
        return await self.inbound.get()

    async def publish_outbound(self, msg: OutboundMessage) -> None:
        """Publish a response from the agent to channels."""
        await self.outbound.put(msg)

    async def consume_outbound(self) -> OutboundMessage:
        """Consume the next outbound message (blocks until available)."""
        return await self.outbound.get()

    def subscribe_outbound(
        self, channel: str, callback: Callable[[OutboundMessage], Awaitable[None]]
    ) -> None:
        """Subscribe to outbound messages for a specific channel."""
        if channel not in self._outbound_subscribers:
            self._outbound_subscribers[channel] = []
        self._outbound_subscribers[channel].append(callback)

    async def dispatch_outbound(self) -> None:
        """
        Dispatch outbound messages to subscribed channels.
        Run this as a background task.
        """
        self._running = True
        while self._running:
            try:
                msg = await self.outbound.get()
                logger.debug(
                    f"[BUS] Routing message to channel: {msg.channel}, chat_id: {msg.chat_id}"
                )
                subscribers = self._outbound_subscribers.get(msg.channel, [])
                logger.debug(
                    f"[BUS] Found {len(subscribers)} subscribers for channel '{msg.channel}'"
                )
                for callback in subscribers:
                    try:
                        await callback(msg)
                        logger.info(f"[BUS] ✓ Delivered to {msg.channel}")
                    except Exception as e:
                        logger.error(f"[BUS] Error dispatching to {msg.channel}: {e}")
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"Error in dispatch loop: {e}")
                await asyncio.sleep(1)

    def stop(self) -> None:
        """Stop the dispatcher loop."""
        self._running = False

    @property
    def inbound_size(self) -> int:
        """Number of pending inbound messages."""
        return self.inbound.qsize()

    @property
    def outbound_size(self) -> int:
        """Number of pending outbound messages."""
        return self.outbound.qsize()
