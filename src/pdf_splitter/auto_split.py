"""Automatic question/solution segmentation for exam PDFs.

Splits an exam (or exam+solutions) PDF into per-question PDFs — each question
carries its shared circuit setup — and per-answer PDFs for the worked
solutions. Handles the header styles used across years:

- exam questions: "שאלה מספר N (5 נקודות)" (2019+), "N. ( 5% )" (2021-2022),
  a bare "N (5 נקודות)" when extraction loses the Hebrew marker words, or a
  plain "N. <question text>" with no score marker at all
- solutions: "שאלה מספר N" restarting at 1, or a "פתרון" title followed by
  "שאלה N" / ":Nשאלה" / "N." / bold bare-digit items, or per-question
  "פתרוןN" headings; 2022 winters interleave each question with its own
  "פתרון" section instead of a trailing solutions part

RTL extraction scrambles multi-digit numbers ("23" -> "3 2") and splits one
visual line into fragments, so fragments at the same height are merged and
header numbers are only trusted when they form an increasing sequence.
"""

from __future__ import annotations

import argparse
import json
import re
from dataclasses import dataclass, field
from pathlib import Path

import fitz

from pdf_splitter.splitter import _ExportCache, _write_segment_pdf

QUESTION_MARKER_RE = re.compile(r"שאלה\s*מספר")
PRIMARY_NUM_RE = re.compile(r"מספר\s*(\d+)")
QUESTION_WORD_RE = re.compile(
    r"(?:ש\s*א\s*ל\s*ה|ה\s*ל\s*א\s*ש|question)", re.IGNORECASE
)
SCORE_MARK_RE = re.compile(r"נקודות|%")
SOL_NAMED_RE = re.compile(r"(?:^|[\s:.\-–])שאלה|שאלה(?:$|[\s:.\-–])")
SOL_NUM_ITEM_RE = re.compile(r"^(\d{1,2})\s*[.)]\s*$")
SOL_MARKER_RE = re.compile(
    r"^[:.()\s]*(?:פתרון(?:\s*הבחינה)?|פתרונות)"
    r"\s*\(?\s*(?:שאלה)?\s*(\d{1,2})?\s*\)?\s*[:.\-–]?\s*$"
)
PLAIN_ITEM_RE = re.compile(r"^\s*(\d{1,2})\s*\.(?!\d)")
SHORT_HEADER_RE = re.compile(r"^\s*((?:\d\s*){1,3})\s*:\s*$")
# Choices are lettered א through ו, occasionally running on to ז. A ו label
# sometimes extracts as the visually identical final nun; no Hebrew word starts
# with a final form, so a standalone "ן." is always a label.
OPTION_LETTERS = "אבגדהוזן"
OPTION_LABEL_RE = re.compile(
    r"(?:^|\s)(?:"
    # "ה ." — a space between the letter and the dot is never a real word
    rf"[{OPTION_LETTERS}]\s+[.)]"
    # "ה." — needs the trailing break so word endings don't match
    rf"|[{OPTION_LETTERS}][.)](?=\s|$)"
    # RTL extraction flips the label to ".ה", sometimes glued to the choice
    rf"|[.(]\s*[{OPTION_LETTERS}](?![א-ת])"
    r")"
)
CHOICE_ITEM_RE = re.compile(r"^\s*([1-9])\s*[.)](?!\d)")
HEBREW_RE = re.compile(r"[א-ת]")
LATIN_RE = re.compile(r"[A-Za-z]")
FOOTER_RE = re.compile(r"עמוד\s*\d+|מתוך\s*\d+|טור\s*\d+")
# Physics 3 papers close with a formula sheet ("נתונים מספריים" / "משוואות").
# It belongs to no question, so the last segment must stop before it. Some
# files extract Hebrew right-to-left, which reverses the whole heading, so
# match each phrase as written and reversed.
APPENDIX_RE = re.compile(
    r"^[\s:.\-–]*(?:"
    + "|".join(
        r"\s*".join(re.escape(word) for word in phrase.split())
        for base in ("נתונים מספריים", "משוואות")
        for phrase in (base, base[::-1])
    )
    + r")[\s:.\-–]*$"
)
# highlighter fills: pure yellow (1 1 0) or pure cyan (0 1 1)
HIGHLIGHT_FILL_RE = re.compile(
    rb"(?<![\d.])(?:1(?:\.0+)?\s+1(?:\.0+)?\s+0(?:\.0+)?|0(?:\.0+)?\s+1(?:\.0+)?\s+1(?:\.0+)?)\s+(rg|RG|scn?|SCN?)"
)
ANSWER_TEXT_COLOR_RE = re.compile(
    rb"(?<![\d.])(?:1(?:\.0+)?\s+0(?:\.0+)?\s+0(?:\.0+)?"
    rb"|0(?:\.0+)?\s+0(?:\.0+)?\s+1(?:\.0+)?)\s+(rg|RG|scn?|SCN?)"
)

MIN_SLICE_PT = 6.0
MIN_SETUP_PT = 15.0
MAX_QUESTION_NUM = 40


@dataclass
class Line:
    page: int
    y0: float
    y1: float
    text: str
    bold: bool = False


@dataclass
class Header:
    page: int
    y: float
    parsed_number: int | None
    # extra question numbers covered by the same header ("שאלה11+12")
    extra: list[int] = field(default_factory=list)


_SOL_LETTERS = sorted("פתרון")


