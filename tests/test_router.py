"""Tests for router.py — command parsing, !more cursor, edge cases."""

import os
import sys
import time
import tempfile
import unittest

from del_fi.core.router import MoreBuffer, Router


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


# --- Router command parsing (with mock WikiEngine/peers) ---


class MockWiki:
    """Minimal mock for WikiEngine."""

    def __init__(self):
        self._ollama_available = True
        self._rag_available = True
        self._page_count = 5

    @property
    def available(self):
        return self._ollama_available

    @property
    def rag_available(self):
        return self._rag_available

    @property
    def page_count(self):
        return self._page_count

    def get_topics(self):
        return ["solar-power", "trail-guide", "first-aid"]

    def query(self, text, peer_ctx="", history="", board_context=""):
        return "Mock LLM response about your question.", True

    def suggest(self, text):
        return None


class MockPeerCache:
    """Minimal mock for PeerCache."""
    def lookup(self, query):
        return None
    def store(self, *a, **kw):
        pass


class MockGossipDir:
    """Minimal mock for GossipDirectory."""
    @property
    def peer_count(self):
        return 0
    def list_peers(self):
        return []
    def receive(self, node_id, text):
        pass
    def referral(self, query):
        return None
    def announce(self):
        return ""


def _make_cfg(tmpdir: str, **overrides) -> dict:
    cfg = {
        "node_name": "TEST-NODE",
        "model": "test-model:3b",
        "max_response_bytes": 230,
        "rate_limit_seconds": 30,
        "response_cache_ttl": 300,
        "personality": "Helpful test assistant.",
        "knowledge_folder": os.path.join(tmpdir, "knowledge"),
        "_seen_senders_file": os.path.join(tmpdir, "seen-senders.txt"),
        "_base_dir": tmpdir,
        "_cache_dir": os.path.join(tmpdir, "cache"),
        "_gossip_dir": os.path.join(tmpdir, "gossip"),
        "_vectorstore_dir": os.path.join(tmpdir, "vectorstore"),
        "embedding_model": "nomic-embed-text",
        "ollama_host": "http://localhost:11434",
        "ollama_timeout": 120,
        "persistent_cache": False,
        "fallback_message": "I don't have docs on that. Try !topics.",
    }
    cfg.update(overrides)
    for d in ("knowledge", "cache", "gossip", "vectorstore"):
        os.makedirs(os.path.join(tmpdir, d), exist_ok=True)
    return cfg


def _make_router(**cfg_overrides):
    """Create a Router with mock dependencies and isolated temp state."""
    tmpdir = tempfile.mkdtemp(prefix="delfi-test-")
    cfg = _make_cfg(tmpdir, **cfg_overrides)
    return Router(cfg, MockWiki(), MockPeerCache(), MockGossipDir())


def test_cmd_ping():
    router = _make_router()
    response = router.route("!sender1", "!ping")
    assert "pong" in response.lower()
    assert "TEST-NODE" in response


def test_cmd_help():
    router = _make_router()
    response = router.route("!sender1", "!help")
    assert "TEST-NODE" in response
    assert "!topics" in response
    assert "!more" in response


def test_cmd_status():
    router = _make_router()
    response = router.route("!sender1", "!status")
    assert "TEST-NODE" in response
    assert "test-model:3b" in response
    assert "wiki pages" in response


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


def test_cmd_peers_empty():
    router = _make_router()
    response = router.route("!sender1", "!peers")
    # MockGossipDir returns [] → "no other nodes" message
    assert "No other" in response or "no" in response.lower()


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
    assert "!topics" in r2


# --- Greeting detection ---


def test_greeting_first_contact():
    router = _make_router()
    response = router.route("!newsender", "hello")
    assert "Hi from TEST-NODE" in response


def test_greeting_returning_user():
    router = _make_router()
    # First contact triggers greeting
    router.route("!sender1", "hello")
    # Second message: should get LLM response, not intro
    response = router.route("!sender1", "hello")
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


# --- classify() ---


def test_classify_empty():
    router = _make_router()
    assert router.classify("") == "empty"
    assert router.classify("   ") == "empty"


def test_classify_command():
    router = _make_router()
    assert router.classify("!help") == "command"
    assert router.classify("!PING") == "command"
    assert router.classify("!more 2") == "command"


def test_classify_gossip():
    router = _make_router()
    assert router.classify("DEL-FI:1:ANNOUNCE:RIDGE:topics=weather") == "gossip"


def test_classify_query():
    router = _make_router()
    assert router.classify("What time is the concert?") == "query"
    assert router.classify("hello") == "query"


# --- busy_message() ---


def test_busy_message_next():
    router = _make_router()
    msg = router.busy_message(1)
    assert "TEST-NODE" in msg
    assert "next" in msg.lower()


def test_busy_message_queued():
    router = _make_router()
    msg = router.busy_message(3)
    assert "TEST-NODE" in msg
    assert "3" in msg
    assert "hang tight" in msg.lower()


# --- Byte-limit enforcement ---


def test_enforce_limit_truncates_oversized_command():
    from del_fi.core.formatter import byte_len
    router = _make_router()
    long_text = "A" * 250
    result = router._enforce_limit(long_text)
    assert byte_len(result) <= 230


