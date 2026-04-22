"""Per-sender conversation memory for Del-Fi."""

import json
import logging
import os
import threading
import time

log = logging.getLogger("del_fi.core.memory")

MAX_TURNS_HARD_CAP = 50
DEFAULT_MAX_TURNS = 10
DEFAULT_MEMORY_TTL = 3600


class ConversationMemory:
    """Per-sender conversation history with TTL and optional persistence.

    Each 'turn' is a (user_msg, assistant_msg) pair stored in a ring buffer.
    Conversations expire after ``ttl`` seconds of inactivity.
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
            if len(entry["turns"]) > self.max_turns:
                entry["turns"] = entry["turns"][-self.max_turns:]
            entry["ts"] = time.time()

        if self._persist:
            self._save_disk()

    def get_history(self, sender_id: str) -> list[tuple[str, str]]:
        """Return recent turns for a sender (oldest first)."""
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
            return sum(1 for e in self._store.values() if not self._expired(e))

    def cleanup(self):
        """Remove expired entries. Call periodically."""
        with self._lock:
            expired_keys = [k for k, v in self._store.items() if self._expired(v)]
            for k in expired_keys:
                del self._store[k]
        if expired_keys and self._persist:
            self._save_disk()

    def format_for_prompt(self, sender_id: str) -> str:
        """Format conversation history as a prompt fragment."""
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
                    log.info(f"loaded conversation memory for {loaded} senders")
        except Exception as e:
            log.warning(f"could not load conversation memory: {e}")

    def _save_disk(self):
        try:
            data = {
                sender: {"turns": list(e["turns"]), "ts": e["ts"]}
                for sender, e in self._store.items()
            }
            tmp = self._memory_file + ".tmp"
            with open(tmp, "w") as f:
                json.dump(data, f)
            os.replace(tmp, self._memory_file)
        except Exception as e:
            log.warning(f"could not save conversation memory: {e}")
