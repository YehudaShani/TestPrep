"""Prepare the supplied Physics 3 exam collection for the study site.

The collection mixes three layouts:

* exam-only PDFs;
* separate exam and solution PDFs; and
* combined PDFs where every question is followed by its solution.

This command pairs matching files by their normalized names, selects the
appropriate segmentation mode, and writes the same question_NN.pdf /
answer_NN.pdf structure consumed by ``build_site``.

    python -m pdf_splitter.physics3 "physics 3 tests" --out Physics3Split
"""

from __future__ import annotations

import argparse
import json
import re
from dataclasses import dataclass
from pathlib import Path

import fitz

from .auto_split import (
    ANSWER_TEXT_COLOR_RE,
    HIGHLIGHT_FILL_RE,
    _extract_lines,
    _find_exam_headers,
    _sol_marker,
    auto_split_pdf,
)
from .splitter import _ExportCache, _write_segment_pdf

ANSWER_HINT_RE = re.compile(r"(?:^|[\s+_-])(?:solutions?|sol)(?:$|[\s+_.-])", re.I)
YEAR_RE = re.compile(r"(20\d{2})")
NON_ALNUM_RE = re.compile(r"[^a-z0-9]+")

# A small number of source PDFs break a question across a physical page in a
# way that cannot be inferred from their headings alone. Keep these corrections
# next to the collection-specific pairing logic so a full rebuild preserves the
# reviewed crops.
SEGMENT_OVERRIDES: dict[
    str, dict[str, dict[int, list[tuple[int, float, float]]]]
] = {
    "physics3-winter-moeda-20242025-solution": {
        "question": {
            # Prompt on source page 3; choices on source page 4.
            4: [(2, 671.1, 745.6), (3, 67.2, 267.4)],
        },
    },
    "physics3-exam-a-spring-2023-solution": {
        "answer": {
            # Choices a-d end page 8 and e-f + the solution begin page 9.
            # Tighten both sides of the page break to remove the false gap.
            9: [(7, 599.2, 767.6), (8, 67.4, 278.3)],
        },
    },
}


@dataclass
class PdfInfo:
    path: Path
    header_count: int
    solution_marker_count: int
    has_answer_markup: bool

    @property
    def answer_hint(self) -> bool:
        return bool(ANSWER_HINT_RE.search(self.path.stem))


def _identity(path: Path) -> str:
    stem = ANSWER_HINT_RE.sub(" ", path.stem.lower()).lstrip("- ")
    return NON_ALNUM_RE.sub("", stem)


def _label(path: Path) -> str:
    label = ANSWER_HINT_RE.sub(" ", path.stem)
    label = re.sub(r"[_-]+", " ", label)
    label = re.sub(r"\bmoed\s*([abc])\b", r"Moed \1", label, flags=re.I)
    label = re.sub(r"\bexam\b", "", label, flags=re.I)
    label = re.sub(r"\s+", " ", label).strip(" +-.")
    return label or path.stem


def _slug(path: Path) -> str:
    slug = NON_ALNUM_RE.sub("-", path.stem.lower()).strip("-")
    return f"physics3-{slug or 'exam'}"


def _inspect(path: Path) -> PdfInfo:
    doc = fitz.open(path)
    try:
        lines, _ = _extract_lines(doc)
        headers = _find_exam_headers(lines)
        markers = sum(1 for line in lines if _sol_marker(line.text)[0])
        markup = False
        for page in doc:
            if any(
                annot.type[1].lower() in {"highlight", "underline", "squiggly"}
                for annot in (page.annots() or [])
            ):
                markup = True
                break
            for xref in page.get_contents():
                stream = doc.xref_stream(xref)
                if HIGHLIGHT_FILL_RE.search(stream) or ANSWER_TEXT_COLOR_RE.search(
                    stream
                ):
                    markup = True
                    break
            if markup:
                break
    finally:
        doc.close()
    return PdfInfo(path, len(headers), markers, markup)


def _entries_by_number(entries: list[dict]) -> list[dict]:
    by_number: dict[int, dict] = {}
    for entry in entries:
        by_number[int(entry["n"])] = entry
    return [by_number[n] for n in sorted(by_number)]


