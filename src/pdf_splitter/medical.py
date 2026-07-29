"""Prepare the supplied Medical Imaging (046831) exam collection.

Ten spring sittings, 2014-2025, in a shape neither of the other collections
has: the paper is divided into lettered parts. Part א is multiple choice and
numbers its questions 1..N; part ב is open questions and starts counting at 1
all over again; two years add a part ג of guest-lecture questions that carries
on from part א's numbering. Nothing in a number alone says which part it
belongs to, so the split reads the parts first and then numbers the questions
straight through the paper.

The papers come as an exam paired with a solution copy (2017-2023), as a
single file that works each question out under a "פתרון" of its own
(2024-2025), or as an exam whose only answers are a key page at the end
(2014-2016), which yields questions and no answers.

    python -m pdf_splitter.medical "Medical Imaging Tests" --out MedicalSplit
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
    LATIN_RE,
    Line,
    PageBounds,
    _extract_lines,
    _span_slices,
    body_size,
)
from .splitter import _ExportCache, _write_segment_pdf

COURSE = "Medical Imaging"
# Every sitting of this course is held in the spring semester; Moed B falls
# after the summer, but it closes the same semester.
SEASON = "Spring"

# "Sol", "sol v2", "Solution", and the Hebrew "פתרון"/"פיתרון"/"תשובות".
ANSWER_HINT_RE = re.compile(
    r"(?:^|[\s_-])(?:sol\w*|solutions?)(?:$|[\s_.-])|פ[יא]?תרון|תשובות", re.I
)
YEAR_RE = re.compile(r"20\d\d")
# "Moed A", "מועד ב". The letter must not run into another one, so
# "Moed B closed questions" still reads as one sitting letter.
MOED_RE = re.compile(r"(?:moed|מועד)[\s_]*([abאב])(?![a-zא-ת\d])", re.I)
MOEDS = {"a": "A", "b": "B", "א": "A", "ב": "B"}

# "חלק 'א- שאלות רב-ברירה". RTL extraction swings the apostrophe of "חלק א'"
# to either side of the letter, and the letter itself is all that matters.
PART_TITLE_RE = re.compile(r"חלק\s*['׳\"]?\s*[אבג]")
# The cover page plans the sitting's time by part ("חלק 'א– 25 דקות"); only a
# line that goes on to say what the part asks is the part's own title.
PART_TITLE_WORD = "שאלות"
# Open questions are headed "1 . שאלת MRI פתוחה (20 נק')", always well above
# body size — which is what tells the heading from the cover page's count of
# how many open questions the paper has.
OPEN_WORD = "פתוחה"
OPEN_SIZE_MARGIN = 3.0
PART_SIZE_MARGIN = 2.0
# A worked solution opens with the word, give or take the colon RTL extraction
# pushes in front of it — or, in the years that print no such heading, with the
# sentence naming the right choice ("התשובה הינה ג׳", "'תשובה א –").
SOLUTION_RE = re.compile(r"^[\s:•\-–'\"׳]*(?:פ[יא]?תרון|ה?תשובה[\s'\"׳])")
SOLUTION_WORD_RE = re.compile(r"פ[יא]?תרון")
# The closing page of multiple-choice answers belongs to no single question.
ANSWER_KEY_RE = re.compile(r"תשובות\s*לשאלות")
NUMBERED_RE = re.compile(r"^\s*(\d{1,2})\s*\.\s*(.*)$")
CONTINUED_NUMBER_RE = re.compile(r"\d(?!\s)")
# Last resort. One paper (2014 Moed B) was typeset with a font that renders
# every full stop as a "9", so its headings extract as "29 ( הקולימטורים" and
# "20 9 נתונה התמונה" — a number, then whatever the stop became, then the
# question. Which character that is can only be read off the paper itself, by
# taking the one that opens the most lines that way; the bracket its choices
# are listed with ("1) להגביר") is the one thing it is never mistaken for.
STOP_RE = re.compile(r"^\s*\d{1,2}?\s*([^\s)\]])\s+[(\[]?\s*[א-תA-Za-z]")

MAX_QUESTIONS = 30
# Shorter than this, a run of consecutively numbered lines is as likely to be
# a question's own sub-sections as it is to be the paper's questions.
MIN_RUN = 3
# A file that yields fewer headings than this is not a paper: the collection
# also holds a one-page answer key and a note about which past-exam topics are
# no longer taught.
MIN_HEADINGS = 3
NON_ALNUM_RE = re.compile(r"[^a-z0-9]+")

Position = tuple[int, float]


@dataclass(frozen=True)
class Sitting:
    year: int | None
    moed: str | None

    def __bool__(self) -> bool:
        return bool(self.year and self.moed)

    @property
    def label(self) -> str:
        return f"{self.year} {SEASON} — Moed {self.moed}"

    @property
    def slug(self) -> str:
        return f"medical-{self.year}-{self.moed.lower()}"


@dataclass
class Paper:
    path: Path
    sitting: Sitting
    headings: list[Position]
    # Part titles and the answer-key page: a question ends at one of these
    # even when the next heading is further down.
    stops: list[Position]
    solutions: list[Position]
    bounds: dict[int, PageBounds]
    used_fallback: bool = False

    @property
    def answer_hint(self) -> bool:
        return bool(ANSWER_HINT_RE.search(self.path.stem))


def _sitting(path: Path) -> Sitting:
    """Read "046831 Spring2021 Moed A sol" or "מבחן מועד א 2024 עם פתרון".

    A paper that names no sitting letter is the only one of its year, and the
    course has never had more than the two, so it is Moed A.
    """
    year = YEAR_RE.search(path.stem)
    moed = MOED_RE.search(path.stem)
    return Sitting(
        int(year.group(0)) if year else None,
        MOEDS[moed.group(1).lower()] if moed else ("A" if year else None),
    )


def _parts(lines: list[Line], body: float) -> tuple[list[list[Line]], list[Position]]:
    """The paper's lettered parts, and where each one's title sits."""
    titles = [
        ln
        for ln in lines
        if ln.size >= body + PART_SIZE_MARGIN
        and PART_TITLE_WORD in ln.text
        and PART_TITLE_RE.search(ln.text)
    ]
    if not titles:
        return [lines], []
    groups: list[list[Line]] = []
    for i, title in enumerate(titles):
        start = (title.page, title.y1)
        end = (
            (titles[i + 1].page, titles[i + 1].y0)
            if i + 1 < len(titles)
            else (1 << 30, 0.0)
        )
        groups.append([ln for ln in lines if start <= (ln.page, ln.y0) < end])
    return groups, [(t.page, t.y0 - 2.0) for t in titles]


def _open_headings(part: list[Line], body: float) -> list[Line]:
    """Open-question headings, told from the solutions that repeat them."""
    return [
        ln
        for ln in part
        if ln.size >= body + OPEN_SIZE_MARGIN
        and OPEN_WORD in ln.text
        and not SOLUTION_WORD_RE.search(ln.text)
    ]


def _numbered(part: list[Line]) -> list[tuple[int, Line]]:
    """Lines opening with a number and a full stop, less the look-alikes.

    The date at the head of every paper ("1.7.2016") opens exactly that way,
    as do decimals in the body text ("0.5) = σ"), so what follows the number
    has to read as words rather than as more of a number: a digit running
    straight into what comes after it is the rest of the same number, while
    one standing on its own is the paper talking about a quantity.
    """
    found: list[tuple[int, Line]] = []
    for ln in part:
        match = NUMBERED_RE.match(ln.text)
        if not match:
            continue
        number = int(match.group(1))
        rest = match.group(2)
        if number > MAX_QUESTIONS or CONTINUED_NUMBER_RE.match(rest):
            continue
        if rest and not (HEBREW_RE.search(rest) or LATIN_RE.search(rest)):
            continue
        found.append((number, ln))
    return found


def _one_stop_headings(part: list[Line]) -> list[Line]:
    """Lines opening the way most of the part's numbered lines do."""
    by_stop: dict[str, list[Line]] = {}
    for ln in part:
        match = STOP_RE.match(ln.text)
        if match:
            by_stop.setdefault(match.group(1), []).append(ln)
    return max(by_stop.values(), key=len) if by_stop else []


