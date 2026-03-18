"""Command-line interface for translate-comments.

Usage examples
--------------
# Translate all C++ files in current directory, print to stdout:
    translate-comments .

# Translate a single file, overwrite in-place:
    translate-comments src/main.cpp --output inplace

# Preview changes as a unified diff, use a custom model:
    translate-comments src/ --output diff --model llama3.2

# Dry-run (detect English comments, no translation):
    translate-comments src/ --dry-run

# Use custom Ollama host and extra file extensions:
    translate-comments src/ --host http://192.168.1.10:11434 --ext .metal --ext .glsl

# Check Ollama connection only:
    translate-comments --check
"""

from __future__ import annotations

import sys

import click

from translate_comments.parsers import registered_extensions
from translate_comments.processor import (
    OUTPUT_DIFF,
    OUTPUT_INPLACE,
    OUTPUT_STDOUT,
    Processor,
    ProcessorConfig,
)
from translate_comments.translator import OllamaTranslator

# ── Defaults exposed for --help display ──────────────────────────────────────
_DEFAULT_EXTENSIONS = [
    ".cpp", ".cxx", ".cc", ".c",
    ".h", ".hpp", ".hxx", ".hh",
    ".inl", ".ipp",
]
_DEFAULT_HOST  = "http://localhost:11434"
_DEFAULT_MODEL = "qwen2.5:7b"


# ── CLI definition ────────────────────────────────────────────────────────────

@click.command(context_settings={"help_option_names": ["-h", "--help"]})
@click.argument("paths", nargs=-1, type=click.Path(exists=True), required=False)
# --- output ---
@click.option(
    "-o", "--output",
    type=click.Choice([OUTPUT_STDOUT, OUTPUT_INPLACE, OUTPUT_DIFF], case_sensitive=False),
    default=OUTPUT_STDOUT,
    show_default=True,
    help="Output mode: print modified source, overwrite file in-place, or show unified diff.",
)
# --- file selection ---
@click.option(
    "-e", "--ext",
    multiple=True,
    metavar="EXT",
    help=(
        "Additional file extension to process (e.g. --ext .glsl).  "
        "May be repeated.  Default extensions: "
        + ", ".join(_DEFAULT_EXTENSIONS)
    ),
)
@click.option(
    "-r/-R", "--recursive/--no-recursive",
    default=True,
    show_default=True,
    help="Recurse into subdirectories.",
)
# --- translation ---
@click.option(
    "--host",
    default=_DEFAULT_HOST,
    show_default=True,
    envvar="OLLAMA_HOST",
    help="Ollama server base URL.  Also reads $OLLAMA_HOST.",
)
@click.option(
    "-m", "--model",
    default=_DEFAULT_MODEL,
    show_default=True,
    envvar="OLLAMA_MODEL",
    help="Ollama model name.  Also reads $OLLAMA_MODEL.",
)
@click.option(
    "--timeout",
    default=120,
    show_default=True,
    type=int,
    help="HTTP timeout for each Ollama request (seconds).",
)
# --- behaviour ---
@click.option(
    "--dry-run",
    is_flag=True,
    default=False,
    help="Detect English comments but do not call Ollama.",
)
@click.option(
    "-v", "--verbose",
    is_flag=True,
    default=False,
    help="Print each translated comment.",
)
@click.option(
    "--check",
    is_flag=True,
    default=False,
    help="Check Ollama connection and model availability, then exit.",
)
@click.option(
    "--list-parsers",
    is_flag=True,
    default=False,
    help="List all registered file extensions and exit.",
)
def main(
    paths,
    output,
    ext,
    recursive,
    host,
    model,
    timeout,
    dry_run,
    verbose,
    check,
    list_parsers,
):
    """Translate English comments in C/C++ source files to Chinese via Ollama.

    PATHS can be files or directories.  Defaults to the current directory
    when no path is given.
    """
    # ── --list-parsers ────────────────────────────────────────────────────
    if list_parsers:
        exts = sorted(f".{e}" for e in registered_extensions())
        click.echo("Registered extensions:")
        for e in exts:
            click.echo(f"  {e}")
        sys.exit(0)

    # ── --check ───────────────────────────────────────────────────────────
    if check:
        translator = OllamaTranslator(host=host, model=model)
        ok, msg = translator.check_connection()
        if ok:
            click.secho(f"✓ {msg}", fg="green")
            sys.exit(0)
        else:
            click.secho(f"✗ {msg}", fg="red", err=True)
            sys.exit(1)

    # ── Resolve target paths ──────────────────────────────────────────────
    if not paths:
        paths = (".",)

    # ── Build extension list ──────────────────────────────────────────────
    extensions = list(_DEFAULT_EXTENSIONS)
    for e in ext:
        normalized = ("." + e.lstrip(".")).lower()
        if normalized not in extensions:
            extensions.append(normalized)

    # ── Warn for inplace + stdout confusion ───────────────────────────────
    if output == OUTPUT_INPLACE and not dry_run:
        click.secho(
            "Warning: --output inplace will overwrite source files. "
            "Make sure you have a backup or working git state.",
            fg="yellow", err=True,
        )

    # ── Build config ──────────────────────────────────────────────────────
    cfg = ProcessorConfig(
        extensions=extensions,
        recursive=recursive,
        output_mode=output,
        ollama_host=host,
        ollama_model=model,
        ollama_timeout=timeout,
        dry_run=dry_run,
        verbose=verbose,
    )

    # ── Progress callback → stderr so stdout stays clean for source output ──
    def progress(msg: str) -> None:
        click.echo(msg, err=True)

    # ── Run ───────────────────────────────────────────────────────────────
    processor = Processor(config=cfg, progress_cb=progress)
    results = processor.run(list(paths))

    # ── Summary ───────────────────────────────────────────────────────────
    total_files      = len(results)
    ok_files         = sum(1 for r in results if r.ok)
    total_translated = sum(r.translated_count for r in results)
    total_comments   = sum(r.total_comments for r in results)
    errors           = [r for r in results if not r.ok]

    click.echo(
        f"\n{'─'*50}\n"
        f"Files processed : {ok_files}/{total_files}\n"
        f"Comments found  : {total_comments}\n"
        f"Translated      : {total_translated}\n",
        err=True,
    )

    if errors:
        click.secho("Errors:", fg="red", err=True)
        for r in errors:
            click.secho(f"  {r.path}: {r.error}", fg="red", err=True)
        sys.exit(1)
