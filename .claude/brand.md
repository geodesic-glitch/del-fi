# Del-Fi — Brand Guidelines

<!-- Parent: .claude/claude.md -->
<!-- Reference: docs/index.html (authoritative CSS variables) -->

---

## 1. Visual Identity

Del-Fi has a terminal CRT aesthetic. The visual language is: retro BBS, field
data terminal, something discovered rather than marketed. Not a SaaS product.

### 1.1 Typography

- **Primary font:** IBM Plex Mono (monospace)
- **Fallback chain:** `'IBM Plex Mono', 'Courier New', Courier, monospace`
- All UI text is monospaced. No sans-serif or serif fonts.

### 1.2 Color Palette

Canonical CSS variables from `docs/index.html`:

```css
--bg:           #0c0c0c;   /* near-black background */
--fg:           #cccccc;   /* default foreground (off-white) */
--cyan:         #54e4e4;   /* primary accent — node names, headings, links */
--yellow:       #e4e454;   /* secondary accent — warnings, source labels */
--green:        #54e454;   /* success / confirmation */
--magenta:      #e454e4;   /* emphasis, tier labels */
--red:          #e45454;   /* errors, stale data warnings */
--dim:          #666666;   /* timestamps, metadata, secondary text */
--border-color: #444444;   /* box-drawing characters, separators */
```

### 1.3 CRT Effect

The landing page (`docs/index.html`) uses a CSS scanlines overlay:

```css
body::after {
    content: "";
    position: fixed;
    inset: 0;
    background: repeating-linear-gradient(
        to bottom,
        transparent 0px,
        transparent 2px,
        rgba(0, 0, 0, 0.08) 2px,
        rgba(0, 0, 0, 0.08) 4px
    );
    pointer-events: none;
}
```

This is a UI decoration for the web docs. It does not affect radio response
formatting.

---

## 2. Project Name

| Context | Form |
|---------|------|
| Prose, documentation | **Del-Fi** |
| Code (class names) | `DelFi`, `DelFiConfig`, etc. |
| CLI commands | `del-fi`, `python main.py` |
| Node names (config) | `ALL-CAPS-HYPHENATED` (not the project name) |
| Package name | `del_fi` (Python: underscore) |

**Never:** `DELFI`, `delfi` (in prose), `DelFI`, `del_fi` (in prose/docs), `DelFi`.

**Tagline:** "community AI oracle over LoRa mesh radio"

---

## 3. Response Tone

The oracle's voice depends on the `oracle_type` configuration, but several
principles apply universally to all radio responses:

### 3.1 Universal radio response principles

- **Terse.** Aim for 1–3 sentences. Every word costs airtime.
- **Factual.** Cite sources: camera numbers, station names, dates, readings.
- **No hedging.** Do not say "It is possible that...", "There might be...",
  "I think...". If you don't know, say: "No data." Full stop.
- **No em-dashes in radio output.** Em-dashes (`—`) consume extra bytes on some
  encodings and render poorly on small displays. Use commas or periods instead.
- **No markdown in radio output.** The formatter strips it, but the LLM prompt
  should explicitly say: "No markdown."
- **Numbers and units.** Always include units with sensor readings.
  `-3°C` not `-3 degrees`. `14km/h` not `14 km/h`.

### 3.2 Tone by oracle type

| Oracle type | Voice |
|-------------|-------|
| `observatory` | Field naturalist. Scientific precision. Cite instruments and dates. Dry, not clinical. |
| `community-hub` | Friendly neighbor. Present tense. Direct. Community-first. |
| `emergency` | Clear authority. Short sentences. Action-oriented. No humor. |
| `event` | Knowledgeable guide. Enthusiastic but controlled. Cite schedule and venue. |
| `trade` | Workshop expert. Practical. No-nonsense. Cite availability and specs. |
| `lore` | Storyteller. Evocative. But still brief — radio is not a novel. |

---

## 4. Node Naming Convention

```
ALL-CAPS-HYPHENATED
```

**Good:**
- `RIDGELINE` — single word, geographic feature
- `VALLEY-ORACLE` — two words, descriptive
- `SUMMIT-POST` — evocative, place-based
- `NEIGHBORHOOD` — community hub, one word

**Not good:**
- `RidgelineOracle` (mixed case)
- `ridge_oracle` (underscore)
- `My Node` (spaces)
- `Oracle1` (number suffix without meaning)

