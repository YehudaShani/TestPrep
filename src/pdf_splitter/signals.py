"""Prepare the supplied Signals & Systems (044131) exam collection.

Twenty years of papers, in four broad shapes:

* an exam PDF paired with a Hebrew solution PDF (2006, 2021-2024);
* an exam PDF paired with an English LaTeX solution whose questions are
  numbered by section heading alone (2016-2020);
* a single Word-era PDF that states each question and then works it out
  ("פתרון" between them), with no separate exam file (2007-2015); and
* a solution copy with no exam counterpart at all.

Unlike the circuits and Physics 3 collections, these are open questions, and
every sub-section carries a weight of its own ("א 4%"), so the generic
"N (5%)" header search reads sub-sections as questions. The finder here goes
by the word "שאלה" and by type size instead, falls back to LaTeX section
headings, and only then to the generic search — truncated to the questions
numbered 1..N, which is all a signals paper ever has.

    python -m pdf_splitter.signals "signals tests" --out SignalsSplit
"""

from __future__ import annotations

import argparse
import json
import re
from dataclasses import dataclass
from pathlib import Path

import fitz

from .auto_split import (
    HEBREW_RE,
    Header,
    Line,
    _extract_lines,
    _find_exam_headers,
    _sol_marker,
    auto_split_pdf,
    body_size,
)
from .splitter import _ExportCache, _write_segment_pdf

