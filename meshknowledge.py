"""Mesh knowledge: gossip, peering, and referral system.

All-stdlib implementation (sqlite3, json, time).
Entirely optional — a node with no mesh config is a standalone oracle.
"""

import json
import logging
import os
import sqlite3
import time
from pathlib import Path

log = logging.getLogger("delfi.meshknowledge")

# Protocol version for gossip announcements
PROTOCOL_VERSION = 1


class MeshKnowledge:
    """Manages the three-tier knowledge system for inter-oracle communication.

    Tier 1: Operator knowledge (local docs) — handled by rag.py, not here.
    Tier 2: Peered knowledge (cached Q&A) — SQLite peer cache.
    Tier 3: Mesh gossip (metadata only) — JSON node directory.
    """

    def __init__(self, cfg: dict):
        self.cfg = cfg
        self.mk_cfg = cfg.get("mesh_knowledge")
        self._gossip: dict = {}
        self._peer_db: sqlite3.Connection | None = None
        self._enabled = self.mk_cfg is not None

        if self._enabled:
            self._init_storage()

    def _init_storage(self):
        """Set up gossip directory and peer cache database."""
        gossip_path = self.cfg["_gossip_dir"]
        cache_path = self.cfg["_cache_dir"]
        os.makedirs(gossip_path, exist_ok=True)
        os.makedirs(cache_path, exist_ok=True)

        # Gossip: JSON file
        self._gossip_file = os.path.join(gossip_path, "node-directory.json")
        self._gossip = self._load_gossip()

        # Peer cache: SQLite
        db_path = os.path.join(cache_path, "mesh-answers.db")
        try:
            self._peer_db = sqlite3.connect(db_path, check_same_thread=False)
            self._peer_db.execute("""
                CREATE TABLE IF NOT EXISTS peer_cache (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    peer_id TEXT NOT NULL,
                    peer_name TEXT NOT NULL,
                    query TEXT NOT NULL,
                    response TEXT NOT NULL,
                    timestamp REAL NOT NULL,
                    ttl INTEGER DEFAULT 604800
                )
            """)
            self._peer_db.execute(
                "CREATE INDEX IF NOT EXISTS idx_query ON peer_cache(query)"
            )
            self._peer_db.commit()
            log.info("mesh knowledge storage initialized")
        except Exception as e:
            log.error(f"peer cache init failed: {e}")
            self._peer_db = None

    # --- Gossip (Tier 3) ---

    def _load_gossip(self) -> dict:
        """Load gossip directory from JSON."""
        try:
            if os.path.exists(self._gossip_file):
                with open(self._gossip_file) as f:
                    return json.load(f)
        except Exception:
            pass
        return {}

    def _save_gossip(self):
        """Persist gossip directory to JSON."""
        try:
            with open(self._gossip_file, "w") as f:
                json.dump(self._gossip, f, indent=2)
        except Exception as e:
            log.error(f"failed to save gossip directory: {e}")

    def parse_announcement(self, text: str) -> dict | None:
        """Parse a DEL-FI gossip announcement.

        Format: DEL-FI:1:ANNOUNCE:NAME:key=val:key=val...
        Returns dict with node info, or None if invalid/incompatible.
        """
        if not text.startswith("DEL-FI:"):
            return None

        parts = text.split(":")
        if len(parts) < 4:
            return None

        try:
            version = int(parts[1])
        except ValueError:
            return None

        if version != PROTOCOL_VERSION:
            log.debug(f"ignoring announcement with protocol version {version}")
            return None

        if parts[2] != "ANNOUNCE":
            return None

        name = parts[3]
        info = {"name": name, "version": version, "last_seen": time.time()}

        # Parse key=value pairs from remaining segments
        for part in parts[4:]:
            if "=" in part:
                key, val = part.split("=", 1)
                info[key] = val

        return info

    def handle_announcement(self, node_id: str, text: str):
        """Process an incoming gossip announcement."""
        if not self._enabled:
            return

        info = self.parse_announcement(text)
        if not info:
            return

        self._gossip[node_id] = info
        self._expire_gossip()
        self._save_gossip()
        log.info(f"gossip: heard from {info['name']} ({node_id})")

    def format_announcement(self) -> str:
        """Create this node's gossip announcement string."""
        name = self.cfg["node_name"]
        model = self.cfg["model"]
        topics = ",".join(self._get_local_topics())
        return (
            f"DEL-FI:{PROTOCOL_VERSION}:ANNOUNCE:{name}"
            f":topics={topics}:model={model}"
        )

    def _expire_gossip(self):
        """Remove nodes that haven't announced within TTL."""
        if not self.mk_cfg:
            return
        ttl = self.mk_cfg.get("gossip", {}).get("directory_ttl", 86400)
        now = time.time()
        expired = [
            nid
            for nid, info in self._gossip.items()
            if now - info.get("last_seen", 0) > ttl
        ]
        for nid in expired:
            del self._gossip[nid]

    # --- Peer Cache (Tier 2) ---

    def check_peer_cache(self, query: str) -> dict | None:
        """Search peer cache for a relevant answer.

        MVP: simple keyword overlap matching. Returns best match or None.
        """
        if not self._peer_db:
            return None

        words = set(query.lower().split())
        if not words:
            return None

        try:
            cursor = self._peer_db.execute(
                "SELECT peer_name, query, response, timestamp FROM peer_cache "
                "ORDER BY timestamp DESC LIMIT 100"
            )

            best = None
            best_score = 0.0

            for peer_name, cached_query, response, ts in cursor:
                cached_words = set(cached_query.lower().split())
                if not cached_words:
                    continue
                overlap = len(words & cached_words)
                score = overlap / max(len(words), len(cached_words))
                if score > best_score and score > 0.5:
                    best = {
                        "peer_name": peer_name,
                        "query": cached_query,
                        "response": response,
                        "timestamp": ts,
                    }
                    best_score = score

            return best

        except Exception as e:
            log.error(f"peer cache search failed: {e}")
            return None

    def store_peer_answer(
        self, peer_id: str, peer_name: str, query: str, response: str
    ):
        """Store a Q&A pair from a trusted peer."""
        if not self._peer_db:
            return

        if not self._is_trusted_peer(peer_id):
            log.debug(f"ignoring answer from untrusted node {peer_id}")
            return

        try:
            self._peer_db.execute(
                "INSERT INTO peer_cache (peer_id, peer_name, query, response, timestamp) "
                "VALUES (?, ?, ?, ?, ?)",
                (peer_id, peer_name, query, response, time.time()),
            )
            self._peer_db.commit()
            self._enforce_cache_limits()
            log.info(f"cached peer answer from {peer_name}: {query[:50]}")
        except Exception as e:
            log.error(f"failed to store peer answer: {e}")

    def _is_trusted_peer(self, node_id: str) -> bool:
        """Check if a node is in our configured peer list."""
        if not self.mk_cfg:
            return False
        peers = self.mk_cfg.get("peers", [])
        return any(p.get("node_id") == node_id for p in peers)

    def _enforce_cache_limits(self):
        """Evict oldest entries if cache exceeds max size."""
        if not self._peer_db or not self.mk_cfg:
            return

        max_entries = self.mk_cfg.get("sync", {}).get("max_cache_entries", 500)
        try:
            count = self._peer_db.execute(
                "SELECT COUNT(*) FROM peer_cache"
            ).fetchone()[0]

            if count > max_entries:
                self._peer_db.execute(
                    "DELETE FROM peer_cache WHERE id IN "
                    "(SELECT id FROM peer_cache ORDER BY timestamp ASC LIMIT ?)",
                    (count - max_entries,),
                )
                self._peer_db.commit()
        except Exception:
            pass

    # --- Referrals (Tier 3 → user) ---

    def find_referral(self, query: str) -> str | None:
        """Check gossip directory for a node likely to answer this query.

        Returns a formatted referral message, or None.
        """
        if not self._gossip:
            return None

        words = set(query.lower().split())
        if not words:
            return None

        for node_id, info in self._gossip.items():
            topics_str = info.get("topics", "")
            if not topics_str:
                continue

            topics = topics_str.lower().split(",")
            for topic in topics:
                # Check if any query word overlaps with topic words
                topic_words = set(topic.replace("-", " ").split())
                if words & topic_words:
                    name = info.get("name", node_id)
                    return (
                        f"I don't have docs on that. {name} advertises: "
                        f"{topics_str}. Try DMing them directly."
                    )

        return None

    # --- Formatted responses ---

    def format_peers_response(self) -> str:
        """Format the !peers command response."""
        parts = []

        # Configured peers
        if self.mk_cfg and self.mk_cfg.get("peers"):
            peer_ids = set()
            parts.append("Peered:")
            for p in self.mk_cfg["peers"]:
                name = p.get("name", p.get("node_id", "unknown"))
                nid = p.get("node_id", "")
                peer_ids.add(nid)
                gossip = self._gossip.get(nid, {})
                topics = gossip.get("topics", "")
                if topics:
                    parts.append(f"  {name} ({topics})")
                else:
                    parts.append(f"  {name}")
        else:
            peer_ids = set()

        # Nearby non-peered nodes from gossip
        nearby = {
            nid: info
            for nid, info in self._gossip.items()
            if nid not in peer_ids
        }
        if nearby:
            parts.append("Nearby:")
            for nid, info in nearby.items():
                name = info.get("name", nid)
                topics = info.get("topics", "")
                if topics:
                    parts.append(f"  {name} ({topics})")
                else:
                    parts.append(f"  {name}")

        if not parts:
            return "No peers configured and no nearby nodes heard."

        return "\n".join(parts)

    def get_peer_names(self) -> list[str]:
        """Get configured peer names for status display."""
        if not self.mk_cfg:
            return []
        return [
            p.get("name", p.get("node_id", "?"))
            for p in self.mk_cfg.get("peers", [])
        ]

    # --- Helpers ---

    def _get_local_topics(self) -> list[str]:
        """Get topic list from knowledge folder filenames."""
        folder = self.cfg.get("knowledge_folder", "")
        if not folder or not os.path.exists(folder):
            return []

        topics = []
        for f in Path(folder).iterdir():
            if f.suffix in (".txt", ".md") and not f.name.startswith("."):
                topics.append(f.stem.replace("_", "-"))
        return sorted(topics)

    def close(self):
        """Clean shutdown."""
        if self._peer_db:
            try:
                self._peer_db.close()
            except Exception:
                pass
