"""Prepare the supplied Introduction to Mechatronics (034022) exam collection.

Twenty-four sittings, 2005-2021, in two quite different examination styles and
one style of answer:

* multiple-choice papers of twenty items, grouped under topic titles
  ("שאלות 3-1 – תופעות מעבר"), which is what the course set from 2009 on; and
* open papers of three or four questions, each with lettered sub-sections,
  which is what it set before that and again in the spring semesters.

Almost every multiple-choice paper in the collection is a marked copy: the
correct choice is highlighted in yellow, which the question crop drops and the
answer crop keeps. That highlight is the only answer most of these sittings
have — six papers carry no cue at all, two work each question out beside it,
and one is a worked solution with no exam file to go with it.

    python -m pdf_splitter.mechatronics "Mechatronics Tests" --out MechatronicsSplit

The item numbers cannot be read in half the multiple-choice papers: they are
typeset in fonts whose character map is wrong, so "10" extracts as ".4" and
"20" as "04". The items are therefore found by what follows them — a block of
four to six lettered alternatives, which nothing else on an exam page looks
like — and numbered by their position on the page rather than by their text.
"""

from __future__ import annotations

import argparse
import json
import re
from dataclasses import dataclass, field
from pathlib import Path

import fitz

from .auto_split import (
    HEBREW_RE,
    HIGHLIGHT_FILL_RE,
    Header,
    Line,
    _extract_lines,
    _span_slices,
    _strip_highlights,
    auto_split_pdf,
)
from .splitter import _ExportCache, _write_segment_pdf

COURSE = "Intro to Mechatronics"

# Papers whose crops cannot be read off their headings: exam dir -> part ->
# question number -> (page, y0, y1) slices, in PDF points from the page top,
# with 0-based pages. A number listed here replaces whatever the header search
# produced; a number absent from it is added.
SEGMENT_OVERRIDES: dict[
    str, dict[str, dict[int, list[tuple[int, float, float]]]]
] = {
    # The five 2005-2006 papers are photocopies with no text layer at all —
    # nothing to search for a heading in — so their questions, and the
    # handwritten solutions that follow them, are measured off the page.
    "mechatronics-2005-spring-a": {
        "question": {
            1: [(0, 235.0, 730.0)],
            2: [(1, 80.0, 415.0)],
            3: [(1, 415.0, 780.0)],
            4: [(2, 62.0, 470.0)],
        },
        "answer": {
            1: [(3, 90.0, 810.0)],
            2: [(4, 62.0, 810.0)],
            3: [(5, 30.0, 810.0), (6, 30.0, 810.0)],
            4: [(7, 85.0, 600.0)],
        },
    },
    "mechatronics-2005-winter-a": {
        "question": {
            1: [(0, 248.0, 600.0)],
            2: [(1, 92.0, 388.0)],
            3: [(1, 388.0, 560.0)],
        }
    },
    # Its third question and the solution to it are not in the file.
    "mechatronics-2005-spring-b": {
        "question": {
            1: [(0, 250.0, 740.0)],
            2: [(1, 80.0, 490.0)],
        },
        "answer": {
            1: [(2, 15.0, 815.0), (3, 15.0, 815.0)],
            2: [(4, 15.0, 800.0), (5, 20.0, 420.0)],
        },
    },
    # The last solution is headed 3 by the hand that wrote it, but it works
    # out the fourth question; the third has no solution here.
    "mechatronics-2005-spring-c": {
        "question": {
            1: [(0, 240.0, 620.0)],
            2: [(1, 40.0, 450.0)],
            3: [(1, 450.0, 730.0)],
            4: [(2, 45.0, 530.0)],
        },
        "answer": {
            1: [(3, 15.0, 815.0), (4, 20.0, 700.0)],
            2: [(5, 15.0, 815.0), (6, 15.0, 815.0), (7, 15.0, 800.0)],
            4: [(8, 15.0, 815.0)],
        },
    },
    # Its alternatives being drawings, there is no block of them to end the
    # question at, so it would otherwise run on into its own solution.
    "mechatronics-2011-winter-a": {
        "question": {14: [(15, 70.0, 752.0), (16, 40.0, 452.0)]},
    },
    "mechatronics-2006-spring-a": {
        "question": {
            1: [(0, 330.0, 700.0)],
            2: [(1, 38.0, 385.0)],
            3: [(1, 385.0, 710.0)],
            4: [(2, 20.0, 320.0)],
        },
        "answer": {
            1: [(3, 15.0, 815.0)],
            2: [(4, 15.0, 800.0)],
            3: [(5, 20.0, 790.0)],
            4: [(6, 15.0, 720.0)],
        },
    },
}
# Crops to delete after splitting: exam dir -> part -> numbers.
SEGMENT_DROPS: dict[str, dict[str, set[int]]] = {}
# Question headings that are part of a scanned page rather than text, given
# as the (page, y) they stand at, in PDF points with 0-based pages. The 2007
# paper writes its questions out and works them by hand underneath; the last
# two pages of statements were scanned back in with the answers, so nothing
# of their headings is left to read.
DRAWN_HEADINGS: dict[str, list[tuple[int, float]]] = {
    "mechatronics-2007-winter-a": [(4, 60.0), (6, 60.0)],
    # A question whose five alternatives are timing diagrams, with the worked
    # solution to it in between, so neither the alternatives nor the space
    # around them says a question opens here.
    "mechatronics-2011-winter-a": [(15, 70.0)],
}

