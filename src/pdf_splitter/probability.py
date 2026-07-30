"""Prepare the supplied Introduction to Probability (104034) exam collection.

Fourteen sittings, 2023-2026, and the only collection with nothing to read:
nine of the fourteen papers are photographs of the printed exam with no text
layer at all, and of the five that carry text, two extract as mojibake. So the
questions are found in the picture of the page rather than in its words.

Each paper is eight multiple-choice questions and two or three open ones. The
multiple-choice questions are read by what closes them: a row of five or six
short alternatives spread across the page, which no other line of an exam
looks like, and which the layout keeps whether the paper was typeset in Word
or in LaTeX. Counting those rows counts the questions, and it also says which
pages hold them — the cover page of instructions and standard normal table has
none, and neither does the page of announcements one paper opens with.

The open questions at the end carry no alternatives, and are read instead by
the hanging indent every paper sets its question numbers with: the number sits
about eighteen points further right than the text it heads, a sub-section
("א.", "ב.") about half that. The phone-scanned papers are keystoned, so the
right margin drifts by as much as fifty points down a page — more than the
indent being measured — and it is therefore fitted as a line rather than taken
as a constant.

    python -m pdf_splitter.probability "Probability Tests" --out ProbabilitySplit

The collection is exam papers only: no sitting comes with a solution, an
answer key, or a marked-up copy, so every sitting yields questions and no
answers.
"""

from __future__ import annotations

import argparse
import json
import re
import statistics
from dataclasses import dataclass
from pathlib import Path

import fitz
from PIL import Image

from .auto_split import PageBounds, _span_slices
from .splitter import _ExportCache, _write_segment_pdf

COURSE = "Probability"

# Rendering resolution. The indent that marks a question is about 18 points,
# so a point has to be worth enough pixels to measure a third of that.
DPI = 150
PT = 72.0 / DPI
# Grey below this is ink. The scans carry a grey cast and JPEG haze around the
# glyphs; anything lighter than this is paper.
INK = 170
# A row of the page counts as inked if this fraction of it is ink, which keeps
# scanner speckle out of the line grouping.
ROW_INK = 0.004
COL_INK = 0.05
# Rows this close together are the same line of text. Hebrew has no
# descenders to speak of, but the exams set fractions and integrals two decks
# high and those must not break into separate lines.
GAP_PT = 3.5
# A column this solid through a band is a drawn rule — the border of the box
# left for the answer, which reaches further right than the question numbers.
RULE_FRAC = 0.7
# A band no taller than this whose ink runs this far across the page is the
# horizontal rule of such a box rather than a line of text.
RULE_HEIGHT_PT = 4.0
RULE_SPAN = 0.35
# What a row of multiple-choice alternatives looks like: four or more short
# clusters of ink, separated by clear blanks, running most of the way across
# the page. Nothing else in these papers is shaped like that — a line of text
# has one long cluster, a formula sits in the middle of the page, and the
# lettered sub-sections of an open question are one to a line.
MIN_CHOICES = 4
CHOICE_GAP_PT = 9.0
MAX_CHOICE_PT = 70.0
CHOICE_SPREAD = 0.45
# Alternatives set two decks high — "3/8" over a rule — break into separate
# bands, and each band reads as a row of its own. Rows this close together are
# one row of alternatives.
CHOICE_MERGE_PT = 32.0
# Two headings this close together are one heading of two lines: several papers
# print "(מבוססת על תרגילי הבית)" — which exercise the question is based on —
# on a line of its own above the question, hanging in the same margin.
MERGE_PT = 30.0
# A crop opens a little above the line that heads it, so the tops of the tallest
# glyphs are not shaved off.
LIFT_PT = 3.0
# The scans darken along the edge of the sheet, and one leaves a black band
# where the phone caught the desk. Nothing that close to the edge is content.
EDGE_PT = 26.0
# How far past the fitted right margin a line has to reach to head a question.
# Sub-sections hang about 7 points out, question numbers 15 to 20. Past 40 is
# no indent at all but the dark edge of the sheet, or a border the scan drew.
MARGIN_PT = 11.0
MAX_MARGIN_PT = 40.0
# How far a line's indent may fall short of the indent measured off the paper's
# own multiple-choice questions and still head an open one.
INDENT_TOLERANCE = 5.0
# A fit needs this many lines to mean anything. Pages that give a question a
# whole page of ruled answer space have fewer, and borrow the fit.
MIN_SUPPORT = 6
# How near the fitted margin a line has to sit to count as supporting it.
FIT_TOLERANCE = 3.0
# Papers run to ten or eleven questions. A reading well outside that is not a
# reading of the questions.
MAX_QUESTIONS = 20
MIN_QUESTIONS = 5
# The shape the papers state for themselves — "חלק רב ברירה (שאלות 1-8)" and
# "חלק פתוח (שאלות 9-10)" — which the Word-typeset years keep to exactly and
# the LaTeX ones stretch to nine multiple-choice questions. A reading outside
# this is reported rather than corrected.
MIN_CHOICE_QUESTIONS = 8
MAX_CHOICE_QUESTIONS = 10
MAX_OPEN_QUESTIONS = 3
# Running headers and footers ("מבוא להסתברות ח 104034", "CamScanner נסרק עם")
# repeat down the paper at the same height and the same width. How much either
# may wander and still be the same piece of furniture — a scan is never
# registered twice the same way — and on how many pages it has to appear.
RUNNING_Y_PT = 10.0
RUNNING_X_PT = 30.0
MIN_RUNNING_PAGES = 3

