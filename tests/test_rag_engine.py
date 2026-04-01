import unittest


class TestRagEngine(unittest.TestCase):
    def test_get_recent_returns_empty_list_when_session_trace_bucket_is_none(self):
        from core.rag_engine import RagEngine

        rag = RagEngine(lambda text, limit: text, lambda value: value)
        rag._recent_rag_traces["web:test"] = None

        self.assertEqual(rag.get_recent("web:test"), [])

    def test_get_recent_skips_invalid_trace_buckets_when_merging_sessions(self):
        from core.rag_engine import RagEngine

        rag = RagEngine(lambda text, limit: text, lambda value: value)
        rag._recent_rag_traces["web:bad"] = None
        rag._recent_rag_traces["web:good"] = [{"ts": 2, "query": "hi"}]

        merged = rag.get_recent()

        self.assertEqual(len(merged), 1)
        self.assertEqual(merged[0]["session_key"], "web:good")
        self.assertEqual(merged[0]["query"], "hi")
