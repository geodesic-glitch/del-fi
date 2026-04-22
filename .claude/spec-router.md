# Del-Fi — Router Specification

<!-- Parent: .claude/claude.md §7, §8 -->
<!-- Related: spec-knowledge.md (Tier 1), spec-memory.md (board/facts), spec-formatter.md -->

---

## 1. Message Classification

The dispatcher classifies every incoming message before routing.

### 1.1 Classification rules (in order)

```
1. Empty / whitespace-only       → discard, no reply
2. Command: starts with "!"      → command handler (inline, dispatcher thread)
3. Gossip: matches pattern       → mesh_knowledge.receive(), no reply
4. Query: everything else        → query_worker (thread, via queue)
```

### 1.2 Gossip pattern

```python
GOSSIP_PATTERN = re.compile(
    r"^DEL-FI:\d+:ANNOUNCE:[A-Z0-9\-]+:.*$"
)
```

Example: `DEL-FI:1:ANNOUNCE:RIDGE-ORACLE:topics=wildlife,weather:model=gemma3:4b:uptime=3d`

Gossip messages are forwarded to `GossipDirectory.receive()` with the sender ID.
No reply is sent to the mesh.

### 1.3 Classification is stateless

The classifier does not maintain per-sender state. Conversation context is
managed by `ConversationMemory`. The classifier cannot be tricked into treating
a query as a command by injecting `!` after a preamble — only leading `!` triggers
command dispatch.

---

## 2. Command Dispatch

Commands run **inline in the dispatcher thread**. They must return quickly.
Do not perform LLM calls or disk I/O that could block for > 100ms from a command handler.
`!retry` is an exception — it re-queues to the worker thread.

### 2.1 COMMAND_REGISTRY

```python
COMMAND_REGISTRY: dict[str, Callable] = {
    "help":    self._cmd_help,
    "topics":  self._cmd_topics,
    "status":  self._cmd_status,
    "board":   self._cmd_board,
    "post":    self._cmd_post,
    "unpost":  self._cmd_unpost,
    "more":    self._cmd_more,
    "retry":   self._cmd_retry,
    "forget":  self._cmd_forget,
    "peers":   self._cmd_peers,
    "data":    self._cmd_data,
    "ping":    self._cmd_ping,
}
```

Dispatch:

```python
def _dispatch_command(self, sender: str, text: str) -> str:
    parts = text[1:].split(maxsplit=1)   # strip leading "!"
    cmd = parts[0].lower()
    args = parts[1] if len(parts) > 1 else ""
    handler = COMMAND_REGISTRY.get(cmd)
    if handler is None:
        return f"Unknown command: !{cmd}. Try !help"
    return handler(sender, args)
```

### 2.2 Command implementations

All commands return a string that is passed through the Formatter before sending.

#### `!help`

```python
def _cmd_help(self, sender: str, args: str) -> str:
    lines = [
        "!help !topics !status !data !ping",
        "!board [query] !post <text> !unpost",
        "!more [N] !retry !forget !peers",
    ]
    return " | ".join(lines)
```

The help text is designed to fit in ≤ 230 bytes as-is.

#### `!topics`

Returns a comma-separated list of wiki page titles from `wiki/index.md`.
Falls back to `knowledge/` filenames if wiki has not been built.

#### `!status`

Returns: `{node_name} | {model} | up {uptime} | {page_count} pages | ollama:{ok/err}`

#### `!board [query]`

If `args` is empty: return the last 3 board posts.
If `args` given: search board posts containing the keywords.

#### `!post <text>`

Delegates to `MessageBoard.post(sender, args)`. Returns confirmation or error.

#### `!unpost`

Delegates to `MessageBoard.unpost(sender)`. Returns confirmation.

#### `!more [N]`

See §4 below.

#### `!retry`

Re-queues the sender's last query to the worker thread, bypassing the response cache.
If the sender has no remembered query, returns: `"No recent query to retry."`

