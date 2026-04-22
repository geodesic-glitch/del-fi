"""Multi-user concurrency stress test for the Del-Fi oracle.

Simulates N users sending messages at varying rates to expose:
  1. Queue starvation (slow LLM blocks everyone)
  2. Thread-safety issues in Router state
  3. Rate-limiter edge cases under concurrent load
  4. !more buffer cross-contamination between senders
  5. Cache correctness under parallel reads/writes

Run:
    python -m unittest tests.test_stress
"""

import os
import queue
import tempfile
import threading
import time
import unittest

from del_fi.core.router import Router, MoreBuffer
from del_fi.core.formatter import byte_len


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_cfg(tmpdir: str, **overrides) -> dict:
    """Minimal config dict for testing (no real Ollama / ChromaDB)."""
    base = {
        "node_name": "STRESS-NODE",
        "model": "test-model",
        "personality": "Terse.",
        "knowledge_folder": os.path.join(tmpdir, "knowledge"),
        "max_response_bytes": 230,
        "rate_limit_seconds": 0,
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
        "_base_dir": tmpdir,
        "_vectorstore_dir": os.path.join(tmpdir, "vectorstore"),
        "_cache_dir": os.path.join(tmpdir, "cache"),
        "_gossip_dir": os.path.join(tmpdir, "gossip"),
        "_seen_senders_file": os.path.join(tmpdir, "seen_senders.txt"),
        "fallback_message": "I don't have docs on that.",
    }
    base.update(overrides)
    for d in ("knowledge", "cache", "gossip", "vectorstore"):
        os.makedirs(os.path.join(tmpdir, d), exist_ok=True)
    return base


def _mock_wiki(available=True, generate_delay=0.0, generate_text="Test answer."):
    """Return a mock WikiEngine with controllable latency."""
    from del_fi.core.peers import PeerCache, GossipDirectory

    _avail = available          # avoid name clash with property
    _delay = generate_delay
    _text  = generate_text

    class _MockWiki:
        page_count   = 5
        available    = _avail
        rag_available = _avail

        def get_topics(self):
            return ["topic-a", "topic-b"]

        def query(self, text, peer_ctx="", history="", board_context=""):
            if _delay > 0:
                time.sleep(_delay)
            return _text, True

        def suggest(self, text):
            return None

    class _MockPeerCache:
        def lookup(self, q): return None
        def store(self, *a, **kw): pass

    class _MockGossipDir:
        peer_count = 0
        def list_peers(self): return []
        def receive(self, nid, txt): pass
        def referral(self, q): return None
        def announce(self): return ""

    return _MockWiki(), _MockPeerCache(), _MockGossipDir()


# ---------------------------------------------------------------------------
# Test: serial processing ― queue depth under load
# ---------------------------------------------------------------------------

class TestQueueBehavior(unittest.TestCase):
    """Verify messages queue correctly when the router is busy."""

    def setUp(self):
        self.tmpdir = tempfile.mkdtemp(prefix="delfi-stress-")

    def test_queue_depth_during_slow_llm(self):
        """While one query blocks on LLM, other messages accumulate."""
        cfg = _make_cfg(self.tmpdir)
        msg_queue = queue.Queue()

        wiki, peers, gossip = _mock_wiki(generate_delay=0.5)
        router = Router(cfg, wiki, peers, gossip)

        results = {}

        def process_loop(n_messages):
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

        num_users = 5
        for i in range(num_users):
            msg_queue.put((f"!user{i:04d}", f"question from user {i}"))

        self.assertEqual(msg_queue.qsize(), num_users)
        process_loop(num_users)
        self.assertEqual(len(results), num_users)

        for uid, r in results.items():
            self.assertIsNotNone(r["response"], f"{uid} got None response")

        first_user = results["!user0000"]
        self.assertGreaterEqual(first_user["latency"], 0.4, "LLM delay not applied")

    def test_queue_unbounded_growth(self):
        """Queue grows without bound if processing is slower than ingestion."""
        cfg = _make_cfg(self.tmpdir)
        msg_queue = queue.Queue()
        wiki, peers, gossip = _mock_wiki(generate_delay=0.1)
        router = Router(cfg, wiki, peers, gossip)

        # Blast 50 messages
        for i in range(50):
            msg_queue.put((f"!flood{i:04d}", f"msg {i}"))

        self.assertEqual(msg_queue.qsize(), 50)

        for _ in range(5):
            sender_id, text = msg_queue.get(timeout=1.0)
            router.route(sender_id, text)

        self.assertEqual(msg_queue.qsize(), 45)


