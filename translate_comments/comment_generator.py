"""Generate configurable Doxygen-style comments for header symbols."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime

from .header_parser import FunctionInfo, HeaderSymbolInfo
from .translator import OllamaTranslator


@dataclass
class HeaderTag:
    name: str
    value: str = ""


@dataclass
class HeaderCommentOptions:
    include_brief: bool = True
    include_params: bool = True
    include_return: bool = True
    author: str = ""
    include_date: bool = False
    date_format: str = "%Y-%m-%d"
    custom_tags: list[HeaderTag] = field(default_factory=list)


_SYSTEM = """\
You are a C++ documentation expert. Generate a Doxygen comment for the given C++ symbol.

Rules:
1. Use /** ... */ format with lines prefixed by " * ".
2. Be concise and technical.
3. Use the declaration and implementation snippet when available.
4. If implementation is unavailable, infer only from the declaration.
5. Do not invent behavior that is not supported by the provided code.
6. Output ONLY the comment block.
"""


def generate_doxygen(
    func: FunctionInfo,
    translator: OllamaTranslator,
    chunk_callback=None,
) -> str:
    """Backward-compatible wrapper for function-only callers."""

    symbol = HeaderSymbolInfo(
        kind="function",
        name=func.name,
        full_signature=func.full_signature,
        line_start=func.line_start,
        line_end=func.line_end,
        class_context=func.class_context,
        namespace_context="",
        has_comment=func.has_comment,
        existing_comment=func.existing_comment,
        comment_line_start=func.comment_line_start,
        comment_line_end=func.comment_line_end,
    )
    return generate_symbol_comment(symbol, translator, HeaderCommentOptions(), chunk_callback)


def generate_symbol_comment(
    symbol: HeaderSymbolInfo,
    translator: OllamaTranslator,
    options: HeaderCommentOptions,
    chunk_callback=None,
) -> str:
    """Generate a configurable Doxygen comment for *symbol*."""

    symbol_desc = "member function" if symbol.kind == "function" else "variable"
    context_lines: list[str] = [
        f"Symbol kind: {symbol_desc}",
        f"Declaration:\n```cpp\n{symbol.full_signature}\n```",
    ]
    if symbol.namespace_context:
        context_lines.append(f"Namespace: {symbol.namespace_context}")
    if symbol.class_context:
        context_lines.append(f"Class/struct: {symbol.class_context}")
    if symbol.implementation_snippet:
        context_lines.append(
            "Implementation snippet:\n"
            f"```cpp\n{symbol.implementation_snippet}\n```"
        )
    else:
        context_lines.append("Implementation snippet: (not found)")

    rules: list[str] = []
    if options.include_brief:
        rules.append("Include @brief.")
    else:
        rules.append("Do not include @brief.")

    if symbol.kind == "function" and options.include_params:
        rules.append("Include one @param line per parameter when parameters exist.")
    else:
        rules.append("Do not include @param lines.")

    if symbol.kind == "function" and options.include_return:
        rules.append("Include @return only when the function returns a meaningful value.")
    else:
        rules.append("Do not include @return.")

    prompt = "\n\n".join(context_lines + ["Formatting requirements: " + " ".join(rules)])
    result = translator.generate(prompt, system=_SYSTEM, chunk_callback=chunk_callback)
    return _normalize_comment(result, symbol, options)


def _normalize_comment(
    comment: str,
    symbol: HeaderSymbolInfo,
    options: HeaderCommentOptions,
) -> str:
    result = comment.strip()
    lines = result.splitlines()
    if lines and lines[0].strip().startswith("```"):
        lines = lines[1:]
    if lines and lines[-1].strip().startswith("```"):
        lines = lines[:-1]
    result = "\n".join(lines).strip()

    if not result.startswith("/**"):
        result = f"/**\n * {result}\n */"

    body = result.splitlines()
    filtered: list[str] = []
    for line in body:
        stripped = line.strip()
        if not options.include_brief and "@brief" in stripped:
            continue
        if (symbol.kind != "function" or not options.include_params) and "@param" in stripped:
            continue
        if (symbol.kind != "function" or not options.include_return) and "@return" in stripped:
            continue
        filtered.append(line)

    if not filtered:
        filtered = ["/**", " */"]

    insert_at = len(filtered) - 1 if filtered[-1].strip() == "*/" else len(filtered)
    extra_lines = _tag_lines(options)
    if extra_lines:
        filtered[insert_at:insert_at] = extra_lines

    return "\n".join(filtered)


def _tag_lines(options: HeaderCommentOptions) -> list[str]:
    lines: list[str] = []
    if options.author.strip():
        lines.append(f" * @author {options.author.strip()}")
    if options.include_date:
        try:
            date_value = datetime.now().strftime(options.date_format)
        except ValueError:
            date_value = datetime.now().strftime("%Y-%m-%d")
        lines.append(f" * @date {date_value}")
    for tag in options.custom_tags:
        name = tag.name.strip().lstrip("@")
        value = tag.value.strip()
        if not name:
            continue
        lines.append(f" * @{name}" + (f" {value}" if value else ""))
    return lines
