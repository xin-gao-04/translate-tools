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
POST /api/analyze-header         → parse C++ header, return function list
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
from translate_comments.comment_generator import generate_doxygen
from translate_comments.detector import is_english
from translate_comments.header_parser import parse_header
from translate_comments.parsers import get_parser
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


class GenerateCommentsRequest(BaseModel):
    path: str
    function_lines: list[int]   # line_start values of functions to generate for
    replace_existing: bool = False
    host: str = "http://localhost:11434"
    model: str = "qwen2.5:7b"


class ApplyCommentsRequest(BaseModel):
    path: str
    # line_start → comment text to insert above that line
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


@app.post("/api/comments")
async def get_comments(req: ScanRequest) -> dict:
    """Parse and return all comments for the given files, including source context."""
    result: dict[str, list[dict]] = {}
    CONTEXT = 4  # lines of context to include on each side
    for path_str in req.paths:
        path = Path(path_str)
        parser = get_parser(path.suffix)
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
    """Parse a C++ header file and return function declaration metadata."""
    path = Path(req.path)
    try:
        source = path.read_text(encoding="utf-8", errors="replace")
    except OSError as exc:
        return {"error": str(exc), "functions": []}
    funcs = parse_header(source)
    return {
        "functions": [
            {
                "name":               f.name,
                "full_signature":     f.full_signature,
                "line_start":         f.line_start,
                "line_end":           f.line_end,
                "class_context":      f.class_context,
                "has_comment":        f.has_comment,
                "existing_comment":   f.existing_comment,
                "comment_line_start": f.comment_line_start,
                "comment_line_end":   f.comment_line_end,
            }
            for f in funcs
        ]
    }


@app.post("/api/apply-comments")
async def apply_comments(req: ApplyCommentsRequest) -> dict:
    """Insert/replace Doxygen comments above function declarations in a header file."""
    path = Path(req.path)
    try:
        source = path.read_text(encoding="utf-8", errors="replace")
    except OSError as exc:
        return {"ok": False, "error": str(exc)}

    lines = source.splitlines(keepends=True)

    # Build a map: line_start (1-based) → (comment_text, replace_existing, comment_line_start, comment_line_end)
    # We parse the header to get existing comment positions.
    funcs = parse_header(source)
    func_map = {f.line_start: f for f in funcs}

    # Apply in reverse order so line numbers stay valid
    for line_str, comment_text in sorted(req.comments.items(), key=lambda x: -int(x[0])):
        target_line = int(line_str)
        func = func_map.get(target_line)

        # Determine indentation from the function declaration line
        decl_line = lines[target_line - 1] if target_line <= len(lines) else ""
        indent = len(decl_line) - len(decl_line.lstrip())
        prefix = " " * indent

        # Add indentation to each comment line
        indented_comment = "\n".join(
            prefix + l if l.strip() else l
            for l in comment_text.splitlines()
        ) + "\n"

        if func and func.has_comment and req.replace_existing:
            # Replace existing comment
            cs = func.comment_line_start - 1  # 0-based
            ce = func.comment_line_end        # exclusive
            lines[cs:ce] = [indented_comment]
        else:
            # Insert before the function declaration
            insert_at = target_line - 1  # 0-based
            lines.insert(insert_at, indented_comment)

    path.write_text("".join(lines), encoding="utf-8")
    return {"ok": True}


