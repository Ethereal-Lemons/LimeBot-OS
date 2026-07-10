"""Async message queues with bounded, channel-isolated outbound delivery."""

import asyncio
from collections import deque
from typing import Awaitable, Callable

from loguru import logger

from core.events import InboundMessage, OutboundMessage


EPHEMERAL_OUTBOUND_TYPES = frozenset(
    {
        "typing",
        "stop_typing",
        "chunk",
        "thinking",
        "activity",
        "matching",
        "matching_clear",
        "progress",
    }
)
_CONCATENATED_EPHEMERAL_TYPES = frozenset({"chunk", "thinking"})


def outbound_message_type(msg: OutboundMessage) -> str:
    """Return the explicit outbound type; unknown types remain durable."""
    return str((msg.metadata or {}).get("type", "message"))


def is_ephemeral_outbound(msg: OutboundMessage) -> bool:
    return outbound_message_type(msg) in EPHEMERAL_OUTBOUND_TYPES


class _ChannelQueue:
    """Finite ordered buffer that sacrifices only coalescible ephemeral state."""

    def __init__(self, maxsize: int, durable_reserve: int):
        self.maxsize = max(2, maxsize)
        self.ephemeral_limit = max(1, self.maxsize - max(1, durable_reserve))
        self._items: deque[OutboundMessage] = deque()
        self._condition = asyncio.Condition()
        self._closed = False

    def _key(self, msg: OutboundMessage) -> tuple[str, str, str, str]:
        metadata = msg.metadata or {}
        return (
            msg.channel,
            str(msg.chat_id),
            str(metadata.get("message_id") or metadata.get("turn_id") or ""),
            outbound_message_type(msg),
        )

    def _coalesce(self, msg: OutboundMessage) -> bool:
        key = self._key(msg)
        for index in range(len(self._items) - 1, -1, -1):
            current = self._items[index]
            # Never move a later ephemeral update across a durable outcome.
            if not is_ephemeral_outbound(current):
                break
            if self._key(current) != key:
                continue
            if outbound_message_type(msg) in _CONCATENATED_EPHEMERAL_TYPES:
                current.content += msg.content
                current.metadata = {**current.metadata, **msg.metadata}
            else:
                self._items[index] = msg
            return True
        return False

    async def put(self, msg: OutboundMessage) -> None:
        async with self._condition:
            if self._closed:
                raise RuntimeError("channel queue is closed")
            ephemeral = is_ephemeral_outbound(msg)
            if ephemeral and self._coalesce(msg):
                return
            if ephemeral:
                ephemeral_count = sum(is_ephemeral_outbound(item) for item in self._items)
                if len(self._items) >= self.ephemeral_limit or ephemeral_count >= self.ephemeral_limit:
                    for index, item in enumerate(self._items):
                        if is_ephemeral_outbound(item):
                            del self._items[index]
                            break
                    else:
                        return
                self._items.append(msg)
                self._condition.notify()
                return

            while len(self._items) >= self.maxsize and not self._closed:
                for index, item in enumerate(self._items):
                    if is_ephemeral_outbound(item):
                        del self._items[index]
                        break
                else:
                    await self._condition.wait()
            if self._closed:
                raise RuntimeError("channel queue is closed")
            self._items.append(msg)
            self._condition.notify()

    async def get(self) -> OutboundMessage | None:
        async with self._condition:
            while not self._items and not self._closed:
                await self._condition.wait()
            if not self._items:
                return None
            msg = self._items.popleft()
            self._condition.notify_all()
            return msg

    async def close(self) -> None:
        async with self._condition:
            self._closed = True
            self._condition.notify_all()

    def qsize(self) -> int:
        return len(self._items)


