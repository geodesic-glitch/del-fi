"""Response formatting for LoRa transmission.

Pure functions. Takes raw LLM output, strips markdown formatting,
and prepares it for the 230-byte LoRa message constraint.
"""

import re

# --- Markdown stripping patterns ---
_BOLD = re.compile(r"\*\*(.+?)\*\*")
_ITALIC = re.compile(r"(?<!\*)\*(?!\*)(.+?)(?<!\*)\*(?!\*)")
_INLINE_CODE = re.compile(r"`([^`]+)`")
_HEADERS = re.compile(r"^#{1,6}\s+", re.MULTILINE)
_LINKS = re.compile(r"\[([^\]]+)\]\([^)]+\)")
_UNORDERED_LIST = re.compile(r"^[\s]*[-*+]\s+", re.MULTILINE)
_ORDERED_LIST = re.compile(r"^[\s]*\d+\.\s+", re.MULTILINE)
_BLOCKQUOTE = re.compile(r"^>\s?", re.MULTILINE)
_HORIZONTAL_RULE = re.compile(r"^[-*_]{3,}\s*$", re.MULTILINE)
_CODE_BLOCK = re.compile(r"```[\s\S]*?```")
_MULTI_SPACE = re.compile(r"[ \t]+")
_MULTI_NEWLINE = re.compile(r"\n{2,}")
# Sentence-ending punctuation.  For narrative/RPG text we also treat
# em-dashes (—), ellipses (… and ...), semicolons, and colons as
# acceptable break points so truncation doesn't land mid-clause.
_SENTENCE_END = re.compile(r"[.!?](?:\s|$)")
_CLAUSE_END = re.compile(r"[.!?;:\u2014\u2026](?:\s|$)|\.\.\. ")

MORE_TAG = " [!more]"
MORE_TAG_BYTES = len(MORE_TAG.encode("utf-8"))


def byte_len(text: str) -> int:
    """UTF-8 byte length of a string."""
    return len(text.encode("utf-8"))


def strip_markdown(text: str) -> str:
    """Remove markdown formatting, preserve plain text content."""
    text = _CODE_BLOCK.sub("", text)
    text = _BOLD.sub(r"\1", text)
    text = _ITALIC.sub(r"\1", text)
    text = _INLINE_CODE.sub(r"\1", text)
    text = _HEADERS.sub("", text)
    text = _LINKS.sub(r"\1", text)
    text = _BLOCKQUOTE.sub("", text)
    text = _HORIZONTAL_RULE.sub("", text)
    text = _UNORDERED_LIST.sub("", text)
    text = _ORDERED_LIST.sub("", text)
    return text


def collapse_whitespace(text: str) -> str:
    """Normalize whitespace to single spaces, trim lines."""
    text = _MULTI_NEWLINE.sub(" ", text)
    text = _MULTI_SPACE.sub(" ", text)
    return text.strip()


def clean_text(text: str) -> str:
    """Full cleaning pipeline: strip markdown + collapse whitespace."""
    return collapse_whitespace(strip_markdown(text))


def truncate_at_sentence(text: str, max_bytes: int) -> str:
    """Truncate text at the last sentence boundary within max_bytes.

    Falls back to clause boundary, then word boundary, then hard cut.
    The clause fallback prevents mid-sentence cuts in narrative text
    that uses em-dashes, semicolons, or ellipses.
    """
    if byte_len(text) <= max_bytes:
        return text

    # Decode the truncated byte slice back to valid characters
    truncated = text.encode("utf-8")[:max_bytes].decode("utf-8", errors="ignore")

    # Search for last sentence-ending punctuation
    best = -1
    for m in _SENTENCE_END.finditer(truncated):
        best = m.start() + 1  # include the punctuation mark

    if best > 0:
        return truncated[:best].strip()

    # Fall back to clause boundary (semicolon, em-dash, ellipsis, colon)
    best_clause = -1
    for m in _CLAUSE_END.finditer(truncated):
        best_clause = m.start() + 1
    if best_clause > 0:
        return truncated[:best_clause].strip()

    # Fall back to word boundary
    last_space = truncated.rfind(" ")
    if last_space > 0:
        return truncated[:last_space].strip()

    # Hard cut (unlikely for natural text)
    return truncated.strip()


def chunk_text(text: str, max_bytes: int) -> list[str]:
    """Split text into chunks that each fit within max_bytes.

    Splits on sentence boundaries where possible. Each chunk
    should be independently readable.
    """
    if byte_len(text) <= max_bytes:
        return [text]

    chunks = []
    remaining = text

    while remaining:
        if byte_len(remaining) <= max_bytes:
            chunks.append(remaining)
            break

        chunk = truncate_at_sentence(remaining, max_bytes)
        if not chunk:
            # Force progress: hard cut at byte boundary
            forced = remaining.encode("utf-8")[:max_bytes].decode("utf-8", errors="ignore").strip()
            chunks.append(forced)
            remaining = remaining[len(forced):].strip()
            continue

        chunks.append(chunk)
        remaining = remaining[len(chunk):].strip()

    return chunks


def format_response(
    text: str,
    max_bytes: int = 230,
    provenance: str | None = None,
) -> tuple[str, list[str], bool]:
    """Format LLM output for LoRa transmission.

    Args:
        text: Raw LLM output
        max_bytes: Maximum bytes per LoRa message
        provenance: Peer node name for attribution (e.g. "MARINA-ORACLE")

    Returns:
        (first_message, all_chunks, is_truncated)
        first_message: ready to send immediately (includes [!more] if truncated)
        all_chunks: full list of all chunks for !more buffer tracking
        is_truncated: whether response was split into multiple chunks
    """
    text = clean_text(text)

    if not text:
        text = "I couldn't generate a response. Try again."

    if provenance:
        text = f"[via {provenance}] {text}"

    # Fits in one message?
    if byte_len(text) <= max_bytes:
        return text, [text], False

    # Need to chunk. First message gets [!more] tag.
    budget = max_bytes - MORE_TAG_BYTES

    # Split full text into sendable chunks
    chunks = chunk_text(text, max_bytes)

    # Rebuild first chunk to fit [!more] indicator
    first = chunks[0]
    if byte_len(first) > budget:
        first = truncate_at_sentence(first, budget)
        # Re-split the remainder so chunk list stays accurate
        leftover = text[len(first):].strip()
        chunks = [first] + chunk_text(leftover, max_bytes)

    first_msg = chunks[0] + MORE_TAG
    return first_msg, chunks, True
