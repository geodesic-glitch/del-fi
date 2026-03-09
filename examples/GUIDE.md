# Building a Good Oracle: Knowledge Base Guide

A practical guide to writing, organizing, and evaluating the documents that power a Del-Fi oracle.

---

## Contents

1. [How RAG Works (and Why It Matters)](#1-how-rag-works-and-why-it-matters)
2. [Oracle Types](#2-oracle-types)
3. [File Structure That Works with RAG](#3-file-structure-that-works-with-rag)
4. [Writing for Retrieval](#4-writing-for-retrieval)
5. [What Not to Do](#5-what-not-to-do)
6. [Evaluating Your Oracle](#6-evaluating-your-oracle)
7. [Templates](#7-templates)

---

## 1. How RAG Works (and Why It Matters)

Del-Fi uses **Retrieval-Augmented Generation** (RAG): when someone asks a question, the system finds the most relevant passages from your documents and feeds them to the language model as context. The model answers *only* from what it's given. If the right passage isn't retrieved, the answer will be wrong or "I don't know" — even if the information exists somewhere in your files.

The pipeline in three steps:

```
Question → [retrieve relevant chunks] → [generate answer from chunks] → Response
```

**What this means for you:** The quality of answers depends almost entirely on the quality of your documents. A well-written, specific document will produce accurate, confident answers. A vague or poorly structured document produces vague or missing answers.

### How Del-Fi chunks your documents

Del-Fi splits each file into **chunks** of roughly 1,000 characters (~256 tokens). It tries these strategies in order, using the first one that produces more than one chunk:

1. **`###` sub-headings** — each subsection becomes a chunk
2. **`##` headings** — each major section becomes a chunk
3. **Blank-line paragraphs** — each paragraph block becomes a chunk
4. **Sentences** — groups of sentences accumulated to fill the chunk size
5. **Characters** — hard split as a last resort

The text *before* the first heading (the preamble) is automatically prepended to every chunk, giving the model document-level context on every retrieval.

**Implication:** Structure your documents with `##` and `###` headings. Every heading becomes a natural chunk boundary, and entities with their own `###` section are always retrieved as a complete unit.

### The 230-byte output constraint

LoRa radio limits each message to 230 bytes (~190 characters of English text). The model is instructed to answer in 2-3 short sentences, and responses are chunked across multiple messages if needed.

**Implication:** Dense, factual writing is rewarded. A well-written document lets the model give a precise answer in two sentences. A vague document forces the model to hedge with long qualifications.

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

**Key challenge:** The exhibitor list is often the most-queried file. Give each exhibitor their own `###` section so they chunk cleanly. Include their booth number/location in the heading or first line.

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

**Key challenge:** Narrative text chunks less predictably than structured text. Use `##` headings generously to create clear chunk boundaries. Each major story beat or person should have its own section.

---

## 3. File Structure That Works with RAG

### Naming your files

The filename stem becomes the topic name in `!topics` and the source label in answers:

```
wildlife-guide.md   →   "wildlife-guide" in !topics
                    →   "(wildlife-guide.md)" at end of answers
```

Use lowercase, hyphen-separated names. Be specific: `trail-camera-log.md` is more useful than `log.md`. Avoid spaces and underscores.

One file, one topic. Don't put wildlife and flora in the same file — they'll compete for retrieval.

### Heading hierarchy

```markdown
# [Location/Node Name] — [Topic Name]

[1-2 sentence intro defining what this document covers.]

---

## Major Section (chunk boundary)

### Specific Entity or Sub-topic (chunk boundary)

Content here...
```

- **H1 (`#`):** Document title. Combined with the intro paragraph, this becomes the preamble prepended to every chunk.
- **H2 (`##`):** Major categories — Terrain, Infrastructure, Spring, Summer, Emergency, etc.
- **H3 (`###`):** Individual entities — species names, locations, organizations, equipment items.

The chunker splits first on `###`, then on `##`, then on blank lines. A document with good `###` headings will produce clean, entity-level chunks that retrieve precisely.

### The preamble

Everything before the first `##` or `###` heading is the preamble. It gets prepended to every chunk so the model always knows the document context.

Keep it short: document title + 1-2 sentences of scope. Don't put important facts in the preamble alone — they need to be in the relevant section too, so the chunk that's retrieved contains them.

**Good preamble:**
```markdown
# Ridgeline Station — Wildlife Guide

Species observed at and around Ridgeline Station (9,240 ft elevation, subalpine zone).
Camera traps and direct observation. Data current through February 2026.
```

**Too long:** Three paragraphs of history before the first heading means a 300-character preamble prepended to every chunk, eating into the context budget.

### Reference files vs. activity logs

Keep these separate:

| Reference files | Activity logs |
|---|---|
| Timeless guides — species, procedures, services | Time-stamped events — observations, incidents, status updates |
| Updated rarely | Updated continuously |
| Answer "what is it" and "how does it work" | Answer "what happened" and "what is the current status" |
| `wildlife-guide.md`, `procedures.md` | `trail-camera-log.md`, `community-log.md` |

If they're mixed in the same file, a query about "elk behavior" may retrieve a camera log entry instead of the ecology section, or vice versa.

---

## 4. Writing for Retrieval

### Specificity: names, numbers, dates

The retriever finds chunks with the highest semantic similarity to the query. Specific, named content is easier to match than generic language.

| Weak | Strong |
|---|---|
| "Bears are active in fall" | "Bears enter hyperphagia in late September, gaining 3-4 lbs/day before denning in November." |
| "Trail may flood" | "Lower Greenway trail (Sycamore to Maple Grove access) floods when the creek exceeds 4.5 ft. This happens 2-3 times per year, typically January–March." |
| "Elk are present" | "A resident herd of 12-18 elk (cows and calves) uses the north meadow as winter range, visible most mornings from the CAM-1 area." |

Use names (species, locations, people, organizations), numbers (measurements, dates, frequencies, quantities), and specific descriptors (color, size, season, time of day).

### Temporal grounding

For anything that changes with time, anchor your statements:

- **Seasonal:** "January–March," "early June," "mid-winter"
- **Specific dates:** "Feb 14: elk herd (8 cows, 2 calves) at CAM-1, 06:32"
- **Relative:** "within 24 hours of a new snowfall," "first two weeks after germination"
- **Freshness marker:** "Last verified: March 2026" or "as of Feb 19, 2026 · 09:00"

For time-sensitive data (sensor readings, road status, flood levels), always include a timestamp. Without it, a 6-month-old "road closed" note looks current.

### Pair narrative with tables

Narrative prose gives the model context for synthesis. Tables give users quick answers. Use both:

```markdown
### Spring Creek Flood History

Spring Creek floods the lower trail section when levels exceed 4.5 ft. This
typically occurs 2-3 times per year from January through March after significant
rainfall events. Recovery takes 1-3 days after the creek drops below 4.0 ft
and the trail crew clears debris.

| Date | Peak Level | Status |
|---|---|---|
| Feb 19 | 4.3 ft (receding) | Elevated |
| Feb 18 | 5.1 ft (peak ~06:00) | Minor flood |
| Jan 31 | 6.2 ft | Moderate flood |
```

The narrative answers "why does this happen and how often?" The table answers "what's the status right now?"

### Hazard annotation

Mark safety-critical content explicitly so the model prioritizes it:

```markdown
**Hazard:** The chute above Spruce Hollow has avalanched in 3 of the last 10
winters after new loading exceeds 6 inches in 24 hours combined with wind slab
formation on the NE face. Avoid this zone after significant snowfall events.
```

Use `**Hazard:**` or `**Safety:**` as a consistent prefix. The model is instructed to include staleness caveats when data is old — combine this with explicit hazard labels so critical warnings always surface.

### Contact information canonicalization

For resource and directory files, use a consistent format for every entry:

```markdown
### Valley Urgent Care

**Address:** 1420 Oak Street, Suite 12 (0.4 miles north of node)
**Phone:** (555) 847-2200
**Hours:** Mon–Sat 8am–8pm, Sun 9am–5pm
**Services:** Urgent care, X-ray, labs. No trauma/ER services.
**Last verified:** January 2026
```

Consistent format means: a query for "urgent care hours" always retrieves a chunk with the phone number in the same place.

### Cross-topic references

RAG doesn't follow links, but explicit mentions of related files help the model synthesize:

```markdown
For recent observations of these species, see trail-camera-log.md.
Winter browse patterns for elk are documented in seasonal-notes.md.
```

This also helps users know where to look if the auto-retrieved answer is incomplete.

---

## 5. What Not to Do

### All bullets, no prose

Bullet lists look organized but chunk poorly. A bullet list of 20 items becomes one long chunk with no clear structure. The model retrieves all 20 items when you asked about one.

**Instead:** Use `###` headings for each item. Let the chunker do the work.

### Vague language

Generic statements produce generic answers.

- "There are several trails in the area" → "The node is on Ridgeline Trail (7.2 miles, strenuous), which connects to Spruce Hollow Trail (3.4 miles, moderate) at the upper junction."
- "Contact the city for utility outages" → "Report outages: (555) 329-8800 (24/7) or cityworks.gov/report"

### Stale current-status data without a date

A document that says "road closed" or "flood stage" without a date will mislead users indefinitely. Either add a timestamp or remove status data when conditions change.

### One enormous file

A 20,000-character document chunks into ~20 pieces. Queries will often retrieve the wrong piece because 20 chunks on the same broad topic are hard to distinguish by embedding similarity alone.

**Instead:** Split by topic. `wildlife-guide.md` and `flora-guide.md` instead of `nature-reference.md`. Each file should answer one kind of question.

### Tiny files with no context

A file containing only:

```
The water pump is behind the barn. Filter should be changed every 6 months.
```

...has no preamble, no headings, no context. If someone asks "where's the water pump?" this chunk might be retrieved, but the model has no idea what property, what barn, or what the pump is for. Add context:

```markdown
# Sunrise Farm — Infrastructure Reference

This document covers utilities, equipment locations, and maintenance schedules
for Sunrise Farm (280 Orchard Road).

---

## Water System

### Main Pump

Located behind the east barn, accessible via the gravel path from the main gate.
Filter replacement every 6 months (April and October). Parts: Pentair #155289.
```

### Writing for search engines, not humans

Over-repeating keywords to "help" the retriever backfires — it makes chunks harder for the model to read and synthesize. Write clearly for a human reader. The embeddings will handle semantic matching.

---

## 6. Evaluating Your Oracle

### Smoke test: `!topics`

Send `!topics` to your node. The response should list every file you dropped in the knowledge folder, by stem name. If a file is missing:

- Check the filename ends in `.md` or `.txt`
- Check the knowledge folder path in your config
- Check the logs for indexing errors (`journalctl -u delfi -f`)

### Coverage test

For each topic in `!topics`, send one representative question. The answer should:

1. Come from the right file (shown in parentheses at end of response)
2. Be specific and grounded, not vague
3. Not claim uncertainty when the information exists

If a topic returns "I don't have anything in my knowledge base about that" when the information exists, the chunk isn't being retrieved. Check:

- Is the query too different from how the document is written? (paraphrase the query to match document vocabulary)
- Is the content buried deep in a large file? (consider splitting the file)
- Is `similarity_threshold` too strict? (try lowering it in config — it's a distance, so higher = stricter, lower = more permissive)

### Edge case test

Ask about something you know is *not* in your documents. The oracle should respond:

> "I don't have anything in my knowledge base about that. Try !topics to see what I know."

If it makes up an answer instead, check that your system prompt hasn't been modified to allow ungrounded responses. Del-Fi's default prompt instructs the model to never speculate.

### Precision test

Ask a specific entity query: "Where is [named location]?" or "What does [species name] eat?" If the wrong chunk is returned (e.g., a weather entry when you asked about an animal), your file may need splitting, or the entity needs its own `###` section to become a discrete chunk.

### Tuning `similarity_threshold`

This config value is a *distance* (0.0 = perfect match, 1.0 = opposite). The default is 0.35. It controls how similar a chunk must be to the query before it's included in the context.

| Symptom | Adjustment |
|---|---|
| Oracle answers irrelevant questions with wrong info | Raise threshold (e.g., 0.35 → 0.30) |
| Oracle says "I don't know" for things that are in your docs | Lower threshold (e.g., 0.35 → 0.40) |

Rename the threshold cautiously — too low means any query gets some chunk, which leads to hallucinations. Too high means only nearly-exact matches retrieve anything.

### When to split a file

Split a file when:

- A single file covers multiple distinct topics and queries keep returning the wrong section
- The file has grown beyond ~3,000 characters and retrieval is inconsistent
- You have a mix of reference material and activity logs in the same file

### When to merge files

Merge files when:

- You have several tiny files (< 300 characters) on closely related topics
- Answers are fragmenting across multiple files when they should come from one coherent response
- `!topics` shows more topics than you actually want to expose

---

## 7. Templates

Copy these skeletons and fill in the brackets. The comments (`<!-- ... -->`) explain what goes where — delete them when done.

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
**Connectivity:** [Serial/TCP/BLE, port or IP]

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
- **[NODE-NAME]** ([node_id if peered]): [Topics they cover, distance]

---

## General Notes

<!-- Practical tips: best observation spots, times of day, hazards, local customs -->
[Notes]
```

---

### Template 2: Field Guide (Species / Items)

```markdown
# [Node Name] — [Topic] Guide

<!-- What this document covers: taxonomy, scope, geographic area -->
[Topic] documented at [location]. [1 sentence on scope or methodology].

---

## [Category 1]

<!-- Each species/item gets its own ### section — this becomes one chunk -->

### [Species or Item Name] ([Latin name or alternate name if relevant])

**Status:** [Presence: resident / seasonal / occasional / rare] · [Seasonality if applicable]

[2-4 sentences: identification, key characteristics, behavior, ecology, or operational specs.
Be specific: size, color, time of day, habitat preference, food source, etc.]

- **[Detail category]:** [Specifics]
- **[Detail category]:** [Specifics]

---

### [Next Species or Item]

...

---

## [Category 2]

...
```

---

### Template 3: Activity / Event Log

```markdown
# [Node Name] — [Log Name]

<!-- What is being logged, who logs it, how often is it updated -->
[Activity/event] log for [location]. Updated [frequency/by whom].

---

## Recent Activity

<!-- Most recent entries first. Each entry: date, time (if relevant), location, observation -->

**[Location label]** — [Date, time if relevant]
[Description of event/observation. Include counts, measurements, identifiers. 1-3 sentences.]

**[Location label]** — [Date]
[Description]

---

## Summary Statistics

<!-- Running totals, counts, or aggregate data to answer "how many" queries -->
[Summary: total events, species count, incident count, etc. — as of [date]]
```

---

### Template 4: Resource Directory

```markdown
# [Node Name] — [Resource Category] Directory

<!-- What types of resources are listed, geographic scope, last comprehensive review -->
Local resources within [distance] of [node location]. Last reviewed: [Month Year].

---

## [Category: e.g., Emergency & Safety]

### [Resource Name]

**Address:** [Street address] ([distance and direction from node])
**Phone:** [Number]
**Hours:** [Hours or "24/7"]
**Services:** [What they provide — be specific]
**Notes:** [Access restrictions, parking, what to bring, language services, etc.]
**Last verified:** [Month Year]

---

### [Next Resource]

...

---

## [Next Category]

...
```

---

### Template 5: Infrastructure & Status

```markdown
# [Node Name] — [Infrastructure Topic]

<!-- What infrastructure is documented, what "normal" looks like, who monitors it -->
[Infrastructure description] serving [area]. [Monitoring source or responsible party].

---

## Current Status

<!-- This section should be updated as conditions change -->
*As of [date/time]:*

| System | Status | Notes |
|---|---|---|
| [System name] | [Normal / Elevated / Alert / Offline] | [Detail] |

---

## Thresholds & Interpretation

<!-- Define what the status levels mean in plain terms -->

| Level | Condition | Meaning |
|---|---|---|
| Normal | [Measurement range] | [What it means for users] |
| Elevated | [Measurement range] | [What users should know] |
| Alert | [Measurement range] | [What users should do] |

---

## Recent History

| Date | Reading | Status |
|---|---|---|
| [Date] | [Value] | [Status] |

---

## Contacts & Reporting

**Report issues:** [Phone or form URL]
**Emergency:** [Emergency number]
**Responsible party:** [Name/org and contact]
```

---

### Template 6: Seasonal Calendar

```markdown
# [Node Name] — Seasonal Notes

<!-- What location, what kind of seasonal variation, what the document covers -->
Seasonal patterns at [location] ([elevation/climate zone if relevant]).
Covers [weather / wildlife activity / flora / human activity / operations].

---

## Quick Reference Calendar

| Event | Typical Timing |
|---|---|
| [Phenological event] | [Month or date range] |
| [Activity pattern] | [Month or date range] |
| [Hazard window] | [Month or date range] |
| [Service change] | [Month or date range] |

---

## Winter ([Months])

### Conditions
[Temperature range, precipitation type, typical extremes. Include specific numbers.]

### [Topic: e.g., Wildlife Activity]
[What changes in this season. Specific behaviors, species, locations.]

### [Topic: e.g., Hazards]
**Hazard:** [Specific hazard, trigger conditions, mitigation.]

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

## Quick Reference Checklist

Before deploying a knowledge file, check:

- [ ] Filename is lowercase and hyphenated — it becomes a topic label
- [ ] H1 title includes node name and topic
- [ ] Preamble (before first heading) is 1-3 sentences max
- [ ] H2 and H3 headings are used for structure — not just bold text
- [ ] Every measurement includes units (ft, °F, miles, lbs)
- [ ] Every location is named, not just described ("north meadow" not "up the hill")
- [ ] Time-sensitive data has a "Last verified" or "as of [date]" marker
- [ ] Contact entries have phone number, address, and hours
- [ ] Reference material is in a separate file from activity logs
- [ ] File is between ~500 and ~5,000 characters (too small = no context; too large = poor retrieval)
