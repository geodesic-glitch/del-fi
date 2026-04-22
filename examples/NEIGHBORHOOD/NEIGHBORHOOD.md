# MAPLEWOOD-ORACLE — Deployment Guide

A community hub oracle for Maplewood, a residential neighborhood in the Pacific Northwest. This oracle answers questions about local services, infrastructure, events, organizations, and seasonal conditions. It is the reference deployment for Del-Fi v0.2 community-hub nodes.

---

## Oracle Profile

| Property | Value |
|----------|-------|
| Node name | `MAPLEWOOD-ORACLE` |
| Oracle type | `community-hub` |
| Hardware | Raspberry Pi 5 + Meshtastic LoRa module |
| Location | Roof of Maplewood Branch Library |
| Serving model | `gemma4:4b` |
| Builder model | `gemma4:12b` |
| Neighbors | EASTSIDE-RELAY, GARFIELD-NODE, MILLBROOK-SENSOR |

**Persona:** A helpful, plain-spoken neighborhood assistant. Answers questions about local resources, infrastructure status, organizations, and events. Avoids political commentary. Always cites sources (which knowledge file the answer came from). Redirects emergency queries to 911 immediately.

---

## Suggested config.yaml

```yaml
node_name: "MAPLEWOOD-ORACLE"
node_description: "Community oracle for Maplewood neighborhood. Ask about local services, infrastructure, events, and organizations."
oracle_type: "community-hub"

model: "gemma4:4b"
wiki_builder_model: "gemma4:12b"

knowledge_folder: ./knowledge
wiki_folder: ./wiki

# These files change frequently — wiki pages get freshness headers at query time
time_sensitive_files:
  - infrastructure.md
  - community-log.md

# Community log changes often (new entries appended); flag as stale quickly
wiki_stale_after_days: 14

# Watch for file changes and patch the wiki incrementally using the serving model
wiki_watch_enabled: true
wiki_patch_threshold_pct: 40

fallback_message: "I don't have that info. Try !topics to see what I know, or ask at the library desk."
welcome_footer: "MAPLEWOOD-ORACLE — ask about local resources, events, and infrastructure."

trusted_peers:
  - "EASTSIDE-RELAY"
  - "GARFIELD-NODE"
  - "MILLBROOK-SENSOR"

fact_query_keywords:
  - creek level
  - creek height
  - flooding
  - creek flood
  - power out
  - outage

mesh_adapter: meshtastic
```

---

## Knowledge Base Files

| File | Contents | Update frequency |
|------|----------|-----------------|
| `area-overview.md` | Neighborhood boundaries, node infrastructure, key facilities, neighboring nodes | Rarely — update if node hardware or boundaries change |
| `community-log.md` | Recent neighborhood events, incidents, announcements (rolling log, newest first) | Weekly or more — append new entries at the top |
| `infrastructure.md` | Creek level, power status, road conditions, node status | As conditions change — update after incidents |
| `local-resources.md` | Directory of services: emergency, medical, food, schools, utilities, transit | Periodically — verify hours/contacts annually |
| `organizations-guide.md` | Community organizations, contacts, meeting times, programs | When organizations change — stable year to year |
| `seasonal-notes.md` | Year-round seasonal patterns, hazards, events calendar | Annually — review each season for accuracy |

### Update workflow

**Routine updates** (e.g. new community-log entry, creek level change):
1. Edit the source file in `knowledge/`
2. The `wiki_watch_enabled` background thread detects the change and calls `patch()` automatically
3. No manual rebuild needed for small appends

**Before deployment or after significant knowledge changes**:
```bash
# Build the full wiki using the larger builder model
python main.py --build-wiki --config config.yaml

# Check the wiki health
python main.py --lint-wiki --config config.yaml
```

---

## Deployment Notes

### Hardware setup
- Raspberry Pi 5 (4 GB RAM minimum). 8 GB recommended if running `gemma4:12b` for builds.
- Meshtastic-compatible LoRa module (e.g. RAK4631, T-Beam) connected via USB or UART.
- External antenna recommended for rooftop mounting — gains ~3 dB over stock whip.
- Battery backup (UPS hat or separate): library power is reliable but occasional outages occur (5–6 hour runtime observed).

### Software setup
```bash
# Install Ollama and pull models
curl -fsSL https://ollama.com/install.sh | sh
ollama pull gemma4:4b          # serving model (always running)
ollama pull gemma4:12b         # builder model (used only for --build-wiki)
ollama pull nomic-embed-text   # embeddings

# Clone repo and install deps
git clone https://github.com/your-org/del-fi.git
cd del-fi
pip install -r requirements.txt

# Add your knowledge files to knowledge/
# (copy from this examples/NEIGHBORHOOD/knowledge/ as a starting point)
cp -r examples/NEIGHBORHOOD/knowledge/* knowledge/

# Edit knowledge files for your actual neighborhood, then build the wiki
python main.py --build-wiki --config config.yaml

# Start the daemon
python main.py --config config.yaml
```

### First build time
`--build-wiki` with `gemma4:12b` takes approximately 3–8 minutes per knowledge file on Raspberry Pi 5. With 6 files, expect 20–45 minutes total. Run this offline before deployment. Subsequent `patch()` updates via `watch()` use `gemma4:4b` and complete in under a minute.

### Mesh positioning
The library rooftop gives excellent line-of-sight to most of the neighborhood. The main coverage gap is the lower Greenway east of Sycamore (screened by the creek embankment). MILLBROOK-SENSOR covers part of this gap.

---

## Adapting This Deployment

To use this template for a different community hub:
1. Replace all knowledge files with content relevant to your neighborhood
2. Update `node_name` in config.yaml
3. Update `trusted_peers` to reflect your actual neighboring nodes
4. Update `fact_query_keywords` to match the environmental data your node tracks
5. Run `--build-wiki` with the builder model before going live
6. Run `--lint-wiki` to verify the wiki is healthy before deployment