YEAR_RE = re.compile(r"20\d\d")
# "FinalA w2015" — the winter semester that opens in the autumn of 2015 is
# examined in the February after it, so the sitting is 2016.
SEMESTER_RE = re.compile(r"\bw\s*(20\d\d)", re.I)
# Hebrew year without its geresh: תשסח, תשעב, תשפא.
HEBREW_YEAR_RE = re.compile(r"תש[א-ת]{1,2}")
HEBREW_LETTER_VALUES = {
    "א": 1, "ב": 2, "ג": 3, "ד": 4, "ה": 5, "ו": 6, "ז": 7, "ח": 8, "ט": 9,
    "י": 10, "כ": 20, "ל": 30, "מ": 40, "נ": 50, "ס": 60, "ע": 70, "פ": 80,
    "צ": 90, "ק": 100, "ר": 200, "ש": 300, "ת": 400,
}
SEASON_RE = re.compile(r"winter|spring|summer|חורף|אביב|קיץ", re.I)
SEASONS = {
    "winter": "Winter", "spring": "Spring", "summer": "Summer",
    "חורף": "Winter", "אביב": "Spring", "קיץ": "Summer",
}
MOED_RE = re.compile(
    r"(?:term|final|moed|מועד)[\s_]*([abcאבג])(?![a-zא-ת\d])", re.I
)
# "FinalA w2015 sol" writes the sitting letter onto the word.
MOED_RUN_ON_RE = re.compile(r"\bfinal\s*([abc])\b", re.I)
# "מבחן 2007 א שאלה 3" — the sitting letter stands on its own after the year.
MOED_BARE_RE = re.compile(r"20\d\d\s+([אבג])(?![א-ת])")
MOEDS = {"a": "A", "b": "B", "c": "C", "א": "A", "ב": "B", "ג": "C"}
NON_ALNUM_RE = re.compile(r"[^a-z0-9]+")
# Papers held one question to a file ("חורף 2008 מועד ב שאלה 1").
FILE_QUESTION_RE = re.compile(r"שאלה\s*(\d{1,2})\s*$")
ANSWER_HINT_RE = re.compile(r"פתרון|תשובות|(?:^|[\s_-])sol\w*", re.I)

# Directional marks and the punctuation that RTL extraction pushes in front of
# a label or an item number.
LEAD = r"[\s‎‏‪-‮().,]*"
# A multiple-choice alternative, in either order the extraction can leave it:
# the letter with its bracket after it ("א.", "א)", "א("), or the bracket
# first (".א", ")א", "( א5"), which needs no closing mark but must not run on
# into a Hebrew word.
# A choice block usually runs א to ה, but a paper that sets two questions to
# a page sometimes carries the lettering on across them, as far as ט. The
# further letters are only read with their bracket after them: "ט" is also how
# a scrambled "פשט" ("simplify") comes out at the head of a question.
OPTION_LETTERS = "אבגדהוזחט"
LEADING_LETTERS = "אבגדהו"
OPTION_LABEL_RE = re.compile(
    rf"(?:^|[\s‎‏])(?:([{OPTION_LETTERS}])\s*[.)(]"
    rf"|[.)(]\s*([{LEADING_LETTERS}])(?![א-ת]))"
)
# An item number and the bracket or stop after it. The number is matched as
# digits-and-stops because the broken fonts render some digits as a full stop
# — which is also why a number closed by a stop has to be followed by a space
# before its question: "0.7" is a value, "7 . 1 מחברים" is question seven.
ITEM_RE = re.compile(rf"^{LEAD}([0-9.]{{1,3}})\s*(?:[)(]\s*\S|\.\s+\S)")
# Lines this far apart in reading order still belong to the same choice block:
# a choice set as a stacked fraction puts its numerator and denominator on
# lines of their own.
OPTION_GAP = 8
MIN_OPTIONS = 4
# An item line is a sentence, not a piece of one: fragments of the equations
# inside a choice block also start with a number and a bracket, so the line
# has to carry a word as well.
MIN_HEBREW = 2
# How much text may stand between a numbered line and the question after it
# for the space between them to be a picture of the alternatives.
DRAWN_CHOICE_LINES = 6

