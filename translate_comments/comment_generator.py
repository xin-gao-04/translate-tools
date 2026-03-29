"""Generate configurable Doxygen-style comments for header symbols."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
import re

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
    language: str = "zh"


_SYSTEM_ZH = """\
You are a C++ documentation expert. Generate a Doxygen comment for the given C++ symbol.

Rules:
1. Use /** ... */ format with lines prefixed by " * ".
2. Be concise and technical.
3. Use the declaration and implementation snippet when available.
4. If implementation is unavailable, infer only from the declaration.
5. Do not invent behavior that is not supported by the provided code.
6. Output ONLY the comment block.
7. Write all descriptive text in Chinese (中文). Keep @-tags, parameter names, and code identifiers in English.
"""

_SYSTEM_EN = """\
You are a C++ documentation expert. Generate a Doxygen comment for the given C++ symbol.

Rules:
1. Use /** ... */ format with lines prefixed by " * ".
2. Be concise and technical.
3. Use the declaration and implementation snippet when available.
4. If implementation is unavailable, infer only from the declaration.
5. Do not invent behavior that is not supported by the provided code.
6. Output ONLY the comment block.
7. Write all descriptive text in English.
"""

_SYSTEM_TRANSLATE = """\
You are a C++ documentation expert and technical translator. \
Translate the existing Doxygen comment into {target_lang} while preserving its \
structure, @-tags, parameter names, and code identifiers. \
Output ONLY the translated comment block in /** ... */ format.
"""


def _detect_chinese(text: str) -> bool:
    """Return True if *text* contains any CJK Unified Ideograph."""
    return any("\u4e00" <= ch <= "\u9fff" for ch in text)


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
    """Generate a configurable Doxygen comment for *symbol*.

    Language-aware logic:
    - If the symbol already has a comment, detect its language:
      - If the existing comment matches the target language → skip (return as-is).
      - If it doesn't match → translate the existing comment.
    - If no comment exists → generate a new one in the target language.
    """
    target_lang = options.language or "zh"

    # ── Handle existing comments: detect language and possibly translate ──
    if symbol.has_comment and symbol.existing_comment.strip():
        existing = symbol.existing_comment.strip()
        is_chinese = _detect_chinese(existing)

        # Already in the target language → return unchanged
        if (target_lang == "zh" and is_chinese) or (target_lang == "en" and not is_chinese):
            return existing

        # Translate existing comment to the target language
        lang_name = "Chinese (中文)" if target_lang == "zh" else "English"
        system = _SYSTEM_TRANSLATE.format(target_lang=lang_name)
        prompt = f"Translate this Doxygen comment to {lang_name}:\n\n{existing}"
        result = translator.generate(prompt, system=system, chunk_callback=chunk_callback)
        return _normalize_comment(result, symbol, options)

    # ── Generate new comment ────────────────────────────────────────────
    system = _SYSTEM_ZH if target_lang == "zh" else _SYSTEM_EN

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
    result = translator.generate(prompt, system=system, chunk_callback=chunk_callback)
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

    content_lines = _comment_content_lines(result)
    filtered_content: list[str] = []
    managed_tags = {"author"}
    if options.include_date:
        managed_tags.add("date")
    managed_tags.update(
        tag.name.strip().lstrip("@").lower()
        for tag in options.custom_tags
        if tag.name.strip()
    )
    signature_line_norms = {
        _normalize_cpp_text(line)
        for line in symbol.full_signature.splitlines()
        if _normalize_cpp_text(line)
    }
    full_signature_norm = _normalize_cpp_text(symbol.full_signature)

    for line in content_lines:
        stripped = line.strip()
        normalized = stripped.lower()
        if not options.include_brief and normalized.startswith("@brief"):
            continue
        if (symbol.kind != "function" or not options.include_params) and normalized.startswith("@param"):
            continue
        if (symbol.kind != "function" or not options.include_return) and normalized.startswith("@return"):
            continue
        if normalized.startswith("@"):
            tag_name = normalized[1:].split(None, 1)[0]
            if tag_name in managed_tags:
                continue
        if _looks_like_declaration_echo(stripped, symbol, signature_line_norms, full_signature_norm):
            continue
        filtered_content.append(line)

    for extra in _tag_lines(options):
        filtered_content.append(extra.removeprefix(" * ").rstrip())

    if not filtered_content:
        return "/**\n */"

    normalized_lines = ["/**"]
    normalized_lines.extend(
        f" * {line}" if line else " *"
        for line in filtered_content
    )
    normalized_lines.append(" */")
    return "\n".join(normalized_lines)


def _comment_content_lines(comment: str) -> list[str]:
    result = comment.strip()
    if not result.startswith("/**"):
        return [line.strip() for line in result.splitlines() if line.strip()]

    end_idx = result.rfind("*/")
    if end_idx == -1:
        core = result[3:]
        trailing = ""
    else:
        core = result[3:end_idx]
        trailing = result[end_idx + 2 :]

    lines = core.splitlines()
    if len(lines) == 1 and "*/" in result and "\n" not in result:
        single = lines[0].strip()
        if single.endswith("*/"):
            single = single[:-2].rstrip()
        lines = [single]

    trailing_lines = trailing.splitlines()
    normalized_lines: list[str] = []
    for line in [*lines, *trailing_lines]:
        stripped = line.strip()
        if not stripped:
            continue
        if stripped == "/**" or stripped == "*/":
            continue
        if stripped.startswith("*"):
            stripped = stripped[1:].lstrip()
        if stripped:
            normalized_lines.append(stripped)
    return normalized_lines


def _normalize_cpp_text(text: str) -> str:
    return re.sub(r"\s+", "", text).strip()


def _looks_like_declaration_echo(
    line: str,
    symbol: HeaderSymbolInfo,
    signature_line_norms: set[str],
    full_signature_norm: str,
) -> bool:
    stripped = line.strip()
    if not stripped:
        return False

    lowered = stripped.lower().rstrip(":")
    if lowered in {"declaration", "signature", "prototype", "function", "variable"}:
        return True
    if lowered.startswith("declaration:") or lowered.startswith("signature:"):
        return True

    normalized = _normalize_cpp_text(stripped)
    if not normalized:
        return False
    if normalized in signature_line_norms or normalized == full_signature_norm:
        return True

    if symbol.name not in stripped:
        return False

    if symbol.kind == "function":
        return bool(
            "(" in stripped
            and ")" in stripped
            and (
                stripped.endswith(";")
                or stripped.endswith("{")
                or stripped.endswith(")")
                or stripped.endswith(") const")
                or stripped.endswith(") noexcept")
            )
        )

    return bool(stripped.endswith(";") or "=" in stripped or "{" in stripped)


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
