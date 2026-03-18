"""Graphical front-end for translate-comments.

Launch:
    translate-comments-gui
    python -m translate_comments.gui
"""

from __future__ import annotations

import sys
from pathlib import Path

from PySide6.QtCore import Qt, QThread, Signal, QSize
from PySide6.QtGui import (
    QBrush, QColor, QDragEnterEvent, QDropEvent,
    QFont, QFontDatabase, QPalette,
)
from PySide6.QtWidgets import (
    QAbstractItemView, QApplication, QComboBox, QFileDialog,
    QHBoxLayout, QHeaderView, QLabel, QLineEdit, QMainWindow,
    QMessageBox, QProgressBar, QPushButton, QSizePolicy,
    QSplitter, QTableWidget, QTableWidgetItem, QToolBar,
    QTreeWidget, QTreeWidgetItem, QVBoxLayout, QWidget,
)

from translate_comments.detector import is_english
from translate_comments.parsers import get_parser
from translate_comments.parsers.base import Comment
from translate_comments.scanner import FileScanner
from translate_comments.translator import OllamaTranslator, TranslationError

# ── Status constants ──────────────────────────────────────────────────────────

PENDING = "pending"
RUNNING = "running"
DONE    = "done"
SKIPPED = "skipped"
ERROR   = "error"

_ICON  = {PENDING: "○", RUNNING: "◐", DONE: "●", SKIPPED: "—", ERROR: "✗"}
_COLOR = {
    PENDING: "#64748B",
    RUNNING: "#3B82F6",
    DONE:    "#10B981",
    SKIPPED: "#94A3B8",
    ERROR:   "#EF4444",
}

COL_LINE, COL_STYLE, COL_ORIGINAL, COL_TRANSLATED, COL_STATUS = range(5)

# ── Threshold above which chunked translation is used ────────────────────────
CHUNK_THRESHOLD = 120   # characters

# ── Global stylesheet ─────────────────────────────────────────────────────────

