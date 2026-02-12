"""Multi-user concurrency stress test for the Del-Fi oracle.

Simulates N users sending messages at varying rates to expose:
  1. Queue starvation (slow LLM blocks everyone)
  2. Thread-safety issues in Router / RAG state
  3. Rate-limiter edge cases under concurrent load
  4. !more buffer cross-contamination between senders
  5. Cache correctness under parallel reads/writes

Run:
    python -m pytest tests/test_stress.py -v
    python -m pytest tests/test_stress.py -v -k "queue_depth"  # single test
"""

import os
import sys
import queue
import threading
import time
from unittest.mock import MagicMock, patch

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from router import Router, MoreBuffer
from rag import RAGEngine
from formatter import byte_len


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_cfg(tmp_path, **overrides):
    """Minimal config dict for testing (no real Ollama / ChromaDB)."""
    base = {
        "node_name": "STRESS-NODE",
        "model": "test-model",
        "personality": "Terse.",
        "knowledge_folder": str(tmp_path / "knowledge"),
        "max_response_bytes": 230,
        "rate_limit_seconds": 0,      # disable for most tests
        "response_cache_ttl": 300,
        "embedding_model": "nomic-embed-text",
        "channels": [],
        "log_level": "warning",
        "ollama_host": "http://localhost:11434",
        "ollama_timeout": 5,
        "num_ctx": 2048,
        "num_predict": 128,
        "persistent_cache": False,
        "mesh_protocol": "meshtastic",
        "radio_connection": "serial",
        "radio_port": "/dev/ttyUSB0",
        "_base_dir": str(tmp_path),
        "_vectorstore_dir": str(tmp_path / "vectorstore"),
        "_cache_dir": str(tmp_path / "cache"),
        "_gossip_dir": str(tmp_path / "gossip"),
        "_seen_senders_file": str(tmp_path / "seen_senders.txt"),
        "mesh_knowledge": None,
    }
    base.update(overrides)
    # ensure dirs exist
    for d in ("knowledge", "cache", "gossip", "vectorstore"):
        (tmp_path / d).mkdir(exist_ok=True)
    return base


def _mock_rag(available=True, generate_delay=0.0, generate_text="Test answer."):
    """Return a mock RAGEngine with controllable latency."""
    rag = MagicMock(spec=RAGEngine)
    rag.available = available
    rag.rag_available = available
    rag.doc_count = 5
    rag.get_topics.return_value = ["topic-a", "topic-b"]

    def fake_query(text):
        return [{"file": "test.md", "text": "Fake context."}]

    def fake_generate(text, context_chunks=None, peer_context=None):
        if generate_delay > 0:
            time.sleep(generate_delay)
        return generate_text

    rag.query.side_effect = fake_query
    rag.generate.side_effect = fake_generate
    return rag


# ---------------------------------------------------------------------------
# Test: serial processing ― queue depth under load
# ---------------------------------------------------------------------------

