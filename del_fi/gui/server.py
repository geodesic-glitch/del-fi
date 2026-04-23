"""Del-Fi GUI — Flask web server for oracle configuration and management.

Provides a browser-based control panel for configuring, testing, and
managing a Del-Fi oracle deployment.

Launch via:
    python main.py --gui [--config PATH] [--gui-port 5174]
"""

import json
import logging
import os
import re
import subprocess
import sys
import threading
import time
import webbrowser
from pathlib import Path

import yaml

log = logging.getLogger("del_fi.gui")

VERSION = "0.2"

# Project root is three levels up: del_fi/gui/server.py → del_fi/gui → del_fi → project root
_PROJECT_ROOT = Path(__file__).parent.parent.parent


def create_app(cfg: dict, config_path: str):
    """Build and return the Flask application."""
    try:
        from flask import Flask, jsonify, render_template, request
    except ImportError:
        print(
            "\n[del-fi] flask is required for --gui.\n"
            "  pip install flask\n",
            file=sys.stderr,
        )
        sys.exit(1)

    template_dir = str(Path(__file__).parent / "templates")
    app = Flask(__name__, template_folder=template_dir)
    app.config["JSON_SORT_KEYS"] = False

    _state: dict = {
        "cfg": cfg,
        "config_path": str(config_path),
        "start_time": time.time(),
        "_router": None,
        "_router_lock": threading.Lock(),
        "_router_stale": False,
    }

    # ── Lazy Router for Simulator ──────────────────────────────────────────

    def _get_router():
        with _state["_router_lock"]:
            if _state["_router"] is None or _state["_router_stale"]:
                from del_fi.core.facts import FactStore
                from del_fi.core.knowledge import WikiEngine
                from del_fi.core.peers import GossipDirectory, PeerCache
                from del_fi.core.router import Router
                c = _state["cfg"]
                for d in (c["_cache_dir"], c["_gossip_dir"], c["wiki_folder"]):
                    os.makedirs(d, exist_ok=True)
                wiki = WikiEngine(c)
                _state["_router"] = Router(
                    c, wiki, PeerCache(c), GossipDirectory(c),
                    fact_store=FactStore(c),
                )
                _state["_router_stale"] = False
        return _state["_router"]

    # ── Helper: run main.py subcommand ────────────────────────────────────

    def _run_main(*args, timeout: int = 30) -> dict:
        main_py = _PROJECT_ROOT / "main.py"
        if not main_py.exists():
            return {"ok": False, "error": f"main.py not found at {main_py}"}
        try:
            r = subprocess.run(
                [sys.executable, str(main_py), *args,
                 "--config", _state["config_path"]],
                capture_output=True, text=True, timeout=timeout,
                cwd=str(_PROJECT_ROOT),
            )
            return {
                "ok": r.returncode == 0,
                "stdout": r.stdout,
                "stderr": r.stderr,
                "returncode": r.returncode,
            }
        except subprocess.TimeoutExpired:
            return {"ok": False, "error": "timed out"}
        except Exception as e:
            return {"ok": False, "error": str(e)}

    # ── Routes ────────────────────────────────────────────────────────────

    @app.route("/")
    def index():
        return render_template("index.html")

    @app.route("/api/status")
    def api_status():
        c = _state["cfg"]
        ollama_ok = False
        ollama_models: list = []
        try:
            from ollama import Client
            cl = Client(host=c["ollama_host"], timeout=5)
            resp = cl.list()
            ollama_ok = True
            ollama_models = [m.model for m in (resp.models or [])]
        except Exception:
            pass

        wiki_dir = Path(c["wiki_folder"])
        wiki_pages = sorted(
            f.stem for f in wiki_dir.glob("*.md")
            if f.name not in ("index.md", "log.md")
        ) if wiki_dir.exists() else []

        knowledge_dir = Path(c.get("knowledge_folder", "./knowledge"))
        knowledge_files = sorted(
            f.name for f in knowledge_dir.iterdir()
            if f.is_file() and f.suffix in (".md", ".txt")
        ) if knowledge_dir.exists() else []

        return jsonify({
            "node_name": c.get("node_name", "UNNAMED"),
            "model": c.get("model", ""),
            "wiki_builder_model": c.get("wiki_builder_model") or "",
            "oracle_type": c.get("oracle_type", ""),
            "ollama_host": c.get("ollama_host", ""),
            "ollama_ok": ollama_ok,
            "ollama_models": ollama_models,
            "wiki_pages": wiki_pages,
            "wiki_page_count": len(wiki_pages),
            "knowledge_files": knowledge_files,
            "knowledge_file_count": len(knowledge_files),
            "wiki_folder": str(wiki_dir),
            "knowledge_folder": str(knowledge_dir),
            "uptime_s": int(time.time() - _state["start_time"]),
            "version": VERSION,
            "config_path": _state["config_path"],
        })

    @app.route("/api/config", methods=["GET"])
    def api_config_get():
        try:
            with open(_state["config_path"], "r", encoding="utf-8") as f:
                raw = yaml.safe_load(f) or {}
            return jsonify({"ok": True, "config": raw})
        except FileNotFoundError:
            return jsonify({"ok": True, "config": {}})
        except Exception as e:
            return jsonify({"ok": False, "error": str(e)}), 500

    @app.route("/api/config", methods=["POST"])
    def api_config_post():
        try:
            body = request.get_json(force=True)
            new_cfg = body.get("config", {})
            # Strip internal runtime keys
            clean = {k: v for k, v in new_cfg.items() if not k.startswith("_")}
            if not str(clean.get("node_name", "")).strip():
                return jsonify({"ok": False, "error": "node_name is required"}), 400
            if not str(clean.get("model", "")).strip():
                return jsonify({"ok": False, "error": "model is required"}), 400
            with open(_state["config_path"], "w", encoding="utf-8") as f:
                yaml.dump(
                    clean, f, default_flow_style=False,
                    allow_unicode=True, sort_keys=False, width=80,
                )
            from del_fi.config import load_config
            _state["cfg"] = load_config(_state["config_path"])
            _state["_router_stale"] = True
            return jsonify({"ok": True})
        except Exception as e:
            log.exception("config save error")
            return jsonify({"ok": False, "error": str(e)}), 500

    @app.route("/api/wiki/pages")
    def api_wiki_pages():
        wiki_dir = Path(_state["cfg"]["wiki_folder"])
        pages = []
        if wiki_dir.exists():
            for f in sorted(wiki_dir.glob("*.md")):
                if f.name in ("index.md", "log.md"):
                    continue
                meta: dict = {
                    "slug": f.stem,
                    "title": f.stem.replace("-", " ").title(),
                    "tags": "", "last_ingested": "", "size": 0,
                }
                try:
                    text = f.read_text(encoding="utf-8")
                    meta["size"] = len(text)
                    m = re.search(r"^title:\s*(.+)$", text, re.MULTILINE)
                    if m:
                        meta["title"] = m.group(1).strip()
                    m = re.search(r"^tags:\s*\[(.+)\]$", text, re.MULTILINE)
                    if m:
                        meta["tags"] = m.group(1).strip()
                    m = re.search(r"^last_ingested:\s*(.+)$", text, re.MULTILINE)
                    if m:
                        meta["last_ingested"] = m.group(1).strip()
                except Exception:
                    pass
                pages.append(meta)
        return jsonify({"ok": True, "pages": pages})

    @app.route("/api/wiki/page/<slug>")
    def api_wiki_page(slug: str):
        slug = re.sub(r"[^a-zA-Z0-9_-]", "", slug)
        page_path = Path(_state["cfg"]["wiki_folder"]) / f"{slug}.md"
        if not page_path.exists():
            return jsonify({"ok": False, "error": "page not found"}), 404
        return jsonify({
            "ok": True,
            "content": page_path.read_text(encoding="utf-8"),
            "slug": slug,
        })

    @app.route("/api/wiki/log")
    def api_wiki_log():
        p = Path(_state["cfg"]["wiki_folder"]) / "log.md"
        content = p.read_text(encoding="utf-8") if p.exists() else ""
        return jsonify({"ok": True, "content": content})

    @app.route("/api/wiki/build", methods=["POST"])
    def api_wiki_build():
        timeout = int(_state["cfg"].get("wiki_build_timeout", 600)) + 30
        result = _run_main("--build-wiki", timeout=timeout)
        return jsonify(result)

    @app.route("/api/wiki/lint", methods=["POST"])
    def api_wiki_lint():
        result = _run_main("--lint-wiki", timeout=60)
        return jsonify(result)

    @app.route("/api/knowledge/files")
    def api_knowledge_files():
        kd = Path(_state["cfg"].get("knowledge_folder", "./knowledge"))
        files = []
        if kd.exists():
            for f in sorted(kd.iterdir()):
                if f.is_file() and f.suffix in (".md", ".txt"):
                    files.append({
                        "name": f.name,
                        "size": f.stat().st_size,
                        "modified": int(f.stat().st_mtime),
                    })
        return jsonify({"ok": True, "files": files, "folder": str(kd)})

    @app.route("/api/simulate", methods=["POST"])
    def api_simulate():
        body = request.get_json(force=True)
        sender = str(body.get("sender", "!gui00000")).strip()[:20]
        text = str(body.get("text", "")).strip()
        if not text:
            return jsonify({"ok": False, "error": "empty message"}), 400
        try:
            router = _get_router()
            messages = router.route_multi(sender, text)
            return jsonify({"ok": True, "responses": messages or []})
        except Exception as e:
            log.exception("simulate error")
            return jsonify({"ok": False, "error": str(e)}), 500

    @app.route("/api/board")
    def api_board():
        board_path = Path(_state["cfg"]["_cache_dir"]) / "board.json"
        if not board_path.exists():
            return jsonify({"ok": True, "posts": []})
        try:
            return jsonify({
                "ok": True,
                "posts": json.loads(board_path.read_text(encoding="utf-8")),
            })
        except Exception as e:
            return jsonify({"ok": False, "error": str(e)}), 500

    @app.route("/api/logs")
    def api_logs():
        try:
            n = min(max(int(request.args.get("lines", 100)), 10), 500)
        except ValueError:
            n = 100
        log_path = Path(_state["cfg"].get("_config_dir", ".")) / "del_fi.log"
        if not log_path.exists():
            return jsonify({"ok": True, "lines": [], "file": str(log_path)})
        try:
            text = log_path.read_text(encoding="utf-8", errors="replace")
            return jsonify({
                "ok": True,
                "lines": text.splitlines()[-n:],
                "file": str(log_path),
            })
        except Exception as e:
            return jsonify({"ok": False, "error": str(e)}), 500

    return app


def launch(cfg: dict, config_path: str, port: int = 5174, open_browser: bool = True):
    """Start the GUI server and optionally open the browser."""
    app = create_app(cfg, config_path)
    url = f"http://127.0.0.1:{port}"
    print(f"\n  ·· DEL-FI GUI ··  {url}\n  Ctrl+C to stop\n")
    log.info(f"GUI server at {url}")

    if open_browser:
        def _open():
            time.sleep(0.8)
            webbrowser.open(url)
        threading.Thread(target=_open, daemon=True).start()

    # Localhost-only; never bind to 0.0.0.0 (remote SSH users: use port forwarding)
    app.run(host="127.0.0.1", port=port, debug=False, use_reloader=False)