def _consecutive_run(numbered: list[tuple[int, Line]]) -> list[Line]:
    """The longest run of lines numbered one after another, in page order."""
    best: list[tuple[int, Line]] = []
    for start in range(len(numbered)):
        run = [numbered[start]]
        for number, ln in numbered[start + 1 :]:
            if number == run[-1][0] + 1:
                run.append((number, ln))
        if len(run) > len(best):
            best = run
    return [ln for _, ln in best]


def _part_headings(part: list[Line], body: float) -> tuple[list[Line], bool]:
    """One part's question headings, and whether the numbers could be read.

    Three papers (2014 A and B, 2017) were typeset with a font whose digits
    extract as other characters — the 2017 date reads "62...602." — so no run
    of numbers can be found in them at all. Those fall back to taking every
    numbered line in page order, which finds the questions but cannot tell a
    missed heading from a spurious one.
    """
    opens = _open_headings(part, body)
    if len(opens) >= 2:
        return opens, False
    numbered = _numbered(part)
    run = _consecutive_run(numbered)
    if len(run) >= MIN_RUN and len(run) * 2 >= len(numbered):
        return run, False
    if opens:
        return opens, False
    if len(numbered) >= 2:
        # A part of two or three questions is too short for a run to prove
        # anything — part ג of the 2018 paper carries on from part א's
        # numbering — so only a part that should have shown one is reported.
        return [ln for _, ln in numbered], len(numbered) > MIN_RUN
    by_stop = _one_stop_headings(part)
    if len(by_stop) >= MIN_HEADINGS:
        # Whichever reading found a heading is worth keeping: the stop the
        # font mangled is not always mangled the same way twice.
        found = {(ln.page, ln.y0): ln for ln in by_stop}
        found.update({(ln.page, ln.y0): ln for _, ln in numbered})
        return [found[key] for key in sorted(found)], True
    return [ln for _, ln in numbered], bool(numbered)


