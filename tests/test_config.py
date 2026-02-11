"""Tests for config.py — config loading and validation."""

import sys
import os
import tempfile
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import pytest
from config import load_config


def _write_config(tmp_path, content: str) -> str:
    """Write a config string to a temp file and return the path."""
    cfg_file = os.path.join(str(tmp_path), "config.yaml")
    with open(cfg_file, "w", encoding="utf-8") as f:
        f.write(content)
    return cfg_file


class TestValidConfig:
    def test_minimal_config(self, tmp_path):
        path = _write_config(tmp_path, 'node_name: "TEST-1"\nmodel: "qwen2.5:7b"\n')
        cfg = load_config(path)
        assert cfg["node_name"] == "TEST-1"
        assert cfg["model"] == "qwen2.5:7b"
        # Check defaults applied
        assert cfg["max_response_bytes"] == 230
        assert cfg["rate_limit_seconds"] == 60
        assert cfg["radio_connection"] == "serial"

    def test_all_fields(self, tmp_path):
        content = """
node_name: "MY-NODE"
model: "llama3:8b"
personality: "Grumpy librarian."
knowledge_folder: /tmp/knowledge
max_response_bytes: 200
radio_connection: tcp
radio_port: "192.168.1.100:4403"
rate_limit_seconds: 30
response_cache_ttl: 600
embedding_model: "nomic-embed-text"
ollama_timeout: 60
log_level: debug
"""
        path = _write_config(tmp_path, content)
        cfg = load_config(path)
        assert cfg["node_name"] == "MY-NODE"
        assert cfg["personality"] == "Grumpy librarian."
        assert cfg["max_response_bytes"] == 200
        assert cfg["radio_connection"] == "tcp"
        assert cfg["log_level"] == "debug"

    def test_log_level_normalized(self, tmp_path):
        path = _write_config(tmp_path, 'node_name: "T"\nmodel: "m"\nlog_level: WARNING\n')
        cfg = load_config(path)
        assert cfg["log_level"] == "warning"


class TestInvalidConfig:
    def test_missing_file(self, tmp_path):
        with pytest.raises(SystemExit):
            load_config(os.path.join(str(tmp_path), "nonexistent.yaml"))

    def test_empty_file(self, tmp_path):
        path = _write_config(tmp_path, "")
        with pytest.raises(SystemExit):
            load_config(path)

    def test_missing_node_name(self, tmp_path):
        path = _write_config(tmp_path, 'model: "qwen2.5:7b"\n')
        with pytest.raises(SystemExit):
            load_config(path)

    def test_missing_model_uses_default(self, tmp_path):
        path = _write_config(tmp_path, 'node_name: "TEST"\n')
        cfg = load_config(path)
        assert cfg["model"] == "gemma3:12b"

    def test_wrong_type_max_bytes(self, tmp_path):
        path = _write_config(tmp_path,
            'node_name: "T"\nmodel: "m"\nmax_response_bytes: "not a number"\n')
        with pytest.raises(SystemExit):
            load_config(path)

    def test_invalid_radio_connection(self, tmp_path):
        path = _write_config(tmp_path,
            'node_name: "T"\nmodel: "m"\nradio_connection: "wifi"\n')
        with pytest.raises(SystemExit):
            load_config(path)

    def test_invalid_log_level(self, tmp_path):
        path = _write_config(tmp_path,
            'node_name: "T"\nmodel: "m"\nlog_level: "verbose"\n')
        with pytest.raises(SystemExit):
            load_config(path)

    def test_negative_max_bytes(self, tmp_path):
        path = _write_config(tmp_path,
            'node_name: "T"\nmodel: "m"\nmax_response_bytes: -1\n')
        with pytest.raises(SystemExit):
            load_config(path)

    def test_invalid_yaml(self, tmp_path):
        path = _write_config(tmp_path, ":\n  :\n    [invalid yaml]]]")
        with pytest.raises(SystemExit):
            load_config(path)

    def test_non_mapping_yaml(self, tmp_path):
        path = _write_config(tmp_path, "- a list\n- not a mapping\n")
        with pytest.raises(SystemExit):
            load_config(path)