#### `!forget`

Calls `ConversationMemory.forget(sender)`. Returns: `"Conversation cleared."`

#### `!peers`

Returns the gossip directory contents:
`VALLEY-ORACLE: fishing, lake-levels | FARM-ORACLE: livestock, planting`
Truncated to fit 230 bytes.

#### `!data`

Returns `FactStore.snapshot()` — all current sensor readings with age annotations.

#### `!ping`

Returns: `"{node_name} online"`

---

## 3. Response Cache

The response cache stores exact-match query → response pairs. It avoids repeated
LLM inference for identical questions.

### 3.1 Cache key

```python
cache_key = query_text.strip().lower()
```

No fuzzy matching. Only exact-match after normalisation.

### 3.2 Cache storage

In-memory dict + disk persistence (JSON file at `cache/response_cache.json`).
Loaded from disk on startup. Flushed to disk by background thread every
60 seconds and on clean shutdown.

### 3.3 Cache entry format

```python
{
    "query_lower": {
        "response": "Answer text...",
        "timestamp": 1714000000.0,   # Unix timestamp
        "sender": "!a1b2c3d4",       # last sender (informational, for log)
    }
}
```

### 3.4 TTL

Config key: `cache_ttl_seconds` (default: 300).

On cache lookup:

```python
if time.time() - entry["timestamp"] > self._cache_ttl:
    del self._cache[cache_key]
    return None
```

### 3.5 Cache bypass

- `!retry` command: bypasses cache and re-runs the LLM query.
- Cache is populated at the end of every successful query-worker run.
- Commands do not use or populate the response cache.

---

## 4. `!more` Buffer

Stores the last full (untruncated) response per sender so follow-up chunks can
be retrieved.

### 4.1 Data structure

```python
# per sender: {"full_text": str, "chunks": list[str], "timestamp": float}
_more_buffers: dict[str, dict] = {}
```

### 4.2 Lifecycle

1. When `Formatter.chunk(response)` returns > 1 chunks, store them in `_more_buffers[sender]`.
2. Auto-send the first `auto_send_chunks` (config default: 3) chunks.
3. If more chunks remain, append indicator to last auto-sent chunk:
   `" +{N} !more"` where N is remaining chunk count. This must fit within 230 bytes.
4. `!more` without argument → send next unsent chunk (increment internal cursor).
5. `!more N` → re-send chunk N (1-indexed). Handles packet loss on lossy channels.
6. Buffer expires after `more_buffer_ttl_seconds` (default: 600 = 10 minutes).
7. After last chunk is sent, respond: `"[End of response]"`

### 4.3 `!more` with no buffer

If sender has no active buffer (expired or never set):
`"No queued response. Send a query first."`

---

## 5. Query Worker

### 5.1 Architecture

A single background `threading.Thread` reads from `msg_queue: queue.Queue`.
Using a single worker provides:
- Natural FIFO ordering per sender
- No concurrent LLM calls (which would exceed memory budget on small hardware)
- Simple backpressure: queue.Full drops the oldest item with a log warning

```python
msg_queue = queue.Queue(maxsize=20)  # configurable: query_queue_size
```

If queue is full when a new query arrives: discard the oldest item and enqueue
the new one. Log: `"queue full — dropped oldest query from %s"`

### 5.2 Worker loop

```python
def _query_worker(self) -> None:
    while not self._shutdown.is_set():
        try:
            item = self._msg_queue.get(timeout=1.0)
        except queue.Empty:
            continue
        
        sender, query = item
        try:
            response = self._run_tier_hierarchy(sender, query)
            response = self._formatter.format(response)
            chunks = self._formatter.chunk(response)
            self._store_more_buffer(sender, query, chunks)
            for i, chunk in enumerate(chunks[:self._auto_send_chunks]):
                self._adapter.send_dm(sender, chunk)
                if i < len(chunks) - 1:
                    time.sleep(self._chunk_delay_seconds)
        except Exception:
            log.exception("Query worker error for sender %s", sender)
            self._adapter.send_dm(sender, self._config.get("error_message", "Error."))
        finally:
            self._msg_queue.task_done()
```