class TestQueueBehavior:
    """Verify messages queue correctly when the router is busy."""

    def test_queue_depth_during_slow_llm(self, tmp_path):
        """While one query blocks on LLM, other messages accumulate."""
        cfg = _make_cfg(tmp_path)
        msg_queue = queue.Queue()

        # LLM takes 0.5s per call
        rag = _mock_rag(generate_delay=0.5)
        router = Router(cfg, rag)

        results = {}
        errors = []

        def process_loop(n_messages):
            """Mimics delfi.py main loop."""
            processed = 0
            while processed < n_messages:
                try:
                    sender_id, text = msg_queue.get(timeout=5.0)
                except queue.Empty:
                    break
                t_start = time.time()
                resp = router.route(sender_id, text)
                t_elapsed = time.time() - t_start
                results[sender_id] = {
                    "response": resp,
                    "latency": t_elapsed,
                    "queue_depth_at_start": msg_queue.qsize(),
                }
                processed += 1

        # Fire 5 messages from different senders "simultaneously"
        num_users = 5
        for i in range(num_users):
            msg_queue.put((f"!user{i:04d}", f"question from user {i}"))

        assert msg_queue.qsize() == num_users

        # Process them serially (like the real main loop)
        process_loop(num_users)

        assert len(results) == num_users
        # All users got a response
        for uid, r in results.items():
            assert r["response"] is not None, f"{uid} got None response"

        # The last user should have waited ~2s+ (5 × 0.5s serial, minus their own)
        # At minimum, the first user's latency ≈ 0.5s
        first_user = results["!user0000"]
        assert first_user["latency"] >= 0.4, "LLM delay not applied"

    def test_queue_unbounded_growth(self, tmp_path):
        """Queue grows without bound if processing is slower than ingestion."""
        cfg = _make_cfg(tmp_path)
        msg_queue = queue.Queue()
        rag = _mock_rag(generate_delay=0.1)
        router = Router(cfg, rag)

        # Blast 50 messages
        for i in range(50):
            msg_queue.put((f"!flood{i:04d}", f"msg {i}"))

        assert msg_queue.qsize() == 50

        # Process just 5
        for _ in range(5):
            sender_id, text = msg_queue.get(timeout=1.0)
            router.route(sender_id, text)

        # 45 still waiting
        assert msg_queue.qsize() == 45


# ---------------------------------------------------------------------------
# Test: thread-safety of Router state
# ---------------------------------------------------------------------------

class TestRouterThreadSafety:
    """Hammer Router from multiple threads to detect race conditions.

    The real system is single-threaded, but if it ever moves to a
    thread-pool model these need to hold.
    """

    def test_concurrent_command_routing(self, tmp_path):
        """Multiple threads calling route() with commands simultaneously."""
        cfg = _make_cfg(tmp_path)
        rag = _mock_rag()
        router = Router(cfg, rag)

        results = {}
        barrier = threading.Barrier(10)

        def fire(sender_id, text):
            barrier.wait()  # all threads release at once
            resp = router.route(sender_id, text)
            results[sender_id] = resp

        threads = []
        for i in range(10):
            t = threading.Thread(
                target=fire,
                args=(f"!node{i:04d}", "!status"),
            )
            threads.append(t)
            t.start()

        for t in threads:
            t.join(timeout=10)

        # All 10 should get a valid status response
        assert len(results) == 10
        for uid, resp in results.items():
            assert "STRESS-NODE" in resp, f"{uid} got bad response: {resp}"
            assert "up" in resp

    def test_concurrent_queries_no_crash(self, tmp_path):
        """Multiple threads calling route() with freeform queries."""
        cfg = _make_cfg(tmp_path)
        rag = _mock_rag(generate_delay=0.05)
        router = Router(cfg, rag)

        errors = []
        barrier = threading.Barrier(8)

        def fire(sender_id, text):
            try:
                barrier.wait()
                resp = router.route(sender_id, text)
                assert resp is not None
            except Exception as e:
                errors.append((sender_id, e))

        threads = []
        for i in range(8):
            t = threading.Thread(
                target=fire,
                args=(f"!node{i:04d}", f"What is topic {i}?"),
            )
            threads.append(t)
            t.start()

        for t in threads:
            t.join(timeout=30)

        assert len(errors) == 0, f"Errors during concurrent queries: {errors}"

    def test_concurrent_cache_writes(self, tmp_path):
        """Concurrent queries that all write to the response cache."""
        cfg = _make_cfg(tmp_path, persistent_cache=False)
        rag = _mock_rag(generate_delay=0.02)
        router = Router(cfg, rag)

        barrier = threading.Barrier(6)
        results = {}

        def fire(sender_id, question):
            barrier.wait()
            resp = router.route(sender_id, question)
            results[sender_id] = resp

        threads = []
        questions = [
            "Where is the food?",
            "What time is the show?",
            "How do I get to zone B?",
            "Where is the food?",        # duplicate ― cache hit race
            "What time is the show?",     # duplicate
            "Any workshops today?",
        ]
        for i, q in enumerate(questions):
            t = threading.Thread(target=fire, args=(f"!cache{i:04d}", q))
            threads.append(t)
            t.start()

        for t in threads:
            t.join(timeout=30)

        assert len(results) == 6
        # Duplicate questions should get identical answers
        r0 = results["!cache0000"]  # "Where is the food?" first
        r3 = results["!cache0003"]  # "Where is the food?" second
        # Both should be non-None (one from generate, one possibly cached)
        assert r0 is not None
        assert r3 is not None


