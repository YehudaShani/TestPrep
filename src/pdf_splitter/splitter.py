"""PDF strip boundaries and export via PyMuPDF."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import fitz

MIN_STRIP_HEIGHT_PT = 2.0

SplitMarker = tuple[int, float]
PageSlice = tuple[int, float, float]


def boundaries(page_height: float, split_ys: list[float]) -> list[tuple[float, float]]:
    """Return (y0, y1) strips from top (0) through split lines to page bottom."""
    ys = sorted(set(split_ys))
    edges = [0.0, *ys, page_height]
    strips: list[tuple[float, float]] = []
    for i in range(len(edges) - 1):
        y0, y1 = edges[i], edges[i + 1]
        if y1 - y0 >= MIN_STRIP_HEIGHT_PT:
            strips.append((y0, y1))
    return strips


def collect_split_markers(
    splits: dict[int, list[float]],
    page_count: int,
) -> list[SplitMarker]:
    """All split lines in reading order: page 0 top→bottom, then page 1, etc."""
    markers: list[SplitMarker] = []
    for page_index in range(page_count):
        for y in sorted(splits.get(page_index, [])):
            markers.append((page_index, y))
    return markers


def segment_page_slices(
    start: SplitMarker,
    end: SplitMarker,
    page_heights: dict[int, float],
) -> list[PageSlice]:
    """Page-local (y0, y1) pieces from one marker to the next (may span pages)."""
    p0, y0 = start
    p1, y1 = end
    slices: list[PageSlice] = []

    if p0 == p1:
        if y1 - y0 >= MIN_STRIP_HEIGHT_PT:
            slices.append((p0, y0, y1))
        return slices

    h0 = page_heights[p0]
    if h0 - y0 >= MIN_STRIP_HEIGHT_PT:
        slices.append((p0, y0, h0))
    for page_index in range(p0 + 1, p1):
        height = page_heights[page_index]
        if height >= MIN_STRIP_HEIGHT_PT:
            slices.append((page_index, 0.0, height))
    if y1 >= MIN_STRIP_HEIGHT_PT:
        slices.append((p1, 0.0, y1))
    return slices


def _strip_clip_rot0(page: fitz.Page, y0: float, y1: float) -> fitz.Rect:
    """Clip rect in PDF coordinates for a horizontal strip (rotation must be 0)."""
    cb = page.cropbox
    return fitz.Rect(cb.x0, cb.y0 + y0, cb.x1, cb.y0 + y1)


def _normalized_page_doc(src_doc: fitz.Document, page_index: int) -> fitz.Document:
    """Bake rotation into a single-page document matching on-screen layout."""
    page = src_doc[page_index]
    norm = fitz.open()
    rect = page.rect
    norm_page = norm.new_page(width=rect.width, height=rect.height)
    norm_page.show_pdf_page(norm_page.rect, src_doc, page_index)
    return norm


@dataclass
class _PageExportCtx:
    work_doc: fitz.Document
    work_pno: int
    work_page: fitz.Page
    display_width: float
    display_height: float


class _ExportCache:
    def __init__(self, src_doc: fitz.Document) -> None:
        self._src_doc = src_doc
        self._norm_docs: dict[int, fitz.Document] = {}
        self._ctx: dict[int, _PageExportCtx] = {}

    def page_ctx(self, page_index: int) -> _PageExportCtx:
        if page_index not in self._ctx:
            page = self._src_doc[page_index]
            if page.rotation != 0:
                norm_doc = _normalized_page_doc(self._src_doc, page_index)
                self._norm_docs[page_index] = norm_doc
                work_page = norm_doc[0]
                work_doc = norm_doc
                work_pno = 0
            else:
                work_page = page
                work_doc = self._src_doc
                work_pno = page_index
            self._ctx[page_index] = _PageExportCtx(
                work_doc=work_doc,
                work_pno=work_pno,
                work_page=work_page,
                display_width=work_page.rect.width,
                display_height=work_page.rect.height,
            )
        return self._ctx[page_index]

    def close(self) -> None:
        for norm_doc in self._norm_docs.values():
            norm_doc.close()
        self._norm_docs.clear()
        self._ctx.clear()


def _write_segment_pdf(
    cache: _ExportCache,
    slices: list[PageSlice],
    out_path: Path,
) -> bool:
    """Stack slice clips into one PDF; return False if nothing was written."""
    if not slices:
        return False

    strip_heights: list[float] = []
    strip_widths: list[float] = []
    for page_index, y0, y1 in slices:
        ctx = cache.page_ctx(page_index)
        strip_heights.append(y1 - y0)
        strip_widths.append(ctx.display_width)

    page_width = max(strip_widths)
    page_height = sum(strip_heights)

    new_doc = fitz.open()
    try:
        new_page = new_doc.new_page(width=page_width, height=page_height)
        y_cursor = 0.0
        for (page_index, y0, y1), strip_h in zip(slices, strip_heights):
            ctx = cache.page_ctx(page_index)
            clip = _strip_clip_rot0(ctx.work_page, y0, y1)
            dest = fitz.Rect(0, y_cursor, ctx.display_width, y_cursor + strip_h)
            new_page.show_pdf_page(dest, ctx.work_doc, ctx.work_pno, clip=clip)
            y_cursor += strip_h
        new_doc.save(out_path)
    finally:
        new_doc.close()
    return True


def export_document_between_lines(
    src_path: str | Path,
    splits: dict[int, list[float]],
    out_dir: str | Path,
    basename: str | None = None,
) -> list[Path]:
    """Export one PDF per consecutive pair of split lines (document order, any pages)."""
    src_path = Path(src_path)
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    if basename is None:
        basename = src_path.stem

    src_doc = fitz.open(src_path)
    cache = _ExportCache(src_doc)
    written: list[Path] = []
    try:
        page_count = len(src_doc)
        markers = collect_split_markers(splits, page_count)
        if len(markers) < 2:
            return written

        page_heights = {
            page_index: cache.page_ctx(page_index).display_height
            for page_index in range(page_count)
        }

        for seg_idx, start in enumerate(markers[:-1], start=1):
            end = markers[seg_idx]
            slices = segment_page_slices(start, end, page_heights)
            out_path = out_dir / f"{basename}_segment{seg_idx:03d}.pdf"
            if _write_segment_pdf(cache, slices, out_path):
                written.append(out_path)
    finally:
        cache.close()
        src_doc.close()

    return written


def export_page_strips(
    src_path: str | Path,
    page_index: int,
    split_ys: list[float],
    out_dir: str | Path,
    basename: str | None = None,
) -> list[Path]:
    """Export one PDF per vertical strip on the given page (visual coordinates)."""
    src_path = Path(src_path)
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    if basename is None:
        basename = src_path.stem

    written: list[Path] = []
    src_doc = fitz.open(src_path)
    norm_doc: fitz.Document | None = None
    try:
        page = src_doc[page_index]
        if page.rotation != 0:
            norm_doc = _normalized_page_doc(src_doc, page_index)
            work_page = norm_doc[0]
        else:
            work_page = page

        display_height = work_page.rect.height
        display_width = work_page.rect.width
        work_doc = norm_doc if norm_doc is not None else src_doc
        work_pno = 0 if norm_doc is not None else page_index

        strips = boundaries(display_height, split_ys)
        if not strips:
            return written

        page_num = page_index + 1
        for part_idx, (y0, y1) in enumerate(strips, start=1):
            strip_h = y1 - y0
            clip = _strip_clip_rot0(work_page, y0, y1)

            new_doc = fitz.open()
            try:
                new_page = new_doc.new_page(width=display_width, height=strip_h)
                dest = fitz.Rect(0, 0, display_width, strip_h)
                new_page.show_pdf_page(dest, work_doc, work_pno, clip=clip)
                out_name = f"{basename}_page{page_num:02d}_part{part_idx:02d}.pdf"
                out_path = out_dir / out_name
                new_doc.save(out_path)
                written.append(out_path)
            finally:
                new_doc.close()
    finally:
        if norm_doc is not None:
            norm_doc.close()
        src_doc.close()

    return written


def screen_y_to_pdf_y(
    screen_y: float,
    image_top: float,
    image_height: float,
    page_height: float,
) -> float:
    """Map widget Y to PDF display Y (origin top of page.rect)."""
    if image_height <= 0:
        return 0.0
    rel = (screen_y - image_top) / image_height
    rel = max(0.0, min(1.0, rel))
    return rel * page_height


def pdf_y_to_screen_y(
    pdf_y: float,
    image_top: float,
    image_height: float,
    page_height: float,
) -> float:
    if page_height <= 0:
        return image_top
    rel = pdf_y / page_height
    return image_top + rel * image_height