_QSS = """
/* ───────────────── App base ───────────────── */
QMainWindow { background: #0F1923; }
QWidget      { font-family: "SF Pro Text", "PingFang SC", "Segoe UI", sans-serif; }

/* ───────────────── Top toolbar ───────────────── */
QToolBar {
    background: #1A2535;
    border-bottom: 1px solid #0A1018;
    spacing: 6px;
    padding: 5px 14px;
}
QToolBar QLabel {
    color: #7B93AD;
    font-size: 12px;
}
QToolBar QLineEdit {
    background: #0F1923;
    border: 1px solid #2A3F55;
    border-radius: 5px;
    color: #C8DCF0;
    padding: 4px 10px;
    font-size: 12px;
    selection-background-color: #2D6A9F;
}
QToolBar QLineEdit:focus { border-color: #3B82F6; }
QToolBar QComboBox {
    background: #0F1923;
    border: 1px solid #2A3F55;
    border-radius: 5px;
    color: #C8DCF0;
    padding: 4px 10px;
    font-size: 12px;
}
QToolBar QComboBox::drop-down { border: none; width: 22px; }
QToolBar QComboBox QAbstractItemView {
    background: #1A2535;
    border: 1px solid #2A3F55;
    color: #C8DCF0;
    selection-background-color: #1E4976;
}

/* ───────────────── Left sidebar ───────────────── */
#sideBar {
    background: #131F2E;
    border-right: 1px solid #0A1018;
}
#sideTitle {
    color: #4A6582;
    font-size: 10px;
    font-weight: bold;
    letter-spacing: 1.5px;
    padding: 6px 12px 2px 12px;
}
QTreeWidget {
    background: transparent;
    border: none;
    outline: none;
    color: #A8C0D6;
    font-size: 13px;
    show-decoration-selected: 1;
}
QTreeWidget::item {
    padding: 5px 8px;
    border-radius: 4px;
}
QTreeWidget::item:selected {
    background: #1E3A55;
    color: #E2F0FF;
}
QTreeWidget::item:hover:!selected { background: #192840; }
QTreeWidget QHeaderView::section {
    background: #0F1923;
    color: #3D5870;
    border: none;
    border-bottom: 1px solid #0A1018;
    font-size: 11px;
    padding: 4px 8px;
}

/* ───────────────── Drop zone ───────────────── */
#dropZone {
    color: #3D5870;
    font-size: 13px;
    border: 2px dashed #1E3A55;
    border-radius: 10px;
    background: #0F1923;
    padding: 24px;
    qproperty-alignment: AlignCenter;
}

/* ───────────────── Sidebar buttons ───────────────── */
QPushButton#btnAdd {
    background: #1E3A55;
    color: #7EB8E8;
    border: none;
    border-radius: 5px;
    padding: 0 10px;
    font-size: 12px;
}
QPushButton#btnAdd:hover    { background: #274F72; color: #B0D8F8; }
QPushButton#btnClear {
    background: #131F2E;
    color: #3D5870;
    border: 1px solid #1E3050;
    border-radius: 5px;
    padding: 0 10px;
    font-size: 12px;
}
QPushButton#btnClear:hover  { background: #1A2A3E; color: #5A7A9A; }

/* ───────────────── Right content panel ───────────────── */
#contentPanel {
    background: #FFFFFF;
    border-radius: 0;
}
#filePathLabel {
    color: #64748B;
    font-size: 12px;
    padding: 6px 10px 2px 10px;
    background: #F8FAFC;
    border-bottom: 1px solid #E2E8F0;
}

/* ───────────────── Comment table ───────────────── */
QTableWidget {
    background: #FFFFFF;
    alternate-background-color: #F8FAFC;
    border: none;
    gridline-color: transparent;
    font-size: 13px;
    selection-background-color: #EFF6FF;
    selection-color: #1E40AF;
    outline: none;
}
QTableWidget::item {
    padding: 5px 8px;
    border-bottom: 1px solid #F1F5F9;
    color: #334155;
}
QTableWidget::item:selected { background: #EFF6FF; color: #1E40AF; }
QTableWidget QHeaderView::section {
    background: #F1F5F9;
    color: #64748B;
    border: none;
    border-bottom: 2px solid #CBD5E1;
    font-size: 11px;
    font-weight: bold;
    letter-spacing: 0.5px;
    padding: 6px 8px;
}

/* ───────────────── Progress bars ───────────────── */
QProgressBar {
    border: none;
    border-radius: 3px;
    background: #E2E8F0;
}
QProgressBar::chunk { border-radius: 3px; }
#fileBar::chunk     { background: #3B82F6; }
#totalBar::chunk    { background: #10B981; }

/* ───────────────── Bottom bar ───────────────── */
#bottomBar {
    background: #1A2535;
    border-top: 1px solid #0A1018;
}
#bottomBar QLabel {
    color: #7B93AD;
    font-size: 12px;
}

/* ───────────────── Action buttons ───────────────── */
QPushButton#btnStart {
    background: qlineargradient(x1:0,y1:0,x2:0,y2:1,
                stop:0 #3B82F6, stop:1 #2563EB);
    color: white;
    border: none;
    border-radius: 6px;
    font-weight: bold;
    font-size: 13px;
}
QPushButton#btnStart:hover    { background: #2563EB; }
QPushButton#btnStart:disabled { background: #475569; color: #94A3B8; }
QPushButton#btnStop {
    background: #1E293B;
    color: #EF4444;
    border: 1px solid #EF4444;
    border-radius: 6px;
    font-size: 13px;
}
QPushButton#btnStop:hover    { background: #3B0000; color: #FCA5A5; }
QPushButton#btnStop:disabled { background: #1E293B; color: #475569; border-color: #334155; }
QPushButton#btnApply {
    background: #1E3A2F;
    color: #10B981;
    border: 1px solid #10B981;
    border-radius: 6px;
    font-size: 12px;
}
QPushButton#btnApply:hover    { background: #10B981; color: #ffffff; }
QPushButton#btnApply:disabled { background: #1E293B; color: #334155; border-color: #334155; }

/* ───────────────── Check button ───────────────── */
QPushButton#btnCheck {
    background: #1E3A55;
    color: #7EB8E8;
    border: none;
    border-radius: 5px;
    padding: 0 12px;
    font-size: 12px;
}
QPushButton#btnCheck:hover { background: #274F72; }

/* ───────────────── Scrollbars ───────────────── */
QScrollBar:vertical {
    background: transparent; width: 8px; border: none;
}
QScrollBar::handle:vertical {
    background: #CBD5E1; border-radius: 4px; min-height: 20px;
}
QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical { height: 0; }
QScrollBar:horizontal {
    background: transparent; height: 8px; border: none;
}
QScrollBar::handle:horizontal {
    background: #CBD5E1; border-radius: 4px; min-width: 20px;
}
QScrollBar::add-line:horizontal, QScrollBar::sub-line:horizontal { width: 0; }
"""


# ── Worker ────────────────────────────────────────────────────────────────────