# "הסתברות 24 חורף א" — the year is the last two digits of the calendar year
# the sitting was held in, and no paper spells the word "מועד".
NAME_RE = re.compile(r"(\d\d)\s*(חורף|אביב|קיץ)\s*([אבג])")
SEASONS = {"חורף": "Winter", "אביב": "Spring", "קיץ": "Summer"}
MOEDS = {"א": "A", "ב": "B", "ג": "C"}
NON_ALNUM_RE = re.compile(r"[^a-z0-9]+")

Position = tuple[int, float]


@dataclass(frozen=True)
class Band:
    """One line of ink on a page, in PDF points from the top."""

    page: int
    y0: float
    y1: float
    x0: float
    x1: float

    @property
    def middle(self) -> float:
        return (self.y0 + self.y1) / 2


@dataclass(frozen=True)
class Sitting:
    year: int | None
    season: str | None
    moed: str | None

    def __bool__(self) -> bool:
        return bool(self.year and self.season and self.moed)

    @property
    def label(self) -> str:
        return f"{self.year} {self.season} — Moed {self.moed}"

    @property
    def slug(self) -> str:
        return f"probability-{self.year}-{self.season.lower()}-{self.moed.lower()}"


@dataclass
class Paper:
    path: Path
    sitting: Sitting
    headings: list[Position]
    bounds: dict[int, PageBounds]
    first_page: int
    warnings: list[str]


def _sitting(path: Path) -> Sitting:
    match = NAME_RE.search(path.stem)
    if not match:
        return Sitting(None, None, None)
    year, season, moed = match.groups()
    return Sitting(2000 + int(year), SEASONS[season], MOEDS[moed])


def _profile(img: Image.Image, size: tuple[int, int]) -> list[float]:
    """Mean ink along one axis, as a fraction of full black."""
    flat = img.resize(size, Image.BOX)
    return [1.0 - value / 255.0 for value in flat.get_flattened_data()]


def _runs(profile: list[float], threshold: float, gap: int) -> list[tuple[int, int]]:
    """Stretches above the threshold, joined across gaps of up to `gap`."""
    out: list[tuple[int, int]] = []
    i = 0
    while i < len(profile):
        if profile[i] <= threshold:
            i += 1
            continue
        last = i
        j = i
        while j < len(profile):
            if profile[j] > threshold:
                last = j
            elif j - last > gap:
                break
            j += 1
        out.append((i, last))
        i = j
    return out