def _sol_marker(text: str) -> tuple[bool, int | None]:
    """Standalone solution title/marker line — "פתרון", "פתרון7",
    "פתרון (שאלה11", ":פתרון הבחינה", or an RTL-scrambled "פתרון" —
    and the question number it names, if any."""
    if len(text) > 25 or LATIN_RE.search(text):
        return False, None
    m = SOL_MARKER_RE.match(text)
    if m:
        return True, int(m.group(1)) if m.group(1) else None
    core = re.sub(r"[\d():.\-–\s]", "", text)
    if sorted(core) == _SOL_LETTERS:
        nums = re.findall(r"\d{1,2}", text)
        if len(nums) == 1 and int(nums[0]) <= MAX_QUESTION_NUM:
            return True, int(nums[0])
        return True, None
    return False, None


@dataclass
class PageBounds:
    top: float
    bottom: float


@dataclass
class Segment:
    part: str  # "question" | "answer"
    number: int
    slices: list[tuple[int, float, float]]
    warnings: list[str] = field(default_factory=list)


def _extract_lines(doc: fitz.Document) -> tuple[list[Line], dict[int, PageBounds]]:
    """Visual lines in reading order (same-height fragments merged, RTL order)
    plus per-page content bounds with running headers/footers trimmed.

    A line is a running header/footer only if its exact text repeats near the
    top/bottom of 3+ pages — solution titles and "שאלהN" headers often sit at
    the very top of the page, so position alone must not discard them."""
    page_merged: list[list[Line]] = []
    heights: list[float] = []
    for pno, page in enumerate(doc):
        heights.append(page.rect.height)
        raw: list[tuple[float, float, float, str, bool]] = []  # y0,y1,x0,text,bold
        for block in page.get_text("dict")["blocks"]:
            for ln in block.get("lines", []):
                text = " ".join(
                    "".join(span["text"] for span in ln["spans"]).split()
                )
                if not text:
                    continue
                bold = all(
                    span["flags"] & 16 or "Bold" in span["font"]
                    for span in ln["spans"]
                )
                bbox = ln["bbox"]
                raw.append((bbox[1], bbox[3], bbox[0], text, bold))
        raw.sort(key=lambda r: r[0])

        merged: list[Line] = []
        group: list[tuple[float, float, float, str, bool]] = []

        def flush() -> None:
            if not group:
                return
            group.sort(key=lambda r: -r[2])  # right-to-left for RTL text
            merged.append(
                Line(
                    pno,
                    min(r[0] for r in group),
                    max(r[1] for r in group),
                    " ".join(r[3] for r in group),
                    all(r[4] for r in group),
                )
            )
            group.clear()

        for item in raw:
            if group and item[0] - group[-1][0] > 3.5:
                flush()
            group.append(item)
        flush()
        page_merged.append(merged)

    running: dict[tuple[str, str], set[int]] = {}
    for pno, merged in enumerate(page_merged):
        height = heights[pno]
        for ln in merged:
            if ln.y1 < 0.12 * height:
                running.setdefault(("top", ln.text), set()).add(pno)
            elif ln.y0 > 0.88 * height:
                running.setdefault(("bottom", ln.text), set()).add(pno)
    repeated = {key for key, pages in running.items() if len(pages) >= 3}

    lines: list[Line] = []
    bounds: dict[int, PageBounds] = {}
    for pno, merged in enumerate(page_merged):
        height = heights[pno]
        top, bottom = 40.0, height - 40.0
        for ln in merged:
            # A repeated "פתרון" is a per-question solution heading, not a
            # running header — structural lines are never trimmed.
            structural = _sol_marker(ln.text)[0] or QUESTION_MARKER_RE.search(ln.text)
            if structural:
                lines.append(ln)
            elif ln.y1 < 0.12 * height and ("top", ln.text) in repeated:
                top = max(top, ln.y1 + 3.0)
            elif ln.y0 > 0.88 * height and (
                ("bottom", ln.text) in repeated or FOOTER_RE.search(ln.text)
            ):
                bottom = min(bottom, ln.y0 - 2.0)
            else:
                lines.append(ln)
        bounds[pno] = PageBounds(top=top, bottom=bottom)
    return lines, bounds


def _number_candidates(text: str) -> set[int]:
    """Numbers that could be the question number in a header line, allowing for
    one points/percent token (3 or 5) and RTL-scrambled digit joins."""
    tokens = re.findall(r"\d+", text)
    variants = [tokens]
    if SCORE_MARK_RE.search(text):
        for score in ("5", "3"):
            if score in tokens:
                dropped = list(tokens)
                dropped.remove(score)
                variants.append(dropped)
    cands: set[int] = set()
    for var in variants:
        cands.update(int(t) for t in var if int(t) <= MAX_QUESTION_NUM)
        singles = [t for t in var if len(t) == 1]
        for a, b in zip(singles, singles[1:]):
            cands.add(int(a + b))
            cands.add(int(b + a))
    return cands


