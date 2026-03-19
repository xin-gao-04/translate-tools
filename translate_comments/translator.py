"""Ollama-backed translator.

Sends comment text to a local Ollama instance and returns the Chinese
translation.  The client is intentionally thin — it owns only the HTTP
interaction and prompt construction; all orchestration lives in
``processor.py``.

Performance features
--------------------
* **Session-level translation cache**: identical comment text is translated
  once per run; subsequent occurrences return the cached result instantly.
* **Adaptive token limit**: ``num_predict`` is sized to ~3.5× input tokens
  rather than a fixed 512, so short comments finish faster.
* **Context trimming**: ``context_before/after`` is omitted for very short
  comments (< 60 chars) where it adds prompt overhead without helping quality.
* **Batch LLM translation**: :meth:`translate_batch` groups multiple short
  comments into a single Ollama call with JSON-structured output, reducing
  HTTP round-trips from N to N÷batch_size.

Ollama API reference: https://github.com/ollama/ollama/blob/main/docs/api.md
"""

from __future__ import annotations

import json
import re
import time
from typing import Iterator

import requests


# ── Default values (all overridable via CLI) ─────────────────────────────────

DEFAULT_HOST = "http://localhost:11434"
DEFAULT_MODEL = "qwen2.5:7b"  # good Chinese↔English model available on Ollama
REQUEST_TIMEOUT = 120  # seconds

# Comments shorter than this skip context_before/after in the prompt (saves
# ~100-200 prompt tokens for comments that don't benefit from context).
_SHORT_COMMENT_THRESHOLD = 60   # chars

# Default batch size for translate_batch()
_DEFAULT_BATCH_SIZE = 6

# ── System / user prompt templates ───────────────────────────────────────────

_SYSTEM_PROMPT = (
    "You are a precise technical translator. "
    "Translate the following source-code comment from English to Simplified Chinese. "
    "Rules:\n"
    "  1. Output ONLY the translated Chinese text — no explanations, no quotes, no labels.\n"
    "  2. Preserve technical terms, variable names, type names, and code identifiers as-is.\n"
    "  3. Keep the translation concise and natural for a developer audience.\n"
    "  4. Do NOT add punctuation that was not in the original unless grammatically required.\n"
)

_USER_TEMPLATE = (
    "Task: translate an English source-code comment into Simplified Chinese.\n"
    "Return only the final Chinese translation.\n\n"
    "Comment:\n{text}\n\n"
    "Context for disambiguation only:\n"
    "{context_block}"
)

_BATCH_SYSTEM_PROMPT = (
    "You are a precise technical translator. "
    "Translate source-code comments from English to Simplified Chinese. "
    "Rules: preserve technical terms, variable names, and code identifiers as-is. "
    "Output ONLY valid JSON — no other text."
)


class TranslationError(RuntimeError):
    """Raised when Ollama returns an error or is unreachable."""


_PROMPT_ECHO_PATTERNS = [
    re.compile(r"^\s*task:\s*translate.*$", re.IGNORECASE),
    re.compile(r"^\s*return only the final chinese translation\.?\s*$", re.IGNORECASE),
    re.compile(r"^\s*comment(?: to translate)?:\s*$", re.IGNORECASE),
    re.compile(r"^\s*context for disambiguation only:\s*$", re.IGNORECASE),
    re.compile(
        r"^\s*use the surrounding source context only to disambiguate meaning\.?\s*$",
        re.IGNORECASE,
    ),
    re.compile(r"^\s*translate only the comment text itself\.?\s*$", re.IGNORECASE),
    re.compile(r"^\s*source context before:\s*$", re.IGNORECASE),
    re.compile(r"^\s*source context after:\s*$", re.IGNORECASE),
    re.compile(r"^\s*full comment block context:\s*$", re.IGNORECASE),
    re.compile(r"^\s*\(no extra context\)\s*$", re.IGNORECASE),
    re.compile(r"^\s*上下文仅用于区分[:：]?\s*$"),
    re.compile(r"^\s*仅使用上下文辅助判断[:：]?\s*$"),
    re.compile(r"^\s*仅翻译注释文本本身[:：]?\s*$"),
    re.compile(r"^\s*源代码上下文.*[:：]?\s*$"),
    re.compile(r"^\s*完整注释块上下文[:：]?\s*$"),
    re.compile(r"^\s*无额外上下文\s*$"),
]


