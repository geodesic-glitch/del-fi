"""Tests for facts.py — FactStore ingestion, staleness, Tier 0 routing."""

import json
import os
import tempfile
import time
import unittest

from del_fi.core.facts import FactStore, _age, _age_label
from del_fi.core.router import Router


# --- Helpers ---


def _make_cfg(tmpdir: str) -> dict:
    """Minimal config dict with all paths pointing to tmpdir."""
    return {
        "node_name": "TEST-NODE",
        "model": "test-model:3b",
        "max_response_bytes": 230,
        "rate_limit_seconds": 30,
        "response_cache_ttl": 300,
        "personality": "Test assistant.",
        "knowledge_folder": os.path.join(tmpdir, "knowledge"),
        "_seen_senders_file": os.path.join(tmpdir, "seen-senders.txt"),
        "_base_dir": tmpdir,
        "_cache_dir": os.path.join(tmpdir, "cache"),
        "_gossip_dir": os.path.join(tmpdir, "gossip"),
        "_vectorstore_dir": os.path.join(tmpdir, "vectorstore"),
        "embedding_model": "nomic-embed-text",
        "ollama_host": "http://localhost:11434",
        "ollama_timeout": 120,
        "persistent_cache": False,
        "fact_feed_file": "",
        "fact_watch_interval_seconds": 30,
        "time_sensitive_files": ["weather-station.md", "trail-camera-log.md"],
        "fact_query_keywords": [
            "temperature", "temp", "humidity", "wind", "pressure",
            "barometer", "snow", "conditions", "current", "right now", "latest",
            "camera", "detected", "detection", "spotted", "sighted",
            "last seen", "cam-1", "cam-2", "cam-3", "cam1", "cam2", "cam3",
        ],
    }


def _fresh_ts() -> str:
    """ISO-8601 timestamp for 5 minutes ago (fresh)."""
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(time.time() - 300))


def _stale_ts() -> str:
    """ISO-8601 timestamp for 48 hours ago (well past any stale_after_seconds)."""
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(time.time() - 172800))


# --- FactStore unit tests ---


def test_ingest_valid_payload():
    with tempfile.TemporaryDirectory(prefix="delfi-test-") as tmpdir:
        fs = FactStore(_make_cfg(tmpdir))
        count, errors = fs.ingest({
            "temperature_f": {
                "value": -4.2,
                "unit": "°F",
                "timestamp": _fresh_ts(),
                "source": "weather-station",
                "stale_after_seconds": 3600,
            }
        })
        assert count == 1
        assert errors == []


def test_get_returns_correct_value():
    with tempfile.TemporaryDirectory(prefix="delfi-test-") as tmpdir:
        fs = FactStore(_make_cfg(tmpdir))
        ts = _fresh_ts()
        fs.ingest({
            "humidity_pct": {
                "value": 72,
                "unit": "%",
                "timestamp": ts,
                "source": "weather-station",
            }
        })
        f = fs.get("humidity_pct")
        assert f is not None
        assert f["value"] == 72
        assert f["unit"] == "%"
        assert f["source"] == "weather-station"
        assert f["is_stale"] is False


def test_get_unknown_key_returns_none():
    with tempfile.TemporaryDirectory(prefix="delfi-test-") as tmpdir:
        fs = FactStore(_make_cfg(tmpdir))
        assert fs.get("nonexistent_key") is None


def test_stale_fact_detected():
    with tempfile.TemporaryDirectory(prefix="delfi-test-") as tmpdir:
        fs = FactStore(_make_cfg(tmpdir))
        fs.ingest({
            "temperature_f": {
                "value": -4.2,
                "unit": "°F",
                "timestamp": _stale_ts(),
                "source": "weather-station",
                "stale_after_seconds": 3600,  # 1 hour; our ts is 48 hours ago
            }
        })
        f = fs.get("temperature_f")
        assert f is not None
        assert f["is_stale"] is True
        assert f["age_seconds"] > 86400


def test_fresh_fact_not_stale():
    with tempfile.TemporaryDirectory(prefix="delfi-test-") as tmpdir:
        fs = FactStore(_make_cfg(tmpdir))
        fs.ingest({
            "wind_mph": {
                "value": 12,
                "unit": "mph",
                "timestamp": _fresh_ts(),
                "source": "weather-station",
                "stale_after_seconds": 3600,
            }
        })
        f = fs.get("wind_mph")
        assert f is not None
        assert f["is_stale"] is False


