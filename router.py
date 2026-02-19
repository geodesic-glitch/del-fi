"""Query routing and command handling.

Receives messages from the mesh interface, dispatches ! commands,
routes freeform queries through the RAG pipeline, and manages
response buffering for the !more chunking system.
"""

import json
import logging
import os
import re
import time

from board import Board
from facts import FactStore
from formatter import byte_len, format_response, truncate_at_sentence, MORE_TAG
from memory import ConversationMemory
from rag import RAGEngine

log = logging.getLogger("delfi.router")

# !more buffers expire after 10 minutes
MORE_BUFFER_TTL = 600

# How many chunks to auto-send before prompting !more (overridden by config)
AUTO_SEND_CHUNKS = 3

# Short messages that are greetings, not questions
GREETINGS = {"hi", "hello", "hey", "yo", "sup", "howdy", "hola", "greetings"}


class MoreBuffer:
    """Per-sender buffer for chunked responses.

    Tracks all chunks and a cursor pointing to the last sent chunk.
    Supports !more (next) and !more N (specific chunk, 1-indexed).
    """

    def __init__(self, chunks: list[str], timestamp: float):
        self.chunks = chunks
        self.cursor = 0  # last sent chunk index (0 = first already sent)
        self.timestamp = timestamp

    def next_chunk(self) -> str | None:
        """Get the next unsent chunk, or None if all sent."""
        self.cursor += 1
        if self.cursor < len(self.chunks):
            chunk = self.chunks[self.cursor]
            remaining = len(self.chunks) - self.cursor - 1
            if remaining > 0:
                chunk += " [!more]"
            return chunk
        return None

    def get_chunk(self, n: int) -> str | None:
        """Get a specific chunk by number (1-indexed for user-facing)."""
        idx = n - 1
        if 0 <= idx < len(self.chunks):
            self.cursor = idx
            chunk = self.chunks[idx]
            remaining = len(self.chunks) - idx - 1
            if remaining > 0:
                chunk += " [!more]"
            return chunk
        return None

    @property
    def expired(self) -> bool:
        return (time.time() - self.timestamp) > MORE_BUFFER_TTL

    @property
    def total_chunks(self) -> int:
        return len(self.chunks)


