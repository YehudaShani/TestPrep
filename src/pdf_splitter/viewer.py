"""PDF page preview with clickable horizontal split overlays."""

from __future__ import annotations

import fitz
from PyQt6.QtCore import QPoint, Qt, pyqtSignal
from PyQt6.QtGui import QColor, QImage, QMouseEvent, QPainter, QPen, QPixmap
from PyQt6.QtWidgets import QSizePolicy, QWidget

from pdf_splitter.splitter import pdf_y_to_screen_y, screen_y_to_pdf_y

SPLIT_LINE_COLOR = (220, 38, 38)
HIT_TOLERANCE_PX = 8
MIN_SPLIT_GAP_PDF_PT = 5.0


class PagePreviewWidget(QWidget):
    """Renders the current PDF page and horizontal split guides."""

    split_added = pyqtSignal(float)
    split_removed = pyqtSignal(float)

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setMinimumSize(400, 500)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        self.setMouseTracking(True)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)

        self._pixmap: QPixmap | None = None
        self._page_height: float = 0.0
        self._split_ys: list[float] = []
        self._image_rect = (0.0, 0.0, 0.0, 0.0)  # x, y, w, h in widget coords

    def set_page_image(self, pixmap: QPixmap, page_height: float) -> None:
        self._pixmap = pixmap
        self._page_height = page_height
        self._update_image_rect()
        self.update()

    def set_splits(self, split_ys: list[float]) -> None:
        self._split_ys = list(split_ys)
        self.update()

    def clear(self) -> None:
        self._pixmap = None
        self._page_height = 0.0
        self._split_ys = []
        self._image_rect = (0.0, 0.0, 0.0, 0.0)
        self.update()

    def _update_image_rect(self) -> None:
        if self._pixmap is None or self._pixmap.isNull():
            self._image_rect = (0.0, 0.0, 0.0, 0.0)
            return
        pw = self._pixmap.width()
        ph = self._pixmap.height()
        ww = self.width()
        wh = self.height()
        if pw <= 0 or ph <= 0 or ww <= 0 or wh <= 0:
            self._image_rect = (0.0, 0.0, float(pw), float(ph))
            return
        scale = min(ww / pw, wh / ph)
        dw = pw * scale
        dh = ph * scale
        ox = (ww - dw) / 2
        oy = (wh - dh) / 2
        self._image_rect = (ox, oy, dw, dh)

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        self._update_image_rect()

    def paintEvent(self, event) -> None:
        painter = QPainter(self)
        painter.fillRect(self.rect(), self.palette().color(self.backgroundRole()))

        if self._pixmap is None or self._pixmap.isNull():
            painter.drawText(self.rect(), Qt.AlignmentFlag.AlignCenter, "Open a PDF to preview")
            return

        ox, oy, dw, dh = self._image_rect
        painter.drawPixmap(int(ox), int(oy), int(dw), int(dh), self._pixmap)

        pen = QPen(QColor(*SPLIT_LINE_COLOR))
        pen.setWidth(2)
        painter.setPen(pen)
        for pdf_y in self._split_ys:
            sy = pdf_y_to_screen_y(pdf_y, oy, dh, self._page_height)
            y = int(sy)
            painter.drawLine(int(ox), y, int(ox + dw), y)

    def _point_in_image(self, pos: QPoint) -> bool:
        ox, oy, dw, dh = self._image_rect
        return ox <= pos.x() <= ox + dw and oy <= pos.y() <= oy + dh

    def _nearest_split_screen_y(self, pos: QPoint) -> float | None:
        if not self._split_ys:
            return None
        ox, oy, dw, dh = self._image_rect
        best: float | None = None
        best_dist = HIT_TOLERANCE_PX + 1
        for pdf_y in self._split_ys:
            sy = pdf_y_to_screen_y(pdf_y, oy, dh, self._page_height)
            dist = abs(pos.y() - sy)
            if dist < best_dist:
                best_dist = dist
                best = pdf_y
        if best_dist <= HIT_TOLERANCE_PX:
            return best
        return None

    def _too_close_to_existing(self, pdf_y: float) -> bool:
        for existing in self._split_ys:
            gap = abs(existing - pdf_y)
            if gap < MIN_SPLIT_GAP_PDF_PT:
                return True
        return False

    def mousePressEvent(self, event: QMouseEvent) -> None:
        if self._pixmap is None or not self._point_in_image(event.pos()):
            return

        ox, oy, _, dh = self._image_rect

        if event.button() == Qt.MouseButton.RightButton:
            hit = self._nearest_split_screen_y(event.pos())
            if hit is not None:
                self.split_removed.emit(hit)
            return

        if event.button() == Qt.MouseButton.LeftButton:
            hit = self._nearest_split_screen_y(event.pos())
            if hit is not None:
                return
            pdf_y = screen_y_to_pdf_y(float(event.pos().y()), oy, dh, self._page_height)
            if pdf_y <= 0 or pdf_y >= self._page_height:
                return
            if not self._too_close_to_existing(pdf_y):
                self.split_added.emit(pdf_y)

    def render_page_to_pixmap(page: fitz.Page, max_width: int = 1200) -> tuple[QPixmap, float]:
        """Render a fitz page to QPixmap; return pixmap and display height (page.rect)."""
        rect = page.rect
        page_height = rect.height
        zoom = min(2.0, max_width / max(rect.width, 1))
        mat = fitz.Matrix(zoom, zoom)
        pix = page.get_pixmap(matrix=mat, alpha=False)
        fmt = QImage.Format.Format_RGB888
        image = QImage(pix.samples, pix.width, pix.height, pix.stride, fmt)
        qpix = QPixmap.fromImage(image.copy())
        return qpix, page_height
