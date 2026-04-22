"""Peer knowledge layers for Del-Fi.

PeerCache (Tier 2)
------------------
SQLite-backed cache of Q&A answers from trusted peer nodes.
Answers are stored with TTL and matched by keyword overlap.

GossipDirectory (Tier 3)
-------------------------
Lightweight directory of other Del-Fi nodes on the mesh, built from
their broadcast announcements. Never caches answers; only provides
referrals: "Try NODE — covers [topic]".

Gossip announcement protocol
------------------------------
  DEL-FI:1:ANNOUNCE:<node_name>:topics=<t1,t2,...>:model=<model>
  Example:
  DEL-FI:1:ANNOUNCE:VALLEY-ORACLE:topics=geology,mining,local-history:model=llama3.2

The protocol version (1) allows future breaking changes without ambiguity.
"""

import json
import logging
import os
import re
import sqlite3
import threading
import time

log = logging.getLogger("del_fi.core.peers")

PROTOCOL_VERSION = 1
ANNOUNCE_PREFIX = f"DEL-FI:{PROTOCOL_VERSION}:ANNOUNCE:"
GOSSIP_TTL_SECONDS = 86400  # 24 hours
JACCARD_THRESHOLD = 0.5
MAX_CACHE_ENTRIES_DEFAULT = 500

_STOP_WORDS = frozenset({
    "a", "an", "the", "is", "it", "in", "on", "at", "to", "for",
    "of", "and", "or", "not", "what", "where", "when", "how",
    "who", "which", "that", "this", "there", "here", "with",
    "from", "about", "i", "me", "my", "you", "your", "we", "our",
})


# ─────────────────────────── PeerCache ────────────────────────────────────