def test_ingest_missing_required_fields_reported():
    with tempfile.TemporaryDirectory(prefix="delfi-test-") as tmpdir:
        fs = FactStore(_make_cfg(tmpdir))
        count, errors = fs.ingest({
            "bad_fact": {"value": 1}  # missing timestamp and source
        })
        assert count == 0
        assert len(errors) == 1
        assert "bad_fact" in errors[0]


def test_ingest_partial_success():
    """Valid facts are ingested even when some are malformed."""
    with tempfile.TemporaryDirectory(prefix="delfi-test-") as tmpdir:
        fs = FactStore(_make_cfg(tmpdir))
        count, errors = fs.ingest({
            "good": {
                "value": 99,
                "timestamp": _fresh_ts(),
                "source": "sensor",
            },
            "bad": {"oops": True},
        })
        assert count == 1
        assert len(errors) == 1
        assert fs.get("good") is not None
        assert fs.get("bad") is None


def test_format_value_fresh():
    with tempfile.TemporaryDirectory(prefix="delfi-test-") as tmpdir:
        fs = FactStore(_make_cfg(tmpdir))
        fs.ingest({
            "temperature_f": {
                "value": -4.2,
                "unit": "°F",
                "timestamp": _fresh_ts(),
                "source": "weather-station",
                "stale_after_seconds": 3600,
            }
        })
        formatted = fs.format_value("temperature_f")
        assert formatted is not None
        assert "-4.2" in formatted
        assert "°F" in formatted
        assert "weather-station" in formatted
        assert "STALE" not in formatted


def test_format_value_stale_includes_caveat():
    with tempfile.TemporaryDirectory(prefix="delfi-test-") as tmpdir:
        fs = FactStore(_make_cfg(tmpdir))
        fs.ingest({
            "temperature_f": {
                "value": -4.2,
                "unit": "°F",
                "timestamp": _stale_ts(),
                "source": "weather-station",
                "stale_after_seconds": 3600,
            }
        })
        formatted = fs.format_value("temperature_f")
        assert formatted is not None
        assert "STALE" in formatted


def test_format_value_with_confidence():
    with tempfile.TemporaryDirectory(prefix="delfi-test-") as tmpdir:
        fs = FactStore(_make_cfg(tmpdir))
        fs.ingest({
            "cam1_last_detection": {
                "value": "2 elk",
                "timestamp": _fresh_ts(),
                "source": "CAM-1",
                "confidence": 0.94,
            }
        })
        formatted = fs.format_value("cam1_last_detection")
        assert "94% conf" in formatted


def test_format_snapshot_empty():
    with tempfile.TemporaryDirectory(prefix="delfi-test-") as tmpdir:
        fs = FactStore(_make_cfg(tmpdir))
        assert "No sensor data" in fs.format_snapshot()


def test_format_snapshot_shows_stale_tag():
    with tempfile.TemporaryDirectory(prefix="delfi-test-") as tmpdir:
        fs = FactStore(_make_cfg(tmpdir))
        fs.ingest({
            "temperature_f": {
                "value": -4.2,
                "unit": "°F",
                "timestamp": _stale_ts(),
                "source": "weather-station",
                "stale_after_seconds": 3600,
            }
        })
        snapshot = fs.format_snapshot()
        assert "STALE" in snapshot


def test_has_facts():
    with tempfile.TemporaryDirectory(prefix="delfi-test-") as tmpdir:
        fs = FactStore(_make_cfg(tmpdir))
        assert not fs.has_facts()
        fs.ingest({
            "x": {"value": 1, "timestamp": _fresh_ts(), "source": "s"}
        })
        assert fs.has_facts()


def test_persistence_round_trip():
    """Facts are persisted on ingest and reloaded by a new FactStore instance."""
    with tempfile.TemporaryDirectory(prefix="delfi-test-") as tmpdir:
        cfg = _make_cfg(tmpdir)
        os.makedirs(cfg["_cache_dir"], exist_ok=True)

        fs1 = FactStore(cfg)
        ts = _fresh_ts()
        fs1.ingest({
            "snow_depth_in": {
                "value": 34,
                "unit": "in",
                "timestamp": ts,
                "source": "weather-station",
            }
        })

        # New instance should load the persisted data
        fs2 = FactStore(cfg)
        f = fs2.get("snow_depth_in")
        assert f is not None
        assert f["value"] == 34