### 5.3 Shutdown

`self._shutdown` is a `threading.Event`. Set on SIGINT/SIGTERM. The worker exits
cleanly after completing the current in-flight item.

---

## 6. Tier Hierarchy — Full Flow

See `.claude/claude.md §7` for the overview. Router-specific detail:

### 6.1 Tier 0 — FactStore

```python
fact = self._fact_store.lookup(query)
if fact:
    return fact    # no LLM call
```

`lookup()` is a keyword match, not a semantic search. If any keyword from
`fact_query_keywords` config list appears in the normalised query, FactStore
returns the relevant sensor reading. Fast path: no Ollama call, no disk I/O.

### 6.2 Tier 1 — WikiEngine

```python
answer, source = self._wiki_engine.query(
    query,
    peer_context=peer_ctx,          # injected if Tier 2 had partial match
    history=self._memory.get_context(sender),
)
if answer:                          # non-empty, non-fallback response
    self._response_cache[cache_key] = answer
    return answer
```

### 6.3 Tier 2 — PeerCache

```python
peer_answer = self._peer_cache.lookup(query)
if peer_answer:
    return peer_answer   # already contains "[via NODE]" label
```

If no direct match but a partial match exists, pass `peer_ctx` to Tier 1 query
(see §6.2 above).

### 6.4 Tier 3 — GossipDirectory

```python
referral = self._gossip_dir.referral(query)
if referral:
    return referral      # e.g. "Try VALLEY-ORACLE — covers fishing, lake-levels"
```

### 6.5 Fallback

```python
return self._config.get(
    "fallback_message",
    "I don't have docs on that. Try !topics."
)
```

---

## 7. Gossip Announcement Protocol

### 7.1 Announcement format

```
DEL-FI:{version}:ANNOUNCE:{NODE_NAME}:topics={t1},{t2}:model={model}:uptime={Xd}:docs={N}
```

- `version`: protocol integer (currently `1`)
- `NODE_NAME`: `ALL-CAPS-HYPHENATED` node name
- `topics`: comma-separated list of wiki page titles (or knowledge folder names)
- `uptime`: human-readable days
- `docs`: integer count of knowledge files

Announcement is broadcast (not DM) at `gossip_interval_seconds` (default: 14400 = 4h).
Announcements are short: must fit in 230 bytes.

### 7.2 Gossip directory TTL

Received announcements expire after `gossip_ttl_seconds` (default: 86400 = 24h).
Expired entries are pruned on each receive and on each `!peers` query.

### 7.3 Topic matching for referrals

```python
def referral(self, query: str) -> str | None:
    """
    Find a peer node whose topics overlap with query keywords.
    Returns referral string or None.
    """
    query_words = set(query.lower().split()) - STOP_WORDS
    best_node = None
    best_score = 0
    for node, entry in self._directory.items():
        topic_words = set(" ".join(entry["topics"]).lower().split())
        score = len(query_words & topic_words)
        if score > best_score:
            best_score = score
            best_node = node
    if best_node and best_score > 0:
        topics = ", ".join(self._directory[best_node]["topics"][:3])
        return f"Try {best_node} — covers {topics}"
    return None
```

---

## 8. "Seen Senders" First-Contact Tracking

The first time a sender contacts the node, the response appends a welcome footer
(if one is configured). This is tracked in `seen_senders.txt` (one ID per line),
loaded on startup, flushed on clean shutdown.

Config key: `welcome_footer` (default: empty string → no footer appended).

```yaml
welcome_footer: "New here? Try !help"
```

The footer is appended within the 230-byte budget. If the response + footer would
exceed 230 bytes, the footer is omitted (silently).

---

<!-- End of spec-router.md -->
