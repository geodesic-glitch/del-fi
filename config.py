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
    "model": "qwen3:4b",
    "personality": "Helpful and concise community assistant.",
    "knowledge_folder": "~/del-fi/knowledge",
    "max_response_bytes": 230,
    "mesh_protocol": "meshtastic",
    "radio_connection": "serial",
    "radio_port": "/dev/ttyUSB0",
    "rate_limit_seconds": 30,
    "response_cache_ttl": 300,
    "embedding_model": "nomic-embed-text",
    "channels": [],
    "log_level": "info",
    "ollama_host": "http://localhost:11434",
    "ollama_timeout": 120,
    "num_ctx": 2048,
    "num_predict": 128,
    "persistent_cache": True,
    "busy_notice": True,
    "memory_max_turns": 0,
    "memory_ttl": 3600,
    "persistent_memory": False,
    "board_enabled": False,
    "board_max_posts": 50,
    "board_post_ttl": 86400,
    "board_show_count": 5,
    "board_persist": True,
    "board_rate_limit": 3,
    "board_rate_window": 3600,
    "board_blocked_patterns": [],
}

# Protocol-specific defaults merged when mesh_protocol is set
MESHCORE_DEFAULTS = {
    "port": "/dev/ttyUSB0",
    "connection": "serial",
    "baud_rate": 115200,
}

# Supported mesh protocols (used for validation)
SUPPORTED_PROTOCOLS = ("meshtastic", "meshcore")

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
        # Check for config.yaml next to the script first, then ~/del-fi/
        script_dir = Path(__file__).resolve().parent
        local_config = script_dir / "config.yaml"
        if local_config.exists():
            config_path = str(local_config)
        else:
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
    for field in ("node_name",):
        if field not in raw or not str(raw[field]).strip():
            _die(f"Missing required config field: '{field}'\n  Add it to {path}")

    # Merge defaults (model defaults to qwen3:4b if not specified)
    cfg = {**DEFAULTS, **raw}

    # Normalize log_level to lowercase
    cfg["log_level"] = str(cfg["log_level"]).lower()

    # Expand and resolve paths
    cfg["knowledge_folder"] = os.path.expanduser(cfg["knowledge_folder"])
    base_dir = os.path.dirname(cfg["knowledge_folder"])
    cfg["_base_dir"] = base_dir
    cfg["_vectorstore_dir"] = os.path.join(base_dir, "vectorstore")
    cfg["_cache_dir"] = os.path.join(base_dir, "cache")
    cfg["_gossip_dir"] = os.path.join(base_dir, "gossip")
    cfg["_seen_senders_file"] = os.path.join(base_dir, "seen_senders.txt")

    # Mesh protocol: normalize and merge protocol-specific defaults
    cfg["mesh_protocol"] = cfg["mesh_protocol"].lower()
    if cfg["mesh_protocol"] == "meshcore":
        mc_raw = raw.get("meshcore", {})
        cfg["meshcore"] = {**MESHCORE_DEFAULTS, **(mc_raw if isinstance(mc_raw, dict) else {})}

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
    # Mesh protocol
    if cfg["mesh_protocol"] not in SUPPORTED_PROTOCOLS:
        _die(
            f"mesh_protocol must be one of: {', '.join(SUPPORTED_PROTOCOLS)} "
            f"(got '{cfg['mesh_protocol']}')"
        )

    # Meshtastic-specific validation
    if cfg["mesh_protocol"] == "meshtastic":
        if cfg["radio_connection"] not in ("serial", "tcp", "ble"):
            _die(
                f"radio_connection must be 'serial', 'tcp', or 'ble' "
                f"(got '{cfg['radio_connection']}')"
            )

    # MeshCore-specific validation
    if cfg["mesh_protocol"] == "meshcore":
        mc = cfg.get("meshcore", {})
        if mc.get("connection") not in ("serial", "tcp"):
            _die(
                f"meshcore.connection must be 'serial' or 'tcp' "
                f"(got '{mc.get('connection')}')"
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
