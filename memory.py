"""Per-sender conversation memory for Del-Fi.

Stores recent exchanges (user query + assistant response) in a ring
buffer per sender. Injected into the LLM prompt so the oracle can
maintain context across messages.

Memory is intentionally lightweight — designed for low-bandwidth
mesh radio where conversations are short and sporadic.
"""

import json
import logging
import os
import threading
import time

log = logging.getLogger("delfi.memory")

# Sane limits to prevent runaway memory use
MAX_TURNS_HARD_CAP = 50
DEFAULT_MAX_TURNS = 10
DEFAULT_MEMORY_TTL = 3600  # 1 hour


class ConversationMemory:
    """Per-sender conversation history with TTL and persistence.

    Each 'turn' is a (user_msg, assistant_msg) pair.  The ring buffer
    keeps the most recent `max_turns` pairs per sender.  Entire
    conversations expire after `ttl` seconds of inactivity.
    """

    def __init__(self, cfg: dict):
        self.max_turns: int = min(
            cfg.get("memory_max_turns", DEFAULT_MAX_TURNS),
            MAX_TURNS_HARD_CAP,
        )
        self.ttl: int = cfg.get("memory_ttl", DEFAULT_MEMORY_TTL)
        self._persist: bool = cfg.get("persistent_memory", False)
        self._memory_file: str = os.path.join(
            cfg.get("_cache_dir", "."), "conversation_memory.json"
        )

        # sender_id -> {"turns": [(user, assistant), ...], "ts": float}
        self._store: dict[str, dict] = {}
        self._lock = threading.Lock()

        if self._persist:
            self._load_disk()

    # --- Public API ---

    def add_turn(self, sender_id: str, user_msg: str, assistant_msg: str):
        """Record a completed exchange."""
        with self._lock:
            entry = self._store.get(sender_id)
            if entry is None or self._expired(entry):
                entry = {"turns": [], "ts": time.time()}
                self._store[sender_id] = entry

            entry["turns"].append((user_msg, assistant_msg))
            # Trim to ring-buffer size
            if len(entry["turns"]) > self.max_turns:
                entry["turns"] = entry["turns"][-self.max_turns:]
            entry["ts"] = time.time()

        if self._persist:
            self._save_disk()

    def get_history(self, sender_id: str) -> list[tuple[str, str]]:
        """Return recent turns for a sender (oldest first).

        Returns an empty list if no history or conversation expired.
        """
        with self._lock:
            entry = self._store.get(sender_id)
            if entry is None or self._expired(entry):
                return []
            return list(entry["turns"])

    def clear(self, sender_id: str):
        """Wipe history for a single sender."""
        with self._lock:
            self._store.pop(sender_id, None)
        if self._persist:
            self._save_disk()

    def clear_all(self):
        """Wipe all conversation history."""
        with self._lock:
            self._store.clear()
        if self._persist:
            self._save_disk()

    def sender_count(self) -> int:
        """Number of senders with active (non-expired) history."""
        with self._lock:
            return sum(
                1 for e in self._store.values() if not self._expired(e)
            )

    def cleanup(self):
        """Remove expired entries. Call periodically."""
        with self._lock:
            expired_keys = [
                k for k, v in self._store.items() if self._expired(v)
            ]
            for k in expired_keys:
                del self._store[k]
        if expired_keys and self._persist:
            self._save_disk()

    # --- Prompt formatting ---

    def format_for_prompt(self, sender_id: str) -> str:
        """Format conversation history as a prompt fragment.

        Returns an empty string if no history exists.
        """
        turns = self.get_history(sender_id)
        if not turns:
            return ""

        lines = ["Recent conversation with this user:"]
        for user_msg, assistant_msg in turns:
            lines.append(f"User: {user_msg}")
            lines.append(f"Assistant: {assistant_msg}")
        return "\n".join(lines)

    # --- Internal ---

    def _expired(self, entry: dict) -> bool:
        return (time.time() - entry["ts"]) > self.ttl

    def _load_disk(self):
        """Load persisted memory. Losing this is harmless."""
        try:
            if os.path.exists(self._memory_file):
                with open(self._memory_file) as f:
                    data = json.load(f)
                now = time.time()
                for sender_id, entry in data.items():
                    ts = entry.get("ts", 0)
                    if now - ts < self.ttl:
                        turns = [tuple(t) for t in entry.get("turns", [])]
                        self._store[sender_id] = {"turns": turns, "ts": ts}
                loaded = len(self._store)
                if loaded:
                    log.info(
                        f"loaded conversation memory for {loaded} senders"
                    )
        except Exception as e:
            log.warning(f"could not load conversation memory: {e}")

    def _save_disk(self):
        """Persist memory to disk. Best effort."""
        try:
            os.makedirs(os.path.dirname(self._memory_file), exist_ok=True)
            with self._lock:
                data = {
                    sender_id: {
                        "turns": entry["turns"],
                        "ts": entry["ts"],
                    }
                    for sender_id, entry in self._store.items()
                    if not self._expired(entry)
                }
            with open(self._memory_file, "w") as f:
                json.dump(data, f)
        except Exception:
            pass  # best effort
