"""Query routing and command handling.

Receives messages from the mesh interface, dispatches ! commands,
routes freeform queries through the RAG pipeline, and manages
response buffering for the !more chunking system.
"""

import logging
import os
import time

from formatter import byte_len, format_response
from rag import RAGEngine

log = logging.getLogger("delfi.router")

# !more buffers expire after 10 minutes
MORE_BUFFER_TTL = 600

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

    def __init__(self, cfg: dict, rag_engine: RAGEngine, mesh_knowledge=None):
        self.cfg = cfg
        self.rag = rag_engine
        self.mesh = mesh_knowledge
        self._more_buffers: dict[str, MoreBuffer] = {}
        self._response_cache: dict[str, tuple[str, float]] = {}
        self._seen_senders: set[str] = set()
        self._start_time = time.time()
        self._query_count = 0
        self._load_seen_senders()

    # --- Main entry point ---

    def route(self, sender_id: str, text: str) -> str | None:
        """Route a message and return the response to send.

        Returns None if no response should be sent.
        """
        text = text.strip()
        if not text:
            return None

        self._clean_expired_buffers()

        # Command dispatch (! prefix)
        if text.startswith("!"):
            return self._handle_command(sender_id, text)

        # Check for gossip announcements from other Del-Fi nodes
        if text.startswith("DEL-FI:") and self.mesh:
            self.mesh.handle_announcement(sender_id, text)
            return None  # gossip is silent, no response

        # Freeform query
        return self._handle_query(sender_id, text)

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
        }

        handler = handlers.get(cmd)
        if handler:
            return handler(sender_id, arg)

        return f"Unknown command: {cmd}. Try !help"

    def _cmd_help(self, sender_id: str, arg: str) -> str:
        name = self.cfg["node_name"]
        model = self.cfg["model"]
        docs = self.rag.doc_count
        return (
            f"{name} · community AI oracle\n"
            f"Ask questions in plain text. I search local "
            f"docs and answer concisely. DM only.\n"
            f"Commands: !help !topics !status !more !ping !peers\n"
            f"Powered by {model} · {docs} docs indexed"
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

    # --- Query handling ---

    def _handle_query(self, sender_id: str, text: str) -> str:
        """Process a freeform query through the RAG pipeline."""
        self._query_count += 1

        # Handle simple greetings
        if self._is_greeting(text) and sender_id not in self._seen_senders:
            self._mark_seen(sender_id)
            name = self.cfg["node_name"]
            docs = self.rag.doc_count
            return (
                f"Hi from {name}. I answer questions using local docs.\n"
                f"Try asking something, or send !help · !topics"
            )

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
        if chunks:
            # Good local match — generate from operator knowledge
            response = self.rag.generate(text, context_chunks=chunks)
        else:
            # No local match — check peer cache (Tier 2)
            peer_result = None
            if self.mesh:
                peer_result = self.mesh.check_peer_cache(text)

            if peer_result:
                log.info(f"  peer: found match from {peer_result['peer_name']}")
                provenance = peer_result["peer_name"]
                peer_ctx = f"[{peer_result['peer_name']}]: {peer_result['response']}"
                response = self.rag.generate(text, peer_context=peer_ctx)
            else:
                # Check gossip for referral (Tier 3)
                if self.mesh:
                    referral = self.mesh.find_referral(text)
                    if referral:
                        return self._finalize(sender_id, referral)

                # Fall back to raw LLM (no context)
                response = self.rag.generate(text)

        if not response:
            return "I'm having trouble thinking right now. Try again in a minute."

        # Cache the successful response
        self._cache_response(text, response)

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
