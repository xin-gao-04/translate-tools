"""Abstract base class for source-code comment parsers."""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field


@dataclass
class Comment:
    """A single comment found in a source file.

    Attributes:
        text:       Raw comment text (stripped of delimiters).
        line_start: 1-based line number where the comment begins.
        line_end:   1-based line number where the comment ends.
        col_start:  0-based column where the opening delimiter starts.
        style:      Arbitrary label describing the comment style
                    (e.g. "line", "block", "doc").
        raw:        The original source fragment including delimiters.
    """

    text: str
    line_start: int
    line_end: int
    col_start: int = 0
    style: str = "line"
    raw: str = field(default="", repr=False)


class BaseParser(ABC):
    """Interface every language parser must implement.

    To add support for a new language:
      1. Create a new module under ``translate_comments/parsers/``.
      2. Subclass ``BaseParser``.
      3. Decorate the subclass with ``@register([".ext", ...])``.
      4. Implement ``extract_comments``.
      5. Import the module in ``parsers/__init__.py``.
    """

    @abstractmethod
    def extract_comments(self, source: str) -> list[Comment]:
        """Parse *source* text and return all comments found.

        Parameters
        ----------
        source:
            Full text content of the source file.

        Returns
        -------
        list[Comment]
            Comments in the order they appear in the file.
        """

    def replace_comments(self, source: str, translations: dict[int, str]) -> str:
        """Return *source* with comments replaced by translated text.

        Parameters
        ----------
        source:
            Original source text.
        translations:
            Mapping of ``Comment.line_start`` → translated text.
            Comments not present in the mapping are left unchanged.

        The default implementation calls ``extract_comments`` and rebuilds
        the source line-by-line.  Subclasses may override for more
        precise (column-aware) replacement.
        """
        comments = self.extract_comments(source)
        lines = source.splitlines(keepends=True)

        # Build a lookup: line_start -> (comment, translated_text)
        replacements: dict[int, tuple[Comment, str]] = {}
        for c in comments:
            if c.line_start in translations:
                replacements[c.line_start] = (c, translations[c.line_start])

        result: list[str] = []
        skip_until: int = -1

        for idx, line in enumerate(lines):
            lineno = idx + 1  # 1-based

            if lineno <= skip_until:
                continue  # already consumed by a multi-line replacement

            if lineno in replacements:
                comment, translated = replacements[lineno]
                # Replace raw comment text in the line(s)
                new_source = source
                if comment.raw:
                    new_source = new_source.replace(comment.raw, _rewrap(comment, translated), 1)
                    # Rebuild lines from the patched source
                    lines = new_source.splitlines(keepends=True)
                    result = lines[:idx]  # keep already-processed lines
                result.append(lines[idx])
                skip_until = comment.line_end
            else:
                result.append(line)

        return "".join(result)


def _rewrap(comment: Comment, text: str) -> str:
    """Re-wrap translated *text* using the same delimiter style as *comment*."""
    if comment.style == "line":
        return f"// {text}"
    if comment.style == "doc":
        return f"/** {text} */"
    # block
    return f"/* {text} */"
