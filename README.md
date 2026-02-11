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

**A daemon that bridges Meshtastic LoRa mesh radio networks with locally-hosted LLMs and RAG knowledge bases.**

Drop documents into a folder, connect a $20 radio, and your community has an AI oracle that answers questions over mesh — no internet, no cloud, no accounts. Just radio waves and local knowledge.

<!-- TODO: photo of real hardware here — Pi + LoRa radio, hand-labeled project box -->

---

## How It Works

```
[LoRa Radio] <--serial/tcp/ble--> [Mesh Interface] <--> [Query Router] <--> [RAG Engine]
                                                              |                  |
                                                      [Response Formatter]  [Mesh Knowledge]
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
- Antenna placement matters more than radio choice for range

**Software:**
- Python 3.10+
- [Ollama](https://ollama.com/) (manages local LLM inference)
- A Meshtastic radio flashed with current firmware

---

## Install

```bash
# 1. Install Ollama (if you haven't)
curl -fsSL https://ollama.com/install.sh | sh

# 2. Pull models
ollama pull qwen2.5:3b              # generation (pick your size)
ollama pull nomic-embed-text         # embeddings (required for RAG)

# 3. Clone Del-Fi
git clone https://github.com/geodesic-glitch/del-fi.git
cd del-fi

# 4. Install Python dependencies
pip install -r requirements.txt

# 5. Set up config
mkdir -p ~/del-fi/knowledge
cp config.example.yaml ~/del-fi/config.yaml
# Edit ~/del-fi/config.yaml — set node_name, model, and radio_port

# 6. Run
python delfi.py
```

That's 6 commands. The config file has two required fields (`node_name` and `model`). Everything else has sensible defaults.

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
personality: "Helpful and concise community assistant."
knowledge_folder: ~/del-fi/knowledge
max_response_bytes: 230
radio_connection: serial     # serial | tcp | ble
radio_port: /dev/ttyUSB0     # or hostname:port for TCP
rate_limit_seconds: 30
response_cache_ttl: 300
embedding_model: "nomic-embed-text"
channels: []                 # empty = listen on all channels
log_level: info
ollama_host: "http://localhost:11434"
ollama_timeout: 120
```

Every field except `node_name` and `model` has a default. Bad config prints a human-readable error, not a traceback.

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

Seven Python files. Four dependencies. That's it.

```
delfi.py          Entry point, daemon lifecycle, startup banner
config.py         YAML loading, validation, sensible defaults
mesh.py           Meshtastic interface + simulator mode
router.py         Command dispatch, RAG pipeline, response cache
rag.py            ChromaDB indexing, Ollama embeddings + generation
formatter.py      Markdown stripping, sentence truncation, chunking
meshknowledge.py  Gossip, peer cache, referrals (stdlib only)
```

### Startup Sequence

```
1. Config         Load YAML, validate, exit on error (the one crash)
2. ChromaDB       Open vector store, or disable RAG on failure
3. Indexing       Scan knowledge folder, index new files
4. Ollama         Health check, retry loop if down
5. Radio          Connect, or reconnect loop if unavailable
6. Ready          Print banner, begin listening
```

Principle: **always start, never block.** A missing radio or unavailable Ollama doesn't prevent launch. Components come online as they become available.

---

## Running Tests

```bash
python tests/test_formatter.py    # 32 tests — markdown, truncation, chunking
python tests/test_router.py       # 19 tests — commands, !more cursor, edge cases
python tests/test_rag.py          # 6 tests  — chunking unit + chromadb integration
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

## License

GPL-3.0 — matching the Meshtastic ecosystem.
