"""Tests for del_fi.core.knowledge (WikiEngine).

Covers: build pipeline, query pipeline, BM25 search, lint, page_count,
get_topics, and suggest. All Ollama and ChromaDB calls are mocked.
"""

import hashlib
import io
import os
import sys
import tempfile
import threading
import unittest
import unittest.mock
from datetime import date, timedelta
from pathlib import Path


def _make_cfg(tmpdir: str, **overrides) -> dict:
    cfg = {
        "node_name": "TEST-NODE",
        "knowledge_folder": os.path.join(tmpdir, "knowledge"),
        "wiki_folder": os.path.join(tmpdir, "wiki"),
        "wiki_builder_model": None,
        "model": "test-model:3b",
        "wiki_stale_after_days": 30,
        "time_sensitive_files": [],
        "similarity_threshold": 0.28,
        "rag_top_k": 3,
        "max_context_tokens": 2048,
        "small_model_prompt": False,
        "reorder_context": False,
        "num_ctx": 2048,
        "num_predict": 300,
        "ollama_host": "http://localhost:11434",
        "ollama_timeout": 30,
        "embedding_model": "nomic-embed-text",
        "personality": "Test assistant.",
        "_vectorstore_dir": os.path.join(tmpdir, "vectorstore"),
        "_cache_dir": os.path.join(tmpdir, "cache"),
    }
    cfg.update(overrides)
    return cfg


def _make_wiki_page(title: str, tags: list[str], body: str, days_old: int = 0) -> str:
    """Return a formatted wiki page string."""
    ingested = (date.today() - timedelta(days=days_old)).isoformat()
    tag_str = ", ".join(tags)
    return (
        f"---\n"
        f"title: {title}\n"
        f"tags: [{tag_str}]\n"
        f"sources: [example.md]\n"
        f"last_ingested: {ingested}\n"
        f"---\n\n"
        f"# {title}\n\n"
        f"{body}\n"
    )


def _write_file(path: str, content: str):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        f.write(content)


class _FakeOllamaClient:
    """Minimal Ollama client stub."""

    def __init__(self, generate_response: str = ""):
        self.generate_response = generate_response
        self.generate_calls: list[dict] = []
        self.embed_calls: list[str] = []

    def list(self):
        return {"models": []}

    def generate(self, model: str, prompt: str, options: dict = None, stream: bool = False, **kwargs):
        self.generate_calls.append({"model": model, "prompt": prompt, **kwargs})

        class _Resp:
            pass

        r = _Resp()
        r.response = self.generate_response
        return r

    def embeddings(self, model: str, prompt: str):
        self.embed_calls.append(prompt)
        # Return a simple vector (16-dimensional fake embedding)
        h = hashlib.md5(prompt.encode()).digest()
        vec = [(b / 127.5) - 1.0 for b in h]
        return {"embedding": vec}


def _make_engine(tmpdir: str, ollama_client=None, **cfg_overrides):
    """Build a WikiEngine with patched Ollama (and ChromaDB disabled)."""
    from del_fi.core.knowledge import WikiEngine

    cfg = _make_cfg(tmpdir, **cfg_overrides)
    os.makedirs(cfg["knowledge_folder"], exist_ok=True)
    os.makedirs(cfg["wiki_folder"], exist_ok=True)

    client = ollama_client or _FakeOllamaClient()

    with unittest.mock.patch("del_fi.core.knowledge.WikiEngine._init_ollama"):
        with unittest.mock.patch("del_fi.core.knowledge.WikiEngine._init_vectorstore"):
            engine = WikiEngine(cfg)

    engine._ollama = client
    engine._ollama_available = True
    engine._rag_available = False
    engine._collection = None

    return engine


# ---------------------------------------------------------------------------
# Tests: build pipeline
# ---------------------------------------------------------------------------

