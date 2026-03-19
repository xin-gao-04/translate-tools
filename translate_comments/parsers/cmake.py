"""CMake comment parser.

Handles line comments beginning with ``#`` in ``.cmake`` files and
``CMakeLists.txt``.
"""

from __future__ import annotations

from translate_comments.parsers import register, register_paths
from translate_comments.parsers.base import BaseParser, Comment


@register([".cmake"])
@register_paths(["cmakelists.txt"])
class CMakeParser(BaseParser):
    """Simple parser for CMake line comments."""

    def extract_comments(self, source: str) -> list[Comment]:
        comments: list[Comment] = []
        lines = source.splitlines()
        idx = 0

        while idx < len(lines):
            line = lines[idx]
            comment_col = self._find_comment_start(line)
            if comment_col is None:
                idx += 1
                continue

            block_lines: list[str] = []
            raw_lines: list[str] = []
            start_line = idx + 1

            while idx < len(lines):
                current = lines[idx]
                current_col = self._find_comment_start(current)
                if current_col is None or current_col != comment_col:
                    break

                raw_lines.append(current[current_col:])
                block_lines.append(current[current_col + 1:].strip())
                idx += 1

            comments.append(Comment(
                text="\n".join(block_lines),
                line_start=start_line,
                line_end=start_line + len(block_lines) - 1,
                col_start=comment_col,
                style="line",
                raw="\n".join(raw_lines),
            ))

        return comments

    def replace_comments(self, source: str, translations: dict[int, str]) -> str:
        comments = self.extract_comments(source)
        result = source

        for comment in sorted(comments, key=lambda item: item.line_start, reverse=True):
            translated = translations.get(comment.line_start)
            if translated is None:
                continue
            result = result.replace(comment.raw, _rewrap_cmake(comment, translated), 1)

        return result

    @staticmethod
    def _find_comment_start(line: str) -> int | None:
        in_quote = False
        escaped = False

        for idx, ch in enumerate(line):
            if ch == "\\" and not escaped:
                escaped = True
                continue
            if ch == '"' and not escaped:
                in_quote = not in_quote
            elif ch == "#" and not in_quote:
                return idx
            escaped = False

        return None


def _rewrap_cmake(comment: Comment, text: str) -> str:
    indent = " " * max(comment.col_start, 0)
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    if not lines:
        lines = [""]

    original_count = max(comment.line_end - comment.line_start + 1, 1)
    if len(lines) < original_count:
        joined = " ".join(lines).strip()
        words = joined.split()
        if len(words) >= original_count:
            base, extra = divmod(len(words), original_count)
            rebuilt: list[str] = []
            start = 0
            for idx in range(original_count):
                size = base + (1 if idx < extra else 0)
                rebuilt.append(" ".join(words[start:start + size]).strip())
                start += size
            lines = rebuilt
        else:
            lines = [joined] + [""] * (original_count - 1)
    elif len(lines) > original_count:
        head = lines[:original_count - 1]
        tail = " ".join(lines[original_count - 1:]).strip()
        lines = [*head, tail]

    return "\n".join(
        f"{indent}# {line}" if line else f"{indent}#"
        for line in lines
    )
