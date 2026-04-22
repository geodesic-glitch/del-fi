# Del-Fi — Configuration Specification

<!-- Parent: .claude/claude.md §9 -->
<!-- Related: config.example.yaml, all spec-*.md files (cross-reference config keys) -->

---

## 1. Config File Loading

```python
# config.py load order
1. Load config.yaml from --config path (or default: ./config.yaml)
2. Apply oracle profile (if model substring matches a profile — see §4)
3. Validate required fields
4. Resolve relative paths against config file directory
```

Config is read once at startup. Live reload is not supported.
If config.yaml is missing, the daemon exits with an error (not a default).

---

## 2. Full Key Reference

### 2.1 Core / Identity

| Key | Type | Default | Required | Description |
|-----|------|---------|----------|-------------|
| `node_name` | str | — | **Yes** | Node identifier. `ALL-CAPS-HYPHENATED`. Appears in responses and announcements. |
| `node_description` | str | `""` | No | One-sentence description used in gossip announcements and `!status`. |
| `oracle_type` | str | `"general"` | No | Persona type: `observatory`, `community-hub`, `emergency`, `event`, `trade`, `lore`. Used in system prompt phrasing. |
| `model` | str | — | **Yes** | Ollama model tag for query serving. e.g. `"gemma3:4b-it-qat"` |
| `ollama_host` | str | `"http://localhost:11434"` | No | Ollama API endpoint. |
| `fallback_message` | str | `"I don't have docs on that. Try !topics."` | No | Returned when all knowledge tiers miss. |
| `error_message` | str | `"Error processing query."` | No | Returned on unhandled exception in query worker. |
| `welcome_footer` | str | `""` | No | Appended to first-ever response to a new sender. Empty = disabled. |

### 2.2 Knowledge / Wiki

| Key | Type | Default | Required | Description |
|-----|------|---------|----------|-------------|
| `knowledge_folder` | str | `"./knowledge"` | No | Path to raw source documents. |
| `wiki_folder` | str | `"./wiki"` | No | Path to compiled wiki output. |
| `wiki_builder_model` | str | `""` | No | Ollama model tag for `--build-wiki`. Falls back to `model` if empty. |
| `wiki_rebuild_on_start` | bool | `false` | No | Run `--build-wiki` automatically when daemon starts. Blocking. |
| `wiki_stale_after_days` | int | `30` | No | Days before `--lint-wiki` flags a wiki page as stale. |
| `time_sensitive_files` | list[str] | `[]` | No | Source filenames whose wiki pages get freshness headers in query context. |

### 2.3 Retrieval

| Key | Type | Default | Required | Description |
|-----|------|---------|----------|-------------|
| `similarity_threshold` | float | `0.28` | No | Minimum cosine similarity for ChromaDB results to be used. Range: 0.0–1.0. |
| `rag_top_k` | int | `4` | No | Number of wiki pages to retrieve. |
| `max_context_tokens` | int | `1024` | No | Max tokens of context passed to serving LLM. Oldest pages truncated first. |
| `small_model_prompt` | bool | `false` | No | Use shorter system prompt variant (see spec-knowledge.md §7.3). |
| `reorder_context` | bool | `false` | No | Put most-relevant context page last (improves small-model recall). |
| `vectorstore_path` | str | `"./vectorstore"` | No | ChromaDB persistent directory. |
| `embed_model` | str | `"nomic-embed-text"` | No | Ollama embedding model. |

### 2.4 Mesh / Radio

| Key | Type | Default | Required | Description |
|-----|------|---------|----------|-------------|
| `mesh_type` | str | — | **Yes** | Adapter type. See spec-mesh.md for valid values. |
| `serial_port` | str | `null` | No* | Serial port path. `null` = auto-detect. Required if `mesh_type: meshtastic-serial`. |
| `tcp_host` | str | `null` | No* | TCP host. Required if `mesh_type: meshtastic-tcp`. |
| `tcp_port` | int | `4403` | No | TCP port. |
| `ble_address` | str | `null` | No | BLE device address. `null` = auto-scan. |
| `max_response_bytes` | int | `230` | No | Hard byte limit for outbound messages. Never set above 230. |
| `chunk_delay_seconds` | float | `3.0` | No | Delay between chunks of a multi-chunk response. Minimum: 1.0. |
| `auto_send_chunks` | int | `3` | No | Chunks auto-sent before requiring `!more`. |
| `append_node_suffix` | bool | `false` | No | Append `// NODE_NAME` to all responses. |
| `node_suffix_format` | str | `"// {node_name}"` | No | Suffix template. `{node_name}` is substituted. |