def _page_ink(page: fitz.Page, pno: int) -> tuple[list[Band], list[float]]:
    """One page's lines of text, and the foot of each row of alternatives.

    A rule drawn across the page is neither, and drops out here: the papers
    rule a box for the answer to an open question, and its border reaches
    further right than any question number.
    """
    pixmap = page.get_pixmap(dpi=DPI, colorspace=fitz.csGRAY)
    image = Image.frombytes("L", (pixmap.width, pixmap.height), pixmap.samples)
    image = image.point(lambda value: 0 if value < INK else 255)
    edge = int(EDGE_PT / PT)
    inner = image.crop((edge, edge, image.width - edge, image.height - edge))
    width, height = inner.size
    if width <= 0 or height <= 0:
        return [], []

    bands: list[Band] = []
    choices: list[float] = []
    for top, bottom in _runs(
        _profile(inner, (1, height)), ROW_INK, max(1, int(GAP_PT / PT))
    ):
        strip = inner.crop((0, top, width, bottom + 1))
        columns = _profile(strip, (width, 1))
        if (bottom - top + 1) * PT <= RULE_HEIGHT_PT:
            drawn = [i for i, value in enumerate(columns) if value > 0.4]
            if drawn and (drawn[-1] - drawn[0]) > RULE_SPAN * width:
                continue
        # A vertical rule is solid through the band's whole height; a glyph is
        # not, so the border of a box the band falls inside drops out here.
        inked = [i for i, value in enumerate(columns) if COL_INK < value <= RULE_FRAC]
        if not inked:
            continue
        clusters = _runs(columns, COL_INK, int(CHOICE_GAP_PT / PT))
        if (
            len(clusters) >= MIN_CHOICES
            and all((c[1] - c[0] + 1) * PT <= MAX_CHOICE_PT for c in clusters)
            and (clusters[-1][1] - clusters[0][0]) >= CHOICE_SPREAD * width
        ):
            choices.append((bottom + 1 + edge) * PT)
        bands.append(
            Band(
                pno,
                (top + edge) * PT,
                (bottom + 1 + edge) * PT,
                (inked[0] + edge) * PT,
                (inked[-1] + 1 + edge) * PT,
            )
        )
    return bands, choices


def _line(points: list[tuple[float, float]]) -> tuple[float, float]:
    """Least-squares slope and intercept of x against y."""
    n = len(points)
    mean_y = sum(y for y, _ in points) / n
    mean_x = sum(x for _, x in points) / n
    spread = sum((y - mean_y) ** 2 for y, _ in points)
    if spread == 0:
        return 0.0, mean_x
    slope = sum((y - mean_y) * (x - mean_x) for y, x in points) / spread
    return slope, mean_x - slope * mean_y


def _margin(bands: list[Band]) -> tuple[float, float, int] | None:
    """The right margin of one page, as a line, with the count that backs it.

    Taken as the line the most lines of text end on. The paper's body is
    justified to the margin, so it is the widest agreement on the page — while
    the question numbers, hanging past it, are a handful of lines at most, and
    the formulas and lists of choices that stop short of it agree on nothing.
    """
    points = [(band.middle, band.x1) for band in bands]
    if len(points) < 2:
        return None
    best: list[tuple[float, float]] = []
    for i, first in enumerate(points):
        for second in points[i + 1 :]:
            if first[0] == second[0]:
                continue
            slope = (second[1] - first[1]) / (second[0] - first[0])
            intercept = first[1] - slope * first[0]
            support = [
                p
                for p in points
                if abs(p[1] - (slope * p[0] + intercept)) <= FIT_TOLERANCE
            ]
            if len(support) > len(best):
                best = support
    if not best:
        return None
    slope, intercept = _line(best)
    return slope, intercept, len(best)