# Papers whose crops cannot be read off their headings: exam dir -> part ->
# question number -> (page, y0, y1) slices, in PDF points from the page top.
# A number listed here replaces whatever the header search produced; a number
# absent from the source is simply added. Page numbers are 0-based.
SEGMENT_OVERRIDES: dict[
    str, dict[str, dict[int, list[tuple[int, float, float]]]]
] = {
    # Questions and their worked solutions share one file and one heading
    # style, so the solution headings read as further questions. Split by hand
    # into three questions and the three solutions that answer them.
    "signals-2013-spring-a": {
        "question": {
            1: [(1, 70.2, 801.9), (2, 40.0, 70.2)],
            2: [(2, 70.2, 801.9), (3, 40.0, 801.9), (4, 40.0, 70.2)],
            3: [(4, 70.2, 801.9), (5, 40.0, 801.9), (6, 40.0, 70.2)],
        },
        "answer": {
            1: [(6, 70.2, 801.9), (7, 40.0, 558.9)],
            2: [
                (7, 558.9, 801.9),
                (8, 40.0, 801.9),
                (9, 40.0, 801.9),
                (10, 40.0, 801.9),
                (11, 40.0, 88.2),
            ],
            3: [
                (11, 88.2, 801.9),
                (12, 40.0, 801.9),
                (13, 40.0, 801.9),
                (14, 40.0, 801.9),
                (15, 40.0, 801.9),
            ],
        },
    },
    # Same shape. The headings number themselves 3, 2, 1 down the page — RTL
    # extraction scrambled them — so the questions are taken in page order and
    # matched to the solutions, which run 1, 2, 3.
    "signals-2014-winter-a": {
        "question": {
            1: [(1, 96.4, 522.7)],
            2: [(1, 522.7, 801.9), (2, 40.0, 801.9), (3, 40.0, 159.0)],
            3: [(3, 159.0, 801.9), (4, 40.0, 801.9), (5, 40.0, 70.1)],
        },
        "answer": {
            1: [(5, 124.8, 416.1)],
            2: [(5, 416.1, 801.9), (6, 40.0, 801.9), (7, 40.0, 347.1)],
            3: [(7, 347.1, 801.9), (8, 40.0, 801.9)],
        },
    },
    # Two of the three question headings extract fully reversed ("2 שאלה"),
    # so only the third was found.
    "signals-2014-winter-b": {
        "question": {
            1: [(1, 70.2, 752.0), (2, 40.0, 96.1)],
            2: [(2, 96.1, 752.0), (3, 40.0, 752.0), (4, 40.0, 86.3)],
            3: [(4, 86.3, 752.0), (5, 40.0, 752.0), (6, 40.0, 95.8)],
        },
        "answer": {
            1: [(6, 121.6, 488.5)],
            2: [(6, 488.5, 752.0), (7, 40.0, 752.0), (8, 40.0, 420.0)],
            3: [(8, 420.0, 752.0), (9, 40.0, 752.0), (10, 40.0, 752.0)],
        },
    },
    "signals-2015-winter-b": {
        "question": {
            1: [(1, 70.1, 752.0), (2, 40.0, 70.5)],
            2: [(2, 70.5, 752.0), (3, 40.0, 91.2)],
            3: [(3, 91.2, 752.0), (4, 40.0, 752.0), (5, 40.0, 70.1)],
        },
        "answer": {
            1: [(5, 70.1, 752.0), (6, 40.0, 752.0), (7, 40.0, 752.0), (8, 40.0, 70.5)],
            2: [(8, 70.5, 592.9)],
            3: [(8, 592.9, 752.0)],
        },
    },
    # The paper opens with four multiple-choice questions under one "חלק I"
    # title; only the second onwards carry a "שאלה N" heading, so everything
    # shifts up by one without the first being written out here.
    "signals-2008-winter-a": {
        part: {
            1: [(1, 69.5, 802.0), (2, 40.0, 802.0), (3, 40.0, 71.6)],
            2: [(3, 71.6, 802.0), (4, 40.0, 802.0), (5, 40.0, 71.6)],
            3: [(5, 71.6, 802.0), (6, 40.0, 802.0), (7, 40.0, 71.6)],
            4: [(7, 71.6, 802.0), (8, 40.0, 802.0), (9, 40.0, 124.4)],
            5: [
                (9, 124.4, 802.0),
                (10, 40.0, 802.0),
                (11, 40.0, 802.0),
                (12, 40.0, 802.0),
                (13, 40.0, 802.0),
                (14, 40.0, 71.6),
            ],
            6: [(14, 71.6, 802.0), (15, 40.0, 802.0), (16, 40.0, 802.0), (17, 40.0, 802.0)],
        }
        for part in ("question", "answer")
    },
    # The closing bonus question is headed "שאלת בונוס", not "שאלה N".
    "signals-2020-spring-b": {
        "question": {
            13: [(9, 68.3, 778.2), (10, 40.0, 70.5)],
            14: [(10, 70.5, 778.2)],
        },
        "answer": {
            13: [(13, 231.1, 778.9), (14, 40.0, 279.5)],
            14: [(14, 279.5, 778.9)],
        },
    },
    # A closing "שאלות רבות ברירה" part, whose items are lettered rather than
    # numbered, was running on into question 3. It is kept as one crop, which
    # is how the official solution treats it too.
    "signals-2017-winter-a": {
        "question": {
            3: [(3, 53.4, 752.0), (4, 40.0, 752.0), (5, 40.0, 752.0), (6, 40.0, 53.4)],
            4: [(6, 53.4, 752.0), (7, 40.0, 752.0)],
        }
    },
    "signals-2017-winter-b": {
        "question": {
            3: [(2, 53.4, 752.0), (3, 40.0, 53.4)],
            4: [
                (3, 53.4, 752.0),
                (4, 40.0, 752.0),
                (5, 40.0, 752.0),
                (6, 40.0, 752.0),
                (7, 40.0, 752.0),
                (8, 40.0, 752.0),
                (9, 40.0, 752.0),
            ],
        }
    },
}
# Questions to drop after splitting — a heading that turned out to head a
# worked solution rather than a question: exam dir -> part -> numbers.
SEGMENT_DROPS: dict[str, dict[str, set[int]]] = {
    "signals-2014-winter-a": {"question": {4, 5, 6}, "answer": {4, 5, 6}},
    "signals-2014-winter-b": {"question": {4}, "answer": {4}},
    "signals-2015-winter-b": {"question": {4}, "answer": {4}},
}

# "Sol", "Sol2", "sol v2", "solution", and the Hebrew "פתרון" prefix.
ANSWER_HINT_RE = re.compile(r"(?:^|[\s_-])(?:sol\w*|solutions?)(?:$|[\s_.-])|פתרון", re.I)

