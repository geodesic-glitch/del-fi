"""Tests for router.py — command parsing, !more cursor, edge cases."""

import sys
import os
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from router import MoreBuffer, Router


# --- MoreBuffer ---


def test_more_buffer_next_chunk():
    buf = MoreBuffer(["chunk1", "chunk2", "chunk3"], time.time())
    # cursor starts at 0 (first chunk already sent)
    c1 = buf.next_chunk()
    assert c1 is not None
    assert "chunk2" in c1
    assert "[!more]" in c1  # more chunks remain

    c2 = buf.next_chunk()
    assert c2 is not None
    assert "chunk3" in c2
    assert "[!more]" not in c2  # last chunk, no more indicator

    c3 = buf.next_chunk()
    assert c3 is None  # exhausted


def test_more_buffer_specific_chunk():
    buf = MoreBuffer(["one", "two", "three"], time.time())

    c = buf.get_chunk(2)  # 1-indexed
    assert c is not None
    assert "two" in c
    assert "[!more]" in c  # chunk 3 still exists

    c = buf.get_chunk(3)
    assert c is not None
    assert "three" in c
    assert "[!more]" not in c  # last chunk

    c = buf.get_chunk(4)
    assert c is None  # out of range

    c = buf.get_chunk(0)
    assert c is None  # 0 is invalid (1-indexed)


def test_more_buffer_expiry():
    # Buffer with old timestamp should be expired
    buf = MoreBuffer(["a", "b"], time.time() - 700)
    assert buf.expired

    buf2 = MoreBuffer(["a", "b"], time.time())
    assert not buf2.expired


def test_more_buffer_total_chunks():
    buf = MoreBuffer(["a", "b", "c", "d"], time.time())
    assert buf.total_chunks == 4


# --- Router command parsing (with mock RAG engine) ---


class MockRAG:
    """Minimal mock for RAGEngine."""

    def __init__(self):
        self._ollama_available = True
        self._rag_available = True
        self._doc_count = 5

    @property
    def available(self):
        return self._ollama_available

    @property
    def rag_available(self):
        return self._rag_available

    @property
    def doc_count(self):
        return self._doc_count

    def get_topics(self):
        return ["solar-power", "trail-guide", "first-aid"]

    def query(self, text, top_k=3):
        return []

    def generate(self, text, context_chunks=None, peer_context=None):
        return "Mock LLM response about your question."


def _make_router():
    """Create a Router with mock dependencies."""
    cfg = {
        "node_name": "TEST-NODE",
        "model": "test-model:3b",
        "max_response_bytes": 230,
        "rate_limit_seconds": 30,
        "response_cache_ttl": 300,
        "personality": "Helpful test assistant.",
        "knowledge_folder": "/tmp/test-knowledge",
        "_seen_senders_file": "/tmp/test-seen-senders.txt",
        "_base_dir": "/tmp",
        "_cache_dir": "/tmp/cache",
        "_gossip_dir": "/tmp/gossip",
        "_vectorstore_dir": "/tmp/vectorstore",
        "mesh_knowledge": None,
        "embedding_model": "nomic-embed-text",
        "ollama_host": "http://localhost:11434",
        "ollama_timeout": 120,
    }
    rag = MockRAG()
    return Router(cfg, rag, mesh_knowledge=None)


def test_cmd_ping():
    router = _make_router()
    response = router.route("!sender1", "!ping")
    assert "pong" in response.lower()
    assert "TEST-NODE" in response


def test_cmd_help():
    router = _make_router()
    response = router.route("!sender1", "!help")
    assert "TEST-NODE" in response
    assert "!help" in response
    assert "!topics" in response


def test_cmd_status():
    router = _make_router()
    response = router.route("!sender1", "!status")
    assert "TEST-NODE" in response
    assert "test-model:3b" in response
    assert "5 docs" in response


def test_cmd_topics():
    router = _make_router()
    response = router.route("!sender1", "!topics")
    assert "solar-power" in response
    assert "trail-guide" in response
    assert "first-aid" in response


def test_cmd_unknown():
    router = _make_router()
    response = router.route("!sender1", "!foobar")
    assert "Unknown command" in response
    assert "!help" in response


def test_cmd_peers_no_mesh():
    router = _make_router()
    response = router.route("!sender1", "!peers")
    assert "not configured" in response.lower()


def test_cmd_more_no_buffer():
    router = _make_router()
    response = router.route("!sender1", "!more")
    assert "No pending" in response


def test_cmd_more_with_buffer():
    router = _make_router()
    # Manually inject a buffer
    router._more_buffers["!sender1"] = MoreBuffer(
        ["first chunk", "second chunk", "third chunk"], time.time()
    )
    response = router.route("!sender1", "!more")
    assert "second chunk" in response

    response2 = router.route("!sender1", "!more")
    assert "third chunk" in response2

    response3 = router.route("!sender1", "!more")
    assert "End of response" in response3


def test_cmd_more_specific_chunk():
    router = _make_router()
    router._more_buffers["!sender1"] = MoreBuffer(
        ["one", "two", "three"], time.time()
    )
    response = router.route("!sender1", "!more 2")
    assert "two" in response


def test_cmd_more_invalid_chunk():
    router = _make_router()
    router._more_buffers["!sender1"] = MoreBuffer(["one", "two"], time.time())
    response = router.route("!sender1", "!more 5")
    assert "No chunk 5" in response


def test_cmd_case_insensitive():
    router = _make_router()
    r1 = router.route("!sender1", "!PING")
    assert "pong" in r1.lower()

    r2 = router.route("!sender1", "!Help")
    assert "!help" in r2


# --- Greeting detection ---


def test_greeting_first_contact():
    router = _make_router()
    response = router.route("!newsender", "hello")
    assert "Hi from TEST-NODE" in response


def test_greeting_returning_user():
    router = _make_router()
    # First contact
    router.route("!sender1", "hello")
    # Second time: should go to LLM, not greeting handler
    response = router.route("!sender1", "hello")
    # Should be an LLM response, not the intro message
    assert "Mock LLM response" in response or "Hi from" not in response


# --- Empty / whitespace ---


def test_empty_message():
    router = _make_router()
    response = router.route("!sender1", "")
    assert response is None


def test_whitespace_message():
    router = _make_router()
    response = router.route("!sender1", "   ")
    assert response is None


# --- Run tests ---

if __name__ == "__main__":
    import inspect

    passed = 0
    failed = 0

    for name, func in sorted(
        inspect.getmembers(sys.modules[__name__], inspect.isfunction)
    ):
        if name.startswith("test_"):
            try:
                func()
                passed += 1
                print(f"  ✓ {name}")
            except AssertionError as e:
                failed += 1
                print(f"  ✗ {name}: {e}")
            except Exception as e:
                failed += 1
                print(f"  ✗ {name}: {type(e).__name__}: {e}")

    print(f"\n{passed} passed, {failed} failed")
    sys.exit(1 if failed else 0)
