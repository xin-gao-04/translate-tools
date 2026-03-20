from fastapi.testclient import TestClient

from translate_comments.api import app
from translate_comments.translator import TranslationError, sanitize_translation_output


client = TestClient(app)


def test_analyze_header_returns_symbols_and_implementation(tmp_path) -> None:
    header = tmp_path / "widget.hpp"
    impl = tmp_path / "widget.cpp"

    header.write_text(
        """
namespace demo {
class Widget {
public:
    int value_;
    void run();
};
}
""".strip()
        + "\n",
        encoding="utf-8",
    )
    impl.write_text(
        """
#include "widget.hpp"

void demo::Widget::run() {
    value_ += 1;
}
""".strip()
        + "\n",
        encoding="utf-8",
    )

    response = client.post("/api/analyze-header", json={"path": str(header)})
    assert response.status_code == 200
    payload = response.json()

    symbols = {(item["name"], item["kind"]): item for item in payload["symbols"]}
    assert ("value_", "variable") in symbols
    assert symbols[("run", "function")]["implementation_path"] == str(impl)
    assert "Widget::run" in symbols[("run", "function")]["implementation_snippet"]


def test_preview_comments_returns_unified_diff(tmp_path) -> None:
    header = tmp_path / "widget.hpp"
    header.write_text(
        """
class Widget {
public:
    void run();
};
""".strip()
        + "\n",
        encoding="utf-8",
    )

    analysis = client.post("/api/analyze-header", json={"path": str(header)}).json()
    run_symbol = next(item for item in analysis["symbols"] if item["name"] == "run")
    preview = client.post(
        "/api/preview-comments",
        json={
            "path": str(header),
            "comments": {
                str(run_symbol["line_start"]): "/**\n * @brief Runs the widget.\n */",
            },
            "replace_existing": False,
        },
    )

    assert preview.status_code == 200
    payload = preview.json()
    assert payload["ok"] is True
    assert "@brief Runs the widget." in payload["preview"]
    assert "@brief Runs the widget." in payload["diff"]


def test_ws_translation_stops_after_untranslated_comment_and_preserves_context(tmp_path, monkeypatch) -> None:
    first = tmp_path / "first.cpp"
    second = tmp_path / "second.cpp"
    first.write_text(
        """
// Translate the first comment.
int first_value = 1;
// Fail this second comment.
int second_value = 2;
""".strip()
        + "\n",
        encoding="utf-8",
    )
    second.write_text(
        """
// This file should not start.
int value = 3;
""".strip()
        + "\n",
        encoding="utf-8",
    )

    class FakeTranslator:
        calls: list[tuple[str, str, str, str]] = []

        def __init__(self, host: str, model: str) -> None:
            self.host = host
            self.model = model

        def translate(
            self,
            text: str,
            *,
            context_before: str = "",
            context_after: str = "",
            related_text: str = "",
        ) -> str:
            self.__class__.calls.append((text, context_before, context_after, related_text))
            if "Fail this second comment." in text:
                raise TranslationError("mock translation failure")
            return f"ZH:{text}"

    monkeypatch.setattr("translate_comments.api.OllamaTranslator", FakeTranslator)

    events: list[dict] = []
    with client.websocket_connect("/ws") as ws:
        ws.send_json({
            "paths": [str(first), str(second)],
            "host": "http://localhost:11434",
            "model": "fake",
            "chunk_threshold": 120,
        })
        while True:
            event = ws.receive_json()
            events.append(event)
            if event["type"] == "all_done":
                break

    first_done = next(event for event in events if event["type"] == "file_done")
    assert first_done["path"] == str(first)
    assert first_done["untranslated"] == 1
    assert not any(event.get("path") == str(second) and event["type"] == "file_started" for event in events)
    assert any(event["type"] == "comment_failed" for event in events)
    assert "int first_value = 1;" in FakeTranslator.calls[0][2]


def test_sanitize_translation_output_strips_prompt_echo() -> None:
    raw = """
Use the surrounding source context only to disambiguate meaning.
Translate only the comment text itself.

初始化控件
""".strip()

    assert sanitize_translation_output(raw, "Initialize the widget") == "初始化控件"


def test_sanitize_translation_output_keeps_normal_translation() -> None:
    raw = "计算所有可见项目的包围盒"
    assert sanitize_translation_output(raw, "Compute the bounding box for all visible items") == raw
