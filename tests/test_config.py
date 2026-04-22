"""Tests for del_fi/config.py — config loading and validation."""

import os
import tempfile
import unittest

from del_fi.config import load_config


def _write_config(tmpdir: str, content: str) -> str:
    """Write a config string to a temp file and return the path."""
    cfg_file = os.path.join(tmpdir, "config.yaml")
    with open(cfg_file, "w", encoding="utf-8") as f:
        f.write(content)
    return cfg_file


class TestValidConfig(unittest.TestCase):
    def setUp(self):
        self.tmpdir = tempfile.mkdtemp(prefix="delfi-cfgtest-")

    def test_minimal_config(self):
        path = _write_config(self.tmpdir, 'node_name: "TEST-1"\nmodel: "qwen3:4b"\n')
        cfg = load_config(path)
        self.assertEqual(cfg["node_name"], "TEST-1")
        self.assertEqual(cfg["model"], "qwen3:4b")
        self.assertEqual(cfg["max_response_bytes"], 230)
        self.assertEqual(cfg["rate_limit_seconds"], 30)
        self.assertEqual(cfg["radio_connection"], "serial")

    def test_all_fields(self):
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
        path = _write_config(self.tmpdir, content)
        cfg = load_config(path)
        self.assertEqual(cfg["node_name"], "MY-NODE")
        self.assertEqual(cfg["personality"], "Grumpy librarian.")
        self.assertEqual(cfg["max_response_bytes"], 200)
        self.assertEqual(cfg["radio_connection"], "tcp")
        self.assertEqual(cfg["log_level"], "debug")

    def test_log_level_normalized(self):
        path = _write_config(self.tmpdir, 'node_name: "T"\nmodel: "m"\nlog_level: WARNING\n')
        cfg = load_config(path)
        self.assertEqual(cfg["log_level"], "warning")


class TestInvalidConfig(unittest.TestCase):
    def setUp(self):
        self.tmpdir = tempfile.mkdtemp(prefix="delfi-cfgtest-")

    def test_missing_file(self):
        with self.assertRaises(SystemExit):
            load_config(os.path.join(self.tmpdir, "nonexistent.yaml"))

    def test_empty_file(self):
        path = _write_config(self.tmpdir, "")
        with self.assertRaises(SystemExit):
            load_config(path)

    def test_missing_node_name(self):
        path = _write_config(self.tmpdir, 'model: "qwen2.5:7b"\n')
        with self.assertRaises(SystemExit):
            load_config(path)

    def test_missing_model_uses_default(self):
        path = _write_config(self.tmpdir, 'node_name: "TEST"\n')
        cfg = load_config(path)
        self.assertIn("model", cfg)

    def test_wrong_type_max_bytes(self):
        path = _write_config(
            self.tmpdir,
            'node_name: "T"\nmodel: "m"\nmax_response_bytes: "not a number"\n',
        )
        with self.assertRaises(SystemExit):
            load_config(path)

    def test_invalid_mesh_protocol(self):
        path = _write_config(
            self.tmpdir, 'node_name: "T"\nmodel: "m"\nmesh_protocol: "wifi"\n'
        )
        with self.assertRaises(SystemExit):
            load_config(path)

    def test_invalid_rate_limit(self):
        path = _write_config(
            self.tmpdir, 'node_name: "T"\nmodel: "m"\nrate_limit_seconds: -5\n'
        )
        with self.assertRaises(SystemExit):
            load_config(path)

    def test_negative_max_bytes(self):
        path = _write_config(
            self.tmpdir, 'node_name: "T"\nmodel: "m"\nmax_response_bytes: -1\n'
        )
        with self.assertRaises(SystemExit):
            load_config(path)

    def test_invalid_yaml(self):
        path = _write_config(self.tmpdir, ":\n  :\n    [invalid yaml]]]")
        with self.assertRaises(SystemExit):
            load_config(path)

    def test_non_mapping_yaml(self):
        path = _write_config(self.tmpdir, "- a list\n- not a mapping\n")
        with self.assertRaises(SystemExit):
            load_config(path)


class TestMeshProtocol(unittest.TestCase):
    def setUp(self):
        self.tmpdir = tempfile.mkdtemp(prefix="delfi-cfgtest-")

    def test_default_protocol_is_meshtastic(self):
        path = _write_config(self.tmpdir, 'node_name: "T"\nmodel: "m"\n')
        cfg = load_config(path)
        self.assertEqual(cfg["mesh_protocol"], "meshtastic")

    def test_meshcore_protocol(self):
        content = 'node_name: "T"\nmodel: "m"\nmesh_protocol: meshcore\n'
        path = _write_config(self.tmpdir, content)
        cfg = load_config(path)
        self.assertEqual(cfg["mesh_protocol"], "meshcore")

    def test_invalid_protocol(self):
        path = _write_config(
            self.tmpdir, 'node_name: "T"\nmodel: "m"\nmesh_protocol: zigbee\n'
        )
        with self.assertRaises(SystemExit):
            load_config(path)


class TestWikiConfig(unittest.TestCase):
    def setUp(self):
        self.tmpdir = tempfile.mkdtemp(prefix="delfi-cfgtest-")

    def test_wiki_defaults_present(self):
        path = _write_config(self.tmpdir, 'node_name: "T"\nmodel: "m"\n')
        cfg = load_config(path)
        self.assertIn("wiki_folder", cfg)
        self.assertFalse(cfg.get("wiki_rebuild_on_start", True))
        self.assertEqual(cfg.get("wiki_stale_after_days", 30), 30)

    def test_wiki_builder_model_override(self):
        content = 'node_name: "T"\nmodel: "gemma3:1b"\nwiki_builder_model: "qwen2.5:7b"\n'
        path = _write_config(self.tmpdir, content)
        cfg = load_config(path)
        self.assertEqual(cfg["wiki_builder_model"], "qwen2.5:7b")


if __name__ == "__main__":
    unittest.main()