YEAR_RE = re.compile(r"20\d\d")
SEASON_RE = re.compile(r"winter|spring|summer|חורף|אביב|קיץ", re.I)
SEASONS = {
    "winter": "Winter",
    "spring": "Spring",
    "summer": "Summer",
    "חורף": "Winter",
    "אביב": "Spring",
    "קיץ": "Summer",
}
# "Term A", "Final B", "מועד א". The letter must not run into another letter
# or digit, so "Term_B_" and "Term A Sol2" both read as one sitting letter.
MOED_RE = re.compile(
    r"(?:term|final|moed|מועד)[\s_]*([abcאבג])(?![a-zא-ת\d])", re.I
)
MOEDS = {"a": "A", "b": "B", "c": "C", "א": "A", "ב": "B", "ג": "C"}
NON_ALNUM_RE = re.compile(r"[^a-z0-9]+")

# A signals paper carries at most three open questions and a multiple-choice
# part of ten or so; anything past that is a sub-section number the generic
# header search mistook for a question.
MAX_QUESTIONS = 16
SCORE_RE = re.compile(r"נקודות|נק['׳]|%")
# A number that is a score, not a question number — RTL extraction puts the
# percent sign on either side of it.
SCORED_NUMBER_RE = re.compile(r"\d{1,3}\s*(?:%|נקודות|נק['׳])|%\s*\d{1,3}")
# The question word, including the reversed form some files extract it as.
QUESTION_WORD_RE = re.compile(r"ש\s*א\s*ל\s*ה|ה\s*ל\s*א\s*ש")
SOLUTION_WORD_RE = re.compile(r"פתרון|ןורתפ")
# "שאלה זו..." — "this question ..." — opens a sentence about the question,
# never the question itself.
THIS_QUESTION_RE = re.compile(r"^\S+\s*(?:זו|זאת)\b")
# How far into a line the question word may sit and still head it. RTL
# extraction can swing the word to either end of its own heading, so a short
# line that ends with it counts too.
WORD_LEAD = 6
WORD_LEAD_SCORED = 20
SHORT_LINE = 30
MAX_HEADING_CHARS = 90
# Headings of one paper are set within a couple of points of each other; the
# multiple-choice part and the cover page sit further down than this.
SIZE_TOLERANCE = 3.0
# Multiple-choice parts run to ten questions; a couple of stray matches must
# not pass for one.
MIN_CHOICE_RUN = 3
# LaTeX solutions head each question with its bare number, sometimes with a
# "Q<n>" beside it, set a size or more above the body text.
SECTION_RE = re.compile(r"^(\d{1,2})(?:\s+Q\1)?$|^Q(\d{1,2})$")
# A few papers leave the word out and head each question with its number and
# weight alone — "1 ( 28% )".
PRICED_HEADING_RE = re.compile(
    r"^\s*(\d{1,2})\s*[.)]?\s*[(\[]\s*\d{1,3}\s*(?:%|נקודות|נק['׳])"
)
# How far above the body text a line has to be set to read as a heading.
SIZE_MARGIN = 1.0


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
        return f"signals-{self.year}-{self.season.lower()}-{self.moed.lower()}"


@dataclass
class PdfInfo:
    path: Path
    sitting: Sitting
    header_count: int
    solution_marker_count: int

    @property
    def answer_hint(self) -> bool:
        return bool(ANSWER_HINT_RE.search(self.path.stem))


def _sitting(path: Path) -> Sitting:
    """Read "2016_Spring_Term_A_Sol" or "פתרון מבחן חורף 2024 מועד א"."""
    stem = path.stem
    year = YEAR_RE.search(stem)
    season = SEASON_RE.search(stem)
    moed = MOED_RE.search(stem)
    return Sitting(
        int(year.group(0)) if year else None,
        SEASONS[season.group(0).lower()] if season else None,
        MOEDS[moed.group(1).lower()] if moed else None,
    )