class PeerCache:
    """Stores Q&A answers received from trusted peer nodes.

    Thread-safe SQLite WAL database.  Trusted peers are configured via
    the ``trusted_peers`` config key (list of node names).
    """

    CREATE_DDL = """
        CREATE TABLE IF NOT EXISTS peer_cache (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            peer_id     TEXT    NOT NULL,
            peer_name   TEXT    NOT NULL,
            query       TEXT    NOT NULL,
            response    TEXT    NOT NULL,
            timestamp   REAL    NOT NULL,
            ttl         REAL    NOT NULL
        );
        CREATE INDEX IF NOT EXISTS idx_peer_cache_ts ON peer_cache(timestamp);
    """

    def __init__(self, cfg: dict):
        self.cfg = cfg
        self._trusted: set[str] = {
            p.upper() for p in cfg.get("trusted_peers", [])
        }
        self._ttl: float = cfg.get("peer_cache_ttl", GOSSIP_TTL_SECONDS)
        self._max_entries: int = cfg.get("max_cache_entries", MAX_CACHE_ENTRIES_DEFAULT)
        cache_dir = cfg.get("_cache_dir", ".")
        os.makedirs(cache_dir, exist_ok=True)
        self._db_path = os.path.join(cache_dir, "mesh-answers.db")
        self._lock = threading.Lock()
        self._init_db()

    # --- Public API ---

    def lookup(self, query: str) -> dict | None:
        """Return the best matching cached answer for *query*, or None.

        Matching uses Jaccard similarity on word tokens; returns the
        highest-scoring result above JACCARD_THRESHOLD.
        """
        query_tokens = _tokenize(query)
        if not query_tokens:
            return None

        now = time.time()
        with self._lock:
            conn = self._conn()
            rows = conn.execute(
                "SELECT peer_id, peer_name, query, response, timestamp "
                "FROM peer_cache WHERE timestamp + ttl > ?",
                (now,),
            ).fetchall()

        best_score = 0.0
        best_row = None
        for peer_id, peer_name, q, response, ts in rows:
            row_tokens = _tokenize(q)
            score = _jaccard(query_tokens, row_tokens)
            if score > best_score:
                best_score = score
                best_row = (peer_id, peer_name, q, response, ts)

        if best_score >= JACCARD_THRESHOLD and best_row is not None:
            peer_id, peer_name, q, response, ts = best_row
            return {
                "peer_id": peer_id,
                "peer_name": peer_name,
                "query": q,
                "response": response,
                "score": best_score,
                "timestamp": ts,
            }
        return None

    def store(
        self, query: str, answer: str, peer_id: str, peer_name: str
    ):
        """Cache an answer from a peer node. Only accepts trusted peers."""
        if not peer_id.upper() in self._trusted and not peer_name.upper() in self._trusted:
            log.debug(f"ignoring answer from untrusted peer {peer_name!r}")
            return

        now = time.time()
        with self._lock:
            conn = self._conn()
            conn.execute(
                "INSERT INTO peer_cache "
                "(peer_id, peer_name, query, response, timestamp, ttl) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                (peer_id, peer_name, query, answer, now, self._ttl),
            )
            conn.commit()
        self.prune()

    def prune(self):
        """Remove expired entries and enforce max_cache_entries."""
        now = time.time()
        with self._lock:
            conn = self._conn()
            conn.execute(
                "DELETE FROM peer_cache WHERE timestamp + ttl <= ?", (now,)
            )
            count = conn.execute(
                "SELECT COUNT(*) FROM peer_cache"
            ).fetchone()[0]
            if count > self._max_entries:
                excess = count - self._max_entries
                conn.execute(
                    "DELETE FROM peer_cache WHERE id IN "
                    "(SELECT id FROM peer_cache ORDER BY timestamp ASC LIMIT ?)",
                    (excess,),
                )
            conn.commit()

    @property
    def entry_count(self) -> int:
        with self._lock:
            conn = self._conn()
            return conn.execute("SELECT COUNT(*) FROM peer_cache").fetchone()[0]

    # --- Internal ---

    def _init_db(self):
        try:
            conn = sqlite3.connect(self._db_path, check_same_thread=False)
            conn.execute("PRAGMA journal_mode=WAL")
            conn.executescript(self.CREATE_DDL)
            conn.commit()
            self._db: sqlite3.Connection = conn
            log.info(f"peer cache ready at {self._db_path}")
        except Exception as e:
            log.error(f"could not init peer cache DB: {e}")
            raise

    def _conn(self) -> sqlite3.Connection:
        return self._db


# ─────────────────────────── GossipDirectory ──────────────────────────────