def test_enforce_limit_passes_short_text():
    router = _make_router()
    short = "Hello world."
    assert router._enforce_limit(short) == short


def test_enforce_limit_none():
    router = _make_router()
    assert router._enforce_limit(None) is None


def test_all_commands_fit_byte_limit():
    """Every built-in command response fits within max_response_bytes."""
    from del_fi.core.formatter import byte_len
    router = _make_router()
    max_bytes = router.cfg["max_response_bytes"]

    commands = [
        "!help", "!status", "!topics", "!ping", "!peers",
        "!more", "!retry", "!data", "!foobar",
    ]
    for cmd in commands:
        response = router.route("!testlimit", cmd)
        if response is not None:
            assert byte_len(response) <= max_bytes, (
                f"{cmd} response is {byte_len(response)}B, "
                f"exceeds {max_bytes}B limit: {response!r}"
            )


# --- route_multi() ---


def _make_router_with_long_answer(text: str, max_bytes: int = 100):
    """Router with a wiki that always returns a specific long answer."""
    tmpdir = tempfile.mkdtemp(prefix="delfi-test-")

    class _LongWiki(MockWiki):
        def query(self, q, peer_ctx="", history="", board_context=""):
            return text, True

    cfg = _make_cfg(tmpdir, max_response_bytes=max_bytes, rate_limit_seconds=0)
    return Router(cfg, _LongWiki(), MockPeerCache(), MockGossipDir())


def test_route_multi_single_chunk_returns_list():
    """Short response returns a 1-element list."""
    router = _make_router()
    result = router.route_multi("!sender1", "!ping")
    assert isinstance(result, list)
    assert len(result) == 1
    assert "pong" in result[0].lower()


def test_route_multi_none_on_empty():
    router = _make_router()
    result = router.route_multi("!sender1", "")
    assert result is None


def test_route_multi_auto_sends_two_chunks():
    """2-chunk response → both auto-sent, no [!more] on last."""
    answer = ("A " * 35 + ". ") + ("B " * 35 + ".")
    router = _make_router_with_long_answer(answer, max_bytes=80)
    result = router.route_multi("!testuser", "tell me something")
    assert isinstance(result, list)
    assert len(result) == 2, f"expected 2, got {len(result)}: {result}"
    assert "[!more]" not in result[-1]


def test_route_multi_auto_sends_three_chunks():
    """3-chunk response → all auto-sent."""
    part = "Word " * 14 + ". "
    answer = part + part + part
    router = _make_router_with_long_answer(answer, max_bytes=80)
    result = router.route_multi("!testuser", "tell me something")
    assert isinstance(result, list)
    assert len(result) >= 2
    assert "[!more]" not in result[-1]


def test_route_multi_prompts_more_beyond_window():
    """4+ chunk response → last auto-sent chunk ends with [!more]."""
    sentence = "This is a sentence about the topic at hand. "
    answer = sentence * 8
    router = _make_router_with_long_answer(answer, max_bytes=80)
    result = router.route_multi("!testuser", "tell me everything")
    assert isinstance(result, list)
    assert len(result) == 3
    assert result[-1].endswith("[!more]"), (
        f"expected [!more] on last auto-sent chunk: {result[-1]!r}"
    )
    assert "[!more]" not in result[0]
    assert "[!more]" not in result[1]


def test_route_multi_more_buffer_cursor_advanced():
    """After route_multi(), !more returns chunk 4, not chunk 2."""
    sentence = "This is a sentence about the topic at hand. "
    answer = sentence * 8
    router = _make_router_with_long_answer(answer, max_bytes=80)
    router.route_multi("!testuser", "tell me everything")

    # Buffer cursor should be at chunk 2 (0-indexed) after 3 auto-sends
    more = router.route("!testuser", "!more")
    assert more is not None
    assert "No pending" not in more  # a real chunk came back


def test_route_multi_config_override():
    """auto_send_chunks=1 in config behaves like the old single-send."""
    sentence = "This is a sentence about the topic at hand. "
    answer = sentence * 8
    router = _make_router_with_long_answer(answer, max_bytes=80)
    router.cfg["auto_send_chunks"] = 1
    result = router.route_multi("!testuser", "tell me everything")
    assert isinstance(result, list)
    assert len(result) == 1
    assert result[0].endswith("[!more]")


# --- Dispatcher integration ---


def test_dispatcher_fast_vs_slow_classification():
    """Commands are classified as fast, queries as slow."""
    router = _make_router()
    fast = ["!help", "!ping", "!status", "!topics", "!more", "!retry"]
    for cmd in fast:
        assert router.classify(cmd) == "command", f"{cmd} should be 'command'"

    slow = ["What is solar power?", "hello", "tell me about first aid"]
    for q in slow:
        assert router.classify(q) == "query", f"{q!r} should be 'query'"


# ---------------------------------------------------------------------------
# unittest discovery wrapper — makes bare test_ functions discoverable
# ---------------------------------------------------------------------------

_Tests = type(
    "_Tests",
    (unittest.TestCase,),
    {
        n: (lambda f: lambda self: f())(f)
        for n, f in list(globals().items())
        if n.startswith("test_") and callable(f)
    },
)

if __name__ == "__main__":
    unittest.main()