# The question word, including the reversed form some files extract it as.
QUESTION_WORD_RE = re.compile(r"ש\s*א\s*ל\s*ה|ה\s*ל\s*א\s*ש")
# Legacy Hebrew encodings in this collection drop final letters, so the word
# for a solution is as likely to come out "פתרו" as "פתרון".
SOLUTION_WORD_RE = re.compile(r"פתרונ?ו?ת?|פתרו")
SOLUTION_HEAD_RE = re.compile(rf"^{LEAD}(?:פתרון|פתרו|תשובה)\b")
# A heading opens with the question word, give or take the weight RTL
# extraction pushes in front of it ("(30%) שאלה1").
WORD_LEAD = 12
MAX_HEADING_CHARS = 80
MAX_QUESTIONS = 30
# Open papers that head their questions by number and weight alone —
# "1 ) [ ניתוח מעגלים% 30 :]".
PRICED_HEADING_RE = re.compile(r"^\s*(\d{1,2})\s*[.)]")
SCORE_RE = re.compile(r"נקודות|נק['׳]|%")
# Below this many items a choice-block reading is a paper's sub-sections
# rather than its questions.
MIN_CHOICE_ITEMS = 6


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
        return f"mechatronics-{self.year}-{self.season.lower()}-{self.moed.lower()}"


def _hebrew_year(stem: str) -> int | None:
    """Read תשע"ב as 2012: the Hebrew year less 3760, which is the Gregorian
    year both of that academic year's examination periods fall in."""
    match = HEBREW_YEAR_RE.search(stem)
    if not match:
        return None
    value = sum(HEBREW_LETTER_VALUES.get(ch, 0) for ch in match.group(0))
    year = 5000 + value - 3760
    return year if 1990 <= year <= 2100 else None


def _cover_text(path: Path) -> str:
    """The first page's text, or nothing when there is no file to read."""
    if not path.is_file():
        return ""
    doc = fitz.open(path)
    try:
        return doc[0].get_text() if len(doc) else ""
    finally:
        doc.close()


def _sitting(path: Path) -> Sitting:
    """Read the sitting from the file name, and from the cover page for the
    part of it the name leaves out."""
    stem = path.stem
    semester = SEMESTER_RE.search(stem)
    year: int | None = None
    season: str | None = None
    if semester:
        year, season = int(semester.group(1)) + 1, "Winter"
    else:
        found = YEAR_RE.search(stem)
        year = int(found.group(0)) if found else _hebrew_year(stem)
        match = SEASON_RE.search(stem)
        season = SEASONS[match.group(0).lower()] if match else None

    moed_match = (
        MOED_RE.search(stem) or MOED_RUN_ON_RE.search(stem) or MOED_BARE_RE.search(stem)
    )
    moed = MOEDS[moed_match.group(1).lower()] if moed_match else None

    if season is None or moed is None:
        cover = _cover_text(path)
        if season is None:
            match = SEASON_RE.search(cover)
            season = SEASONS[match.group(0).lower()] if match else None
        if moed is None:
            match = MOED_RE.search(cover)
            moed = MOEDS[match.group(1).lower()] if match else None
    return Sitting(year, season, moed)


def _slug(path: Path) -> str:
    """Directory name for a paper whose name gives no sitting."""
    slug = NON_ALNUM_RE.sub("-", path.stem.lower()).strip("-")
    return f"mechatronics-{slug or 'exam'}"