class GossipDirectory:
    """Directory of other Del-Fi nodes on the mesh.

    Built from broadcast announcements; entries expire after GOSSIP_TTL_SECONDS.
    Never stores knowledge — only metadata about other nodes and their topics.
    """

    def __init__(self, cfg: dict):
        self.cfg = cfg
        self._gossip_dir = cfg.get("_gossip_dir", "gossip")
        self._dir_file = os.path.join(self._gossip_dir, "node-directory.json")
        self._nodes: dict[str, dict] = {}
        self._lock = threading.Lock()
        os.makedirs(self._gossip_dir, exist_ok=True)
        self._load_disk()

    # --- Public API ---

    def receive(self, node_id: str, announcement_text: str):
        """Parse and store a node announcement from the mesh.

        Announcement format:
          DEL-FI:1:ANNOUNCE:<node_name>:topics=<t1,t2,...>:model=<model>
        """
        text = announcement_text.strip()
        if not text.startswith(ANNOUNCE_PREFIX):
            return

        rest = text[len(ANNOUNCE_PREFIX):]
        parts = rest.split(":")
        if not parts:
            return

        node_name = parts[0].upper()
        meta: dict = {"model": "unknown", "topics": []}
        for part in parts[1:]:
            if "=" not in part:
                continue
            k, _, v = part.partition("=")
            k = k.strip()
            if k == "topics":
                meta["topics"] = [t.strip() for t in v.split(",") if t.strip()]
            elif k == "model":
                meta["model"] = v.strip()

        entry = {
            "node_id": node_id,
            "node_name": node_name,
            "topics": meta["topics"],
            "model": meta["model"],
            "last_seen": time.time(),
        }

        with self._lock:
            self._nodes[node_name] = entry

        self._save_disk()
        log.info(
            f"gossip: received announcement from {node_name} "
            f"({len(meta['topics'])} topic(s))"
        )

    def referral(self, query: str) -> str | None:
        """Return a referral if another node covers the query topic.

        Example: "Try VALLEY-ORACLE — covers geology, mining, local-history"
        """
        self._expire()
        query_tokens = _tokenize(query)
        if not query_tokens:
            return None

        with self._lock:
            nodes = list(self._nodes.values())

        best_node = None
        best_overlap = 0

        for node in nodes:
            topic_tokens = set(
                _tokenize(" ".join(node.get("topics", [])))
            )
            overlap = len(set(query_tokens) & topic_tokens)
            if overlap > best_overlap:
                best_overlap = overlap
                best_node = node

        if best_node and best_overlap > 0:
            name = best_node["node_name"]
            topics = ", ".join(best_node["topics"][:5])
            return f"Try {name} — covers {topics}"

        return None

    def announce(self) -> str:
        """Build this node's announcement string for broadcast."""
        name = self.cfg.get("node_name", "UNNAMED")
        topics = self._local_topics()
        model = self.cfg.get("model", "unknown")
        topics_str = ",".join(topics)
        return f"{ANNOUNCE_PREFIX}{name}:topics={topics_str}:model={model}"

    def list_peers(self) -> list[dict]:
        """Return list of active (non-expired) peer entries."""
        self._expire()
        with self._lock:
            return list(self._nodes.values())

    @property
    def peer_count(self) -> int:
        self._expire()
        with self._lock:
            return len(self._nodes)

    # --- Internal ---

    def _local_topics(self) -> list[str]:
        """Infer local topics from wiki/index.md if available."""
        try:
            wiki_dir = self.cfg.get("wiki_folder", "./wiki")
            index = os.path.join(wiki_dir, "index.md")
            if os.path.exists(index):
                with open(index, encoding="utf-8") as f:
                    content = f.read()
                slugs = re.findall(r"\[\[([^\]]+)\]\]", content)
                return slugs[:10]
        except Exception:
            pass
        return []

    def _expire(self):
        now = time.time()
        with self._lock:
            expired = [
                k for k, v in self._nodes.items()
                if now - v["last_seen"] > GOSSIP_TTL_SECONDS
            ]
            for k in expired:
                del self._nodes[k]
        if expired:
            self._save_disk()

    def _load_disk(self):
        try:
            if os.path.exists(self._dir_file):
                with open(self._dir_file) as f:
                    data = json.load(f)
                with self._lock:
                    self._nodes = data
                self._expire()
                log.info(f"gossip directory loaded ({len(self._nodes)} node(s))")
        except Exception as e:
            log.warning(f"could not load gossip directory: {e}")

    def _save_disk(self):
        try:
            with self._lock:
                data = dict(self._nodes)
            tmp = self._dir_file + ".tmp"
            with open(tmp, "w") as f:
                json.dump(data, f, indent=2)
            os.replace(tmp, self._dir_file)
        except Exception as e:
            log.warning(f"could not save gossip directory: {e}")


# ─────────────────────────── helpers ──────────────────────────────────────

def _tokenize(text: str) -> list[str]:
    words = re.sub(r"[^\w\s]", " ", text.lower()).split()
    return [w for w in words if w not in _STOP_WORDS and len(w) > 1]


def _jaccard(a: list[str], b: list[str]) -> float:
    sa = set(a)
    sb = set(b)
    if not sa or not sb:
        return 0.0
    return len(sa & sb) / len(sa | sb)