def test_feed_file_ingested_on_poll():
    """Writing a JSON feed file triggers ingest on the next poll."""
    with tempfile.TemporaryDirectory(prefix="delfi-test-") as tmpdir:
        cfg = _make_cfg(tmpdir)
        os.makedirs(cfg["_cache_dir"], exist_ok=True)

        feed_path = os.path.join(cfg["_cache_dir"], "sensor_feed.json")
        cfg["fact_feed_file"] = feed_path

        payload = {
            "humidity_pct": {
                "value": 65,
                "unit": "%",
                "timestamp": _fresh_ts(),
                "source": "weather-station",
            }
        }
        with open(feed_path, "w") as fh:
            json.dump(payload, fh)

        fs = FactStore(cfg)
        # Manually trigger poll (watcher thread is not running in tests)
        fs._poll_feed_file()

        f = fs.get("humidity_pct")
        assert f is not None
        assert f["value"] == 65


def test_feed_file_not_reingested_if_unchanged():
    """Polling an unchanged feed file (same mtime) does not re-ingest."""
    with tempfile.TemporaryDirectory(prefix="delfi-test-") as tmpdir:
        cfg = _make_cfg(tmpdir)
        os.makedirs(cfg["_cache_dir"], exist_ok=True)

        feed_path = os.path.join(cfg["_cache_dir"], "sensor_feed.json")
        cfg["fact_feed_file"] = feed_path

        payload = {"x": {"value": 1, "timestamp": _fresh_ts(), "source": "s"}}
        with open(feed_path, "w") as fh:
            json.dump(payload, fh)

        fs = FactStore(cfg)
        fs._poll_feed_file()  # first poll — ingests
        assert fs.has_facts()

        # Overwrite store to simulate "reset" (would normally not happen, just testing guard)
        with fs._lock:
            fs._facts = {}

        # Second poll with same mtime — should NOT re-ingest
        fs._poll_feed_file()
        assert not fs.has_facts()


# --- Helper function tests ---


def test_age_fresh():
    ts = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(time.time() - 120))
    age = _age(ts)
    assert 100 < age < 200


def test_age_invalid_returns_inf():
    # Bad timestamps are treated as infinitely stale (not fresh)
    import math
    assert math.isinf(_age("not-a-date"))


def test_format_age_seconds():
    assert _age_label(45) == "now"


def test_format_age_minutes():
    assert _age_label(180) == "3m ago"


def test_format_age_hours():
    assert _age_label(7200) == "2h ago"


def test_format_age_days():
    assert _age_label(172800) == "2d ago"


# --- Tier 0 routing tests (via Router) ---


class MockWiki:
    """Minimal mock WikiEngine — tracks query() calls."""

    def __init__(self):
        self.query_called = False

    @property
    def available(self):
        return True

    @property
    def rag_available(self):
        return True

    @property
    def page_count(self):
        return 3

    def get_topics(self):
        return ["wildlife-guide", "trail-camera-log", "weather-station"]

    def query(self, text, peer_ctx="", history="", board_context=""):
        self.query_called = True
        return "Mock LLM response.", True

    def suggest(self, text):
        return None


class MockPeerCache:
    def lookup(self, q): return None
    def store(self, *a, **kw): pass


class MockGossipDir:
    peer_count = 0
    def list_peers(self): return []
    def receive(self, nid, txt): pass
    def referral(self, q): return None
    def announce(self): return ""


def _make_router_with_facts(tmpdir: str, facts: dict | None = None) -> tuple:
    """Create a Router+FactStore pair with pre-loaded facts."""
    from del_fi.core.router import Router
    cfg = _make_cfg(tmpdir)
    wiki = MockWiki()
    fs = FactStore(cfg)
    if facts:
        fs.ingest(facts)
    router = Router(cfg, wiki, MockPeerCache(), MockGossipDir(), fact_store=fs)
    return router, wiki, fs