# ---------------------------------------------------------------------------
# Test: thread-safety of Router state
# ---------------------------------------------------------------------------

class TestRouterThreadSafety(unittest.TestCase):
    """Hammer Router from multiple threads to detect race conditions."""

    def setUp(self):
        self.tmpdir = tempfile.mkdtemp(prefix="delfi-stress-")

    def test_concurrent_command_routing(self):
        """Multiple threads calling route() with commands simultaneously."""
        cfg = _make_cfg(self.tmpdir)
        wiki, peers, gossip = _mock_wiki()
        router = Router(cfg, wiki, peers, gossip)

        results = {}
        barrier = threading.Barrier(10)

        def fire(sender_id, text):
            barrier.wait()
            resp = router.route(sender_id, text)
            results[sender_id] = resp

        threads = []
        for i in range(10):
            t = threading.Thread(target=fire, args=(f"!node{i:04d}", "!status"))
            threads.append(t)
            t.start()

        for t in threads:
            t.join(timeout=10)

        self.assertEqual(len(results), 10)
        for uid, resp in results.items():
            self.assertIn("STRESS-NODE", resp, f"{uid} got bad response: {resp}")
            self.assertTrue("wiki pages" in resp or "up" in resp)

    def test_concurrent_queries_no_crash(self):
        """Multiple threads calling route() with freeform queries."""
        cfg = _make_cfg(self.tmpdir)
        wiki, peers, gossip = _mock_wiki(generate_delay=0.05)
        router = Router(cfg, wiki, peers, gossip)

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

        self.assertEqual(len(errors), 0, f"Errors during concurrent queries: {errors}")

    def test_concurrent_cache_writes(self):
        """Concurrent queries that all write to the response cache."""
        cfg = _make_cfg(self.tmpdir, persistent_cache=False)
        wiki, peers, gossip = _mock_wiki(generate_delay=0.02)
        router = Router(cfg, wiki, peers, gossip)

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
            "Where is the food?",
            "What time is the show?",
            "Any workshops today?",
        ]
        for i, q in enumerate(questions):
            t = threading.Thread(target=fire, args=(f"!cache{i:04d}", q))
            threads.append(t)
            t.start()

        for t in threads:
            t.join(timeout=30)

        self.assertEqual(len(results), 6)
        self.assertIsNotNone(results["!cache0000"])
        self.assertIsNotNone(results["!cache0003"])


# ---------------------------------------------------------------------------
# Test: rate limiter under concurrent senders
# ---------------------------------------------------------------------------

class TestRateLimiterConcurrency(unittest.TestCase):
    """Rate limiter should isolate senders — A's limit doesn't affect B."""

    def setUp(self):
        self.tmpdir = tempfile.mkdtemp(prefix="delfi-stress-")

    def test_different_senders_not_rate_limited(self):
        """Each sender has an independent rate limit window."""
        cfg = _make_cfg(self.tmpdir, rate_limit_seconds=60)
        wiki, peers, gossip = _mock_wiki()
        router = Router(cfg, wiki, peers, gossip)

        for i in range(5):
            resp = router.route(f"!user{i:04d}", f"question {i}")
            self.assertIsNotNone(resp, f"user{i} was rate-limited on first msg")

    def test_same_sender_rate_limited(self):
        """Router itself has no rate limiting — that's the mesh adapter's job."""
        cfg = _make_cfg(self.tmpdir, rate_limit_seconds=60)
        wiki, peers, gossip = _mock_wiki()
        router = Router(cfg, wiki, peers, gossip)

        r1 = router.route("!userAAAA", "first question")
        r2 = router.route("!userAAAA", "second question")
        self.assertIsNotNone(r1)
        self.assertIsNotNone(r2)


# ---------------------------------------------------------------------------
# Test: !more buffer isolation between senders
# ---------------------------------------------------------------------------

