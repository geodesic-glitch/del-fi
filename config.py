"""Config loading and validation for Del-Fi.

Loads a single YAML file, validates required fields, merges defaults.
Prints human-readable errors and exits on bad config — the one place
where crashing is correct.
"""

import logging
import os
import sys
from pathlib import Path

import yaml

log = logging.getLogger("delfi.config")

DEFAULTS = {
    "personality": "Helpful and concise community assistant.",
    "knowledge_folder": "~/del-fi/knowledge",
    "max_response_bytes": 230,
    "radio_connection": "serial",
    "radio_port": "/dev/ttyUSB0",
    "rate_limit_seconds": 30,
    "response_cache_ttl": 300,
    "embedding_model": "nomic-embed-text",
    "channels": [],
    "log_level": "info",
    "ollama_host": "http://localhost:11434",
    "ollama_timeout": 120,
}

MESH_DEFAULTS = {
    "gossip": {
        "enabled": False,
        "announce_interval": 14400,
        "directory_ttl": 86400,
    },
    "peers": [],
    "sync": {
        "enabled": False,
        "window_start": "02:00",
        "window_end": "05:00",
        "max_cache_age": "7d",
        "max_cache_entries": 500,
    },
    "serve_to_peers": False,
    "tag_responses": True,
    "reject_contradictions": True,
}


def load_config(config_path: str | None = None) -> dict:
    """Load, validate, and return config dict. Exits on error."""
    if config_path is None:
        config_path = os.path.expanduser("~/del-fi/config.yaml")

    path = Path(config_path)
    if not path.exists():
        _die(
            f"Config file not found: {path}\n"
            "  Copy config.example.yaml to that location and edit it."
        )

    try:
        with open(path) as f:
            raw = yaml.safe_load(f) or {}
    except yaml.YAMLError as e:
        _die(f"Invalid YAML in {path}:\n  {e}")

    # Required fields
    for field in ("node_name", "model"):
        if field not in raw or not str(raw[field]).strip():
            _die(f"Missing required config field: '{field}'\n  Add it to {path}")

    # Merge defaults
    cfg = {**DEFAULTS, **raw}

    # Expand and resolve paths
    cfg["knowledge_folder"] = os.path.expanduser(cfg["knowledge_folder"])
    base_dir = os.path.dirname(cfg["knowledge_folder"])
    cfg["_base_dir"] = base_dir
    cfg["_vectorstore_dir"] = os.path.join(base_dir, "vectorstore")
    cfg["_cache_dir"] = os.path.join(base_dir, "cache")
    cfg["_gossip_dir"] = os.path.join(base_dir, "gossip")
    cfg["_seen_senders_file"] = os.path.join(base_dir, "seen_senders.txt")

    # Mesh knowledge: merge or disable
    if "mesh_knowledge" in raw and raw["mesh_knowledge"]:
        mk = raw["mesh_knowledge"]
        merged = {}
        for key, default_val in MESH_DEFAULTS.items():
            if isinstance(default_val, dict) and key in mk and isinstance(mk[key], dict):
                merged[key] = {**default_val, **mk[key]}
            else:
                merged[key] = mk.get(key, default_val)
        cfg["mesh_knowledge"] = merged
    else:
        cfg["mesh_knowledge"] = None

    _validate(cfg)
    return cfg


def _validate(cfg: dict) -> None:
    """Validate config values. Exit on errors."""
    if cfg["radio_connection"] not in ("serial", "tcp", "ble"):
        _die(
            f"radio_connection must be 'serial', 'tcp', or 'ble' "
            f"(got '{cfg['radio_connection']}')"
        )

    if not isinstance(cfg["max_response_bytes"], int) or cfg["max_response_bytes"] < 50:
        _die("max_response_bytes must be an integer >= 50")

    if not isinstance(cfg["rate_limit_seconds"], (int, float)) or cfg["rate_limit_seconds"] < 0:
        _die("rate_limit_seconds must be a non-negative number")

    if not isinstance(cfg["ollama_timeout"], (int, float)) or cfg["ollama_timeout"] < 1:
        _die("ollama_timeout must be a positive number")

    valid_levels = ("debug", "info", "warning", "error")
    if cfg["log_level"].lower() not in valid_levels:
        _die(f"log_level must be one of: {', '.join(valid_levels)}")


def _die(msg: str) -> None:
    """Print error and exit. The only place Del-Fi intentionally crashes."""
    print(f"[del-fi config error] {msg}", file=sys.stderr)
    sys.exit(1)