class MessageBus:
    """Decouple channels and isolate outbound callbacks by destination channel."""

    def __init__(self, *, outbound_channel_maxsize: int = 128, durable_reserve: int = 32):
        self.inbound: asyncio.Queue[InboundMessage] = asyncio.Queue()
        self.outbound: asyncio.Queue[OutboundMessage] = asyncio.Queue(
            maxsize=max(8, outbound_channel_maxsize * 4)
        )
        self._outbound_subscribers: dict[
            str, list[Callable[[OutboundMessage], Awaitable[None]]]
        ] = {}
        self._channel_queues: dict[str, _ChannelQueue] = {}
        self._channel_workers: dict[str, asyncio.Task] = {}
        self._outbound_channel_maxsize = outbound_channel_maxsize
        self._durable_reserve = durable_reserve
        self._running = False

    async def publish_inbound(self, msg: InboundMessage) -> None:
        await self.inbound.put(msg)

    async def consume_inbound(self) -> InboundMessage:
        return await self.inbound.get()

    async def publish_outbound(self, msg: OutboundMessage) -> None:
        if self._running:
            # Direct routing makes durable backpressure local to this channel;
            # a saturated destination never blocks the global router.
            await self._ensure_worker(msg.channel).put(msg)
            return
        await self.outbound.put(msg)

    async def consume_outbound(self) -> OutboundMessage:
        return await self.outbound.get()

    def subscribe_outbound(
        self, channel: str, callback: Callable[[OutboundMessage], Awaitable[None]]
    ) -> None:
        self._outbound_subscribers.setdefault(channel, []).append(callback)

    def _ensure_worker(self, channel: str) -> _ChannelQueue:
        queue = self._channel_queues.get(channel)
        if queue is None:
            queue = _ChannelQueue(self._outbound_channel_maxsize, self._durable_reserve)
            self._channel_queues[channel] = queue
            self._channel_workers[channel] = asyncio.create_task(
                self._channel_worker(channel, queue), name=f"limebot-delivery-{channel}"
            )
        return queue

    async def _channel_worker(self, channel: str, queue: _ChannelQueue) -> None:
        from core.delivery_tracker import get_delivery_tracker

        tracker = get_delivery_tracker()
        while True:
            msg = await queue.get()
            if msg is None:
                return
            subscribers = self._outbound_subscribers.get(channel, [])
            ephemeral = is_ephemeral_outbound(msg)
            msg_kind = "media" if msg.media else "embed" if msg.metadata.get("embed") else "text"
            for callback in subscribers:
                delivery_id = ""
                try:
                    if not ephemeral:
                        delivery_id = await tracker.track_delivery(
                            channel=msg.channel, target=msg.chat_id, message_kind=msg_kind
                        )
                        await tracker.mark_sending(delivery_id)
                    await callback(msg)
                    if delivery_id:
                        await tracker.mark_sent(delivery_id)
                except asyncio.CancelledError:
                    raise
                except Exception as exc:
                    logger.error(f"[BUS] Error dispatching to {channel}: {exc}")
                    if delivery_id:
                        await tracker.mark_failed(delivery_id, str(exc))

    async def dispatch_outbound(self) -> None:
        """Route into bounded channel workers; a slow channel cannot stall another."""
        self._running = True
        try:
            while self._running:
                msg = await self.outbound.get()
                await self._ensure_worker(msg.channel).put(msg)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            logger.error(f"Error in dispatch loop: {exc}")
        finally:
            await self.shutdown()

    async def shutdown(self) -> None:
        self._running = False
        queues = list(self._channel_queues.values())
        workers = list(self._channel_workers.values())
        for queue in queues:
            await queue.close()
        for worker in workers:
            if not worker.done():
                worker.cancel()
        if workers:
            await asyncio.gather(*workers, return_exceptions=True)
        self._channel_queues.clear()
        self._channel_workers.clear()

    def stop(self) -> None:
        self._running = False

    @property
    def inbound_size(self) -> int:
        return self.inbound.qsize()

    @property
    def outbound_size(self) -> int:
        return self.outbound.qsize() + sum(q.qsize() for q in self._channel_queues.values())