def _headings(lines: list[Line]) -> tuple[list[Position], list[Position], bool]:
    """Question tops, part boundaries, and whether any part fell back."""
    body = body_size(lines)
    parts, titles = _parts(lines, body)
    tops: list[Position] = []
    fallback = False
    for part in parts:
        found, guessed = _part_headings(part, body)
        fallback = fallback or guessed
        tops.extend((ln.page, ln.y0 - 2.0) for ln in found)
    return sorted(tops), titles, fallback


def _read(path: Path) -> Paper:
    doc = fitz.open(path)
    try:
        lines, bounds = _extract_lines(doc)
        tops, titles, fallback = _headings(lines)
        last = len(doc) - 1
        stops = titles + [(last, bounds[last].bottom)]
        key = [
            (ln.page, max(bounds[ln.page].top, ln.y0 - 4.0))
            for ln in lines
            if ANSWER_KEY_RE.search(ln.text)
        ]
        if key:
            stops.append(min(key))
        solutions = [
            (ln.page, ln.y0 - 2.0) for ln in lines if SOLUTION_RE.match(ln.text)
        ]
    finally:
        doc.close()
    return Paper(path, _sitting(path), tops, sorted(stops), solutions, bounds, fallback)


def _question_spans(paper: Paper) -> list[tuple[Position, Position]]:
    """Where each question runs from and to, before any solution is cut off."""
    spans: list[tuple[Position, Position]] = []
    for i, top in enumerate(paper.headings):
        ends = [s for s in paper.stops if s > top]
        if i + 1 < len(paper.headings):
            ends.append(paper.headings[i + 1])
        spans.append((top, min(ends)))
    return spans


def _split_at_solutions(
    paper: Paper,
) -> tuple[list[tuple[int, Position, Position]], list[tuple[int, Position, Position]]]:
    """Cut each question at its own worked solution, and keep both halves.

    Papers that answer their questions in the same file need this, and so do a
    couple of exam copies that were paired with a solution but had the open
    questions' solutions left in them.
    """
    questions: list[tuple[int, Position, Position]] = []
    answers: list[tuple[int, Position, Position]] = []
    for number, (top, end) in enumerate(_question_spans(paper), start=1):
        inside = [s for s in paper.solutions if top < s < end]
        if inside:
            questions.append((number, top, min(inside)))
            answers.append((number, min(inside), end))
        else:
            questions.append((number, top, end))
    return questions, answers


def _export(
    source: Path,
    bounds: dict[int, PageBounds],
    spans: list[tuple[int, Position, Position]],
    exam_dir: Path,
    part: str,
) -> list[dict]:
    doc = fitz.open(source)
    cache = _ExportCache(doc)
    entries: list[dict] = []
    try:
        for number, start, end in spans:
            slices = _span_slices(start, end, bounds)
            name = f"{part}_{number:02d}.pdf"
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


def _numbered_spans(
    spans: list[tuple[Position, Position]]
) -> list[tuple[int, Position, Position]]:
    return [(n, start, end) for n, (start, end) in enumerate(spans, start=1)]


def _works_questions_out(paper: Paper) -> bool:
    """Whether the paper answers its questions one by one.

    The 2014 papers answer theirs in a key page at the end and only remark on
    a question here and there ("התשובות שאינה נכונה הינה"), which is not a
    worked solution to cut the question short at.
    """
    return len(paper.solutions) * 2 >= len(paper.headings)