class Router:
    """Routes incoming messages to the appropriate handler.

    Simple if/elif dispatch. No state machines, no NLP.
    """

    def __init__(
        self,
        cfg: dict,
        rag_engine: RAGEngine,
        mesh_knowledge=None,
        fact_store: FactStore | None = None,
    ):
        self.cfg = cfg
        self.rag = rag_engine
        self.mesh = mesh_knowledge
        self.facts: FactStore | None = fact_store
        self._more_buffers: dict[str, MoreBuffer] = {}
        self._response_cache: dict[str, tuple[str, float]] = {}
        self._seen_senders: set[str] = set()
        self._last_query: dict[str, str] = {}  # sender_id -> last query text
        self._start_time = time.time()
        self._query_count = 0
        self._cache_file = os.path.join(cfg["_cache_dir"], "response_cache.json")
        self._load_seen_senders()
        if cfg.get("persistent_cache", True):
            self._load_disk_cache()

        # Per-sender conversation memory (disabled when memory_max_turns == 0)
        self.memory: ConversationMemory | None = None
        if cfg.get("memory_max_turns", 0) > 0:
            self.memory = ConversationMemory(cfg)
            log.info(
                f"conversation memory enabled "
                f"(max {self.memory.max_turns} turns, "
                f"ttl {self.memory.ttl}s)"
            )

        # Community message board (disabled when board_enabled is false)
        self.board: Board | None = None
        if cfg.get("board_enabled", False):
            self.board = Board(cfg)
            log.info(
                f"board enabled "
                f"(max {self.board.max_posts} posts, "
                f"ttl {self.board.post_ttl}s)"
            )

    # --- Classification (used by dispatcher) ---

    def classify(self, text: str) -> str:
        """Classify a message without processing it.

        Returns: 'empty', 'command', 'gossip', or 'query'.
        Used by the main-loop dispatcher to separate fast-path
        messages (commands, gossip) from slow-path LLM queries.
        """
        text = text.strip()
        if not text:
            return "empty"
        if text.startswith("!"):
            return "command"
        if text.startswith("DEL-FI:") and self.mesh:
            return "gossip"
        return "query"

    def busy_message(self, position: int) -> str:
        """Generate a brief busy notice for a queued sender.

        Kept short — this eats radio airtime on LoRa.
        """
        name = self.cfg["node_name"]
        if position <= 1:
            return f"{name}: Working on another question, yours is next."
        return f"{name}: {position} questions ahead of yours, hang tight."

    # --- Main entry point ---

    def route(self, sender_id: str, text: str) -> str | None:
        """Route a message and return the response to send.

        Returns None if no response should be sent.
        Returns only the first chunk for multi-chunk responses — use
        route_multi() to get all auto-send chunks at once.
        """
        text = text.strip()
        if not text:
            return None

        self._clean_expired_buffers()

        # Command dispatch (! prefix)
        if text.startswith("!"):
            response = self._handle_command(sender_id, text)
            return self._enforce_limit(response)

        # Check for gossip announcements from other Del-Fi nodes
        if text.startswith("DEL-FI:") and self.mesh:
            self.mesh.handle_announcement(sender_id, text)
            return None  # gossip is silent, no response

        # Freeform query — _handle_query already has its own enforcement
        return self._handle_query(sender_id, text)

    def route_multi(self, sender_id: str, text: str) -> list[str] | None:
        """Route a message, auto-sending up to ``auto_send_chunks`` consecutive
        messages before prompting for !more.

        Commands and single-chunk responses return a 1-element list.
        Long responses return their first N chunks directly so the user
        reads the full answer without needing to type !more, and only
        see a [!more] prompt when a further chunk exists beyond the
        auto-send window.
        """
        first = self.route(sender_id, text)
        if first is None:
            return None

        n_auto = self.cfg.get("auto_send_chunks", AUTO_SEND_CHUNKS)
        buf = self._more_buffers.get(sender_id)

        if buf is None or buf.expired or n_auto <= 1:
            # Single chunk, or auto-send disabled — return as-is
            return [first]

        # Multi-chunk response.  route() → format_response() already appended
        # [!more] to the first chunk so the protocol is consistent for callers
        # that use route() directly.  Strip it here — we control the tag.
        base_first = first[: -len(MORE_TAG)] if first.endswith(MORE_TAG) else first
        auto_msgs = [base_first]

        while len(auto_msgs) < n_auto:
            chunk = buf.next_chunk()
            if chunk is None:
                break  # exhausted before reaching the window limit

            # next_chunk() appends [!more] when further chunks remain.
            # Strip it from every position except the final auto-send slot
            # so intermediate messages read cleanly.
            is_last_slot = len(auto_msgs) == n_auto - 1
            if not is_last_slot and chunk.endswith(MORE_TAG):
                chunk = chunk[: -len(MORE_TAG)].rstrip()
            auto_msgs.append(chunk)

        return auto_msgs

    def _enforce_limit(self, text: str | None) -> str | None:
        """Truncate any outgoing message that exceeds the LoRa byte limit.

        Safety net for command responses and any other paths that don't
        go through format_response.  Truncates at sentence boundary.
        """
        if text is None:
            return None
        max_bytes = self.cfg["max_response_bytes"]
        if byte_len(text) <= max_bytes:
            return text
        return truncate_at_sentence(text, max_bytes)

    # --- Command handlers ---

    def _handle_command(self, sender_id: str, text: str) -> str:
        """Dispatch ! commands."""
        parts = text.split(None, 1)
        cmd = parts[0].lower()
        arg = parts[1] if len(parts) > 1 else ""

        handlers = {
            "!help": self._cmd_help,
            "!status": self._cmd_status,
            "!topics": self._cmd_topics,
            "!ping": self._cmd_ping,
            "!peers": self._cmd_peers,
            "!more": self._cmd_more,
            "!retry": self._cmd_retry,
            "!forget": self._cmd_forget,
            "!board": self._cmd_board,
            "!post": self._cmd_post,
            "!unpost": self._cmd_unpost,
            "!data": self._cmd_data,
        }

        handler = handlers.get(cmd)
        if handler:
            return handler(sender_id, arg)

        return f"Unknown command: {cmd}. Try !help"

    def _cmd_help(self, sender_id: str, arg: str) -> str:
        name = self.cfg["node_name"]
        docs = self.rag.doc_count
        return (
            f"{name} · AI oracle · {docs} docs\n"
            f"Ask anything in plain text.\n"
            f"!topics !status !board !post\n"
            f"!more !retry !forget !ping !peers !data"
        )

    def _cmd_status(self, sender_id: str, arg: str) -> str:
        name = self.cfg["node_name"]
        model = self.cfg["model"]
        docs = self.rag.doc_count
        uptime = self._format_uptime()
        ollama_ok = "✓" if self.rag.available else "✗"
        rag_ok = "✓" if self.rag.rag_available else "✗"
        return (
            f"{name} up {uptime} · {model} · {docs} docs\n"
            f"queries: {self._query_count}\n"
            f"ollama: {ollama_ok} · rag: {rag_ok}"
        )

    def _cmd_topics(self, sender_id: str, arg: str) -> str:
        topics = self.rag.get_topics()
        if not topics:
            return (
                "No documents loaded. Drop .txt or .md files "
                "into the knowledge folder."
            )
        return "Topics: " + ", ".join(topics)

    def _cmd_ping(self, sender_id: str, arg: str) -> str:
        return f"pong from {self.cfg['node_name']}"

    def _cmd_peers(self, sender_id: str, arg: str) -> str:
        if not self.mesh:
            return "Mesh knowledge not configured on this node."
        return self.mesh.format_peers_response()

    def _cmd_more(self, sender_id: str, arg: str) -> str:
        buf = self._more_buffers.get(sender_id)
        if not buf or buf.expired:
            return "No pending response. Send a question first."

        # !more N → specific chunk (1-indexed)
        if arg.strip().isdigit():
            n = int(arg.strip())
            chunk = buf.get_chunk(n)
            if chunk:
                return chunk
            return f"No chunk {n}. Response has {buf.total_chunks} parts."

        # !more → next chunk
        chunk = buf.next_chunk()
        if chunk:
            return chunk
        return "End of response. No more chunks."

    def _cmd_retry(self, sender_id: str, arg: str) -> str:
        """Re-run the last query, bypassing and replacing the cache."""
        last = self._last_query.get(sender_id)
        if not last:
            return "No previous query to retry. Ask a question first."

        # Evict the old cached answer
        key = last.lower().strip()
        if key in self._response_cache:
            del self._response_cache[key]
            if self.cfg.get("persistent_cache", True):
                self._save_disk_cache()
            log.info(f"  cache evicted for retry: {key[:40]}")

        # Re-run through the full query pipeline
        return self._handle_query(sender_id, last)

    def _cmd_forget(self, sender_id: str, arg: str) -> str:
        """Clear conversation memory for this sender."""
        if not self.memory:
            return "Conversation memory is not enabled on this node."
        self.memory.clear(sender_id)
        return "Memory cleared. I won't remember our previous conversation."

    def _cmd_board(self, sender_id: str, arg: str) -> str:
        """Read the community board, optionally filtered by a search query."""
        if not self.board:
            return "The board is not enabled on this node."
        return self.board.read(arg)

    def _cmd_post(self, sender_id: str, arg: str) -> str:
        """Post a message to the community board."""
        if not self.board:
            return "The board is not enabled on this node."
        return self.board.post(sender_id, arg)

    def _cmd_unpost(self, sender_id: str, arg: str) -> str:
        """Remove all of your posts from the board."""
        if not self.board:
            return "The board is not enabled on this node."
        return self.board.clear(sender_id)

    def _cmd_data(self, sender_id: str, arg: str) -> str:
        """Show a snapshot of all current sensor facts from the FactStore."""
        if not self.facts or not self.facts.has_facts():
            return (
                "No sensor data loaded. Write readings to "
                "cache/sensor_feed.json (see sensor_feed.example.json)."
            )
        return self.facts.format_snapshot()

    # --- Tier 0: FactStore ---

    def _tier0_facts(self, query: str) -> str | None:
        """Answer sensor / measurement queries directly from FactStore.

        No LLM call is made. Returns a formatted string if one or more matching
        facts are found, or None to fall through to RAG.

        Matching logic:
          1. Quick bail if none of the configured fact_query_keywords appear in
             the lowercased query (avoids scanning the fact store unnecessarily).
          2. Tokenise both the query and each fact key; return facts whose key
             tokens overlap with the query tokens.
        """
        if not self.facts or not self.facts.has_facts():
            return None

        keywords: list[str] = self.cfg.get("fact_query_keywords", [])
        q_lower = query.lower()

        # Fast bail: no known sensor keyword in query
        if not any(kw in q_lower for kw in keywords):
            return None

        # Tokenise the query for key-matching
        q_words = set(re.sub(r"[^\w]", " ", q_lower).split())

        all_facts = self.facts.get_all()
        matched_keys = []
        for key in all_facts:
            # Replace underscores AND non-word chars so "temperature_f" splits to
            # {"temperature", "f"}, which then intersects with query token "temperature"
            key_tokens = set(re.sub(r"[^\w]", " ", key.lower()).replace("_", " ").split())
            if q_words & key_tokens:
                matched_keys.append(key)

        if not matched_keys:
            return None  # keyword hit but no specific fact key — fall to RAG

        lines = [
            self.facts.format_value(k)
            for k in sorted(matched_keys)
            if self.facts.format_value(k)
        ]
        if not lines:
            return None

        name = self.cfg["node_name"]
        return name + ": " + " | ".join(lines)

    # --- Query handling ---

    def _handle_query(self, sender_id: str, text: str) -> str:
        """Process a freeform query through the RAG pipeline."""
        self._query_count += 1
        self._last_query[sender_id] = text  # track for !retry

        # Gather conversation history for this sender
        history = ""
        if self.memory:
            history = self.memory.format_for_prompt(sender_id)

        # Gather relevant board context (if board is enabled)
        board_context = ""
        if self.board and self.board.post_count > 0:
            board_context = self.board.format_for_context(query=text)

        # Handle simple greetings
        if self._is_greeting(text) and sender_id not in self._seen_senders:
            self._mark_seen(sender_id)
            name = self.cfg["node_name"]
            docs = self.rag.doc_count
            return (
                f"Hi from {name}. I answer questions using local docs.\n"
                f"Try asking something, or send !help · !topics"
            )

        # --- Tier 0: FactStore (sensor / measurement queries, no LLM) ---
        # Runs BEFORE the response cache so fresh sensor readings are never
        # served from a stale cached reply. Results are not cached for the same
        # reason — freshness is the whole point of Tier 0.
        fact_response = self._tier0_facts(text)
        if fact_response is not None:
            log.info("  tier0: fact match — returning direct sensor value")
            return self._finalize(sender_id, fact_response)

        # Check response cache (exact match)
        cached = self._check_cache(text)
        if cached:
            log.info("  cache hit")
            return self._finalize(sender_id, cached)

        # Ollama not ready yet?
        if not self.rag.available:
            return "I'm still warming up, try again in a minute."

        # RAG retrieval
        chunks = self.rag.query(text)

        # Route based on what we found
        provenance = None
        had_context = False
        if chunks:
            # Good local match — generate from operator knowledge
            had_context = True
            response = self.rag.generate(
                text, context_chunks=chunks, history=history,
                board_context=board_context,
            )
        else:
            # No local match — check peer cache (Tier 2)
            peer_result = None
            if self.mesh:
                peer_result = self.mesh.check_peer_cache(text)

            if peer_result:
                had_context = True
                log.info(f"  peer: found match from {peer_result['peer_name']}")
                provenance = peer_result["peer_name"]
                peer_ctx = f"[{peer_result['peer_name']}]: {peer_result['response']}"
                response = self.rag.generate(
                    text, peer_context=peer_ctx, history=history,
                    board_context=board_context,
                )
            else:
                # Check gossip for referral (Tier 3)
                if self.mesh:
                    referral = self.mesh.find_referral(text)
                    if referral:
                        return self._finalize(sender_id, referral)

                # No local docs, no peer cache — refuse rather than hallucinate.
                # Raw LLM answers with no grounding are unreliable; better to
                # tell the user explicitly than to fabricate an answer.
                log.info("  no context found — declining to answer")
                name = self.cfg["node_name"]
                response = (
                    f"{name}: I don't have anything in my knowledge base about that. "
                    f"Try !topics to see what I know."
                )
                return self._finalize(sender_id, response)

        if not response:
            return "I'm having trouble thinking right now. Try again in a minute."

        # Only cache responses backed by good RAG retrieval or peer data.
        # Raw LLM fallback (no context) is more likely to hallucinate,
        # so we don't cache those.
        if had_context:
            self._cache_response(text, response)
        else:
            log.info("  skipping cache — no RAG context (hallucination risk)")

        # Record the exchange in conversation memory
        if self.memory and response:
            self.memory.add_turn(sender_id, text, response)

        return self._finalize(sender_id, response, provenance=provenance)

    def _finalize(
        self, sender_id: str, text: str, provenance: str | None = None
    ) -> str:
        """Format response, handle chunking, add welcome footer for first contact."""
        max_bytes = self.cfg["max_response_bytes"]

        first_msg, all_chunks, is_truncated = format_response(
            text, max_bytes=max_bytes, provenance=provenance
        )

        # Welcome footer for first-time senders
        if sender_id not in self._seen_senders:
            self._mark_seen(sender_id)
            docs = self.rag.doc_count
            footer = f"\n---\nDel-Fi oracle · {docs} docs · !help !topics"
            with_footer = first_msg + footer
            if byte_len(with_footer) <= max_bytes:
                first_msg = with_footer

        # Store buffer for !more
        if is_truncated:
            self._more_buffers[sender_id] = MoreBuffer(all_chunks, time.time())

        return first_msg

    # --- Greeting detection ---

    def _is_greeting(self, text: str) -> bool:
        """Check if message is a simple greeting, not a question."""
        cleaned = text.lower().strip().rstrip("!.,?")
        return cleaned in GREETINGS

    # --- Response cache ---

    def _check_cache(self, query: str) -> str | None:
        """Check if we've recently answered this exact query."""
        key = query.lower().strip()
        if key in self._response_cache:
            response, ts = self._response_cache[key]
            if time.time() - ts < self.cfg["response_cache_ttl"]:
                return response
            del self._response_cache[key]
        return None

    def _cache_response(self, query: str, response: str):
        """Cache a response for future identical queries."""
        key = query.lower().strip()
        self._response_cache[key] = (response, time.time())

        # Periodic eviction to prevent unbounded growth
        if len(self._response_cache) > 100:
            now = time.time()
            ttl = self.cfg["response_cache_ttl"]
            self._response_cache = {
                k: (v, t)
                for k, (v, t) in self._response_cache.items()
                if now - t < ttl
            }

        if self.cfg.get("persistent_cache", True):
            self._save_disk_cache()

    # --- Persistent disk cache ---

    def _load_disk_cache(self):
        """Load response cache from disk. Losing this is harmless."""
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
        """Persist response cache to disk. Best effort."""
        try:
            os.makedirs(os.path.dirname(self._cache_file), exist_ok=True)
            data = {
                k: {"response": v, "ts": t}
                for k, (v, t) in self._response_cache.items()
            }
            with open(self._cache_file, "w") as f:
                json.dump(data, f)
        except Exception:
            pass  # best effort

    # --- Seen senders persistence ---

    def _mark_seen(self, sender_id: str):
        """Mark a sender as seen and persist to disk."""
        self._seen_senders.add(sender_id)
        self._save_seen_senders()

    def _load_seen_senders(self):
        """Load seen sender IDs from disk. Losing this is harmless."""
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
        """Persist seen sender IDs to disk. Best effort."""
        path = self.cfg["_seen_senders_file"]
        try:
            os.makedirs(os.path.dirname(path), exist_ok=True)
            with open(path, "w") as f:
                for s in sorted(self._seen_senders):
                    f.write(s + "\n")
        except Exception:
            pass

    # --- Housekeeping ---

    def _clean_expired_buffers(self):
        """Remove expired !more buffers."""
        expired = [k for k, v in self._more_buffers.items() if v.expired]
        for k in expired:
            del self._more_buffers[k]

    def _format_uptime(self) -> str:
        """Human-readable uptime string."""
        elapsed = int(time.time() - self._start_time)
        days = elapsed // 86400
        hours = (elapsed % 86400) // 3600
        if days > 0:
            return f"{days}d {hours}h"
        minutes = (elapsed % 3600) // 60
        return f"{hours}h {minutes}m"