### 2.5 Rate Limiting

| Key | Type | Default | Required | Description |
|-----|------|---------|----------|-------------|
| `rate_limit_seconds` | int | `30` | No | Per-sender query rate limit in seconds. 0 = disabled. Commands always bypass. |
| `query_queue_size` | int | `20` | No | Max items in the query worker queue before dropping oldest. |

### 2.6 Conversation Memory

| Key | Type | Default | Required | Description |
|-----|------|---------|----------|-------------|
| `memory_max_turns` | int | `3` | No | Max (user, assistant) pairs remembered per sender. |
| `memory_ttl` | int | `1800` | No | Seconds before idle conversation expires. |
| `memory_persist_path` | str | `conversation_memory.json` | No | Disk path for memory persistence. |
| `disable_memory` | bool | `false` | No | Disable conversation history entirely. |

### 2.7 Message Board

| Key | Type | Default | Required | Description |
|-----|------|---------|----------|-------------|
| `board_enabled` | bool | `true` | No | Enable/disable board commands. |
| `board_max_posts` | int | `20` | No | Max posts stored (FIFO). |
| `board_rate_limit` | int | `3` | No | Max posts per sender per `board_rate_window`. |
| `board_rate_window` | int | `3600` | No | Rate limit window in seconds. |
| `board_post_max_chars` | int | `200` | No | Max characters per post. |
| `board_persist_path` | str | `board.json` | No | Disk path for board persistence. |
| `board_in_llm_context` | bool | `true` | No | Inject board content into LLM query context (with prompt-sandwich framing). |

### 2.8 FactStore / Sensors

| Key | Type | Default | Required | Description |
|-----|------|---------|----------|-------------|
| `sensor_feed_path` | str | `cache/sensor_feed.json` | No | Path to sensor feed JSON. |
| `fact_query_keywords` | dict | `{}` | No | Maps query keyword → list of fact keys. |
| `fact_poll_interval` | int | `30` | No | Seconds between sensor_feed.json re-reads. |

### 2.9 Peer / Gossip

| Key | Type | Default | Required | Description |
|-----|------|---------|----------|-------------|
| `trusted_peers` | list[str] | `[]` | No | Hardware node IDs of trusted peers. Only these contribute to Tier 2 cache. |
| `gossip_interval_seconds` | int | `14400` | No | How often to broadcast capability announcement. |
| `gossip_ttl_seconds` | int | `86400` | No | TTL for received gossip directory entries. |
| `peer_sync_enabled` | bool | `false` | No | Enable nightly peer Q&A sync. |
| `peer_sync_hour` | int | `2` | No | Local hour (0–23) for peer sync window start. |

### 2.10 Response Cache

| Key | Type | Default | Required | Description |
|-----|------|---------|----------|-------------|
| `cache_ttl_seconds` | int | `300` | No | TTL for cached query responses. |
| `cache_persist_path` | str | `cache/response_cache.json` | No | Disk path for cache persistence. |
| `more_buffer_ttl_seconds` | int | `600` | No | TTL for `!more` chunk buffers. |

### 2.11 Logging

| Key | Type | Default | Required | Description |
|-----|------|---------|----------|-------------|
| `log_level` | str | `"INFO"` | No | Python logging level: `DEBUG`, `INFO`, `WARNING`, `ERROR`. |
| `log_file` | str | `null` | No | Path to log file. `null` = stderr only. |

---

## 3. Validation Rules

The config loader validates on startup and raises `ConfigError` (not a silent
default) for constraint violations.

