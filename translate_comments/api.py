"""FastAPI backend for translate-comments.

Exposes REST endpoints and a WebSocket stream so any frontend
(Electron, browser, CLI) can drive the translation pipeline.

Start manually:
    python -m translate_comments.api            # default port 8765
    python -m translate_comments.api --port 9000

Endpoints
---------
GET  /api/health                 → {"ok": true, "version": "0.1.0"}
POST /api/scan                   → list of matching source files
GET  /api/check?host=…&model=…   → Ollama connection status
POST /api/comments               → parse + return comments with context lines
POST /api/apply                  → write cached translations to disk
POST /api/analyze-header         → parse C++ header, return symbol list
POST /api/preview-comments       → preview generated header comments as diff
POST /api/apply-comments         → write generated comments into header file
WS   /ws                         → stream translation events (file mode)
WS   /ws/translate-text          → stream translation of arbitrary text
WS   /ws/generate-comments       → stream Doxygen comment generation
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Any

import uvicorn
from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

from translate_comments import __version__
from translate_comments.comment_generator import (
    HeaderCommentOptions,
    HeaderTag,
    generate_symbol_comment,
)
from translate_comments.detector import is_english
from translate_comments.header_workflow import (
    analyze_header_file,
    apply_header_comments,
    preview_header_comments,
)
from translate_comments.parsers import get_parser_for_path
from translate_comments.scanner import FileScanner
from translate_comments.splitter import split_for_translation
from translate_comments.translator import OllamaTranslator, TranslationError

# ── App setup ─────────────────────────────────────────────────────────────────

app = FastAPI(title="translate-comments API", version=__version__)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

# ── Pydantic models ───────────────────────────────────────────────────────────

class ScanRequest(BaseModel):
    paths: list[str]
    extensions: list[str] = [
        ".cpp", ".cxx", ".cc", ".c",
        ".h", ".hpp", ".hxx", ".hh",
        ".inl", ".ipp",
        ".cmake", "CMakeLists.txt",
    ]
    recursive: bool = True


class TranslateRequest(BaseModel):
    paths: list[str]
    host: str = "http://localhost:11434"
    model: str = "qwen2.5:7b"
    chunk_threshold: int = 120   # chars; above this → chunked translation


class ApplyRequest(BaseModel):
    """Apply pre-computed translations to files on disk."""
    translations: dict[str, dict[str, str]]
    # translations[file_path][str(lineno)] = translated_text


class AnalyzeHeaderRequest(BaseModel):
    path: str


class HeaderTagRequest(BaseModel):
    name: str
    value: str = ""


class GenerateCommentsRequest(BaseModel):
    path: str
    symbol_lines: list[int]
    replace_existing: bool = False
    host: str = "http://localhost:11434"
    model: str = "qwen2.5:7b"
    include_brief: bool = True
    include_params: bool = True
    include_return: bool = True
    author: str = ""
    include_date: bool = False
    date_format: str = "%Y-%m-%d"
    custom_tags: list[HeaderTagRequest] = []


class ApplyCommentsRequest(BaseModel):
    path: str
    comments: dict[str, str]
    replace_existing: bool = False


class PreviewCommentsRequest(BaseModel):
    path: str
    comments: dict[str, str]
    replace_existing: bool = False


class TranslateTextRequest(BaseModel):
    text: str
    host: str = "http://localhost:11434"
    model: str = "qwen2.5:7b"
    chunk_threshold: int = 120


# ── Routes ────────────────────────────────────────────────────────────────────

@app.get("/api/health")
async def health() -> dict:
    return {"ok": True, "version": __version__}


@app.post("/api/scan")
async def scan(req: ScanRequest) -> dict:
    """Return all matching source files under the given paths."""
    scanner = FileScanner(extensions=req.extensions, recursive=req.recursive)
    found: list[str] = []
    errors: list[str] = []
    for p in req.paths:
        try:
            found.extend(str(f) for f in scanner.scan(p))
        except FileNotFoundError as exc:
            errors.append(str(exc))
    return {"files": found, "errors": errors}


@app.get("/api/check")
async def check(host: str = "http://localhost:11434", model: str = "qwen2.5:7b") -> dict:
    """Ping Ollama and verify the model is available."""
    ok, msg = OllamaTranslator(host=host, model=model).check_connection()
    return {"ok": ok, "message": msg}


@app.get("/api/models")
async def list_models(host: str = "http://localhost:11434") -> dict:
    """Return available model names from Ollama."""
    try:
        models = OllamaTranslator(host=host).list_models()
        return {"ok": True, "models": models, "message": f"Loaded {len(models)} models."}
    except TranslationError as exc:
        return {"ok": False, "models": [], "message": str(exc)}


@app.post("/api/comments")
async def get_comments(req: ScanRequest) -> dict:
    """Parse and return all comments for the given files, including source context."""
    result: dict[str, list[dict]] = {}
    CONTEXT = 4  # lines of context to include on each side
    for path_str in req.paths:
        path = Path(path_str)
        parser = get_parser_for_path(path_str)
        if not parser:
            continue
        try:
            source = path.read_text(encoding="utf-8", errors="replace")
            src_lines = source.splitlines()
            comments = parser.extract_comments(source)
            rows = []
            for c in comments:
                before_start = max(0, c.line_start - 1 - CONTEXT)
                before_end   = max(0, c.line_start - 1)
                after_start  = min(len(src_lines), c.line_end)
                after_end    = min(len(src_lines), c.line_end + CONTEXT)
                rows.append({
                    "line_start":     c.line_start,
                    "line_end":       c.line_end,
                    "style":          c.style,
                    "text":           c.text,
                    "is_english":     is_english(c.text),
                    "context_before": src_lines[before_start:before_end],
                    "context_after":  src_lines[after_start:after_end],
                })
            result[path_str] = rows
        except OSError as exc:
            result[path_str] = [{"error": str(exc)}]
    return {"comments": result}


@app.post("/api/analyze-header")
async def analyze_header(req: AnalyzeHeaderRequest) -> dict:
    """Parse a C++ header file and return symbol metadata."""
    path = Path(req.path)
    try:
        symbols = analyze_header_file(path)
    except OSError as exc:
        return {"error": str(exc), "symbols": [], "functions": []}

    payload = [
        {
            "kind":               symbol.kind,
            "name":               symbol.name,
            "full_signature":     symbol.full_signature,
            "line_start":         symbol.line_start,
            "line_end":           symbol.line_end,
            "class_context":      symbol.class_context,
            "namespace_context":  symbol.namespace_context,
            "has_comment":        symbol.has_comment,
            "existing_comment":   symbol.existing_comment,
            "comment_line_start": symbol.comment_line_start,
            "comment_line_end":   symbol.comment_line_end,
            "implementation_path": symbol.implementation_path,
            "implementation_snippet": symbol.implementation_snippet,
        }
        for symbol in symbols
    ]
    return {
        "symbols": payload,
        "functions": [item for item in payload if item["kind"] == "function"],
    }


@app.post("/api/preview-comments")
async def preview_comments(req: PreviewCommentsRequest) -> dict:
    """Preview generated header comments without writing to disk."""
    path = Path(req.path)
    try:
        source = path.read_text(encoding="utf-8", errors="replace")
        symbols = analyze_header_file(path)
    except OSError as exc:
        return {"ok": False, "error": str(exc), "diff": "", "preview": ""}

    comments = {int(line): text for line, text in req.comments.items()}
    preview, diff = preview_header_comments(
        path,
        source,
        symbols,
        comments,
        req.replace_existing,
    )
    return {"ok": True, "diff": diff, "preview": preview}


@app.post("/api/apply-comments")
async def apply_comments(req: ApplyCommentsRequest) -> dict:
    """Insert or replace generated comments above header symbols."""
    path = Path(req.path)
    try:
        source = path.read_text(encoding="utf-8", errors="replace")
        symbols = analyze_header_file(path)
    except OSError as exc:
        return {"ok": False, "error": str(exc)}

    comments = {int(line): text for line, text in req.comments.items()}
    new_source = apply_header_comments(source, symbols, comments, req.replace_existing)
    path.write_text(new_source, encoding="utf-8")
    return {"ok": True}


@app.post("/api/apply")
async def apply(req: ApplyRequest) -> dict:
    """Write pre-computed translations to source files."""
    applied: list[str] = []
    errors:  list[str] = []
    for path_str, trans_map in req.translations.items():
        path = Path(path_str)
        parser = get_parser_for_path(path_str)
        if not parser:
            errors.append(f"No parser for {path.name}")
            continue
        try:
            source = path.read_text(encoding="utf-8", errors="replace")
            # Convert str lineno keys back to int
            translations = {int(k): v for k, v in trans_map.items()}
            new_source = parser.replace_comments(source, translations)
            path.write_text(new_source, encoding="utf-8")
            applied.append(path_str)
        except OSError as exc:
            errors.append(f"{path.name}: {exc}")
    return {"applied": applied, "errors": errors}


# ── WebSocket ─────────────────────────────────────────────────────────────────

@app.websocket("/ws")
async def ws_translate(ws: WebSocket) -> None:  # noqa: C901
    """Stream translation events.

    Performance strategy
    --------------------
    * Phase 1 – pre-parse: all files are read and their English comments are
      extracted concurrently (I/O ‖ CPU) before any LLM call starts.  This
      lets the UI show accurate totals instantly and overlaps disk I/O with
      later GPU work.
    * Phase 2 – translate: within each file, short comments (≤ chunk_threshold
      chars) are grouped into batches of up to 6 and sent to Ollama in a single
      JSON-formatted request; long comments are still chunked individually with
      streaming.  A session-level cache (inside OllamaTranslator) short-circuits
      repeated comment texts across files.

    Client sends one JSON message:
        {"paths": [...], "host": "...", "model": "...", "chunk_threshold": 120}

    Server streams JSON events:
        {"type": "file_started",    "path": ..., "english_count": N}
        {"type": "comment_started", "path": ..., "lineno": N, "chunk_total": N}
        {"type": "comment_chunk",   "path": ..., "lineno": N, "partial": "..."}
        {"type": "comment_done",    "path": ..., "lineno": N, "translated": "..."}
        {"type": "file_done",       "path": ..., "translated": N, "skipped": N}
        {"type": "file_error",      "path": ..., "message": "..."}
        {"type": "all_done",        "files": N, "translated": N, "errors": N}
        {"type": "log",             "message": "..."}
    """
    await ws.accept()

    try:
        raw = await ws.receive_text()
        req = TranslateRequest(**json.loads(raw))
    except Exception as exc:
        await ws.send_json({"type": "error", "message": str(exc)})
        await ws.close()
        return

    loop       = asyncio.get_event_loop()
    translator = OllamaTranslator(host=req.host, model=req.model, enable_cache=True)

    total_translated = 0
    total_errors     = 0

    # ── Phase 1: pre-parse all files concurrently ─────────────────────────────
    # Read source, select parser, extract comments. This overlaps disk I/O
    # across all files so the UI gets accurate totals before translation starts.

    async def _prepare_file(path_str: str):
        """Return (source_lines, english_comments, total_count) or (error_msg,)."""
        path = Path(path_str)
        try:
            source = await asyncio.get_event_loop().run_in_executor(
                None, lambda: path.read_text(encoding="utf-8", errors="replace")
            )
        except OSError as exc:
            return (str(exc),)
        parser = get_parser_for_path(path_str)
        if not parser:
            return (f"No parser for {path.name}",)
        all_comments = parser.extract_comments(source)
        english = [c for c in all_comments if is_english(c.text)]
        return source.splitlines(), english, len(all_comments)

    prepared = await asyncio.gather(*[_prepare_file(p) for p in req.paths])

    # Emit file_started for every file now that we know comment counts
    for path_str, prep in zip(req.paths, prepared):
        if len(prep) == 1:
            # Error tuple
            await ws.send_json({"type": "file_error", "path": path_str, "message": prep[0]})
            total_errors += 1
            continue
        source_lines, english, total_count = prep
        await ws.send_json({
            "type": "file_started",
            "path": path_str,
            "english_count": len(english),
            "total_comments": total_count,
        })

    # ── Phase 2: translate file by file ───────────────────────────────────────

    try:
        for path_str, prep in zip(req.paths, prepared):
            if len(prep) == 1:
                continue  # already emitted file_error above

            source_lines, english, total_count = prep
            path = Path(path_str)
            skipped = total_count - len(english)

            if not english:
                await ws.send_json({
                    "type": "file_done", "path": path_str,
                    "translated": 0, "skipped": skipped, "untranslated": 0,
                    "translations": {},
                })
                continue

            translations: dict[int, str] = {}
            file_translated   = 0
            file_untranslated = 0

            def _context_for(comment, src_lines=source_lines) -> tuple[str, str]:
                before_start = max(0, comment.line_start - 4)
                before_end   = max(0, comment.line_start - 1)
                after_start  = min(len(src_lines), comment.line_end)
                after_end    = min(len(src_lines), comment.line_end + 3)
                return (
                    "\n".join(src_lines[before_start:before_end]),
                    "\n".join(src_lines[after_start:after_end]),
                )

            # Separate into short (batchable) and long (needs chunking)
            short_comments = [
                c for c in english
                if len(c.text) <= req.chunk_threshold
                and len(split_for_translation(c.text, max_chars=req.chunk_threshold)) <= 1
            ]
            long_comments = [c for c in english if c not in short_comments]

            # Per-call asyncio timeout: prevents a single stuck Ollama request
            # from blocking all subsequent comments/files.
            CALL_TIMEOUT = 90   # seconds per batch or individual translate call

            async def _run_in_executor(fn):
                """Run *fn()* in a thread pool with a per-call asyncio timeout."""
                try:
                    return await asyncio.wait_for(
                        loop.run_in_executor(None, fn),
                        timeout=CALL_TIMEOUT,
                    )
                except asyncio.TimeoutError as exc:
                    raise TranslationError(
                        f"翻译超时（>{CALL_TIMEOUT}s），跳过本条"
                    ) from exc

            # ── Batch short comments ───────────────────────────────────────────
            BATCH_SIZE = 6
            for batch_start in range(0, len(short_comments), BATCH_SIZE):
                batch = short_comments[batch_start : batch_start + BATCH_SIZE]

                # Emit comment_started for each item in this batch
                for c in batch:
                    await ws.send_json({
                        "type": "comment_started",
                        "path": path_str,
                        "lineno": c.line_start,
                        "chunk_total": 1,
                    })

                batch_texts = [c.text for c in batch]
                try:
                    batch_results = await _run_in_executor(
                        lambda texts=batch_texts: translator.translate_batch(texts)
                    )

                    for c, result in zip(batch, batch_results):
                        translations[c.line_start] = result
                        await ws.send_json({
                            "type": "comment_done",
                            "path": path_str,
                            "lineno": c.line_start,
                            "translated": result,
                        })
                        file_translated  += 1
                        total_translated += 1

                except (TranslationError, Exception) as exc:  # noqa: BLE001
                    for c in batch:
                        file_untranslated += 1
                        await ws.send_json({
                            "type": "comment_failed",
                            "path": path_str,
                            "lineno": c.line_start,
                            "message": str(exc),
                        })
                    await ws.send_json({
                        "type": "log",
                        "message": f"批量翻译失败 ({path.name} L{batch[0].line_start}–L{batch[-1].line_start}): {exc}",
                    })

            # ── Long comments: chunk + stream individually ─────────────────────
            from translate_comments.splitter import join_translations
            for c in long_comments:
                context_before, context_after = _context_for(c)
                chunks = split_for_translation(c.text, max_chars=req.chunk_threshold)
                chunk_total = max(len(chunks), 1)

                await ws.send_json({
                    "type": "comment_started",
                    "path": path_str,
                    "lineno": c.line_start,
                    "chunk_total": chunk_total,
                })

                try:
                    if len(chunks) > 1:
                        translated_parts: list[str] = []
                        for idx, chunk in enumerate(chunks):
                            part = await _run_in_executor(
                                lambda ch=chunk, before=context_before, after=context_after, full=c.text:
                                    translator.translate(ch, context_before=before,
                                                         context_after=after, related_text=full)
                            )
                            translated_parts.append(part)
                            partial_text = join_translations(translated_parts, c.text)
                            await ws.send_json({
                                "type": "comment_chunk",
                                "path": path_str,
                                "lineno": c.line_start,
                                "partial": partial_text,
                                "chunk_idx": idx,
                                "chunk_total": len(chunks),
                            })
                        result = join_translations(translated_parts, c.text)
                    else:
                        result = await _run_in_executor(
                            lambda text=c.text, before=context_before, after=context_after:
                                translator.translate(text, context_before=before,
                                                     context_after=after)
                        )

                    translations[c.line_start] = result
                    await ws.send_json({
                        "type": "comment_done",
                        "path": path_str,
                        "lineno": c.line_start,
                        "translated": result,
                    })
                    file_translated  += 1
                    total_translated += 1

                except (TranslationError, Exception) as exc:  # noqa: BLE001
                    file_untranslated += 1
                    await ws.send_json({
                        "type": "comment_failed",
                        "path": path_str,
                        "lineno": c.line_start,
                        "message": str(exc),
                    })
                    await ws.send_json({
                        "type": "log",
                        "message": f"翻译失败 L{c.line_start} ({path.name}): {exc}",
                    })

            await ws.send_json({
                "type": "file_done",
                "path": path_str,
                "translated": file_translated,
                "skipped": skipped,
                "untranslated": file_untranslated,
                "translations": {str(k): v for k, v in translations.items()},
            })

            if file_untranslated > 0:
                total_errors += 1
                await ws.send_json({
                    "type": "log",
                    "message": f"{path.name} 有 {file_untranslated} 条注释未能翻译，继续处理后续文件。",
                })

        # Emit cache stats as a log hint
        stats = translator.cache_stats()
        if stats["cached_entries"] > 0:
            await ws.send_json({
                "type": "log",
                "message": f"缓存命中: 本次运行共缓存 {stats['cached_entries']} 条不同注释翻译",
            })

        await ws.send_json({
            "type": "all_done",
            "files": len(req.paths),
            "translated": total_translated,
            "errors": total_errors,
        })

    except WebSocketDisconnect:
        pass


# ── WebSocket: text translation ───────────────────────────────────────────────

@app.websocket("/ws/translate-text")
async def ws_translate_text(ws: WebSocket) -> None:
    """Stream translation of an arbitrary text block.

    Client sends:  {"text": "...", "host": "...", "model": "...", "chunk_threshold": 120}
    Server emits:
        {"type": "chunk",  "idx": N, "total": N, "partial": "..."}
        {"type": "done",   "text": "..."}
        {"type": "error",  "message": "..."}
    """
    await ws.accept()
    try:
        raw = await ws.receive_text()
        req = TranslateTextRequest(**json.loads(raw))
    except Exception as exc:
        await ws.send_json({"type": "error", "message": str(exc)})
        await ws.close()
        return

    loop   = asyncio.get_event_loop()
    translator = OllamaTranslator(host=req.host, model=req.model)

    try:
        chunks = (
            split_for_translation(req.text)
            if len(req.text) > req.chunk_threshold
            else [req.text]
        )
        total = len(chunks)
        translated_parts: list[str] = []

        from translate_comments.splitter import join_translations
        for idx, chunk in enumerate(chunks):
            part = await loop.run_in_executor(None, translator.translate, chunk)
            translated_parts.append(part)
            partial = join_translations(translated_parts, req.text)
            await ws.send_json({
                "type": "chunk",
                "idx": idx,
                "total": total,
                "partial": partial,
            })

        final = join_translations(translated_parts, req.text)
        await ws.send_json({"type": "done", "text": final})

    except WebSocketDisconnect:
        pass
    except Exception as exc:
        try:
            await ws.send_json({"type": "error", "message": str(exc)})
        except Exception:
            pass


# ── WebSocket: Doxygen comment generation ────────────────────────────────────

@app.websocket("/ws/generate-comments")
async def ws_generate_comments(ws: WebSocket) -> None:
    """Stream Doxygen comment generation for selected header symbols.

    Client sends:  {"path": "...", "symbol_lines": [N,...], "replace_existing": bool,
                    "host": "...", "model": "..."}
    Server emits:
        {"type": "symbol_started",   "name": "...", "kind": "...", "line": N}
        {"type": "comment_chunk",    "name": "...", "partial": "..."}
        {"type": "comment_done",     "name": "...", "kind": "...", "line": N, "comment": "..."}
        {"type": "all_done",         "count": N}
        {"type": "error",            "message": "..."}
    """
    await ws.accept()
    try:
        raw = await ws.receive_text()
        req = GenerateCommentsRequest(**json.loads(raw))
    except Exception as exc:
        await ws.send_json({"type": "error", "message": str(exc)})
        await ws.close()
        return

    loop = asyncio.get_event_loop()

    try:
        path = Path(req.path)
        all_symbols = analyze_header_file(path)

        wanted = set(req.symbol_lines)
        symbols = [symbol for symbol in all_symbols if symbol.line_start in wanted]

        translator = OllamaTranslator(host=req.host, model=req.model)
        options = HeaderCommentOptions(
            include_brief=req.include_brief,
            include_params=req.include_params,
            include_return=req.include_return,
            author=req.author,
            include_date=req.include_date,
            date_format=req.date_format,
            custom_tags=[HeaderTag(name=tag.name, value=tag.value) for tag in req.custom_tags],
        )
        count = 0

        for symbol in symbols:
            if not req.replace_existing and symbol.has_comment:
                continue

            await ws.send_json({
                "type": "symbol_started",
                "name": symbol.name,
                "kind": symbol.kind,
                "line": symbol.line_start,
            })

            partial_buf: list[str] = []

            def on_chunk(partial: str, name: str = symbol.name, line: int = symbol.line_start) -> None:
                partial_buf.clear()
                partial_buf.append(partial)
                asyncio.run_coroutine_threadsafe(
                    ws.send_json({"type": "comment_chunk", "name": name, "line": line, "partial": partial}),
                    loop,
                )

            comment = await loop.run_in_executor(
                None,
                lambda current=symbol: generate_symbol_comment(
                    current,
                    translator,
                    options,
                    chunk_callback=on_chunk,
                ),
            )

            await ws.send_json({
                "type": "comment_done",
                "name": symbol.name,
                "kind": symbol.kind,
                "line": symbol.line_start,
                "comment": comment,
            })
            count += 1

        await ws.send_json({"type": "all_done", "count": count})

    except WebSocketDisconnect:
        pass
    except Exception as exc:
        try:
            await ws.send_json({"type": "error", "message": str(exc)})
        except Exception:
            pass


# ── Entry point ───────────────────────────────────────────────────────────────

def run(host: str = "127.0.0.1", port: int = 8765, **kwargs) -> None:
    uvicorn.run(app, host=host, port=port, **kwargs)


if __name__ == "__main__":
    import argparse
    p = argparse.ArgumentParser()
    p.add_argument("--host", default="127.0.0.1")
    p.add_argument("--port", type=int, default=8765)
    p.add_argument("--reload", action="store_true")
    args = p.parse_args()
    uvicorn.run(
        "translate_comments.api:app",
        host=args.host, port=args.port, reload=args.reload,
    )
