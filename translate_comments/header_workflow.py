"""Helpers for header analysis, implementation lookup, and diff preview."""

from __future__ import annotations

import difflib
import re
from dataclasses import replace
from pathlib import Path

from translate_comments.header_parser import HeaderSymbolInfo, _preprocess, parse_header_symbols
from translate_comments.scanner import FileScanner


_SOURCE_EXTENSIONS = [".cpp", ".cxx", ".cc", ".c", ".ipp", ".inl"]


def analyze_header_file(path: Path) -> list[HeaderSymbolInfo]:
    source = path.read_text(encoding="utf-8", errors="replace")
    symbols = parse_header_symbols(source)
    return [enrich_symbol_with_implementation(path, symbol) for symbol in symbols]


def enrich_symbol_with_implementation(path: Path, symbol: HeaderSymbolInfo) -> HeaderSymbolInfo:
    if symbol.kind != "function":
        return symbol

    match = find_function_implementation(path, symbol)
    if not match:
        return symbol

    impl_path, snippet = match
    return replace(
        symbol,
        implementation_path=str(impl_path),
        implementation_snippet=snippet,
    )


def find_function_implementation(path: Path, symbol: HeaderSymbolInfo) -> tuple[Path, str] | None:
    candidates = _candidate_implementation_files(path)
    if not candidates:
        return None

    for candidate in candidates:
        try:
            source = candidate.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        snippet = _extract_definition_snippet(source, _candidate_names(symbol))
        if snippet:
            return candidate, snippet
    return None


def apply_header_comments(
    source: str,
    symbols: list[HeaderSymbolInfo],
    comments: dict[int, str],
    replace_existing: bool,
) -> str:
    """Insert or replace comments in *source* and return the modified text."""

    if not comments:
        return source

    lines = source.splitlines(keepends=True)
    symbol_map = {symbol.line_start: symbol for symbol in symbols}

    for target_line, comment_text in sorted(comments.items(), reverse=True):
        symbol = symbol_map.get(target_line)
        decl_line = lines[target_line - 1] if 0 < target_line <= len(lines) else ""
        indent = len(decl_line) - len(decl_line.lstrip())
        prefix = " " * indent
        indented_comment = "\n".join(
            prefix + line if line.strip() else line
            for line in comment_text.splitlines()
        ) + "\n"

        if symbol and symbol.has_comment and replace_existing:
            start = symbol.comment_line_start - 1
            end = symbol.comment_line_end
            lines[start:end] = [indented_comment]
        else:
            insert_at = target_line - 1
            lines.insert(insert_at, indented_comment)

    return "".join(lines)


def preview_header_comments(
    path: Path,
    source: str,
    symbols: list[HeaderSymbolInfo],
    comments: dict[int, str],
    replace_existing: bool,
) -> tuple[str, str]:
    modified = apply_header_comments(source, symbols, comments, replace_existing)
    diff = "".join(
        difflib.unified_diff(
            source.splitlines(keepends=True),
            modified.splitlines(keepends=True),
            fromfile=str(path),
            tofile=f"{path} (generated)",
        )
    )
    return modified, diff


def _candidate_implementation_files(header_path: Path) -> list[Path]:
    scanner = FileScanner(_SOURCE_EXTENSIONS, recursive=True)
    roots = [header_path.parent]
    parent = header_path.parent.parent
    if parent not in roots and parent.exists():
        roots.append(parent)

    found: dict[Path, int] = {}
    for root in roots:
        try:
            for file_path in scanner.scan(root):
                if file_path == header_path:
                    continue
                found[file_path] = found.get(file_path, 0) + 1
        except FileNotFoundError:
            continue

    stem = header_path.stem
    return sorted(
        found,
        key=lambda p: (
            0 if p.stem == stem else 1,
            0 if p.parent == header_path.parent else 1,
            len(str(p)),
            str(p),
        ),
    )


def _candidate_names(symbol: HeaderSymbolInfo) -> list[str]:
    names: list[str] = []
    namespace_prefix = f"{symbol.namespace_context}::" if symbol.namespace_context else ""

    if symbol.class_context:
        if namespace_prefix:
            names.append(f"{namespace_prefix}{symbol.class_context}::{symbol.name}")
        names.append(f"{symbol.class_context}::{symbol.name}")
    elif namespace_prefix:
        names.append(f"{namespace_prefix}{symbol.name}")

    names.append(symbol.name)

    seen: set[str] = set()
    ordered: list[str] = []
    for name in names:
        if name and name not in seen:
            seen.add(name)
            ordered.append(name)
    return ordered


def _extract_definition_snippet(source: str, candidate_names: list[str]) -> str:
    masked = "\n".join(_preprocess(source))
    for candidate in candidate_names:
        pattern = re.compile(rf"(?<!\w){re.escape(candidate)}\s*\(")
        for match in pattern.finditer(masked):
            brace_pos = _find_definition_open_brace(masked, match.end() - 1)
            if brace_pos < 0:
                continue
            end_pos = _find_matching_brace(masked, brace_pos)
            if end_pos < 0:
                continue
            line_start = source.rfind("\n", 0, match.start()) + 1
            return source[line_start : end_pos + 1].strip()
    return ""


def _find_definition_open_brace(source: str, start_pos: int) -> int:
    paren_depth = 0
    i = start_pos
    n = len(source)
    while i < n:
        ch = source[i]
        if ch == "(":
            paren_depth += 1
        elif ch == ")":
            paren_depth = max(0, paren_depth - 1)
        elif ch == ";" and paren_depth == 0:
            return -1
        elif ch == "{" and paren_depth == 0:
            return i
        i += 1
    return -1


def _find_matching_brace(source: str, open_pos: int) -> int:
    depth = 0
    for i in range(open_pos, len(source)):
        ch = source[i]
        if ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                return i
    return -1