def _slug(path: Path) -> str:
    """Directory name for a paper whose name gives no sitting."""
    slug = NON_ALNUM_RE.sub("-", path.stem.lower()).strip("-")
    return f"signals-{slug or 'exam'}"


def _fallback_label(path: Path) -> str:
    label = ANSWER_HINT_RE.sub(" ", path.stem)
    label = re.sub(r"[_-]+", " ", label)
    label = re.sub(r"\s+", " ", label).strip(" +-.")
    return label or path.stem


def _numbers(text: str) -> set[int]:
    """Question numbers the line could be naming, ignoring its score."""
    return {
        int(token)
        for token in re.findall(r"\d{1,2}", SCORED_NUMBER_RE.sub(" ", text))
        if 1 <= int(token) <= MAX_QUESTIONS
    }


def _named_candidates(lines: list[Line]) -> list[Line]:
    """Lines that read as "question N", not as prose mentioning one."""
    body = body_size(lines)
    found: list[Line] = []
    for ln in lines:
        if len(ln.text) > MAX_HEADING_CHARS or _sol_marker(ln.text)[0]:
            continue
        match = QUESTION_WORD_RE.search(ln.text)
        if not match:
            continue
        if THIS_QUESTION_RE.match(ln.text[match.start() :]):
            continue
        # "שאלה1 – פתרון" heads the worked solution, not the question. Only a
        # short line is read that way: a heading may well go on to say that
        # its sections can be solved in any order.
        if len(ln.text) <= SHORT_LINE and SOLUTION_WORD_RE.search(ln.text):
            continue
        # A heading opens with the word, give or take the score RTL extraction
        # pushes in front of it ("(25%) 1שאלה"). Only digits and punctuation
        # ever get pushed across, so any Hebrew in front means prose —
        # "בשאלה זו" ("in this question"), or the tail of a section title.
        before = ln.text[: match.start()]
        after = ln.text[match.end() :].lstrip()
        lead = WORD_LEAD_SCORED if SCORE_RE.search(ln.text) else WORD_LEAD
        opens = match.start() <= lead and (
            not HEBREW_RE.search(before)
            # A heading may name its topic before the word once extraction has
            # shuffled them ("־ זמן רציף1( שאלה33%)"); what marks it out from
            # prose is the number still hanging off the word.
            or after[:1].isdigit()
        )
        # Extraction can instead swing the word to the far end of its own
        # heading ("־ זמן רציף1שאלה"). That only counts when the line is set
        # above body size and the question number stands right against the
        # word, which is what the word was swung past; a section title ending
        # in it ("...במשקל 4% כל שאלה") has a Hebrew word there instead.
        trails = (
            len(ln.text) <= SHORT_LINE
            and match.end() >= len(ln.text) - 1
            and ln.size >= body + SIZE_MARGIN
            and before.endswith(tuple("0123456789"))
        )
        if not opens and not trails:
            continue
        found.append(ln)
    return found


def _named_headers(lines: list[Line]) -> list[Header]:
    """Question headings, told from their look-alikes by type size.

    Every paper sets its question headings larger than anything else that
    says "שאלה" — the cover page's list of weights, a "continued" note, a
    reference to another question. Papers that close with a multiple-choice
    part head those questions one size down, so a second run of headings is
    taken too when it starts after the last full-size one and numbers itself
    1, 2, 3, …
    """
    candidates = _named_candidates(lines)
    if not candidates:
        return []
    # The cover page counts up what each question is worth and which sections
    # are a safety net, in body type and in full sentences. Whatever stands
    # before the paper's first real heading is that, not a heading.
    body = body_size(lines)
    opening = [ln for ln in candidates if ln.size >= body + SIZE_MARGIN]
    if opening:
        start = (opening[0].page, opening[0].y0)
        candidates = [ln for ln in candidates if (ln.page, ln.y0) >= start]

    largest = max(ln.size for ln in candidates)
    primary = [ln for ln in candidates if ln.size >= largest - SIZE_TOLERANCE]

    end = (primary[-1].page, primary[-1].y0)
    chain: list[Line] = []
    for ln in candidates:
        if ln.size >= largest - SIZE_TOLERANCE or (ln.page, ln.y0) <= end:
            continue
        if len(chain) + 1 in _numbers(ln.text):
            chain.append(ln)
    if len(chain) < MIN_CHOICE_RUN:
        chain = []

    return [Header(ln.page, ln.y0 - 2.0, None) for ln in primary + chain]


