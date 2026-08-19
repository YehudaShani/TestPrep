"""Prepare the supplied Compressible Fluid Mechanics (036008) exam collection.

Fifteen papers, 2016-2025, four or fewer open questions each, and no two
consecutive years written the same way. Nearly half of the collection is
still in Word, so the module converts the .docx papers to PDF (through Word
itself) before reading them; the converted file is kept beside its source.

The questions are headed in five dialects, which the finder takes in turn:

* Hebrew, "שאלה 1 (30 נק')" or "תרגיל 1 - (25 נקודות)" — RTL extraction
  breaks the word itself apart ("ת רגיל 2"), so the letters are matched with
  space allowed between them;
* English, "QUESTION #1 (25 POINTS)" or "Question 1:", at the start of a line
  so that "Solution for question 1:" is not read as one; and
* a Word list, where the number is auto-numbered and lands wherever bidi
  ordering leaves it — usually at the end of the line it heads
  ("(30 Points) The supersonic flow ... 1."). That reading is only taken when
  the other two find nothing, since a numbered line with a weight beside it
  is otherwise an ordinary sub-section.

Solutions are marked "Solution to Q1:", "Solution for question 1:" or
"פתרון המבחן", which the shared splitter is handed as this collection's
`sol_marker`; two papers state each question and work it out immediately
after, and are split on those markers.

    python -m pdf_splitter.fluids "Compressable Fluid Mechanics" --out FluidsSplit

What the collection cannot say about itself is which sitting a paper is. The
headers inside the papers contradict their names — the 2022 Moed B paper is
headed "מועד א", the 2024 Moed B paper "Term A" — so the moed is read from
the file name, which is consistent throughout, and only the year and season
are read from the paper (a Hebrew academic year, an English season, or the
date the exam was held). "partial solution 2019.pdf" is misnamed outright:
it is the handwritten solution to questions 1-3 of the 2021 Moed A paper,
whose data it works with question for question, and it is written out below.
"""

from __future__ import annotations

import argparse
import json
import re
import subprocess
from dataclasses import dataclass
from pathlib import Path

import fitz

from .auto_split import Header, Line, _extract_lines, auto_split_pdf
from .splitter import _ExportCache, _write_segment_pdf

COURSE = "Compressible Fluid Mechanics"

# Word documents are converted next to their source, and re-converted when
# the source has been edited since.
DOCX_GLOB = "*.docx"

# The question word, in both spellings the papers use and with the space RTL
# extraction leaves between its letters.
HEB_HEADING_RE = re.compile(
    r"(?:ש\s*א\s*ל\s*ה|ת\s*ר\s*ג\s*י\s*ל)\s*(\d{1,2})(?!\d)"
)
# "QUESTION #1", "Question 1:", "Q1 – (30 POINTS)". Anchored, so the line
# that opens a worked solution is not read as the question starting again.
EN_HEADING_RE = re.compile(r"^\s*(?:question|q)\s*[#.]?\s*(\d{1,2})(?!\d)", re.I)
# What a question is worth, in either language. The bracket may hold a stray
# fragment RTL extraction pushed inside it ("( ,ש"ב25 נקודות)").
SCORE_RE = re.compile(
    r"\(\s*[^()]{0,12}?(\d{1,3})\s*[^()]{0,12}?(?:points?|pts?|pt\.|נקודות|נק['׳])",
    re.I,
)
# A Word list number, wherever bidi ordering left it on the line.
LIST_NUMBER_RE = re.compile(r"(?:^|\s)(\d{1,2})\.(?=\s|$)")
# A heading is a line, not a paragraph. The Hebrew ones carry their weight
# and nothing else; anything longer is prose that mentions a question.
MAX_HEADING_CHARS = 70
# Papers run to four questions. A reading past that is not a reading of the
# questions — except in a file that states each question and then solves it,
# where the solutions are numbered over again.
MAX_QUESTIONS = 8

