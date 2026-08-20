from pathlib import Path

import pytest

from pdf_splitter.auto_split import Line
from pdf_splitter.fluids_exercises import Practice, _practice, _title


def _line(text: str, page: int = 0, y: float = 100.0, size: float = 12.0) -> Line:
    return Line(page, y, y + 12.0, text, size=size)


@pytest.mark.parametrize(
    ("stem", "number"),
    [
        ("Compressible fluid mechanics Spring 2026-practice 1", 1),
        ("Compressible fluid mechanics-practice 3 2026", 3),
        # The year stands beside the number in half the names and is never
        # taken for it: the word always comes first.
        ("Compressible fluid mechanics-practice 10 2026", 10),
        ("Practice 11- Rehearsal before the exam part 1 2026", 11),
        ("Practice 12- Rehearsal before the exam part 2 2026", 12),
    ],
)
def test_the_set_is_numbered_by_its_file_name(stem: str, number: int) -> None:
    assert _practice(Path(stem + ".pdf"), [_line("Question 1:")]).number == number


def test_the_paper_does_not_overrule_the_name_about_the_number() -> None:
    # Practice 7 is headed "Practice 6", as its predecessor was.
    lines = [
        _line("Compressible fluid mechanics- Practice 6"),
        _line("Subject: Oblique shock waves and supersonic flow over wedges", y=120),
    ]

    practice = _practice(Path("Compressible fluid mechanics-practice 7 2026.pdf"), lines)

    assert practice.number == 7
    assert practice.title == "Oblique shock waves and supersonic flow over wedges"


def test_the_subject_line_titles_the_set() -> None:
    lines = [
        _line("Compressible Fluid Mechanics Spring 2026- Practice 1"),
        _line("Subject: Thermodynamics basics", y=120),
        _line("Useful constants:", y=140),
    ]

    assert _title(lines) == "Thermodynamics basics"


def test_a_set_with_no_subject_line_is_titled_by_its_header() -> None:
    # Practice 12 carries its topic in the header instead, with the course
    # name, its own number and the year around it.
    lines = [_line("Practice 12- Rehearsal before the exam part 2")]

    assert _title(lines) == "Rehearsal before the exam part 2"


def test_only_the_opening_page_is_read_for_a_title() -> None:
    # A later page may open with prose the patterns would otherwise take.
    lines = [
        _line("Compressible fluid mechanics- Practice 4"),
        _line("Subject: Rayleigh (heat addition) flow", y=120),
        _line("Subject: something a later page says", page=2),
    ]

    assert _title(lines) == "Rayleigh (heat addition) flow"


def test_a_set_labels_and_names_itself() -> None:
    practice = Practice(3, "Isentropic flow and isentropic relations")

    assert practice.label == "Practice 3 — Isentropic flow and isentropic relations"
    # Zero-padded, so the sets list in the order they are given out.
    assert practice.slug == "fluids-practice-03"


def test_a_set_that_says_nothing_about_itself_is_labelled_by_number_alone() -> None:
    assert Practice(5, "").label == "Practice 5"


def test_a_file_that_is_not_a_practice_set_is_refused() -> None:
    with pytest.raises(SystemExit):
        _practice(Path("2018 A.pdf"), [_line("Question 1:")])
