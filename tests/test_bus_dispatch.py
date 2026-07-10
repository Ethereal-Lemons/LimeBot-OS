import asyncio
import tempfile
import threading
import time
import unittest

import core.delivery_tracker as delivery_module
from core.bus import MessageBus, _ChannelQueue
from core.events import OutboundMessage
from core.delivery_tracker import DeliveryTracker


class _Tracker:
    def __init__(self):
        self.tracked = []
        self.sent = []

    async def track_delivery(self, **values):
        self.tracked.append(values)
        return f"delivery-{len(self.tracked)}"

    async def mark_sending(self, delivery_id):
        return delivery_id

    async def mark_sent(self, delivery_id):
        self.sent.append(delivery_id)

    async def mark_failed(self, delivery_id, error):
        return None


class MessageBusDispatchTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.original_tracker = delivery_module._instance
        self.tracker = _Tracker()
        delivery_module._instance = self.tracker

    async def asyncTearDown(self):
        delivery_module._instance = self.original_tracker

    async def test_blocked_channel_does_not_block_other_channel(self):
        bus = MessageBus(outbound_channel_maxsize=8, durable_reserve=2)
        blocked = asyncio.Event()
        web_received = asyncio.Event()

        async def slow(_msg):
            await blocked.wait()

        async def web(_msg):
            web_received.set()

        bus.subscribe_outbound("discord", slow)
        bus.subscribe_outbound("web", web)
        router = asyncio.create_task(bus.dispatch_outbound())
        await bus.publish_outbound(OutboundMessage("discord", "1", "slow"))
        await bus.publish_outbound(OutboundMessage("web", "1", "fast", metadata={"type": "chunk"}))
        await asyncio.wait_for(web_received.wait(), 0.2)
        router.cancel()
        await asyncio.gather(router, return_exceptions=True)

    async def test_same_channel_durable_order_is_preserved(self):
        bus = MessageBus(outbound_channel_maxsize=4, durable_reserve=2)
        received = []
        complete = asyncio.Event()

        async def callback(msg):
            received.append(msg.content)
            if len(received) == 20:
                complete.set()

        bus.subscribe_outbound("web", callback)
        router = asyncio.create_task(bus.dispatch_outbound())
        for index in range(20):
            await bus.publish_outbound(OutboundMessage("web", "1", str(index)))
        await asyncio.wait_for(complete.wait(), 1.0)
        self.assertEqual(received, [str(index) for index in range(20)])
        router.cancel()
        await asyncio.gather(router, return_exceptions=True)

    async def test_ephemeral_bypasses_tracker_and_durable_is_tracked(self):
        bus = MessageBus()
        complete = asyncio.Event()
        seen = []

        async def callback(msg):
            seen.append(msg.content)
            if msg.content == "final":
                complete.set()

        bus.subscribe_outbound("web", callback)
        router = asyncio.create_task(bus.dispatch_outbound())
        await bus.publish_outbound(OutboundMessage("web", "1", "a", metadata={"type": "chunk"}))
        await bus.publish_outbound(OutboundMessage("web", "1", "final", metadata={"type": "message"}))
        await asyncio.wait_for(complete.wait(), 0.5)
        self.assertEqual(len(self.tracker.tracked), 1)
        self.assertEqual(len(self.tracker.sent), 1)
        self.assertEqual(seen[-1], "final")
        router.cancel()
        await asyncio.gather(router, return_exceptions=True)

    async def test_shutdown_cancels_blocked_workers_without_leaks(self):
        bus = MessageBus()
        started = asyncio.Event()

        async def blocked(_msg):
            started.set()
            await asyncio.Event().wait()

        bus.subscribe_outbound("web", blocked)
        router = asyncio.create_task(bus.dispatch_outbound())
        await bus.publish_outbound(OutboundMessage("web", "1", "final"))
        await asyncio.wait_for(started.wait(), 0.2)
        router.cancel()
        await asyncio.wait_for(asyncio.gather(router, return_exceptions=True), 0.2)
        self.assertEqual(bus._channel_workers, {})

    async def test_channel_buffer_is_bounded_and_only_durable_full_backpressures(self):
        queue = _ChannelQueue(maxsize=6, durable_reserve=2)
        for index in range(50):
            await queue.put(
                OutboundMessage(
                    "web", "1", str(index), metadata={"type": "progress", "message_id": str(index)}
                )
            )
        self.assertLessEqual(queue.qsize(), 4)
        for index in range(6):
            await queue.put(OutboundMessage("web", "1", f"durable-{index}"))
        self.assertEqual(queue.qsize(), 6)
        blocked_put = asyncio.create_task(queue.put(OutboundMessage("web", "1", "wait")))
        await asyncio.sleep(0)
        self.assertFalse(blocked_put.done())
        await queue.get()
        await asyncio.wait_for(blocked_put, 0.2)
        await queue.close()


if __name__ == "__main__":
    unittest.main()


class StreamingDeliveryEndToEndTests(unittest.IsolatedAsyncioTestCase):
    async def test_first_chunk_and_order_survive_other_channel_and_persistence_stalls(self):
        previous = delivery_module._instance
        with tempfile.TemporaryDirectory() as directory:
            tracker = DeliveryTracker(directory, debounce_seconds=0)
            writer_entered = threading.Event()
            writer_release = threading.Event()
            original_write = tracker._write_snapshot_sync

            def blocked_write(payload):
                writer_entered.set()
                writer_release.wait(1.0)
                original_write(payload)

            tracker._write_snapshot_sync = blocked_write
            delivery_module._instance = tracker
            bus = MessageBus(outbound_channel_maxsize=16, durable_reserve=4)
            other_release = asyncio.Event()
            web_parts = []
            first_chunk = asyncio.Event()
            final_seen = asyncio.Event()

            async def blocked_other(_msg):
                await other_release.wait()

            async def web(msg):
                web_parts.append(msg.content)
                if len(web_parts) == 1:
                    first_chunk.set()
                if (msg.metadata or {}).get("type") == "message":
                    final_seen.set()

            bus.subscribe_outbound("discord", blocked_other)
            bus.subscribe_outbound("web", web)
            router = asyncio.create_task(bus.dispatch_outbound())
            started = time.perf_counter()
            await bus.publish_outbound(OutboundMessage("discord", "d", "blocked"))
            for part in ("one", " ", "two"):
                await bus.publish_outbound(
                    OutboundMessage("web", "w", part, metadata={"type": "chunk", "message_id": "m1"})
                )
            await asyncio.wait_for(first_chunk.wait(), 0.2)
            self.assertLess(time.perf_counter() - started, 0.2)
            await bus.publish_outbound(
                OutboundMessage("web", "w", "one two", metadata={"type": "message", "message_id": "m1"})
            )
            await asyncio.wait_for(final_seen.wait(), 0.5)
            self.assertEqual("".join(web_parts[:-1]), "one two")
            self.assertEqual(web_parts[-1], "one two")
            web_deliveries = await tracker.list_deliveries(channel_filter="web")
            self.assertEqual(len(web_deliveries), 1)
            self.assertEqual(web_deliveries[0].message_kind, "text")
            self.assertTrue(writer_entered.wait(0.5))
            writer_release.set()
            await tracker.flush()
            router.cancel()
            await asyncio.gather(router, return_exceptions=True)
        delivery_module._instance = previous