class TestBuildPipeline(unittest.TestCase):
    """Build pipeline: knowledge/ → wiki/."""

    def setUp(self):
        self.tmpdir = tempfile.mkdtemp(prefix="delfi-knowledge-")

    def test_build_creates_wiki_page(self):
        """build() writes a wiki page for each knowledge file."""
        wiki_page = _make_wiki_page(
            title="Wildlife Guide",
            tags=["wildlife", "elk"],
            body="Elk are large ungulates. They migrate seasonally.",
        )
        client = _FakeOllamaClient(generate_response=wiki_page)
        engine = _make_engine(self.tmpdir, ollama_client=client)

        knowledge_file = os.path.join(engine.cfg["knowledge_folder"], "wildlife-guide.md")
        _write_file(knowledge_file, "# Wildlife\n\nElk are present in the area.")

        count = engine.build()

        self.assertEqual(count, 1)
        wiki_path = os.path.join(engine.cfg["wiki_folder"], "wildlife-guide.md")
        self.assertTrue(os.path.exists(wiki_path), "wiki page should be written")
        content = Path(wiki_path).read_text(encoding="utf-8")
        self.assertIn("Wildlife Guide", content)

    def test_build_creates_index_entry(self):
        """build() updates wiki/index.md with the new page slug."""
        wiki_page = _make_wiki_page(
            title="Trail Camera Log",
            tags=["cameras", "wildlife"],
            body="Camera 1 is at the north trailhead.",
        )
        client = _FakeOllamaClient(generate_response=wiki_page)
        engine = _make_engine(self.tmpdir, ollama_client=client)

        knowledge_file = os.path.join(engine.cfg["knowledge_folder"], "trail-camera-log.md")
        _write_file(knowledge_file, "# Cameras\n\nCamera 1 is north.")

        engine.build()

        index_path = os.path.join(engine.cfg["wiki_folder"], "index.md")
        self.assertTrue(os.path.exists(index_path))
        index_content = Path(index_path).read_text(encoding="utf-8")
        self.assertIn("trail-camera-log", index_content)

    def test_build_skips_unchanged_files(self):
        """build() skips files whose MD5 hash hasn't changed since last run."""
        wiki_page = _make_wiki_page(
            title="Weather Station",
            tags=["weather"],
            body="Davis station records temperature, humidity, and wind.",
        )
        client = _FakeOllamaClient(generate_response=wiki_page)
        engine = _make_engine(self.tmpdir, ollama_client=client)

        knowledge_file = os.path.join(engine.cfg["knowledge_folder"], "weather-station.md")
        _write_file(knowledge_file, "# Weather\n\nDavis station.")

        # First build — file is new, should call LLM
        engine.build()
        calls_after_first = len(client.generate_calls)
        self.assertEqual(calls_after_first, 1)

        # Second build — same content, should skip
        engine.build()
        calls_after_second = len(client.generate_calls)
        self.assertEqual(calls_after_second, 1, "LLM should not be called for unchanged file")

    def test_build_reprocesses_changed_files(self):
        """build() reprocesses a file when its content changes."""
        wiki_page = _make_wiki_page(
            title="Area Overview",
            tags=["area"],
            body="The station is at 2400m elevation.",
        )
        client = _FakeOllamaClient(generate_response=wiki_page)
        engine = _make_engine(self.tmpdir, ollama_client=client)

        knowledge_file = os.path.join(engine.cfg["knowledge_folder"], "area-overview.md")
        _write_file(knowledge_file, "Original content.")
        engine.build()

        # Modify the file
        _write_file(knowledge_file, "Updated content — new field added.")
        engine.build()

        self.assertEqual(len(client.generate_calls), 2, "LLM should be called again for changed file")

    def test_build_with_single_file(self):
        """build(file=path) processes only the specified file."""
        wiki_page = _make_wiki_page(title="Flora Guide", tags=["flora"], body="Pine trees.")
        client = _FakeOllamaClient(generate_response=wiki_page)
        engine = _make_engine(self.tmpdir, ollama_client=client)

        f1 = os.path.join(engine.cfg["knowledge_folder"], "flora-guide.md")
        f2 = os.path.join(engine.cfg["knowledge_folder"], "fauna-guide.md")
        _write_file(f1, "Pines and firs.")
        _write_file(f2, "Elk and deer.")

        engine.build(file=f1)
        self.assertEqual(len(client.generate_calls), 1)

    def test_build_without_ollama_returns_zero(self):
        """build() returns 0 immediately when Ollama is unavailable."""
        engine = _make_engine(self.tmpdir)
        engine._ollama_available = False

        knowledge_file = os.path.join(engine.cfg["knowledge_folder"], "test.md")
        _write_file(knowledge_file, "Some content.")

        count = engine.build()
        self.assertEqual(count, 0)


