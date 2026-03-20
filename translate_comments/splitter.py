"""Text splitter for long comment blocks.

Strategy:
  1. If the text contains Doxygen/Javadoc @tags, each tag-line is its own
     translation unit (preserves tag semantics).
  2. Otherwise split into sentences, then group them into chunks ≤ max_chars.
  3. A single sentence that is still too long gets hard-split at a word
     boundary.

The output is a list of strings that can each be sent to Ollama and then
re-joined to form the full translated text.
"""

from __future__ import annotations

import re

# Common abbreviations that end with a period but are NOT sentence boundaries.
_ABBREV = re.compile(
    r"\b(?:Mr|Mrs|Ms|Dr|Prof|Sr|Jr|vs|etc|e\.g|i\.e|fig|no|vol|pp|est|approx|"
    r"min|max|avg|std|ref|impl|init|param|ret|arg|args|cf|ibid)\.",
    re.IGNORECASE,
)

# Sentence-end: punctuation followed by whitespace + uppercase / digit / CJK
_SENT_SPLIT = re.compile(r'(?<=[.!?])\s+(?=[A-Z0-9\u4e00-\u9fff])')

# Doxygen / Javadoc / clang tag at line start
_DOC_TAG = re.compile(r'^\s*@\w+')


# ── Public API ────────────────────────────────────────────────────────────────

def split_for_translation(text: str, max_chars: int = 280) -> list[str]:
    """Split *text* into translation-friendly chunks.

    Parameters
    ----------
    text:
        Raw comment text (delimiters already stripped, may contain \\n).
    max_chars:
        Soft upper bound per chunk.  A single over-long sentence is hard-split
        at the nearest word boundary.

    Returns
    -------
    list[str]
        Ordered chunks ready to be translated individually and then joined.
    """
    text = text.strip()
    if not text:
        return []

    lines = text.splitlines()

    # ── Doxygen / doc-comment style ───────────────────────────────────────
    # Always split by @tag lines regardless of total length so each tag
    # gets its own focused translation.
    if any(_DOC_TAG.match(ln) for ln in lines):
        result = _split_doc_tags(lines, max_chars)
        if len(result) > 1:
            return result
        # Only one chunk → fall through to length check below

    # ── Fast path: short enough to send as-is ────────────────────────────
    if len(text) <= max_chars:
        return [text]

    # ── Plain prose: sentence-split then group ────────────────────────────
    flat = " ".join(ln.strip() for ln in lines if ln.strip())
    sentences = _split_sentences(flat)
    return _group_chunks(sentences, max_chars)


def join_translations(chunks: list[str], original: str) -> str:
    """Re-join translated chunks, preserving the structure of *original*.

    If the original was multi-line (has \\n), the joined result is wrapped
    back to roughly the same line count; otherwise a single line is returned.
    """
    if "\n" not in original:
        return " ".join(chunks)

    # Multi-line: wrap so lines are ≤ 80 chars
    combined = " ".join(chunks)
    words = combined.split()
    lines: list[str] = []
    current: list[str] = []
    length = 0
    for w in words:
        if length + len(w) + 1 > 80 and current:
            lines.append(" ".join(current))
            current = [w]
            length = len(w)
        else:
            current.append(w)
            length += len(w) + 1
    if current:
        lines.append(" ".join(current))
    return "\n".join(lines)


# ── Internal helpers ──────────────────────────────────────────────────────────

def _split_doc_tags(lines: list[str], max_chars: int) -> list[str]:
    """Each @tag (and its continuation lines) becomes one chunk."""
    chunks: list[str] = []
    current: list[str] = []

    for line in lines:
        stripped = line.strip()
        if not stripped:
            continue
        if _DOC_TAG.match(stripped) and current:
            chunk = " ".join(current)
            if len(chunk) > max_chars:
                chunks.extend(_split_sentences(chunk))
            else:
                chunks.append(chunk)
            current = [stripped]
        else:
            current.append(stripped)

    if current:
        chunk = " ".join(current)
        if len(chunk) > max_chars:
            chunks.extend(_split_sentences(chunk))
        else:
            chunks.append(chunk)

    return [c for c in chunks if c.strip()]


def _split_sentences(text: str) -> list[str]:
    """Split *text* into sentences, respecting common abbreviations."""
    # Temporarily mask abbreviation periods
    masked = _ABBREV.sub(lambda m: m.group().replace(".", "\x00"), text)
    parts = _SENT_SPLIT.split(masked)
    sentences = [p.replace("\x00", ".").strip() for p in parts if p.strip()]
    return sentences if sentences else [text]


def _group_chunks(sentences: list[str], max_chars: int) -> list[str]:
    """Group *sentences* into chunks of up to *max_chars* each."""
    chunks: list[str] = []
    current: list[str] = []
    cur_len = 0

    for sent in sentences:
        sent_len = len(sent)
        if sent_len > max_chars:
            # Hard-split oversized sentence at word boundary
            if current:
                chunks.append(" ".join(current))
                current, cur_len = [], 0
            chunks.extend(_hard_split(sent, max_chars))
        elif cur_len + sent_len + 1 > max_chars and current:
            chunks.append(" ".join(current))
            current, cur_len = [sent], sent_len
        else:
            current.append(sent)
            cur_len += sent_len + 1

    if current:
        chunks.append(" ".join(current))

    return chunks


def _hard_split(text: str, max_chars: int) -> list[str]:
    """Split *text* at word boundaries so each piece is ≤ *max_chars*."""
    words = text.split()
    chunks: list[str] = []
    current: list[str] = []
    length = 0
    for w in words:
        if length + len(w) + 1 > max_chars and current:
            chunks.append(" ".join(current))
            current, length = [w], len(w)
        else:
            current.append(w)
            length += len(w) + 1
    if current:
        chunks.append(" ".join(current))
    return chunks
