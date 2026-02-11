"""Del-Fi daemon entry point.

Startup sequence, main loop, signal handling, and the banner.
This is the file you run: python delfi.py --simulator
"""

import argparse
import logging
import os
import queue
import signal
import sys
import threading
import time

from config import load_config
from formatter import byte_len
from mesh import create_interface
from meshknowledge import MeshKnowledge
from rag import RAGEngine
from router import Router

VERSION = "0.1"

log = logging.getLogger("delfi")


# --- Logging with personality ---


class _DelFiFormatter(logging.Formatter):
    """Timestamped log lines in the Del-Fi aesthetic."""

    def format(self, record):
        ts = time.strftime("%H:%M:%S", time.localtime(record.created))
        return f"[{ts}] {record.getMessage()}"


def setup_logging(level: str):
    numeric = getattr(logging, level.upper(), logging.INFO)
    handler = logging.StreamHandler()
    handler.setFormatter(_DelFiFormatter())
    root = logging.getLogger("delfi")
    root.setLevel(numeric)
    root.addHandler(handler)


# --- Startup banner ---


def print_banner(cfg: dict, rag: RAGEngine, mesh_iface, mesh_knowledge):
    name = cfg["node_name"]
    model = cfg["model"]
    docs = rag.doc_count
    status = "ready" if rag.available else "waiting for ollama"

    conn = cfg["radio_connection"]
    port = cfg["radio_port"]

    if isinstance(mesh_iface, type) or not hasattr(mesh_iface, "connected"):
        radio_str = "simulator"
    elif mesh_iface.connected:
        radio_str = f"✓ {conn}:{port}"
    else:
        radio_str = f"✗ {conn} (reconnecting)"

    lines = [
        f"  ·· DEL-FI ··  v{VERSION}",
        f"  node: {name}",
        f"  model: {model} · {docs} docs · {status}",
        f"  radio: {radio_str}",
    ]

    if mesh_knowledge:
        peer_names = mesh_knowledge.get_peer_names()
        if peer_names:
            lines.append(f"  peers: {' · '.join(peer_names)}")

    # Compute box width from longest content line
    w = max(len(line) for line in lines) + 2

    print(f"╔{'═' * w}╗")
    for line in lines:
        print(f"║{line:<{w}}║")
    print(f"╚{'═' * w}╝")


# --- Background threads ---


def knowledge_watcher(cfg: dict, rag: RAGEngine, stop: threading.Event):
    """Periodically re-scan the knowledge folder for changes."""
    folder = cfg["knowledge_folder"]
    while not stop.is_set():
        try:
            rag.index_folder(folder)
        except Exception as e:
            log.error(f"knowledge watcher error: {e}")
        stop.wait(60)  # poll every 60 seconds


def ollama_health_check(rag: RAGEngine, stop: threading.Event):
    """Retry Ollama connection when it's down."""
    while not stop.is_set():
        if not rag.available:
            rag.check_ollama()
        stop.wait(30)  # check every 30 seconds


# --- Main ---


def main():
    parser = argparse.ArgumentParser(
        description="Del-Fi — Meshtastic mesh oracle daemon"
    )
    parser.add_argument(
        "--config",
        "-c",
        help="Path to config.yaml (default: ~/del-fi/config.yaml)",
    )
    parser.add_argument(
        "--simulator",
        "-s",
        action="store_true",
        help="Run in simulator mode (stdin/stdout, no radio required)",
    )
    args = parser.parse_args()

    # === Step 1: Config ===
    # On failure: exit with human-readable error. This is the one place
    # where crashing is correct.
    cfg = load_config(args.config)
    setup_logging(cfg["log_level"])
    log.info("config loaded")

    # Ensure runtime directories exist
    for d in (cfg["knowledge_folder"], cfg["_cache_dir"], cfg["_gossip_dir"]):
        os.makedirs(d, exist_ok=True)

    # === Step 2: ChromaDB / RAG Engine ===
    # On failure: RAG disabled, falls back to raw LLM.
    rag = RAGEngine(cfg)

    # === Step 3: Knowledge indexing ===
    # On failure: log and skip individual files, continue.
    try:
        count = rag.index_folder(cfg["knowledge_folder"])
        if count > 0:
            log.info(f"initial indexing: {count} files")
        elif rag.doc_count == 0:
            log.warning("no documents in knowledge folder")
    except Exception as e:
        log.error(f"initial indexing failed: {e}")

    # === Step 4: Ollama check (non-blocking) ===
    # On failure: daemon starts, queries wait, commands work immediately.
    if not rag.available:
        log.warning("ollama not available — commands work, queries will wait")

    # === Step 5: Mesh Knowledge (optional) ===
    mesh_knowledge = None
    if cfg.get("mesh_knowledge"):
        mesh_knowledge = MeshKnowledge(cfg)

    # === Step 6: Router ===
    router = Router(cfg, rag, mesh_knowledge)

    # === Step 7: Radio / Simulator ===
    msg_queue = queue.Queue()
    mesh_iface = create_interface(cfg, args.simulator, msg_queue)

    if not args.simulator:
        if not mesh_iface.connect():
            log.warning("radio not connected — entering reconnect loop")
            threading.Thread(
                target=mesh_iface.reconnect_loop, daemon=True
            ).start()

    # === Step 8: Ready ===
    print_banner(cfg, rag, mesh_iface, mesh_knowledge)
    log.info("listening...")

    # Background threads
    stop_event = threading.Event()

    threading.Thread(
        target=knowledge_watcher, args=(cfg, rag, stop_event), daemon=True
    ).start()

    threading.Thread(
        target=ollama_health_check, args=(rag, stop_event), daemon=True
    ).start()

    # Signal handling
    def shutdown(sig, frame):
        log.info("shutting down...")
        stop_event.set()
        mesh_iface.close()
        if mesh_knowledge:
            mesh_knowledge.close()
        sys.exit(0)

    signal.signal(signal.SIGINT, shutdown)
    signal.signal(signal.SIGTERM, shutdown)

    # === Main loop ===
    # Process messages from the queue. Never crash.
    while True:
        try:
            sender_id, text = msg_queue.get(timeout=1.0)
        except queue.Empty:
            continue
        except Exception:
            continue

        try:
            response = router.route(sender_id, text)
            if response:
                mesh_iface.send_dm(sender_id, response)
                log.info(f"  ✓ response: {byte_len(response)} bytes → {sender_id}")
        except Exception as e:
            log.error(f"error processing message from {sender_id}: {e}")
            # Never let one bad query kill the daemon
            try:
                mesh_iface.send_dm(
                    sender_id, "I hit an error processing that. Try again."
                )
            except Exception:
                pass


if __name__ == "__main__":
    main()