def _priced_headers(lines: list[Line]) -> list[Header]:
    """Headings that give a number and a weight but no word — "1 ( 28% )".

    Sub-sections are priced the same way, so a heading is told from them by
    being set above body size; the numbers still have to run 1, 2, 3, …
    """
    body = body_size(lines)
    found: list[Header] = []
    expected = 1
    for ln in lines:
        match = PRICED_HEADING_RE.match(ln.text)
        if not match or ln.size < body + SIZE_MARGIN:
            continue
        if int(match.group(1)) == expected:
            found.append(Header(ln.page, ln.y0 - 2.0, expected))
            expected += 1
    return found


def _section_headers(lines: list[Line]) -> list[Header]:
    """LaTeX solution sections: a bare number set larger than the body text."""
    body = body_size(lines)
    found: list[Header] = []
    expected = 1
    for ln in lines:
        match = SECTION_RE.match(ln.text)
        if not match or ln.size < body + SIZE_MARGIN:
            continue
        if int(match.group(1) or match.group(2)) == expected:
            found.append(Header(ln.page, ln.y0 - 2.0, expected))
            expected += 1
    return found


def _leading_run(headers: list[Header]) -> list[Header]:
    """Keep the headers numbered 1, 2, 3, … and drop the tail.

    The generic search reads a signals paper's sub-sections as questions once
    it runs past the last real heading, giving chains like 1, 2, 3, 4, 7, 26.
    """
    kept: list[Header] = []
    for header in headers:
        if header.parsed_number != len(kept) + 1:
            break
        kept.append(header)
    return kept


def find_headers(lines: list[Line]) -> list[Header]:
    candidates = (
        _named_headers(lines),
        _priced_headers(lines),
        _section_headers(lines),
        _leading_run(_find_exam_headers(lines)),
    )
    for headers in candidates:
        if len(headers) >= 2:
            return headers[:MAX_QUESTIONS]
    return max(candidates, key=len)[:MAX_QUESTIONS]


def _inspect(path: Path) -> PdfInfo:
    doc = fitz.open(path)
    try:
        lines, _ = _extract_lines(doc)
        headers = find_headers(lines)
        markers = sum(1 for ln in lines if _sol_marker(ln.text)[0])
    finally:
        doc.close()
    return PdfInfo(path, _sitting(path), len(headers), markers)


def _clear_generated(exam_dir: Path) -> None:
    exam_dir.mkdir(parents=True, exist_ok=True)
    for pattern in ("question_*.pdf", "answer_*.pdf"):
        for path in exam_dir.glob(pattern):
            path.unlink()


def _split(source: Path, exam_dir: Path, part: str, **kwargs) -> dict:
    return auto_split_pdf(
        source,
        exam_dir,
        part=part,
        include_shared_setup=False,
        header_finder=find_headers,
        # Papers that work each question out under its own "פתרון" must not
        # hand the worked solution over in question mode. Nothing is shared
        # between signals questions, so one that carries on over a page break
        # ("המשך שאלה 2....") runs to the next heading.
        stop_at_solution=(part == "questions"),
        run_to_next_header=(part == "questions"),
        **kwargs,
    )


def _apply_overrides(
    exam_dir: Path, question_source: Path | None, answer_source: Path | None
) -> None:
    """Write the crops recorded by hand for this exam, replacing any the
    header search produced for the same numbers."""
    overrides = SEGMENT_OVERRIDES.get(exam_dir.name)
    if not overrides:
        return
    sources = {"question": question_source, "answer": answer_source}
    for part, numbered in overrides.items():
        source = sources[part]
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
    """Delete crops whose heading turned out not to head a question."""
    for part, numbers in SEGMENT_DROPS.get(exam_dir.name, {}).items():
        for number in numbers:
            (exam_dir / f"{part}_{number:02d}.pdf").unlink(missing_ok=True)


