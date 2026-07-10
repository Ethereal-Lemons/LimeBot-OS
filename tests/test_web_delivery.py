import asyncio
import unittest

from channels.web import WebChannel


class _Socket:
    def __init__(self, *, block=False):
        self.block = block
        self.payloads = []

    async def send_text(self, payload):
        if self.block:
            await asyncio.Event().wait()
        self.payloads.append(payload)


class WebDeliveryTests(unittest.IsolatedAsyncioTestCase):
    def test_ephemeral_timeout_is_shorter_than_durable(self):
        self.assertLess(WebChannel._delivery_timeout("chunk"), WebChannel._delivery_timeout("message"))

    async def test_stale_connection_is_removed_after_first_timeout(self):
        channel = object.__new__(WebChannel)
        stale = _Socket(block=True)
        healthy = _Socket()
        channel.active_connections = {stale, healthy}
        original = WebChannel._delivery_timeout
        channel._delivery_timeout = lambda _kind: 0.01
        try:
            await channel._broadcast_chat_payload("first", "chunk")
            self.assertNotIn(stale, channel.active_connections)
            await channel._broadcast_chat_payload("second", "chunk")
            self.assertEqual(healthy.payloads, ["first", "second"])
        finally:
            channel._delivery_timeout = original


if __name__ == "__main__":
    unittest.main()
