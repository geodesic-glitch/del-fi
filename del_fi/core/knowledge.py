"""WikiEngine: LLM-compiled knowledge base for Del-Fi.

Three layers:
  knowledge/   — raw source documents (human-owned, gitignored)
  wiki/        — LLM-compiled pages (rebuilt via --build-wiki, gitignored)
  .claude/     — wiki schema / spec (always tracked in git)

Build pipeline (--build-wiki):
  1. Scan knowledge/ for .md and .txt files
  2. Skip unchanged files (MD5 hash check)
  3. For each changed file: prompt the LLM to extract entities and write
     a structured wiki page with YAML frontmatter
  4. Write wiki/<slug>.md, update wiki/index.md, append to wiki/log.md

Query pipeline (Tier 1):
  1. BM25 keyword search on wiki/index.md titles and tags
  2. Read top 2–3 wiki pages as context
  3. Optional: ChromaDB vector search on wiki-page embeddings
  4. Assemble context string and pass to serving LLM
"""

import hashlib
import json
import logging
import math
import os
import re
import threading
import time
from datetime import date, datetime, timezone
from pathlib import Path

log = logging.getLogger("del_fi.core.knowledge")

# Stop words for BM25 keyword extraction
_STOP_WORDS = frozenset({
    "a", "an", "the", "is", "it", "in", "on", "at", "to", "for",
    "of", "and", "or", "not", "be", "are", "was", "were", "do",
    "does", "did", "has", "have", "had", "can", "could", "will",
    "would", "should", "may", "might", "i", "me", "my", "you",
    "your", "we", "our", "they", "them", "their", "what", "where",
    "when", "how", "who", "which", "that", "this", "there",
    "here", "with", "from", "about", "into", "if", "so", "than",
    "but", "just", "any", "some", "all", "no", "yes",
})

# Compact system prompt for small models
SMALL_MODEL_SYSTEM = (
    "You are {name}, a community assistant. {personality} "
    "You are given a wiki index summary followed by the full source document(s) it indexes. "
    "Answer using ONLY the source content below. "
    "If the source does not directly answer the question, share the most relevant "
    "information from it and note what it covers. Never state facts not in the source. "
    "Be brief. 1-3 sentences maximum."
)

STANDARD_SYSTEM = (
    "You are {name}, a community assistant. {personality} "
    "You are given a wiki index summary followed by the full source document(s) it indexes. "
    "Answer using ONLY the provided source content. "
    "If the source does not directly answer the question, share the closest relevant "
    "information it does contain and briefly note what topic it covers. "
    "Never state facts not in the source. "
    "Be concise and factual. Cite the source document name when relevant."
)

# Short phrases that indicate the LLM refused to answer from context.
# Used to detect useless responses and fall through to suggest().
_IDK_PATTERNS = (
    "i don't know",
    "i do not know",
    "i'm not sure",
    "i am not sure",
    "don't have information",
    "do not have information",
    "not mentioned in the context",
    "not in the context",
    "not provided in the context",
    "context does not contain",
    "context doesn't contain",
    "context doesn't mention",
    "context does not mention",
    "cannot answer",
    "can't answer",
    "no information available",
)

# Build prompt: given raw source content, produce a structured wiki page
WIKI_BUILD_PROMPT = """\
You are a knowledge compiler. Your job is to turn a raw source document into
a SEARCH INDEX page. This wiki page will be used to FIND the source document
at query time — the raw source is always available for full detail. Therefore:

- Maximise keyword and entity coverage. Every name, place, date, organisation,
  measurement, and topic must appear somewhere in the page — even if only
  in a heading or tag — so keyword search can find it.
- Section headings should name the topic precisely (e.g. "Spring Clean-Up Day"
  not "Events").
- Use [[page-slug]] cross-references freely. They are how the system navigates
  between topics at query time.
- Summaries can be brief (1-2 sentences). Full detail lives in the source file.

Format EXACTLY as shown:

---
title: <page title>
tags: [tag1, tag2, tag3, ...]
sources: [{filename}]
last_ingested: {today}
---

# <page title>

## <Precise Section Topic>

<1-2 sentences. Key facts, dates, measurements. [[cross-ref]] any related pages.>

## <Another Precise Section Topic>

...

Rules:
- Be factual. No filler.
- If the source contradicts an existing claim, annotate the old text:
  > [superseded {today} by {filename}]
- Keep the total page under 500 words.
- tags: must include every major searchable keyword from the document.
- Do NOT include meta-commentary, only the wiki content.

Source document ({filename}):
---
{content}
---

Respond with ONLY the wiki page (YAML frontmatter + body). No preamble."""


