"""Core processing pipeline.

Orchestrates: scan → parse → detect → translate → output.

The ``Processor`` class is deliberately decoupled from the CLI so it can
be used programmatically or tested in isolation.
"""

from __future__ import annotations

import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

from translate_comments.detector import is_english
from translate_comments.parsers import get_parser
from translate_comments.parsers.base import Comment
from translate_comments.scanner import FileScanner
from translate_comments.translator import OllamaTranslator, TranslationError


# ── Output modes ─────────────────────────────────────────────────────────────

OUTPUT_INPLACE = "inplace"   # overwrite the original file
OUTPUT_STDOUT  = "stdout"    # print modified source to stdout
OUTPUT_DIFF    = "diff"      # print unified diff to stdout


@dataclass
class ProcessResult:
    """Result for a single processed file."""

    path: Path
    total_comments: int = 0
    translated_count: int = 0
    skipped_count: int = 0
    error: str | None = None

    @property
    def ok(self) -> bool:
        return self.error is None


@dataclass
class ProcessorConfig:
    """All tunable parameters for a processing run."""

    # File discovery
    extensions: list[str] = field(default_factory=lambda: [
        ".cpp", ".cxx", ".cc", ".c",
        ".h", ".hpp", ".hxx", ".hh",
        ".inl", ".ipp",
    ])
    recursive: bool = True

    # Output
    output_mode: str = OUTPUT_STDOUT   # inplace | stdout | diff
    encoding: str = "utf-8"
    encoding_errors: str = "replace"   # how to handle decode errors

    # Translation gate
    min_comment_length: int = 3        # skip very short comments
    ascii_ratio_threshold: float = 0.6

    # Ollama
    ollama_host: str = "http://localhost:11434"
    ollama_model: str = "qwen2.5:7b"
    ollama_timeout: int = 120

    # Behaviour
    dry_run: bool = False              # detect only, do not call Ollama
    verbose: bool = False


class Processor:
    """End-to-end pipeline for a single invocation.

    Parameters
    ----------
    config:
        Runtime configuration.
    progress_cb:
        Optional callback ``(message: str) -> None`` for progress reporting.
    """

    def __init__(
        self,
        config: ProcessorConfig | None = None,
        progress_cb: Callable[[str], None] | None = None,
    ) -> None:
        self.cfg = config or ProcessorConfig()
        self._log = progress_cb or (lambda msg: None)
        self._translator = OllamaTranslator(
            host=self.cfg.ollama_host,
            model=self.cfg.ollama_model,
            timeout=self.cfg.ollama_timeout,
        )
        self._scanner = FileScanner(
            extensions=self.cfg.extensions,
            recursive=self.cfg.recursive,
        )

    # ------------------------------------------------------------------ #
    # Public API                                                          #
    # ------------------------------------------------------------------ #

    def run(self, paths: list[str | Path]) -> list[ProcessResult]:
        """Process all *paths* and return one ``ProcessResult`` per file."""
        files: list[Path] = []
        for p in paths:
            found = self._scanner.scan(p)
            if not found:
                self._log(f"[skip] No matching files in: {p}")
            files.extend(found)

        if not files:
            self._log("No source files found.")
            return []

        results: list[ProcessResult] = []
        for f in files:
            result = self._process_file(f)
            results.append(result)

        return results

    # ------------------------------------------------------------------ #
    # Per-file pipeline                                                    #
    # ------------------------------------------------------------------ #

    def _process_file(self, path: Path) -> ProcessResult:
        result = ProcessResult(path=path)
        self._log(f"\n[file] {path}")

        # 1. Read source
        try:
            source = path.read_text(
                encoding=self.cfg.encoding,
                errors=self.cfg.encoding_errors,
            )
        except OSError as exc:
            result.error = f"Read error: {exc}"
            self._log(f"  [error] {result.error}")
            return result

        # 2. Select parser
        parser = get_parser(path.suffix)
        if parser is None:
            result.error = f"No parser for extension '{path.suffix}'"
            self._log(f"  [skip] {result.error}")
            return result

        # 3. Extract comments
        comments: list[Comment] = parser.extract_comments(source)
        result.total_comments = len(comments)
        self._log(f"  found {len(comments)} comment(s)")

        # 4. Filter to English-only
        to_translate: list[Comment] = [
            c for c in comments
            if is_english(
                c.text,
                min_length=self.cfg.min_comment_length,
                ascii_ratio_threshold=self.cfg.ascii_ratio_threshold,
            )
        ]
        result.skipped_count = len(comments) - len(to_translate)
        self._log(
            f"  English: {len(to_translate)}, "
            f"skipped: {result.skipped_count}"
        )

        if not to_translate:
            return result

        if self.cfg.dry_run:
            self._log("  [dry-run] translation skipped")
            for c in to_translate:
                self._log(f"    L{c.line_start}: {c.text[:80]}")
            return result

        # 5. Translate
        translations: dict[int, str] = {}
        for c in to_translate:
            self._log(
                f"  translating L{c.line_start}: {c.text[:60]}"
                + ("…" if len(c.text) > 60 else "")
            )
            try:
                translated = self._translator.translate(c.text)
                translations[c.line_start] = translated
                result.translated_count += 1
                if self.cfg.verbose:
                    self._log(f"    → {translated}")
            except TranslationError as exc:
                self._log(f"  [warn] Translation failed for L{c.line_start}: {exc}")

        if not translations:
            return result

        # 6. Rewrite source
        new_source = parser.replace_comments(source, translations)

        # 7. Output
        self._emit(path, source, new_source)
        return result

    # ------------------------------------------------------------------ #
    # Output handlers                                                      #
    # ------------------------------------------------------------------ #

    def _emit(self, path: Path, original: str, modified: str) -> None:
        mode = self.cfg.output_mode

        if mode == OUTPUT_INPLACE:
            path.write_text(modified, encoding=self.cfg.encoding)
            self._log(f"  [inplace] written → {path}")

        elif mode == OUTPUT_STDOUT:
            sys.stdout.write(modified)

        elif mode == OUTPUT_DIFF:
            import difflib
            diff = difflib.unified_diff(
                original.splitlines(keepends=True),
                modified.splitlines(keepends=True),
                fromfile=str(path),
                tofile=str(path) + " (translated)",
            )
            sys.stdout.writelines(diff)

        else:
            raise ValueError(f"Unknown output mode: {mode!r}")