def test_tier0_intercepts_temperature_query():
    """Temperature query hits FactStore directly — no wiki.query() call."""
    with tempfile.TemporaryDirectory(prefix="delfi-test-") as tmpdir:
        router, wiki, _ = _make_router_with_facts(tmpdir, {
            "temperature_f": {
                "value": -4.2,
                "unit": "°F",
                "timestamp": _fresh_ts(),
                "source": "weather-station",
                "stale_after_seconds": 3600,
            }
        })
        response = router.route("sender1", "what is the temperature right now")
        assert response is not None
        assert "-4.2" in response
        assert wiki.query_called is False


def test_tier0_intercepts_camera_query():
    """Camera detection query hits FactStore directly."""
    with tempfile.TemporaryDirectory(prefix="delfi-test-") as tmpdir:
        router, wiki, _ = _make_router_with_facts(tmpdir, {
            "cam1_last_detection": {
                "value": "7 elk",
                "timestamp": _fresh_ts(),
                "source": "CAM-1",
                "stale_after_seconds": 86400,
            }
        })
        response = router.route("sender1", "what did cam1 detect last")
        assert response is not None
        assert "elk" in response
        assert wiki.query_called is False


def test_tier0_stale_fact_includes_caveat():
    """Stale sensor value includes a staleness caveat in the direct response."""
    with tempfile.TemporaryDirectory(prefix="delfi-test-") as tmpdir:
        router, _, _ = _make_router_with_facts(tmpdir, {
            "temperature_f": {
                "value": -4.2,
                "unit": "°F",
                "timestamp": _stale_ts(),
                "source": "weather-station",
                "stale_after_seconds": 3600,
            }
        })
        response = router.route("sender1", "temperature")
        assert response is not None
        assert "STALE" in response


def test_tier0_misses_non_sensor_query():
    """Non-sensor query falls through Tier 0 to wiki."""
    with tempfile.TemporaryDirectory(prefix="delfi-test-") as tmpdir:
        router, wiki, _ = _make_router_with_facts(tmpdir, {
            "temperature_f": {
                "value": -4.2,
                "unit": "°F",
                "timestamp": _fresh_ts(),
                "source": "weather-station",
            }
        })
        response = router.route("sender1", "tell me about elk migration patterns")
        assert response is not None
        assert "-4.2" not in response


def test_tier0_no_facts_falls_through():
    """When FactStore is empty, query falls through to wiki."""
    with tempfile.TemporaryDirectory(prefix="delfi-test-") as tmpdir:
        router, wiki, _ = _make_router_with_facts(tmpdir, facts=None)
        response = router.route("sender1", "what is the temperature")
        assert response is not None
        # wiki.query should have been called since Tier 0 had no facts
        assert wiki.query_called is True


def test_cmd_data_no_facts():
    with tempfile.TemporaryDirectory(prefix="delfi-test-") as tmpdir:
        router, _, _ = _make_router_with_facts(tmpdir)
        response = router.route("sender1", "!data")
        assert response is not None
        assert "No sensor data" in response


def test_cmd_data_with_facts():
    with tempfile.TemporaryDirectory(prefix="delfi-test-") as tmpdir:
        router, _, _ = _make_router_with_facts(tmpdir, {
            "temperature_f": {
                "value": -4.2,
                "unit": "°F",
                "timestamp": _fresh_ts(),
                "source": "weather-station",
            }
        })
        response = router.route("sender1", "!data")
        assert response is not None
        assert "Temperature F" in response
        assert "-4.2" in response


def test_router_without_fact_store_still_works():
    """Router without fact_store kwarg must still work."""
    with tempfile.TemporaryDirectory(prefix="delfi-test-") as tmpdir:
        cfg = _make_cfg(tmpdir)
        # No fact_store kwarg — should default to None
        router = Router(cfg, MockWiki(), MockPeerCache(), MockGossipDir())
        response = router.route("sender1", "!ping")
        assert "pong" in response.lower()


# ---------------------------------------------------------------------------
# unittest discovery wrapper — makes bare test_ functions discoverable
# ---------------------------------------------------------------------------

_Tests = type(
    "_Tests",
    (unittest.TestCase,),
    {
        n: (lambda f: lambda self: f())(f)
        for n, f in list(globals().items())
        if n.startswith("test_") and callable(f)
    },
)

if __name__ == "__main__":
    unittest.main()
