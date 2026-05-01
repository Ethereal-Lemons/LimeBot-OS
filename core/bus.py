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
        from core.delivery_tracker import get_delivery_tracker

        tracker = get_delivery_tracker()
        self._running = True
        while self._running:
            try:
                msg = await self.outbound.get()
                subscribers = self._outbound_subscribers.get(msg.channel, [])

                # Determine message kind for delivery tracking
                msg_kind = "text"
                if msg.media:
                    msg_kind = "media"
                elif msg.metadata.get("embed"):
                    msg_kind = "embed"

                for callback in subscribers:
                    delivery_id = ""
                    try:
                        delivery_id = await tracker.track_delivery(
                            channel=msg.channel,
                            target=msg.chat_id,
                            message_kind=msg_kind,
                        )
                        await tracker.mark_sending(delivery_id)
                        await callback(msg)
                        await tracker.mark_sent(delivery_id)
                    except Exception as e:
                        logger.error(f"[BUS] Error dispatching to {msg.channel}: {e}")
                        if delivery_id:
                            await tracker.mark_failed(delivery_id, str(e))
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