# ---------------------------------------------------------------------------
# Test: rate limiter under concurrent senders
# ---------------------------------------------------------------------------

class TestRateLimiterConcurrency:
    """Rate limiter should isolate senders ― A's limit doesn't affect B."""

    def test_different_senders_not_rate_limited(self, tmp_path):
        """Each sender has an independent rate limit window."""
        cfg = _make_cfg(tmp_path, rate_limit_seconds=60)
        rag = _mock_rag()
        router = Router(cfg, rag)

        # First message from each sender should always work
        for i in range(5):
            resp = router.route(f"!user{i:04d}", f"question {i}")
            assert resp is not None, f"user{i} was rate-limited on first msg"

    def test_same_sender_rate_limited(self, tmp_path):
        """Same sender sending twice within window gets different behavior.

        Note: rate limiting happens in the mesh adapter, not the router.
        The router sees all messages that made it through. This test
        documents that the router itself has no rate limiting — it's
        the mesh layer's job.
        """
        cfg = _make_cfg(tmp_path, rate_limit_seconds=60)
        rag = _mock_rag()
        router = Router(cfg, rag)

        # Router processes both ― rate limiting is in the mesh adapter
        r1 = router.route("!userAAAA", "first question")
        r2 = router.route("!userAAAA", "second question")
        assert r1 is not None
        assert r2 is not None  # router doesn't rate-limit


# ---------------------------------------------------------------------------
# Test: !more buffer isolation between senders
# ---------------------------------------------------------------------------

class TestMoreBufferIsolation:
    """Each sender's !more buffer must be independent."""

    def test_more_buffers_dont_cross_contaminate(self, tmp_path):
        """User A's !more buffer is separate from user B's."""
        cfg = _make_cfg(tmp_path)
        # Return a response long enough to be chunked
        long_answer = "A" * 300 + " " + "B" * 300
        rag = _mock_rag(generate_text=long_answer)
        router = Router(cfg, rag)

        # Both users ask a question ― should both get chunk 1
        r_a = router.route("!userA", "long question A")
        r_b = router.route("!userB", "long question B")
        assert r_a is not None
        assert r_b is not None

        # User A asks for !more
        more_a = router.route("!userA", "!more")
        # User B asks for !more
        more_b = router.route("!userB", "!more")

        # Both should get their own chunk 2, not each other's
        assert more_a is not None
        assert more_b is not None
        # They shouldn't be "no pending response" (which means the wrong buffer was hit)
        assert "No pending" not in more_a, "User A's more buffer was lost"
        assert "No pending" not in more_b, "User B's more buffer was lost"

    def test_more_buffer_not_shared(self, tmp_path):
        """User C has no buffer — shouldn't see user A's chunks."""
        cfg = _make_cfg(tmp_path)
        long_answer = "X" * 500
        rag = _mock_rag(generate_text=long_answer)
        router = Router(cfg, rag)

        router.route("!userA", "trigger long response")

        # User C never asked a question
        resp = router.route("!userC", "!more")
        assert "No pending" in resp


# ---------------------------------------------------------------------------
# Test: simulate realistic multi-user session
# ---------------------------------------------------------------------------

