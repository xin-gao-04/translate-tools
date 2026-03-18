"""Parse C++ headers and expose a unified symbol model.

This module extracts declaration-level symbols from header-like files:
functions, methods, constructors/destructors, and common variable/member
declarations. The parser is intentionally heuristic, but it keeps line
numbers stable and stays namespace/class aware so the UI can drive comment
generation and diff preview reliably.
"""

from __future__ import annotations

import re
from dataclasses import dataclass


# ── Data model ────────────────────────────────────────────────────────────────

@dataclass
class HeaderSymbolInfo:
    """A declaration-like symbol found in a header file."""

    kind: str                   # "function" | "variable"
    name: str
    full_signature: str
    line_start: int
    line_end: int
    class_context: str
    namespace_context: str
    has_comment: bool
    existing_comment: str
    comment_line_start: int
    comment_line_end: int
    implementation_path: str = ""
    implementation_snippet: str = ""


@dataclass
class FunctionInfo:
    """Backward-compatible function view used by older callers/tests."""

    name: str
    full_signature: str
    line_start: int
    line_end: int
    class_context: str
    has_comment: bool
    existing_comment: str
    comment_line_start: int
    comment_line_end: int


# ── Keywords that are definitely not symbol names ────────────────────────────

_CTRL_FLOW = frozenset({
    "if", "else", "while", "for", "do", "switch", "case", "default",
    "return", "break", "continue", "goto", "throw", "catch", "try",
    "sizeof", "alignof", "decltype", "typeid", "new", "delete",
    "static_assert", "assert", "class", "struct", "enum", "union",
    "namespace", "typedef", "using", "template", "friend",
    "operator", "auto",
})

_SKIP_FIRST_WORD = frozenset({
    "class", "struct", "enum", "union", "namespace", "typedef", "using",
    "template", "friend", "return", "if", "else", "while", "for", "switch",
    "case", "break", "continue", "goto", "throw", "catch", "#",
    "public", "private", "protected", "signals", "slots",
})


# ── Source preprocessor ───────────────────────────────────────────────────────

def _preprocess(source: str) -> list[str]:
    """Return lines with comments and literal bodies masked by spaces."""

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

        if c == "/" and i + 1 < n and source[i + 1] == "/":
            while i < n and source[i] != "\n":
                line.append(" ")
                i += 1
            continue

        if c == "/" and i + 1 < n and source[i + 1] == "*":
            line.append("  ")
            i += 2
            in_block = True
            continue

        if c == '"':
            line.append(c)
            i += 1
            in_string = True
            continue

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
    """Return the comment block immediately preceding *line_idx* if present."""

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


# ── Declaration regexes ───────────────────────────────────────────────────────

_QUALIFIERS = (
    r"(?:(?:virtual|static|inline|explicit|constexpr|const|volatile|mutable|"
    r"friend|override|final|noexcept|nodiscard|\[\[[\w:,\s]+\]\]|__\w+)\s+)*"
)

_FUNC_RE = re.compile(
    _QUALIFIERS
    + r"(?:[\w:*&<>\s,\[\]]+?\s+)?"
    + r"(?P<name>~?[A-Za-z_]\w*)\s*"
    + r"\("
    + r"[^)]*"
    + r"\)"
    + r"[^;{=]*"
    + r"(?:;|\{|=\s*(?:0|default|delete)\s*;)",
)

_VAR_RE = re.compile(
    _QUALIFIERS
    + r"(?:[\w:<>~,\[\]\s]+?)\s+"
    + r"(?:[*&]\s*)*"
    + r"(?P<name>[A-Za-z_]\w*)"
    + r"\s*(?:\[[^\]]*\])?"
    + r"\s*(?:=\s*[^;]+|\{[^;]*\})?\s*;",
)

_CLASS_SCOPE_RE = re.compile(r"\s*(class|struct)\s+(\w+)[^;{]*(?:\{|$)")
_NAMESPACE_SCOPE_RE = re.compile(r"\s*(?:inline\s+)?namespace(?:\s+([\w:]+))?[^;{]*(?:\{|$)")


def _class_context(scope_stack: list[tuple[str, str, int]]) -> str:
    for kind, name, _depth in reversed(scope_stack):
        if kind in {"class", "struct"} and name:
            return name
    return ""


def _namespace_context(scope_stack: list[tuple[str, str, int]]) -> str:
    names = [name for kind, name, _depth in scope_stack if kind == "namespace" and name]
    return "::".join(names)


def _accumulate_function(lines: list[str], start: int) -> tuple[str, int]:
    combined = lines[start]
    paren_depth = lines[start].count("(") - lines[start].count(")")
    j = start + 1
    while paren_depth > 0 and j < len(lines) and j < start + 20:
        nxt = lines[j]
        combined += " " + nxt.strip()
        paren_depth += nxt.count("(") - nxt.count(")")
        j += 1
    end = (j - 1) if (j > start + 1 and paren_depth <= 0) else start
    return combined, end


