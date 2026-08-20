"""Prepare the Compressible Fluid Mechanics (036008) recitation sets.

Twelve practice sets from Spring 2026, the tutorial half of the course whose
exams `fluids` prepares. Every one is written the same way: a page or two of
the week's theory, then "Question 1:", its statement, "Solution:", its worked
answer, and on to the next question. That is the interleaved shape the shared
splitter already reads, so this module only has to say where a set's number
and title come from and hand `fluids`' own heading and solution readings over.

    python -m pdf_splitter.fluids_exercises "Compressed Fluid Mechanics Exercises" \
        --out FluidsExercisesSplit

Two things the sets will not say about themselves:

* the number, which the papers get wrong — practice 7 is headed "Practice 6" —
  and which is therefore read from the file name, as the exams' moed is; and
* the title, which is the "Subject:" line under the header, and in the one set
  that has no such line is what is left of the header after its own number.

The theory that opens a set is not split out: it stands before the first
question and the viewer holds questions and answers, so it is dropped the way
an exam's cover page is. The sets are numbered continuously across a pair when
they run in a pair, so practice 12 opens at question 3; that numbering is kept
rather than restarted, since it is how the two halves refer to each other.
"""

from __future__ import annotations

import argparse
import json
import re
from dataclasses import dataclass
from pathlib import Path

import fitz

from .auto_split import Line, _extract_lines, auto_split_pdf
from .fluids import COURSE, find_headers, sol_marker

# The set's number, from the file name: "...-practice 1.pdf", "practice 10
# 2026.pdf", "Practice 11- Rehearsal...". A year is never mistaken for it,
# since the word always stands right before the number.
PRACTICE_RE = re.compile(r"practice\s*(\d{1,2})(?!\d)", re.I)
# The line under the header that says what the set is about.
SUBJECT_RE = re.compile(r"^\s*subject\s*[:\-–]\s*(.+?)\s*$", re.I)
# What is left of a header once the course and the set's own number are cut
# away, for the one set that carries no "Subject:" line.
HEADER_TITLE_RE = re.compile(
    r"^\s*(?:compressible\s+fluid\s+mechanics\s*)?(?:spring\s*20\d\d\s*)?"
    r"[-–\s]*practice\s*\d{1,2}\s*[-–:\s]*(.*?)\s*(?:20\d\d)?\s*$",
    re.I,
)
# How far into a set its own header and subject are still worth reading.
TITLE_PAGES = 1
# The sets are all of one semester, which the first two of them state in
# their headers and the rest leave to the folder.
YEAR = 2026
SEASON = "Spring"


@dataclass(frozen=True)
class Practice:
    number: int
    title: str

    @property
    def label(self) -> str:
        if not self.title:
            return f"Practice {self.number}"
        return f"Practice {self.number} — {self.title}"

    @property
    def slug(self) -> str:
        return f"fluids-practice-{self.number:02d}"


def _title(lines: list[Line]) -> str:
    """What the set says it is about, from its subject line or its header."""
    opening = [ln.text for ln in lines if ln.page < TITLE_PAGES]
    for text in opening:
        if (match := SUBJECT_RE.match(text)) is not None:
            return match.group(1)
    for text in opening:
        if (match := HEADER_TITLE_RE.match(text)) is not None and match.group(1):
            return match.group(1)
    return ""


def _practice(path: Path, lines: list[Line]) -> Practice:
    match = PRACTICE_RE.search(path.stem)
    if match is None:
        raise SystemExit(f"cannot tell which practice set {path.name} is")
    return Practice(int(match.group(1)), _title(lines))


def _clear_generated(out_dir: Path) -> None:
    out_dir.mkdir(parents=True, exist_ok=True)
    for pattern in ("question_*.pdf", "answer_*.pdf"):
        for path in out_dir.glob(pattern):
            path.unlink()


def _split(source: Path, out_dir: Path) -> dict:
    return auto_split_pdf(
        source,
        out_dir,
        part="auto",
        # Every question opens with its own heading and carries its own
        # figure, and the theory above the first one belongs to no question.
        include_shared_setup=False,
        run_to_next_header=True,
        stop_at_solution=True,
        header_finder=find_headers,
        sol_marker=sol_marker,
        # A set states a question and works it out directly under it, always.
        # Half of them hold only two questions, which leaves a single marker
        # standing between the first heading and the last, so no threshold
        # above that reading would take it.
        min_interleaved_markers=0,
    )


def _split_set(path: Path, out_root: Path) -> dict:
    doc = fitz.open(path)
    try:
        lines, _ = _extract_lines(doc)
    finally:
        doc.close()
    practice = _practice(path, lines)

    out_dir = out_root / practice.slug
    _clear_generated(out_dir)
    result = _split(path, out_dir)

    questions = result.get("questions", [])
    answers = result.get("answers", [])
    index = {
        "label": practice.label,
        "practice": practice.number,
        "year": YEAR,
        "season": SEASON,
        "moed": None,
        "course": COURSE,
        "strategy": "combined question/solution",
        "source": {"questions": str(path), "answers": str(path) if answers else None},
        "questions": questions,
        "answers": answers,
        "question_count": len(questions),
        "answer_count": len(answers),
    }
    (out_dir / "index.json").write_text(
        json.dumps(index, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    return {"dir": out_dir.name, **index}


def prepare(root: Path, out_root: Path) -> list[dict]:
    pdfs = sorted(root.rglob("*.pdf"))
    if not pdfs:
        raise SystemExit(f"no PDF files found under {root}")

    out_root.mkdir(parents=True, exist_ok=True)
    results = [_split_set(path, out_root) for path in pdfs]
    results.sort(key=lambda result: result["practice"])
    for result in results:
        print(
            f"{result['label']}: {result['question_count']} questions, "
            f"{result['answer_count']} answers"
        )

    (out_root / "fluids-exercises-report.json").write_text(
        json.dumps(results, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    questions = sum(result["question_count"] for result in results)
    answers = sum(result["answer_count"] for result in results)
    print(f"\n{len(results)} practice sets, {questions} questions, {answers} answers")
    empty = [result["label"] for result in results if not result["question_count"]]
    for label in empty:
        print(f"  unread: {label}")
    return results


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "root", help="folder holding the Compressible Fluid Mechanics practice sets"
    )
    parser.add_argument("--out", default="FluidsExercisesSplit")
    args = parser.parse_args()
    prepare(Path(args.root).resolve(), Path(args.out).resolve())


if __name__ == "__main__":
    main()