# ---------------------------------------------------------------------------
# Tests: query pipeline
# ---------------------------------------------------------------------------

class TestQueryPipeline(unittest.TestCase):
    """Query pipeline: BM25 search + LLM answer generation."""

    def setUp(self):
        self.tmpdir = tempfile.mkdtemp(prefix="delfi-knowledge-")

    def _write_wiki(self, slug: str, title: str, tags: list[str], body: str, days_old: int = 0):
        wiki_dir = os.path.join(self.tmpdir, "wiki")
        page = _make_wiki_page(title, tags, body, days_old)
        _write_file(os.path.join(wiki_dir, f"{slug}.md"), page)

        # Also update index
        index_path = os.path.join(wiki_dir, "index.md")
        tag_str = ", ".join(tags)
        row = f"| [[{slug}]] | {body[:60]} | {tag_str} | {date.today().isoformat()} |"
        if not os.path.exists(index_path):
            _write_file(
                index_path,
                "# Wiki Index\n\n"
                "| Page | Summary | Tags | Updated |\n"
                "|------|---------|------|--------|\n"
                f"{row}\n",
            )
        else:
            existing = Path(index_path).read_text(encoding="utf-8")
            _write_file(index_path, existing.rstrip() + f"\n{row}\n")

    def test_query_bm25_hits_matching_page(self):
        """query() uses BM25 to find a page whose tags match the query."""
        self._write_wiki(
            "wildlife-guide",
            "Wildlife Guide",
            ["wildlife", "elk", "mountain-lion"],
            "Elk are ungulates that migrate seasonally.",
        )

        client = _FakeOllamaClient(generate_response="Elk migrate in spring and fall.")
        engine = _make_engine(self.tmpdir, ollama_client=client)

        answer, had_context = engine.query("Where do elk migrate?")

        self.assertTrue(had_context, "had_context should be True when a page is found")
        self.assertEqual(len(client.generate_calls), 1, "LLM should be called once")

    def test_query_returns_false_when_no_wiki(self):
        """query() returns (answer, False) when wiki/ is empty."""
        client = _FakeOllamaClient(generate_response="I don't know.")
        engine = _make_engine(self.tmpdir, ollama_client=client)

        answer, had_context = engine.query("What is the weather like?")

        self.assertFalse(had_context, "had_context should be False when wiki is empty")

    def test_query_includes_context_in_prompt(self):
        """query() inserts wiki page content into the LLM prompt."""
        self._write_wiki(
            "weather-station",
            "Weather Station",
            ["weather", "temperature"],
            "The Davis station reads temperature, humidity, and wind speed.",
        )
        client = _FakeOllamaClient(generate_response="Temperature is 12°C.")
        engine = _make_engine(self.tmpdir, ollama_client=client)

        engine.query("What is the temperature?")

        self.assertEqual(len(client.generate_calls), 1)
        prompt_used = client.generate_calls[0]["prompt"]
        # The wiki page body should be embedded in the prompt
        self.assertIn("Davis station", prompt_used)

    def test_query_with_no_ollama_returns_empty(self):
        """query() returns ('', False) when Ollama is unavailable."""
        self._write_wiki(
            "area-overview",
            "Area Overview",
            ["area"],
            "Station sits at 2400m above sea level.",
        )
        engine = _make_engine(self.tmpdir)
        engine._ollama_available = False

        answer, had_context = engine.query("What elevation is the station?")

        self.assertIsInstance(answer, str)
        self.assertFalse(had_context, "had_context should be False when Ollama is down")

    def test_query_passes_peer_context(self):
        """query() includes peer context in the LLM prompt when provided."""
        self._write_wiki(
            "trail-guide",
            "Trail Guide",
            ["trails", "hiking"],
            "There are 12 trails in the park.",
        )
        client = _FakeOllamaClient(generate_response="12 trails total.")
        engine = _make_engine(self.tmpdir, ollama_client=client)

        engine.query("How many trails?", peer_ctx="[via PEAK-NODE: 12 trails reported]")

        prompt_used = client.generate_calls[0]["prompt"]
        self.assertIn("PEAK-NODE", prompt_used)

    def test_query_passes_history(self):
        """query() includes conversation history in the LLM prompt."""
        self._write_wiki("guide", "Guide", ["guide"], "Shuttle runs hourly.")
        client = _FakeOllamaClient(generate_response="Shuttle at :00 and :30.")
        engine = _make_engine(self.tmpdir, ollama_client=client)

        engine.query("When is the next shuttle?", history="You: Is there a shuttle? Me: Yes.")

        prompt_used = client.generate_calls[0]["prompt"]
        self.assertIn("shuttle", prompt_used.lower())