def _find_exam_headers(lines: list[Line]) -> list[Header]:
    """Exam question headers, preferring the explicit "שאלה מספר" marker."""
    marked: list[Header] = []
    for ln in lines:
        if QUESTION_MARKER_RE.search(ln.text):
            m = PRIMARY_NUM_RE.search(ln.text)
            n = int(m.group(1)) if m else None
            if n is not None and not 1 <= n <= MAX_QUESTION_NUM:
                continue
            if marked and n is not None and marked[-1].parsed_number == n:
                continue
            marked.append(Header(ln.page, ln.y0 - 2.0, n))
    if len(marked) >= 4:
        return marked

    # Physics 3 exams use a shorter "שאלה N" heading.  Some generators
    # extract the Hebrew word backwards ("הלאש"), so accept both forms and
    # chain only increasing question numbers to reject page/footer noise.
    named: list[Header] = []
    all_named: list[Header] = []
    expected = 1
    for ln in lines:
        normalized = QUESTION_WORD_RE.sub("שאלה", ln.text)
        word_pos = normalized.find("שאלה")
        short = SHORT_HEADER_RE.match(normalized)
        # A Hebrew letter directly before the word makes it a prefixed form
        # ("בשאלה זו..." — "in this question"), which is prose, not a heading.
        prefixed = word_pos > 0 and bool(HEBREW_RE.match(normalized[word_pos - 1]))
        named_style = 0 <= word_pos <= 5 and len(normalized) <= 80 and not prefixed
        if not named_style and not short:
            continue
        if _sol_marker(ln.text)[0]:
            continue
        candidates = _number_candidates(normalized)
        plausible = sorted(n for n in candidates if 1 <= n <= MAX_QUESTION_NUM)
        if not plausible:
            if named_style and re.search(r"\d", normalized):
                all_named.append(Header(ln.page, ln.y0 - 2.0, None))
            continue
        parsed = expected if expected in candidates else None
        all_named.append(Header(ln.page, ln.y0 - 2.0, parsed))

        next_expected: int
        if expected in candidates:
            n = expected
            next_expected = expected + 1
        elif not named:
            starting = [n for n in candidates if 1 <= n <= 3]
            if not starting:
                continue
            n = min(starting)
            next_expected = n + 1
        elif expected + 1 in candidates:
            # A few official solution files print the next heading twice
            # (for example 9, 11, 11, 12). Keep both physical boundaries;
            # _assign_numbers will then number them by position and warn.
            n = expected + 1
            next_expected = expected + 1
        else:
            continue
        named.append(Header(ln.page, ln.y0 - 2.0, n))
        expected = next_expected
    if len(all_named) >= 4 and len(all_named) > len(named):
        return all_named
    if len(named) >= 2:
        return named

    # Fallback: score-marked lines ("N. ( 5% )" or fragmented "N (5 נקודות)")
    # and plain "N. <Hebrew question text>" lines.  The longest strictly
    # increasing numbering chain wins, so missing or malformed headers don't
    # cut off everything after them.
    items: list[tuple[Line, set[int], bool]] = []
    for ln in lines:
        scored = bool(SCORE_MARK_RE.search(ln.text) and "(" in ln.text)
        cands: set[int] = set()
        if scored:
            cands = _number_candidates(ln.text)
        else:
            m = PLAIN_ITEM_RE.match(ln.text)
            if m and len(ln.text) >= 12 and HEBREW_RE.search(ln.text):
                n = int(m.group(1))
                if 1 <= n <= MAX_QUESTION_NUM:
                    cands = {n}
        if cands:
            items.append((ln, cands, scored))

    # best[n]: (chain length, scored count, linked node) for chains ending at n
    Node = tuple[Line, int, object]
    best: dict[int, tuple[int, int, Node | None]] = {}
    for ln, cands, scored in items:
        updates: dict[int, tuple[int, int, Node | None]] = {}
        for c in cands:
            base: tuple[int, int, Node | None] = (0, 0, None)
            for m_, v in best.items():
                if m_ < c and (v[0], v[1]) > (base[0], base[1]):
                    base = v
            val = (base[0] + 1, base[1] + int(scored), (ln, c, base[2]))
            if c not in updates or (val[0], val[1]) > (updates[c][0], updates[c][1]):
                updates[c] = val
        for c, val in updates.items():
            if c not in best or (val[0], val[1]) > (best[c][0], best[c][1]):
                best[c] = val
    if not best:
        return marked
    top = max(best.values(), key=lambda v: (v[0], v[1]))
    chain: list[Header] = []
    node = top[2]
    while node is not None:
        ln, c, node = node
        chain.append(Header(ln.page, ln.y0 - 2.0, c))
    chain.reverse()
    return chain if len(chain) > len(marked) else marked


def _split_exam_and_solutions(
    headers: list[Header],
) -> tuple[list[Header], list[Header]]:
    """Split at the last numbering reset to 1 (start of the solutions part)."""
    reset = None
    for i in range(3, len(headers)):
        if headers[i].parsed_number == 1:
            reset = i
    if reset is None:
        return headers, []
    return headers[:reset], headers[reset:]


def _image_item_markers(doc: fitz.Document) -> list[tuple[int, float]]:
    """Item positions only visible as images (2019 A-Winter solutions):
    list numbers rendered as tiny digit/period image pairs in the right
    margin (clustered into one marker each), and whole items scanned as a
    single full-width image whose top is the item boundary."""
    markers: list[tuple[int, float]] = []
    for pno, page in enumerate(doc):
        width = page.rect.width
        ys: list[float] = []
        for block in page.get_text("dict")["blocks"]:
            if block["type"] == 0:
                continue
            x0, y0, x1, y1 = block["bbox"]
            if x0 > width - 105 and x1 - x0 < 16 and y1 - y0 < 15:
                ys.append(y0)
            elif x1 - x0 > 300 and y1 - y0 > 60:
                markers.append((pno, y0))
        start = last = None
        for y in sorted(ys):
            if last is not None and y - last <= 12:
                last = y
                continue
            if start is not None:
                markers.append((pno, start))
            start = last = y
        if start is not None:
            markers.append((pno, start))
    return markers