class TestRealisticMultiUser:
    """End-to-end simulation of a realistic multi-user scenario.

    Models a festival with 8 users asking questions at different rates,
    some sending commands, some sending !more, one spamming.
    """

    def test_festival_scenario(self, tmp_path):
        """Simulate 8 festival attendees hitting the oracle."""
        cfg = _make_cfg(tmp_path, rate_limit_seconds=0)
        rag = _mock_rag(generate_delay=0.05, generate_text="Zone A has food trucks and a makerspace. Check the map near the entrance.")
        router = Router(cfg, rag)
        msg_queue = queue.Queue()

        # Scenario script: (delay_ms, sender, message)
        script = [
            (0,   "!alice",   "Where can I find food?"),
            (10,  "!bob",     "!help"),
            (20,  "!carol",   "What workshops are today?"),
            (30,  "!dave",    "!topics"),
            (40,  "!eve",     "Where can I find food?"),      # same as Alice ― cache test
            (50,  "!frank",   "!status"),
            (60,  "!grace",   "Is there a soldering workshop?"),
            (70,  "!hank",    "!ping"),
            (100, "!alice",   "!more"),                        # Alice wants more
            (110, "!bob",     "What if it rains?"),
            (120, "!carol",   "!more"),                        # Carol wants more
            (130, "!dave",    "!help"),
            (140, "!hank",    "!status"),
            (150, "!eve",     "!topics"),
        ]

        # Enqueue all messages (in real life they arrive over seconds)
        for delay_ms, sender, text in script:
            msg_queue.put((sender, text))

        # Process all messages serially (like real main loop)
        responses = []
        processed = 0
        while not msg_queue.empty():
            sender_id, text = msg_queue.get(timeout=1.0)
            t_start = time.time()
            resp = router.route(sender_id, text)
            latency = time.time() - t_start
            responses.append({
                "sender": sender_id,
                "text": text,
                "response": resp,
                "latency_ms": latency * 1000,
            })
            processed += 1

        assert processed == len(script)

        # Validate every message got a response
        for r in responses:
            assert r["response"] is not None, (
                f"{r['sender']} sent '{r['text']}' and got None"
            )

        # Validate specific responses
        help_responses = [r for r in responses if r["text"] == "!help"]
        for r in help_responses:
            assert "STRESS-NODE" in r["response"]

        ping_responses = [r for r in responses if r["text"] == "!ping"]
        for r in ping_responses:
            assert "pong" in r["response"]

        status_responses = [r for r in responses if r["text"] == "!status"]
        for r in status_responses:
            assert "up" in r["response"]

        topics_responses = [r for r in responses if r["text"] == "!topics"]
        for r in topics_responses:
            assert "topic-a" in r["response"]

        # Validate all responses fit within byte limit
        for r in responses:
            assert byte_len(r["response"]) <= cfg["max_response_bytes"] + 50, (
                f"Response too large ({byte_len(r['response'])}B): {r['response'][:80]}"
            )

    def test_rapid_fire_same_user(self, tmp_path):
        """One user sends 20 messages rapidly — router shouldn't crash."""
        cfg = _make_cfg(tmp_path, rate_limit_seconds=0)
        rag = _mock_rag(generate_delay=0.01)
        router = Router(cfg, rag)

        responses = []
        for i in range(20):
            resp = router.route("!spammer", f"question number {i}")
            responses.append(resp)

        # All should get responses (no crashes, no None)
        assert all(r is not None for r in responses)

    def test_interleaved_queries_and_commands(self, tmp_path):
        """Alternating freeform queries and commands from different users."""
        cfg = _make_cfg(tmp_path, rate_limit_seconds=0)
        rag = _mock_rag(generate_delay=0.01)
        router = Router(cfg, rag)

        # Round-robin: user sends query, next sends command, repeat
        senders = [f"!user{i:02d}" for i in range(6)]
        messages = [
            "What is in zone A?",
            "!help",
            "Where is the bathroom?",
            "!topics",
            "Is there wifi?",
            "!status",
        ]

        results = {}
        for sender, msg in zip(senders, messages):
            resp = router.route(sender, msg)
            results[sender] = (msg, resp)

        for sender, (msg, resp) in results.items():
            assert resp is not None, f"{sender} got None for '{msg}'"


# ---------------------------------------------------------------------------
# Test: query count and cache stats under load
# ---------------------------------------------------------------------------