class TranslationWorker(QThread):
    """Translates files one-by-one, using chunked translation for long texts."""

    sig_file_started    = Signal(str, int)        # path, english_count
    sig_file_done       = Signal(str, int, int)   # path, translated, skipped
    sig_file_error      = Signal(str, str)         # path, message
    sig_comment_started = Signal(str, int, int)   # path, lineno, chunk_total
    sig_comment_chunk   = Signal(str, int, str)   # path, lineno, partial_text
    sig_comment_done    = Signal(str, int, str)   # path, lineno, final_text
    sig_all_done        = Signal(int, int, int)   # files, translated, errors
    sig_log             = Signal(str)

    def __init__(
        self,
        files: list[Path],
        host: str,
        model: str,
        output_mode: str,
        parent=None,
    ):
        super().__init__(parent)
        self._files       = files
        self._host        = host
        self._model       = model
        self._output_mode = output_mode
        self._stop        = False

    def stop(self) -> None:
        self._stop = True

    def run(self) -> None:  # noqa: C901
        from translate_comments.splitter import split_for_translation

        translator       = OllamaTranslator(host=self._host, model=self._model)
        total_translated = 0
        total_errors     = 0

        for path in self._files:
            if self._stop:
                break

            try:
                source = path.read_text(encoding="utf-8", errors="replace")
            except OSError as exc:
                self.sig_file_error.emit(str(path), str(exc))
                total_errors += 1
                continue

            parser = get_parser(path.suffix)
            if not parser:
                self.sig_file_error.emit(str(path), f"无解析器: {path.suffix}")
                total_errors += 1
                continue

            all_comments = parser.extract_comments(source)
            english      = [c for c in all_comments if is_english(c.text)]
            skipped      = len(all_comments) - len(english)

            self.sig_file_started.emit(str(path), len(english))

            if not english:
                self.sig_file_done.emit(str(path), 0, skipped)
                continue

            translations: dict[int, str] = {}
            file_translated = 0

            for c in english:
                if self._stop:
                    break

                # Decide: single call or chunked?
                chunks = split_for_translation(c.text) if len(c.text) > CHUNK_THRESHOLD else []
                chunk_total = max(len(chunks), 1)
                self.sig_comment_started.emit(str(path), c.line_start, chunk_total)

                try:
                    if chunks:
                        result = translator.translate_chunked(
                            c.text,
                            chunk_callback=lambda idx, tot, partial, ln=c.line_start, p=str(path):
                                self.sig_comment_chunk.emit(p, ln, partial),
                        )
                    else:
                        result = translator.translate(c.text)

                    translations[c.line_start] = result
                    self.sig_comment_done.emit(str(path), c.line_start, result)
                    file_translated  += 1
                    total_translated += 1

                except TranslationError as exc:
                    self.sig_log.emit(f"翻译失败 L{c.line_start} ({path.name}): {exc}")

            if self._output_mode == "inplace" and translations:
                try:
                    new_source = parser.replace_comments(source, translations)
                    path.write_text(new_source, encoding="utf-8")
                except OSError as exc:
                    self.sig_log.emit(f"写回失败 {path}: {exc}")

            self.sig_file_done.emit(str(path), file_translated, skipped)

        self.sig_all_done.emit(len(self._files), total_translated, total_errors)


# ── Drop zone ─────────────────────────────────────────────────────────────────

class DropZone(QLabel):
    files_dropped = Signal(list)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("dropZone")
        self.setAcceptDrops(True)
        self.setAlignment(Qt.AlignCenter)
        self.setText("拖放文件或文件夹\n到此处\n\n或点击「添加」按钮")
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)

    def dragEnterEvent(self, e: QDragEnterEvent) -> None:
        if e.mimeData().hasUrls():
            e.acceptProposedAction()
            self.setStyleSheet(
                "#dropZone { color:#3B82F6; border-color:#3B82F6; background:#0A1828; }"
            )

    def dragLeaveEvent(self, _e) -> None:
        self.setStyleSheet("")

    def dropEvent(self, e: QDropEvent) -> None:
        self.setStyleSheet("")
        self.files_dropped.emit([u.toLocalFile() for u in e.mimeData().urls()])


# ── Left sidebar ──────────────────────────────────────────────────────────────

