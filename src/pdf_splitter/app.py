"""Main application window."""

from __future__ import annotations

from pathlib import Path

import fitz
from PyQt6.QtCore import Qt
from PyQt6.QtGui import QKeyEvent
from PyQt6.QtWidgets import (
    QFileDialog,
    QHBoxLayout,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QMainWindow,
    QMessageBox,
    QPushButton,
    QSplitter,
    QStatusBar,
    QVBoxLayout,
    QWidget,
)

from pdf_splitter.splitter import collect_split_markers, export_document_between_lines
from pdf_splitter.viewer import PagePreviewWidget


class MainWindow(QMainWindow):
    def __init__(self) -> None:
        super().__init__()
        self.setWindowTitle("PDF Horizontal Splitter")
        self.resize(1100, 750)

        self._doc: fitz.Document | None = None
        self._pdf_path: Path | None = None
        self._page_index = 0
        self._splits: dict[int, list[float]] = {}

        self._build_ui()
        self._connect_signals()
        self._update_controls()

    def _build_ui(self) -> None:
        toolbar = QWidget()
        tb_layout = QHBoxLayout(toolbar)
        tb_layout.setContentsMargins(8, 8, 8, 4)

        self._open_btn = QPushButton("Open PDF")
        self._export_btn = QPushButton("Export…")
        self._prev_btn = QPushButton("◀ Previous")
        self._next_btn = QPushButton("Next ▶")
        self._page_label = QLabel("No document")

        tb_layout.addWidget(self._open_btn)
        tb_layout.addWidget(self._export_btn)
        tb_layout.addStretch()
        tb_layout.addWidget(self._prev_btn)
        tb_layout.addWidget(self._page_label)
        tb_layout.addWidget(self._next_btn)

        self._preview = PagePreviewWidget()
        self._split_list = QListWidget()
        self._split_list.setMinimumWidth(220)
        self._split_list.setMaximumWidth(320)

        side = QWidget()
        side_layout = QVBoxLayout(side)
        side_layout.addWidget(QLabel("Split lines (this page)"))
        side_layout.addWidget(self._split_list)
        self._remove_btn = QPushButton("Remove selected")
        self._clear_btn = QPushButton("Clear all")
        side_layout.addWidget(self._remove_btn)
        side_layout.addWidget(self._clear_btn)
        side_layout.addStretch()

        center_split = QSplitter(Qt.Orientation.Horizontal)
        center_split.addWidget(self._preview)
        center_split.addWidget(side)
        center_split.setStretchFactor(0, 1)
        center_split.setStretchFactor(1, 0)

        root = QWidget()
        root_layout = QVBoxLayout(root)
        root_layout.addWidget(toolbar)
        root_layout.addWidget(center_split, 1)
        self.setCentralWidget(root)

        self._status = QStatusBar()
        self.setStatusBar(self._status)
        self._status.showMessage(
            "Open a PDF · Left-click to add split · Right-click near a line to remove"
        )

    def _connect_signals(self) -> None:
        self._open_btn.clicked.connect(self._open_pdf)
        self._export_btn.clicked.connect(self._export)
        self._prev_btn.clicked.connect(self._prev_page)
        self._next_btn.clicked.connect(self._next_page)
        self._remove_btn.clicked.connect(self._remove_selected_split)
        self._clear_btn.clicked.connect(self._clear_splits)
        self._preview.split_added.connect(self._on_split_added)
        self._preview.split_removed.connect(self._on_split_removed)
        self._split_list.itemSelectionChanged.connect(self._on_split_selection)

    def keyPressEvent(self, event: QKeyEvent) -> None:
        if event.key() in (Qt.Key.Key_Delete, Qt.Key.Key_Backspace):
            if self._split_list.hasFocus() or self._preview.hasFocus():
                self._remove_selected_split()
                return
        super().keyPressEvent(event)

    def _on_split_selection(self) -> None:
        row = self._split_list.currentRow()
        if row >= 0:
            splits = self._current_splits()
            if row < len(splits):
                self._status.showMessage(f"Selected split at {splits[row]:.1f} pt — Delete to remove")

    def _open_pdf(self) -> None:
        path, _ = QFileDialog.getOpenFileName(
            self,
            "Open PDF",
            "",
            "PDF files (*.pdf);;All files (*.*)",
        )
        if not path:
            return
        try:
            doc = fitz.open(path)
        except Exception as exc:
            QMessageBox.critical(self, "Open failed", str(exc))
            return

        if self._doc is not None:
            self._doc.close()

        self._doc = doc
        self._pdf_path = Path(path)
        self._page_index = 0
        self._splits = {}
        self._refresh_page()
        self._update_controls()

    def _current_splits(self) -> list[float]:
        return self._splits.get(self._page_index, [])

    def _set_current_splits(self, splits: list[float]) -> None:
        if splits:
            self._splits[self._page_index] = sorted(splits)
        elif self._page_index in self._splits:
            del self._splits[self._page_index]

    def _on_split_added(self, pdf_y: float) -> None:
        splits = self._current_splits()
        splits.append(pdf_y)
        self._set_current_splits(splits)
        self._refresh_splits_ui()

    def _on_split_removed(self, pdf_y: float) -> None:
        splits = self._current_splits()
        splits = [y for y in splits if abs(y - pdf_y) > 0.01]
        self._set_current_splits(splits)
        self._refresh_splits_ui()

    def _remove_selected_split(self) -> None:
        row = self._split_list.currentRow()
        if row < 0:
            return
        splits = self._current_splits()
        if 0 <= row < len(splits):
            splits.pop(row)
            self._set_current_splits(splits)
            self._refresh_splits_ui()

    def _clear_splits(self) -> None:
        self._set_current_splits([])
        self._refresh_splits_ui()

    def _refresh_splits_ui(self) -> None:
        splits = self._current_splits()
        self._preview.set_splits(splits)
        self._split_list.clear()
        for y in splits:
            item = QListWidgetItem(f"{y:.1f} pt")
            self._split_list.addItem(item)
        n = len(splits)
        if self._doc:
            markers = collect_split_markers(self._splits, len(self._doc))
            segments = max(0, len(markers) - 1)
            self._status.showMessage(
                f"Page {self._page_index + 1}: {n} line(s) here · "
                f"{len(markers)} total → {segments} segment(s) on export"
            )

    def _refresh_page(self) -> None:
        if self._doc is None:
            self._preview.clear()
            return
        page = self._doc[self._page_index]
        pixmap, page_height = PagePreviewWidget.render_page_to_pixmap(page)
        self._preview.set_page_image(pixmap, page_height)
        self._refresh_splits_ui()

    def _prev_page(self) -> None:
        if self._doc and self._page_index > 0:
            self._page_index -= 1
            self._refresh_page()
            self._update_controls()

    def _next_page(self) -> None:
        if self._doc and self._page_index < len(self._doc) - 1:
            self._page_index += 1
            self._refresh_page()
            self._update_controls()

    def _update_controls(self) -> None:
        has_doc = self._doc is not None
        self._export_btn.setEnabled(has_doc)
        self._prev_btn.setEnabled(has_doc and self._page_index > 0)
        self._next_btn.setEnabled(
            has_doc and self._doc is not None and self._page_index < len(self._doc) - 1
        )
        self._remove_btn.setEnabled(has_doc)
        self._clear_btn.setEnabled(has_doc)
        if has_doc and self._doc:
            self._page_label.setText(
                f"Page {self._page_index + 1} / {len(self._doc)}"
            )
        else:
            self._page_label.setText("No document")

    def _export(self) -> None:
        if self._doc is None or self._pdf_path is None:
            return
        markers = collect_split_markers(self._splits, len(self._doc))
        if len(markers) < 2:
            QMessageBox.warning(
                self,
                "Not enough split lines",
                "Add at least two split lines in document order "
                "(they can be on different pages). Each export is the "
                "content between one line and the next.",
            )
            return

        out_dir = QFileDialog.getExistingDirectory(self, "Export segments to folder")
        if not out_dir:
            return

        try:
            paths = export_document_between_lines(
                self._pdf_path,
                self._splits,
                out_dir,
                basename=self._pdf_path.stem,
            )
        except Exception as exc:
            QMessageBox.critical(self, "Export failed", str(exc))
            return

        if not paths:
            QMessageBox.warning(
                self, "Export", "No segments were written (regions too thin?)."
            )
            return

        QMessageBox.information(
            self,
            "Export complete",
            f"Wrote {len(paths)} file(s) to:\n{out_dir}",
        )
        self._status.showMessage(f"Exported {len(paths)} PDF(s) to {out_dir}")

    def closeEvent(self, event) -> None:
        if self._doc is not None:
            self._doc.close()
            self._doc = None
        super().closeEvent(event)