def _running(bands: list[Band]) -> set[Band]:
    """Bands that are the paper's running header or footer.

    Some papers reprint the course and exam number across the top of each page,
    and the phone-scanned ones carry a scanning app's footer. Position alone
    does not tell one from a question heading — half the papers open a page with
    a question — so a band only counts as running furniture if it repeats down
    the paper at the same height *and* the same width. A heading's width is set
    by how long the question happens to be.
    """
    edges: dict[int, list[Band]] = {}
    for band in bands:
        edges.setdefault(band.page, []).append(band)
    candidates: list[Band] = []
    for page in edges.values():
        candidates.append(min(page, key=lambda b: b.y0))
        candidates.append(max(page, key=lambda b: b.y1))

    out: set[Band] = set()
    for band in candidates:
        alike = [
            other
            for other in candidates
            if abs(other.y0 - band.y0) <= RUNNING_Y_PT
            and abs(other.x0 - band.x0) <= RUNNING_X_PT
            and abs(other.x1 - band.x1) <= RUNNING_X_PT
        ]
        if len({other.page for other in alike}) >= MIN_RUNNING_PAGES:
            out.update(alike)
    return out


def _bounds(
    bands: list[Band], running: set[Band], doc: fitz.Document
) -> dict[int, PageBounds]:
    """Per-page content bounds, with the running header and footer trimmed."""
    kept: dict[int, tuple[float, float]] = {}
    for band in bands:
        if band in running:
            continue
        top, bottom = kept.get(band.page, (band.y0, band.y1))
        kept[band.page] = (min(top, band.y0), max(bottom, band.y1))
    out: dict[int, PageBounds] = {}
    for pno in range(len(doc)):
        height = doc[pno].rect.height
        if pno in kept:
            top, bottom = kept[pno]
            out[pno] = PageBounds(max(0.0, top - 6.0), min(height, bottom + 6.0))
        else:
            out[pno] = PageBounds(0.0, height)
    return out


def _overhangs(bands: list[Band], running: set[Band]) -> dict[Band, float]:
    """How far past its page's right margin each line of text reaches."""
    by_page: dict[int, list[Band]] = {}
    for band in bands:
        by_page.setdefault(band.page, []).append(band)

    fits: dict[int, tuple[float, float, int]] = {}
    for pno, page in by_page.items():
        fit = _margin([b for b in page if b not in running])
        if fit is not None:
            fits[pno] = fit
    backed = [fit for fit in fits.values() if fit[2] >= MIN_SUPPORT]

    out: dict[Band, float] = {}
    for pno, page in by_page.items():
        fit = fits.get(pno)
        if fit is None:
            continue
        if fit[2] < MIN_SUPPORT and backed:
            # A page given over to ruled answer space has too few lines of its
            # own to fit a margin to — sometimes only the question that opens it
            # and one sub-section, in which case its own widest line is the
            # hanging number and fitting to it hides what we are looking for.
            # The margin is a property of the layout, so borrow it from the
            # pages that do show it.
            fit = (
                statistics.median(f[0] for f in backed),
                statistics.median(f[1] for f in backed),
                fit[2],
            )
        slope, intercept, _ = fit
        for band in page:
            if band not in running:
                out[band] = band.x1 - (slope * band.middle + intercept)
    return out


def _merged(tops: list[Position]) -> list[Position]:
    """One heading per question, where a heading runs over two lines."""
    out: list[Position] = []
    for top in tops:
        if out and out[-1][0] == top[0] and top[1] - out[-1][1] <= MERGE_PT:
            continue
        out.append(top)
    return out


def _choice_rows(choices: dict[int, list[float]]) -> list[Position]:
    """The foot of each row of alternatives, one per multiple-choice question."""
    rows: list[Position] = []
    for pno in sorted(choices):
        for y in sorted(choices[pno]):
            if rows and rows[-1][0] == pno and y - rows[-1][1] <= CHOICE_MERGE_PT:
                rows[-1] = (pno, y)
                continue
            rows.append((pno, y))
    return rows