class FilePanel(QWidget):
    file_selected = Signal(str)

    _EXTS = [".cpp",".cxx",".cc",".c",".h",".hpp",".hxx",".hh",".inl",".ipp"]

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("sideBar")
        self.setAcceptDrops(True)
        self._items: dict[str, QTreeWidgetItem] = {}
        self._build()

    def _build(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        title = QLabel("FILES")
        title.setObjectName("sideTitle")
        root.addWidget(title)

        self._zone = DropZone()
        self._zone.files_dropped.connect(self.add_paths)
        self._zone.setContentsMargins(8, 0, 8, 0)
        root.addWidget(self._zone)

        self._tree = QTreeWidget()
        self._tree.setHeaderLabels(["文件", "", ""])
        hdr = self._tree.header()
        hdr.setSectionResizeMode(0, QHeaderView.Stretch)
        hdr.setSectionResizeMode(1, QHeaderView.ResizeToContents)
        hdr.setSectionResizeMode(2, QHeaderView.ResizeToContents)
        hdr.setMinimumSectionSize(20)
        self._tree.setRootIsDecorated(False)
        self._tree.setColumnWidth(1, 22)
        self._tree.setColumnWidth(2, 30)
        self._tree.itemClicked.connect(
            lambda item, _: self.file_selected.emit(item.data(0, Qt.UserRole) or "")
        )
        self._tree.setVisible(False)
        root.addWidget(self._tree)

        # Buttons
        btn_row = QHBoxLayout()
        btn_row.setContentsMargins(8, 6, 8, 6)
        btn_row.setSpacing(6)
        b_add = QPushButton("＋ 添加")
        b_add.setObjectName("btnAdd")
        b_add.setFixedHeight(28)
        b_add.clicked.connect(self._browse)
        b_clr = QPushButton("清空")
        b_clr.setObjectName("btnClear")
        b_clr.setFixedHeight(28)
        b_clr.clicked.connect(self.clear)
        btn_row.addWidget(b_add)
        btn_row.addWidget(b_clr)
        root.addLayout(btn_row)

    # ── Public ────────────────────────────────────────────────────────────

    def add_paths(self, paths: list[str]) -> None:
        scanner = FileScanner(extensions=self._EXTS)
        found: list[Path] = []
        for p in paths:
            try:
                found.extend(scanner.scan(p))
            except FileNotFoundError:
                pass
        if not found:
            QMessageBox.information(self, "无匹配文件", "未发现 C/C++ 源文件。")
            return
        for f in found:
            key = str(f)
            if key in self._items:
                continue
            item = QTreeWidgetItem([f.name, _ICON[PENDING], ""])
            item.setData(0, Qt.UserRole, key)
            item.setForeground(1, QBrush(QColor(_COLOR[PENDING])))
            item.setToolTip(0, key)
            self._tree.addTopLevelItem(item)
            self._items[key] = item
        self._zone.setVisible(False)
        self._tree.setVisible(True)

    def clear(self) -> None:
        self._tree.clear()
        self._items.clear()
        self._tree.setVisible(False)
        self._zone.setVisible(True)

    def all_files(self) -> list[Path]:
        return [Path(p) for p in self._items]

    def set_status(self, path: str, status: str, note: str = "") -> None:
        item = self._items.get(path)
        if not item:
            return
        item.setText(1, _ICON[status])
        item.setForeground(1, QBrush(QColor(_COLOR[status])))
        if note:
            item.setText(2, note)

    def dragEnterEvent(self, e: QDragEnterEvent) -> None:
        if e.mimeData().hasUrls():
            e.acceptProposedAction()

    def dropEvent(self, e: QDropEvent) -> None:
        self.add_paths([u.toLocalFile() for u in e.mimeData().urls()])

    def _browse(self) -> None:
        p = QFileDialog.getExistingDirectory(self, "选择文件夹")
        if p:
            self.add_paths([p])


# ── Comment preview (right panel) ────────────────────────────────────────────

class PreviewPanel(QWidget):
    _STYLES = {"line": "//", "block": "/* */", "doc": "/**"}

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("contentPanel")
        self._row_key: dict[tuple[str, int], int] = {}
        self._build()

    def _build(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        self._path_label = QLabel("— 在左侧点击文件查看注释 —")
        self._path_label.setObjectName("filePathLabel")
        root.addWidget(self._path_label)

        self._table = QTableWidget(0, 5)
        self._table.setHorizontalHeaderLabels(["行号", "类型", "原文", "译文", ""])
        h = self._table.horizontalHeader()
        h.setSectionResizeMode(COL_LINE,       QHeaderView.ResizeToContents)
        h.setSectionResizeMode(COL_STYLE,      QHeaderView.ResizeToContents)
        h.setSectionResizeMode(COL_ORIGINAL,   QHeaderView.Stretch)
        h.setSectionResizeMode(COL_TRANSLATED, QHeaderView.Stretch)
        h.setSectionResizeMode(COL_STATUS,     QHeaderView.Fixed)
        self._table.setColumnWidth(COL_STATUS, 28)
        self._table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self._table.setAlternatingRowColors(True)
        self._table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self._table.verticalHeader().setVisible(False)
        self._table.setShowGrid(False)
        self._table.setWordWrap(False)
        self._table.verticalHeader().setDefaultSectionSize(32)
        root.addWidget(self._table)

        # File progress bar
        prog_row = QHBoxLayout()
        prog_row.setContentsMargins(10, 4, 10, 4)
        prog_row.setSpacing(8)

        lbl = QLabel("进度")
        lbl.setStyleSheet("color:#94A3B8; font-size:11px;")
        prog_row.addWidget(lbl)

        self._file_bar = QProgressBar()
        self._file_bar.setObjectName("fileBar")
        self._file_bar.setFixedHeight(6)
        self._file_bar.setTextVisible(False)
        prog_row.addWidget(self._file_bar)

        self._file_lbl = QLabel("0 / 0")
        self._file_lbl.setStyleSheet("color:#64748B; font-size:11px; min-width:50px;")
        prog_row.addWidget(self._file_lbl)

        prog_widget = QWidget()
        prog_widget.setStyleSheet("background:#F8FAFC; border-top:1px solid #E2E8F0;")
        prog_widget.setFixedHeight(24)
        prog_widget.setLayout(prog_row)
        root.addWidget(prog_widget)

    # ── Public ────────────────────────────────────────────────────────────

    def load_file(
        self,
        path: str,
        comments: list[Comment],
        cached: dict[int, str] | None = None,
    ) -> None:
        self._path_label.setText(f"  {path}")
        self._table.setRowCount(0)
        self._row_key = {k: v for k, v in self._row_key.items() if k[0] != path}

        eng_total = 0
        cached = cached or {}

        for c in comments:
            row = self._table.rowCount()
            self._table.insertRow(row)

            eng    = is_english(c.text)
            status = (DONE if c.line_start in cached else PENDING) if eng else SKIPPED
            disp   = c.text.replace("\n", " ↵ ")

            ln = QTableWidgetItem(f"L{c.line_start}")
            ln.setTextAlignment(Qt.AlignCenter)
            ln.setForeground(QBrush(QColor("#94A3B8")))

            st = QTableWidgetItem(self._STYLES.get(c.style, c.style))
            st.setTextAlignment(Qt.AlignCenter)
            st.setForeground(QBrush(QColor("#CBD5E1" if not eng else "#64748B")))

            orig = QTableWidgetItem(disp[:160])
            orig.setToolTip(c.text)
            orig.setForeground(QBrush(QColor("#475569" if not eng else "#1E293B")))

            if not eng:
                tr_txt, tr_col = "—", "#CBD5E1"
            elif c.line_start in cached:
                d = cached[c.line_start].replace("\n", " ↵ ")
                tr_txt, tr_col = d[:160], "#1E293B"
            else:
                tr_txt, tr_col = "", "#94A3B8"

            tr = QTableWidgetItem(tr_txt)
            tr.setForeground(QBrush(QColor(tr_col)))
            if c.line_start in cached:
                tr.setToolTip(cached.get(c.line_start, ""))

            ico = QTableWidgetItem(_ICON[status])
            ico.setTextAlignment(Qt.AlignCenter)
            ico.setForeground(QBrush(QColor(_COLOR[status])))

            for col, item in enumerate([ln, st, orig, tr, ico]):
                self._table.setItem(row, col, item)

            if eng:
                self._row_key[(path, c.line_start)] = row
                eng_total += 1

        done = sum(1 for (p, ln_) in self._row_key if p == path and ln_ in cached)
        self._file_bar.setMaximum(max(eng_total, 1))
        self._file_bar.setValue(done)
        self._file_lbl.setText(f"{done} / {eng_total}")

    def mark_translating(self, path: str, lineno: int, chunk_total: int) -> None:
        row = self._row_key.get((path, lineno))
        if row is None:
            return
        label = "翻译中…" if chunk_total <= 1 else f"翻译中 0/{chunk_total}…"
        self._set_tr(row, label, "#3B82F6")
        self._set_ico(row, RUNNING)
        self._table.scrollToItem(self._table.item(row, 0))

    def update_chunk(self, path: str, lineno: int, partial: str) -> None:
        """Show partial (incremental) translation text while chunks arrive."""
        row = self._row_key.get((path, lineno))
        if row is None:
            return
        disp = partial.replace("\n", " ↵ ")
        self._set_tr(row, disp[:160] + "…", "#3B82F6")

    def mark_done(self, path: str, lineno: int, translated: str) -> None:
        row = self._row_key.get((path, lineno))
        if row is None:
            return
        disp = translated.replace("\n", " ↵ ")
        item = QTableWidgetItem(disp[:160])
        item.setForeground(QBrush(QColor("#1E293B")))
        item.setToolTip(translated)
        self._table.setItem(row, COL_TRANSLATED, item)
        self._set_ico(row, DONE)
        # Update progress bar
        done  = sum(1 for (p, _), r in self._row_key.items()
                    if p == path and self._row_ico(r) == DONE)
        total = sum(1 for (p, _) in self._row_key if p == path)
        self._file_bar.setValue(done)
        self._file_lbl.setText(f"{done} / {total}")

    # ── Internal ─────────────────────────────────────────────────────────

    def _set_tr(self, row: int, text: str, color: str) -> None:
        item = QTableWidgetItem(text)
        item.setForeground(QBrush(QColor(color)))
        self._table.setItem(row, COL_TRANSLATED, item)

    def _set_ico(self, row: int, status: str) -> None:
        ico = QTableWidgetItem(_ICON[status])
        ico.setTextAlignment(Qt.AlignCenter)
        ico.setForeground(QBrush(QColor(_COLOR[status])))
        self._table.setItem(row, COL_STATUS, ico)

    def _row_ico(self, row: int) -> str:
        item = self._table.item(row, COL_STATUS)
        if not item:
            return PENDING
        for s, icon in _ICON.items():
            if icon == item.text():
                return s
        return PENDING


# ── Main window ───────────────────────────────────────────────────────────────

class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("translate-comments")
        self.setMinimumSize(1100, 660)
        self.resize(1320, 780)

        self._worker:       TranslationWorker | None = None
        self._all_comments: dict[str, list[Comment]] = {}
        self._translations: dict[str, dict[int, str]] = {}
        self._files_done       = 0
        self._total_translated = 0

        self.setStyleSheet(_QSS)
        self._build_toolbar()
        self._build_central()
        self._build_bottom()
        self.statusBar().setStyleSheet(
            "QStatusBar { background:#131F2E; color:#4A6582; font-size:12px; border-top:1px solid #0A1018; }"
        )
        self.statusBar().showMessage("就绪 — 拖放文件夹或点击侧栏「添加」开始")

    # ── Build ─────────────────────────────────────────────────────────────

    def _build_toolbar(self) -> None:
        bar = QToolBar()
        bar.setMovable(False)
        bar.setIconSize(QSize(14, 14))
        self.addToolBar(bar)

        bar.addWidget(QLabel("  Host:"))
        self._host_edit = QLineEdit("http://localhost:11434")
        self._host_edit.setFixedWidth(210)
        bar.addWidget(self._host_edit)

        bar.addSeparator()
        bar.addWidget(QLabel("模型:"))
        self._model_combo = QComboBox()
        self._model_combo.setEditable(True)
        self._model_combo.addItems(
            ["qwen2.5:7b", "qwen2.5:14b", "qwen2.5:32b", "llama3.2", "mistral"]
        )
        self._model_combo.setFixedWidth(175)
        bar.addWidget(self._model_combo)

        bar.addSeparator()
        bar.addWidget(QLabel("输出:"))
        self._output_combo = QComboBox()
        self._output_combo.addItems(
            ["inplace — 覆写原文件", "stdout — 打印终端", "diff — 仅差异"]
        )
        self._output_combo.setFixedWidth(180)
        bar.addWidget(self._output_combo)

        bar.addSeparator()
        chk = QPushButton("🔗  检查连接")
        chk.setObjectName("btnCheck")
        chk.setFixedHeight(26)
        chk.clicked.connect(self._check_connection)
        bar.addWidget(chk)
        self._check_btn = chk

    def _build_central(self) -> None:
        central = QWidget()
        central.setStyleSheet("background:#0F1923;")
        self.setCentralWidget(central)
        lay = QVBoxLayout(central)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(0)

        splitter = QSplitter(Qt.Horizontal)
        splitter.setHandleWidth(1)
        splitter.setStyleSheet("QSplitter::handle { background:#0A1018; }")

        self._file_panel = FilePanel()
        self._file_panel.setFixedWidth(230)
        self._file_panel.file_selected.connect(self._show_preview)
        splitter.addWidget(self._file_panel)

        self._preview = PreviewPanel()
        splitter.addWidget(self._preview)

        splitter.setStretchFactor(0, 0)
        splitter.setStretchFactor(1, 1)
        lay.addWidget(splitter)

    def _build_bottom(self) -> None:
        bar = QWidget()
        bar.setObjectName("bottomBar")
        bar.setFixedHeight(48)
        lay = QHBoxLayout(bar)
        lay.setContentsMargins(14, 0, 14, 0)
        lay.setSpacing(10)

        lay.addWidget(QLabel("总进度"))

        self._total_bar = QProgressBar()
        self._total_bar.setObjectName("totalBar")
        self._total_bar.setFixedHeight(6)
        self._total_bar.setTextVisible(False)
        lay.addWidget(self._total_bar)

        self._stats_lbl = QLabel("0 / 0 文件    0 条已翻译")
        self._stats_lbl.setStyleSheet("color:#4A6582; font-size:12px; min-width:200px;")
        lay.addWidget(self._stats_lbl)

        lay.addStretch()

        # "Apply to files" — enabled when cached translations exist and
        # current output mode is NOT inplace (so the user can apply later)
        self._apply_btn = QPushButton("💾  应用到文件")
        self._apply_btn.setObjectName("btnApply")
        self._apply_btn.setFixedSize(130, 34)
        self._apply_btn.setEnabled(False)
        self._apply_btn.setToolTip("将已完成的翻译写回源文件（不需要重新翻译）")
        self._apply_btn.clicked.connect(self._apply_translations)
        lay.addWidget(self._apply_btn)

        self._start_btn = QPushButton("▶  开始翻译")
        self._start_btn.setObjectName("btnStart")
        self._start_btn.setFixedSize(120, 34)
        self._start_btn.clicked.connect(self._start)
        lay.addWidget(self._start_btn)

        self._stop_btn = QPushButton("■  停止")
        self._stop_btn.setObjectName("btnStop")
        self._stop_btn.setFixedSize(90, 34)
        self._stop_btn.setEnabled(False)
        self._stop_btn.clicked.connect(self._stop)
        lay.addWidget(self._stop_btn)

        # Attach below the existing central widget
        wrap = QWidget()
        wl = QVBoxLayout(wrap)
        wl.setContentsMargins(0, 0, 0, 0)
        wl.setSpacing(0)
        wl.addWidget(self.centralWidget())
        wl.addWidget(bar)
        self.setCentralWidget(wrap)

    # ── File preview ──────────────────────────────────────────────────────

    def _show_preview(self, path: str) -> None:
        if path not in self._all_comments:
            p = Path(path)
            parser = get_parser(p.suffix)
            if not parser:
                return
            try:
                source = p.read_text(encoding="utf-8", errors="replace")
                self._all_comments[path] = parser.extract_comments(source)
            except OSError:
                return
        self._preview.load_file(
            path,
            self._all_comments[path],
            cached=self._translations.get(path),
        )

    # ── Translation control ───────────────────────────────────────────────

    def _start(self) -> None:
        files = self._file_panel.all_files()
        if not files:
            QMessageBox.information(self, "无文件", "请先添加文件或文件夹。")
            return

        host   = self._host_edit.text().strip() or "http://localhost:11434"
        model  = self._model_combo.currentText().strip() or "qwen2.5:7b"
        output = {0: "inplace", 1: "stdout", 2: "diff"}.get(
            self._output_combo.currentIndex(), "inplace"
        )

        if output == "inplace":
            ans = QMessageBox.question(
                self, "确认覆写",
                f"将原地覆写 {len(files)} 个源文件。\n请确保已备份或在版本控制下。",
                QMessageBox.Ok | QMessageBox.Cancel,
            )
            if ans != QMessageBox.Ok:
                return

        self._files_done = self._total_translated = 0
        self._total_bar.setMaximum(len(files))
        self._total_bar.setValue(0)
        self._stats_lbl.setText(f"0 / {len(files)} 文件    0 条已翻译")
        self._start_btn.setEnabled(False)
        self._stop_btn.setEnabled(True)

        self._worker = TranslationWorker(files, host, model, output)
        w = self._worker
        w.sig_file_started.connect(self._on_file_started)
        w.sig_file_done.connect(self._on_file_done)
        w.sig_file_error.connect(self._on_file_error)
        w.sig_comment_started.connect(self._on_comment_started)
        w.sig_comment_chunk.connect(self._on_comment_chunk)
        w.sig_comment_done.connect(self._on_comment_done)
        w.sig_all_done.connect(self._on_all_done)
        w.sig_log.connect(self.statusBar().showMessage)
        w.start()

    def _stop(self) -> None:
        if self._worker:
            self._worker.stop()
        self._stop_btn.setEnabled(False)
        self.statusBar().showMessage("已停止")

    # ── Worker signal handlers ────────────────────────────────────────────

    def _on_file_started(self, path: str, eng_count: int) -> None:
        self._file_panel.set_status(path, RUNNING)
        self.statusBar().showMessage(
            f"翻译中: {Path(path).name}   ({eng_count} 条英文注释)"
        )
        if path not in self._all_comments:
            self._show_preview(path)
        self._preview.load_file(
            path,
            self._all_comments.get(path, []),
            cached=self._translations.get(path),
        )

    def _on_file_done(self, path: str, translated: int, skipped: int) -> None:
        self._file_panel.set_status(path, DONE, f"+{translated}" if translated else "—")
        self._files_done       += 1
        self._total_translated += translated
        self._total_bar.setValue(self._files_done)
        self._stats_lbl.setText(
            f"{self._files_done} / {self._total_bar.maximum()} 文件"
            f"    {self._total_translated} 条已翻译"
        )

    def _on_file_error(self, path: str, msg: str) -> None:
        self._file_panel.set_status(path, ERROR)
        self._files_done += 1
        self._total_bar.setValue(self._files_done)
        self.statusBar().showMessage(f"错误: {Path(path).name}: {msg}")

    def _on_comment_started(self, path: str, lineno: int, chunk_total: int) -> None:
        self._preview.mark_translating(path, lineno, chunk_total)

    def _on_comment_chunk(self, path: str, lineno: int, partial: str) -> None:
        self._preview.update_chunk(path, lineno, partial)

    def _on_comment_done(self, path: str, lineno: int, translated: str) -> None:
        self._translations.setdefault(path, {})[lineno] = translated
        self._preview.mark_done(path, lineno, translated)

    def _on_all_done(self, files: int, translated: int, errors: int) -> None:
        self._start_btn.setEnabled(True)
        self._stop_btn.setEnabled(False)
        # Enable "apply" when there are cached results AND output is not inplace
        output = {0: "inplace", 1: "stdout", 2: "diff"}.get(
            self._output_combo.currentIndex(), "inplace"
        )
        has_results = any(self._translations.values())
        self._apply_btn.setEnabled(has_results and output != "inplace")
        msg = f"✓  完成  —  {translated} 条注释已翻译（{files} 个文件）"
        if errors:
            msg += f"   {errors} 个错误"
        if has_results and output != "inplace":
            msg += "   · 点击「应用到文件」写回"
        self.statusBar().showMessage(msg)

    def _apply_translations(self) -> None:
        """Write all cached translations to their source files (no re-translation)."""
        if not self._translations:
            return
        ans = QMessageBox.question(
            self, "确认写回",
            f"将把翻译结果写回 {len(self._translations)} 个文件。\n请确保已备份。",
            QMessageBox.Ok | QMessageBox.Cancel,
        )
        if ans != QMessageBox.Ok:
            return

        applied = 0
        for path_str, trans in self._translations.items():
            if not trans:
                continue
            path = Path(path_str)
            parser = get_parser(path.suffix)
            if not parser:
                continue
            try:
                source = path.read_text(encoding="utf-8", errors="replace")
                new_source = parser.replace_comments(source, trans)
                path.write_text(new_source, encoding="utf-8")
                applied += 1
                self._file_panel.set_status(path_str, DONE, f"+{len(trans)}")
            except OSError as exc:
                self.statusBar().showMessage(f"写入失败: {path.name}: {exc}")
                return

        self._apply_btn.setEnabled(False)
        self.statusBar().showMessage(f"✓  已将翻译写回 {applied} 个文件")

    # ── Connection check ──────────────────────────────────────────────────

    def _check_connection(self) -> None:
        self._check_btn.setEnabled(False)
        self._check_btn.setText("检查中…")
        QApplication.processEvents()
        host  = self._host_edit.text().strip() or "http://localhost:11434"
        model = self._model_combo.currentText().strip() or "qwen2.5:7b"
        ok, msg = OllamaTranslator(host=host, model=model).check_connection()
        self._check_btn.setEnabled(True)
        self._check_btn.setText("🔗  检查连接")
        if ok:
            QMessageBox.information(self, "连接成功", msg)
        else:
            QMessageBox.warning(self, "连接失败", msg)


# ── Entry point ───────────────────────────────────────────────────────────────

def main() -> None:
    app = QApplication(sys.argv)
    app.setApplicationName("translate-comments")
    app.setStyle("Fusion")

    # Do NOT set a dark app-wide QPalette — it pollutes QTableWidget and
    # dialog colors.  Dark elements are styled exclusively via QSS.

    win = MainWindow()
    win.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
