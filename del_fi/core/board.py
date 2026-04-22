"""Community message board for Del-Fi.

Users post with !post <message>, read with !board [query],
remove their own posts with !unpost.
"""

import json
import logging
import os
import re
import threading
import time

log = logging.getLogger("del_fi.core.board")

DEFAULT_MAX_POSTS = 50
DEFAULT_POST_TTL = 86400
DEFAULT_SHOW_COUNT = 5
DEFAULT_RATE_LIMIT = 3
DEFAULT_RATE_WINDOW = 3600
MAX_POSTS_HARD_CAP = 500
MAX_POST_LENGTH = 200

_BUILTIN_BLOCKED = [
    r"ignore\s+(previous|above|all)\s+(instructions|prompts?)",
    r"you\s+are\s+now\b",
    r"new\s+instructions?\s*:",
    r"system\s*prompt\s*:",
    r"<\s*/?\s*system\s*>",
]


class Board:
    """Community message board with TTL, rate limiting, and content filtering."""

    def __init__(self, cfg: dict):
        self.max_posts: int = min(
            cfg.get("board_max_posts", DEFAULT_MAX_POSTS),
            MAX_POSTS_HARD_CAP,
        )
        self.post_ttl: int = cfg.get("board_post_ttl", DEFAULT_POST_TTL)
        self.show_count: int = cfg.get("board_show_count", DEFAULT_SHOW_COUNT)
        self._persist: bool = cfg.get("board_persist", True)
        self._board_file: str = os.path.join(
            cfg.get("_cache_dir", "."), "board.json"
        )
        self._rate_limit: int = cfg.get("board_rate_limit", DEFAULT_RATE_LIMIT)
        self._rate_window: int = cfg.get("board_rate_window", DEFAULT_RATE_WINDOW)
        self._post_times: dict[str, list[float]] = {}

        extra_patterns = cfg.get("board_blocked_patterns", [])
        raw_patterns = _BUILTIN_BLOCKED + (
            extra_patterns if isinstance(extra_patterns, list) else []
        )
        self._blocked_re: list[re.Pattern] = []
        for pat in raw_patterns:
            try:
                self._blocked_re.append(re.compile(pat, re.IGNORECASE))
            except re.error as e:
                log.warning(f"bad board filter pattern '{pat}': {e}")

        self._posts: list[dict] = []
        self._lock = threading.Lock()

        if self._persist:
            self._load_disk()

    # --- Public API ---

    def post(self, sender_id: str, text: str) -> str:
        """Add a message to the board. Returns confirmation string."""
        text = text.strip()
        if not text:
            return "Usage: !post <message>"

        if len(text) > MAX_POST_LENGTH:
            return f"Post too long ({len(text)} chars). Keep it under {MAX_POST_LENGTH}."

        if not self._check_rate(sender_id):
            return (
                f"Slow down — max {self._rate_limit} posts "
                f"per {self._rate_window // 60} min."
            )

        blocked = self._check_content(text)
        if blocked:
            log.warning(f"board post blocked from {sender_id}: matched filter [{blocked}]")
            return "Post rejected by content filter."

        with self._lock:
            self._expire()
            self._posts.append({"sender": sender_id, "text": text, "ts": time.time()})
            if len(self._posts) > self.max_posts:
                self._posts = self._posts[-self.max_posts:]
            count = len(self._posts)

        if self._persist:
            self._save_disk()

        log.info(f"board post from {sender_id}: {text[:60]}")
        return f"Posted to board ({count} messages total)."

    def read(self, query: str = "") -> str:
        """Read the board. Empty query = recent posts. Non-empty = search."""
        query = query.strip()
        with self._lock:
            self._expire()
            if not self._posts:
                return "The board is empty. Post with: !post <message>"
            if query:
                return self._search(query)
            return self._recent()

    def clear(self, sender_id: str) -> str:
        """Remove all posts from a sender."""
        with self._lock:
            before = len(self._posts)
            self._posts = [p for p in self._posts if p["sender"] != sender_id]
            removed = before - len(self._posts)

        if self._persist and removed:
            self._save_disk()

        if removed == 0:
            return "You have no posts on the board."
        return f"Removed {removed} post(s)."

    @property
    def post_count(self) -> int:
        with self._lock:
            return len(self._posts)

    def format_for_context(self, query: str = "") -> str:
        """Format board posts for LLM context (sandboxed)."""
        with self._lock:
            self._expire()
            if not self._posts:
                return ""
            posts = list(self._posts[-10:])

        lines = ["=== BEGIN BOARD POSTS (untrusted user content) ==="]
        for p in posts:
            lines.append(f"[{p['sender']}]: {p['text']}")
        lines.append("=== END BOARD POSTS ===")
        return "\n".join(lines)

    # --- Internal ---

    def _recent(self) -> str:
        recent = self._posts[-self.show_count:]
        lines = []
        for p in reversed(recent):
            age = int((time.time() - p["ts"]) / 60)
            age_str = f"{age}m ago" if age < 60 else f"{age // 60}h ago"
            lines.append(f"[{p['sender']} {age_str}]: {p['text']}")
        return "\n".join(lines)

    def _search(self, query: str) -> str:
        q = query.lower()
        matched = [p for p in self._posts if q in p["text"].lower()]
        if not matched:
            return f"No posts matching '{query}'."
        matched = matched[-self.show_count:]
        lines = []
        for p in reversed(matched):
            lines.append(f"[{p['sender']}]: {p['text']}")
        return "\n".join(lines)

    def _check_rate(self, sender_id: str) -> bool:
        now = time.time()
        times = self._post_times.get(sender_id, [])
        times = [t for t in times if now - t < self._rate_window]
        if len(times) >= self._rate_limit:
            self._post_times[sender_id] = times
            return False
        times.append(now)
        self._post_times[sender_id] = times
        return True

    def _check_content(self, text: str) -> str | None:
        for pattern in self._blocked_re:
            if pattern.search(text):
                return pattern.pattern
        return None

    def _expire(self):
        now = time.time()
        self._posts = [p for p in self._posts if now - p["ts"] < self.post_ttl]

    def _load_disk(self):
        try:
            if os.path.exists(self._board_file):
                with open(self._board_file) as f:
                    data = json.load(f)
                self._posts = data.get("posts", [])
                self._expire()
                log.info(f"board loaded ({len(self._posts)} posts)")
        except Exception as e:
            log.warning(f"could not load board: {e}")

    def _save_disk(self):
        try:
            tmp = self._board_file + ".tmp"
            with open(tmp, "w") as f:
                json.dump({"posts": self._posts}, f)
            os.replace(tmp, self._board_file)
        except Exception as e:
            log.warning(f"could not save board: {e}")
