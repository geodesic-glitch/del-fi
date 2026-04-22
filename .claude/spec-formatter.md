# Del-Fi — Formatter Specification

<!-- Parent: .claude/claude.md §2.1 (230-byte limit) -->
<!-- Related: spec-router.md §4 (!more chunking), spec-mesh.md §2.5 (inter-chunk delay) -->

---

## 1. Purpose and Position in the Pipeline

The Formatter is the **last step** before any text is sent to the radio adapter.
Every outbound message passes through the Formatter. No exceptions.

```
LLM output
    │
    ▼
[Formatter.format(text)]
    │
    ├─► If ≤ 230 bytes → send as single message
    │
    └─► If > 230 bytes → truncate at sentence/clause/word boundary
              │
              ▼
         [Formatter.chunk(full_text)] → list of ≤ 230-byte chunks
              │
              ▼
         Router stores chunks in !more buffer
         Auto-sends first N chunks
```

The Formatter is **pure** — it does not call Ollama, read files, or maintain
state. It is a stateless text processing module.

---

## 2. Byte Limit

```python
DEFAULT_MAX_BYTES = 230
```

Config key: `max_response_bytes` (default: 230). Operators may lower this for
conservative deployments (e.g., 200 for extra headroom). Never set above 230.

All byte counts use UTF-8 encoding. The check is:

```python
len(text.encode("utf-8")) <= max_bytes
```

**UTF-8 safety:** Never truncate inside a multi-byte code point. After slicing
by byte index, always decode with `errors="ignore"` or use the Python codec's
byte boundary logic to find the nearest safe cut point.

---

## 3. Markdown Stripping

Raw LLM output often contains markdown. Strip it before byte counting.

### 3.1 Stripping rules (applied in order)

| Pattern | Replacement | Notes |
|---------|-------------|-------|
| Code fences ` ```...``` ` | content only, no backticks | Multi-line |
| Inline code `` `...` `` | content only | Single-line |
| `**bold**` or `__bold__` | content only | |
| `*italic*` or `_italic_` | content only | Careful: `_` in identifiers |
| `~~strikethrough~~` | content only | |
| ATX headings `## Heading` | `Heading` | Strip `#` and space |
| Unordered list bullets `- item` or `* item` | `item` | Strip `- ` or `* ` |
| Ordered list `1. item` | `item` | Strip `N. ` |
| Blockquotes `> text` | `text` | Strip `> ` |
| Links `[text](url)` | `text` | Keep display text, drop URL |
| Images `![alt](url)` | `alt` | Keep alt text |
| Horizontal rules `---` or `***` | ` ` (space) | |

### 3.2 Whitespace normalisation

After stripping:
- Replace multiple consecutive spaces with a single space.
- Replace multiple consecutive newlines with a single newline.
- Strip leading/trailing whitespace.
- Replace `\n` with ` ` (space) in single-message responses (radio is single-line).
  Chunked responses may preserve newlines as sentence boundaries.

---

## 4. Truncation Algorithm

Truncation is applied when stripping alone is insufficient to fit within `max_bytes`.

### 4.1 Priority chain

Attempt each boundary type in order. Use the first that produces valid output:

```
1. Sentence boundary    — ends with: . ! ?
2. Clause boundary      — ends with: ; — … :
3. Word boundary        — ends before the last space
4. Hard cut             — slice at max_bytes, ensure UTF-8 safety
```

### 4.2 Sentence boundary

```python
SENTENCE_TERMINATORS = frozenset(".!?")

def _truncate_sentence(text: str, max_bytes: int) -> str | None:
    encoded = text.encode("utf-8")
    if len(encoded) <= max_bytes:
        return text
    # Find the last sentence terminator within the byte budget
    candidate = encoded[:max_bytes].decode("utf-8", errors="ignore")
    for i in range(len(candidate) - 1, -1, -1):
        if candidate[i] in SENTENCE_TERMINATORS:
            return candidate[:i+1]
    return None
```

### 4.3 Clause boundary

```python
CLAUSE_BOUNDARIES = frozenset(";:—…")  # em-dash and ellipsis included

def _truncate_clause(text: str, max_bytes: int) -> str | None:
    candidate = text.encode("utf-8")[:max_bytes].decode("utf-8", errors="ignore")
    for i in range(len(candidate) - 1, -1, -1):
        if candidate[i] in CLAUSE_BOUNDARIES:
            return candidate[:i]   # don't include the separator itself
    return None
```

### 4.4 Word boundary

```python
def _truncate_word(text: str, max_bytes: int) -> str:
    candidate = text.encode("utf-8")[:max_bytes].decode("utf-8", errors="ignore")
    last_space = candidate.rfind(" ")
    if last_space > 0:
        return candidate[:last_space]
    return candidate  # single long token — falls through to hard cut
```

