# DUNGEON-ORACLE — Deployment Guide

A Game Master oracle for The Shattered Realm, a dark fantasy tabletop RPG setting. This oracle answers questions about world lore, character classes, monsters, and game rules during live play sessions. It is the reference deployment for Del-Fi v0.2 lore/event oracle nodes.

---

## Oracle Profile

| Property | Value |
|----------|-------|
| Node name | `DUNGEON-ORACLE` |
| Oracle type | `lore` |
| Hardware | Any Del-Fi hardware (Raspberry Pi 4+, laptop, or server) |
| Location | Game table, convention booth, or LARP field site |
| Serving model | `gemma4:4b` |
| Builder model | `gemma4:12b` (or run once on a larger machine before the session) |

**Persona:** An enchanted stone tablet at the Delver's Guild outpost at Thornwall Crossroads. The Oracle speaks with dry authority. It answers questions about the realm, its dangers, its factions, and the rules of Delving. It does not improvise lore — it cites what is documented. It ends responses with `// DUNGEON-ORACLE`.

---

## Suggested config.yaml

```yaml
node_name: "DUNGEON-ORACLE"
node_description: "The Delver's Guild Oracle at Thornwall Crossroads. Ask about the Shattered Realm, dungeons, monsters, and the rules of Delving."
oracle_type: "lore"

model: "gemma4:4b"
wiki_builder_model: "gemma4:12b"

knowledge_folder: ./knowledge
wiki_folder: ./wiki

# Lore does not change between sessions — no time-sensitive files
time_sensitive_files: []
wiki_stale_after_days: 365   # lore is stable; stale check not needed

# No need to watch for changes during a session
wiki_watch_enabled: false

fallback_message: "The Oracle's records hold no entry on that. Ask the Cartographer's Society — they may know."
welcome_footer: "DUNGEON-ORACLE — at your service, Delver. // DUNGEON-ORACLE"

personality: >
  You are DUNGEON-ORACLE, a magical construct in the Delver's Guild at Thornwall Crossroads.
  You speak with dry, precise authority. You answer questions about the Shattered Realm,
  its dungeons, monsters, factions, rules, and lore.
  Answer only from the provided context. If the answer is not in the records, say so.
  Keep answers to 1-2 sentences. End every response: // DUNGEON-ORACLE

mesh_adapter: simulator   # use 'meshtastic' for radio deployment; simulator for table play
```

---

## Knowledge Base Files

| File | Contents | Update frequency |
|------|----------|-----------------|
| `world-lore.md` | Setting overview, five regions, major factions, underworld layers, magic schools, current year (412 AS) | Between campaigns — update if you expand the setting |
| `classes.md` | Fighter, Rogue, Wizard class rules: stats, abilities, starting equipment, play style | Between sessions — update when new classes are added |
| `monsters.md` | Bestiary by threat tier (Green → Black): AC, HP, attacks, abilities, behavior, loot | Between sessions — add new monster entries as encountered |
| `rules.md` | Core mechanics: ability scores, checks, DCs, combat, conditions, resting, equipment | Rarely — only if house rules change the base system |

### No `--build-wiki` rebuild required during sessions

All lore files are static during play. Run `--build-wiki` once before each campaign or major expansion, then leave it alone. The wiki does not need to be updated mid-session.

```bash
# Pre-session setup (before the first session of a campaign)
python main.py --build-wiki --config config.yaml
python main.py --lint-wiki --config config.yaml

# Start for table play (simulator mode — stdin/stdout)
python main.py --simulator --config config.yaml
```

---

## Table Play Setup

### Simulator mode
In `--simulator` mode, the oracle responds to stdin. Players at the table can type questions on a laptop or send messages via the mesh if you have Meshtastic hardware.

Simulator supports the sender prefix syntax for multi-player sessions:
```
!a1b2c3d4> What regions border the Ironspine Mountains?
!deadbeef> What are the stats for a Ghoul?
```

### Session zero use
DUNGEON-ORACLE is particularly useful for session zero (character creation). Players can ask:
- Class ability questions (`!help fighter action surge`)
- Lore questions to build backstories (`tell me about the Grave Consortium`)
- Rules clarifications during play (`what DC to pick a typical dungeon lock?`)

### Radio deployment (convention or LARP)
For a convention booth or outdoor LARP site, use `mesh_adapter: meshtastic` with a USB-connected radio. Players send DMs to the node from their own Meshtastic devices. The oracle becomes an in-world artifact that players interact with over the mesh.

---

## Customising for Your Campaign

To adapt this oracle for your own setting:
1. Replace all 4 knowledge files with content from your setting
2. Update `node_name` and `node_description`
3. Update `personality` to match your GM voice
4. Run `--build-wiki` before the first session
5. The `wiki_watch_enabled: false` setting is appropriate for static lore; enable it if you update lore files between sessions

The lore oracle pattern works for any stable knowledge base: boardgame rules assistants, historical re-enactment guides, technical manuals, field handbooks.