def _fallback_label(sitting: Sitting, path: Path, numbers: list[int]) -> str:
    """Label for a paper whose name does not give the whole sitting.

    Two sittings are one question of a paper rather than the paper, so the
    label says which, or the list would show a sitting that looks like the
    whole exam and holds one question.
    """
    head = " ".join(str(part) for part in (sitting.year, sitting.season) if part)
    if sitting.moed:
        head = f"{head} — Moed {sitting.moed}" if head else f"Moed {sitting.moed}"
    label = head or re.sub(r"[_-]+", " ", path.stem).strip()
    if numbers:
        held = ", ".join(str(n) for n in numbers)
        label += f" (question{'s' if len(numbers) > 1 else ''} {held} only)"
    return label


def _options_on(text: str) -> list[str]:
    """The alternative labels a line carries.

    A whole choice block is sometimes set as one row ("(א) (ב) (ג) (ד)"), so a
    line can carry several. What it must not carry is a label out of order: a
    choice that says "answers ב and ג are both right" ends in something shaped
    exactly like the next label.
    """
    labels = [a or b for a, b in OPTION_LABEL_RE.findall(text)]
    ordered: list[str] = []
    for label in labels:
        if not ordered or label > ordered[-1]:
            ordered.append(label)
    return ordered


def _choice_blocks(lines: list[Line]) -> list[tuple[int, int]]:
    """The (first line, last line) of each block of alternatives.

    Labels run א, ב, ג, … down the block, so a label that does not follow the
    one before it opens the next question's block — which is what separates
    two questions whose choices are only a line apart.
    """
    blocks: list[tuple[int, int]] = []
    start = last = None
    highest = ""
    for index, ln in enumerate(lines):
        labels = _options_on(ln.text)
        if not labels:
            continue
        for label in labels:
            if start is not None and (label <= highest or index - last > OPTION_GAP):
                blocks.append((start, last))
                start = highest = None
            if start is None:
                start, highest = index, label
            highest = max(highest, label)
        last = index
    if start is not None:
        blocks.append((start, last))
    return [
        (first, end)
        for first, end in blocks
        if len({label for ln in lines[first : end + 1] for label in _options_on(ln.text)})
        >= MIN_OPTIONS
    ]


def _item_headers(lines: list[Line]) -> list[Header]:
    """Multiple-choice items, read by the alternatives that close them.

    Half these papers are typeset in fonts whose character map is wrong, so an
    item's number is not to be trusted and often not to be read at all. What
    can be read is the shape: a block of four to six lettered alternatives,
    and above it the numbered line that asks the question. Numbers are taken
    from the text only when every one of them reads and they run 1, 2, 3, …
    """
    found: list[tuple[Line, str]] = []
    previous = -1
    for first, end in _choice_blocks(lines):
        window = [
            index
            for index in range(previous + 1, first)
            if ITEM_RE.match(lines[index].text)
        ]
        # A question stands on the page its alternatives do. Looking further
        # back would reach the cover page, whose numbered list of instructions
        # is the one thing in these papers shaped exactly like an item.
        same_page = [i for i in window if lines[i].page == lines[first].page]
        window = same_page or window
        worded = [
            index
            for index in window
            if len(HEBREW_RE.findall(lines[index].text)) >= MIN_HEBREW
        ]
        # The question is the first numbered line since the block before it —
        # what follows is the equations of the choices, which start the same
        # way. A question with no words in it at all ("13 ) :") is taken only
        # when nothing better stands between the two blocks.
        chosen = (worded or window)[:1]
        previous = end
        if not chosen:
            continue
        ln = lines[chosen[0]]
        found.append((ln, ITEM_RE.match(ln.text).group(1)))

    found += _drawn_choice_items(lines, [ln for ln, _ in found])
    found.sort(key=lambda pair: (pair[0].page, pair[0].y0))

    numbers = [int(token) for _, token in found if token.isdigit()]
    if len(numbers) != len(found) or numbers != list(range(1, len(found) + 1)):
        numbers = []
    return [
        Header(ln.page, ln.y0 - 2.0, numbers[i] if numbers else None)
        for i, (ln, _) in enumerate(found)
    ]