### 4.5 Hard cut

UTF-8-safe slice. Used only as last resort:

```python
def _hard_cut(text: str, max_bytes: int) -> str:
    encoded = text.encode("utf-8")[:max_bytes]
    return encoded.decode("utf-8", errors="ignore")
```

### 4.6 Combined `format()` method

```python
def format(self, text: str, suffix: str = "") -> str:
    """
    Strip markdown, normalise whitespace, truncate to fit max_bytes.
    If suffix given (e.g. "// RIDGELINE"), append within byte budget.
    """
    text = self._strip_markdown(text)
    text = self._normalise_whitespace(text)
    
    budget = self._max_bytes - len(suffix.encode("utf-8"))
    if budget < 10:
        suffix = ""   # suffix doesn't fit; drop it
        budget = self._max_bytes
    
    if len(text.encode("utf-8")) <= budget:
        return (text + suffix).strip()
    
    for truncator in (
        self._truncate_sentence,
        self._truncate_clause,
        self._truncate_word,
    ):
        result = truncator(text, budget)
        if result:
            return (result + suffix).strip()
    
    return (self._hard_cut(text, budget) + suffix).strip()
```

---

## 5. Node Suffix

Config key: `append_node_suffix` (default: false).
Config key: `node_suffix_format` (default: `"// {node_name}"`).

If enabled, the suffix is appended to every outbound message within the 230-byte
budget. The Formatter reserves `len(suffix_bytes)` before truncating the body.
If the budget after suffix reservation is < 10 bytes, the suffix is silently dropped.

---

## 6. Chunking

Used when the full (un-truncated) LLM response is longer than 230 bytes and the
operator wants to send all of it via `!more` chunks.

### 6.1 `chunk()` algorithm

```python
def chunk(self, text: str) -> list[str]:
    """
    Split text into a list of ≤ max_bytes UTF-8 chunks.
    Splits prefer sentence boundaries, then word boundaries.
    Each chunk is stripped and validated before inclusion.
    """
    if len(text.encode("utf-8")) <= self._max_bytes:
        return [text]
    
    chunks = []
    remaining = text
    while remaining:
        if len(remaining.encode("utf-8")) <= self._max_bytes:
            chunks.append(remaining.strip())
            break
        # Try sentence boundary first, then word
        cut = (
            self._truncate_sentence(remaining, self._max_bytes) or
            self._truncate_word(remaining, self._max_bytes) or
            self._hard_cut(remaining, self._max_bytes)
        )
        chunks.append(cut.strip())
        remaining = remaining[len(cut):].lstrip()
    
    return [c for c in chunks if c]  # drop empty
```

### 6.2 Chunk count suffix

When auto-sending the first N chunks, the last auto-sent chunk gets a suffix
indicating remaining chunks (added by the Router, not the Formatter):

```python
# added by router._store_more_buffer(), not Formatter
if remaining_chunks > 0:
    indicator = f" +{remaining_chunks} !more"
    # Formatter.format() is called again to ensure indicator fits
```

The Formatter must be called again on the indicator-appended chunk to ensure the
final byte count is ≤ 230.

---

## 7. Edge Cases

### 7.1 Empty input

`format("")` returns `""`. Do not send empty messages to the radio.

### 7.2 Already-truncated input

If the Router truncates before calling `format()` (should not happen — Formatter
is the sole truncation authority), `format()` will still pass through cleanly.

### 7.3 Non-ASCII content

Node names, place names, and quoted source material may contain non-ASCII characters.
The Formatter always measures bytes, never characters. Test cases must include
multi-byte characters (e.g. `°`, `é`, `→`, emoji).

### 7.4 Very long single words / URLs

A token longer than 230 bytes (e.g. a URL) forces the hard cut path. This is
acceptable — URLs should not appear in radio responses (LLM system prompt
instructs against them).

---

## 8. Testing Requirements

| Test | Description |
|------|-------------|
| `test_format_within_limit` | Input ≤ 230 bytes → returned unchanged (after markdown strip) |
| `test_truncate_sentence` | Input truncated at sentence boundary |
| `test_truncate_clause` | No sentence boundary → truncate at `;` or `:` |
| `test_truncate_word` | No clause boundary → truncate at word boundary |
| `test_hard_cut` | Single long token → UTF-8-safe hard cut |
| `test_utf8_safety` | Hard cut does not split a multi-byte code point |
| `test_chunk_count` | Long response produces correct number of chunks |
| `test_chunk_byte_limit` | Every chunk ≤ 230 bytes |
| `test_strip_markdown` | Bold, headers, links, code fences stripped correctly |
| `test_suffix_fits` | Suffix appended when it fits; dropped silently when it doesn't |
| `test_empty_input` | `format("")` returns `""` |

---

<!-- End of spec-formatter.md -->