Node names appear in:
- Gossip announcements on the mesh
- Response suffixes (if `append_node_suffix: true`)
- `!status`, `!ping` command responses
- `[via NODE-NAME]` peer attribution labels

---

## 5. Oracle Persona Types

Canonical types from `examples/GUIDE.md`. Used in `oracle_type` config key
and referenced in system prompts.

### 5.1 Observatory

Wilderness sensor station. Primary data: weather, wildlife, trail conditions,
phenology. Answers from sensors and field observation logs.

**Example:** RIDGELINE (wilderness observatory at 9,240 ft, Routt National Forest, CO)

**Knowledge files:** `weather-station.md`, `wildlife-guide.md`,
`trail-camera-log.md`, `seasonal-notes.md`, `flora-guide.md`

**Update cadence:** Sensor data auto-refreshes; field notes updated weekly–monthly.

**System prompt phrasing:** "You are {node_name}, a high-altitude field station.
Answer from instrument data and field observations."

### 5.2 Community Hub

Neighborhood oracle. Primary data: local resources, services, events, community
log. Human-curated, frequently updated.

**Example:** NEIGHBORHOOD

**Knowledge files:** `area-overview.md`, `local-resources.md`,
`organizations-guide.md`, `community-log.md`, `infrastructure.md`, `seasonal-notes.md`

**Update cadence:** Community log updated daily–weekly; reference docs quarterly.

**System prompt phrasing:** "You are {node_name}, a community information hub.
Answer from local knowledge. Be helpful and neighborly."

### 5.3 Emergency Node

Disaster preparedness or response oracle. Primary data: evacuation routes,
shelter locations, resource maps, contact procedures.

**Knowledge files:** `evacuation-routes.md`, `shelter-locations.md`,
`emergency-contacts.md`, `resource-map.md`

**Update cadence:** Static reference docs updated before emergency season.
No live sensor feed expected.

**System prompt phrasing:** "You are {node_name}, an emergency preparedness
resource. Answer clearly and directly. Short sentences. Cite locations."

### 5.4 Event Oracle

Temporary oracle for a conference, festival, market, or event. Primary data:
schedule, venue map, vendors, programming.

**Knowledge files:** `schedule.md`, `venue-map.md`, `vendors.md`, `faq.md`

**Update cadence:** Pre-event build. Daily update during event.
Decommission post-event.

**System prompt phrasing:** "You are {node_name}, an event information assistant.
Answer from the schedule and venue guide."

### 5.5 Trade Oracle

Workshop, maker space, repair café, or market oracle. Primary data: tools,
materials, skills available, trading/borrowing policies.

**Knowledge files:** `tools-catalog.md`, `materials.md`, `skills-available.md`,
`policies.md`

**Update cadence:** Updated when inventory changes. Weekly refresh recommended.

**System prompt phrasing:** "You are {node_name}, a tool library and workshop
resource. Answer from inventory and policy documents."

### 5.6 Lore Oracle

Artistic installation, game, historical archive, or creative fiction oracle.
Primary data: lore documents, world-building notes, narrative records.

**Knowledge files:** `world-lore.md`, `history.md`, `characters.md`, `rules.md`

**Update cadence:** Updated by lore keepers as the world evolves.

**System prompt phrasing:** "You are {node_name}. Answer from the lore and
records provided. Stay in voice."

---

## 6. Docs and Web Presence

`docs/index.html` is a single-file static landing page. It is the only web
presence for the project. It contains:

- Project tagline and short description
- ASCII art / terminal demo
- Quick-start install commands
- Link to the GitHub repo

Design principles for `docs/index.html`:
- Self-contained: no external JS, no CDN fonts (IBM Plex Mono from bundled CSS
  or system font fallback).
- No tracking, no analytics, no cookies.
- Renders correctly in Lynx and other text browsers.
- Terminal aesthetic: `--bg` background, `--cyan` headings, box-drawing separators.

---

## 7. README

`README.md` covers:
1. What Del-Fi is (2–3 sentences)
2. Quick-start (3 commands: clone, pip install, run with simulator)
3. Hardware and software prerequisites
4. Oracle types (link to `examples/GUIDE.md`)
5. Configuration reference (link to `config.example.yaml` and `.claude/spec-config.md`)
6. Building the wiki (`--build-wiki`)
7. Contributing (link to `.github/CONTRIBUTING.md`)
8. License

README does not duplicate spec content. It points to `.claude/` for deep detail.

---

<!-- End of brand.md -->
