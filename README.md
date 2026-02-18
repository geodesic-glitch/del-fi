```
 ██████   █████  ██             █████  ████
 ██   ██  ██     ██             ██      ██
 ██   ██  ████   ██    ════    ████    ██
 ██   ██  ██     ██             ██      ██
 ██████   █████  █████          ██     ████

          ██████   ██████   █████
          ██   ██  ██   ██  ██
          ██████   ██████   █████
          ██   ██  ██   ██     ██
          ██████   ██████   █████

         community AI oracle over LoRa mesh radio
```

# Del-Fi

**A daemon that bridges LoRa mesh radio networks with locally-hosted LLMs and RAG knowledge bases.**

Drop documents into a folder, connect a $20 radio, and your community has an AI oracle that answers questions over mesh — no internet, no cloud, no accounts. Just radio waves and local knowledge.

Supports **Meshtastic** and **MeshCore** mesh protocols through a pluggable adapter system — same oracle, your choice of radio stack.

<!-- TODO: photo of real hardware here — Pi + LoRa radio, hand-labeled project box -->

---

## How It Works

```
[LoRa Radio] <--serial/tcp/ble--> [Mesh Adapter] <--> [Query Router] <--> [RAG Engine]
                                        |                  |                  |
                                   (meshtastic     [Response Formatter]  [Mesh Knowledge]
                                    or meshcore)
```

Someone on the mesh sends your node a DM. Del-Fi searches your local documents, feeds the relevant chunks to a local LLM, and sends back a concise answer — all within the 230-byte LoRa message limit. No internet required. Everything runs on your hardware.

---

## What You Need

**Compute (pick one):**

| Hardware | Speed | Power | Cost | Best For |
|---|---|---|---|---|
| Raspberry Pi 5 8GB | ~5 tok/s (3B) | ~10W | ~$80 | Budget field nodes |
| Jetson Orin Nano Super | ~30 tok/s (3B) | ~15W | ~$249 | Solar field nodes |
| Mac Mini M4 | ~18 tok/s (7B) | ~30W | ~$499 | Powered stations |

