"""Language detection for comment text.

Uses a lightweight heuristic approach (no external ML dependency):

1. If the text contains a significant proportion of CJK codepoints → not English.
2. Count "English word characters" (ASCII letters + common punctuation) vs
   total non-whitespace characters.  If the ratio is high enough → English.
3. A minimum meaningful-length threshold avoids misclassifying single symbols.

This is intentionally simple — the goal is to decide "should we send this
to Ollama for translation?" rather than performing precise language ID.
For a more robust solution, swap out ``is_english`` with a call to
``langdetect`` or ``lingua-language-detector``.
"""

from __future__ import annotations

import re
import unicodedata

# Characters that are essentially "English prose" characters
_ASCII_LETTER = re.compile(r"[a-zA-Z]")

# CJK Unified Ideographs (covers most Chinese/Japanese/Korean characters)
_CJK_RANGES = (
    (0x4E00, 0x9FFF),   # CJK Unified Ideographs
    (0x3400, 0x4DBF),   # CJK Extension A
    (0x20000, 0x2A6DF), # CJK Extension B
    (0xF900, 0xFAFF),   # CJK Compatibility
    (0x2E80, 0x2EFF),   # CJK Radicals Supplement
    (0x3000, 0x303F),   # CJK Symbols and Punctuation
    (0xFF00, 0xFFEF),   # Halfwidth/Fullwidth Forms
)

# Regex / code artefacts that are never worth translating
_CODE_ONLY = re.compile(
    r"^[\s\-=*#/!@|<>(){}\[\]_+.,:;\"'`~\\%^&?0-9]+$"
)


def _is_cjk(ch: str) -> bool:
    cp = ord(ch)
    return any(lo <= cp <= hi for lo, hi in _CJK_RANGES)


def is_english(text: str, min_length: int = 3, ascii_ratio_threshold: float = 0.6) -> bool:
    """Return True if *text* appears to be primarily English.

    Parameters
    ----------
    text:
        The comment text to test (delimiters already stripped).
    min_length:
        Minimum number of non-whitespace characters required to bother
        translating (very short labels like ``//!`` are skipped).
    ascii_ratio_threshold:
        Fraction of non-whitespace chars that must be ASCII letters for the
        text to be considered English.
    """
    stripped = text.strip()
    if not stripped:
        return False

    # Skip pure-code fragments (URLs, file paths, preprocessor directives, …)
    if _CODE_ONLY.match(stripped):
        return False

    non_ws = [ch for ch in stripped if not ch.isspace()]
    if len(non_ws) < min_length:
        return False

    # If any CJK characters are present, assume already translated / mixed
    cjk_count = sum(1 for ch in non_ws if _is_cjk(ch))
    if cjk_count > 0:
        return False

    ascii_letters = sum(1 for ch in non_ws if ch.isascii() and ch.isalpha())
    ratio = ascii_letters / len(non_ws)
    return ratio >= ascii_ratio_threshold
