"""Parse C++ header files to extract function declarations.

Strategy
--------
1. Preprocess the source: replace string/char literals and comment
   *bodies* with spaces so the line structure is preserved (line numbers
   stay accurate) but syntactic noise is eliminated.
2. Walk the preprocessed lines while keeping accurate brace-depth and
   class-scope state.  Brace counts happen **unconditionally** before any
   skip logic so depth is never lost.
3. Only examine lines at "declaration depth" (not inside a function body).
4. Match function names with a lenient regex that handles:
     - Regular functions:   ``void foo(int x);``
     - Constructors:        ``MyClass();``  (no return type)
     - Destructors:         ``~MyClass();``
     - Qualified names:     ``Ns::Class::method()``
     - Templates (next-line body): ``T getValue() const;``
5. Filter matched names against a keyword blocklist.
"""

from __future__ import annotations

import re
from dataclasses import dataclass


# ── Data model ────────────────────────────────────────────────────────────────

@dataclass
class FunctionInfo:
    """A single function/method declaration found in a header file."""
    name: str                   # simple identifier (e.g. ``parse``, ``~MyClass``)
    full_signature: str         # complete declaration text (may span lines)
    line_start: int             # 1-based, first line of declaration
    line_end: int               # 1-based, last line of declaration
    class_context: str          # enclosing class/struct name; "" for free functions
    has_comment: bool           # True if a comment block immediately precedes this
    existing_comment: str       # text of that comment, or ""
    comment_line_start: int     # 1-based; -1 if no comment
    comment_line_end: int       # 1-based; -1 if no comment


# ── Keywords that are definitely not function names ───────────────────────────

_CTRL_FLOW = frozenset({
    "if", "else", "while", "for", "do", "switch", "case", "default",
    "return", "break", "continue", "goto", "throw", "catch", "try",
    "sizeof", "alignof", "decltype", "typeid", "new", "delete",
    "static_assert", "assert", "class", "struct", "enum", "union",
    "namespace", "typedef", "using", "template", "friend", "extern",
    "operator", "auto",
})

# First word on a line that means "skip this line for function detection"
# (braces are still counted before the skip)
_SKIP_FIRST_WORD = frozenset({
    "class", "struct", "enum", "union", "namespace", "typedef", "using",
    "template", "friend", "return", "if", "else", "while", "for", "switch",
    "case", "break", "continue", "goto", "throw", "catch", "#",
    "public", "private", "protected", "signals", "slots",
})


# ── Source preprocessor ───────────────────────────────────────────────────────

