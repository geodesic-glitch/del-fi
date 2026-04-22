# Contributing to Del-Fi

Thanks for your interest in contributing. Del-Fi is a community project for
community infrastructure — contributions of all kinds are welcome.

---

## Ways to contribute

- **Bug fixes** — see open issues labelled `bug`
- **New mesh adapter** — add support for a new radio protocol
- **Knowledge base templates** — improve `examples/` for new oracle types
- **Documentation** — improve README, examples, or `.claude/` specs
- **Testing** — add test cases, especially for edge cases in the formatter
- **Hardware testing** — reports from real deployments on new hardware

---

## Development setup

```bash
git clone https://github.com/geodesic-glitch/del-fi.git
cd del-fi
python3 -m venv .venv
source .venv/bin/activate          # Windows: .venv\Scripts\activate
pip install -r requirements.txt

# Run tests (no hardware or Ollama required)
python -m unittest discover tests/

# Simulator mode (no radio hardware required)
python main.py --simulator
```

You do not need a physical radio or a running Ollama instance to run the tests.
The test suite mocks all external dependencies.

---

## Read the spec first

Before making non-trivial changes, read the relevant spec file:

| Changing | Read |
|----------|------|
| Knowledge retrieval | `.claude/spec-knowledge.md` |
| Mesh adapters | `.claude/spec-mesh.md` |
| Query routing / commands | `.claude/spec-router.md` |
| Response formatting | `.claude/spec-formatter.md` |
| Memory / board / facts | `.claude/spec-memory.md` |
| Config system | `.claude/spec-config.md` |
| Brand / tone | `.claude/brand.md` |

The master spec is `.claude/claude.md`. It covers architecture, conventions,
and the testing contract.

---

## Adding a new mesh adapter

1. Create `mesh/<protocol>_adapter.py`
2. Subclass `MeshAdapter` from `mesh/base.py`
3. Implement: `connect()`, `send_dm(dest, text)`, `close()`
4. Optionally implement `reconnect_loop()` for auto-recovery
5. Register the adapter name in `config.py` → `MESH_ADAPTERS` dict
6. Add tests in `tests/test_mesh.py` covering: connect, send_dm, rate limiting, dedup

---

## Adding a new command

1. Add entry to `COMMAND_REGISTRY` in `router.py`
2. Implement `_cmd_<name>(self, sender: str, args: str) -> str`
3. Handler must return a string — the formatter will enforce the 230-byte limit
4. Update `_cmd_help()` text to include the new command
5. Add tests in `tests/test_router.py`

---

## Code conventions

- Python 3.10+
- Type hints on all public methods
- Logging via `log = logging.getLogger(__name__)` — no `print()` in library code
- Max line length: 100 characters
- Tests use `unittest` (no pytest dependency)
- Imports: stdlib → third-party → local, with blank lines between groups

---

## Pull request checklist

- [ ] Tests pass: `python -m unittest discover tests/`
- [ ] New behaviour has test coverage
- [ ] No internet calls added (offline-first constraint)
- [ ] Formatter is still called before every radio send
- [ ] `config.yaml` is not committed (only `config.example.yaml`)
- [ ] Added/updated `config.example.yaml` if new config keys were introduced

---

## Reporting bugs

Use the **Bug Report** issue template. Include:
- Hardware (Raspberry Pi model, PC, etc.)
- OS and Python version
- Mesh adapter type (Meshtastic serial/TCP/BLE, MeshCore, simulator)
- Ollama model being used
- Full error output and relevant log lines

---

## Questions

Open a Discussion on GitHub rather than an issue for general questions.