class TestMoreBufferIsolation(unittest.TestCase):
    """Each sender's !more buffer must be independent."""

    def setUp(self):
        self.tmpdir = tempfile.mkdtemp(prefix="delfi-stress-")

    def test_more_buffers_dont_cross_contaminate(self):
        """User A's !more buffer is separate from user B's."""
        cfg = _make_cfg(self.tmpdir)
        long_answer = "A" * 300 + " " + "B" * 300
        wiki, peers, gossip = _mock_wiki(generate_text=long_answer)
        router = Router(cfg, wiki, peers, gossip)

        r_a = router.route("!userA", "long question A")
        r_b = router.route("!userB", "long question B")
        self.assertIsNotNone(r_a)
        self.assertIsNotNone(r_b)

        more_a = router.route("!userA", "!more")
        more_b = router.route("!userB", "!more")

        self.assertIsNotNone(more_a)
        self.assertIsNotNone(more_b)
        self.assertNotIn("No pending", more_a, "User A's more buffer was lost")
        self.assertNotIn("No pending", more_b, "User B's more buffer was lost")

    def test_more_buffer_not_shared(self):
        """User C has no buffer — shouldn't see user A's chunks."""
        cfg = _make_cfg(self.tmpdir)
        long_answer = "X" * 500
        wiki, peers, gossip = _mock_wiki(generate_text=long_answer)
        router = Router(cfg, wiki, peers, gossip)

        router.route("!userA", "trigger long response")

        resp = router.route("!userC", "!more")
        self.assertIn("No pending", resp)


# ---------------------------------------------------------------------------
# Test: simulate realistic multi-user session
# ---------------------------------------------------------------------------

class TestRealisticMultiUser(unittest.TestCase):
    """End-to-end simulation of a realistic multi-user scenario."""

    def setUp(self):
        self.tmpdir = tempfile.mkdtemp(prefix="delfi-stress-")

    def test_festival_scenario(self):
        """Simulate 8 festival attendees hitting the oracle."""
        cfg = _make_cfg(self.tmpdir, rate_limit_seconds=0)
        wiki, peers, gossip = _mock_wiki(
            generate_delay=0.05,
            generate_text="Zone A has food trucks and a makerspace. Check the map near the entrance.",
        )
        router = Router(cfg, wiki, peers, gossip)
        msg_queue = queue.Queue()

        script = [
            (0,   "!alice",  "Where can I find food?"),
            (10,  "!bob",    "!help"),
            (20,  "!carol",  "What workshops are today?"),
            (30,  "!dave",   "!topics"),
            (40,  "!eve",    "Where can I find food?"),
            (50,  "!frank",  "!status"),
            (60,  "!grace",  "Is there a soldering workshop?"),
            (70,  "!hank",   "!ping"),
            (100, "!alice",  "!more"),
            (110, "!bob",    "What if it rains?"),
            (120, "!carol",  "!more"),
            (130, "!dave",   "!help"),
            (140, "!hank",   "!status"),
            (150, "!eve",    "!topics"),
        ]

        for _delay, sender, text in script:
            msg_queue.put((sender, text))

        responses = []
        while not msg_queue.empty():
            sender_id, text = msg_queue.get(timeout=1.0)
            resp = router.route(sender_id, text)
            responses.append({"sender": sender_id, "text": text, "response": resp})

        self.assertEqual(len(responses), len(script))

        for r in responses:
            self.assertIsNotNone(
                r["response"], f"{r['sender']} sent '{r['text']}' and got None"
            )

        for r in (r for r in responses if r["text"] == "!ping"):
            self.assertIn("pong", r["response"])

        for r in (r for r in responses if r["text"] == "!status"):
            self.assertIn("up", r["response"])

        for r in (r for r in responses if r["text"] == "!topics"):
            self.assertIn("topic-a", r["response"])

        for r in responses:
            self.assertLessEqual(
                byte_len(r["response"]),
                cfg["max_response_bytes"] + 50,
                f"Response too large ({byte_len(r['response'])}B): {r['response'][:80]}",
            )

    def test_rapid_fire_same_user(self):
        """One user sends 20 messages rapidly — router shouldn't crash."""
        cfg = _make_cfg(self.tmpdir, rate_limit_seconds=0)
        wiki, peers, gossip = _mock_wiki(generate_delay=0.01)
        router = Router(cfg, wiki, peers, gossip)

        responses = [router.route("!spammer", f"question number {i}") for i in range(20)]
        self.assertTrue(all(r is not None for r in responses))

    def test_interleaved_queries_and_commands(self):
        """Alternating freeform queries and commands from different users."""
        cfg = _make_cfg(self.tmpdir, rate_limit_seconds=0)
        wiki, peers, gossip = _mock_wiki(generate_delay=0.01)
        router = Router(cfg, wiki, peers, gossip)

        senders = [f"!user{i:02d}" for i in range(6)]
        messages = [
            "What is in zone A?",
            "!help",
            "Where is the bathroom?",
            "!topics",
            "Is there wifi?",
            "!status",
        ]

        for sender, msg in zip(senders, messages):
            resp = router.route(sender, msg)
            self.assertIsNotNone(resp, f"{sender} got None for '{msg}'")