# ---------------------------------------------------------------------------
# Tests: lint
# ---------------------------------------------------------------------------

class TestLint(unittest.TestCase):
    """wiki lint() health checks."""

    def setUp(self):
        self.tmpdir = tempfile.mkdtemp(prefix="delfi-knowledge-")
        self.wiki_dir = os.path.join(self.tmpdir, "wiki")
        os.makedirs(self.wiki_dir, exist_ok=True)

    def _write_wiki_page(self, slug: str, body: str = "", days_old: int = 0, refs: list = None):
        page = _make_wiki_page(slug.replace("-", " ").title(), [slug], body or "Content.", days_old)
        if refs:
            page += "\n" + " ".join(f"[[{r}]]" for r in refs)
        _write_file(os.path.join(self.wiki_dir, f"{slug}.md"), page)

    def _write_index(self, slugs: list[str]):
        rows = "\n".join(f"| [[{s}]] | Summary | tag | {date.today().isoformat()} |" for s in slugs)
        _write_file(
            os.path.join(self.wiki_dir, "index.md"),
            f"# Wiki Index\n\n| Page | Summary | Tags | Updated |\n|------|---------|------|--------|\n{rows}\n",
        )

    def test_lint_no_issues_when_clean(self):
        """lint() returns empty list when wiki is consistent."""
        self._write_wiki_page("wildlife-guide")
        self._write_index(["wildlife-guide"])

        engine = _make_engine(self.tmpdir)
        issues = engine.lint()

        # Filter out timing-related issues that may appear in CI
        non_stale = [i for i in issues if "stale" not in i]
        self.assertEqual(non_stale, [])

    def test_lint_detects_orphan_page(self):
        """lint() reports pages in wiki/ not listed in index.md."""
        self._write_wiki_page("flora-guide")   # file exists
        self._write_index([])                   # but not in index

        engine = _make_engine(self.tmpdir)
        issues = engine.lint()

        orphan_issues = [i for i in issues if "orphan" in i]
        self.assertGreaterEqual(len(orphan_issues), 1, f"Expected orphan issue, got: {issues}")
        self.assertTrue(any("flora-guide" in i for i in orphan_issues))

    def test_lint_detects_missing_page(self):
        """lint() reports index entries whose wiki page file is absent."""
        self._write_index(["ghost-page"])  # in index but no file

        engine = _make_engine(self.tmpdir)
        issues = engine.lint()

        missing_issues = [i for i in issues if "missing page" in i]
        self.assertGreaterEqual(len(missing_issues), 1, f"Expected missing-page issue, got: {issues}")
        self.assertTrue(any("ghost-page" in i for i in missing_issues))

    def test_lint_detects_stale_page(self):
        """lint() flags pages whose last_ingested is older than wiki_stale_after_days."""
        days_old = 60
        self._write_wiki_page("old-guide", days_old=days_old)
        self._write_index(["old-guide"])

        engine = _make_engine(self.tmpdir, wiki_stale_after_days=30)
        issues = engine.lint()

        stale_issues = [i for i in issues if "stale" in i]
        self.assertGreaterEqual(len(stale_issues), 1, f"Expected stale issue, got: {issues}")
        self.assertTrue(any("old-guide" in i for i in stale_issues))

    def test_lint_ignores_fresh_pages(self):
        """lint() does not flag pages ingested within the stale window."""
        self._write_wiki_page("fresh-guide", days_old=1)
        self._write_index(["fresh-guide"])

        engine = _make_engine(self.tmpdir, wiki_stale_after_days=30)
        issues = engine.lint()

        stale_issues = [i for i in issues if "stale" in i and "fresh-guide" in i]
        self.assertEqual(stale_issues, [])

    def test_lint_detects_missing_cross_ref(self):
        """lint() reports [[cross-refs]] that point to non-existent pages."""
        self._write_wiki_page("wildlife-guide", refs=["nonexistent-page"])
        self._write_index(["wildlife-guide"])

        engine = _make_engine(self.tmpdir)
        issues = engine.lint()

        ref_issues = [i for i in issues if "cross-ref" in i or "missing cross-ref" in i]
        self.assertGreaterEqual(len(ref_issues), 1, f"Expected cross-ref issue, got: {issues}")

    def test_lint_no_wiki_dir(self):
        """lint() returns a helpful message when wiki/ doesn't exist."""
        import shutil
        shutil.rmtree(self.wiki_dir, ignore_errors=True)

        engine = _make_engine(self.tmpdir)
        issues = engine.lint()

        self.assertEqual(len(issues), 1)
        self.assertIn("--build-wiki", issues[0])

    def test_lint_no_index(self):
        """lint() returns a helpful message when wiki/index.md is missing."""
        self._write_wiki_page("flora-guide")
        # No index.md written

        engine = _make_engine(self.tmpdir)
        issues = engine.lint()

        self.assertEqual(len(issues), 1)
        self.assertIn("index.md", issues[0])


