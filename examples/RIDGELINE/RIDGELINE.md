# RIDGELINE — Deployment Guide

A wilderness observatory oracle for Ridgeline Station, a remote field research site at 9,240 ft elevation in the Colorado Rockies (Routt National Forest). This oracle answers questions about local wildlife, weather, trail conditions, flora, and seasonal patterns. It is the reference deployment for Del-Fi v0.2 observatory nodes.

---

## Oracle Profile

| Property | Value |
|----------|-------|
| Node name | `RIDGELINE` |
| Oracle type | `observatory` |
| Hardware | Raspberry Pi 5 + Meshtastic LoRa module |
| Location | Ridgeline Station, 9,240 ft, Routt National Forest, CO |
| Power | 200W solar + 400Ah battery + wind turbine supplement |
| Serving model | `gemma4:4b` |
| Builder model | `gemma4:12b` |
| Neighbors | VALLEY-ORACLE (9 mi SE, 6,800 ft), SUMMIT-POST (11,100 ft, sensor-only) |

**Persona:** A precise, data-oriented field science oracle. Speaks plainly and prioritises safety information. Distances in miles, temperatures in Fahrenheit, elevations in feet. Answers are 1–2 sentences; safety-critical information always comes first.

---

## Suggested config.yaml

```yaml
node_name: "RIDGELINE"
node_description: "Wilderness observatory oracle at 9,240 ft. Wildlife, weather, trails, and field science for Ridgeline Station."
oracle_type: "observatory"

model: "gemma4:4b"
wiki_builder_model: "gemma4:12b"

knowledge_folder: ./knowledge
wiki_folder: ./wiki

# These files change frequently — wiki pages get age headers at query time
time_sensitive_files:
  - weather-station.md
  - trail-camera-log.md

wiki_stale_after_days: 7    # field station data goes stale quickly

# Watch for changes and patch wiki incrementally with serving model
wiki_watch_enabled: true
wiki_patch_threshold_pct: 40

fallback_message: "No data on that. Try !topics or !data for current sensor readings."
welcome_footer: "RIDGELINE — ask about wildlife, weather, trails, and field conditions."

trusted_peers:
  - "VALLEY-ORACLE"
  - "SUMMIT-POST"

# Tier 0 fast path — answer directly from sensor_feed.json for these queries
fact_query_keywords:
  - temperature
  - temp
  - wind
  - snow depth
  - snow
  - conditions
  - weather
  - battery
  - solar
  - creek level
  - creek
  - pressure
  - barometer

mesh_adapter: meshtastic

personality: >
  You are RIDGELINE, a field science oracle at a remote wilderness observatory.
  You speak plainly and precisely. Prioritise safety information.
  Distances in miles, temperatures in Fahrenheit, elevations in feet.
  Two sentences maximum per answer.
```

---

## Knowledge Base Files

| File | Contents | Update frequency |
|------|----------|-----------------|
| `area-overview.md` | Station location, terrain, infrastructure, access routes, neighboring nodes | Rarely — update if hardware changes |
| `weather-station.md` | Current conditions, 7-day log, historical climate averages | **Daily** — update current conditions and append log entry |
| `trail-camera-log.md` | Camera captures by date: species, behavior, camera, time | **Weekly** — append new entries after each SD card check |
| `wildlife-guide.md` | Species accounts: ID, behavior, seasonal patterns, tracks | Rarely — update when new species documented or status changes |
| `flora-guide.md` | Trees, shrubs, ground cover: ID, ecology, seasonal phenology | Rarely — update with phenology observations |
| `seasonal-notes.md` | Year-round calendar: weather, wildlife activity, hazards, access | **Annually** — review each season before it begins |

### Update workflow

**Weather station updates** (daily, via sensor feed):
- The Tier 0 FactStore reads `cache/sensor_feed.json` directly — no wiki rebuild needed for live sensor data.
- Update `weather-station.md` weekly with the 7-day log summary; `watch()` will `patch()` the wiki automatically.

**Camera log updates** (after each monthly SD card check):
1. Append new entries to the top of `trail-camera-log.md`
2. The `watch()` thread detects the change and calls `patch()` using the serving model
3. For large batches (many new entries), run `--build-wiki` for a full re-synthesis

**Before deployment or after major updates**:
```bash
python main.py --build-wiki --config config.yaml
python main.py --lint-wiki --config config.yaml
```

---

## Deployment Notes

### Power considerations
This is a solar-powered deployment. Key risks:
- **Extended overcast (>5 days):** Battery may drop below operating threshold. Ensure the Pi has an undervoltage/shutdown script. Log power state in sensor_feed.json.
- **Winter (Dec–Feb):** Low sun angle + short days = minimal solar. The wind turbine supplements but storms reduce it too. Consider a scheduled low-power mode (processor sleep, radio check-in only every 30 min) during deep winter.
- **Spring:** Full power returns rapidly in March–April as days lengthen. Solar output at this elevation in summer is excellent (>200W peak on clear days).

### Access constraints
- **Summer:** Ridgeline Trail accessible on foot (4.2 mi, 1,900 ft gain from Clark trailhead). Plan 3–4 hours round-trip for hardware service.
- **Winter (Nov–Apr):** Snowmobile or ski access only. CR-129 closed at Forest Service gate (mi marker 6). Any winter service trip requires significant preparation.
- Run `--build-wiki` remotely before winter closes access. The node must be self-sustaining for 4–5 months.

### Connectivity
RIDGELINE has no internet access. All communication is via Meshtastic mesh. VALLEY-ORACLE is the primary peer and can relay queries to lower-elevation users. SUMMIT-POST is sensor-only (weather data; no LLM).

### First build
`--build-wiki` with `gemma4:12b` takes ~20–45 minutes for 6 files on Raspberry Pi 5. Run this connected to a power supply before deployment. The builder model can be run on a separate machine if the Pi lacks RAM for the 12B model; copy the resulting `wiki/` folder to the Pi.

---

## Adapting This Deployment

To use this template for a different field station:
1. Replace knowledge files with content relevant to your location and species
2. Update `node_name` and `node_description`
3. Update `fact_query_keywords` to match your sensor data
4. Update `time_sensitive_files` to match which files change most often
5. Set `wiki_stale_after_days` to match your update cadence (7 is aggressive; use 14–30 for less-active stations)
6. Run `--build-wiki` and `--lint-wiki` before deployment