def _chain_item_headers(
    lines: list[Line],
    title: tuple[int, float],
    image_markers: list[tuple[int, float]] = [],
) -> list[Header]:
    """Chain-validate per-question items after a solutions title: "שאלה N"
    lines (also "N+M" combined and long merged lines that start with the
    header), "N." / "N)" items, standalone bold bare digits, and image list
    markers (which carry no readable number and take the next expected)."""
    events: list[tuple[int, float, Line | None]] = [
        (ln.page, ln.y0, ln) for ln in lines
    ]
    events += [(p, y, None) for p, y in image_markers]
    events.sort(key=lambda e: (e[0], e[1], e[2] is not None))

    headers: list[Header] = []
    expected = 1
    for page, y0, ln in events:
        if (page, y0 - 2.0) < title:
            continue
        if ln is None:
            headers.append(Header(page, y0 - 2.0, expected))
            expected += 1
            continue
        n: int | None = None
        extra: list[int] = []
        text = ln.text
        m = SOL_NUM_ITEM_RE.match(text) or (
            PLAIN_ITEM_RE.match(text) if HEBREW_RE.search(text) else None
        )
        if m and int(m.group(1)) == expected:
            n = expected
        elif ln.bold and re.fullmatch(r"\d{1,2}", text.strip()):
            if int(text) == expected:
                n = expected
        else:
            # Extraction sometimes splits the word itself ("שא לה7").
            text = re.sub(r"ש\s*א\s*ל\s*ה", "שאלה", text)
            # A same-height merge can glue prose onto the header, pushing the
            # line over the length limit — then only trust a leading "שאלה N".
            window = None
            if len(text) <= 40 and SOL_NAMED_RE.search(text):
                window = text
            elif "שאלה" in text[:12]:
                window = text[:16]
            if window:
                # Combined "שאלה 11+12" — RTL may scramble it to "+12 11",
                # so pair any two adjacent numbers joined by a plus sign.
                if "+" in window:
                    nums = [int(x) for x in re.findall(r"\d{1,2}", window)]
                    if len(nums) == 2:
                        a, b = min(nums), max(nums)
                        if b == a + 1 and expected <= a <= expected + 3:
                            n = a
                            extra = [b]
                if n is None:
                    cands = _number_candidates(window)
                    if expected in cands:
                        n = expected
                    else:
                        ahead = [c for c in cands if expected < c <= expected + 3]
                        if ahead:
                            n = min(ahead)
        if n is None:
            continue
        headers.append(Header(page, y0 - 2.0, n, extra))
        expected = max([n] + extra) + 1
    return headers


def _chain_marker_headers(
    lines: list[Line], after: tuple[int, float]
) -> list[Header]:
    """Chain-validate per-question "פתרוןN" markers ("פתרון" with the number
    lost in extraction fills the next expected slot)."""
    headers: list[Header] = []
    expected = 1
    for ln in lines:
        if (ln.page, ln.y0) <= after:
            continue
        marker, num = _sol_marker(ln.text)
        if not marker:
            continue
        if num is not None and expected <= num <= expected + 3:
            headers.append(Header(ln.page, ln.y0 - 2.0, num))
            expected = num + 1
        elif num is None and headers:
            headers.append(Header(ln.page, ln.y0 - 2.0, expected))
            expected += 1
    return headers


def _find_solutions_after_title(
    lines: list[Line],
    after: tuple[int, float],
    image_markers: list[tuple[int, float]] = [],
) -> tuple[list[Header], tuple[int, float] | None]:
    """Locate a solutions title after `after`, then chain-validate its headers."""
    title: tuple[int, float] | None = None
    for ln in lines:
        if (ln.page, ln.y0) <= after:
            continue
        if _sol_marker(ln.text)[0]:
            title = (ln.page, ln.y0 - 2.0)
            break
    if title is None:
        return [], None

    by_item = _chain_item_headers(lines, title)
    if image_markers:
        # Image list markers are unlabeled, so trust them only when they make
        # the chain substantially longer than text items alone.
        with_images = _chain_item_headers(lines, title, image_markers)
        if len(with_images) > len(by_item) + 4:
            by_item = with_images
    by_marker = _chain_marker_headers(lines, after)
    if len(by_marker) > len(by_item):
        return by_marker, (by_marker[0].page, by_marker[0].y)
    return by_item, title


def _assign_numbers(headers: list[Header]) -> list[tuple[Header, int, list[str]]]:
    """Use parsed numbers when clean and increasing; else sequential positions."""
    known = [h.parsed_number for h in headers if h.parsed_number is not None]
    increasing = all(a < b for a, b in zip(known, known[1:]))
    result: list[tuple[Header, int, list[str]]] = []
    if known and len(known) >= 0.8 * len(headers) and increasing:
        prev = 0
        for h in headers:
            n = h.parsed_number if h.parsed_number is not None else prev + 1
            warnings = []
            if n > prev + 1:
                warnings.append(f"gap: previous header was {prev} (missed header?)")
            result.append((h, n, warnings))
            prev = max([n] + h.extra)
    else:
        for i, h in enumerate(headers):
            n = i + 1
            warnings = []
            if h.parsed_number is not None and h.parsed_number != n:
                warnings.append(
                    f"header text says {h.parsed_number}, assigned {n} by position"
                )
            result.append((h, n, warnings))
    return result