def _choice_block(rows: list[Position]) -> list[Position]:
    """The multiple-choice part: the longest unbroken run of pages carrying rows.

    The alternatives are the only thing shaped this way, but a formula set as a
    row of short terms is not far off, and the open questions at the end of a
    paper are where that happens. They sit past a page that carries nothing,
    which is what tells them from the multiple-choice part.
    """
    if not rows:
        return []
    runs: list[list[Position]] = [[rows[0]]]
    for row in rows[1:]:
        if row[0] - runs[-1][-1][0] <= 1:
            runs[-1].append(row)
        else:
            runs.append([row])
    return max(runs, key=len)


def _indent(overhangs: dict[Band, float], starts: list[Position]) -> float:
    """How far this paper hangs a question number past its margin.

    Measured rather than assumed: the multiple-choice questions have already
    been placed by their alternatives, so the line that opens each of them is
    known, and how far it hangs is what an open question's number will hang.
    """
    measured: list[float] = []
    for pno, y in starts:
        below = [b for b in overhangs if b.page == pno and b.y1 > y]
        if below:
            measured.append(overhangs[min(below, key=lambda b: b.y0)])
    plausible = [m for m in measured if MARGIN_PT <= m <= MAX_MARGIN_PT]
    return statistics.median(plausible) if plausible else MARGIN_PT + 4.0


def _open_headings(
    overhangs: dict[Band, float], after: Position, indent: float
) -> list[Position]:
    """The open questions after the multiple-choice part, by their indent.

    A sub-section of an open question hangs too, about half as far, and there
    are more of them than there are questions — so the paper's own indent, not
    a bare threshold, is what a heading has to match.
    """
    floor = max(MARGIN_PT, indent - INDENT_TOLERANCE)
    return sorted(
        (band.page, band.y0 - LIFT_PT)
        for band, over in overhangs.items()
        if floor <= over <= MAX_MARGIN_PT and (band.page, band.y1) > after
    )


def _read(path: Path) -> Paper:
    doc = fitz.open(path)
    try:
        bands: list[Band] = []
        choices: dict[int, list[float]] = {}
        for pno in range(len(doc)):
            page_bands, page_choices = _page_ink(doc[pno], pno)
            bands += page_bands
            if page_choices:
                choices[pno] = page_choices
        running = _running(bands)
        bounds = _bounds(bands, running, doc)
    finally:
        doc.close()

    block = _choice_block(_choice_rows(choices))
    warnings: list[str] = []
    if not block:
        warnings.append("no rows of alternatives found; the paper could not be read")
        return Paper(path, _sitting(path), [], bounds, 0, warnings)

    first = block[0][0]
    # The first question opens where the page holding the first row of
    # alternatives does, and every row but the last opens the next question.
    tops: list[Position] = [(first, bounds[first].top)]
    tops += [(pno, y + LIFT_PT) for pno, y in block[:-1]]
    # Past the last row of alternatives the paper turns to its open questions,
    # which carry none and are read by their indent instead.
    overhangs = _overhangs([b for b in bands if b.page >= first], running)
    tail = _open_headings(overhangs, block[-1], _indent(overhangs, tops))
    tops += tail

    if not MIN_CHOICE_QUESTIONS <= len(block) <= MAX_CHOICE_QUESTIONS:
        warnings.append(
            f"{len(block)} rows of alternatives read where a paper carries eight "
            "or nine multiple-choice questions"
        )
    if len(tail) > MAX_OPEN_QUESTIONS:
        # The indent that tells an open question's number from one of its own
        # sub-sections is about eight points, and the keystone of a phone scan
        # moves the margin by more than that. Where more sub-sections than
        # questions come back, each sub-section becomes an entry of its own.
        warnings.append(
            f"{len(tail)} open questions read where a paper carries two or three; "
            "some are a sub-section split off on its own"
        )
    elif not tail:
        # The LaTeX-typeset papers set their question numbers flush with the
        # text, so there is no indent to read the open questions by and they
        # stay attached to the last multiple-choice question.
        warnings.append(
            "no indent to read the open questions by; they are left on the end "
            "of the last multiple-choice question"
        )
    if not MIN_QUESTIONS <= len(tops) <= MAX_QUESTIONS:
        warnings.append(
            f"{len(tops)} questions read from the page; the papers carry ten"
        )
    return Paper(path, _sitting(path), _merged(tops), bounds, first, warnings)