| Rule | Error |
|------|-------|
| `node_name` empty or missing | `"node_name is required"` |
| `model` empty or missing | `"model is required"` |
| `mesh_type` not in `MESH_ADAPTERS` | `"unknown mesh_type: {value}"` |
| `similarity_threshold` not in `[0.0, 1.0]` | `"similarity_threshold must be 0.0–1.0"` |
| `max_response_bytes` > 230 | `"max_response_bytes cannot exceed 230"` |
| `max_response_bytes` < 50 | `"max_response_bytes must be ≥ 50"` |
| `chunk_delay_seconds` < 1.0 | `"chunk_delay_seconds minimum is 1.0"` |
| `memory_max_turns` < 1 | `"memory_max_turns must be ≥ 1"` |
| `peer_sync_hour` not in `[0, 23]` | `"peer_sync_hour must be 0–23"` |

Unknown config keys generate a `log.warning` but do not cause a startup failure.
This allows forward-compatibility when a new config key is documented before the
code is deployed.

---

## 4. Oracle Profiles

Oracle profiles auto-tune retrieval parameters based on the `model` config value.
They are applied **after** user config is loaded, overriding only the listed keys.
Explicit user config values always win (profiles only provide defaults).

### Profile application logic

```python
def _apply_oracle_profile(config: dict) -> dict:
    model = config.get("model", "").lower()
    for pattern, overrides in ORACLE_PROFILES.items():
        if pattern in model:
            for key, value in overrides.items():
                if key not in USER_SET_KEYS:  # don't override explicit user values
                    config[key] = value
            log.info("Applied oracle profile: %s", pattern)
            break
    return config
```

### Defined profiles

```python
ORACLE_PROFILES = {
    # Sub-2B models: tight context budget, simpler prompt, fewer chunks
    "gemma3:1b": {
        "similarity_threshold": 0.35,
        "rag_top_k": 2,
        "max_context_tokens": 512,
        "small_model_prompt": True,
        "reorder_context": True,
    },
    "llama3.2:1b": {
        "similarity_threshold": 0.35,
        "rag_top_k": 2,
        "max_context_tokens": 512,
        "small_model_prompt": True,
        "reorder_context": True,
    },
    # Mid-range 3–4B models: standard config
    "gemma3:4b": {
        "similarity_threshold": 0.28,
        "rag_top_k": 4,
    },
    "qwen2.5:3b": {
        "similarity_threshold": 0.28,
        "rag_top_k": 4,
    },
    # 7B+ models: no profile; use config values as-is
}
```

Profile matching is substring: `"gemma3:1b"` matches `"gemma3:1b-it-qat"`.
Only the first matching profile is applied. More specific patterns should be
listed before less specific ones.

---

## 5. Path Resolution

All path config values (e.g. `knowledge_folder`, `wiki_folder`, `sensor_feed_path`)
are resolved relative to the **directory containing config.yaml**, not the current
working directory.

```python
config_dir = os.path.dirname(os.path.abspath(config_path))
config["knowledge_folder"] = os.path.join(config_dir, config["knowledge_folder"])
```

This ensures `python main.py --config /etc/del-fi/config.yaml` works correctly
regardless of where the daemon is started from.

---

## 6. config.example.yaml

The canonical portable template committed to git. It must always reflect all
current config keys with sensible defaults and inline comments.

Sections in order:
1. Identity: `node_name`, `node_description`, `oracle_type`
2. LLM: `model`, `ollama_host`, `fallback_message`
3. Knowledge / Wiki: `knowledge_folder`, `wiki_folder`, `wiki_builder_model`, `wiki_rebuild_on_start`, `wiki_stale_after_days`
4. Retrieval: `similarity_threshold`, `rag_top_k`, `max_context_tokens`
5. Mesh: `mesh_type`, `serial_port`, `max_response_bytes`, `chunk_delay_seconds`
6. Rate limiting: `rate_limit_seconds`, `query_queue_size`
7. Memory: `memory_max_turns`, `memory_ttl`
8. Board: `board_enabled`, `board_max_posts`, `board_rate_limit`
9. Sensors: `sensor_feed_path`, `fact_query_keywords`
10. Gossip / peers: `trusted_peers`, `gossip_interval_seconds`
11. Logging: `log_level`, `log_file`

All values in `config.example.yaml` must be valid defaults. Do not include
deployment-specific values (node names, serial ports, peer IDs).

---

<!-- End of spec-config.md -->