def _strip_highlights(doc: fitz.Document) -> None:
    """Remove visual answer markup while preserving the underlying choices."""
    for page in doc:
        for annot in list(page.annots() or []):
            if annot.type[1].lower() in {"highlight", "underline", "squiggly"}:
                page.delete_annot(annot)
    # Word-produced PDFs often put page text inside nested Form XObjects, so
    # scanning only page.get_contents() misses the actual color operators.
    for xref in range(1, doc.xref_length()):
        if not doc.xref_is_stream(xref):
            continue
        if doc.xref_get_key(xref, "Subtype")[1] == "/Image":
            continue
        stream = doc.xref_stream(xref)
        cleaned = HIGHLIGHT_FILL_RE.sub(rb"1 1 1 \1", stream)
        cleaned = ANSWER_TEXT_COLOR_RE.sub(rb"0 0 0 \1", cleaned)
        if cleaned != stream:
            doc.update_stream(xref, cleaned)


def _redact_colored_solution_work(
    source_doc: fitz.Document,
    clean_doc: fitz.Document,
    headers: list[Header],
) -> None:
    """Hide worked text that shares a row with right-column answer choices.

    Several legacy solution-only PDFs place red correct choices in a narrow
    right column and start black worked equations in the left column before
    the final choices have ended. A horizontal crop cannot separate them, so
    remove only left-column text in that overlap. Images and vector diagrams
    are preserved.
    """
    for i, header in enumerate(headers):
        end = (
            (headers[i + 1].page, headers[i + 1].y)
            if i + 1 < len(headers)
            else (
                len(source_doc) - 1,
                source_doc[len(source_doc) - 1].rect.height,
            )
        )
        for pno in range(header.page, end[0] + 1):
            page = source_doc[pno]
            lo = header.y if pno == header.page else 0.0
            hi = end[1] if pno == end[0] else page.rect.height
            labels: list[fitz.Rect] = []
            colored: list[fitz.Rect] = []
            for block in page.get_text("dict")["blocks"]:
                for line in block.get("lines", []):
                    for span in line["spans"]:
                        rect = fitz.Rect(span["bbox"])
                        if rect.y1 <= lo or rect.y0 >= hi:
                            continue
                        text = span["text"].strip()
                        if re.fullmatch(r"[אבגדהו]\s*[.)]", text):
                            labels.append(rect)
                        color = int(span.get("color", 0))
                        if color not in {0, 0xFFFFFF}:
                            colored.append(rect)
            if len(labels) < 4 or not colored:
                continue
            if min(rect.x0 for rect in labels) <= page.rect.width * 0.55:
                continue
            redact_y0 = max(lo, min(rect.y0 for rect in colored) - 2.0)
            redact_y1 = min(hi, max(rect.y1 for rect in labels) + 3.0)
            if redact_y1 - redact_y0 < MIN_SLICE_PT:
                continue
            clean_page = clean_doc[pno]
            clean_page.add_redact_annot(
                fitz.Rect(0, redact_y0, page.rect.width * 0.55, redact_y1),
                fill=(1, 1, 1),
            )
            clean_page.apply_redactions(images=0, graphics=0, text=0)


def _span_slices(
    start: tuple[int, float],
    end: tuple[int, float],
    bounds: dict[int, PageBounds],
) -> list[tuple[int, float, float]]:
    """Content slices from start to end, trimmed to page bounds per page."""
    p0, y0 = start
    p1, y1 = end
    slices: list[tuple[int, float, float]] = []
    if p0 == p1:
        lo, hi = max(y0, bounds[p0].top), min(y1, bounds[p0].bottom)
        if hi - lo >= MIN_SLICE_PT:
            slices.append((p0, lo, hi))
        return slices
    lo = max(y0, bounds[p0].top)
    if bounds[p0].bottom - lo >= MIN_SLICE_PT:
        slices.append((p0, lo, bounds[p0].bottom))
    for p in range(p0 + 1, p1):
        if bounds[p].bottom - bounds[p].top >= MIN_SLICE_PT:
            slices.append((p, bounds[p].top, bounds[p].bottom))
    hi = min(y1, bounds[p1].bottom)
    if hi - bounds[p1].top >= MIN_SLICE_PT:
        slices.append((p1, bounds[p1].top, hi))
    return slices


def _choice_block_end(
    start: tuple[int, float], end: tuple[int, float], lines: list[Line]
) -> tuple[int, float] | None:
    """Just past the multiple-choice block inside [start, end), or None."""
    option_lines: list[Line] = []
    option_count = 0
    # Some exams label the choices "1." to "6." instead of "א." to "ו.", and a
    # few list lettered statements first and the real choices as a numbered
    # block below them — so track both and keep whichever ends last.
    numbered_choices: dict[int, Line] = {}
    for ln in lines:
        pos = (ln.page, ln.y0)
        if pos < start or pos >= end:
            continue
        matches = OPTION_LABEL_RE.findall(ln.text)
        if matches:
            option_lines.append(ln)
            option_count += len(matches)
        m = CHOICE_ITEM_RE.match(ln.text)
        if m:
            numbered_choices[int(m.group(1))] = ln

    run = 0
    while run + 1 in numbered_choices:
        run += 1

    ends: list[Line] = []
    if option_count >= 4:
        ends.append(option_lines[-1])
    if run >= 4:
        ends.append(numbered_choices[run])
    if ends:
        last = max(ends, key=lambda ln: (ln.page, ln.y1))
        # A choice set as a stacked fraction puts its denominator on its own
        # line, overlapping the label's row — keep pulling in lines that still
        # overlap so the last choice is not cut in half.
        bottom = last.y1
        for ln in lines:
            if ln.page != last.page or (ln.page, ln.y0) >= end:
                continue
            if last.y0 <= ln.y0 < bottom:
                bottom = max(bottom, ln.y1)
        return (last.page, bottom + 4.0)
    return None