# "Solution to Q1:", "Solution for question 1:", "פתרון המבחן". The whole
# line has to be the marker, so the title of a solution file ("Solution to
# exam A in compressible fluid mechanics...") is not read as one.
SOL_MARKER_RE = re.compile(
    r"^\s*(?:solutions?|answers?)\s*(?:for|to)?\s*(?:questions?|q)?\s*"
    r"(\d{1,2})?\s*[:.\-–]?\s*$",
    re.I,
)
HEB_SOL_MARKER_RE = re.compile(
    r"^\s*פתרון\s*(?:ה?מבחן|הבחינה|השאלות)?\s*(?:שאלה)?\s*(\d{1,2})?\s*[:.\-–]?\s*$"
)

# "Solution 2023A", "ExamB 2024 V1 solutions", "partial solution 2019".
ANSWER_HINT_RE = re.compile(r"(?:^|[\s_-])(?:sol\w*|solutions?)(?:$|[\s_.-])|פתרון", re.I)

YEAR_RE = re.compile(r"20\d\d")
# "MoedA", "Exam A 2021", "ExamB 2025", "2018 A", "2023A". The letter must
# not run into another one, so "Exam A vr 2" reads as Moed A and "ExamB" as
# Moed B.
MOED_RE = re.compile(r"(?:moed|exam|term|final|מועד)\s*([abcאבג])(?![a-zא-ת\d])", re.I)
YEAR_MOED_RE = re.compile(r"20\d\d\s*([abc])(?![a-z\d])", re.I)
SEASON_RE = re.compile(r"winter|spring|summer|חורף|אביב|קיץ", re.I)
SEASONS = {
    "winter": "Winter",
    "spring": "Spring",
    "summer": "Summer",
    "חורף": "Winter",
    "אביב": "Spring",
    "קיץ": "Summer",
}
MOEDS = {"a": "A", "b": "B", "c": "C", "א": "A", "ב": "B", "ג": "C"}
NON_ALNUM_RE = re.compile(r"[^a-z0-9]+")

# A Hebrew academic year, "תשע"ו" or "תשפ"ב", with the quote and the spaces
# RTL extraction scatters through it taken out first. Its letters add up to
# the year since 5000; a Technion exam of that academic year — winter in
# February, spring in July — falls in the calendar year it ends in.
HEB_YEAR_RE = re.compile(r"תש[א-ת]{1,2}")
HEB_LETTER_VALUES = {
    **{letter: value for value, letter in enumerate("אבגדהוזחט", start=1)},
    **{letter: value * 10 for value, letter in enumerate("יכלמנסעפצ", start=1)},
    **{letter: value * 100 for value, letter in enumerate("קרשת", start=1)},
}
HEB_YEAR_OFFSET = 1240
# "25/03/25", the date the exam was held. Winter-semester exams are held in
# February and March, spring-semester ones from July to October.
DATE_RE = re.compile(r"\b(\d{1,2})/(\d{1,2})/(\d{2,4})\b")
MONTH_SEASONS = {1: "Winter", 2: "Winter", 3: "Winter", 4: "Winter",
                 7: "Spring", 8: "Spring", 9: "Spring", 10: "Spring"}
# How far into a paper its own header is still worth reading.
COVER_PAGES = 2

# Papers whose name says nothing true about the sitting they belong to.
SITTING_OVERRIDES: dict[str, tuple[int, str | None, str]] = {
    # Named for 2019, but every question it works out is a question of the
    # 2021 Moed A paper: the Argon shock tube at p4/p1 = 3.5, the nozzle fed
    # at 5 atm with At = 1m², and the diamond airfoil at M = 3.
    "partial solution 2019": (2021, "Spring", "A"),
}

# Crops that cannot be read off the headings: exam dir -> part -> question
# number -> (page, y0, y1) slices, in PDF points from the page top. Page
# numbers are 0-based.
SEGMENT_OVERRIDES: dict[
    str, dict[str, dict[int, list[tuple[int, float, float]]]]
] = {
    # A scan of a handwritten solution: no text to find headings in, so the
    # three questions it answers are taken as whole pages. Question 4 was
    # never worked out, which is what makes the file "partial".
    "fluids-2021-spring-a": {
        "answer": {
            1: [(0, 0.0, 842.0), (1, 0.0, 842.0)],
            2: [(2, 0.0, 842.0)],
            3: [(3, 0.0, 842.0), (4, 0.0, 842.0)],
        }
    },
    # Questions 2 and 4 are worked out with no "Solution to Q..." line
    # between statement and solution — the solution simply starts. Both
    # statements end in white space, and the boundary is measured there.
    "fluids-2024-spring-b": {
        "question": {
            2: [(3, 93.8, 280.0)],
            4: [(7, 93.8, 350.0)],
        },
        "answer": {
            2: [(3, 280.0, 801.9), (4, 40.0, 801.9)],
            4: [(7, 350.0, 801.9)],
        },
    },
}