def _drawn_choice_items(
    lines: list[Line], headers: list[Line]
) -> list[tuple[Line, str]]:
    """Items whose alternatives are drawings rather than text.

    A few questions ask which graph or truth table fits, and set the five
    answers as pictures, so there is no block of labels to find them by. What
    is left of them is a numbered line with next to no text between it and the
    question after it — the page it asks for is a picture.
    """
    if len(headers) < 2:
        return []
    blocked = {
        index
        for first, end in _choice_blocks(lines)
        for index in range(first, end + 1)
    }
    positions = [(ln.page, ln.y0) for ln in headers]
    taken = set(positions)
    found: list[tuple[Line, str]] = []
    for index, ln in enumerate(lines):
        if index in blocked or (ln.page, ln.y0) in taken:
            continue
        if (ln.page, ln.y0) < positions[0]:
            continue
        match = ITEM_RE.match(ln.text)
        if not match or len(HEBREW_RE.findall(ln.text)) < MIN_HEBREW:
            continue
        after = [p for p in positions if p > (ln.page, ln.y0)]
        following = min(after) if after else (10**6, 0.0)
        between = sum(
            1 for other in lines[index + 1 :] if (other.page, other.y0) < following
        )
        if between <= DRAWN_CHOICE_LINES:
            found.append((ln, match.group(1)))
    return found


def _named(lines: list[Line]) -> list[tuple[Line, int | None, bool]]:
    """Lines that read as "שאלה N", with the number they name and whether they
    head the worked solution rather than the question itself."""
    found: list[tuple[Line, int | None, bool]] = []
    for ln in lines:
        if len(ln.text) > MAX_HEADING_CHARS:
            continue
        match = QUESTION_WORD_RE.search(ln.text)
        if not match or match.start() > WORD_LEAD:
            continue
        # Hebrew in front of the word makes it prose — "בשאלה זו", or the tail
        # of a sentence. Only digits and brackets get pushed across it.
        if HEBREW_RE.search(ln.text[: match.start()]):
            continue
        after = ln.text[match.end() :]
        digits = re.findall(r"\d{1,2}", after)
        number = int(digits[0]) if digits and int(digits[0]) <= MAX_QUESTIONS else None
        found.append((ln, number, bool(SOLUTION_WORD_RE.search(after))))
    return found


def _named_headers(lines: list[Line]) -> list[Header]:
    """Question headings of an open paper, in the order they are set.

    A heading that goes on to say "פתרון" heads the worked solution of the
    question it names, and is left to `_solution_headers`. Numbers are read
    from the headings only when they run 1, 2, 3, …: one paper is typeset in a
    font whose digits extract as other digits, and heads its three questions
    5, 0 and 2.
    """
    found = [(ln, number) for ln, number, solution in _named(lines) if not solution]
    numbers = [number for _, number in found if number is not None]
    if numbers != list(range(1, len(found) + 1)):
        numbers = []
    return [
        Header(ln.page, ln.y0 - 2.0, numbers[i] if numbers else None)
        for i, (ln, _) in enumerate(found)
    ]


def _solution_headers(lines: list[Line]) -> list[Header]:
    """Worked-solution headings: "שאלה2 – פתרון", or a bare "פתרון".

    A bare one has to stand alone on its line: the worked solutions
    themselves talk about the solution they are working out ("פתרון הומוגני
    דועך"), and a sentence is not a heading.
    """
    headers: list[Header] = []
    named = {id(ln) for ln, _, solution in _named(lines) if solution}
    for ln in lines:
        number: int | None = None
        if id(ln) in named:
            digits = re.findall(r"\d{1,2}", ln.text)
            number = int(digits[0]) if digits else None
        else:
            match = SOLUTION_HEAD_RE.match(ln.text)
            if not match:
                continue
            rest = ln.text[match.end() :]
            if len(HEBREW_RE.findall(rest)) > 2 or len(ln.text) > 30:
                continue
        headers.append(Header(ln.page, ln.y0 - 2.0, number))
    return headers


def _priced_headers(lines: list[Line]) -> list[Header]:
    """Open questions headed by number and weight alone — "1 ) [ מגברים% 30 ]"."""
    headers: list[Header] = []
    expected = 1
    for ln in lines:
        match = PRICED_HEADING_RE.match(ln.text)
        if not match or not SCORE_RE.search(ln.text):
            continue
        if int(match.group(1)) == expected:
            headers.append(Header(ln.page, ln.y0 - 2.0, expected))
            expected += 1
    return headers