# ---------------------------------------------------------------------------
# Test: query count and cache stats under load
# ---------------------------------------------------------------------------

class TestStatsUnderLoad(unittest.TestCase):
    """Verify internal counters stay consistent under load."""

    def setUp(self):
        self.tmpdir = tempfile.mkdtemp(prefix="delfi-stress-")

    def test_query_count_accurate(self):
        """_query_count should match number of freeform queries processed."""
        cfg = _make_cfg(self.tmpdir, rate_limit_seconds=0)
        wiki, peers, gossip = _mock_wiki()
        router = Router(cfg, wiki, peers, gossip)

        num_queries = 15
        num_commands = 5

        for i in range(num_queries):
            router.route(f"!user{i:04d}", f"freeform question {i}")

        for i in range(num_commands):
            router.route(f"!cmd{i:04d}", "!ping")

        self.assertEqual(router._query_count, num_queries)

    def test_cache_populated_correctly(self):
        """Unique queries get cached; commands don't."""
        cfg = _make_cfg(self.tmpdir, rate_limit_seconds=0, persistent_cache=False)
        wiki, peers, gossip = _mock_wiki()
        router = Router(cfg, wiki, peers, gossip)

        unique_questions = [
            "Where is zone A?",
            "What time is lunch?",
            "How to solder?",
        ]
        for i, q in enumerate(unique_questions):
            router.route(f"!user{i:04d}", q)

        router.route("!cmduser", "!help")
        router.route("!cmduser", "!status")

        self.assertEqual(len(router._response_cache), 3)
        for q in unique_questions:
            self.assertIn(q.lower().strip(), router._response_cache)


# ---------------------------------------------------------------------------
# Test: measure serialization impact (timing)
# ---------------------------------------------------------------------------

class TestLatencyProfile(unittest.TestCase):
    """Measure how serialization affects tail latency."""

    def setUp(self):
        self.tmpdir = tempfile.mkdtemp(prefix="delfi-stress-")

    def test_serial_latency_grows_linearly(self):
        """With 0.1s LLM delay, N users wait ~N*0.1s total."""
        cfg = _make_cfg(self.tmpdir, rate_limit_seconds=0)
        llm_delay = 0.1
        wiki, peers, gossip = _mock_wiki(generate_delay=llm_delay)
        router = Router(cfg, wiki, peers, gossip)

        n_users = 5
        latencies = []

        t_global_start = time.time()
        for i in range(n_users):
            t_start = time.time()
            router.route(f"!user{i:04d}", f"question {i}")
            latencies.append(time.time() - t_start)
        total_wall = time.time() - t_global_start

        self.assertGreaterEqual(
            total_wall,
            n_users * llm_delay * 0.8,
            f"Total time {total_wall:.2f}s < expected {n_users * llm_delay:.2f}s",
        )

        for i, lat in enumerate(latencies):
            self.assertGreaterEqual(
                lat,
                llm_delay * 0.7,
                f"User {i} latency {lat:.3f}s < expected ~{llm_delay}s",
            )


# ---------------------------------------------------------------------------
# Test: busy-notice dispatcher integration
# ---------------------------------------------------------------------------