class WikiEngine:
    """LLM-compiled wiki knowledge base.

    Public interface
    ----------------
    build(file=None)                  compile knowledge/ → wiki/
    query(q, peer_ctx, history)       BM25 + LLM → answer string
    lint()                            health check → list of issue strings
    watch(interval, stop)             background knowledge watcher
    available                         True when Ollama is reachable
    wiki_available                    True when wiki/ has pages
    page_count                        number of wiki pages
    get_topics()                      list of page titles from index
    """

    def __init__(self, cfg: dict):
        self.cfg = cfg
        self._wiki_dir = Path(cfg["wiki_folder"])
        self._knowledge_dir = Path(cfg.get("knowledge_folder", "./knowledge"))
        self._ollama = None
        self._ollama_build = None
        self._ollama_available = False
        self._collection = None
        self._rag_available = False
        self._lock = threading.Lock()
        self._file_hashes: dict[str, str] = {}
        self._hash_cache_file = self._wiki_dir / ".hash_cache.json"

        self._init_ollama()
        self._init_vectorstore()
        self._load_hash_cache()

    # --- Initialization ---

    def _init_ollama(self):
        try:
            from ollama import Client
            self._ollama = Client(
                host=self.cfg["ollama_host"],
                timeout=self.cfg["ollama_timeout"],
            )
            self._ollama.list()
            self._ollama_available = True
            log.info(f"ollama connected at {self.cfg['ollama_host']}")
            # Separate client with a longer timeout for --build-wiki.
            # Large builder models (e.g. 26B) can take several minutes per page.
            build_timeout = self.cfg.get("wiki_build_timeout", 600)
            self._ollama_build = Client(
                host=self.cfg["ollama_host"],
                timeout=build_timeout,
            )
        except Exception as e:
            log.warning(f"ollama not available (will retry): {e}")
            self._ollama_available = False
            self._ollama_build = None

    def _init_vectorstore(self):
        """Initialize ChromaDB for wiki-page semantic search. Optional."""
        try:
            import chromadb
            from chromadb.config import Settings
            db_path = self.cfg["_vectorstore_dir"]
            os.makedirs(db_path, exist_ok=True)
            client = chromadb.PersistentClient(
                path=db_path,
                settings=Settings(anonymized_telemetry=False),
            )
            self._collection = client.get_or_create_collection(
                name="del_fi_wiki",
                metadata={"hnsw:space": "cosine"},
            )
            self._rag_available = True
            log.info(f"vectorstore ready ({self._collection.count()} wiki pages indexed)")
        except Exception as e:
            log.warning(f"chromadb init failed — semantic search disabled: {e}")
            self._rag_available = False

    def check_ollama(self) -> bool:
        """Re-check Ollama availability. Called by health-check thread."""
        if self._ollama_available:
            return True
        self._init_ollama()
        return self._ollama_available

    # --- Properties ---

    @property
    def available(self) -> bool:
        return self._ollama_available

    @property
    def rag_available(self) -> bool:
        return self._rag_available

    @property
    def wiki_available(self) -> bool:
        index = self._wiki_dir / "index.md"
        return index.exists() and index.stat().st_size > 0

    @property
    def page_count(self) -> int:
        if not self._wiki_dir.exists():
            return 0
        return sum(
            1 for f in self._wiki_dir.glob("*.md")
            if f.name not in ("index.md", "log.md")
        )

    # --- Build pipeline ---

    def build(self, file: str | None = None) -> int:
        """Compile knowledge/ → wiki/.

        If *file* is given, only (re)process that file.
        Returns the number of wiki pages written.
        """
        self._wiki_dir.mkdir(parents=True, exist_ok=True)

        if not self._ollama_available:
            log.error("ollama not available — cannot build wiki")
            return 0

        builder_model = self.cfg.get("wiki_builder_model") or self.cfg["model"]
        log.info(f"building wiki with model {builder_model!r}")

        if file:
            targets = [Path(file)]
        else:
            targets = list(self._knowledge_dir.glob("*.md")) + list(
                self._knowledge_dir.glob("*.txt")
            )

        written = 0
        for path in targets:
            try:
                if self._build_page(path, builder_model):
                    written += 1
            except Exception as e:
                log.error(f"build failed for {path.name}: {e}")

        self._save_hash_cache()

        if written:
            log.info(f"wiki build complete: {written} page(s) written")
            # Re-embed updated pages
            self._embed_wiki_pages()

        return written

    # Build token budget: enough for a complete wiki page (≤600 words ≈ 800 tokens)
    # plus frontmatter. We use 1600 to give headroom; the larger builder model
    # handles this without issue. A second pass is attempted if truncation is detected.
    _BUILD_NUM_PREDICT = 1600
    _BUILD_NUM_PREDICT_RETRY = 2400  # wider budget for retry pass

    def _is_truncated(self, text: str) -> bool:
        """Return True when the LLM output appears to have been cut mid-generation."""
        if not text:
            return True
        stripped = text.rstrip()
        # Sentence ends with a terminal punctuation mark, a closing code fence,
        # or a markdown list item end. Mid-word / mid-sentence cuts do not.
        terminal = (".", "!", "?", "```", ">", "*", "-")
        for t in terminal:
            if stripped.endswith(t):
                return False
        # Also accept lines that end with a closing bracket/paren (cross-refs)
        if stripped.endswith((")", "]", "]]")):
            return False
        return True

    def _generate_wiki_page(
        self, filename: str, prompt: str, model: str, max_retries: int = 2
    ) -> str | None:
        """Generate a wiki page with truncation detection and retry.

        Returns the completed wiki text, or None on unrecoverable failure.
        """
        budgets = [self._BUILD_NUM_PREDICT] + [self._BUILD_NUM_PREDICT_RETRY] * max_retries

        client = self._ollama_build or self._ollama
        for attempt, budget in enumerate(budgets):
            try:
                response = client.generate(
                    model=model,
                    prompt=prompt,
                    options={"num_predict": budget, "temperature": 0.1},
                )
                text = response.response.strip()
            except Exception as e:
                log.error(f"LLM build failed for {filename} (attempt {attempt + 1}): {e}")
                return None

            if not self._is_truncated(text):
                if attempt > 0:
                    log.info(f"  {filename}: truncation resolved on attempt {attempt + 1}")
                return text

            log.warning(
                f"  {filename}: output appears truncated at {len(text)} chars "
                f"(attempt {attempt + 1}/{len(budgets)}) — retrying with budget {budget} → "
                f"{budgets[attempt + 1] if attempt + 1 < len(budgets) else 'N/A'}"
            )

        # Final attempt exhausted — log and return what we have rather than failing
        log.warning(
            f"  {filename}: could not resolve truncation after {max_retries + 1} attempts; "
            f"saving best result ({len(text)} chars)"
        )
        return text

    def _build_page(self, source_path: Path, model: str) -> bool:
        """Build a single wiki page from a source file. Returns True if written."""
        try:
            content = source_path.read_text(encoding="utf-8", errors="replace")
        except Exception as e:
            log.warning(f"cannot read {source_path.name}: {e}")
            return False

        content_hash = hashlib.md5(content.encode()).hexdigest()
        key = str(source_path)

        with self._lock:
            if self._file_hashes.get(key) == content_hash:
                return False  # unchanged
            self._file_hashes[key] = content_hash

        today = date.today().isoformat()
        prompt = WIKI_BUILD_PROMPT.format(
            filename=source_path.name,
            today=today,
            content=content[:12000],  # cap to ~3k tokens for build context
        )

        log.info(f"  compiling {source_path.name} → wiki...")
        wiki_text = self._generate_wiki_page(source_path.name, prompt, model)
        if wiki_text is None:
            with self._lock:
                del self._file_hashes[key]  # allow retry next time
            return False

        # Derive wiki page slug from source filename
        slug = re.sub(r"[^\w-]", "-", source_path.stem.lower()).strip("-")
        wiki_path = self._wiki_dir / f"{slug}.md"

        # Atomic write
        tmp = wiki_path.with_suffix(".tmp")
        tmp.write_text(wiki_text, encoding="utf-8")
        tmp.replace(wiki_path)

        self._update_index(slug, wiki_path, source_path.name, today)
        self._append_log(f"[{today}] ingest | {source_path.name}\nWrote: {slug}.md")
        log.info(f"  wrote wiki/{slug}.md")
        return True

    def _update_index(
        self, slug: str, wiki_path: Path, source_name: str, today: str
    ):
        """Update the wiki/index.md table entry for this page."""
        index_path = self._wiki_dir / "index.md"

        # Parse frontmatter to extract title, tags, summary
        text = wiki_path.read_text(encoding="utf-8", errors="replace")
        title = slug
        tags = ""
        summary = ""

        fm_match = re.match(r"^---\n(.*?)\n---", text, re.DOTALL)
        if fm_match:
            fm = fm_match.group(1)
            t = re.search(r"^title:\s*(.+)$", fm, re.MULTILINE)
            if t:
                title = t.group(1).strip().strip('"')
            tg = re.search(r"^tags:\s*\[(.+)\]", fm, re.MULTILINE)
            if tg:
                tags = tg.group(1).strip()

        # Extract first sentence of body for summary (single-line, safe for table)
        body = text[fm_match.end():].strip() if fm_match else text
        s = re.search(r"[A-Z][^.!?\n]{10,}[.!?]", body)
        if s:
            summary = s.group(0).replace("\n", " ").replace("|", "-")[:100]

        row = f"| [[{slug}]] | {summary} | {tags} | {today} |"

        if not index_path.exists():
            index_path.write_text(
                "# Wiki Index\n\n"
                "| Page | Summary | Tags | Updated |\n"
                "|------|---------|------|--------|\n"
                f"{row}\n",
                encoding="utf-8",
            )
            return

        content = index_path.read_text(encoding="utf-8")
        # Replace existing row for this slug or append
        pattern = re.compile(rf"^\| \[\[{re.escape(slug)}\]\].*$", re.MULTILINE)
        if pattern.search(content):
            content = pattern.sub(row, content)
        else:
            content = content.rstrip() + f"\n{row}\n"

        tmp = index_path.with_suffix(".tmp")
        tmp.write_text(content, encoding="utf-8")
        tmp.replace(index_path)

    def _append_log(self, entry: str):
        """Append an entry to wiki/log.md."""
        log_path = self._wiki_dir / "log.md"
        with open(log_path, "a", encoding="utf-8") as f:
            f.write(f"\n## {entry}\n")

    # --- Query pipeline ---

    def query(
        self,
        q: str,
        peer_ctx: str = "",
        history: str = "",
        board_context: str = "",
    ) -> tuple[str, bool]:
        """Answer a query from the compiled wiki.

        Returns (answer, had_context). If had_context is False the caller
        should not cache the result and should consider falling through to
        Tier 2.
        """
        if not self._ollama_available:
            return "", False

        # Step 1: BM25 keyword search on index.md
        page_slugs = self._bm25_search(q)

        # Step 2: Optional semantic search fallback
        if not page_slugs and self._rag_available:
            page_slugs = self._vector_search(q)

        # Step 3 (fallback): search wiki page bodies directly when index misses
        if not page_slugs:
            page_slugs = self._content_search(q)

        if not page_slugs:
            return "", False

        # Step 3: Read top pages, then follow sources: links into knowledge/
        context_parts = []
        for slug in page_slugs[:3]:
            page_path = self._wiki_dir / f"{slug}.md"
            if not page_path.exists():
                continue
            wiki_text = page_path.read_text(encoding="utf-8", errors="replace")

            # Annotate time-sensitive pages with staleness
            ts_files = self.cfg.get("time_sensitive_files", [])
            age_header = ""
            if any(f.replace(".md", "") in slug for f in ts_files):
                age_note = self._staleness_note(page_path)
                if age_note:
                    age_header = f"[{slug} — {age_note}]\n"

            # Follow sources: links → read raw knowledge files for full detail
            src_files = self._extract_sources(wiki_text)
            source_texts: list[str] = []
            for src_file in src_files:
                src_path = self._knowledge_dir / src_file
                if src_path.exists():
                    try:
                        raw = src_path.read_text(encoding="utf-8", errors="replace")
                        source_texts.append(f"[Source: {src_file}]\n{raw}")
                    except Exception as exc:
                        log.warning(f"could not read source {src_file}: {exc}")

            if source_texts:
                # Source files are the primary context; wiki index is the header
                section = (
                    age_header
                    + f"[Wiki index: {slug}]\n{wiki_text}\n\n"
                    + "\n\n".join(source_texts)
                )
            else:
                # No source file available — fall back to wiki page alone
                section = age_header + wiki_text

            context_parts.append(section)

        if not context_parts:
            return "", False

        context = "\n\n---\n\n".join(context_parts)

        # Step 4: Trim context to token budget
        max_tokens = self.cfg.get("max_context_tokens")
        if max_tokens:
            context = context[: max_tokens * 4]  # ~4 chars/token

        if self.cfg.get("reorder_context") and context_parts:
            # Small-model heuristic: move highest-ranked context to end
            if len(context_parts) > 1:
                context_parts = context_parts[1:] + [context_parts[0]]
                context = "\n\n---\n\n".join(context_parts)

        # Step 5: Assemble and generate
        answer = self._generate(q, context, peer_ctx=peer_ctx, history=history,
                                board_context=board_context)
        # _generate returns "" on IDK or exception — treat as no match so the
        # router falls through to suggest() rather than caching a dead response.
        if not answer:
            return "", False
        return answer, True

    def _generate(
        self,
        query: str,
        context: str,
        peer_ctx: str = "",
        history: str = "",
        board_context: str = "",
    ) -> str:
        """Call Ollama to generate an answer from context."""
        name = self.cfg["node_name"]
        personality = self.cfg.get("personality", "")

        if self.cfg.get("small_model_prompt"):
            system = SMALL_MODEL_SYSTEM.format(name=name, personality=personality)
        else:
            system = STANDARD_SYSTEM.format(name=name, personality=personality)

        parts = [f"Context:\n{context}"]
        if peer_ctx:
            parts.append(f"Peer data:\n{peer_ctx}")
        if board_context:
            parts.append(board_context)
        if history:
            parts.append(history)
        parts.append(f"Question: {query}")

        prompt = "\n\n".join(parts)

        options: dict = {"num_predict": self.cfg.get("num_predict", 300)}
        num_ctx = self.cfg.get("num_ctx")
        if num_ctx:
            options["num_ctx"] = num_ctx

        try:
            response = self._ollama.generate(
                model=self.cfg["model"],
                system=system,
                prompt=prompt,
                options=options,
            )
            text = response.response.strip()
        except Exception as e:
            log.error(f"LLM generation failed: {e}")
            return ""

        # If the LLM refused to answer from context, return "" so the caller
        # falls through to suggest() rather than caching a useless response.
        if self._is_idk_response(text):
            log.info("LLM returned IDK response — falling through to suggest()")
            return ""
        return text

    def _is_idk_response(self, text: str) -> bool:
        """Return True when the LLM response is a bare refusal."""
        if not text or len(text) > 180:
            return not text  # long responses are probably useful
        lower = text.lower()
        return any(p in lower for p in _IDK_PATTERNS)

    def suggest(self, query: str) -> str:
        """Return a soft suggestion when no wiki page matches well."""
        topics = self.get_topics()
        if not topics:
            return ""
        name = self.cfg["node_name"]
        topic_str = ", ".join(topics[:8])
        return (
            f"{name}: I don't have specific info on that. "
            f"I know about: {topic_str}. Try !topics for full list."
        )

    # --- BM25 search ---

    def _bm25_search(self, query: str) -> list[str]:
        """BM25 keyword search on wiki/index.md. Returns ranked slug list."""
        index_path = self._wiki_dir / "index.md"
        if not index_path.exists():
            return []

        content = index_path.read_text(encoding="utf-8", errors="replace")
        query_terms = _tokenize(query)
        if not query_terms:
            return []

        # Parse index rows: | [[slug]] | summary | tags | date |
        rows = re.findall(
            r"^\|\s*\[\[([^\]]+)\]\]\s*\|([^|]*)\|([^|]*)\|[^|]*\|",
            content,
            re.MULTILINE,
        )

        if not rows:
            return []

        # Build corpus: one document per row (slug + summary + tags)
        corpus: list[tuple[str, list[str]]] = []
        for slug, summary, tags in rows:
            doc_text = f"{slug} {summary} {tags}"
            corpus.append((slug.strip(), _tokenize(doc_text)))

        scores = _bm25_scores(query_terms, corpus)
        ranked = sorted(zip(scores, [slug for slug, _ in corpus]), reverse=True)

        threshold = 0.0
        return [slug for score, slug in ranked if score > threshold]

    def _extract_sources(self, wiki_text: str) -> list[str]:
        """Parse the sources: [...] list from a wiki page's YAML frontmatter."""
        fm_match = re.match(r"^---\n(.*?)\n---", wiki_text, re.DOTALL)
        if not fm_match:
            return []
        src_m = re.search(r"^sources:\s*\[(.+)\]", fm_match.group(1), re.MULTILINE)
        if not src_m:
            return []
        return [
            s.strip().strip("\"'")
            for s in src_m.group(1).split(",")
            if s.strip()
        ]

    def _content_search(self, query: str) -> list[str]:
        """Last-resort fallback: score wiki page bodies by raw term frequency.

        Used when BM25 index search and vector search both return nothing.
        Returns pages ranked by how many query terms appear in the full body.
        """
        query_terms = _tokenize(query)
        if not query_terms:
            return []

        scored: list[tuple[int, str]] = []
        for page_path in self._wiki_dir.glob("*.md"):
            if page_path.name in ("index.md", "log.md"):
                continue
            try:
                body = page_path.read_text(encoding="utf-8", errors="replace").lower()
                score = sum(body.count(term) for term in query_terms)
                if score > 0:
                    scored.append((score, page_path.stem))
            except Exception:
                continue

        scored.sort(reverse=True)
        return [slug for _, slug in scored]

    # --- Vector search ---

    def _vector_search(self, query: str) -> list[str]:
        """Semantic search on ChromaDB wiki-page embeddings."""
        if not self._rag_available or not self._ollama_available:
            return []

        try:
            embedding = self._embed_text(query)
            if not embedding:
                return []

            top_k = self.cfg.get("rag_top_k", 4)
            results = self._collection.query(
                query_embeddings=[embedding],
                n_results=min(top_k, max(1, self._collection.count())),
                include=["metadatas", "distances"],
            )

            threshold = self.cfg.get("similarity_threshold", 0.28)
            slugs = []
            for meta, dist in zip(
                results["metadatas"][0], results["distances"][0]
            ):
                similarity = 1.0 - dist
                if similarity >= threshold:
                    slugs.append(meta["slug"])

            return slugs

        except Exception as e:
            log.warning(f"vector search failed: {e}")
            return []

    def _embed_text(self, text: str) -> list[float] | None:
        try:
            resp = self._ollama.embeddings(
                model=self.cfg["embedding_model"],
                prompt=text,
            )
            return resp.embedding
        except Exception as e:
            log.warning(f"embedding failed: {e}")
            return None

    def _embed_wiki_pages(self):
        """Embed all wiki pages into ChromaDB for semantic search."""
        if not self._rag_available or not self._ollama_available:
            return

        pages = [
            f for f in self._wiki_dir.glob("*.md")
            if f.name not in ("index.md", "log.md")
        ]
        if not pages:
            return

        log.info(f"embedding {len(pages)} wiki page(s)...")
        for page_path in pages:
            try:
                text = page_path.read_text(encoding="utf-8", errors="replace")
                slug = page_path.stem
                emb = self._embed_text(text[:4000])  # cap embedding input
                if not emb:
                    continue
                self._collection.upsert(
                    ids=[slug],
                    embeddings=[emb],
                    documents=[text[:2000]],
                    metadatas=[{"slug": slug, "file": page_path.name}],
                )
            except Exception as e:
                log.warning(f"embedding failed for {page_path.name}: {e}")

        log.info("wiki embedding complete")

    # --- Lint ---

    # Valid wiki page slug: lowercase letters, digits, hyphens only.
    # Inline mentions like [[Apr 22, 2026]], [[Birdhouse Coffee]], [[cityelectric.gov/outage]]
    # are intentionally excluded — they are narrative references, not page links.
    _SLUG_PAT = re.compile(r"^[a-z0-9][a-z0-9-]*$")

    @staticmethod
    def _is_page_slug(ref: str) -> bool:
        return bool(WikiEngine._SLUG_PAT.match(ref))

    @staticmethod
    def _normalise_ref(ref: str) -> str:
        """Strip .md suffix so [[area-overview.md]] resolves like [[area-overview]]."""
        return ref[:-3] if ref.endswith(".md") else ref

    def lint(self) -> list[str]:
        """Check wiki health. Returns list of issue strings."""
        issues: list[str] = []

        if not self._wiki_dir.exists():
            return ["wiki/ directory does not exist — run --build-wiki"]

        index_path = self._wiki_dir / "index.md"
        if not index_path.exists():
            return ["wiki/index.md missing — run --build-wiki"]

        index_content = index_path.read_text(encoding="utf-8", errors="replace")

        # Extract page slugs only from the first column of index table rows.
        # Pattern: start-of-line | optional-spaces [[slug]]
        #
        # Using re.findall(r"\[\[...\]\]", full_text) is unreliable because a
        # malformed unclosed [[ref in a summary column causes [^\]]+ to greedily
        # swallow newlines and consume the [[slug]] on the following row.
        # Anchoring to "^ | [[slug]]" makes the extraction independent of what
        # appears in later columns.
        indexed_slugs: set[str] = set(
            re.findall(
                r"^\|\s*\[\[([a-z0-9][a-z0-9-]*)\]\]",
                index_content,
                re.MULTILINE,
            )
        )

        pages = {
            f.stem
            for f in self._wiki_dir.glob("*.md")
            if f.name not in ("index.md", "log.md")
        }

        # Orphan pages: in wiki/ but not in index.md
        orphans = pages - indexed_slugs
        for slug in sorted(orphans):
            issues.append(f"orphan page: {slug}.md (not in index)")

        # Missing pages: in index.md but not in wiki/
        missing = indexed_slugs - pages
        for slug in sorted(missing):
            issues.append(f"missing page: [[{slug}]] is in index but file not found")

        # Stale pages
        stale_after = self.cfg.get("wiki_stale_after_days", 30)
        for slug in sorted(pages):
            page_path = self._wiki_dir / f"{slug}.md"
            text = page_path.read_text(encoding="utf-8", errors="replace")
            m = re.search(r"^last_ingested:\s*(.+)$", text, re.MULTILINE)
            if m:
                try:
                    ingested = date.fromisoformat(m.group(1).strip())
                    age_days = (date.today() - ingested).days
                    if age_days > stale_after:
                        issues.append(
                            f"stale page: {slug}.md "
                            f"(last ingested {age_days}d ago)"
                        )
                except ValueError:
                    pass

        # Missing cross-refs: [[slug]] in a page body that looks like a page link
        # but has no corresponding wiki file.  Skip inline mentions (dates, proper
        # nouns, URLs) — only check refs that are valid kebab-case page slugs.
        for slug in sorted(pages):
            page_path = self._wiki_dir / f"{slug}.md"
            text = page_path.read_text(encoding="utf-8", errors="replace")
            refs = set(re.findall(r"\[\[([^\]]+)\]\]", text))
            for ref in sorted(refs):
                norm = self._normalise_ref(ref)
                if not self._is_page_slug(norm):
                    continue   # inline mention — not a page link
                if norm not in pages:
                    issues.append(f"missing cross-ref: [[{ref}]] in {slug}.md")

        today = date.today().isoformat()
        self._append_log(
            f"[{today}] lint\n"
            f"Issues: {len(issues)} total. "
            f"{sum(1 for i in issues if 'orphan' in i)} orphan, "
            f"{sum(1 for i in issues if 'stale' in i)} stale, "
            f"{sum(1 for i in issues if 'cross-ref' in i)} missing cross-refs."
        )

        return issues

    # --- Watch ---

    def watch(self, interval: int, stop: threading.Event):
        """Background watcher: re-build when knowledge/ files change."""
        def _watcher():
            while not stop.is_set():
                try:
                    changed = self._detect_changes()
                    if changed:
                        log.info(f"knowledge change detected ({len(changed)} file(s))")
                        for f in changed:
                            self.build(file=f)
                except Exception as e:
                    log.error(f"wiki watcher error: {e}")
                stop.wait(interval)

        threading.Thread(target=_watcher, daemon=True).start()
        log.info(f"wiki watcher started (poll every {interval}s)")

    def _detect_changes(self) -> list[str]:
        """Return list of knowledge file paths that have changed since last build."""
        changed = []
        for ext in ("*.md", "*.txt"):
            for path in self._knowledge_dir.glob(ext):
                try:
                    content = path.read_text(encoding="utf-8", errors="replace")
                    h = hashlib.md5(content.encode()).hexdigest()
                    with self._lock:
                        if self._file_hashes.get(str(path)) != h:
                            changed.append(str(path))
                except Exception:
                    pass
        return changed

    # --- Topics ---

    def get_topics(self) -> list[str]:
        """Return list of wiki page titles from index.md."""
        index_path = self._wiki_dir / "index.md"
        if not index_path.exists():
            return []
        content = index_path.read_text(encoding="utf-8", errors="replace")
        # Extract slugs; convert to readable titles
        slugs = re.findall(r"\[\[([^\]]+)\]\]", content)
        return [s.replace("-", " ").title() for s in slugs]

    # --- Staleness annotation ---

    def _staleness_note(self, page_path: Path) -> str:
        """Return a staleness annotation for time-sensitive pages."""
        try:
            text = page_path.read_text(encoding="utf-8", errors="replace")
            m = re.search(r"^last_ingested:\s*(.+)$", text, re.MULTILINE)
            if m:
                ingested = datetime.fromisoformat(m.group(1).strip())
                now = datetime.now()
                delta = now - ingested
                hours = int(delta.total_seconds() / 3600)
                if hours < 1:
                    return "last updated < 1 hr ago"
                if hours < 24:
                    return f"last updated {hours} hrs ago"
                return f"last updated {delta.days}d ago"
        except Exception:
            pass
        return ""

    # --- Hash cache persistence ---

    def _load_hash_cache(self):
        try:
            if self._hash_cache_file.exists():
                with open(self._hash_cache_file) as f:
                    with self._lock:
                        self._file_hashes = json.load(f)
        except Exception:
            pass

    def _save_hash_cache(self):
        try:
            self._wiki_dir.mkdir(parents=True, exist_ok=True)
            tmp = self._hash_cache_file.with_suffix(".tmp")
            with self._lock:
                data = dict(self._file_hashes)
            with open(tmp, "w") as f:
                json.dump(data, f)
            tmp.replace(self._hash_cache_file)
        except Exception as e:
            log.warning(f"could not save hash cache: {e}")


# --- BM25 helpers ---

def _tokenize(text: str) -> list[str]:
    """Lowercase, remove punctuation, filter stop words."""
    words = re.sub(r"[^\w\s]", " ", text.lower()).split()
    return [w for w in words if w not in _STOP_WORDS and len(w) > 1]


def _bm25_scores(
    query_terms: list[str],
    corpus: list[tuple[str, list[str]]],
    k1: float = 1.5,
    b: float = 0.75,
) -> list[float]:
    """Compute BM25 scores for query_terms against each document in corpus."""
    n = len(corpus)
    if n == 0:
        return []

    avg_dl = sum(len(doc) for _, doc in corpus) / n
    scores = []

    for _, doc_tokens in corpus:
        dl = len(doc_tokens)
        score = 0.0
        for term in query_terms:
            tf = doc_tokens.count(term)
            if tf == 0:
                continue
            df = sum(1 for _, d in corpus if term in d)
            idf = math.log((n - df + 0.5) / (df + 0.5) + 1)
            tf_norm = (tf * (k1 + 1)) / (tf + k1 * (1 - b + b * dl / avg_dl))
            score += idf * tf_norm
        scores.append(score)

    return scores