def find_headers(lines: list[Line]) -> list[Header]:
    """A paper's question headings, whichever style it is written in."""
    items = _item_headers(lines)
    if len(items) >= MIN_CHOICE_ITEMS:
        return items
    named = _named_headers(lines)
    if named:
        return named
    priced = _priced_headers(lines)
    if priced:
        return priced
    return items


@dataclass
class PdfInfo:
    path: Path
    sitting: Sitting
    question_number: int | None
    headers: list[Header] = field(default_factory=list)
    solutions: list[Header] = field(default_factory=list)
    marked: bool = False
    multiple_choice: bool = False

    @property
    def answer_hint(self) -> bool:
        return bool(ANSWER_HINT_RE.search(self.path.stem))


def _is_marked(doc: fitz.Document) -> bool:
    """Whether the paper has its correct choices highlighted.

    Some copies mark them with a highlight annotation and some paint a yellow
    rectangle into the page itself; the export strips both from question mode,
    so both count.
    """
    for page in doc:
        for annot in page.annots() or []:
            if annot.type[1].lower() in {"highlight", "underline", "squiggly"}:
                return True
    for xref in range(1, doc.xref_length()):
        if not doc.xref_is_stream(xref):
            continue
        if doc.xref_get_key(xref, "Subtype")[1] == "/Image":
            continue
        if HIGHLIGHT_FILL_RE.search(doc.xref_stream(xref)):
            return True
    return False


def _inspect(path: Path) -> PdfInfo:
    doc = fitz.open(path)
    try:
        lines, _ = _extract_lines(doc)
        items = _item_headers(lines)
        multiple_choice = len(items) >= MIN_CHOICE_ITEMS
        found = FILE_QUESTION_RE.search(path.stem)
        return PdfInfo(
            path=path,
            sitting=_sitting(path),
            question_number=int(found.group(1)) if found else None,
            headers=find_headers(lines),
            solutions=[] if multiple_choice else _solution_headers(lines),
            marked=_is_marked(doc),
            multiple_choice=multiple_choice,
        )
    finally:
        doc.close()


def _clear_generated(exam_dir: Path) -> None:
    exam_dir.mkdir(parents=True, exist_ok=True)
    for pattern in ("question_*.pdf", "answer_*.pdf"):
        for path in exam_dir.glob(pattern):
            path.unlink()


def _header_finder(exam_dir: Path):
    """The header search, plus any heading recorded by hand for this exam."""
    drawn = DRAWN_HEADINGS.get(exam_dir.name, [])
    if not drawn:
        return find_headers

    def finder(lines: list[Line]) -> list[Header]:
        headers = find_headers(lines) + [Header(page, y, None) for page, y in drawn]
        return sorted(headers, key=lambda h: (h.page, h.y))

    return finder


def _split(info: PdfInfo, exam_dir: Path, part: str, **kwargs) -> dict:
    return auto_split_pdf(
        info.path,
        exam_dir,
        part=part,
        header_finder=_header_finder(exam_dir),
        # A multiple-choice question shares the setup drawn at the top of its
        # page with the items beside it, and ends with its own alternatives.
        # An open question shares nothing and runs to the next heading.
        include_shared_setup=info.multiple_choice,
        trim_after_options=info.multiple_choice and part == "questions",
        run_to_next_header=not info.multiple_choice,
        **kwargs,
    )


def _combined_segments(
    info: PdfInfo,
    exam_dir: Path,
    first: int,
) -> tuple[list[dict], list[dict]]:
    """Split a paper that works each question out where it stands.

    The question runs from its heading to its solution, and the solution from
    there to whatever comes next. Two papers are written this way, and one of
    them has its last two statements as scanned pages, whose headings are
    recorded in `DRAWN_HEADINGS` since there is nothing in them to read.
    """
    doc = fitz.open(info.path)
    cache = _ExportCache(doc)
    questions: list[dict] = []
    answers: list[dict] = []
    try:
        lines, bounds = _extract_lines(doc)
        last = len(doc) - 1
        drawn = [Header(page, y, None) for page, y in DRAWN_HEADINGS.get(exam_dir.name, [])]
        events = [(h.page, h.y, "question", h) for h in info.headers + drawn]
        events += [(h.page, h.y, "answer", h) for h in info.solutions]
        events.sort(key=lambda e: (e[0], e[1]))
        counts = {"question": first - 1, "answer": first - 1}
        for i, (page, y, part, header) in enumerate(events):
            end = (
                (events[i + 1][0], events[i + 1][1])
                if i + 1 < len(events)
                else (last, bounds[last].bottom)
            )
            counts[part] += 1
            number = header.parsed_number or counts[part]
            counts[part] = number
            slices = _span_slices((page, y), end, bounds)
            name = f"{part}_{number:02d}.pdf"
            if _write_segment_pdf(cache, slices, exam_dir / name):
                entry = {
                    "n": number,
                    "file": name,
                    "pages": sorted({s[0] + 1 for s in slices}),
                }
                (questions if part == "question" else answers).append(entry)
    finally:
        cache.close()
        doc.close()
    return questions, answers