# Questions to drop after splitting: exam dir -> part -> numbers.
SEGMENT_DROPS: dict[str, dict[str, set[int]]] = {
    # The paper closes with a "פתרון המבחן / שאלה 3" cover page and nothing
    # under it, which reads as a fourth question.
    "fluids-2016-winter-a": {"question": {4}},
    # "Solution for question 4:" is followed by "a." and the file ends: the
    # last solution was never written.
    "fluids-2024-a": {"answer": {4}},
}


def _heading_number(text: str) -> int | None:
    """The question number a line heads, or None if it heads none."""
    match = EN_HEADING_RE.match(text)
    if match:
        return int(match.group(1))
    if len(text) <= MAX_HEADING_CHARS:
        match = HEB_HEADING_RE.search(text)
        if match:
            return int(match.group(1))
    return None


def _named_headers(lines: list[Line]) -> list[Header]:
    """Headings that name their question, in Hebrew or in English."""
    found: list[Header] = []
    for ln in lines:
        number = _heading_number(ln.text)
        if number is not None and 1 <= number <= MAX_QUESTIONS:
            found.append(Header(ln.page, ln.y0 - 2.0, number))
    return found


def _list_headers(lines: list[Line]) -> list[Header]:
    """Headings a Word list numbered, whose number the bidi run has moved.

    The number is wherever it landed on the line, so the only thing that
    tells a question from a sub-section is that it carries the question's
    weight and continues the count.
    """
    found: list[Header] = []
    expected = 1
    for ln in lines:
        if not SCORE_RE.search(ln.text):
            continue
        numbers = {int(m.group(1)) for m in LIST_NUMBER_RE.finditer(ln.text)}
        if expected in numbers:
            found.append(Header(ln.page, ln.y0 - 2.0, expected))
            expected += 1
    return found


def find_headers(lines: list[Line]) -> list[Header]:
    named = _named_headers(lines)
    if len(named) >= 2:
        return named
    return max((named, _list_headers(lines)), key=len)


def sol_marker(text: str) -> tuple[bool, int | None]:
    """Whether a line opens a worked solution, and the question it answers."""
    for pattern in (SOL_MARKER_RE, HEB_SOL_MARKER_RE):
        match = pattern.match(text)
        if match:
            number = match.group(1)
            return True, int(number) if number else None
    return False, None


@dataclass(frozen=True)
class Sitting:
    year: int | None
    season: str | None
    moed: str | None

    def __bool__(self) -> bool:
        return bool(self.year and self.moed)

    @property
    def label(self) -> str:
        season = f" {self.season}" if self.season else ""
        return f"{self.year}{season} — Moed {self.moed}"

    @property
    def slug(self) -> str:
        season = f"-{self.season.lower()}" if self.season else ""
        return f"fluids-{self.year}{season}-{self.moed.lower()}"


def _hebrew_year(text: str) -> int | None:
    """The calendar year a Hebrew academic year names, if the text gives one."""
    match = HEB_YEAR_RE.search(re.sub(r"[\s\"'׳״]", "", text))
    if not match:
        return None
    value = sum(HEB_LETTER_VALUES.get(letter, 0) for letter in match.group(0))
    return value + HEB_YEAR_OFFSET


def _paper_sitting(lines: list[Line]) -> Sitting:
    """What the paper's own opening pages say about the sitting."""
    text = " ".join(ln.text for ln in lines if ln.page < COVER_PAGES)
    year = None
    season = None
    if (found := YEAR_RE.search(text)) is not None:
        year = int(found.group(0))
    if (found := SEASON_RE.search(text)) is not None:
        season = SEASONS[found.group(0).lower()]
    if (found := DATE_RE.search(text)) is not None:
        month = int(found.group(2))
        season = season or MONTH_SEASONS.get(month)
        year = year or 2000 + int(found.group(3)) % 100
    hebrew = _hebrew_year(text)
    return Sitting(year or hebrew, season, None)


