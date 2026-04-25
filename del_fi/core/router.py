"""Query routing and command dispatch for Del-Fi v0.2.

Routes incoming messages to commands (inline) or the tier hierarchy:
  Tier 0 — FactStore (sensor facts, no LLM)
  Tier 1 — WikiEngine (BM25 + LLM on compiled wiki pages)
  Tier 2 — PeerCache (trusted peer Q&A)
  Tier 3 — GossipDirectory (referrals only)
  Fallback — fallback_message config value

Multi-chunk responses are buffered per-sender; !more fetches later chunks.
"""

import json
import logging
import os
import re
import time

from del_fi.core.facts import FactStore
from del_fi.core.formatter import byte_len, format_response, truncate_at_sentence, MORE_TAG
from del_fi.core.knowledge import WikiEngine
from del_fi.core.memory import ConversationMemory
from del_fi.core.board import Board
from del_fi.core.peers import GossipDirectory, PeerCache

log = logging.getLogger("del_fi.core.router")

# !more buffers expire after 10 minutes of inactivity
MORE_BUFFER_TTL = 600

# Default auto-send window (config key: auto_send_chunks)
AUTO_SEND_CHUNKS = 3

GREETINGS = frozenset({
    "hi", "hello", "hey", "yo", "sup", "howdy", "hola", "greetings"
})


class MoreBuffer:
    """Per-sender buffer for chunked responses.

    Tracks all chunks from format_response() and a cursor pointing to
    the last sent chunk.  Supports !more (next) and !more N (specific,
    1-indexed).
    """

    def __init__(self, chunks: list[str], timestamp: float):
        self.chunks = chunks
        self.cursor = 0
        self.timestamp = timestamp

    def next_chunk(self) -> str | None:
        """Return the next unsent chunk, or None if exhausted."""
        self.cursor += 1
        if self.cursor < len(self.chunks):
            chunk = self.chunks[self.cursor]
            if self.cursor < len(self.chunks) - 1:
                chunk += MORE_TAG
            return chunk
        return None

    def get_chunk(self, n: int) -> str | None:
        """Return a specific chunk by 1-based index."""
        idx = n - 1
        if 0 <= idx < len(self.chunks):
            self.cursor = idx
            chunk = self.chunks[idx]
            if idx < len(self.chunks) - 1:
                chunk += MORE_TAG
            return chunk
        return None

    @property
    def expired(self) -> bool:
        return (time.time() - self.timestamp) > MORE_BUFFER_TTL

    @property
    def total_chunks(self) -> int:
        return len(self.chunks)