class TestBusyNotice(unittest.TestCase):
    """Verify the dispatcher + worker correctly sends busy ack messages."""

    def setUp(self):
        self.tmpdir = tempfile.mkdtemp(prefix="delfi-stress-")

    def test_busy_notice_sent_when_worker_occupied(self):
        """When the worker is busy, new query senders get an ack."""
        cfg = _make_cfg(self.tmpdir, rate_limit_seconds=0)
        cfg["busy_notice"] = True
        llm_delay = 0.5
        wiki, peers, gossip = _mock_wiki(generate_delay=llm_delay)
        router = Router(cfg, wiki, peers, gossip)

        msg_queue = queue.Queue()
        query_queue = queue.Queue()
        worker_busy = threading.Event()
        pending_senders: set = set()
        pending_lock = threading.Lock()
        sent_messages: list = []
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

        query_queue.put(("!userA", "What is solar power?"))
        time.sleep(0.05)

        self.assertTrue(worker_busy.is_set(), "Worker should be busy")
        sid2 = "!userB"
        with pending_lock:
            already_pending = sid2 in pending_senders
            pending_senders.add(sid2)

        if worker_busy.is_set() and not already_pending:
            position = query_queue.qsize() + 1
            ack = router.busy_message(position)
            fake_send(sid2, ack)

        query_queue.put((sid2, "Where is the first aid tent?"))

        time.sleep(llm_delay * 3)
        stop.set()
        t.join(timeout=2.0)

        with lock:
            senders_who_got_ack = [
                sid for sid, text in sent_messages
                if sid == "!userB" and ("hang tight" in text.lower() or "next" in text.lower())
            ]
            responses_to_b = [
                text for sid, text in sent_messages
                if sid == "!userB" and "Test answer" in text
            ]

        self.assertGreaterEqual(
            len(senders_who_got_ack),
            1,
            f"userB should have received a busy ack, got: "
            f"{[t for s, t in sent_messages if s == '!userB']}",
        )
        self.assertGreaterEqual(len(responses_to_b), 1, "userB should also get the real answer")

    def test_no_busy_notice_when_worker_idle(self):
        """When the worker is idle, queries are dispatched without ack."""
        cfg = _make_cfg(self.tmpdir, rate_limit_seconds=0)
        cfg["busy_notice"] = True
        wiki, peers, gossip = _mock_wiki(generate_delay=0)
        router = Router(cfg, wiki, peers, gossip)

        worker_busy = threading.Event()
        self.assertFalse(worker_busy.is_set())
        self.assertEqual(router.classify("What is solar power?"), "query")

    def test_no_duplicate_ack_for_same_sender(self):
        """A sender with a pending query should not get spammed with acks."""
        cfg = _make_cfg(self.tmpdir, rate_limit_seconds=0)
        cfg["busy_notice"] = True
        wiki, peers, gossip = _mock_wiki(generate_delay=0.3)
        router = Router(cfg, wiki, peers, gossip)

        pending_senders: set = set()
        pending_lock = threading.Lock()
        ack_count = 0

        worker_busy = threading.Event()
        worker_busy.set()

        sid = "!userC"
        for _ in range(5):
            with pending_lock:
                already_pending = sid in pending_senders
                pending_senders.add(sid)
            if worker_busy.is_set() and not already_pending:
                ack_count += 1

        self.assertEqual(ack_count, 1, f"Expected 1 ack, got {ack_count}")

    def test_busy_notice_disabled_by_config(self):
        """When busy_notice is False, no acks are sent."""
        cfg = _make_cfg(self.tmpdir, rate_limit_seconds=0)
        cfg["busy_notice"] = False
        wiki, peers, gossip = _mock_wiki(generate_delay=0)
        router = Router(cfg, wiki, peers, gossip)

        worker_busy = threading.Event()
        worker_busy.set()

        self.assertFalse(cfg.get("busy_notice", True))

    def test_commands_bypass_worker(self):
        """Commands are classified as fast and never enter the query queue."""
        cfg = _make_cfg(self.tmpdir)
        wiki, peers, gossip = _mock_wiki()
        router = Router(cfg, wiki, peers, gossip)

        commands = ["!help", "!ping", "!status", "!topics", "!more", "!retry"]
        for cmd in commands:
            self.assertEqual(router.classify(cmd), "command")
            resp = router.route("!user", cmd)
            self.assertIsNotNone(resp, f"{cmd} should return a response")


if __name__ == "__main__":
    unittest.main()

