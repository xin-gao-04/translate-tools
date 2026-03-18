"""Parse C++ header files to extract function declarations.

Finds every function/method declaration, determines whether it already has
a preceding Doxygen/line comment, and returns structured metadata so the
AI comment generator can produce well-targeted output.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field


# ── Data model ────────────────────────────────────────────────────────────────

@dataclass
class FunctionInfo:
    """A single function/method declaration found in a header file."""
    name: str                   # simple identifier (e.g. "parse", "~MyClass")
    full_signature: str         # complete declaration text (may span lines)
    line_start: int             # 1-based, first line of declaration
    line_end: int               # 1-based, last line of declaration
    class_context: str          # enclosing class/struct name, "" for free functions
    has_comment: bool           # True if a comment block immediately precedes this
    existing_comment: str       # text of that comment, or ""
    comment_line_start: int     # 1-based; -1 if no comment
    comment_line_end: int       # 1-based; -1 if no comment


# ── Control-flow keywords (never function names) ──────────────────────────────

_CTRL_FLOW = frozenset({
    "if", "else", "while", "for", "do", "switch", "case", "default",
    "return", "break", "continue", "goto", "throw", "catch", "try",
    "sizeof", "alignof", "decltype", "typeid", "new", "delete",
    "static_assert", "assert", "class", "struct", "enum", "union",
    "namespace", "typedef", "using", "template", "friend", "extern",
    "operator",
})


# ── Source preprocessor ───────────────────────────────────────────────────────

def _preprocess(source: str) -> list[str]:
    """Return lines where string/char literals and comment *bodies* are replaced
    with spaces, so the line structure is preserved for accurate line numbers.
    Block-comment delimiters `/*` and `*/` become two spaces each.
    """
    out: list[str] = []
    line: list[str] = []
    i = 0
    n = len(source)
    in_block = False
    in_string = False

    while i < n:
        c = source[i]

        if c == "\n":
            out.append("".join(line))
            line = []
            i += 1
            in_string = False
            continue

        if in_block:
            if c == "*" and i + 1 < n and source[i + 1] == "/":
                line.append("  ")
                i += 2
                in_block = False
            else:
                line.append(" ")
                i += 1
            continue

        if in_string:
            if c == "\\" and i + 1 < n:
                line.append("  ")
                i += 2
            elif c == '"':
                line.append(c)
                i += 1
                in_string = False
            else:
                line.append(" ")
                i += 1
            continue

        # Line comment → blank out to EOL
        if c == "/" and i + 1 < n and source[i + 1] == "/":
            while i < n and source[i] != "\n":
                line.append(" ")
                i += 1
            continue

        # Block comment start
        if c == "/" and i + 1 < n and source[i + 1] == "*":
            line.append("  ")
            i += 2
            in_block = True
            continue

        # String literal
        if c == '"':
            line.append(c)
            i += 1
            in_string = True
            continue

        # Char literal (skip until matching ' or newline)
        if c == "'":
            line.append(c)
            i += 1
            while i < n and source[i] != "'" and source[i] != "\n":
                if source[i] == "\\" and i + 1 < n:
                    line.append("  ")
                    i += 2
                else:
                    line.append(" ")
                    i += 1
            if i < n and source[i] == "'":
                line.append(source[i])
                i += 1
            continue

        line.append(c)
        i += 1

    if line:
        out.append("".join(line))
    return out


# ── Comment-before detector ───────────────────────────────────────────────────

def _find_comment_before(orig_lines: list[str], line_idx: int) -> tuple[int, int, str]:
    """Look backwards from line_idx (0-based) for a comment block that
    immediately precedes this line (one blank line is tolerated).

    Returns ``(start_0based, end_0based, text)`` or ``(-1, -1, "")``.
    """
    i = line_idx - 1

    # Allow one blank separator
    if i >= 0 and not orig_lines[i].strip():
        i -= 1

    if i < 0:
        return -1, -1, ""

    s = orig_lines[i].strip()

    # Line-comment block (//...)
    if s.startswith("//"):
        end = i
        start = i
        while start > 0 and orig_lines[start - 1].strip().startswith("//"):
            start -= 1
        return start, end, "\n".join(orig_lines[start : end + 1])

    # End of / inside a block comment
    if s.endswith("*/") or s.startswith("/*") or (s.startswith("*") and not s.startswith("*/")):
        end = i
        # Walk forward to find actual */ if we landed mid-block
        while end < line_idx - 1 and not orig_lines[end].strip().endswith("*/"):
            end += 1
        start = i
        while start > 0 and not orig_lines[start].strip().startswith("/*"):
            start -= 1
        return start, end, "\n".join(orig_lines[start : end + 1])

    return -1, -1, ""


# ── Function-declaration pattern ──────────────────────────────────────────────

_FUNC_RE = re.compile(
    r"(?:(?:virtual|static|inline|explicit|constexpr|const|volatile|"
    r"mutable|friend|override|final|noexcept|nodiscard|\[\[[\w:,\s]+\]\])\s+)*"
    r"(?:[\w:*&<>\s,]+?\s+)"   # return type (non-greedy)
    r"(?P<name>~?[A-Za-z_]\w*)\s*"  # function name
    r"\([^)]*\)"                # parameter list
    r"[^;{=]*"                  # post-qualifiers (const, override, noexcept …)
    r"(?:;|\{|=\s*(?:0|default|delete)\s*;)",  # terminator
)

# Prefixes that are definitively NOT function declarations on the same line
_SKIP_FIRST_WORD = frozenset({
    "class", "struct", "enum", "union", "namespace", "typedef", "using",
    "template", "friend", "return", "if", "else", "while", "for", "switch",
    "case", "break", "continue", "goto", "throw", "catch", "#",
})


# ── Public API ────────────────────────────────────────────────────────────────

def parse_header(source: str) -> list[FunctionInfo]:
    """Extract function declarations from C++ source text.

    Works on both header files (.h/.hpp) and implementation files (.cpp).
    Returns a deduplicated list of :class:`FunctionInfo` sorted by line number.
    """
    orig_lines = source.splitlines()
    proc_lines = _preprocess(source)

    results: list[FunctionInfo] = []
    class_stack: list[str] = []
    brace_depth = 0
    seen_lines: set[int] = set()

    i = 0
    while i < len(proc_lines):
        raw = proc_lines[i]
        stripped = raw.strip()

        # ── Track class/struct scope ──────────────────────────────────────
        cls_m = re.match(r"\s*(?:class|struct)\s+(\w+)[^;]*$", raw)
        if cls_m and "{" in raw:
            class_stack.append(cls_m.group(1))
            brace_depth += raw.count("{")
            i += 1
            continue

        opens = raw.count("{")
        closes = raw.count("}")
        brace_depth += opens - closes
        if closes and class_stack and brace_depth < len(class_stack):
            class_stack.pop()

        # ── Skip trivially non-function lines ────────────────────────────
        if not stripped or stripped.startswith("#"):
            i += 1
            continue

        m_first = re.match(r"^\s*(\w+)", stripped)
        first_word = m_first.group(1) if m_first else ""
        if first_word in _SKIP_FIRST_WORD:
            i += 1
            continue

        if "(" not in raw:
            i += 1
            continue

        # ── Accumulate multi-line declarations ───────────────────────────
        decl_start = i
        combined = proc_lines[i]
        depth = raw.count("(") - raw.count(")")
        j = i + 1
        while depth > 0 and j < len(proc_lines) and j < i + 20:
            nxt = proc_lines[j]
            combined += " " + nxt.strip()
            depth += nxt.count("(") - nxt.count(")")
            j += 1
        decl_end = (j - 1) if (j > i + 1 and depth <= 0) else i

        # ── Try to match a function declaration ──────────────────────────
        m = _FUNC_RE.search(combined)
        if m:
            func_name = m.group("name")
            if func_name not in _CTRL_FLOW and len(func_name) > 1 and decl_start not in seen_lines:
                seen_lines.add(decl_start)
                sig = "\n".join(orig_lines[decl_start : decl_end + 1]).strip()
                cs, ce, ct = _find_comment_before(orig_lines, decl_start)
                results.append(
                    FunctionInfo(
                        name=func_name,
                        full_signature=sig,
                        line_start=decl_start + 1,
                        line_end=decl_end + 1,
                        class_context=class_stack[-1] if class_stack else "",
                        has_comment=cs >= 0,
                        existing_comment=ct,
                        comment_line_start=cs + 1 if cs >= 0 else -1,
                        comment_line_end=ce + 1 if ce >= 0 else -1,
                    )
                )
                i = decl_end + 1
                continue

        i += 1

    results.sort(key=lambda f: f.line_start)
    return results