def _spans(paper: Paper) -> list[tuple[int, Position, Position]]:
    """Where each question runs from and to."""
    out: list[tuple[int, Position, Position]] = []
    last = max(paper.bounds)
    for i, top in enumerate(paper.headings):
        end = (
            paper.headings[i + 1]
            if i + 1 < len(paper.headings)
            else (last, paper.bounds[last].bottom)
        )
        out.append((i + 1, top, end))
    return out


def _export(paper: Paper, exam_dir: Path) -> list[dict]:
    doc = fitz.open(paper.path)
    cache = _ExportCache(doc)
    entries: list[dict] = []
    try:
        for number, start, end in _spans(paper):
            slices = _span_slices(start, end, paper.bounds)
            name = f"question_{number:02d}.pdf"
            if _write_segment_pdf(cache, slices, exam_dir / name):
                entries.append(
                    {
                        "n": number,
                        "file": name,
                        "pages": sorted({s[0] + 1 for s in slices}),
                    }
                )
    finally:
        cache.close()
        doc.close()
    return entries


def _clear_generated(exam_dir: Path) -> None:
    exam_dir.mkdir(parents=True, exist_ok=True)
    for pattern in ("question_*.pdf", "answer_*.pdf"):
        for path in exam_dir.glob(pattern):
            path.unlink()


def _slug(path: Path) -> str:
    slug = NON_ALNUM_RE.sub("-", path.stem.lower()).strip("-")
    return f"probability-{slug or 'exam'}"


def _fallback_label(path: Path) -> str:
    return re.sub(r"\s+", " ", path.stem).strip(" +-.") or path.stem


def _split(paper: Paper, out_root: Path) -> dict:
    sitting = paper.sitting
    exam_dir = out_root / (sitting.slug if sitting else _slug(paper.path))
    _clear_generated(exam_dir)
    questions = _export(paper, exam_dir)
    index = {
        "label": sitting.label if sitting else _fallback_label(paper.path),
        "year": sitting.year,
        "season": sitting.season,
        "moed": sitting.moed,
        "course": COURSE,
        "strategy": "exam only",
        # Which page the questions were found to start on: most papers open with
        # a cover page of instructions and the standard normal table, one adds a
        # page of announcements after it, and two have no cover page at all.
        "first_question_page": paper.first_page + 1,
        "source": {"questions": str(paper.path), "answers": None},
        "questions": questions,
        "answers": [],
        "question_count": len(questions),
        "answer_count": 0,
    }
    if paper.warnings:
        index["warnings"] = paper.warnings
    (exam_dir / "index.json").write_text(
        json.dumps(index, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    return {"dir": exam_dir.name, **index}


def prepare(root: Path, out_root: Path) -> list[dict]:
    pdfs = sorted(root.rglob("*.pdf"))
    if not pdfs:
        raise SystemExit(f"no PDF files found under {root}")

    out_root.mkdir(parents=True, exist_ok=True)
    papers = [_read(path) for path in pdfs]
    results = [
        _split(paper, out_root)
        for paper in sorted(papers, key=lambda p: (p.sitting.year or 0, p.path.stem))
    ]
    for result in results:
        print(f"{result['label']}: {result['question_count']} questions")
        for warning in result.get("warnings", []):
            print(f"    {warning}")

    (out_root / "probability-report.json").write_text(
        json.dumps(results, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    questions = sum(r["question_count"] for r in results)
    print(f"\n{len(results)} sittings, {questions} questions, no answers in the source")
    return results


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("root", help="folder containing the Probability PDFs")
    parser.add_argument("--out", default="ProbabilitySplit")
    args = parser.parse_args()
    prepare(Path(args.root).resolve(), Path(args.out).resolve())


if __name__ == "__main__":
    main()