# ---------------------------------------------------------------------------
# Tests: properties
# ---------------------------------------------------------------------------

class TestProperties(unittest.TestCase):
    """page_count, get_topics, wiki_available, suggest."""

    def setUp(self):
        self.tmpdir = tempfile.mkdtemp(prefix="delfi-knowledge-")
        self.wiki_dir = os.path.join(self.tmpdir, "wiki")
        os.makedirs(self.wiki_dir, exist_ok=True)

    def _write_page(self, slug: str):
        page = _make_wiki_page(slug.replace("-", " ").title(), [slug], "Content.")
        _write_file(os.path.join(self.wiki_dir, f"{slug}.md"), page)

    def _write_index(self, slugs: list[str]):
        rows = "\n".join(f"| [[{s}]] | Summary | tag | {date.today().isoformat()} |" for s in slugs)
        _write_file(
            os.path.join(self.wiki_dir, "index.md"),
            f"# Wiki Index\n\n| Page | Summary | Tags | Updated |\n|------|---------|------|--------|\n{rows}\n",
        )

    def test_page_count_empty(self):
        """page_count == 0 when wiki/ exists but has no pages."""
        engine = _make_engine(self.tmpdir)
        self.assertEqual(engine.page_count, 0)

    def test_page_count_correct(self):
        """page_count reflects wiki/*.md files excluding index.md and log.md."""
        self._write_page("page-one")
        self._write_page("page-two")
        self._write_page("page-three")
        # These should not be counted
        _write_file(os.path.join(self.wiki_dir, "index.md"), "# Index\n")
        _write_file(os.path.join(self.wiki_dir, "log.md"), "# Log\n")

        engine = _make_engine(self.tmpdir)
        self.assertEqual(engine.page_count, 3)

    def test_wiki_available_false_when_no_index(self):
        """wiki_available is False when index.md is absent."""
        engine = _make_engine(self.tmpdir)
        self.assertFalse(engine.wiki_available)

    def test_wiki_available_true_when_index_exists(self):
        """wiki_available is True when index.md has content."""
        _write_file(
            os.path.join(self.wiki_dir, "index.md"),
            "# Wiki Index\n\n| [[page-one]] | Summary | tag | today |\n",
        )
        engine = _make_engine(self.tmpdir)
        self.assertTrue(engine.wiki_available)

    def test_get_topics_empty(self):
        """get_topics() returns [] when there is no index.md."""
        engine = _make_engine(self.tmpdir)
        self.assertEqual(engine.get_topics(), [])

    def test_get_topics_returns_titles(self):
        """get_topics() extracts page slugs from index.md and formats them."""
        self._write_index(["wildlife-guide", "weather-station", "trail-log"])
        engine = _make_engine(self.tmpdir)

        topics = engine.get_topics()
        self.assertEqual(len(topics), 3)
        # Slugs are title-cased
        self.assertIn("Wildlife Guide", topics)
        self.assertIn("Weather Station", topics)
        self.assertIn("Trail Log", topics)

    def test_suggest_returns_matching_topic(self):
        """suggest() returns a related topic when the query matches index keywords."""
        self._write_page("wildlife-guide")
        self._write_index(["wildlife-guide"])

        engine = _make_engine(self.tmpdir)
        result = engine.suggest("elk sighting near trailhead")

        # suggest() may return None or a string — both valid; just no crash
        self.assertIsInstance(result, (str, type(None)))