**Radio:**
- Any [Meshtastic-supported LoRa radio](https://meshtastic.org/docs/hardware/devices/) — Heltec V3 (~$20) works great
- [MeshCore-flashed radios](https://github.com/ripplebiz/MeshCore) also supported
- Antenna placement matters more than radio choice for range

**Software:**
- Python 3.10+
- [Ollama](https://ollama.com/) (manages local LLM inference)
- A Meshtastic or MeshCore radio flashed with current firmware

---

## Install

```bash
# 1. Install Ollama (if you haven't)
curl -fsSL https://ollama.com/install.sh | sh

# 2. Pull models
ollama pull gemma3:4b-it-qat              # generation (pick your size)
ollama pull nomic-embed-text         # embeddings (required for RAG)

# 3. Clone Del-Fi
git clone https://github.com/geodesic-glitch/del-fi.git
cd del-fi

# 4. Create a virtual environment and install dependencies
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt

# 5. Set up config
mkdir -p ~/del-fi/knowledge
cp config.example.yaml ~/del-fi/config.yaml
# Edit ~/del-fi/config.yaml — set node_name, model, and radio_port

# 6. Run
python delfi.py
```

That's 6 commands. The config file has two required fields (`node_name` and `model`). Everything else has sensible defaults.

> **Raspberry Pi / Debian note:** Modern Raspberry Pi OS (Bookworm+) marks the system Python as externally managed (PEP 668), so `pip install` outside a venv will fail. The virtual environment in step 4 handles this. If you see `error: externally-managed-environment`, make sure you activated the venv (`source venv/bin/activate`) before running pip. You may also need `sudo apt install python3-full` if `python3 -m venv` isn't available.

> **Important:** You must activate the venv **every time** you open a new shell before running Del-Fi. If you see `No module named 'ollama'` (or any other import error), you forgot to `source venv/bin/activate`. The `ollama` CLI is a separate system binary — it will work without the venv, but the Python package won't. For a headless Pi you can add the activate line to your `.bashrc` or use a systemd service (see [Running as a Service](#running-as-a-service) below).

**Simulator mode** (no radio needed — for development and testing):

```bash
python delfi.py --simulator
```

This reads from stdin and writes to stdout. You can test the full pipeline without hardware.

---

## Add Knowledge

Drop `.txt` or `.md` files into `~/del-fi/knowledge/`:

```bash
cp field-guide-to-edible-plants.txt ~/del-fi/knowledge/
cp emergency-procedures.md ~/del-fi/knowledge/
```

Del-Fi watches this folder. New files are automatically chunked, embedded, and indexed. No restart needed — changes are picked up within 60 seconds.

The file names become your topic list. A file named `wilderness-first-aid.md` shows up when someone sends `!topics`.

---

## Use It

From any Meshtastic app, DM the Del-Fi node:

```
You:   What plants are edible in April?
Node:  Several common edibles are available in
       April: dandelion greens, wild garlic,
       chickweed, and violet leaves. All are best
       harvested young. [!more]
You:   !more
Node:  Dandelion is identifiable by its toothed
       leaves and yellow flower. Avoid lookalikes
       like cat's ear which has branching stems.
```

### Commands

| Command | What it does |
|---|---|
| `!help` | Usage instructions and available commands |
| `!topics` | List loaded knowledge base topics |
| `!status` | Node health, model info, uptime, doc count |
| `!more` | Next chunk of a long response |
| `!more 2` | Re-request chunk 2 (if a chunk was lost) |
| `!ping` | Liveness check |
| `!peers` | Show peered and nearby Del-Fi nodes |

Commands are always processed immediately — they bypass the rate limiter. Freeform queries are rate-limited to one every 30 seconds per sender (configurable).

---

## Configuration

`~/del-fi/config.yaml`:

```yaml
# Required
node_name: "FARM-ORACLE"
model: "qwen2.5:3b"

# Optional — defaults shown
mesh_protocol: meshtastic    # meshtastic | meshcore
personality: "Helpful and concise community assistant."
knowledge_folder: ~/del-fi/knowledge
max_response_bytes: 230
radio_connection: serial     # serial | tcp | ble (Meshtastic)
radio_port: /dev/ttyUSB0     # or hostname:port for TCP
rate_limit_seconds: 30
response_cache_ttl: 300
busy_notice: true            # tell queued users their question is in line
embedding_model: "nomic-embed-text"
channels: []                 # empty = listen on all channels
log_level: info
ollama_host: "http://localhost:11434"
ollama_timeout: 120
```

Every field except `node_name` has a default (`model` defaults to `qwen3:4b`). Bad config prints a human-readable error, not a traceback.

### MeshCore Configuration

To use a MeshCore radio instead of Meshtastic:

```yaml
mesh_protocol: meshcore
meshcore:
  port: "/dev/ttyUSB0"        # serial port or host:port
  connection: serial           # serial | tcp
  baud_rate: 115200
```

> **Note:** The MeshCore adapter is currently a stub with full scaffolding. Implement the `connect()` and `send_dm()` methods in `mesh/meshcore_adapter.py` against the MeshCore Python library to bring it online.

### Mesh Knowledge (Optional)

Connect your node to a trust-based knowledge network. See [Setting Up Peering](#setting-up-peering) below.

```yaml
mesh_knowledge:
  gossip:
    enabled: true
    announce_interval: 14400    # 4 hours
    directory_ttl: 86400        # 24 hours

  peers:
    - node_id: "!a1b2c3d4"
      name: "MARINA-ORACLE"

  sync:
    enabled: true
    window_start: "02:00"
    window_end: "05:00"
    max_cache_age: 7d
    max_cache_entries: 500

  serve_to_peers: false         # OFF by default — privacy first
  tag_responses: true
  reject_contradictions: true
```

---

## Setting Up Peering

Del-Fi nodes can share knowledge through explicit trust relationships. This is a human decision, not an automatic process.

### Three Tiers of Knowledge

```
Tier 1 — Operator Knowledge     Your docs. Sacrosanct. Never overridden.
Tier 2 — Peered Knowledge       Cached Q&A from trusted peers. Tagged with source.
Tier 3 — Mesh Gossip            Metadata only. Enables referrals, not answers.
```

### How to Peer

1. **Meet the other operator.** In person, at a makerspace, ham radio club, wherever. Peering is a trust decision between humans.

2. **Exchange hardware node IDs.** These look like `!a1b2c3d4`. Not display names — those aren't authenticated.

3. **Both operators add each other to their config:**

```yaml
mesh_knowledge:
  peers:
    - node_id: "!a1b2c3d4"
      name: "MARINA-ORACLE"
```

4. **That's it.** Nodes will sync cached Q&A pairs during quiet hours (2–5 AM by default). Answers from peers are always tagged:

```
[via MARINA-ORACLE] Smallmouth bass creel limit
in February is 6 per day, 12" minimum.
```

### What Peering Doesn't Do

- Mesh knowledge never masquerades as your docs
- Nodes never automatically forward queries
- Peering is never automatic
- Local docs always win in conflicts

### Gossip

Even without peering, Del-Fi nodes that hear each other on the mesh exchange topic metadata. This enables referrals:

```
I don't have info on fish species. MARINA-ORACLE
advertises: fishing-regulations, species-id,
bait-guide. Try DMing them directly.
```

No knowledge transferred. No trust required. Just a pointer.

---

## Troubleshooting

**"Radio not detected"**
- Check USB connection: `ls /dev/ttyUSB*` or `ls /dev/ttyACM*`
- Verify Meshtastic firmware is flashed
- Try `radio_connection: tcp` with the radio's IP if serial is flaky
- Run with `--simulator` to test everything else while you debug the radio

**"Ollama not available"**
- Is Ollama running? `curl http://localhost:11434/api/tags`
- Is the model pulled? `ollama list` should show your configured model
- Del-Fi will keep retrying every 30 seconds — commands work while waiting

**"No documents loaded"**
- Files must be `.txt` or `.md` (PDF support coming later)
- Check the knowledge folder path in your config
- Binary files and unreadable files are silently skipped (check logs)

**Slow responses**
- 3B models are the sweet spot for constrained hardware
- On Pi 5, expect 5-10 seconds per response — that's normal
- Large knowledge bases (50+ docs) may slow initial indexing

**Messages getting cut off**
- LoRa limit is 230 bytes. Responses are auto-truncated at sentence boundaries.
- Send `!more` to get the next chunk
- If a chunk was lost, `!more 2` re-requests that specific chunk

---

## Use Cases

**Trail Oracle** — Solar node at a trailhead. Plant ID, trail conditions, wildlife, emergency procedures. No cell signal needed.

**Farm Oracle** — Planting calendars, livestock medicine, equipment repair. The knowledge in one person's head, available to everyone on the property.

**Emergency Response** — Triage protocols, shelter locations, phrase books. Works when cell towers don't.

**Interactive Fiction** — Text adventures over radio. 230 bytes forces Zork-density prose. `!more` becomes "look around." Geocaching crossover: hide a solar node with a story.

**Festival Concierge** — Schedules, vendor maps, food guides at a maker faire. No cell service required.

**Museum Docent** — Local history, oral histories, old maps. Works across the whole property. Cheaper than a touchscreen kiosk.

**The Dead Drop** — Mysterious node appears on mesh. Cryptic name, oddly specific local knowledge. No one knows who runs it. Part art installation, part folklore.

**Neighborhood Mesh** — HOA rules, garbage schedule, business hours. "When's bulk trash pickup?"

---

## Architecture

Seven Python files (plus the mesh adapter package). Four dependencies. That's it.

```
delfi.py              Entry point, daemon lifecycle, startup banner
config.py             YAML loading, validation, sensible defaults
mesh/                 Pluggable mesh protocol adapters
  __init__.py           Factory + adapter registry
  base.py               Abstract MeshAdapter interface
  meshtastic_adapter.py Meshtastic radios (serial/TCP/BLE)
  meshcore_adapter.py   MeshCore radios (stub — ready to implement)
  simulator.py          stdin/stdout for development
router.py             Command dispatch, RAG pipeline, response cache
rag.py                ChromaDB indexing, Ollama embeddings + generation
formatter.py          Markdown stripping, sentence truncation, chunking
meshknowledge.py      Gossip, peer cache, referrals (stdlib only)
```

### Adding a New Mesh Protocol

1. Create `mesh/<protocol>_adapter.py` with a class that inherits from `MeshAdapter`
2. Implement `connect()`, `send_dm()`, and `close()`
3. Register it in `mesh/__init__.py` → `ADAPTERS` dict
4. Add protocol-specific config defaults in `config.py`

### Startup Sequence

```
1. Config         Load YAML, validate, exit on error (the one crash)
2. ChromaDB       Open vector store, or disable RAG on failure
3. Indexing       Scan knowledge folder, index new files
4. Ollama         Health check, retry loop if down
5. Radio          Connect, or reconnect loop if unavailable
6. Ready          Print banner, spawn worker thread, begin listening
```

The main loop is a **dispatcher**: commands and gossip are handled inline (sub-millisecond), while LLM queries are handed to a background worker thread. If the worker is already processing a query, new senders receive a brief "hang tight" ack so they know their question was received. This keeps the daemon responsive under load without blocking the radio.

Principle: **always start, never block.** A missing radio or unavailable Ollama doesn't prevent launch. Components come online as they become available.

---

## Running as a Service

On a headless Pi, run Del-Fi via systemd so it starts on boot and always uses the correct venv — no SSH session required.

Create `/etc/systemd/system/delfi.service`:

```ini
[Unit]
Description=Del-Fi mesh oracle
After=network.target ollama.service
Wants=ollama.service

[Service]
Type=simple
User=pi
WorkingDirectory=/home/pi/del-fi
ExecStart=/home/pi/del-fi/venv/bin/python delfi.py
Restart=on-failure
RestartSec=10

# Keep thermals in check
CPUQuota=80%
MemoryMax=75%

[Install]
WantedBy=multi-user.target
```

> **Key detail:** `ExecStart` points directly at the venv's Python binary (`venv/bin/python`), so you don't need to activate the venv — systemd handles it.

```bash
sudo systemctl daemon-reload
sudo systemctl enable delfi          # start on boot
sudo systemctl start delfi           # start now
journalctl -u delfi -f               # tail logs
```

Adjust `User`, `WorkingDirectory`, and paths if your clone is somewhere other than `/home/pi/del-fi`.

### Pi Thermal Tips

If your Pi is running hot during inference:

- **Use a smaller model** — `gemma3:1b` or `qwen2.5:1.5b` instead of 4B+
- **Raise `rate_limit_seconds`** to 30–60 to give the CPU thermal recovery time between queries
- **Set `num_ctx: 1024`** and **`num_predict: 64`** to reduce per-request compute
- **Lower `memory_max_turns`** to 3–5 to keep prompts smaller
- **Add a heatsink + fan** — the official Pi 5 active cooler makes a big difference
- The `CPUQuota=80%` in the service file above prevents Del-Fi from fully saturating the CPU

---

## Running Tests

```bash
python -m pytest tests/             # run all tests
python -m pytest tests/test_mesh.py # 16 tests — adapter pattern, factory, simulator
python -m pytest tests/test_formatter.py    # markdown, truncation, chunking
python -m pytest tests/test_router.py       # commands, classify, busy notice, !more cursor
python -m pytest tests/test_rag.py          # chunking unit + chromadb integration
python -m pytest tests/test_stress.py       # concurrency, dispatcher, busy-notice integration
```

---

## Contributing

Del-Fi follows the "boring technology" principle. Before adding a dependency, ask: can this be done with stdlib? Before adding a feature, ask: does this make the first-run experience harder?

The four non-negotiable constraints:

1. **Don't crash the daemon.** Every error caught, every failure recovered.
2. **Be honest.** No answer? Say so. Peer answer? Say where it came from.
3. **Fit in LoRa.** ≤ 230 bytes per message. No exceptions.
4. **Be readable.** Plain text. No markdown artifacts. No "as an AI language model."

Everything else is fair game.

---

## Roadmap

**Meshmouth** — LLM-native wire format for oracle-to-oracle traffic.

Right now gossip and peer sync use human-readable strings. Both ends are language models — they don't need `key=value` headers and full English sentences on the wire. Meshmouth is a compact encoding that LLMs can produce and consume natively: fixed token-budget preambles, lossy semantic compression, symbolic shorthand, and negotiated per-pair codebooks. The goal is 3–5× more meaning in the same 230-byte LoRa frame when oracles talk to each other, while still decompressing cleanly for human questioners. Think of it as a pidgin the oracles converge on — not a hand-designed binary protocol, but a model-discovered compressed language.

---

## License

GPL-3.0 — matching the Meshtastic ecosystem.
