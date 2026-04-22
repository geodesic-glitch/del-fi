# Del-Fi — Knowledge System Specification

<!-- Parent: .claude/claude.md §4, §6 -->
<!-- Related: spec-config.md §wiki_* keys, spec-router.md §7 Tier 1 -->

---

## 1. Three-Layer Architecture

Based on the Karpathy LLM Wiki pattern. The insight: retrieval quality improves
dramatically when the LLM compiles raw sources into a structured wiki **once**
rather than ingesting raw chunks at query time.

```
knowledge/          ← Human-owned raw sources (gitignored, deployment-specific)
    wildlife-guide.md
    weather-station.md
    trail-camera-log.md
         │
         │  --build-wiki (offline, pre-deployment, wiki_builder_model)
         ▼
wiki/               ← LLM-compiled wiki pages (gitignored, rebuilt per deployment)
    index.md
    log.md
    wildlife-guide.md
    weather-station.md
    trail-camera-log.md
         │
         │  query time (WikiEngine.query, serving model)
         ▼
context string assembled → Ollama generation → response
```

**Invariants:**
- `knowledge/` is read-only to the code — humans edit it, the code only reads.
- `wiki/` is write-only from `--build-wiki` — the daemon reads it; humans do not edit it.
- The schema (how wiki pages are structured) lives in this spec and `claude.md`.

---

## 2. WikiEngine Class Interface

```python
class WikiEngine:
    def __init__(self, config: dict, ollama_client: OllamaClient) -> None:
        """
        config keys used:
          wiki_folder, knowledge_folder, model, wiki_builder_model,
          similarity_threshold, rag_top_k, max_context_tokens,
          small_model_prompt, time_sensitive_files, wiki_stale_after_days
        """

    def build(self, file_path: str | None = None) -> dict:
        """
        Run --build-wiki pipeline.
        If file_path is given, rebuild only that source file's wiki pages.
        Returns summary: {"pages_updated": int, "pages_created": int, "errors": list}
        """

    def query(
        self,
        q: str,
        peer_context: str = "",
        history: str = "",
    ) -> tuple[str, str]:
        """
        Query the compiled wiki.
        Returns (answer, source_label) where source_label may be empty.
        source_label example: "wildlife-guide, weather-station"
        """

    def lint(self) -> list[str]:
        """
        Lint the wiki for: orphan pages, missing cross-refs,
        stale pages, data gaps. Returns list of issue strings.
        """

    def watch(self, interval: int = 60) -> None:
        """
        Background thread. Polls knowledge/ folder for file changes.
        On change: calls self.build(file_path=changed_file).
        Runs until daemon shutdown.
        """

    def _bm25_search(self, query: str, top_k: int = 5) -> list[str]:
        """
        Keyword search over wiki/index.md titles and tags.
        Returns list of wiki page filenames, ranked by match score.
        """

    def _vector_search(self, query: str, top_k: int = 3) -> list[str]:
        """
        ChromaDB cosine similarity search over embedded wiki pages.
        Returns list of wiki page filenames, ranked by similarity.
        """

    def _read_wiki_page(self, filename: str) -> tuple[dict, str]:
        """
        Read a wiki page. Returns (frontmatter_dict, body_text).
        Raises FileNotFoundError if page does not exist in wiki/.
        """

    def _write_wiki_page(self, filename: str, frontmatter: dict, body: str) -> None:
        """Write a wiki page atomically (write to .tmp, then rename)."""

    def _update_index(self, filename: str, frontmatter: dict, body: str) -> None:
        """Update wiki/index.md row for the given page."""

    def _append_log(self, entry: str) -> None:
        """Append an entry to wiki/log.md."""
```

### 2.1 OllamaClient wrapper

The `WikiEngine` should not call `requests` directly. Use a thin wrapper that
the test suite can mock:

```python
class OllamaClient:
    def generate(self, model: str, prompt: str, system: str = "") -> str: ...
    def embed(self, model: str, text: str) -> list[float]: ...
    def is_available(self) -> bool: ...
```

---

## 3. Wiki Page Format

### 3.1 Frontmatter

```yaml
---
title: Wildlife Guide
tags: [wildlife, elk, mountain-lion, coyote, mule-deer, identification]
sources: [wildlife-guide.md]           # raw knowledge files that contributed
last_ingested: 2026-04-22              # date of last successful build from source
---
```

All frontmatter fields are required. `tags` and `sources` must be non-empty lists.

### 3.2 Body

- GFM markdown.
- Top-level heading `# <title>` required.
- Sub-sections with `##` headings for major topics.
- Internal cross-links as `[[wiki-page-name]]` (wiki link syntax, no `.md` extension).
- Factual claims should be dense and specific — the LLM's job is to distil
  source material, not to paraphrase it loosely.
- Data points: include numbers, dates, units, and sources where the raw document
  provides them.

### 3.3 Contradiction annotation

When new source material contradicts an existing claim:

```markdown
Elk calving season: mid-May to mid-June (below 8500 ft).
> [superseded 2026-04-15 by trail-camera-log.md — see camera 3 data]
Elk calving season: late May to late June (revised upward based on 3yr camera data).
```

The old claim is kept for audit trail, marked as superseded.

### 3.4 Staleness notation

For time-sensitive pages, the query pipeline injects a freshness header:

```
[weather-station — last ingested 6h ago]
```

If age > `wiki_stale_after_days` config value, the header reads:
```
[STALE: weather-station — last ingested 45 days ago, run --build-wiki]
```

---

## 4. wiki/index.md Format

```markdown
# Del-Fi Wiki Index

Last updated: 2026-04-22

## Index

| Page | Summary | Tags | Updated |
|------|---------|------|---------|
| [[wildlife-guide]] | Species ID — mountain lion, elk, mule deer, coyote | wildlife, species, id | 2026-04-22 |
| [[weather-station]] | Station readings, thresholds, historical norms (Davis VP2) | weather, temperature, wind, precip | 2026-04-22 |
| [[trail-camera-log]] | Camera trap summary — active cameras, notable captures | wildlife, cameras, evidence | 2026-04-22 |
```

The `Summary` column is the primary BM25 search target alongside `Tags`.
The builder LLM should write summaries as dense keyword phrases, not prose.

---

## 5. wiki/log.md Format

Append-only. One `##` entry per build or lint run.

```markdown
# Del-Fi Wiki Build Log

## [2026-04-22] build | wildlife-guide.md
Model: qwen2.5:7b. Pages touched: wildlife-guide (updated). 2 new cross-refs added.

## [2026-04-22] build | weather-station.md
Model: qwen2.5:7b. Pages touched: weather-station (created), trail-camera-log (updated tags).

## [2026-04-22] lint
Orphan pages: none. Stale pages: 0. Missing cross-refs: 2 (flora-guide→trail-camera-log,
flora-guide→wildlife-guide). Data gaps flagged: 0.
```

---

## 6. Build Pipeline Detail

### 6.1 Entry point: `python main.py --build-wiki`

The build command does **not** start the radio listener. It is a batch job.

### 6.2 Per-file processing

```
for each file in knowledge/ (sorted by mtime, oldest first):
    compute MD5 hash
    if hash matches wiki/.<filename>.hash: skip (unchanged)
    
    prompt = build_prompt(source_content, existing_wiki_pages)
    wiki_pages = wiki_builder_model.generate(prompt)
    
    for each wiki_page in wiki_pages:
        merge_into_wiki(wiki_page)
        update_index(wiki_page)
    
    write wiki/.<filename>.hash
    append_log(entry)
```

### 6.3 Build prompt

The build prompt is constructed as:

```
SYSTEM:
You are a knowledge compiler for a field deployment named {node_name}.
You convert raw field documents into structured wiki pages.

Each wiki page you produce must be in this format:
---
title: <Page Title>
tags: [comma, separated, tags]
sources: [source-filename.md]
last_ingested: {today}
---

# <Page Title>

<body — dense facts, measurements, dates, cross-links as [[page-name]]>

Rules:
- Extract and condense — do not paraphrase loosely.
- Include numbers, dates, and units wherever the source provides them.
- Use [[wikilinks]] for internal cross-references.
- Contradictions with existing pages: keep old text, mark as superseded, add new claim.
- Write tag summaries as keyword phrases, not prose.
- Output ONLY wiki page blocks in the format above. No commentary.

EXISTING WIKI PAGES (for context and cross-reference):
{existing_wiki_index}

SOURCE DOCUMENT TO COMPILE:
Filename: {filename}
---
{source_content}
```

### 6.4 Atomic writes

Wiki page writes are atomic: write to `wiki/<page>.md.tmp`, then rename to
`wiki/<page>.md`. This prevents partial wiki pages if the build is interrupted.

### 6.5 No network during build

The build uses Ollama (local). It does not call any external service.

---

## 7. Query Pipeline Detail

### 7.1 Full sequence

```
1. Normalise query: lowercase, strip punctuation, remove stop words.

2. BM25 keyword search on wiki/index.md:
   - Split into terms.
   - Score each row by term frequency in (Page title + Summary + Tags).
   - Return top_k (default 5) page filenames.

3. Vector search (ChromaDB, optional):
   - Embed query with nomic-embed-text via Ollama.
   - Query ChromaDB for top rag_top_k pages by cosine similarity.
   - If max similarity < similarity_threshold: vector results discarded.

4. Merge results:
   - BM25 results (required, high confidence in intent).
   - Vector results (optional, semantic fallback for paraphrase queries).
   - Deduplicate. Cap total context pages at max 3.

5. Read wiki pages:
   - For each page: read body, prepend staleness header if applicable.

6. Assemble context string:
   - Context is: join(page bodies, separator="\n\n---\n\n").
   - Cap at max_context_tokens if needed (truncate oldest pages first).

7. Generate with serving model (model config key):
   - System prompt includes oracle persona, 230-byte guidance.
   - Context prepended to user query.
   - See §7.2 for small_model_prompt variant.

8. Return (answer, source_label).
```

### 7.2 System prompt (standard)

```
You are {node_name}, a field AI assistant serving {oracle_type} over mesh radio.
Answer questions using ONLY the provided context.
If the context doesn't contain the answer, say exactly: "I don't have data on that."
Keep your response under 230 characters. Be direct. Cite dates and numbers from the context.
```

### 7.3 System prompt (small_model_prompt: true)

Used when oracle profile sets `small_model_prompt: true` (sub-2B models).
Shorter system prompt to preserve token budget:

```
You are {node_name}. Use ONLY the context below. Answer in 1-2 sentences.
If not in context, say "No data." Max 230 characters.
```

### 7.4 Context reordering

When `reorder_context: true` (oracle profile), put the most-relevant page
**last** in the context window. Small models suffer from the lost-in-the-middle
problem; they attend better to the end of context.

---

## 8. ChromaDB Integration

### 8.1 What changes from v0.1

| v0.1 (rag.py) | v0.2 (knowledge.py) |
|---------------|---------------------|
| Embeds raw document chunks (1024-char windows) | Embeds whole wiki pages |
| Many embeddings per source file | One embedding per wiki page |
| Updated on file change | Updated on `--build-wiki` |
| Collection: `del_fi_knowledge` | Collection: `del_fi_wiki` |
| Metadata: `{source, chunk_index, heading}` | Metadata: `{page, tags, last_ingested}` |

### 8.2 Collection parameters

```python
collection = chroma_client.get_or_create_collection(
    name="del_fi_wiki",
    metadata={"hnsw:space": "cosine"},
)
```