class Router:
    """Routes incoming messages to commands or the tier hierarchy."""

    def __init__(
        self,
        cfg: dict,
        wiki: WikiEngine,
        peer_cache: PeerCache,
        gossip_dir: GossipDirectory,
        fact_store: FactStore | None = None,
    ):
        self.cfg = cfg
        self.wiki = wiki
        self.peer_cache = peer_cache
        self.gossip_dir = gossip_dir
        self.facts: FactStore | None = fact_store

        self._more_buffers: dict[str, MoreBuffer] = {}
        self._response_cache: dict[str, tuple[str, float]] = {}
        self._seen_senders: set[str] = set()
        self._last_query: dict[str, str] = {}
        self._start_time = time.time()
        self._query_count = 0
        self._cache_file = os.path.join(cfg["_cache_dir"], "response_cache.json")
        self._cache_dirty = False

        self._query_queue = None  # set by main.py after query_queue is created

        self._load_seen_senders()
        if cfg.get("persistent_cache", True):
            self._load_disk_cache()

        self.memory: ConversationMemory | None = None
        if cfg.get("memory_max_turns", 0) > 0:
            self.memory = ConversationMemory(cfg)
            log.info(
                f"conversation memory enabled "
                f"(max {self.memory.max_turns} turns, ttl {self.memory.ttl}s)"
            )

        self.board: Board | None = None
        if cfg.get("board_enabled", False):
            self.board = Board(cfg)
            log.info(
                f"board enabled "
                f"(max {self.board.max_posts} posts, ttl {self.board.post_ttl}s)"
            )

    # --- Classification ---

    def classify(self, text: str) -> str:
        """Return 'empty', 'command', 'gossip', or 'query'."""
        text = text.strip()
        if not text:
            return "empty"
        if text.startswith("!"):
            return "command"
        if text.startswith("DEL-FI:"):
            return "gossip"
        return "query"

    def busy_message(self, position: int) -> str:
        name = self.cfg["node_name"]
        if position <= 1:
            return f"{name}: Working on another question, yours is next."
        return f"{name}: {position} questions ahead of yours, hang tight."

    # --- Main entry points ---

    def route(self, sender_id: str, text: str) -> str | None:
        """Route a message and return the first-chunk response, or None."""
        text = text.strip()
        if not text:
            return None

        self._clean_expired_buffers()

        if text.startswith("!"):
            response = self._handle_command(sender_id, text)
            return self._enforce_limit(response)

        if text.startswith("DEL-FI:"):
            self.gossip_dir.receive(sender_id, text)
            return None

        return self._handle_query(sender_id, text)

    def route_multi(self, sender_id: str, text: str) -> list[str] | None:
        """Route and return up to auto_send_chunks messages.

        Returns a list of strings to send in order.  Single-chunk
        responses return a 1-element list.
        """
        first = self.route(sender_id, text)
        if first is None:
            return None

        n_auto = self.cfg.get("auto_send_chunks", AUTO_SEND_CHUNKS)
        buf = self._more_buffers.get(sender_id)

        if buf is None or buf.expired or n_auto <= 1:
            return [first]

        base_first = first[: -len(MORE_TAG)] if first.endswith(MORE_TAG) else first
        auto_msgs = [base_first]

        while len(auto_msgs) < n_auto:
            chunk = buf.next_chunk()
            if chunk is None:
                break
            is_last_slot = len(auto_msgs) == n_auto - 1
            if not is_last_slot and chunk.endswith(MORE_TAG):
                chunk = chunk[: -len(MORE_TAG)].rstrip()
            auto_msgs.append(chunk)

        return auto_msgs

    def _enforce_limit(self, text: str | None) -> str | None:
        if text is None:
            return None
        max_bytes = self.cfg["max_response_bytes"]
        if byte_len(text) <= max_bytes:
            return text
        return truncate_at_sentence(text, max_bytes)

    # --- Command dispatch ---

    def _handle_command(self, sender_id: str, text: str) -> str:
        parts = text.split(None, 1)
        cmd = parts[0].lower()
        arg = parts[1] if len(parts) > 1 else ""

        handlers = {
            "!help": self._cmd_help,
            "!topics": self._cmd_topics,
            "!status": self._cmd_status,
            "!board": self._cmd_board,
            "!post": self._cmd_post,
            "!unpost": self._cmd_unpost,
            "!more": self._cmd_more,
            "!retry": self._cmd_retry,
            "!forget": self._cmd_forget,
            "!peers": self._cmd_peers,
            "!data": self._cmd_data,
            "!ping": self._cmd_ping,
        }

        handler = handlers.get(cmd)
        if handler:
            return handler(sender_id, arg)
        return f"Unknown command: {cmd}. Try !help"

    def _cmd_help(self, sender_id: str, arg: str) -> str:
        name = self.cfg["node_name"]
        pages = self.wiki.page_count
        return (
            f"{name} · AI oracle · {pages} wiki pages\n"
            f"Ask anything in plain text.\n"
            f"!topics !status !board !post !unpost\n"
            f"!more !retry !forget !ping !peers !data"
        )

    def _cmd_topics(self, sender_id: str, arg: str) -> str:
        topics = self.wiki.get_topics()
        if not topics:
            return (
                "No wiki pages loaded. Run: python main.py --build-wiki"
            )
        return "Topics: " + ", ".join(topics)

    def _cmd_status(self, sender_id: str, arg: str) -> str:
        name = self.cfg["node_name"]
        model = self.cfg["model"]
        pages = self.wiki.page_count
        uptime = self._format_uptime()
        ollama_ok = "+" if self.wiki.available else "-"
        rag_ok = "+" if self.wiki.rag_available else "-"
        peers = self.gossip_dir.peer_count
        return (
            f"{name} up {uptime} · {model}\n"
            f"{pages} wiki pages · {self._query_count} queries\n"
            f"ollama:{ollama_ok} rag:{rag_ok} peers:{peers}"
        )

    def _cmd_board(self, sender_id: str, arg: str) -> str:
        if not self.board:
            return "The board is not enabled on this node."
        return self.board.read(arg)

    def _cmd_post(self, sender_id: str, arg: str) -> str:
        if not self.board:
            return "The board is not enabled on this node."
        return self.board.post(sender_id, arg)

    def _cmd_unpost(self, sender_id: str, arg: str) -> str:
        if not self.board:
            return "The board is not enabled on this node."
        return self.board.clear(sender_id)

    def _cmd_more(self, sender_id: str, arg: str) -> str:
        buf = self._more_buffers.get(sender_id)
        if not buf or buf.expired:
            return "No pending response. Send a question first."

        if arg.strip().isdigit():
            n = int(arg.strip())
            chunk = buf.get_chunk(n)
            if chunk:
                return chunk
            return f"No chunk {n}. Response has {buf.total_chunks} parts."

        chunk = buf.next_chunk()
        if chunk:
            return chunk
        return "End of response. No more chunks."

    def _cmd_retry(self, sender_id: str, arg: str) -> str:
        last = self._last_query.get(sender_id)
        if not last:
            return "No previous query to retry. Ask a question first."
        key = last.lower().strip()
        if key in self._response_cache:
            del self._response_cache[key]
            if self.cfg.get("persistent_cache", True):
                self._save_disk_cache()
            log.info(f"cache evicted for retry: {key[:40]}")
        if self._query_queue is not None:
            self._query_queue.put((sender_id, last))
            return "Retrying..."
        # Fallback when called outside main daemon (tests, GUI)
        return self._handle_query(sender_id, last)

    def _cmd_forget(self, sender_id: str, arg: str) -> str:
        if not self.memory:
            return "Conversation memory is not enabled on this node."
        self.memory.clear(sender_id)
        return "Memory cleared. I won't remember our previous conversation."

    def _cmd_peers(self, sender_id: str, arg: str) -> str:
        peers = self.gossip_dir.list_peers()
        if not peers:
            return "No other Del-Fi nodes seen yet."
        lines = []
        for p in peers:
            topics = ", ".join(p.get("topics", [])[:4])
            lines.append(f"{p['node_name']}: {topics}")
        return "\n".join(lines)

    def _cmd_data(self, sender_id: str, arg: str) -> str:
        if not self.facts or not self.facts.has_facts():
            return (
                "No sensor data loaded. Write readings to "
                "cache/sensor_feed.json (see sensor_feed.example.json)."
            )
        return self.facts.format_snapshot()

    def _cmd_ping(self, sender_id: str, arg: str) -> str:
        return f"pong from {self.cfg['node_name']}"

    # --- Query pipeline ---

    def _handle_query(self, sender_id: str, text: str) -> str:
        self._query_count += 1
        self._last_query[sender_id] = text

        history = self.memory.format_for_prompt(sender_id) if self.memory else ""
        board_ctx = (
            self.board.format_for_context(query=text)
            if self.board and self.board.post_count > 0
            else ""
        )

        # Welcome greeting for first-time senders
        if self._is_greeting(text) and sender_id not in self._seen_senders:
            self._mark_seen(sender_id)
            name = self.cfg["node_name"]
            pages = self.wiki.page_count
            return (
                f"Hi from {name}. I answer questions using local docs.\n"
                f"{pages} wiki pages loaded. Try !help or !topics."
            )

        # Tier 0: FactStore (sensor / measurement queries, no LLM)
        # Bypasses the response cache — freshness is the whole point.
        if self.facts and self.facts.has_facts():
            fact_response = self.facts.lookup(text)
            if fact_response is not None:
                log.info("tier0: fact match")
                return self._finalize(sender_id, fact_response)

        # Response cache (exact match)
        cached = self._check_cache(text)
        if cached:
            log.info("cache hit")
            return self._finalize(sender_id, cached)

        # Ollama not ready
        if not self.wiki.available:
            return "I'm still warming up, try again in a minute."

        # Tier 1: WikiEngine (BM25 + LLM)
        answer, had_context = self.wiki.query(
            text, history=history, board_context=board_ctx
        )

        provenance: str | None = None

        if not had_context:
            # Tier 2: PeerCache
            peer_result = self.peer_cache.lookup(text)
            if peer_result:
                had_context = True
                provenance = peer_result["peer_name"]
                log.info(f"tier2: peer match from {provenance}")
                # Re-run wiki.query with peer context so LLM can synthesise
                peer_ctx = f"[{peer_result['peer_name']}]: {peer_result['response']}"
                answer, _ = self.wiki.query(
                    text, peer_ctx=peer_ctx, history=history,
                    board_context=board_ctx,
                )
                if not answer:
                    answer = peer_result["response"]

            else:
                # Tier 3: GossipDirectory (referral only)
                referral = self.gossip_dir.referral(text)
                if referral:
                    return self._finalize(sender_id, referral)

                # Fallback: configured fallback message
                fallback = self.cfg.get("fallback_message", "")
                if not fallback:
                    fallback = self.wiki.suggest(text) or (
                        f"{self.cfg['node_name']}: I don't have docs on that. "
                        f"Try !topics to see what I know."
                    )
                return self._finalize(sender_id, fallback)

        if not answer:
            return "I'm having trouble thinking right now. Try again in a minute."

        if had_context:
            self._cache_response(text, answer)

        if self.memory and answer:
            self.memory.add_turn(sender_id, text, answer)

        return self._finalize(sender_id, answer, provenance=provenance)

    def _finalize(
        self, sender_id: str, text: str, provenance: str | None = None
    ) -> str:
        max_bytes = self.cfg["max_response_bytes"]
        first_msg, all_chunks, is_truncated = format_response(
            text, max_bytes=max_bytes, provenance=provenance
        )

        if sender_id not in self._seen_senders:
            self._mark_seen(sender_id)
            pages = self.wiki.page_count
            footer = f"\n---\nDel-Fi oracle · {pages} pages · !help !topics"
            with_footer = first_msg + footer
            if byte_len(with_footer) <= max_bytes:
                first_msg = with_footer

        if is_truncated:
            self._more_buffers[sender_id] = MoreBuffer(all_chunks, time.time())

        return first_msg

    # --- Helpers ---

    def _is_greeting(self, text: str) -> bool:
        return text.lower().strip().rstrip("!.,?") in GREETINGS

    def _check_cache(self, query: str) -> str | None:
        key = query.lower().strip()
        if key in self._response_cache:
            response, ts = self._response_cache[key]
            if time.time() - ts < self.cfg["response_cache_ttl"]:
                return response
            del self._response_cache[key]
        return None

    def _cache_response(self, query: str, response: str):
        key = query.lower().strip()
        self._response_cache[key] = (response, time.time())
        self._cache_dirty = True
        if len(self._response_cache) > 100:
            now = time.time()
            ttl = self.cfg["response_cache_ttl"]
            self._response_cache = {
                k: (v, t)
                for k, (v, t) in self._response_cache.items()
                if now - t < ttl
            }

    def flush_cache(self):
        """Write response cache to disk if dirty. Called by background thread."""
        if self._cache_dirty and self.cfg.get("persistent_cache", True):
            self._save_disk_cache()
            self._cache_dirty = False

    def _load_disk_cache(self):
        try:
            if os.path.exists(self._cache_file):
                with open(self._cache_file) as f:
                    data = json.load(f)
                now = time.time()
                ttl = self.cfg["response_cache_ttl"]
                for key, entry in data.items():
                    if now - entry["ts"] < ttl:
                        self._response_cache[key] = (entry["response"], entry["ts"])
                loaded = len(self._response_cache)
                if loaded:
                    log.info(f"loaded {loaded} cached responses from disk")
        except Exception as e:
            log.warning(f"could not load response cache: {e}")

    def _save_disk_cache(self):
        try:
            os.makedirs(os.path.dirname(self._cache_file), exist_ok=True)
            data = {
                k: {"response": v, "ts": t}
                for k, (v, t) in self._response_cache.items()
            }
            with open(self._cache_file, "w") as f:
                json.dump(data, f)
        except Exception:
            log.exception("disk cache save failed")

    def _mark_seen(self, sender_id: str):
        self._seen_senders.add(sender_id)
        self._save_seen_senders()

    def _load_seen_senders(self):
        path = self.cfg["_seen_senders_file"]
        try:
            if os.path.exists(path):
                with open(path) as f:
                    self._seen_senders = {
                        line.strip() for line in f if line.strip()
                    }
        except Exception:
            pass

    def _save_seen_senders(self):
        path = self.cfg["_seen_senders_file"]
        try:
            os.makedirs(os.path.dirname(path), exist_ok=True)
            with open(path, "w") as f:
                for s in sorted(self._seen_senders):
                    f.write(s + "\n")
        except Exception:
            pass

    def _clean_expired_buffers(self):
        expired = [k for k, v in self._more_buffers.items() if v.expired]
        for k in expired:
            del self._more_buffers[k]

    def _format_uptime(self) -> str:
        elapsed = int(time.time() - self._start_time)
        days = elapsed // 86400
        hours = (elapsed % 86400) // 3600
        if days > 0:
            return f"{days}d {hours}h"
        minutes = (elapsed % 3600) // 60
        return f"{hours}h {minutes}m"