def _own_body_end(
    hdr: Header, limit: tuple[int, float], lines: list[Line]
) -> tuple[int, float] | None:
    """Where a question's own body stops, searching up to the next header.

    A question ends at its worked solution if one follows it, otherwise just
    after its multiple-choice block. Returns None when neither is found, so the
    caller can keep its conservative page-break default.
    """
    start = (hdr.page, hdr.y)
    end = limit
    solution_starts = [
        (ln.page, ln.y0 - 2.0)
        for ln in lines
        if start < (ln.page, ln.y0) < end and _sol_marker(ln.text)[0]
    ]
    if solution_starts:
        end = min(solution_starts)
    choice_end = _choice_block_end(start, end, lines)
    if choice_end is not None:
        return choice_end
    return end if solution_starts else None


def _is_cue_color(color: int) -> bool:
    """A saturated ink, as used to mark the correct choice.

    Body text is not always pure black — some generators emit near-black greys
    — so plain "not black" would flag ordinary punctuation.
    """
    red, green, blue = (color >> 16) & 0xFF, (color >> 8) & 0xFF, color & 0xFF
    return max(red, green, blue) - min(red, green, blue) >= 60


def _has_answer_cue(doc: fitz.Document, start: tuple[int, float], end: tuple[int, float]) -> bool:
    """Colored choice text or highlighter markup inside the span.

    A question printed without a worked solution can still show which choice is
    correct, so such a span is worth keeping as the answer.
    """
    for pno in range(start[0], min(end[0], len(doc) - 1) + 1):
        page = doc[pno]
        lo = start[1] if pno == start[0] else 0.0
        hi = end[1] if pno == end[0] else page.rect.height
        for annot in page.annots() or []:
            if annot.type[1].lower() in {"highlight", "underline", "squiggly"}:
                if annot.rect.y1 > lo and annot.rect.y0 < hi:
                    return True
        for block in page.get_text("dict")["blocks"]:
            for line in block.get("lines", []):
                for span in line["spans"]:
                    if not span["text"].strip():
                        continue
                    if span["bbox"][3] <= lo or span["bbox"][1] >= hi:
                        continue
                    if _is_cue_color(int(span.get("color", 0))):
                        return True
    return False


def _is_unanswered(
    doc: fitz.Document,
    start: tuple[int, float],
    end: tuple[int, float],
    lines: list[Line],
) -> bool:
    """True when the span holds a question and its choices and nothing else.

    Combined exam+solution files occasionally print a question the author never
    worked out and never marked; exporting that as an "answer" just repeats the
    question, so the caller drops it instead.
    """
    if any(
        start < (ln.page, ln.y0) < end and _sol_marker(ln.text)[0] for ln in lines
    ):
        return False
    choice_end = _choice_block_end(start, end, lines)
    if choice_end is None:
        return False
    if any(
        choice_end <= (ln.page, ln.y0) < end and ln.text.strip() for ln in lines
    ):
        return False
    return not _has_answer_cue(doc, start, end)


def _build_question_segments(
    numbered: list[tuple[Header, int, list[str]]],
    bounds: dict[int, PageBounds],
    part_end: tuple[int, float],
    *,
    lines: list[Line] | None = None,
    include_shared_setup: bool = True,
    trim_after_options: bool = False,
) -> list[Segment]:
    """One segment per question: shared setup (top of its group page) + body."""
    headers = [h for h, _, _ in numbered]
    segments: list[Segment] = []
    for i, (hdr, number, warnings) in enumerate(numbered):
        if i + 1 < len(headers):
            nxt = headers[i + 1]
            if nxt.page == hdr.page:
                end = limit = (nxt.page, nxt.y)
            else:
                # A page break usually means the next page opens with the next
                # question's own setup, so stop at this page's end. `limit` is
                # the true boundary — the option/solution scan below uses it to
                # recover questions whose body runs over the break.
                end = (nxt.page, bounds[nxt.page].top)
                limit = (nxt.page, nxt.y)
        else:
            end = limit = part_end

        if trim_after_options and lines is not None:
            own_end = _own_body_end(hdr, limit, lines)
            if own_end is not None:
                end = own_end

        slices = _span_slices((hdr.page, hdr.y), end, bounds)

        if include_shared_setup:
            first_on_page = next(h for h in headers if h.page == hdr.page)
            if first_on_page is not hdr:
                # Not first on its page: prepend the shared setup above the group.
                setup = _span_slices(
                    (hdr.page, bounds[hdr.page].top), (hdr.page, first_on_page.y), bounds
                )
                if setup and setup[0][2] - setup[0][1] >= MIN_SETUP_PT:
                    slices = setup + slices
            else:
                setup = _span_slices(
                    (hdr.page, bounds[hdr.page].top), (hdr.page, hdr.y), bounds
                )
                slices = setup + slices

        segments.append(Segment("question", number, slices, list(warnings)))
    return segments


