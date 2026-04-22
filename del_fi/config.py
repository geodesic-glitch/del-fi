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

log = logging.getLogger("del_fi.config")

DEFAULTS: dict = {
    "model": "gemma3:4b-it-qat",
    "personality": "You are a helpful and concise community assistant.",
    "description": "",
    # --- Wiki (v0.2) ---
    "wiki_folder": "./wiki",
    "wiki_builder_model": None,          # falls back to model if unset
    "wiki_rebuild_on_start": False,
    "wiki_stale_after_days": 30,
    "time_sensitive_files": ["weather-station.md", "trail-camera-log.md"],
    # --- Retrieval ---
    "max_response_bytes": 230,
    "similarity_threshold": 0.28,
    "rag_top_k": 4,
    "max_context_tokens": None,
    "small_model_prompt": False,
    "reorder_context": False,
    "enable_suggestions_fallback": False,
    # --- Mesh ---
    "mesh_protocol": "meshtastic",
    "radio_connection": "serial",
    "radio_port": "/dev/ttyUSB0",
    "rate_limit_seconds": 30,
    "channels": [],
    # --- Ollama ---
    "ollama_host": "http://localhost:11434",
    "ollama_timeout": 120,
    "embedding_model": "nomic-embed-text",
    "num_ctx": None,
    "num_predict": 300,
    # --- Response cache ---
    "persistent_cache": True,
    "response_cache_ttl": 300,
    "auto_send_chunks": 3,
    "busy_notice": True,
    # --- Memory ---
    "memory_max_turns": 0,
    "memory_ttl": 3600,
    "persistent_memory": False,
    # --- Board ---
    "board_enabled": False,
    "board_max_posts": 50,
    "board_post_ttl": 86400,
    "board_show_count": 5,
    "board_persist": True,
    "board_rate_limit": 3,
    "board_rate_window": 3600,
    "board_blocked_patterns": [],
    # --- Facts / FactStore ---
    "fact_feed_file": "",
    "fact_watch_interval_seconds": 30,
    "fact_query_keywords": [
        "temperature", "temp", "humidity", "wind", "pressure",
        "barometer", "snow", "conditions", "current", "right now", "latest",
        "camera", "detected", "detection", "spotted", "sighted",
        "last seen", "cam-1", "cam-2", "cam-3", "cam1", "cam2", "cam3",
    ],
    # --- Logging ---
    "log_level": "info",
}

# Oracle profiles: per-model default overrides applied automatically
# when the configured model name contains the profile key (substring, case-insensitive).
# Keys NOT explicitly set in config.yaml take the profile value.
ORACLE_PROFILES: dict[str, dict] = {
    "gemma3:1b": {
        "similarity_threshold": 0.35,
        "rag_top_k": 2,
        "max_context_tokens": 512,
        "small_model_prompt": True,
        "reorder_context": True,
    },
    "llama3.2:1b": {
        "similarity_threshold": 0.35,
        "rag_top_k": 2,
        "max_context_tokens": 512,
        "small_model_prompt": True,
        "reorder_context": True,
    },
    "gemma3:4b": {
        "similarity_threshold": 0.28,
        "rag_top_k": 4,
    },
    "qwen2.5:3b": {
        "similarity_threshold": 0.28,
        "rag_top_k": 4,
    },
}

# Protocol-specific defaults merged when mesh_protocol is set
MESHCORE_DEFAULTS: dict = {
    "port": "/dev/ttyUSB0",
    "connection": "serial",
    "baud_rate": 115200,
}

SUPPORTED_PROTOCOLS = ("meshtastic", "meshcore")

MESH_DEFAULTS: dict = {
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


def _match_profile(model: str) -> dict | None:
    """Return oracle profile overrides for a known model, or None."""
    model_lower = model.lower()
    for profile_key, profile_vals in ORACLE_PROFILES.items():
        if profile_key in model_lower:
            return profile_vals
    return None


def load_config(config_path: str | None = None) -> dict:
    """Load, validate, and return config dict. Exits on error."""
    if config_path is None:
        script_dir = Path(__file__).resolve().parent.parent  # package root
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

    for field in ("node_name",):
        if field not in raw or not str(raw[field]).strip():
            _die(f"Missing required config field: '{field}'\n  Add it to {path}")

    # Merge defaults
    cfg: dict = {**DEFAULTS, **raw}

    # Apply oracle profile for known small models
    profile = _match_profile(cfg.get("model", ""))
    if profile:
        for key, val in profile.items():
            if key not in raw:
                cfg[key] = val
        log.debug(f"oracle profile applied for model '{cfg['model']}'")

    cfg["log_level"] = str(cfg["log_level"]).lower()

    # Resolve paths relative to the config file's directory
    config_dir = str(path.resolve().parent)

    # wiki_folder
    wiki_raw = cfg["wiki_folder"]
    wiki_raw = os.path.expanduser(wiki_raw)
    if not os.path.isabs(wiki_raw):
        wiki_raw = os.path.join(config_dir, wiki_raw)
    cfg["wiki_folder"] = wiki_raw

    # knowledge_folder (optional legacy key kept for background watcher)
    knowledge_raw = cfg.get("knowledge_folder", "./knowledge")
    knowledge_raw = os.path.expanduser(knowledge_raw)
    if not os.path.isabs(knowledge_raw):
        knowledge_raw = os.path.join(config_dir, knowledge_raw)
    cfg["knowledge_folder"] = knowledge_raw

    # Derived runtime paths (all relative to config dir)
    cfg["_config_dir"] = config_dir
    cfg["_vectorstore_dir"] = os.path.join(config_dir, "vectorstore")
    cfg["_cache_dir"] = os.path.join(config_dir, "cache")
    cfg["_gossip_dir"] = os.path.join(config_dir, "gossip")
    cfg["_seen_senders_file"] = os.path.join(config_dir, "seen_senders.txt")

    # Mesh protocol normalization
    cfg["mesh_protocol"] = cfg["mesh_protocol"].lower()
    if cfg["mesh_protocol"] == "meshcore":
        mc_raw = raw.get("meshcore", {})
        cfg["meshcore"] = {
            **MESHCORE_DEFAULTS,
            **(mc_raw if isinstance(mc_raw, dict) else {}),
        }

    # Mesh knowledge
    if "mesh_knowledge" in raw and raw["mesh_knowledge"]:
        mk = raw["mesh_knowledge"]
        merged: dict = {}
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
    if cfg["mesh_protocol"] not in SUPPORTED_PROTOCOLS:
        _die(
            f"Invalid mesh_protocol: '{cfg['mesh_protocol']}'\n"
            f"  Supported: {', '.join(SUPPORTED_PROTOCOLS)}"
        )

    max_bytes = cfg.get("max_response_bytes", 230)
    if not isinstance(max_bytes, int) or max_bytes < 50 or max_bytes > 256:
        _die(
            f"max_response_bytes must be an integer 50–256 (got {max_bytes!r}).\n"
            "  LoRa practical limit is 230 bytes."
        )

    rate = cfg.get("rate_limit_seconds", 30)
    if not isinstance(rate, (int, float)) or rate < 0:
        _die(f"rate_limit_seconds must be a non-negative number (got {rate!r})")

    ttl = cfg.get("response_cache_ttl", 300)
    if not isinstance(ttl, (int, float)) or ttl < 0:
        _die(f"response_cache_ttl must be a non-negative number (got {ttl!r})")


def _die(message: str) -> None:
    print(f"[del-fi] Config error: {message}", file=sys.stderr)
    sys.exit(1)
