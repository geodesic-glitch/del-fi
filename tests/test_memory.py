"""Tests for memory.py — per-sender conversation memory."""

import os
import sys
import tempfile
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from memory import ConversationMemory


def _make_cfg(**overrides):
    """Build a minimal config dict for ConversationMemory."""
    tmpdir = tempfile.mkdtemp(prefix="delfi-memtest-")
    cfg = {
        "memory_max_turns": 5,
        "memory_ttl": 3600,
        "persistent_memory": False,
        "_cache_dir": os.path.join(tmpdir, "cache"),
    }
    cfg.update(overrides)
    return cfg


# --- Basic add / get ---


def test_add_and_get():
    mem = ConversationMemory(_make_cfg())
    mem.add_turn("!alice", "hello", "hi there")
    mem.add_turn("!alice", "what is KHI?", "Kurosawa Heavy Industries.")
    history = mem.get_history("!alice")
    assert len(history) == 2
    assert history[0] == ("hello", "hi there")
    assert history[1] == ("what is KHI?", "Kurosawa Heavy Industries.")


def test_empty_history():
    mem = ConversationMemory(_make_cfg())
    assert mem.get_history("!nobody") == []


def test_separate_senders():
    mem = ConversationMemory(_make_cfg())
    mem.add_turn("!alice", "q1", "a1")
    mem.add_turn("!bob", "q2", "a2")
    assert len(mem.get_history("!alice")) == 1
    assert len(mem.get_history("!bob")) == 1
    assert mem.get_history("!alice")[0][0] == "q1"
    assert mem.get_history("!bob")[0][0] == "q2"


# --- Ring buffer (max turns) ---


def test_ring_buffer_trims():
    mem = ConversationMemory(_make_cfg(memory_max_turns=3))
    for i in range(5):
        mem.add_turn("!alice", f"q{i}", f"a{i}")
    history = mem.get_history("!alice")
    assert len(history) == 3
    # Should keep the 3 most recent
    assert history[0] == ("q2", "a2")
    assert history[1] == ("q3", "a3")
    assert history[2] == ("q4", "a4")


# --- TTL expiry ---


def test_ttl_expiry():
    mem = ConversationMemory(_make_cfg(memory_ttl=1))
    mem.add_turn("!alice", "old q", "old a")
    # Manually expire the entry
    mem._store["!alice"]["ts"] = time.time() - 2
    assert mem.get_history("!alice") == []


def test_ttl_resets_on_activity():
    mem = ConversationMemory(_make_cfg(memory_ttl=10))
    mem.add_turn("!alice", "q1", "a1")
    ts1 = mem._store["!alice"]["ts"]
    time.sleep(0.01)
    mem.add_turn("!alice", "q2", "a2")
    ts2 = mem._store["!alice"]["ts"]
    assert ts2 > ts1


# --- Clear ---


def test_clear_sender():
    mem = ConversationMemory(_make_cfg())
    mem.add_turn("!alice", "q", "a")
    mem.add_turn("!bob", "q", "a")
    mem.clear("!alice")
    assert mem.get_history("!alice") == []
    assert len(mem.get_history("!bob")) == 1


def test_clear_all():
    mem = ConversationMemory(_make_cfg())
    mem.add_turn("!alice", "q", "a")
    mem.add_turn("!bob", "q", "a")
    mem.clear_all()
    assert mem.get_history("!alice") == []
    assert mem.get_history("!bob") == []


# --- Prompt formatting ---


def test_format_for_prompt_empty():
    mem = ConversationMemory(_make_cfg())
    assert mem.format_for_prompt("!nobody") == ""


def test_format_for_prompt():
    mem = ConversationMemory(_make_cfg())
    mem.add_turn("!alice", "who is Mika Chen?", "She runs the Gaslight bar.")
    mem.add_turn("!alice", "where is it?", "Neon Row, Floor 155.")
    prompt = mem.format_for_prompt("!alice")
    assert "Recent conversation" in prompt
    assert "User: who is Mika Chen?" in prompt
    assert "Assistant: She runs the Gaslight bar." in prompt
    assert "User: where is it?" in prompt
    assert "Assistant: Neon Row, Floor 155." in prompt


# --- Sender count ---


def test_sender_count():
    mem = ConversationMemory(_make_cfg())
    assert mem.sender_count() == 0
    mem.add_turn("!alice", "q", "a")
    mem.add_turn("!bob", "q", "a")
    assert mem.sender_count() == 2


def test_sender_count_excludes_expired():
    mem = ConversationMemory(_make_cfg(memory_ttl=1))
    mem.add_turn("!alice", "q", "a")
    mem._store["!alice"]["ts"] = time.time() - 2
    assert mem.sender_count() == 0


# --- Cleanup ---


def test_cleanup_removes_expired():
    mem = ConversationMemory(_make_cfg(memory_ttl=1))
    mem.add_turn("!alice", "q", "a")
    mem.add_turn("!bob", "q", "a")
    # Expire alice
    mem._store["!alice"]["ts"] = time.time() - 2
    mem.cleanup()
    assert "!alice" not in mem._store
    assert "!bob" in mem._store


# --- Persistence ---


def test_persistence_round_trip():
    cfg = _make_cfg(persistent_memory=True)
    mem = ConversationMemory(cfg)
    mem.add_turn("!alice", "q1", "a1")
    mem.add_turn("!alice", "q2", "a2")

    # Create a new instance that loads from disk
    mem2 = ConversationMemory(cfg)
    history = mem2.get_history("!alice")
    assert len(history) == 2
    assert history[0] == ("q1", "a1")
    assert history[1] == ("q2", "a2")


def test_persistence_expired_not_loaded():
    cfg = _make_cfg(persistent_memory=True, memory_ttl=1)
    mem = ConversationMemory(cfg)
    mem.add_turn("!alice", "q", "a")
    # Expire and re-save
    mem._store["!alice"]["ts"] = time.time() - 2
    mem._save_disk()

    mem2 = ConversationMemory(cfg)
    assert mem2.get_history("!alice") == []


# --- Hard cap ---


def test_hard_cap():
    cfg = _make_cfg(memory_max_turns=9999)
    mem = ConversationMemory(cfg)
    # Should be clamped to 50
    assert mem.max_turns == 50
