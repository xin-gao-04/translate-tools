"""Ollama-backed translator.

Sends comment text to a local Ollama instance and returns the Chinese
translation.  The client is intentionally thin — it owns only the HTTP
interaction and prompt construction; all orchestration lives in
``processor.py``.

Ollama API reference: https://github.com/ollama/ollama/blob/main/docs/api.md
"""

from __future__ import annotations

import json
import time
from typing import Iterator

import requests


# ── Default values (all overridable via CLI) ─────────────────────────────────

DEFAULT_HOST = "http://localhost:11434"
DEFAULT_MODEL = "qwen2.5:7b"  # good Chinese↔English model available on Ollama
REQUEST_TIMEOUT = 120  # seconds

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

_USER_TEMPLATE = "Comment to translate:\n{text}"


class TranslationError(RuntimeError):
    """Raised when Ollama returns an error or is unreachable."""


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
    """

    def __init__(
        self,
        host: str = DEFAULT_HOST,
        model: str = DEFAULT_MODEL,
        timeout: int = REQUEST_TIMEOUT,
        max_retries: int = 3,
        retry_delay: float = 2.0,
    ) -> None:
        self.host = host.rstrip("/")
        self.model = model
        self.timeout = timeout
        self.max_retries = max_retries
        self.retry_delay = retry_delay
        self._session = requests.Session()

    # ------------------------------------------------------------------ #
    # Public interface                                                     #
    # ------------------------------------------------------------------ #

    def translate(self, text: str) -> str:
        """Translate a single *text* string and return the Chinese result.

        Raises ``TranslationError`` on failure.
        """
        payload = self._build_payload(text, stream=False)
        response = self._post_with_retry("/api/chat", payload)
        return self._parse_chat_response(response)

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

    def translate_batch(self, texts: list[str]) -> list[str]:
        """Translate a list of texts sequentially and return results in order.

        Failed translations are replaced with the original text so the
        process does not abort mid-file.
        """
        results: list[str] = []
        for text in texts:
            try:
                results.append(self.translate(text))
            except TranslationError:
                results.append(text)  # fall back to original
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

    # ------------------------------------------------------------------ #
    # Private helpers                                                      #
    # ------------------------------------------------------------------ #

    def _build_payload(self, text: str, stream: bool = False) -> dict:
        return {
            "model": self.model,
            "stream": stream,
            "messages": [
                {"role": "system", "content": _SYSTEM_PROMPT},
                {"role": "user", "content": _USER_TEMPLATE.format(text=text)},
            ],
            "options": {
                "temperature": 0.1,   # low temperature → deterministic translation
                "num_predict": 512,
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
            except requests.ConnectionError as exc:
                last_exc = exc
                if attempt < self.max_retries - 1:
                    time.sleep(self.retry_delay)
            except requests.HTTPError as exc:
                raise TranslationError(f"Ollama HTTP error: {exc}") from exc
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