def _surviving(entries: list[dict], exam_dir: Path, part: str) -> list[dict]:
    dropped = SEGMENT_DROPS.get(exam_dir.name, {}).get(part, set())
    overridden = SEGMENT_OVERRIDES.get(exam_dir.name, {}).get(part, {})
    kept = [e for e in entries if int(e["n"]) not in dropped]
    known = {int(e["n"]) for e in kept}
    for number in sorted(set(overridden) - known - dropped):
        kept.append({"n": number, "file": f"{part}_{number:02d}.pdf", "by_hand": True})
    return sorted(kept, key=lambda e: int(e["n"]))


def _entries_by_number(entries: list[dict]) -> list[dict]:
    by_number = {int(entry["n"]): entry for entry in entries}
    return [by_number[n] for n in sorted(by_number)]


def _split_group(infos: list[PdfInfo], out_root: Path) -> dict:
    plain = [info for info in infos if not info.answer_hint]
    solved = [info for info in infos if info.answer_hint]
    representative = (plain or solved)[0]
    sitting = representative.sitting
    exam_dir = out_root / (sitting.slug if sitting else _slug(representative.path))
    _clear_generated(exam_dir)

    question_result: dict = {"questions": []}
    answer_result: dict = {"answers": []}
    question_source: Path | None = None
    answer_source: Path | None = None

    if plain and solved:
        # Several sittings kept two solution files ("Sol" and "Sol2"); the one
        # whose questions can be told apart is the one worth splitting.
        question_source = max(plain, key=lambda info: info.header_count).path
        answer_source = max(solved, key=lambda info: info.header_count).path
        question_result = _split(question_source, exam_dir, "questions")
        answer_result = _split(answer_source, exam_dir, "answers")
        strategy = "paired exam + solution"
    else:
        info = max(infos, key=lambda candidate: candidate.header_count)
        question_source = answer_source = info.path
        if info.solution_marker_count >= 2:
            # Question, then its worked solution, then the next question.
            question_result = _split(info.path, exam_dir, "questions")
            answer_result = _split(
                info.path, exam_dir, "answers", drop_unanswered=True
            )
            strategy = "combined question/solution"
        else:
            # Nothing separates statement from solution — the same crop has to
            # serve both modes.
            question_result = _split(info.path, exam_dir, "questions")
            answer_result = _split(info.path, exam_dir, "answers")
            strategy = "solution copy only"

    _apply_overrides(exam_dir, question_source, answer_source)
    _apply_drops(exam_dir)

    questions = _surviving(
        _entries_by_number(question_result.get("questions", [])), exam_dir, "question"
    )
    answers = _surviving(
        _entries_by_number(answer_result.get("answers", [])), exam_dir, "answer"
    )
    index = {
        "label": sitting.label if sitting else _fallback_label(representative.path),
        "year": sitting.year,
        "season": sitting.season,
        "moed": sitting.moed,
        "course": "Signals & Systems",
        "strategy": strategy,
        "source": {
            "questions": str(question_source) if question_source else None,
            "answers": str(answer_source) if answer_source else None,
        },
        "questions": questions,
        "answers": answers,
        "question_count": len(questions),
        "answer_count": len(answers),
    }
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
        # Papers whose name gives no sitting can only stand on their own.
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

    (out_root / "signals-report.json").write_text(
        json.dumps(results, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    empty = [r["label"] for r in results if not r["question_count"]]
    print(f"\n{len(results)} sittings, {len(empty)} with no readable questions")
    for label in empty:
        print(f"  unread: {label}")
    return results


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("root", help="folder containing the Signals & Systems PDFs")
    parser.add_argument("--out", default="SignalsSplit")
    args = parser.parse_args()
    prepare(Path(args.root).resolve(), Path(args.out).resolve())


if __name__ == "__main__":
    main()