def _name_sitting(path: Path) -> Sitting:
    """What the file name says: the moed always, the rest when it is there."""
    stem = path.stem
    year = YEAR_RE.search(stem)
    season = SEASON_RE.search(stem)
    moed = MOED_RE.search(stem) or YEAR_MOED_RE.search(stem)
    return Sitting(
        int(year.group(0)) if year else None,
        SEASONS[season.group(0).lower()] if season else None,
        MOEDS[moed.group(1).lower()] if moed else None,
    )


def _sitting(path: Path, lines: list[Line]) -> Sitting:
    override = SITTING_OVERRIDES.get(path.stem)
    if override:
        return Sitting(*override)
    named = _name_sitting(path)
    paper = _paper_sitting(lines)
    return Sitting(
        named.year or paper.year, named.season or paper.season, named.moed
    )


def _slug(path: Path) -> str:
    """Directory name for a paper whose name gives no sitting."""
    slug = NON_ALNUM_RE.sub("-", path.stem.lower()).strip("-")
    return f"fluids-{slug or 'exam'}"


@dataclass
class PdfInfo:
    path: Path
    sitting: Sitting
    header_count: int
    solution_marker_count: int
    numbering_resets: bool

    @property
    def answer_hint(self) -> bool:
        return bool(ANSWER_HINT_RE.search(self.path.stem))


def _inspect(path: Path) -> PdfInfo:
    doc = fitz.open(path)
    try:
        lines, _ = _extract_lines(doc)
        headers = find_headers(lines)
        markers = sum(1 for ln in lines if sol_marker(ln.text)[0])
        numbers = [h.parsed_number for h in headers if h.parsed_number is not None]
        resets = any(
            number == 1 for number in numbers[3:]
        )
        return PdfInfo(path, _sitting(path, lines), len(headers), markers, resets)
    finally:
        doc.close()


def convert_word_papers(root: Path) -> list[Path]:
    """Export every .docx in the collection to a PDF beside it.

    Word is the only thing that lays these files out as their authors saw
    them — equations, drawn figures and all — so the conversion is done by
    Word itself, and only for files whose PDF is missing or out of date.
    """
    pending = [
        path
        for path in sorted(root.rglob(DOCX_GLOB))
        if not path.name.startswith("~$")
        and not (
            (pdf := path.with_suffix(".pdf")).exists()
            and pdf.stat().st_mtime >= path.stat().st_mtime
        )
    ]
    if not pending:
        return []
    script = """
$word = New-Object -ComObject Word.Application
$word.Visible = $false
foreach ($src in $args) {
  $dst = [System.IO.Path]::ChangeExtension($src, ".pdf")
  $doc = $word.Documents.Open($src, $false, $true)
  $doc.ExportAsFixedFormat($dst, 17)
  $doc.Close(0)
}
$word.Quit()
"""
    done = subprocess.run(
        ["powershell", "-NoProfile", "-Command", script, *(str(p) for p in pending)],
        capture_output=True,
        text=True,
    )
    missing = [p for p in pending if not p.with_suffix(".pdf").exists()]
    if missing:
        raise SystemExit(
            "could not convert the Word papers to PDF "
            f"({done.stderr.strip() or 'Word not available?'}); "
            "export them by hand next to their .docx and run again:\n  "
            + "\n  ".join(str(p) for p in missing)
        )
    return pending


def _clear_generated(exam_dir: Path) -> None:
    exam_dir.mkdir(parents=True, exist_ok=True)
    for pattern in ("question_*.pdf", "answer_*.pdf"):
        for path in exam_dir.glob(pattern):
            path.unlink()


def _split(source: Path, exam_dir: Path, part: str) -> dict:
    return auto_split_pdf(
        source,
        exam_dir,
        part=part,
        # Nothing stands above a question that belongs to it: each one opens
        # with its own heading and carries its own figure.
        include_shared_setup=False,
        run_to_next_header=True,
        stop_at_solution=part in {"auto", "questions"},
        header_finder=find_headers,
        sol_marker=sol_marker,
        # These papers say "Solution to Q1" and mean it, so two markers are
        # already a paper that states each question and then works it out.
        min_interleaved_markers=2,
    )


