import unittest


class TestTurnHistoryService(unittest.IsolatedAsyncioTestCase):
    async def test_flush_is_debounced_and_saves_safe_copies(self):
        from core.turn_history_service import TurnHistoryService

        histories = {"web_test": [{"role": "system", "content": "system"}]}
        dirty = {}
        last_flush = {"web_test": 100.0}
        saved = []
        now = [104.0]

        async def save_history(session_key, entries):
            saved.append((session_key, entries))

        service = TurnHistoryService(
            histories=histories,
            dirty=dirty,
            last_flush=last_flush,
            save_history=save_history,
            safe_content=lambda content: "safe copy" if content == "private" else content,
            flush_interval=lambda: 5.0,
            monotonic=lambda: now[0],
        )
        service.append("web_test", {"role": "user", "content": "private"})

        self.assertFalse(await service.flush("web_test"))
        self.assertTrue(dirty["web_test"])
        self.assertEqual(saved, [])

        now[0] = 105.0
        self.assertTrue(await service.flush("web_test"))
        self.assertFalse(dirty["web_test"])
        self.assertEqual(last_flush["web_test"], 105.0)
        self.assertEqual(saved[0][1][-1]["content"], "safe copy")
        self.assertEqual(histories["web_test"][-1]["content"], "private")

    async def test_force_flush_bypasses_interval_and_missing_history_is_ignored(self):
        from core.turn_history_service import TurnHistoryService

        saved = []

        async def save_history(session_key, entries):
            saved.append(session_key)

        service = TurnHistoryService(
            histories={"web_test": []},
            dirty={"web_test": True},
            last_flush={"web_test": 100.0},
            save_history=save_history,
            safe_content=lambda content: content,
            flush_interval=lambda: 60.0,
            monotonic=lambda: 101.0,
        )

        self.assertTrue(await service.flush("web_test", force=True))
        self.assertEqual(saved, ["web_test"])
        self.assertFalse(await service.flush("missing", force=True))