One document per wiki page. ID is the wiki page filename (without `.md`).

### 8.3 Embedding model

`nomic-embed-text` via Ollama. No change from v0.1.

### 8.4 ChromaDB failure mode

If ChromaDB is unavailable at startup, WikiEngine logs a warning and continues.
Vector search (`_vector_search`) returns empty list. BM25 search still works.
The daemon does not crash.

---

## 9. Staleness Model

| Config key | Default | Purpose |
|------------|---------|---------|
| `wiki_stale_after_days` | 30 | `--lint-wiki` stale page threshold |
| `time_sensitive_files` | `[]` | Source filenames that get age annotations at query time |

`time_sensitive_files` example:

```yaml
time_sensitive_files:
  - weather-station.md
  - trail-camera-log.md
```

When these source files have produced wiki pages, those pages get the inline
age annotation in the context string (see §3.4).

---

## 10. Lint (`--lint-wiki`)

Runs as: `python main.py --lint-wiki`. Does not start the radio listener.

### Lint checks

| Check | Description |
|-------|-------------|
| Orphan pages | Wiki pages with no incoming `[[wikilinks]]` from any other page |
| Missing cross-refs | A page mentions an entity that matches another page title but has no `[[link]]` to it |
| Stale pages | `last_ingested` older than `wiki_stale_after_days` |
| Missing source | `sources:` frontmatter lists a file not in `knowledge/` |
| Empty tags | `tags:` list is empty |
| Index drift | A wiki page exists but is not in `wiki/index.md` (or vice versa) |

Lint exits with code 0 (no issues), 1 (warnings), 2 (errors).
CI/CD can gate on lint exit code.

### Lint output format

```
WARN  orphan-page: flora-guide (no incoming links)
WARN  missing-cross-ref: trail-camera-log → weather-station (see line 47: "overnight temperatures")
INFO  stale: 0 pages stale (threshold: 30d)
INFO  index: 5 pages, all consistent
LINT RESULT: 2 warnings, 0 errors
```

---

## 11. Migration from rag.py

### What changes

| Concern | v0.1 rag.py | v0.2 knowledge.py |
|---------|-------------|-------------------|
| Ingestion trigger | File change → immediate re-chunk and embed | File change → `build()` → wiki update → re-embed whole page |
| Retrieval unit | 1024-char chunk | Whole wiki page |
| LLM reads | Raw document fragments | Synthesised wiki page |
| Index | ChromaDB only | `wiki/index.md` (BM25) + ChromaDB (vector) |
| Contradiction handling | None (duplicate chunks) | superseded annotation |
| Staleness | None | `last_ingested` frontmatter + lint check |
| Build model | Same as serving model | `wiki_builder_model` (can be larger) |

### What stays the same

- ChromaDB with SQLite backend at `vectorstore/`.
- Embedding model: `nomic-embed-text` via Ollama.
- Ollama generation API endpoint.
- `similarity_threshold`, `rag_top_k` config keys (semantics unchanged; now apply to wiki pages).
- The `query()` interface that `Router` calls.

### Migration path (Phase 2)

1. `WikiEngine` is implemented alongside `RAGEngine` initially.
2. `Router` is updated to call `wiki_engine.query()` instead of `rag_engine.retrieve()`.
3. Old ChromaDB collection (`del_fi_knowledge`) is left on disk but not written to.
4. After a `--build-wiki` run, the new collection (`del_fi_wiki`) is populated.
5. `rag.py` is removed in a follow-up commit once tests pass.

---

## 12. `wiki/` Directory Conventions

- All wiki page filenames: `kebab-case.md` (matches source filename).
- Reserved filenames: `index.md`, `log.md` (generated automatically — do not create manually).
- Hash files: `.<source-filename>.hash` (hidden, used for change detection).
- Temp files: `<page>.md.tmp` (transient, cleaned up on build completion).

---

<!-- End of spec-knowledge.md -->