def _whole_document(info: PdfInfo, exam_dir: Path, number: int) -> list[dict]:
    """A file that holds one question and no heading to find it by."""
    doc = fitz.open(info.path)
    clean = fitz.open(info.path)
    _strip_highlights(clean)
    cache = _ExportCache(clean)
    try:
        _, bounds = _extract_lines(doc)
        last = len(doc) - 1
        slices = _span_slices(
            (0, bounds[0].top), (last, bounds[last].bottom), bounds
        )
        name = f"question_{number:02d}.pdf"
        if not _write_segment_pdf(cache, slices, exam_dir / name):
            return []
        return [
            {"n": number, "file": name, "pages": sorted({s[0] + 1 for s in slices})}
        ]
    finally:
        cache.close()
        clean.close()
        doc.close()


def _renumber(entries: list[dict], exam_dir: Path, part: str, first: int) -> list[dict]:
    """Shift a one-question file's crop to the number its name gives it."""
    if first == 1:
        return entries
    shifted: list[dict] = []
    for entry in entries:
        number = int(entry["n"]) + first - 1
        name = f"{part}_{number:02d}.pdf"
        (exam_dir / entry["file"]).replace(exam_dir / name)
        shifted.append({**entry, "n": number, "file": name})
    return shifted


def _apply_overrides(exam_dir: Path, sources: dict[str, Path | None]) -> None:
    """Write the crops recorded by hand for this exam."""
    for part, numbered in SEGMENT_OVERRIDES.get(exam_dir.name, {}).items():
        source = sources.get(part)
        if source is None:
            raise ValueError(f"{exam_dir.name}: no {part} source to crop from")
        doc = fitz.open(source)
        cache = _ExportCache(doc)
        try:
            for number, slices in numbered.items():
                out_path = exam_dir / f"{part}_{number:02d}.pdf"
                if not _write_segment_pdf(cache, slices, out_path):
                    raise ValueError(
                        f"{exam_dir.name}: empty override for {part} {number}"
                    )
        finally:
            cache.close()
            doc.close()


def _apply_drops(exam_dir: Path) -> None:
    for part, numbers in SEGMENT_DROPS.get(exam_dir.name, {}).items():
        for number in numbers:
            (exam_dir / f"{part}_{number:02d}.pdf").unlink(missing_ok=True)


def _surviving(entries: list[dict], exam_dir: Path, part: str) -> list[dict]:
    dropped = SEGMENT_DROPS.get(exam_dir.name, {}).get(part, set())
    overridden = SEGMENT_OVERRIDES.get(exam_dir.name, {}).get(part, {})
    kept = {int(e["n"]): e for e in entries if int(e["n"]) not in dropped}
    for number in sorted(set(overridden) - set(kept) - dropped):
        kept[number] = {
            "n": number,
            "file": f"{part}_{number:02d}.pdf",
            "by_hand": True,
        }
    return [kept[n] for n in sorted(kept)]