def _accumulate_statement(lines: list[str], start: int) -> tuple[str, int]:
    combined = lines[start]
    j = start + 1
    while ";" not in combined and j < len(lines) and j < start + 20:
        combined += " " + lines[j].strip()
        j += 1
    end = max(start, j - 1)
    return combined, end


def _looks_like_variable_statement(statement: str) -> bool:
    stripped = statement.strip()
    if not stripped or ";" not in stripped:
        return False
    if "(" in stripped or ")" in stripped:
        return False
    if stripped.startswith(("public:", "private:", "protected:", "signals:", "slots:")):
        return False
    first = re.match(r"^\s*(\w+)", stripped)
    first_word = first.group(1) if first else ""
    if first_word in _SKIP_FIRST_WORD or first_word in _CTRL_FLOW:
        return False
    if "::" in stripped and stripped.endswith("};"):
        return False
    return True


def _make_symbol(
    *,
    kind: str,
    name: str,
    decl_start: int,
    decl_end: int,
    orig_lines: list[str],
    scope_stack: list[tuple[str, str, int]],
) -> HeaderSymbolInfo:
    cs, ce, ct = _find_comment_before(orig_lines, decl_start)
    return HeaderSymbolInfo(
        kind=kind,
        name=name,
        full_signature="\n".join(orig_lines[decl_start : decl_end + 1]).strip(),
        line_start=decl_start + 1,
        line_end=decl_end + 1,
        class_context=_class_context(scope_stack),
        namespace_context=_namespace_context(scope_stack),
        has_comment=cs >= 0,
        existing_comment=ct,
        comment_line_start=cs + 1 if cs >= 0 else -1,
        comment_line_end=ce + 1 if ce >= 0 else -1,
    )


# ── Public API ────────────────────────────────────────────────────────────────

def parse_header_symbols(source: str) -> list[HeaderSymbolInfo]:
    """Extract declaration-level symbols from C++ header/source text."""

    orig_lines = source.splitlines()
    proc_lines = _preprocess(source)

    results: list[HeaderSymbolInfo] = []
    scope_stack: list[tuple[str, str, int]] = []
    brace_depth = 0
    pending_scope: tuple[str, str] | None = None
    seen_lines: set[int] = set()

    i = 0
    while i < len(proc_lines):
        raw = proc_lines[i]
        stripped = raw.strip()
        line_start_depth = brace_depth

        opens = raw.count("{")
        closes = raw.count("}")
        opened_scopes: list[tuple[str, str, int]] = []

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

        if not stripped or stripped.startswith("#"):
            i += 1
            continue

        first = re.match(r"^\s*(\w+)", stripped)
        first_word = first.group(1) if first else ""
        if first_word in _SKIP_FIRST_WORD:
            i += 1
            continue

        # Only examine declaration-depth lines. Using line_start_depth avoids
        # skipping declarations that themselves open braces (e.g. inline defs or
        # brace initialisers) before we have a chance to classify them.
        if line_start_depth > len(scope_stack):
            i += 1
            continue

        decl_start = i

        if "(" in raw:
            combined, decl_end = _accumulate_function(proc_lines, i)
            m_func = _FUNC_RE.search(combined)
            if m_func:
                func_name = m_func.group("name")
                if func_name not in _CTRL_FLOW and decl_start not in seen_lines:
                    seen_lines.add(decl_start)
                    results.append(
                        _make_symbol(
                            kind="function",
                            name=func_name,
                            decl_start=decl_start,
                            decl_end=decl_end,
                            orig_lines=orig_lines,
                            scope_stack=scope_stack,
                        )
                    )
                    i = decl_end + 1
                    continue

        if ";" not in raw and i + 1 < len(proc_lines):
            combined_stmt, decl_end = _accumulate_statement(proc_lines, i)
        else:
            combined_stmt, decl_end = raw, i

        if _looks_like_variable_statement(combined_stmt):
            m_var = _VAR_RE.search(combined_stmt)
            if m_var and decl_start not in seen_lines:
                name = m_var.group("name")
                if name not in _CTRL_FLOW:
                    seen_lines.add(decl_start)
                    results.append(
                        _make_symbol(
                            kind="variable",
                            name=name,
                            decl_start=decl_start,
                            decl_end=decl_end,
                            orig_lines=orig_lines,
                            scope_stack=scope_stack,
                        )
                    )
                    i = decl_end + 1
                    continue

        i += 1

    results.sort(key=lambda symbol: (symbol.line_start, symbol.kind, symbol.name))
    return results


def parse_header(source: str) -> list[FunctionInfo]:
    """Backward-compatible function-only API."""

    functions: list[FunctionInfo] = []
    for symbol in parse_header_symbols(source):
        if symbol.kind != "function":
            continue
        functions.append(
            FunctionInfo(
                name=symbol.name,
                full_signature=symbol.full_signature,
                line_start=symbol.line_start,
                line_end=symbol.line_end,
                class_context=symbol.class_context,
                has_comment=symbol.has_comment,
                existing_comment=symbol.existing_comment,
                comment_line_start=symbol.comment_line_start,
                comment_line_end=symbol.comment_line_end,
            )
        )
    return functions