def _build_answer_segments(
    numbered: list[tuple[Header, int, list[str]]],
    bounds: dict[int, PageBounds],
    doc_end: tuple[int, float],
    *,
    lines: list[Line] | None = None,
    doc: fitz.Document | None = None,
) -> list[Segment]:
    """One segment per worked solution: header to next header (may span pages).
    A combined header ("שאלה11+12") emits the same content for each number.

    With `lines` and `doc`, a span that turns out to hold only the question and
    its choices — no solution, no marked choice — is skipped rather than
    exported as an answer that just repeats the question.
    """
    headers = [h for h, _, _ in numbered]
    segments: list[Segment] = []
    for i, (hdr, number, warnings) in enumerate(numbered):
        if i + 1 < len(headers):
            end = (headers[i + 1].page, headers[i + 1].y)
        else:
            end = doc_end
        if (
            lines is not None
            and doc is not None
            and _is_unanswered(doc, (hdr.page, hdr.y), end, lines)
        ):
            continue
        slices = _span_slices((hdr.page, hdr.y), end, bounds)
        segments.append(Segment("answer", number, slices, list(warnings)))
        for e in hdr.extra:
            segments.append(
                Segment("answer", e, slices, [f"shares a solution with question {number}"])
            )
    return segments


def _build_interleaved_segments(
    numbered: list[tuple[Header, int, list[str]]],
    markers: list[tuple[int, float, int | None]],
    bounds: dict[int, PageBounds],
    doc_end: tuple[int, float],
    *,
    include_shared_setup: bool = True,
) -> list[Segment]:
    """Segments for exams where each question is followed by its own "פתרון"
    section (2022 winters).  A question runs from its header to the next
    event (its solution marker or the next question); a solution runs from
    its marker to the next event.  An unnumbered marker answers every
    question since the previous marker (paired 5%/3% questions share one)."""
    q_headers = [h for h, _, _ in numbered]
    events: list[tuple[int, float, str, object]] = []
    for hdr, n, warn in numbered:
        events.append((hdr.page, hdr.y, "q", (hdr, n, warn)))
    for page, y, num in markers:
        events.append((page, y, "s", num))
    events.sort(key=lambda e: (e[0], e[1]))

    segments: list[Segment] = []
    pending: list[int] = []
    last_num = 0
    for i, (page, y, kind, payload) in enumerate(events):
        nxt = events[i + 1] if i + 1 < len(events) else None
        end = (nxt[0], nxt[1]) if nxt else doc_end
        if kind == "q":
            hdr, n, warnings = payload
            if nxt and nxt[2] == "q" and nxt[0] != page:
                # next question's page starts with that question's setup
                end = (nxt[0], bounds[nxt[0]].top)
            slices = _span_slices((page, y), end, bounds)
            if include_shared_setup:
                first_on_page = next(h for h in q_headers if h.page == page)
                if first_on_page is not hdr:
                    setup = _span_slices(
                        (page, bounds[page].top), (page, first_on_page.y), bounds
                    )
                    if setup and setup[0][2] - setup[0][1] >= MIN_SETUP_PT:
                        slices = setup + slices
                else:
                    setup = _span_slices((page, bounds[page].top), (page, y), bounds)
                    slices = setup + slices
            segments.append(Segment("question", n, slices, list(warnings)))
            pending.append(n)
            last_num = max(last_num, n)
        else:
            num = payload
            warnings = []
            if num is not None:
                nums = [num]
            elif pending:
                nums = list(pending)
                if len(nums) > 1:
                    warnings = [
                        "solution shared by questions "
                        + "+".join(str(n2) for n2 in nums)
                    ]
            else:
                nums = [last_num + 1]
                warnings = ["solution marker with no matching question header"]
            if nxt and nxt[2] == "q" and nxt[0] != page:
                # next question's page starts with that question's setup
                end = (nxt[0], bounds[nxt[0]].top)
            slices = _span_slices((page, y), end, bounds)
            for n2 in nums:
                segments.append(Segment("answer", n2, slices, list(warnings)))
            last_num = max([last_num] + nums)
            pending = []
    return segments