def _split_sitting(papers: list[Paper], out_root: Path) -> dict:
    plain = [p for p in papers if not p.answer_hint]
    solved = [p for p in papers if p.answer_hint]
    representative = (plain or solved)[0]
    sitting = representative.sitting
    exam_dir = out_root / (sitting.slug if sitting else _slug(representative.path))
    _clear_generated(exam_dir)

    warnings: list[str] = []
    answer_source: Path | None = None

    question_paper = (plain or solved)[0]
    if plain:
        question_paper = max(plain, key=lambda p: len(p.headings))
    question_spans, own_answers = _split_at_solutions(question_paper)
    questions = _export(
        question_paper.path, question_paper.bounds, question_spans, exam_dir, "question"
    )

    if plain and solved:
        # The solution copy is the exam again with the answers written into it,
        # so the same headings serve both: a question is cropped from the plain
        # paper and its whole entry, question and working alike, from the copy.
        answer_paper = max(solved, key=lambda p: len(p.headings))
        strategy = "paired exam + solution"
        answers = _export(
            answer_paper.path,
            answer_paper.bounds,
            _numbered_spans(_question_spans(answer_paper)),
            exam_dir,
            "answer",
        )
        answer_source = answer_paper.path
        if len(answer_paper.headings) != len(question_paper.headings):
            warnings.append(
                f"{len(question_paper.headings)} questions in the exam but "
                f"{len(answer_paper.headings)} in the solution copy"
            )
    elif not solved:
        strategy = "exam only"
        answers = []
    else:
        # One file holding both. Where it works every question out, that is the
        # whole solution; where it only remarks on one here and there and
        # answers the rest in a key page at the end, those remarks are all
        # there is to show.
        strategy = (
            "combined question/solution"
            if _works_questions_out(question_paper)
            else "exam with answer key"
        )
        answers = _export(
            question_paper.path, question_paper.bounds, own_answers, exam_dir, "answer"
        )
        answer_source = question_paper.path

    if question_paper.used_fallback:
        warnings.append("question numbers unreadable; headings taken in page order")

    index = {
        "label": sitting.label if sitting else _fallback_label(representative.path),
        "year": sitting.year,
        "season": SEASON,
        "moed": sitting.moed,
        "course": COURSE,
        "strategy": strategy,
        "source": {
            "questions": str(question_paper.path),
            "answers": str(answer_source) if answer_source else None,
        },
        "questions": questions,
        "answers": answers,
        "question_count": len(questions),
        "answer_count": len(answers),
    }
    if warnings:
        index["warnings"] = warnings
    (exam_dir / "index.json").write_text(
        json.dumps(index, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    return {"dir": exam_dir.name, **index}


def _slug(path: Path) -> str:
    slug = NON_ALNUM_RE.sub("-", path.stem.lower()).strip("-")
    return f"medical-{slug or 'exam'}"


def _fallback_label(path: Path) -> str:
    label = re.sub(r"[_-]+", " ", path.stem)
    return re.sub(r"\s+", " ", label).strip(" +-.") or path.stem


def prepare(root: Path, out_root: Path) -> list[dict]:
    pdfs = sorted(root.rglob("*.pdf"))
    if not pdfs:
        raise SystemExit(f"no PDF files found under {root}")

    papers: list[Paper] = []
    for path in pdfs:
        paper = _read(path)
        if len(paper.headings) < MIN_HEADINGS:
            print(f"not a paper, skipped: {path.name}")
            continue
        papers.append(paper)

    groups: dict[tuple, list[Paper]] = {}
    for paper in papers:
        key = (
            (paper.sitting.year, paper.sitting.moed)
            if paper.sitting
            else (paper.path.stem,)
        )
        groups.setdefault(key, []).append(paper)

    out_root.mkdir(parents=True, exist_ok=True)
    results = [_split_sitting(groups[key], out_root) for key in sorted(groups, key=str)]
    for result in results:
        print(
            f"{result['label']}: {result['question_count']} questions, "
            f"{result['answer_count']} answers ({result['strategy']})"
        )
        for warning in result.get("warnings", []):
            print(f"    {warning}")

    (out_root / "medical-report.json").write_text(
        json.dumps(results, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    questions = sum(r["question_count"] for r in results)
    answered = sum(r["answer_count"] for r in results)
    print(f"\n{len(results)} sittings, {questions} questions, {answered} answers")
    return results


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("root", help="folder containing the Medical Imaging PDFs")
    parser.add_argument("--out", default="MedicalSplit")
    args = parser.parse_args()
    prepare(Path(args.root).resolve(), Path(args.out).resolve())


if __name__ == "__main__":
    main()