def _apply_overrides(exam_dir: Path, sources: dict[str, Path | None]) -> None:
    """Write the crops recorded by hand, replacing any read off the headings."""
    for part, numbered in SEGMENT_OVERRIDES.get(exam_dir.name, {}).items():
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
    for part, numbers in SEGMENT_DROPS.get(exam_dir.name, {}).items():
        for number in numbers:
            (exam_dir / f"{part}_{number:02d}.pdf").unlink(missing_ok=True)


def _surviving(entries: list[dict], exam_dir: Path, part: str) -> list[dict]:
    dropped = SEGMENT_DROPS.get(exam_dir.name, {}).get(part, set())
    overridden = SEGMENT_OVERRIDES.get(exam_dir.name, {}).get(part, {})
    by_number = {
        int(entry["n"]): entry for entry in entries if int(entry["n"]) not in dropped
    }
    for number in sorted(set(overridden) - set(by_number) - dropped):
        by_number[number] = {
            "n": number,
            "file": f"{part}_{number:02d}.pdf",
            "by_hand": True,
        }
    return [by_number[number] for number in sorted(by_number)]


def _split_group(infos: list[PdfInfo], out_root: Path) -> dict:
    plain = [info for info in infos if not info.answer_hint]
    solved = [info for info in infos if info.answer_hint]
    representative = (plain or solved)[0]
    sitting = representative.sitting
    exam_dir = out_root / (sitting.slug if sitting else _slug(representative.path))
    _clear_generated(exam_dir)

    question_result: dict = {"questions": []}
    answer_result: dict = {"answers": []}

    if plain and solved:
        question_source = max(plain, key=lambda info: info.header_count).path
        answer_source = max(solved, key=lambda info: info.header_count).path
        question_result = _split(question_source, exam_dir, "questions")
        answer_result = _split(answer_source, exam_dir, "answers")
        strategy = "paired exam + solution"
    else:
        info = max(infos, key=lambda candidate: candidate.header_count)
        question_source = answer_source = info.path
        if info.solution_marker_count >= 2 or info.numbering_resets:
            # Question, then its worked solution, then the next question.
            question_result = answer_result = _split(info.path, exam_dir, "auto")
            strategy = "combined question/solution"
        else:
            # Exam paper only: this collection keeps no answer key, so a
            # sitting with no solution file simply has no answers.
            question_result = _split(info.path, exam_dir, "questions")
            strategy = "exam only"

    _apply_overrides(
        exam_dir, {"question": question_source, "answer": answer_source}
    )
    _apply_drops(exam_dir)

    questions = _surviving(question_result.get("questions", []), exam_dir, "question")
    answers = _surviving(answer_result.get("answers", []), exam_dir, "answer")
    index = {
        "label": sitting.label if sitting else representative.path.stem,
        "year": sitting.year,
        "season": sitting.season,
        "moed": sitting.moed,
        "course": COURSE,
        "strategy": strategy,
        "source": {
            "questions": str(question_source),
            "answers": str(answer_source) if answers else None,
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
    converted = convert_word_papers(root)
    for path in converted:
        print(f"converted {path.name} to PDF")

    pdfs = sorted(root.rglob("*.pdf"))
    if not pdfs:
        raise SystemExit(f"no PDF files found under {root}")

    groups: dict[tuple, list[PdfInfo]] = {}
    for path in pdfs:
        info = _inspect(path)
        # The season is what the papers of one sitting disagree about, so a
        # paper is grouped by the year and moed its name gives and takes the
        # season from whichever of them states one.
        key = (
            (info.sitting.year, info.sitting.moed)
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

    (out_root / "fluids-report.json").write_text(
        json.dumps(results, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    empty = [r["label"] for r in results if not r["question_count"]]
    print(f"\n{len(results)} sittings, {len(empty)} with no readable questions")
    for label in empty:
        print(f"  unread: {label}")
    return results


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "root", help="folder containing the Compressible Fluid Mechanics papers"
    )
    parser.add_argument("--out", default="FluidsSplit")
    args = parser.parse_args()
    prepare(Path(args.root).resolve(), Path(args.out).resolve())


if __name__ == "__main__":
    main()