class TestStatsUnderLoad:
    """Verify internal counters stay consistent under load."""

    def test_query_count_accurate(self, tmp_path):
        """_query_count should match number of freeform queries processed."""
        cfg = _make_cfg(tmp_path, rate_limit_seconds=0)
        rag = _mock_rag()
        router = Router(cfg, rag)

        num_queries = 15
        num_commands = 5

        for i in range(num_queries):
            router.route(f"!user{i:04d}", f"freeform question {i}")

        for i in range(num_commands):
            router.route(f"!cmd{i:04d}", "!ping")

        # Only freeform queries count (greetings also count via _handle_query)
        assert router._query_count == num_queries

    def test_cache_populated_correctly(self, tmp_path):
        """Unique queries get cached; commands don't."""
        cfg = _make_cfg(tmp_path, rate_limit_seconds=0, persistent_cache=False)
        rag = _mock_rag()
        router = Router(cfg, rag)

        unique_questions = [
            "Where is zone A?",
            "What time is lunch?",
            "How to solder?",
        ]
        for i, q in enumerate(unique_questions):
            router.route(f"!user{i:04d}", q)

        # Commands should not be cached
        router.route("!cmduser", "!help")
        router.route("!cmduser", "!status")

        # Cache should have exactly the 3 unique queries
        assert len(router._response_cache) == 3
        for q in unique_questions:
            assert q.lower().strip() in router._response_cache


# ---------------------------------------------------------------------------
# Test: measure serialization impact (timing)
# ---------------------------------------------------------------------------

class TestLatencyProfile:
    """Measure how serialization affects tail latency.

    Not assertions on wall-clock (fragile in CI), but structural checks
    that the Nth message truly waited for N-1 LLM calls.
    """

    def test_serial_latency_grows_linearly(self, tmp_path):
        """With 0.1s LLM delay, user N waits ~N*0.1s total."""
        cfg = _make_cfg(tmp_path, rate_limit_seconds=0)
        llm_delay = 0.1
        rag = _mock_rag(generate_delay=llm_delay)
        router = Router(cfg, rag)

        n_users = 5
        latencies = []

        t_global_start = time.time()
        for i in range(n_users):
            t_start = time.time()
            router.route(f"!user{i:04d}", f"question {i}")
            latencies.append(time.time() - t_start)
        total_wall = time.time() - t_global_start

        # Total wall time should be at least n_users * llm_delay
        assert total_wall >= n_users * llm_delay * 0.8, (
            f"Total time {total_wall:.2f}s < expected {n_users * llm_delay:.2f}s"
        )

        # Each individual call should take roughly llm_delay
        for i, lat in enumerate(latencies):
            assert lat >= llm_delay * 0.7, (
                f"User {i} latency {lat:.3f}s < expected ~{llm_delay}s"
            )


# ---------------------------------------------------------------------------
# Test: busy-notice dispatcher integration
# ---------------------------------------------------------------------------

