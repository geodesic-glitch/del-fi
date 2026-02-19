"""FactStore: structured sensor data layer for absolute-truth queries.

External scripts write sensor readings to cache/sensor_feed.json.
FactStore ingests that file, tracks freshness, and answers exact-value
queries directly — bypassing the LLM entirely to eliminate hallucination
on time-sensitive measurements (weather, camera detections, etc.).

Feed schema (cache/sensor_feed.json):
  {
    "<fact_key>": {
      "value":               <scalar — number, string, bool>,
      "unit":                "<string>",          (optional, default "")
      "timestamp":           "<ISO-8601>",
      "source":              "<string>",
      "stale_after_seconds": <int>,               (optional, default 3600)
      "confidence":          <0.0–1.0>            (optional, for CV outputs)
    }
  }

Example keys: temperature_f, humidity_pct, wind_mph, snow_depth_in,
              cam1_last_detection, cam2_last_detection.

The FactStore is CV-ready: the optional 'confidence' field is designed
for computer-vision detection results with a confidence score.
"""

import json
import logging
import os
import threading
import time
from datetime import datetime, timezone

log = logging.getLogger("delfi.facts")

REQUIRED_FIELDS = {"value", "timestamp", "source"}


class FactStore:
    """Manages structured sensor facts with freshness tracking.

    Thread-safe: all reads and writes go through _lock.

    Lifecycle:
      fs = FactStore(cfg)          # loads persisted facts
      fs.watch(stop_event)         # starts background file-poll thread
      fs.ingest(payload_dict)      # upsert facts programmatically
      fs.get("temperature_f")      # retrieve single fact with staleness
      fs.format_snapshot()         # human-readable multi-line summary
    """

    def __init__(self, cfg: dict):
        self.cfg = cfg
        self._facts: dict[str, dict] = {}
        self._lock = threading.Lock()
        self._feed_mtime: float = 0.0

        # Derive feed file path: config override, else <_cache_dir>/sensor_feed.json
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
        """Upsert facts from a payload dict.

        Returns (count_updated, list_of_error_strings).
        Partial success is possible: valid keys are ingested, invalid ones
        are reported in the error list.
        """
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
                "confidence": data.get("confidence"),   # None if absent
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
        """Return a single fact enriched with is_stale and age_seconds.

        Returns None if the key is not in the store.
        """
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

    def format_value(self, key: str) -> str | None:
        """Format a single fact as a human-readable string for radio.

        Fresh:  "Temperature F: -4.2 °F (weather-station, 3 min ago)"
        Stale:  "Temperature F: -4.2 °F (weather-station, as of Feb 18 07:42
                 — 26 hr ago — may not be current)"
        Confidence included when set:
                "Cam1 Last Detection: 2 elk (CAM-1, 5 min ago, 94% conf)"

        Returns None if the key is unknown.
        """
        f = self.get(key)
        if f is None:
            return None

        val = f["value"]
        unit = f" {f['unit']}" if f["unit"] else ""
        source = f["source"]
        age_str = _format_age(f["age_seconds"])

        conf_str = ""
        if f.get("confidence") is not None:
            conf_str = f", {int(f['confidence'] * 100)}% conf"

        label = key.replace("_", " ").title()

        if f["is_stale"]:
            ts_human = _format_ts(f["timestamp"])
            return (
                f"{label}: {val}{unit} "
                f"({source}, as of {ts_human} — {age_str} ago{conf_str}"
                f" — may not be current)"
            )
        return f"{label}: {val}{unit} ({source}, {age_str} ago{conf_str})"

    def format_snapshot(self) -> str:
        """Format all facts as a compact multi-line snapshot (!data command).

        Each line: "key: value unit (age ago)[STALE]"
        """
        all_facts = self.get_all()
        if not all_facts:
            return "No sensor data available."

        lines = []
        for key, f in sorted(all_facts.items()):
            val = f["value"]
            unit = f" {f['unit']}" if f["unit"] else ""
            age_str = _format_age(f["age_seconds"])
            stale_tag = " [STALE]" if f["is_stale"] else ""
            lines.append(f"{key}: {val}{unit} ({age_str} ago){stale_tag}")

        return "\n".join(lines)

    def has_facts(self) -> bool:
        """Return True if the store contains at least one fact."""
        with self._lock:
            return bool(self._facts)

    # --- Background watcher ---

    def watch(self, stop_event: threading.Event | None = None) -> threading.Thread:
        """Start a background thread that polls the sensor feed file.

        Checks for file modification every fact_watch_interval_seconds (default 30).
        When the feed file changes, it is ingested automatically.

        Args:
            stop_event: optional threading.Event to signal shutdown.

        Returns the started thread (daemon=True).
        """
        interval = self.cfg.get("fact_watch_interval_seconds", 30)

        def _loop():
            log.info(f"facts: watching {self._feed_file} every {interval}s")
            while stop_event is None or not stop_event.is_set():
                self._poll_feed()
                if stop_event:
                    stop_event.wait(interval)
                else:
                    time.sleep(interval)

        t = threading.Thread(target=_loop, daemon=True, name="fact-watcher")
        t.start()
        return t

    def _poll_feed(self):
        """Check the feed file for changes and ingest if modified (mtime guard)."""
        try:
            if not os.path.exists(self._feed_file):
                return

            mtime = os.path.getmtime(self._feed_file)
            if mtime <= self._feed_mtime:
                return  # unchanged since last check

            self._feed_mtime = mtime

            with open(self._feed_file) as fh:
                payload = json.load(fh)

            if not isinstance(payload, dict):
                log.warning("facts: sensor_feed.json root must be a JSON object")
                return

            count, errors = self.ingest(payload)
            if count:
                log.info(f"facts: feed updated, {count} fact(s) ingested")

        except json.JSONDecodeError as e:
            log.warning(f"facts: invalid JSON in feed file: {e}")
        except Exception as e:
            log.warning(f"facts: feed poll error: {e}")

    # --- Persistence ---

    def _load_persistent(self):
        """Load persisted facts from disk on startup."""
        try:
            if os.path.exists(self._store_file):
                with open(self._store_file) as fh:
                    data = json.load(fh)
                with self._lock:
                    self._facts = data
                log.info(f"facts: loaded {len(self._facts)} persisted fact(s)")
        except Exception as e:
            log.warning(f"facts: could not load persisted facts: {e}")

    def _save_persistent(self):
        """Persist current facts to disk. Best effort — never raises."""
        try:
            os.makedirs(os.path.dirname(self._store_file), exist_ok=True)
            with self._lock:
                data = dict(self._facts)
            with open(self._store_file, "w") as fh:
                json.dump(data, fh, indent=2)
        except Exception as e:
            log.warning(f"facts: could not persist facts: {e}")


# --- Module-level helpers (also used by rag.py for staleness caveats) ---


def _age(timestamp: str) -> float:
    """Return age in seconds from an ISO-8601 timestamp. Returns 0 on error."""
    try:
        if timestamp.endswith("Z"):
            timestamp = timestamp[:-1] + "+00:00"
        dt = datetime.fromisoformat(timestamp)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return (datetime.now(tz=timezone.utc) - dt).total_seconds()
    except Exception:
        return 0.0


def _format_age(age_seconds: float) -> str:
    """Format an age value as a human-readable string."""
    s = max(0, int(age_seconds))
    if s < 60:
        return f"{s} sec"
    if s < 3600:
        return f"{s // 60} min"
    if s < 86400:
        return f"{s // 3600} hr"
    return f"{s // 86400} day(s)"


def _format_ts(timestamp: str) -> str:
    """Format an ISO-8601 timestamp as 'Mon DD HH:MM'."""
    try:
        if timestamp.endswith("Z"):
            timestamp = timestamp[:-1] + "+00:00"
        return datetime.fromisoformat(timestamp).strftime("%b %d %H:%M")
    except Exception:
        return timestamp