def _preprocess(source: str) -> list[str]:
    """Return lines where string/char literal content and comment bodies
    are replaced with spaces, so line structure is preserved exactly.
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

        # Line comment
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

        # Char literal
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
    """Look backwards from *line_idx* (0-based) for a comment block that
    immediately precedes this line (one blank line is tolerated).

    Returns ``(start_0based, end_0based, text)`` or ``(-1, -1, "")``.
    """
    i = line_idx - 1
    if i >= 0 and not orig_lines[i].strip():
        i -= 1
    if i < 0:
        return -1, -1, ""

    s = orig_lines[i].strip()

    if s.startswith("//"):
        end = start = i
        while start > 0 and orig_lines[start - 1].strip().startswith("//"):
            start -= 1
        return start, end, "\n".join(orig_lines[start : end + 1])

    if s.endswith("*/") or s.startswith("/*") or (s.startswith("*") and not s.startswith("*/")):
        end = i
        while end < line_idx - 1 and not orig_lines[end].strip().endswith("*/"):
            end += 1
        start = i
        while start > 0 and not orig_lines[start].strip().startswith("/*"):
            start -= 1
        return start, end, "\n".join(orig_lines[start : end + 1])

    return -1, -1, ""


# ── Function-declaration regex ────────────────────────────────────────────────
#
# Key design choices:
#   * Return type is OPTIONAL (``?``) so constructors / destructors match.
#   * Qualifiers (virtual, static, …) are matched before the optional return type.
#   * ``~?`` at the start of name handles destructors.
#   * ``[^)]*`` inside the parameter group is deliberately loose to
#     tolerate ``std::function<void(int)>`` style parameters; false positives
#     are rejected via the ``_CTRL_FLOW`` blocklist.
#   * Terminator: ``;``, ``{`` (inline def), or ``= 0 ;`` / ``= default ;`` /
#     ``= delete ;`` (special member declarations).

_QUALIFIERS = (
    r"(?:(?:virtual|static|inline|explicit|constexpr|const|volatile|mutable|"
    r"friend|override|final|noexcept|nodiscard|\[\[[\w:,\s]+\]\]|__\w+)\s+)*"
)

_FUNC_RE = re.compile(
    _QUALIFIERS
    + r"(?:[\w:*&<>\s,\[\]]+?\s+)?"   # optional return type  (none for ctors/dtors)
    + r"(?P<name>~?[A-Za-z_]\w*)\s*"  # function name
    + r"\("                            # opening paren
    + r"[^)]*"                         # params (simplified)
    + r"\)"
    + r"[^;{=]*"                       # post-qualifiers (const, override …)
    + r"(?:;|\{|=\s*(?:0|default|delete)\s*;)",  # terminator
)

_CLASS_SCOPE_RE = re.compile(r"\s*(class|struct)\s+(\w+)[^;{]*(?:\{|$)")
_NAMESPACE_SCOPE_RE = re.compile(r"\s*(?:inline\s+)?namespace(?:\s+([\w:]+))?[^;{]*(?:\{|$)")


def _class_context(scope_stack: list[tuple[str, str, int]]) -> str:
    """Return the innermost class/struct scope name, if any."""
    for kind, name, _depth in reversed(scope_stack):
        if kind in {"class", "struct"} and name:
            return name
    return ""


# ── Public API ────────────────────────────────────────────────────────────────

def parse_header(source: str) -> list[FunctionInfo]:
    """Extract function declarations from C++ source text.

    Works on both header files (.h/.hpp) and implementation files (.cpp).
    Returns a deduplicated list of :class:`FunctionInfo` sorted by line number.

    Handles:
    * ``class Foo {`` and ``class Foo\\n{`` (brace on next line)
    * ``namespace demo { ... }`` and ``inline namespace v1 { ... }``
    * Constructors, destructors, and plain methods
    * Multi-line declarations (accumulated until parens balance)
    * Only looks for declarations at declaration depth (skips function bodies)
    """
    orig_lines = source.splitlines()
    proc_lines = _preprocess(source)

    results: list[FunctionInfo] = []
    scope_stack: list[tuple[str, str, int]] = []
    brace_depth = 0          # total open braces
    pending_scope: tuple[str, str] | None = None   # scope whose { hasn't appeared yet
    seen_lines: set[int] = set()

    i = 0
    while i < len(proc_lines):
        raw = proc_lines[i]
        stripped = raw.strip()
        line_start_depth = brace_depth

        # ── STEP 1: unconditionally track braces ──────────────────────────
        #   This must happen before any ``continue`` so we never lose depth.

        opens  = raw.count("{")
        closes = raw.count("}")

        opened_scopes: list[tuple[str, str, int]] = []

        # ── STEP 2: detect class/struct/namespace declarations ────────────
        class_m = _CLASS_SCOPE_RE.match(raw)
        namespace_m = _NAMESPACE_SCOPE_RE.match(raw)
        if class_m:
            kind, name = class_m.group(1), class_m.group(2)
            if "{" in raw:
                opened_scopes.append((kind, name, line_start_depth + 1))
            else:
                pending_scope = (kind, name)
        elif namespace_m:
            name = namespace_m.group(1) or ""
            if "{" in raw:
                opened_scopes.append(("namespace", name, line_start_depth + 1))
            else:
                pending_scope = ("namespace", name)
        elif opens > 0 and pending_scope is not None:
            opened_scopes.append((pending_scope[0], pending_scope[1], line_start_depth + 1))
            pending_scope = None

        brace_depth = line_start_depth + opens - closes
        scope_stack.extend(opened_scopes)
        while scope_stack and brace_depth < scope_stack[-1][2]:
            scope_stack.pop()

        if class_m or namespace_m:
            i += 1
            continue

        # ── STEP 3: skip trivially non-function lines ─────────────────────
        if not stripped or stripped.startswith("#"):
            i += 1
            continue

        m_first = re.match(r"^\s*(\w+)", stripped)
        first_word = m_first.group(1) if m_first else ""
        if first_word in _SKIP_FIRST_WORD:
            i += 1
            continue

        # ── STEP 4: skip if inside a function/method body ─────────────────
        # ``scope_stack`` tracks declaration scopes (namespace/class/struct).
        # When brace depth exceeds open declaration scopes, we're inside a body.
        if brace_depth > len(scope_stack):
            i += 1
            continue

        if "(" not in raw:
            i += 1
            continue

        # ── STEP 5: accumulate multi-line declarations ────────────────────
        decl_start = i
        combined   = proc_lines[i]
        paren_depth = raw.count("(") - raw.count(")")
        j = i + 1
        while paren_depth > 0 and j < len(proc_lines) and j < i + 20:
            nxt = proc_lines[j]
            combined   += " " + nxt.strip()
            paren_depth += nxt.count("(") - nxt.count(")")
            j += 1
        decl_end = (j - 1) if (j > i + 1 and paren_depth <= 0) else i

        # ── STEP 6: regex match ───────────────────────────────────────────
        m = _FUNC_RE.search(combined)
        if m:
            func_name = m.group("name")
            if func_name not in _CTRL_FLOW and decl_start not in seen_lines:
                seen_lines.add(decl_start)
                sig = "\n".join(orig_lines[decl_start : decl_end + 1]).strip()
                cs, ce, ct = _find_comment_before(orig_lines, decl_start)
                results.append(
                    FunctionInfo(
                        name=func_name,
                        full_signature=sig,
                        line_start=decl_start + 1,
                        line_end=decl_end + 1,
                        class_context=_class_context(scope_stack),
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