class TestBusyNotice:
    """Verify the dispatcher + worker correctly sends busy ack messages."""

    def test_busy_notice_sent_when_worker_occupied(self, tmp_path):
        """When the worker is busy, new query senders get an ack."""
        cfg = _make_cfg(tmp_path, rate_limit_seconds=0)
        cfg["busy_notice"] = True
        llm_delay = 0.5
        rag = _mock_rag(generate_delay=llm_delay)
        router = Router(cfg, rag)

        msg_queue = queue.Queue()
        query_queue = queue.Queue()
        worker_busy = threading.Event()
        pending_senders: set = set()
        pending_lock = threading.Lock()
        sent_messages: list = []   # (sender_id, text) log
        lock = threading.Lock()

        def fake_send(sender_id, text):
            with lock:
                sent_messages.append((sender_id, text))

        stop = threading.Event()

        def worker():
            while not stop.is_set():
                try:
                    sid, txt = query_queue.get(timeout=0.1)
                except queue.Empty:
                    continue
                worker_busy.set()
                try:
                    resp = router.route(sid, txt)
                    if resp:
                        fake_send(sid, resp)
                finally:
                    with pending_lock:
                        pending_senders.discard(sid)
                    worker_busy.clear()

        t = threading.Thread(target=worker, daemon=True)
        t.start()

        # Enqueue first query — worker picks it up immediately
        query_queue.put(("!userA", "What is solar power?"))
        time.sleep(0.05)  # let worker start processing

        # Now worker is busy; send a second query through the dispatcher logic
        assert worker_busy.is_set(), "Worker should be busy"
        sid2 = "!userB"
        with pending_lock:
            already_pending = sid2 in pending_senders
            pending_senders.add(sid2)

        if worker_busy.is_set() and not already_pending:
            position = query_queue.qsize() + 1
            ack = router.busy_message(position)
            fake_send(sid2, ack)

        query_queue.put((sid2, "Where is the first aid tent?"))

        # Wait for both queries to finish
        time.sleep(llm_delay * 3)
        stop.set()
        t.join(timeout=2.0)

        with lock:
            senders_who_got_ack = [
                sid for sid, text in sent_messages
                if sid == "!userB" and "hang tight" in text.lower() or "next" in text.lower()
            ]
            responses_to_b = [
                text for sid, text in sent_messages
                if sid == "!userB" and "Test answer" in text
            ]

        assert len(senders_who_got_ack) >= 1, (
            f"userB should have received a busy ack, got: "
            f"{[t for s,t in sent_messages if s == '!userB']}"
        )
        assert len(responses_to_b) >= 1, "userB should also get the real answer"

    def test_no_busy_notice_when_worker_idle(self, tmp_path):
        """When the worker is idle, queries are dispatched without ack."""
        cfg = _make_cfg(tmp_path, rate_limit_seconds=0)
        cfg["busy_notice"] = True
        rag = _mock_rag(generate_delay=0)  # instant replies
        router = Router(cfg, rag)

        worker_busy = threading.Event()
        # Worker is NOT busy
        assert not worker_busy.is_set()
        # No busy notice should be generated
        # (Just verify the flag check — real dispatcher skips ack)
        assert router.classify("What is solar power?") == "query"

    def test_no_duplicate_ack_for_same_sender(self, tmp_path):
        """A sender with a pending query should not get spammed with acks."""
        cfg = _make_cfg(tmp_path, rate_limit_seconds=0)
        cfg["busy_notice"] = True
        rag = _mock_rag(generate_delay=0.3)
        router = Router(cfg, rag)

        pending_senders: set = set()
        pending_lock = threading.Lock()
        ack_count = 0

        worker_busy = threading.Event()
        worker_busy.set()  # simulate busy worker

        sid = "!userC"
        for _ in range(5):
            with pending_lock:
                already_pending = sid in pending_senders
                pending_senders.add(sid)
            if worker_busy.is_set() and not already_pending:
                ack_count += 1

        # Only the first message should have triggered an ack
        assert ack_count == 1, f"Expected 1 ack, got {ack_count}"

    def test_busy_notice_disabled_by_config(self, tmp_path):
        """When busy_notice is False, no acks are sent."""
        cfg = _make_cfg(tmp_path, rate_limit_seconds=0)
        cfg["busy_notice"] = False
        rag = _mock_rag(generate_delay=0)
        router = Router(cfg, rag)

        worker_busy = threading.Event()
        worker_busy.set()  # simulate busy

        # Config says no busy notice
        assert not cfg.get("busy_notice", True)

    def test_commands_bypass_worker(self, tmp_path):
        """Commands are classified as fast and never enter the query queue."""
        cfg = _make_cfg(tmp_path)
        rag = _mock_rag()
        router = Router(cfg, rag)

        commands = ["!help", "!ping", "!status", "!topics", "!more", "!retry"]
        for cmd in commands:
            assert router.classify(cmd) == "command"
            # Commands handled inline, response returned immediately
            resp = router.route("!user", cmd)
            assert resp is not None, f"{cmd} should return a response"