def sanitize_translation_output(content: str, original_text: str) -> str:
    """Remove prompt-echo boilerplate from translation output."""
    lines = content.splitlines()
    cleaned: list[str] = []
    skipping_context = False

    for line in lines:
        stripped = line.strip()
        lower = stripped.lower()

        if stripped == original_text.strip():
            continue

        if any(pattern.match(stripped) for pattern in _PROMPT_ECHO_PATTERNS):
            skipping_context = (
                "context" in lower
                or "上下文" in stripped
            )
            continue

        if skipping_context:
            if not stripped:
                skipping_context = False
            continue

        cleaned.append(line)

    result = "\n".join(cleaned).strip()
    return result or content.strip()


class OllamaTranslator:
    """Translate text via a running Ollama instance.

    Parameters
    ----------
    host:
        Base URL of the Ollama server (default: ``http://localhost:11434``).
    model:
        Model name as registered in Ollama (default: ``qwen2.5:7b``).
    timeout:
        HTTP request timeout in seconds.
    max_retries:
        Number of times to retry on connection error.
    retry_delay:
        Seconds to wait between retries.
    enable_cache:
        If True (default), identical comment texts are translated once and
        the result reused for subsequent occurrences in the same run.
    """

    def __init__(
        self,
        host: str = DEFAULT_HOST,
        model: str = DEFAULT_MODEL,
        timeout: int = REQUEST_TIMEOUT,
        max_retries: int = 3,
        retry_delay: float = 2.0,
        enable_cache: bool = True,
    ) -> None:
        self.host = host.rstrip("/")
        self.model = model
        self.timeout = timeout
        self.max_retries = max_retries
        self.retry_delay = retry_delay
        self.enable_cache = enable_cache
        self._session = requests.Session()
        # Session-level cache: normalised_text → translated_text
        self._cache: dict[str, str] = {}

    # ------------------------------------------------------------------ #
    # Public interface                                                     #
    # ------------------------------------------------------------------ #

    def translate(
        self,
        text: str,
        *,
        context_before: str = "",
        context_after: str = "",
        related_text: str = "",
    ) -> str:
        """Translate a single *text* string and return the Chinese result.

        Results are cached by normalised text so repeated occurrences within
        a run are free.  Raises ``TranslationError`` on failure.
        """
        cache_key = text.strip()
        if self.enable_cache and cache_key in self._cache:
            return self._cache[cache_key]

        # Short comments don't benefit from context — omit to save tokens
        use_context = len(cache_key) >= _SHORT_COMMENT_THRESHOLD
        payload = self._build_payload(
            text,
            stream=False,
            context_before=context_before if use_context else "",
            context_after=context_after if use_context else "",
            related_text=related_text if use_context else "",
        )
        response = self._post_with_retry("/api/chat", payload)
        content = self._parse_chat_response(response)
        result = sanitize_translation_output(content, text)

        if self.enable_cache:
            self._cache[cache_key] = result
        return result

    def translate_batch(
        self,
        texts: list[str],
        *,
        batch_size: int = _DEFAULT_BATCH_SIZE,
    ) -> list[str]:
        """Translate a list of short texts using batched LLM calls.

        Groups up to *batch_size* texts into a single Ollama request with
        JSON-structured output, reducing HTTP round-trips from N to ≈N÷batch_size.

        The cache is checked before sending and updated after each batch.
        Falls back to individual :meth:`translate` calls if the LLM response
        cannot be parsed as a valid JSON array of the expected length.

        Parameters
        ----------
        texts:
            List of comment strings to translate (in order).
        batch_size:
            Maximum number of texts per LLM call.  Tune downward for models
            that have trouble with long structured outputs.

        Returns
        -------
        list[str]
            Translated strings in the same order as *texts*.  Items that fail
            are returned as the original text (never raises).
        """
        if not texts:
            return []

        results: list[str] = [""] * len(texts)
        pending_indices: list[int] = []

        # Fill cache hits first
        for idx, text in enumerate(texts):
            key = text.strip()
            if self.enable_cache and key in self._cache:
                results[idx] = self._cache[key]
            else:
                pending_indices.append(idx)

        # Process pending in batches
        for batch_start in range(0, len(pending_indices), batch_size):
            batch_idx = pending_indices[batch_start : batch_start + batch_size]
            batch_texts = [texts[i] for i in batch_idx]

            if len(batch_texts) == 1:
                # Single item: use standard translate (gets streaming + context)
                results[batch_idx[0]] = self._translate_safe(batch_texts[0])
                if self.enable_cache:
                    self._cache[batch_texts[0].strip()] = results[batch_idx[0]]
                continue

            translated = self._translate_batch_llm(batch_texts)
            for list_pos, original_idx in enumerate(batch_idx):
                result = translated[list_pos]
                results[original_idx] = result
                if self.enable_cache:
                    self._cache[texts[original_idx].strip()] = result

        return results

    def translate_chunked(
        self,
        text: str,
        chunk_callback=None,
        max_chars: int = 280,
    ) -> str:
        """Translate *text* by splitting it into sentence-level chunks.

        Useful for long block comments where a single Ollama call may give
        poor results or hit token limits.

        Parameters
        ----------
        text:
            Full comment text to translate.
        chunk_callback:
            Optional ``(chunk_index: int, total: int, partial: str) -> None``
            called after each chunk is translated with the accumulated result
            so far.  Useful for live progress in the GUI.
        max_chars:
            Soft per-chunk character limit passed to the splitter.
        """
        from translate_comments.splitter import split_for_translation, join_translations

        chunks = split_for_translation(text, max_chars=max_chars)
        if not chunks:
            return text

        translated_chunks: list[str] = []
        for idx, chunk in enumerate(chunks):
            try:
                result = self.translate(chunk)
            except TranslationError:
                result = chunk   # fall back to original for this chunk
            translated_chunks.append(result)
            if chunk_callback:
                partial = join_translations(translated_chunks, text)
                chunk_callback(idx, len(chunks), partial)

        return join_translations(translated_chunks, text)

    def translate_batch_sequential(self, texts: list[str]) -> list[str]:
        """Translate a list of texts sequentially (no batching).

        Failed translations are replaced with the original text.
        Prefer :meth:`translate_batch` for better performance.
        """
        results: list[str] = []
        for text in texts:
            results.append(self._translate_safe(text))
        return results

    def generate(
        self,
        prompt: str,
        system: str = "",
        chunk_callback=None,
    ) -> str:
        """Call Ollama with an arbitrary *system* + *prompt* (not translation).

        Used by :mod:`translate_comments.comment_generator` to produce
        Doxygen comments.  Optionally calls ``chunk_callback(partial_text)``
        with streamed tokens when using streaming mode (currently non-streaming
        for simplicity).
        """
        messages: list[dict] = []
        if system:
            messages.append({"role": "system", "content": system})
        messages.append({"role": "user", "content": prompt})

        payload: dict = {
            "model": self.model,
            "stream": False,
            "messages": messages,
            "options": {
                "temperature": 0.2,
                "num_predict": 1024,
            },
        }
        response = self._post_with_retry("/api/chat", payload)
        return self._parse_chat_response(response)

    def check_connection(self) -> tuple[bool, str]:
        """Ping Ollama and verify the model is available.

        Returns ``(ok, message)``.
        """
        try:
            resp = self._session.get(
                f"{self.host}/api/tags", timeout=10
            )
            resp.raise_for_status()
            data = resp.json()
            models = [m["name"] for m in data.get("models", [])]
            # Accept prefix match so "qwen2.5:7b" matches "qwen2.5:7b-instruct"
            match = any(m.startswith(self.model.split(":")[0]) for m in models)
            if not match:
                available = ", ".join(models) if models else "(none)"
                return (
                    False,
                    f"Model '{self.model}' not found. Available: {available}",
                )
            return True, f"Connected to Ollama at {self.host}, model '{self.model}' ready."
        except requests.ConnectionError:
            return False, f"Cannot connect to Ollama at {self.host}."
        except Exception as exc:  # noqa: BLE001
            return False, f"Ollama check failed: {exc}"

    def list_models(self) -> list[str]:
        """Return model names available from the Ollama host."""
        try:
            resp = self._session.get(f"{self.host}/api/tags", timeout=10)
            resp.raise_for_status()
            data = resp.json()
        except requests.ConnectionError as exc:
            raise TranslationError(f"Cannot connect to Ollama at {self.host}.") from exc
        except requests.RequestException as exc:
            raise TranslationError(f"Failed to fetch models from Ollama: {exc}") from exc
        except json.JSONDecodeError as exc:
            raise TranslationError(f"Invalid JSON from Ollama: {exc}") from exc

        models = [m.get("name", "").strip() for m in data.get("models", [])]
        return [name for name in models if name]

    def cache_stats(self) -> dict[str, int]:
        """Return current cache statistics."""
        return {"cached_entries": len(self._cache)}

    def clear_cache(self) -> None:
        """Clear the in-memory translation cache."""
        self._cache.clear()

    # ------------------------------------------------------------------ #
    # Private helpers                                                      #
    # ------------------------------------------------------------------ #

    def _translate_safe(self, text: str) -> str:
        """Translate *text*, returning original on failure (never raises)."""
        try:
            return self.translate(text)
        except TranslationError:
            return text

    def _translate_batch_llm(self, texts: list[str]) -> list[str]:
        """Send *texts* as a single batched JSON request to Ollama.

        Returns a list of translations in the same order.  Falls back to
        individual translation for each item on any parse error.
        """
        n = len(texts)
        texts_json = json.dumps(texts, ensure_ascii=False)

        # Estimate output tokens: each input word → ~2 Chinese chars + overhead
        total_input_chars = sum(len(t) for t in texts)
        estimated_tokens = max(64, min(2048, int(total_input_chars * 3)))

        prompt = (
            f"Translate these {n} English source-code comments to Simplified Chinese.\n"
            f"Return ONLY a JSON array of exactly {n} translated strings, "
            f"in the same order as the input. No extra text.\n\n"
            f"Input: {texts_json}"
        )

        payload = {
            "model": self.model,
            "stream": False,
            "format": "json",
            "messages": [
                {"role": "system", "content": _BATCH_SYSTEM_PROMPT},
                {"role": "user",   "content": prompt},
            ],
            "options": {
                "temperature": 0.1,
                "num_predict": estimated_tokens,
            },
        }

        try:
            response = self._post_with_retry("/api/chat", payload)
            content = self._parse_chat_response(response)
            parsed = json.loads(content)

            # Direct array
            if isinstance(parsed, list) and len(parsed) == n:
                return [str(item).strip() for item in parsed]

            # Wrapped: {"translations": [...], "results": [...], ...}
            if isinstance(parsed, dict):
                for key in ("translations", "results", "output", "comments", "data"):
                    arr = parsed.get(key)
                    if isinstance(arr, list) and len(arr) == n:
                        return [str(item).strip() for item in arr]

        except Exception:  # noqa: BLE001
            pass

        # Fallback: translate individually
        return [self._translate_safe(t) for t in texts]

    def _build_payload(
        self,
        text: str,
        stream: bool = False,
        *,
        context_before: str = "",
        context_after: str = "",
        related_text: str = "",
    ) -> dict:
        context_parts: list[str] = []
        if context_before.strip():
            context_parts.append(f"Source context before:\n{context_before.strip()}")
        if context_after.strip():
            context_parts.append(f"Source context after:\n{context_after.strip()}")
        if related_text.strip():
            context_parts.append(f"Full comment block context:\n{related_text.strip()}")
        context_block = "\n\n".join(context_parts).strip()
        if not context_block:
            context_block = "(no extra context)"

        # Adaptive token limit: size to ~3.5× estimated input tokens so short
        # comments don't burn time generating a 512-token budget they won't use.
        word_count = max(1, len(text.split()))
        num_predict = max(32, min(512, int(word_count * 3.5)))

        return {
            "model": self.model,
            "stream": stream,
            "messages": [
                {"role": "system", "content": _SYSTEM_PROMPT},
                {
                    "role": "user",
                    "content": _USER_TEMPLATE.format(
                        text=text,
                        context_block=context_block,
                    ),
                },
            ],
            "options": {
                "temperature": 0.1,   # low temperature → deterministic translation
                "num_predict": num_predict,
            },
        }

    def _post_with_retry(self, endpoint: str, payload: dict) -> requests.Response:
        url = f"{self.host}{endpoint}"
        last_exc: Exception | None = None
        for attempt in range(self.max_retries):
            try:
                resp = self._session.post(url, json=payload, timeout=self.timeout)
                resp.raise_for_status()
                return resp
            except (requests.ConnectionError, requests.Timeout) as exc:
                last_exc = exc
                if attempt < self.max_retries - 1:
                    time.sleep(self.retry_delay)
            except requests.HTTPError as exc:
                last_exc = exc
                status = exc.response.status_code if exc.response is not None else None
                if status is not None and status >= 500 and attempt < self.max_retries - 1:
                    time.sleep(self.retry_delay)
                    continue
                raise TranslationError(f"Ollama HTTP error: {exc}") from exc
            except requests.RequestException as exc:
                last_exc = exc
                raise TranslationError(f"Ollama request failed: {exc}") from exc
        raise TranslationError(
            f"Cannot reach Ollama at {self.host} after {self.max_retries} attempts: {last_exc}"
        )

    def _parse_chat_response(self, response: requests.Response) -> str:
        try:
            data = response.json()
        except json.JSONDecodeError as exc:
            raise TranslationError(f"Invalid JSON from Ollama: {exc}") from exc

        # /api/chat non-stream response
        message = data.get("message", {})
        content = message.get("content", "").strip()
        if not content:
            raise TranslationError(f"Empty translation response: {data}")
        return content
