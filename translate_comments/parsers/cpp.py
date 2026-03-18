"""C / C++ comment parser.

Handles:
  - Line comments:      //  ...
  - Block comments:     /* ... */
  - Doc comments:       /** ... */  (Doxygen / Javadoc style)

The parser is a hand-rolled state machine that correctly skips:
  - String literals  "..."
  - Character literals '.'
  - Raw string literals  R"delimiter(...)delimiter"  (C++11)
  - Nested // inside /* */ blocks
"""

from __future__ import annotations

import re

from translate_comments.parsers.base import BaseParser, Comment
from translate_comments.parsers import register


@register([".cpp", ".cxx", ".cc", ".c", ".h", ".hpp", ".hxx", ".hh", ".inl", ".ipp"])
class CppParser(BaseParser):
    """State-machine parser for C/C++ source files."""

    def extract_comments(self, source: str) -> list[Comment]:  # noqa: C901  (complex but necessary)
        comments: list[Comment] = []
        src = source
        n = len(src)
        i = 0
        line = 1
        col = 0  # 0-based column tracking

        def _line_of(pos: int) -> int:
            return src.count("\n", 0, pos) + 1

        def _col_of(pos: int) -> int:
            last_nl = src.rfind("\n", 0, pos)
            return pos - last_nl - 1 if last_nl != -1 else pos

        while i < n:
            ch = src[i]

            # ── Raw string literal  R"delim(...)delim"
            if ch == "R" and i + 1 < n and src[i + 1] == '"':
                # find the delimiter
                j = i + 2
                delim_start = j
                while j < n and src[j] not in ("(", "\n"):
                    j += 1
                if j < n and src[j] == "(":
                    delim = src[delim_start:j]
                    close = f'){delim}"'
                    end = src.find(close, j + 1)
                    if end == -1:
                        break
                    i = end + len(close)
                    continue
                # not a raw string after all, fall through

            # ── Regular string literal
            if ch == '"':
                i += 1
                while i < n:
                    c = src[i]
                    if c == "\\":
                        i += 2
                        continue
                    if c == '"':
                        i += 1
                        break
                    i += 1
                continue

            # ── Character literal
            if ch == "'":
                i += 1
                while i < n:
                    c = src[i]
                    if c == "\\":
                        i += 2
                        continue
                    if c == "'":
                        i += 1
                        break
                    i += 1
                continue

            # ── Line comment  // ...
            if ch == "/" and i + 1 < n and src[i + 1] == "/":
                start = i
                line_start = _line_of(start)
                col_start = _col_of(start)
                text_lines: list[str] = []

                while True:
                    line_comment_start = i
                    i += 2
                    while i < n and src[i] != "\n":
                        i += 1
                    raw_line = src[line_comment_start:i]
                    text_lines.append(raw_line[2:].strip())

                    if i >= n or src[i] != "\n":
                        break

                    j = i + 1
                    while j < n and src[j] in (" ", "\t"):
                        j += 1
                    next_col = _col_of(j) if j < n else 0
                    if j + 1 < n and src[j] == "/" and src[j + 1] == "/" and next_col == col_start:
                        i = j
                        continue
                    break

                raw = src[start:i]
                text = "\n".join(text_lines)
                comments.append(Comment(
                    text=text,
                    line_start=line_start,
                    line_end=_line_of(i - 1),
                    col_start=col_start,
                    style="line",
                    raw=raw,
                ))
                continue

            # ── Block comment  /* ... */  or doc comment  /** ... */
            if ch == "/" and i + 1 < n and src[i + 1] == "*":
                start = i
                line_start = _line_of(start)
                col_start = _col_of(start)
                is_doc = i + 2 < n and src[i + 2] == "*" and (i + 3 >= n or src[i + 3] != "/")
                i += 2
                while i < n:
                    if src[i] == "*" and i + 1 < n and src[i + 1] == "/":
                        i += 2
                        break
                    i += 1
                raw = src[start:i]
                line_end = _line_of(i - 1)

                # Strip delimiters and clean up per-line * prefix
                inner = raw
                if inner.startswith("/**"):
                    inner = inner[3:]
                elif inner.startswith("/*"):
                    inner = inner[2:]
                if inner.endswith("*/"):
                    inner = inner[:-2]

                # Remove leading * on each line (doxygen / clang style)
                cleaned_lines = []
                for block_line in inner.splitlines():
                    stripped = block_line.strip().lstrip("*").strip()
                    if stripped:
                        cleaned_lines.append(stripped)
                # Preserve newlines for multiline blocks so translation and
                # rewriting can honour the original line structure.
                text = "\n".join(cleaned_lines)

                comments.append(Comment(
                    text=text,
                    line_start=line_start,
                    line_end=line_end,
                    col_start=col_start,
                    style="doc" if is_doc else "block",
                    raw=raw,
                ))
                continue

            i += 1

        return comments

    # ------------------------------------------------------------------ #
    # replace_comments — column-aware in-place rewrite                    #
    # ------------------------------------------------------------------ #

    def replace_comments(self, source: str, translations: dict[int, str]) -> str:
        """Rewrite *source* replacing each comment whose line_start is in
        *translations* with the translated text, preserving indentation and
        surrounding code on the same line."""
        comments = self.extract_comments(source)

        # Work on a mutable character list for precise offset replacement
        result = list(source)
        offset = 0  # cumulative length delta from previous replacements

        # Sort by position so offsets accumulate correctly
        for c in sorted(comments, key=lambda x: x.line_start):
            if c.line_start not in translations:
                continue
            translated = translations[c.line_start]
            new_raw = _rewrap(c, translated)

            # Find the raw comment in the (already-offset) result string
            current = "".join(result)
            pos = current.find(c.raw)
            if pos == -1:
                continue  # safety: skip if not found (e.g., already replaced)

            result[pos : pos + len(c.raw)] = list(new_raw)

        return "".join(result)


# ────────────────────────────────────────────────────────────────────────────
# Helpers
# ────────────────────────────────────────────────────────────────────────────

def _rewrap(comment: Comment, text: str) -> str:
    if comment.style == "line":
        indent = " " * max(comment.col_start, 0)
        lines = [line.strip() for line in text.splitlines()] or [text.strip()]
        return "\n".join(
            f"{indent}// {line}" if line else f"{indent}//"
            for line in lines
        )

    # Block / doc — rebuild with per-line " * " prefix when multiline
    lines = text.splitlines()
    if len(lines) <= 1:
        single = text.replace("\n", " ")
        if comment.style == "doc":
            return f"/** {single} */"
        return f"/* {single} */"

    # Detect original indentation from the raw comment's first " *" line
    indent = ""
    if comment.raw:
        for raw_line in comment.raw.splitlines()[1:]:
            stripped = raw_line.lstrip()
            if stripped.startswith("*"):
                indent = raw_line[: len(raw_line) - len(raw_line.lstrip())]
                break

    open_tag  = "/**" if comment.style == "doc" else "/*"
    body      = "\n".join(f"{indent} * {l}" for l in lines)
    close_tag = f"{indent} */"
    return f"{open_tag}\n{body}\n{close_tag}"