def _clear_generated(exam_dir: Path) -> None:
    exam_dir.mkdir(parents=True, exist_ok=True)
    for pattern in ("question_*.pdf", "answer_*.pdf"):
        for path in exam_dir.glob(pattern):
            path.unlink()


def _apply_segment_overrides(
    exam_dir: Path,
    question_source: Path | None,
    answer_source: Path | None,
) -> None:
    overrides = SEGMENT_OVERRIDES.get(exam_dir.name)
    if not overrides:
        return

    sources = {"question": question_source, "answer": answer_source}
    for part, numbered_slices in overrides.items():
        source = sources[part]
        if source is None:
            raise ValueError(f"{exam_dir.name}: missing {part} source for override")
        doc = fitz.open(source)
        cache = _ExportCache(doc)
        try:
            for number, slices in numbered_slices.items():
                out_path = exam_dir / f"{part}_{number:02d}.pdf"
                if not _write_segment_pdf(cache, slices, out_path):
                    raise ValueError(
                        f"{exam_dir.name}: empty override for {part} {number}"
                    )
        finally:
            cache.close()
            doc.close()


def _split_group(infos: list[PdfInfo], out_root: Path) -> dict:
    plain = [info for info in infos if not info.answer_hint]
    solved = [info for info in infos if info.answer_hint]
    representative = plain[0].path if plain else solved[0].path
    exam_dir = out_root / _slug(representative)
    _clear_generated(exam_dir)

    question_result: dict = {"questions": []}
    answer_result: dict = {"answers": []}
    question_source: Path | None = None
    answer_source: Path | None = None
    strategy: str

    if plain and solved:
        question_source = max(plain, key=lambda info: info.header_count).path
        answer_source = max(solved, key=lambda info: info.header_count).path
        question_result = auto_split_pdf(
            question_source,
            exam_dir,
            part="questions",
            include_shared_setup=False,
            trim_after_options=True,
        )
        answer_result = auto_split_pdf(
            answer_source,
            exam_dir,
            part="answers",
            include_shared_setup=False,
        )
        strategy = "paired exam + solution"
    else:
        info = infos[0]
        question_source = info.path
        if info.solution_marker_count >= 2:
            question_result = auto_split_pdf(
                info.path,
                exam_dir,
                part="questions",
                trim_after_options=True,
                include_shared_setup=False,
            )
            answer_result = auto_split_pdf(
                info.path,
                exam_dir,
                part="answers",
                include_shared_setup=False,
            )
            answer_source = info.path
            strategy = "combined question/solution"
        elif info.answer_hint or info.has_answer_markup:
            question_result = auto_split_pdf(
                info.path,
                exam_dir,
                part="questions",
                include_shared_setup=False,
                trim_after_options=True,
            )
            answer_result = auto_split_pdf(
                info.path,
                exam_dir,
                part="answers",
                include_shared_setup=False,
            )
            answer_source = info.path
            strategy = "annotated solution copy"
        else:
            question_result = auto_split_pdf(
                info.path,
                exam_dir,
                part="questions",
                include_shared_setup=False,
                trim_after_options=True,
            )
            strategy = "questions only"

    _apply_segment_overrides(exam_dir, question_source, answer_source)

    questions = _entries_by_number(question_result.get("questions", []))
    answers = _entries_by_number(answer_result.get("answers", []))
    index = {
        "label": _label(representative),
        "course": "Physics 3",
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

    infos = [_inspect(path) for path in pdfs]
    groups: dict[str, list[PdfInfo]] = {}
    for info in infos:
        groups.setdefault(_identity(info.path), []).append(info)

    out_root.mkdir(parents=True, exist_ok=True)
    results: list[dict] = []
    for key in sorted(groups):
        group = groups[key]
        result = _split_group(group, out_root)
        results.append(result)
        print(
            f"{result['label']}: {result['question_count']} questions, "
            f"{result['answer_count']} answers ({result['strategy']})"
        )

    (out_root / "physics3-report.json").write_text(
        json.dumps(results, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    return results


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("root", help="folder containing the Physics 3 PDFs")
    parser.add_argument("--out", default="Physics3Split")
    args = parser.parse_args()
    prepare(Path(args.root).resolve(), Path(args.out).resolve())


if __name__ == "__main__":
    main()
