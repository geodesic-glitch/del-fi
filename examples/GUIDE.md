# Building a Good Oracle: Knowledge Base Guide

A practical guide to writing, organizing, and deploying the documents that power a Del-Fi oracle.

---

## Contents

1. [How the LLM Wiki Works](#1-how-the-llm-wiki-works)
2. [Oracle Types](#2-oracle-types)
3. [Writing Good Source Documents](#3-writing-good-source-documents)
4. [What Not to Do](#4-what-not-to-do)
5. [Building and Maintaining Your Wiki](#5-building-and-maintaining-your-wiki)
6. [Evaluating Your Oracle](#6-evaluating-your-oracle)
7. [Templates](#7-templates)

---

## 1. How the LLM Wiki Works

Del-Fi uses a **three-layer knowledge architecture** based on the LLM Wiki pattern. The key insight: retrieval quality improves dramatically when an LLM compiles raw source documents into a structured wiki *once*, at ingest time, rather than feeding raw fragments to the serving model at query time.

```
knowledge/          ← You write and maintain these (source of truth)
    wildlife-guide.md
    weather-station.md
    community-log.md
         │
         │  python main.py --build-wiki
         │  (offline, run before deployment)
         ▼
wiki/               ← LLM-compiled, structured pages (do not edit by hand)
    wildlife-guide.md
    weather-station.md
    community-log.md
    index.md
    log.md
         │
         │  query time (BM25 + vector search)
         ▼
context assembled → LLM generates answer → response over radio
```

### What this means for you

**You own `knowledge/`.** Write clear, factual, well-organized source documents. The builder LLM reads them and compiles structured wiki pages with extracted facts, cross-links, and dense summaries. Think of your source documents as briefing notes for a diligent editor.

**You never touch `wiki/`.** It is generated automatically. If you need to change something, edit the source document in `knowledge/` and run `--build-wiki` again. The wiki compounds — each new source updates existing pages rather than replacing them wholesale.

**The wiki is the retrieval unit.** At query time, Del-Fi searches the compiled wiki (not your raw source files). Because the LLM has already synthesised and structured the content, the serving model gets clean, relevant context instead of raw document fragments.

### The build pipeline

```bash
# Compile knowledge/ → wiki/ (run before first use and after updating sources)
python main.py --build-wiki

# Check wiki health: orphan pages, stale content, missing cross-refs
python main.py --lint-wiki
```

`--build-wiki` uses a larger builder model (`wiki_builder_model` config key, defaults to `model`). It is designed to be run with a capable model (7B+) before deployment, even if the serving model is smaller. The compilation work is done once.

### The 230-byte output constraint

LoRa radio limits each message to 230 bytes (~190 characters of English text). The serving model is instructed to answer in 2-3 short sentences, and responses are chunked across multiple messages if needed.

**Implication:** Dense, factual source material produces confident, specific answers. Vague source material forces the model to hedge — and hedging is expensive in 230 bytes. Write like you're briefing someone who needs to act on the information.

---

## 2. Oracle Types

Different deployments have different knowledge shapes. Pick the pattern that fits your use case, then combine files from multiple patterns if needed.

---

### The Observatory

**Example:** RIDGELINE wilderness station

A deployed sensor array collects continuous data (cameras, weather, soil moisture) plus manual field observations. The oracle answers questions about what's happening, what to expect, and what's been recorded.

**Files:**
- `area-overview.md` — location, terrain, infrastructure, access routes
- `wildlife-guide.md` — species reference, identification, behavior, ecology
- `flora-guide.md` — plant reference, phenology, identification
- `trail-camera-log.md` — date-stamped observation events
- `weather-station.md` — current conditions, thresholds, historical norms
- `seasonal-notes.md` — what to expect by month or season

**Update cadence:** Guides — annually. Logs and station data — as events occur.

**Key challenge:** Keep reference guides (timeless) and logs (time-sensitive) in separate files. A query about "elk behavior" should retrieve the wildlife guide, not last week's camera log.

---

### The Community Hub

**Example:** NEIGHBORHOOD mesh node

A shared reference for a residential or commercial area. The oracle answers "who do I call," "what's open," "what's the status of X," and "what's happening in the neighborhood."

**Files:**
- `area-overview.md` — geography, node location, neighboring nodes
- `local-resources.md` — businesses, services, addresses, hours, phone numbers
- `organizations-guide.md` — community groups, governance, contacts
- `infrastructure.md` — roads, utilities, flood status, outage history
- `community-log.md` — recent events, volunteer activity, announcements
- `seasonal-notes.md` — city services calendar, local event patterns

**Update cadence:** Resources and orgs — quarterly. Infrastructure and activity log — continuously.

**Key challenge:** Contact information goes stale quickly. Build a "last verified" date into every entry that has a phone number, address, or hours.

---

### The Emergency Node

A deployable oracle for disaster response, search and rescue, or field medical operations. Answers need to be immediate and unambiguous.

**Files:**
- `procedures.md` — triage protocols, evacuation steps, search patterns
- `shelter-locations.md` — addresses, capacity, access, opening criteria
- `contacts.md` — ICS roles, radio channels, agency numbers
- `medical-reference.md` — field treatments, dosing, contraindications
- `map-reference.md` — grid coordinates, landmarks, known hazards
- `phrase-book.md` — language translations for common emergency phrases

**Update cadence:** Procedures — after every exercise or incident. Contacts — before each deployment.

**Key challenge:** Answers must be crisp and actionable. Avoid hedging language. Every entry should end with a clear action ("go to," "call," "do not"). The 230-byte limit is a feature here — it forces concision.

---

### The Event Oracle

A temporary oracle for a festival, market, maker faire, conference, or similar event. Active for days or weeks.

**Files:**
- `schedule.md` — stage/venue times, set durations, day-by-day breakdown
- `map.md` — vendor locations, stages, entrances, services (first aid, water, toilets)
- `exhibitors.md` — names, descriptions, locations, what they're showing or selling
- `faq.md` — parking, tickets, pets, re-entry, rules, lost and found
- `logistics.md` — volunteer shifts, setup/teardown, load-in access

**Update cadence:** Before the event (build it out). During the event (update cancellations, changes, emergencies in real time).

**Key challenge:** The exhibitor list is often the most-queried file. Give each exhibitor their own section with a clear heading. Include their booth number/location in the heading or first line so the builder LLM can extract it precisely.

---

### The Trade Oracle

An operational oracle for a farm, workshop, makerspace, or small business. Answers questions about procedures, equipment, materials, and schedules.

**Files:**
- `equipment.md` — tools and machines, operating procedures, maintenance, safety
- `materials.md` — supplies, vendors, substitutions, specifications
- `procedures.md` — how to do recurring tasks (planting, brewing, wiring, etc.)
- `calendar.md` — seasonal or recurring task schedule
- `troubleshooting.md` — common problems and fixes
- `contacts.md` — suppliers, service providers, emergency contacts

**Update cadence:** Equipment and procedures — when things change. Calendar — annually.

**Key challenge:** Procedures need to be numbered and action-oriented. "Mix fertilizer at 2 tbsp per gallon of water" beats "fertilizer should be diluted appropriately."

---

### The Lore Oracle

A mystery, art installation, museum, or community memory project. Answers are narrative rather than factual.

**Files:**
- `history.md` — chronological narrative, key events and dates
- `people.md` — individuals, roles, stories, quotes
- `places.md` — locations, descriptions, what happened there
- `artifacts.md` — objects, their origins, provenance, significance
- `oral-histories.md` — transcribed or paraphrased first-person accounts

**Update cadence:** Rarely. This knowledge is stable; the point is depth, not currency.

**Key challenge:** Narrative text requires clear structure for the builder LLM to synthesise well. Use `##` headings generously. Each major story beat or person should have its own named section — the builder will extract these as distinct wiki entities.

---

## 3. Writing Good Source Documents

### What the builder LLM does with your documents

When you run `--build-wiki`, the builder LLM reads each file in `knowledge/` and extracts:

- Named entities (species, people, places, organizations, equipment)
- Facts with measurements, dates, and units
- Relationships between entities (for cross-links)
- A dense summary suitable for keyword search

It writes these into structured wiki pages that the serving model reads at query time. **Your job is to give it clear, complete, factual source material.** The builder handles structure and synthesis — you handle accuracy and coverage.

### Naming your files

The filename stem becomes the wiki page name and the source label in answers:

```
wildlife-guide.md   →   wiki page "wildlife-guide"
                    →   source label in query responses
                    →   visible in !topics
```

Use lowercase, hyphen-separated names. Be specific: `trail-camera-log.md` is more useful than `log.md`. Avoid spaces and underscores.

One file, one topic. The builder creates one wiki page per source file by default. Separate topics produce cleaner wiki pages and better retrieval.

### Document structure

You don't need to optimize heading structure for a text chunker — the LLM reads the whole document. But clear structure still matters because it:

- Helps the builder LLM identify discrete entities to extract
- Makes source documents easier for you to maintain
- Produces better cross-references between wiki pages

A good structure:

```markdown
# [Node Name] — [Topic Name]

[1-2 sentence intro: what this document covers, scope, last updated.]

---

## [Major Category]

### [Specific Entity or Sub-topic]

[Dense facts about this entity. Include names, numbers, dates, units.]
```

Use `##` for major categories and `###` for individual named entities (species, people, locations, equipment). Named entities with their own `###` section are extracted cleanly as distinct wiki entries.

### Reference files vs. activity logs

Keep these separate:

| Reference files | Activity logs |
|---|---|
| Timeless guides — species, procedures, services | Time-stamped events — observations, incidents, status updates |
| Updated rarely | Updated continuously |
| Answer "what is it" and "how does it work" | Answer "what happened" and "what is the current status" |
| `wildlife-guide.md`, `procedures.md` | `trail-camera-log.md`, `community-log.md` |

The builder LLM processes them differently. Reference files become stable wiki pages. Activity logs produce wiki pages that are updated on each `--build-wiki` run, with older entries superseded by newer ones.

### Cross-references between documents

Mention related documents by filename in your source text:

```markdown
For recent camera observations, see trail-camera-log.md.
Winter browse patterns are documented in seasonal-notes.md.
```

The builder LLM will convert these into `[[wikilinks]]` between wiki pages, improving retrieval coverage for queries that span multiple topics.

---

## 4. What Not to Do

### Vague language

The builder LLM extracts facts verbatim. Vague source material produces vague wiki pages, which produce vague answers. Be specific.

| Weak | Strong |
|---|---|
| "Bears are active in fall" | "Bears enter hyperphagia in late September, gaining 3-4 lbs/day before denning in November." |
| "Trail may flood" | "Lower Greenway trail (Sycamore to Maple Grove access) floods when creek exceeds 4.5 ft. Occurs 2-3 times/year, typically January–March." |
| "Contact the city for outages" | "Report outages: (555) 329-8800 (24/7) or cityworks.gov/report" |

Use names (species, locations, people, organizations), numbers (measurements, dates, frequencies, quantities), and specific descriptors.

### Stale data without dates

A document that says "road closed" or "flood stage" without a date will mislead users indefinitely. Every piece of time-sensitive data needs a timestamp. The builder LLM will extract the date and include it in the wiki page's `last_ingested` metadata — staleness warnings at query time depend on this.

```markdown
<!-- Bad -->
Lower trail is closed.

<!-- Good -->
As of Apr 22, 2026: Lower trail closed (Oak to Sycamore section).
Expected reopening: April 25 when creek drops below 4.0 ft.
```

### One enormous file covering everything

A 20,000-character document that mixes species, weather, infrastructure, and events produces one overloaded wiki page that is poor at every topic. Split by subject so the builder produces focused wiki pages.

**Instead:** `wildlife-guide.md` + `flora-guide.md` instead of `nature-reference.md`. Each source file should answer one kind of question.

### All bullets, no prose

Bullet lists are harder for the builder LLM to synthesise into coherent wiki text than prose paragraphs with clear subjects. A list of 20 bullets becomes an undifferentiated wiki section.

**Instead:** Use `###` headings for distinct entities, then write a paragraph per entity. The builder can extract each one cleanly.

### Editing wiki pages by hand

The `wiki/` directory is LLM-owned. If you edit a wiki page directly, your changes will be overwritten the next time `--build-wiki` processes the corresponding source file.

**Instead:** Edit the source document in `knowledge/` and re-run `--build-wiki`. The builder will update the wiki page and log the change in `wiki/log.md`.

### Skipping `--build-wiki` after updating sources

The wiki is not automatically rebuilt when you update `knowledge/` files (unless `wiki_rebuild_on_start: true` is set in config). If you update a source and forget to rebuild, the oracle is answering from stale wiki content.

Run `--lint-wiki` after updates — it will flag any wiki pages whose source files have been modified since last ingest.

---

## 5. Building and Maintaining Your Wiki

### First-time setup

```bash
# 1. Add your source documents to knowledge/
# 2. Build the wiki (use a capable model — 7B+ recommended for --build-wiki)
python main.py --build-wiki

# 3. Check wiki health
python main.py --lint-wiki

# 4. Start the daemon
python main.py
```

For `--build-wiki`, set `wiki_builder_model` in config to a larger model even if the serving model is smaller:

```yaml
model: "gemma4:4b"           # used for query answering
wiki_builder_model: "gemma4:12b"  # used only for --build-wiki
```

### Updating source documents

When you update a file in `knowledge/`:

1. Edit the source file
2. Run `python main.py --build-wiki` — the builder detects changes by MD5 hash and only rebuilds affected wiki pages
3. Run `python main.py --lint-wiki` to verify the wiki is healthy

You can rebuild a single file: `python main.py --build-wiki --file knowledge/weather-station.md`

### Understanding the wiki build log

Every build run appends to `wiki/log.md`. This is your audit trail:

```markdown
## [2026-04-22] build | weather-station.md
Model: gemma4:12b. Pages touched: weather-station (updated). 1 contradiction resolved.

## [2026-04-22] lint
Orphan pages: none. Stale: 0. Missing cross-refs: 1 (flora-guide → trail-camera-log).
```

If a build run produces unexpected results, check this log first.

### Lint exit codes

`--lint-wiki` exits with:
- `0` — no issues
- `1` — warnings (orphan pages, missing cross-refs)
- `2` — errors (stale pages beyond threshold, index drift)

Add `python main.py --lint-wiki` to your deployment checklist. CI/CD can gate on the exit code.

### Time-sensitive files

Declare source files that contain time-sensitive data in config:

```yaml
time_sensitive_files:
  - weather-station.md
  - community-log.md
  - trail-camera-log.md
```

When these files' wiki pages are included in a query response, the system prepends a freshness header:

```
[community-log — last ingested 3h ago]
```

If the page is stale (older than `wiki_stale_after_days`), the header warns users:

```
[STALE: community-log — last ingested 45 days ago, run --build-wiki]
```

### When to rebuild vs. when to restart

| Action | Command |
|---|---|
| Added/edited source documents | `--build-wiki`, then restart daemon |
| Wiki looks correct, just starting service | `python main.py` |
| Checking wiki health without changing anything | `--lint-wiki` only |
| Source files changed, want wiki rebuilt at startup | Set `wiki_rebuild_on_start: true` in config |

---

## 6. Evaluating Your Oracle

### Step 1: Check the wiki built correctly

After `--build-wiki`, check `wiki/index.md`. It should have one row per source file. If a file is missing:

- Check the filename ends in `.md` or `.txt`
- Check the knowledge folder path in your config
- Check `wiki/log.md` for build errors

```bash
python main.py --lint-wiki
# Should exit 0 with no orphan pages or stale entries
```

### Step 2: Smoke test with `!topics`

Send `!topics` to your node. It lists the wiki pages available. Each source file you added should correspond to a wiki page here.

### Step 3: Coverage test

For each topic in `!topics`, send one representative question. A good answer:

1. Cites the right source (shown in the response)
2. Contains specific facts — numbers, names, dates — not vague generalisations
3. Does not claim uncertainty when the information exists in your source docs

If the oracle says "I don't know" for something that's in your documents:

- Did you run `--build-wiki` after adding or editing the file?
- Run `--lint-wiki` to check if the wiki page was actually created
- Try rephrasing the query to use vocabulary from the source document
- Check `similarity_threshold` — lower values (e.g., 0.20) are more permissive

### Step 4: Hallucination test

Ask about something you know is *not* in your documents. The oracle should say:

> "I don't have docs on that. Try !topics to see what I know."

If it fabricates an answer, the system prompt may have been modified. Del-Fi's default prompt instructs the LLM to answer *only* from the provided context.

### Step 5: Precision test

Ask a specific entity query: "Where is [named place]?" or "What are [organization]'s hours?" If the answer covers the wrong entity or topic:

- The entity may need its own `###` section in the source document so the builder extracts it as a distinct wiki entry
- The source file may cover too many topics — consider splitting it

### Tuning `similarity_threshold`

Controls how closely a wiki page must match the query to be included in context. Lower = more permissive.

| Symptom | Adjustment |
|---|---|
| Oracle answers unrelated questions with wrong info | Raise threshold (stricter) |
| Oracle says "I don't know" for things in your wiki | Lower threshold (more permissive) |

Default is 0.28. Adjust in small steps (0.05 at a time).

### When to split a source file

Split when:
- A single file covers multiple unrelated topics
- You have a mix of reference material and activity logs
- `--lint-wiki` reports that a wiki page is too broad to be useful

### When to rebuild the wiki

- After any edit to `knowledge/`
- After adding new source files
- When `--lint-wiki` reports stale pages (older than `wiki_stale_after_days`)

---

## 7. Templates

These are templates for source documents in `knowledge/`. Copy, fill in the brackets, and delete the comments. The builder LLM will compile them into wiki pages.

Note: you don't need to write wiki-formatted output. Write clear, factual prose and let `--build-wiki` handle the wiki structure.

---

### Template 1: Area Overview

```markdown
# [Node Name] — Area Overview

<!-- 1-2 sentences: what is this place, where is it, what is the node's purpose -->
[Node Name] is located at [location description]. This oracle serves [community/purpose].

---

## Location & Terrain

<!-- Specific geography: elevation, boundaries, notable landmarks, coordinates if relevant -->
[Terrain description with measurements and named features]

---

## Node Infrastructure

**Hardware:** [Radio model, compute hardware]
**Power:** [Battery/solar/grid, runtime estimate]
**Coverage:** [Approximate radio range, known dead spots]

---

## Access

### Primary Access
[Route description with distance, surface, seasonal conditions]

### Alternative Access
[Backup route or conditions when primary is unavailable]

**Seasonal closures:** [Dates/conditions when access is restricted]

---

## Neighboring Nodes

<!-- Other Del-Fi nodes within range. Include their node IDs if peering is configured -->
- **[NODE-NAME]** ([node_id if peered]): [Topics they cover, distance, signal quality]

---

## General Notes

[Practical tips: best times to query, known hazards, local context that helps interpret answers.]
```

---

### Template 2: Field Guide (Species / Entities)

```markdown
# [Node Name] — [Topic] Guide

<!-- What this document covers: scope, geographic area, methodology, last updated -->
[Topic] documented at [location]. Data current through [Month Year].

---

## [Category 1]

### [Entity Name] ([alternate name or Latin name if relevant])

**Status:** [Presence / frequency / operational status]

[2-4 sentences: key facts about this entity. Include specific numbers, dates,
behaviors, locations, measurements. Be as concrete as possible — the builder LLM
will extract these facts verbatim into the wiki.]

- **[Fact category]:** [Specific value with units]
- **[Fact category]:** [Specific value]

---

### [Next Entity]

...

---

## [Category 2]

...
```

---

### Template 3: Activity / Event Log

```markdown
# [Node Name] — [Log Name]

<!-- What is being logged, who logs it, how often updated. Include a brief summary
     of the period covered so the builder can extract a useful wiki summary. -->
[Activity/event] log for [location]. Updated [frequency]. Covers [date range].

---

## [Most Recent Date]

**[Time, if relevant] · [Location]**
[Description. Include counts, measurements, named individuals, specific outcomes. 2-3 sentences.]

**[Time] · [Location]**
[Description]

---

## [Previous Date]

...

---

## Summary

<!-- Running totals or aggregate facts the builder can extract as a wiki summary -->
Total events this month: [N]. Notable: [key highlights]. As of [date].
```

---

### Template 4: Resource Directory

```markdown
# [Node Name] — [Resource Category] Directory

<!-- What types of resources are listed, geographic scope, last comprehensive review -->
Local [category] resources within [distance] of [node location]. Last reviewed: [Month Year].

---

## [Category: e.g., Emergency & Safety]

### [Resource Name]

**Address:** [Street address] ([distance and direction from node])
**Phone:** [Number]
**Hours:** [Hours or "24/7"]
**Services:** [What they provide — be specific, include what they do NOT provide]
**Notes:** [Access, parking, language services, any caveats]
**Last verified:** [Month Year]

---

### [Next Resource]

...
```

---

### Template 5: Infrastructure & Status

```markdown
# [Node Name] — [Infrastructure Topic]

<!-- What infrastructure, what monitoring source, what "normal" looks like -->
[Description] serving [area]. Monitored by [source/party]. As of [date].

---

## Current Status

*As of [date/time]:* [Normal / Elevated / Alert]
[1-2 sentences on what users need to know right now.]

---

## Thresholds & What They Mean

| Level | Condition | What it means |
|---|---|---|
| Normal | [Measurement range] | [User-facing meaning] |
| Elevated | [Measurement range] | [What users should know or do] |
| Alert | [Measurement range] | [Clear action users should take] |

---

## Recent History

| Date | Reading | Status | Notes |
|---|---|---|---|
| [Date] | [Value] | [Status] | [Brief note] |

---

## Reporting & Contacts

**Report issues:** [Phone or URL]
**Emergency:** [Number]
**Responsible party:** [Name/org]
```

---

### Template 6: Seasonal Notes

```markdown
# [Node Name] — Seasonal Notes

<!-- What location, climate zone, what the document covers, last reviewed -->
Seasonal patterns at [location]. Covers [topics]. Last reviewed [Month Year].

---

## Quick Reference

| Event | Typical Timing |
|---|---|
| [Key event] | [Month or date range] |
| [Hazard window] | [Month or date range] |
| [Service change] | [Month or date range] |

---

## Winter ([Months])

### Conditions
[Temperature range with specific numbers, precipitation type, typical extremes.]

### [Topic: e.g., Key Hazards]
**Hazard:** [Specific trigger conditions and mitigation.]

### [Topic: e.g., Community Rhythms]
[What changes in this season. Named events, dates, who's involved.]

---

## Spring ([Months])

...

---

## Summer ([Months])

...

---

## Fall ([Months])

...
```

---

## Summer ([Months])

...

---

## Fall ([Months])

...
```

---

## Quick Reference Checklist

Before deploying your knowledge base, check:

**Source documents (`knowledge/`)**
- [ ] Each file covers one topic — not a mix of subjects
- [ ] Filename is lowercase and hyphenated (`wildlife-guide.md`, not `WildlifeGuide.md`)
- [ ] H1 title includes node name and topic
- [ ] H2/H3 headings used for named entities and categories (not just bold text)
- [ ] Every measurement includes units (ft, °F, miles, lbs)
- [ ] Every location is named, not just described ("north meadow" not "up the hill")
- [ ] Time-sensitive data has a date stamp ("as of [date]" or "Last verified: [month]")
- [ ] Contact entries have phone number, address, and hours
- [ ] Reference material is in a separate file from activity logs
- [ ] Cross-references to related files mentioned by filename (helps builder create wikilinks)

**Build and deploy**
- [ ] `python main.py --build-wiki` run after all source files are ready
- [ ] `python main.py --lint-wiki` exits with code 0 (or only expected warnings)
- [ ] `wiki/index.md` has one row per source file
- [ ] `wiki_builder_model` in config set to a capable model (7B+ recommended)
- [ ] Time-sensitive files listed under `time_sensitive_files` in config
- [ ] `wiki_stale_after_days` set to a sensible threshold for your deployment cadence
