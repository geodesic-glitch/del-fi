"""FactStore: structured sensor data layer for Tier 0 queries.

External scripts write sensor readings to cache/sensor_feed.json.
FactStore ingests that file, tracks freshness, and answers exact-value
queries directly — bypassing the LLM entirely to eliminate hallucination
on time-sensitive measurements.

Feed schema (cache/sensor_feed.json):
  {
    "<fact_key>": {
      "value":               <scalar>,
      "unit":                "<string>",
      "timestamp":           "<ISO-8601>",
      "source":              "<string>",
      "stale_after_seconds": <int>,
      "confidence":          <0.0–1.0>
    }
  }
"""

import json
import logging
import os
import re
import threading
import time
from datetime import datetime, timezone

log = logging.getLogger("del_fi.core.facts")

REQUIRED_FIELDS = {"value", "timestamp", "source"}


class FactStore:
    """Manages structured sensor facts with freshness tracking.

    Thread-safe: all reads and writes go through _lock.
    """

    def __init__(self, cfg: dict):
        self.cfg = cfg
        self._facts: dict[str, dict] = {}
        self._lock = threading.Lock()
        self._feed_mtime: float = 0.0

        feed_file = cfg.get("fact_feed_file", "")
        self._feed_file = (
            feed_file
            if feed_file
            else os.path.join(cfg["_cache_dir"], "sensor_feed.json")
        )
        self._store_file = os.path.join(cfg["_cache_dir"], "facts.json")
        self._load_persistent()

    # --- Public API ---

    def ingest(self, payload: dict) -> tuple[int, list[str]]:
        """Upsert facts from a payload dict. Returns (count_updated, errors)."""
        errors: list[str] = []
        count = 0

        for key, data in payload.items():
            if not isinstance(data, dict):
                errors.append(f"{key}: value must be a JSON object")
                continue

            missing = REQUIRED_FIELDS - set(data.keys())
            if missing:
                errors.append(f"{key}: missing required fields {sorted(missing)}")
                continue

            fact = {
                "value": data["value"],
                "unit": data.get("unit", ""),
                "timestamp": data["timestamp"],
                "source": data["source"],
                "stale_after_seconds": int(data.get("stale_after_seconds", 3600)),
                "confidence": data.get("confidence"),
                "ingested_at": time.time(),
            }

            with self._lock:
                self._facts[key] = fact
            count += 1

        if count:
            self._save_persistent()
            log.info(f"facts: ingested {count} fact(s)")

        for err in errors:
            log.warning(f"facts: ingest error — {err}")

        return count, errors

    def get(self, key: str) -> dict | None:
        """Return a single fact enriched with is_stale and age_seconds."""
        with self._lock:
            fact = self._facts.get(key)
        if fact is None:
            return None

        age = _age(fact["timestamp"])
        is_stale = age > fact["stale_after_seconds"]
        return {**fact, "is_stale": is_stale, "age_seconds": age}

    def get_all(self) -> dict[str, dict]:
        """Return all facts enriched with freshness info. Snapshot copy."""
        with self._lock:
            keys = list(self._facts.keys())
        result = {}
        for k in keys:
            f = self.get(k)
            if f is not None:
                result[k] = f
        return result

    def has_facts(self) -> bool:
        with self._lock:
            return bool(self._facts)

    def format_value(self, key: str) -> str | None:
        """Format a single fact as a human-readable string for radio."""
        f = self.get(key)
        if f is None:
            return None

        label = key.replace("_", " ").title()
        value = f["value"]
        unit = f" {f['unit']}" if f.get("unit") else ""
        source = f["source"]
        age = f["age_seconds"]
        conf = f.get("confidence")

        age_str = _age_label(age)

        if f["is_stale"]:
            ts_str = _iso_short(f["timestamp"])
            conf_str = f", {int(conf * 100)}% conf" if conf is not None else ""
            return (
                f"{label}: {value}{unit} ({source}, as of {ts_str} — STALE{conf_str})"
            )

        conf_str = f", {int(conf * 100)}% conf" if conf is not None else ""
        return f"{label}: {value}{unit} ({source}, {age_str}{conf_str})"

    def format_snapshot(self) -> str:
        """Return all facts as a multi-line radio-friendly summary."""
        all_facts = self.get_all()
        if not all_facts:
            return "No sensor data."

        lines = []
        for key in sorted(all_facts):
            line = self.format_value(key)
            if line:
                lines.append(line)

        return "\n".join(lines)

    def lookup(self, query: str) -> str | None:
        """Tier 0 keyword lookup. Returns formatted facts or None."""
        if not self.has_facts():
            return None

        keywords: list[str] = self.cfg.get("fact_query_keywords", [])
        q_lower = query.lower()

        if not any(kw in q_lower for kw in keywords):
            return None

        q_words = set(re.sub(r"[^\w]", " ", q_lower).split())
        all_facts = self.get_all()
        matched_keys = []
        for key in all_facts:
            key_tokens = set(re.sub(r"[^\w]", " ", key.lower()).replace("_", " ").split())
            if q_words & key_tokens:
                matched_keys.append(key)

        if not matched_keys:
            return None

        lines = [
            self.format_value(k)
            for k in sorted(matched_keys)
            if self.format_value(k)
        ]
        if not lines:
            return None

        name = self.cfg["node_name"]
        return name + ": " + " | ".join(lines)

    def watch(self, stop: threading.Event):
        """Start background file-poll thread."""
        interval = self.cfg.get("fact_watch_interval_seconds", 30)

        def _watcher():
            while not stop.is_set():
                try:
                    self._poll_feed_file()
                except Exception as e:
                    log.error(f"fact watcher error: {e}")
                stop.wait(interval)

        threading.Thread(target=_watcher, daemon=True).start()
        log.info(f"fact watcher started (poll every {interval}s)")

    # --- Internal ---

    def _poll_feed_file(self):
        """Ingest sensor_feed.json if it has changed since last poll."""
        if not os.path.exists(self._feed_file):
            return

        mtime = os.path.getmtime(self._feed_file)
        if mtime <= self._feed_mtime:
            return

        try:
            with open(self._feed_file) as f:
                payload = json.load(f)
            if isinstance(payload, dict):
                count, errors = self.ingest(payload)
                if count:
                    log.info(f"facts: ingested {count} fact(s) from {self._feed_file}")
                for err in errors:
                    log.warning(f"facts: feed error — {err}")
            self._feed_mtime = mtime
        except Exception as e:
            log.warning(f"could not read sensor feed: {e}")

    def _load_persistent(self):
        try:
            if os.path.exists(self._store_file):
                with open(self._store_file) as f:
                    data = json.load(f)
                with self._lock:
                    self._facts = data
                log.info(f"facts: loaded {len(self._facts)} persisted fact(s)")
        except Exception as e:
            log.warning(f"could not load persisted facts: {e}")

    def _save_persistent(self):
        try:
            with self._lock:
                data = dict(self._facts)
            tmp = self._store_file + ".tmp"
            with open(tmp, "w") as f:
                json.dump(data, f, indent=2)
            os.replace(tmp, self._store_file)
        except Exception as e:
            log.warning(f"could not save facts: {e}")


# --- Helpers ---

def _age(timestamp: str) -> float:
    """Return age in seconds for an ISO-8601 timestamp string."""
    try:
        dt = datetime.fromisoformat(timestamp)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return (datetime.now(timezone.utc) - dt).total_seconds()
    except Exception:
        return 0.0


def _age_label(age_seconds: float) -> str:
    if age_seconds < 90:
        return "now"
    if age_seconds < 3600:
        return f"{int(age_seconds / 60)}m ago"
    if age_seconds < 86400:
        return f"{int(age_seconds / 3600)}h ago"
    return f"{int(age_seconds / 86400)}d ago"


def _iso_short(timestamp: str) -> str:
    """Format an ISO timestamp as a short human string."""
    try:
        dt = datetime.fromisoformat(timestamp)
        return dt.strftime("%b %d %H:%M")
    except Exception:
        return timestamp[:16]