@app.post("/api/apply")
async def apply(req: ApplyRequest) -> dict:
    """Write pre-computed translations to source files."""
    applied: list[str] = []
    errors:  list[str] = []
    for path_str, trans_map in req.translations.items():
        path = Path(path_str)
        parser = get_parser(path.suffix)
        if not parser:
            errors.append(f"No parser for {path.suffix}")
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

    loop   = asyncio.get_event_loop()
    send   = lambda evt: asyncio.run_coroutine_threadsafe(ws.send_json(evt), loop)

    translator     = OllamaTranslator(host=req.host, model=req.model)
    total_translated = 0
    total_errors   = 0
    all_translations: dict[str, dict[str, str]] = {}  # for apply-later

    try:
        for path_str in req.paths:
            path = Path(path_str)

            try:
                source = path.read_text(encoding="utf-8", errors="replace")
            except OSError as exc:
                await ws.send_json({"type": "file_error", "path": path_str, "message": str(exc)})
                total_errors += 1
                continue

            parser = get_parser(path.suffix)
            if not parser:
                await ws.send_json({
                    "type": "file_error", "path": path_str,
                    "message": f"No parser for {path.suffix}",
                })
                total_errors += 1
                continue

            all_comments = parser.extract_comments(source)
            english      = [c for c in all_comments if is_english(c.text)]
            skipped      = len(all_comments) - len(english)

            await ws.send_json({
                "type": "file_started",
                "path": path_str,
                "english_count": len(english),
                "total_comments": len(all_comments),
            })

            if not english:
                await ws.send_json({
                    "type": "file_done", "path": path_str,
                    "translated": 0, "skipped": skipped,
                })
                continue

            translations: dict[int, str] = {}
            file_translated = 0

            for c in english:
                chunks = (
                    split_for_translation(c.text)
                    if len(c.text) > req.chunk_threshold
                    else []
                )
                chunk_total = max(len(chunks), 1)

                await ws.send_json({
                    "type": "comment_started",
                    "path": path_str,
                    "lineno": c.line_start,
                    "chunk_total": chunk_total,
                })

                try:
                    if chunks:
                        # Chunked translation with partial streaming
                        async def _chunked(c=c, chunks=chunks):
                            translated_parts: list[str] = []
                            from translate_comments.splitter import join_translations
                            for idx, chunk in enumerate(chunks):
                                part = await loop.run_in_executor(
                                    None, translator.translate, chunk
                                )
                                translated_parts.append(part)
                                partial = join_translations(translated_parts, c.text)
                                await ws.send_json({
                                    "type": "comment_chunk",
                                    "path": path_str,
                                    "lineno": c.line_start,
                                    "partial": partial,
                                    "chunk_idx": idx,
                                    "chunk_total": len(chunks),
                                })
                            from translate_comments.splitter import join_translations
                            return join_translations(translated_parts, c.text)

                        result = await _chunked()
                    else:
                        result = await loop.run_in_executor(
                            None, translator.translate, c.text
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

                except TranslationError as exc:
                    await ws.send_json({
                        "type": "log",
                        "message": f"翻译失败 L{c.line_start} ({path.name}): {exc}",
                    })

            all_translations[path_str] = {str(k): v for k, v in translations.items()}

            await ws.send_json({
                "type": "file_done",
                "path": path_str,
                "translated": file_translated,
                "skipped": skipped,
                "translations": {str(k): v for k, v in translations.items()},
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
    """Stream Doxygen comment generation for selected functions.

    Client sends:  {"path": "...", "function_lines": [N,...], "replace_existing": bool,
                    "host": "...", "model": "..."}
    Server emits:
        {"type": "function_started", "name": "...", "line": N}
        {"type": "comment_chunk",    "name": "...", "partial": "..."}
        {"type": "comment_done",     "name": "...", "line": N, "comment": "..."}
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
        source = path.read_text(encoding="utf-8", errors="replace")
        all_funcs = parse_header(source)

        wanted = set(req.function_lines)
        funcs = [f for f in all_funcs if f.line_start in wanted]

        translator = OllamaTranslator(host=req.host, model=req.model)
        count = 0

        for func in funcs:
            if not req.replace_existing and func.has_comment:
                continue

            await ws.send_json({
                "type": "function_started",
                "name": func.name,
                "line": func.line_start,
            })

            partial_buf: list[str] = []

            def on_chunk(partial: str, name: str = func.name) -> None:
                partial_buf.clear()
                partial_buf.append(partial)
                asyncio.run_coroutine_threadsafe(
                    ws.send_json({"type": "comment_chunk", "name": name, "partial": partial}),
                    loop,
                )

            comment = await loop.run_in_executor(
                None,
                lambda f=func: generate_doxygen(f, translator, chunk_callback=on_chunk),
            )

            await ws.send_json({
                "type": "comment_done",
                "name": func.name,
                "line": func.line_start,
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