def auto_split_pdf(
    src_path: str | Path,
    out_dir: str | Path | None = None,
    *,
    part: str = "auto",
    include_shared_setup: bool = True,
    trim_after_options: bool = False,
    drop_unanswered: bool = False,
) -> dict:
    """Split an exam PDF into question_NN.pdf / answer_NN.pdf plus index.json.

    `drop_unanswered` suits combined exam+solution files: a question the source
    never worked out and never marked produces no answer_NN.pdf at all.
    """
    if part not in {"auto", "questions", "answers"}:
        raise ValueError(f"unsupported part: {part}")
    src_path = Path(src_path)
    if out_dir is None:
        out_dir = src_path.parent / f"{src_path.stem}_split"
    out_dir = Path(out_dir)

    doc = fitz.open(src_path)
    cache = _ExportCache(doc)
    # Questions are exported from a copy with the answer highlighting removed,
    # so the correct option is not given away; answers keep the original look.
    clean_doc = fitz.open(src_path)
    _strip_highlights(clean_doc)
    clean_cache = _ExportCache(clean_doc)
    index: dict = {"source": str(src_path), "questions": [], "answers": []}
    try:
        lines, bounds = _extract_lines(doc)
        headers = _find_exam_headers(lines)
        if not headers:
            index["error"] = "no question headers found (unsupported format?)"
            return index
        if trim_after_options and part in {"auto", "questions"}:
            _redact_colored_solution_work(doc, clean_doc, headers)

        exam_headers, sol_headers = _split_exam_and_solutions(headers)
        last_page = len(doc) - 1
        doc_end = (last_page, bounds[last_page].bottom)
        # The closing formula sheet belongs to no question — end the last
        # segment there instead of running on to the final page.
        after_last = (headers[-1].page, headers[-1].y)
        appendix = [
            (ln.page, max(bounds[ln.page].top, ln.y0 - 2.0))
            for ln in lines
            if (ln.page, ln.y0) > after_last and APPENDIX_RE.match(ln.text)
        ]
        if appendix:
            doc_end = min(min(appendix), doc_end)

        answer_filter = (
            {"lines": lines, "doc": doc} if drop_unanswered else {}
        )

        if part == "questions":
            segments = _build_question_segments(
                _assign_numbers(headers),
                bounds,
                doc_end,
                lines=lines,
                include_shared_setup=include_shared_setup,
                trim_after_options=trim_after_options,
            )
        elif part == "answers":
            segments = _build_answer_segments(
                _assign_numbers(headers), bounds, doc_end, **answer_filter
            )
        else:
            interleaved: list[tuple[int, float, int | None]] = []
            if not sol_headers:
                first_pos = (exam_headers[0].page, exam_headers[0].y)
                last_pos = (exam_headers[-1].page, exam_headers[-1].y)
                markers = []
                for ln in lines:
                    pos = (ln.page, ln.y0 - 2.0)
                    if pos <= first_pos:
                        continue
                    if _sol_marker(ln.text)[0]:
                        markers.append(
                            (ln.page, ln.y0 - 2.0, _sol_marker(ln.text)[1])
                        )
                if sum(1 for m in markers if (m[0], m[1]) < last_pos) >= 3:
                    interleaved = markers

            if interleaved:
                segments = _build_interleaved_segments(
                    _assign_numbers(exam_headers),
                    interleaved,
                    bounds,
                    doc_end,
                    include_shared_setup=include_shared_setup,
                )
            else:
                sol_start: tuple[int, float] | None = None
                if sol_headers:
                    sol_start = (sol_headers[0].page, sol_headers[0].y)
                else:
                    last_exam = exam_headers[-1]
                    sol_headers, sol_start = _find_solutions_after_title(
                        lines,
                        (last_exam.page, last_exam.y),
                        _image_item_markers(doc),
                    )
                if sol_start is not None:
                    exam_end = (
                        sol_start[0],
                        max(bounds[sol_start[0]].top, sol_start[1]),
                    )
                else:
                    exam_end = doc_end

                segments = _build_question_segments(
                    _assign_numbers(exam_headers),
                    bounds,
                    exam_end,
                    lines=lines,
                    include_shared_setup=include_shared_setup,
                    trim_after_options=trim_after_options,
                )
                segments += _build_answer_segments(
                    _assign_numbers(sol_headers), bounds, doc_end
                )

        out_dir.mkdir(parents=True, exist_ok=True)
        for seg in segments:
            name = f"{seg.part}_{seg.number:02d}.pdf"
            out_path = out_dir / name
            seg_cache = clean_cache if seg.part == "question" else cache
            if _write_segment_pdf(seg_cache, seg.slices, out_path):
                entry = {
                    "n": seg.number,
                    "file": name,
                    "pages": sorted({s[0] + 1 for s in seg.slices}),
                }
                if seg.warnings:
                    entry["warnings"] = seg.warnings
                index["questions" if seg.part == "question" else "answers"].append(entry)
    finally:
        cache.close()
        doc.close()
        clean_cache.close()
        clean_doc.close()

    index["question_count"] = len(index["questions"])
    index["answer_count"] = len(index["answers"])
    (out_dir / "index.json").write_text(
        json.dumps(index, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    return index


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("pdfs", nargs="+", help="exam PDF(s) to split")
    parser.add_argument("-o", "--out-dir", help="output directory (single PDF only)")
    parser.add_argument(
        "--part",
        choices=("auto", "questions", "answers"),
        default="auto",
        help="force question-only or answer-only segmentation",
    )
    parser.add_argument(
        "--no-shared-setup",
        action="store_true",
        help="do not prepend the top of a page to later questions on that page",
    )
    parser.add_argument(
        "--trim-after-options",
        action="store_true",
        help="end questions after their multiple-choice option block",
    )
    args = parser.parse_args()

    for pdf in args.pdfs:
        out_dir = args.out_dir if len(args.pdfs) == 1 and args.out_dir else None
        result = auto_split_pdf(
            pdf,
            out_dir,
            part=args.part,
            include_shared_setup=not args.no_shared_setup,
            trim_after_options=args.trim_after_options,
        )
        qs, ans = result.get("question_count", 0), result.get("answer_count", 0)
        status = result.get("error", f"{qs} questions, {ans} answers")
        print(f"{pdf}: {status}")
        for part in ("questions", "answers"):
            for entry in result.get(part, []):
                for w in entry.get("warnings", []):
                    print(f"  warning ({part[:-1]} {entry['n']}): {w}")


if __name__ == "__main__":
    main()