def _split_group(infos: list[PdfInfo], out_root: Path) -> dict:
    """Split one sitting, which may be spread over several files."""
    infos.sort(key=lambda info: (info.question_number or 0, info.path.name))
    representative = next(
        (info for info in infos if not info.answer_hint), infos[0]
    )
    sitting = representative.sitting
    exam_dir = out_root / (sitting.slug if sitting else _slug(representative.path))
    _clear_generated(exam_dir)

    questions: list[dict] = []
    answers: list[dict] = []
    # Where a hand-recorded crop is cut from, unless a strategy below finds a
    # better source: a paper with nothing to read has only the one file.
    sources: dict[str, Path | None] = {
        "question": representative.path,
        "answer": representative.path,
    }
    strategies: list[str] = []
    notes: list[str] = []
    by_hand = SEGMENT_OVERRIDES.get(exam_dir.name, {})

    plain = [info for info in infos if not info.marked]
    marked = [info for info in infos if info.marked]

    per_question = any(info.question_number for info in infos)
    if not per_question and len(plain) == len(marked) == 1:
        # An exam and the same paper with its answers highlighted.
        question_info, answer_info = plain[0], marked[0]
        questions = _split(question_info, exam_dir, "questions").get("questions", [])
        answers = _split(answer_info, exam_dir, "answers").get("answers", [])
        sources = {"question": question_info.path, "answer": answer_info.path}
        strategies.append("exam + marked copy")
        if len(questions) != len(answers):
            notes.append(
                f"exam has {len(questions)} questions, marked copy {len(answers)}"
            )
    else:
        for info in infos:
            first = info.question_number or 1
            if not info.headers:
                if info.question_number:
                    questions += _whole_document(info, exam_dir, first)
                    strategies.append("whole file is one question")
                elif by_hand:
                    strategies.append("measured off the page")
                else:
                    notes.append(f"{info.path.name}: nothing to read")
                continue
            if info.solutions and not info.multiple_choice:
                found, worked = _combined_segments(info, exam_dir, first)
                questions += found
                answers += worked
                strategies.append("question and solution side by side")
                sources["question"] = sources["answer"] = info.path
                continue
            result = _split(info, exam_dir, "questions")
            questions += _renumber(
                result.get("questions", []), exam_dir, "question", first
            )
            sources["question"] = info.path
            if info.marked:
                result = _split(info, exam_dir, "answers")
                answers += _renumber(
                    result.get("answers", []), exam_dir, "answer", first
                )
                sources["answer"] = info.path
                strategies.append("marked copy")
            else:
                strategies.append("exam paper only")

    _apply_overrides(exam_dir, sources)
    _apply_drops(exam_dir)
    questions = _surviving(questions, exam_dir, "question")
    answers = _surviving(answers, exam_dir, "answer")

    held = sorted(info.question_number for info in infos if info.question_number)
    index = {
        "label": (
            sitting.label
            if sitting
            else _fallback_label(sitting, representative.path, held)
        ),
        "year": sitting.year,
        "season": sitting.season,
        "moed": sitting.moed,
        "course": COURSE,
        "strategy": ", ".join(dict.fromkeys(strategies)) or "unreadable",
        "source": {
            "questions": str(sources["question"]) if sources["question"] else None,
            "answers": str(sources["answer"]) if sources["answer"] else None,
            "files": [info.path.name for info in infos],
        },
        "questions": questions,
        "answers": answers,
        "question_count": len(questions),
        "answer_count": len(answers),
    }
    if notes:
        index["notes"] = notes
    (exam_dir / "index.json").write_text(
        json.dumps(index, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    return {"dir": exam_dir.name, **index}


def prepare(root: Path, out_root: Path) -> list[dict]:
    pdfs = sorted(root.rglob("*.pdf"))
    if not pdfs:
        raise SystemExit(f"no PDF files found under {root}")

    groups: dict[tuple, list[PdfInfo]] = {}
    for path in pdfs:
        info = _inspect(path)
        key = (
            (info.sitting.year, info.sitting.season, info.sitting.moed)
            if info.sitting
            else (info.path.stem,)
        )
        groups.setdefault(key, []).append(info)

    out_root.mkdir(parents=True, exist_ok=True)
    results = [_split_group(groups[key], out_root) for key in sorted(groups, key=str)]
    for result in results:
        print(
            f"{result['label']}: {result['question_count']} questions, "
            f"{result['answer_count']} answers ({result['strategy']})"
        )
        for note in result.get("notes", []):
            print(f"    {note}")

    (out_root / "mechatronics-report.json").write_text(
        json.dumps(results, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    empty = [r["label"] for r in results if not r["question_count"]]
    print(f"\n{len(results)} sittings, {len(empty)} with no readable questions")
    for label in empty:
        print(f"  unread: {label}")
    return results


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("root", help="folder containing the Mechatronics PDFs")
    parser.add_argument("--out", default="MechatronicsSplit")
    args = parser.parse_args()
    prepare(Path(args.root).resolve(), Path(args.out).resolve())


if __name__ == "__main__":
    main()