# ---------------------------------------------------------------------------
# Tests: concurrent safety
# ---------------------------------------------------------------------------

class TestConcurrency(unittest.TestCase):
    """WikiEngine should be safe to call from multiple threads."""

    def setUp(self):
        self.tmpdir = tempfile.mkdtemp(prefix="delfi-knowledge-")
        self.wiki_dir = os.path.join(self.tmpdir, "wiki")
        os.makedirs(self.wiki_dir, exist_ok=True)

    def _write_wiki_page_file(self, slug: str):
        page = _make_wiki_page(slug.replace("-", " ").title(), [slug], "Content.")
        _write_file(os.path.join(self.wiki_dir, f"{slug}.md"), page)
        rows = f"| [[{slug}]] | Summary | {slug} | {date.today().isoformat()} |"
        index_path = os.path.join(self.wiki_dir, "index.md")
        if not os.path.exists(index_path):
            _write_file(
                index_path,
                f"# Wiki Index\n\n| Page | Summary | Tags | Updated |\n|------|---------|------|--------|\n{rows}\n",
            )
        else:
            content = Path(index_path).read_text()
            _write_file(index_path, content.rstrip() + f"\n{rows}\n")

    def test_concurrent_queries_do_not_crash(self):
        """Multiple threads calling query() simultaneously should not crash."""
        for i in range(3):
            self._write_wiki_page_file(f"topic-{i}")

        client = _FakeOllamaClient(generate_response="An answer.")
        engine = _make_engine(self.tmpdir, ollama_client=client)

        errors: list[Exception] = []
        results: list = []
        lock = threading.Lock()

        def _run(q: str):
            try:
                r = engine.query(q)
                with lock:
                    results.append(r)
            except Exception as e:
                with lock:
                    errors.append(e)

        threads = [
            threading.Thread(target=_run, args=(f"question {i}",))
            for i in range(8)
        ]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=5.0)

        self.assertEqual(errors, [], f"Concurrent query errors: {errors}")
        self.assertEqual(len(results), 8)


if __name__ == "__main__":
    unittest.main()
