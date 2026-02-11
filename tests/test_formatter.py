"""Tests for formatter.py — pure functions, easy to test.

Covers: markdown stripping, whitespace collapsing, sentence boundary
detection, byte counting, chunking, [!more] placement, provenance tags.
"""

import sys
import os

# Add project root to path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from formatter import (
    byte_len,
    chunk_text,
    clean_text,
    collapse_whitespace,
    format_response,
    strip_markdown,
    truncate_at_sentence,
)


# --- strip_markdown ---


def test_strip_bold():
    assert strip_markdown("This is **bold** text") == "This is bold text"


def test_strip_italic():
    assert strip_markdown("This is *italic* text") == "This is italic text"


def test_strip_inline_code():
    assert strip_markdown("Use `print()` here") == "Use print() here"


def test_strip_headers():
    result = strip_markdown("## Section Title\nSome content")
    assert "##" not in result
    assert "Section Title" in result
    assert "Some content" in result


def test_strip_links():
    assert strip_markdown("See [the docs](http://example.com)") == "See the docs"


def test_strip_code_blocks():
    text = "Before\n```python\nprint('hi')\n```\nAfter"
    result = strip_markdown(text)
    assert "```" not in result
    assert "Before" in result
    assert "After" in result


def test_strip_blockquote():
    assert ">" not in strip_markdown("> This is quoted")


def test_strip_lists():
    text = "Items:\n- First\n- Second\n* Third"
    result = strip_markdown(text)
    assert "First" in result
    assert "Second" in result
    assert "-" not in result.split("First")[0]  # no list markers before content


def test_strip_ordered_lists():
    text = "Steps:\n1. First\n2. Second"
    result = strip_markdown(text)
    assert "First" in result
    # The numbered markers should be removed
    assert "1." not in result


# --- collapse_whitespace ---


def test_collapse_multiple_spaces():
    assert collapse_whitespace("hello   world") == "hello world"


def test_collapse_multiple_newlines():
    assert collapse_whitespace("hello\n\n\nworld") == "hello world"


def test_collapse_tabs():
    assert collapse_whitespace("hello\t\tworld") == "hello world"


def test_collapse_trim():
    assert collapse_whitespace("  hello  ") == "hello"


# --- clean_text ---


def test_clean_combined():
    text = "## Title\n\nThis is **bold** and *italic*.\n\n- A list item"
    result = clean_text(text)
    assert "##" not in result
    assert "**" not in result
    assert "*" not in result
    assert "- " not in result
    # No double spaces or newlines
    assert "  " not in result


# --- byte_len ---


def test_byte_len_ascii():
    assert byte_len("hello") == 5


def test_byte_len_unicode():
    # ✓ is 3 bytes in UTF-8
    assert byte_len("✓") == 3


def test_byte_len_empty():
    assert byte_len("") == 0


# --- truncate_at_sentence ---


def test_truncate_fits():
    text = "Short text."
    assert truncate_at_sentence(text, 100) == text


def test_truncate_at_period():
    text = "First sentence. Second sentence. Third sentence."
    result = truncate_at_sentence(text, 30)
    assert result.endswith(".")
    assert byte_len(result) <= 30


def test_truncate_at_question_mark():
    text = "Is this a question? Yes it is."
    result = truncate_at_sentence(text, 25)
    assert result.endswith("?")
    assert byte_len(result) <= 25


def test_truncate_word_boundary_fallback():
    text = "No sentence endings here just words flowing on and on"
    result = truncate_at_sentence(text, 30)
    assert byte_len(result) <= 30
    assert not result.endswith(" ")  # shouldn't end with space


def test_truncate_preserves_content():
    text = "The answer is 42. More details follow."
    result = truncate_at_sentence(text, 20)
    assert "42" in result


# --- chunk_text ---


def test_chunk_fits_single():
    text = "Short message."
    chunks = chunk_text(text, 100)
    assert len(chunks) == 1
    assert chunks[0] == text


def test_chunk_splits():
    text = "First sentence. Second sentence. Third sentence. Fourth sentence."
    chunks = chunk_text(text, 40)
    assert len(chunks) > 1
    for chunk in chunks:
        assert byte_len(chunk) <= 40


def test_chunk_all_content_preserved():
    text = "Alpha. Bravo. Charlie. Delta. Echo. Foxtrot."
    chunks = chunk_text(text, 25)
    combined = " ".join(chunks)
    for word in ["Alpha", "Bravo", "Charlie", "Delta", "Echo", "Foxtrot"]:
        assert word in combined


# --- format_response ---


def test_format_fits_one_message():
    text = "Short answer."
    first, chunks, truncated = format_response(text, max_bytes=100)
    assert first == "Short answer."
    assert len(chunks) == 1
    assert not truncated


def test_format_strips_markdown():
    text = "The answer is **42** according to `docs`."
    first, _, _ = format_response(text, max_bytes=200)
    assert "**" not in first
    assert "`" not in first
    assert "42" in first


def test_format_truncated_has_more_tag():
    text = "A" * 300  # way too long
    first, chunks, truncated = format_response(text, max_bytes=100)
    assert truncated
    assert first.endswith("[!more]")
    assert byte_len(first) <= 100
    assert len(chunks) > 1


def test_format_provenance_tag():
    text = "Fish limit is 6 per day."
    first, _, _ = format_response(text, max_bytes=200, provenance="MARINA-ORACLE")
    assert first.startswith("[via MARINA-ORACLE]")


def test_format_provenance_truncated():
    # Provenance + long text should still fit within limits
    text = "A very long answer. " * 20
    first, _, truncated = format_response(
        text, max_bytes=100, provenance="MARINA-ORACLE"
    )
    assert byte_len(first) <= 100
    assert "[via MARINA-ORACLE]" in first


def test_format_empty_input():
    first, _, _ = format_response("", max_bytes=230)
    assert first  # should have a fallback message


def test_format_whitespace_only():
    first, _, _ = format_response("   \n\n  ", max_bytes=230)
    assert first  # fallback message


# --- Run tests ---

if __name__ == "__main__":
    import inspect

    passed = 0
    failed = 0

    for name, func in sorted(inspect.getmembers(sys.modules[__name__], inspect.isfunction)):
        if name.startswith("test_"):
            try:
                func()
                passed += 1
                print(f"  ✓ {name}")
            except AssertionError as e:
                failed += 1
                print(f"  ✗ {name}: {e}")
            except Exception as e:
                failed += 1
                print(f"  ✗ {name}: {type(e).__name__}: {e}")

    print(f"\n{passed} passed, {failed} failed")
    sys.exit(1 if failed else 0)
