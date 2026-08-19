from pathlib import Path

import pytest

from pdf_splitter.auto_split import Line
from pdf_splitter.fluids import (
    ANSWER_HINT_RE,
    _hebrew_year,
    _name_sitting,
    _paper_sitting,
    _sitting,
    find_headers,
    sol_marker,
)

BODY = 11.0


def _line(text: str, page: int = 0, y: float = 100.0, size: float = 12.0) -> Line:
    return Line(page, y, y + 12.0, text, size=size)


def _body_text(count: int = 6, page: int = 0) -> list[Line]:
    return [
        _line("ordinary body text of the question " * 2, page, 200 + i * 20, BODY)
        for i in range(count)
    ]


@pytest.mark.parametrize(
    ("text", "number"),
    [
        # Hebrew, both words, with the score written every way the papers do.
        ("שאלה1 ( 30 )'נק", 1),
        ("ש אלה 2 ) 25 ('נק", 2),
        ('תרגיל1 - ( ,ש"ב25 נקודות)', 1),
        ("ת רגיל 4 - ( 25 )נקודות", 4),
        # The bilingual year prints both headings on one line, either way round.
        ("Q3 – (30 POINTS) - תרגיל3 - ) 30 (נקודות", 3),
        ("- Q4 – (25 POINTS) תרגיל4 - ) 25 (נקודות", 4),
        # English.
        ("QUESTION #1 (25 POINTS)", 1),
        ("Question 2 (30 points):", 2),
        ("Question 3(35 points):", 3),
        ("Question 4:", 4),
    ],
)
def test_headings_are_read_in_every_dialect(text: str, number: int) -> None:
    headers = find_headers([_line(text), *_body_text()])

    assert [h.parsed_number for h in headers] == [number]


@pytest.mark.parametrize(
    "text",
    [
        # The line that opens a worked solution names the question but does
        # not head it.
        "Solution for question 1:",
        "Solution to Q1:",
        # Prose about a question, in a paper that heads its own with the word.
        ")שאלה זאת כוללת פתרון איטרטיבי. אין צורך להגיע לערך המדוייק של הלחץ, רק לעשות",
    ],
)
def test_lines_that_only_mention_a_question_do_not_head_one(text: str) -> None:
    assert find_headers([_line(text), *_body_text()]) == []


def test_a_word_list_heads_its_questions_by_number_and_weight() -> None:
    # The list number lands wherever the bidi run leaves it — usually at the
    # end of the line it heads.
    lines = [
        _line("(30 Points) The supersonic flow at the inlet of a channel 1.", size=BODY),
        _line("a. (5 pt.) Determine the shock angle of the first shocks", y=120, size=BODY),
        _line("(25Points) Two flow deflectors are used to deflect a stream 2.", y=140, size=BODY),
        *_body_text(),
    ]

    headers = find_headers(lines)

    assert [h.parsed_number for h in headers] == [1, 2]


def test_a_priced_sub_section_is_not_a_question() -> None:
    # Sub-sections carry a weight of their own, and are lettered rather than
    # numbered, so the count never continues through them.
    lines = [
        _line("(30 Points) The supersonic flow at the inlet of a channel 1.", size=BODY),
        _line("b. (10 pt.) Find the angles of the reflected shocks", y=120, size=BODY),
        _line("c. (10 pt.) Find the pressure at region 3", y=140, size=BODY),
        *_body_text(),
    ]

    headers = find_headers(lines)

    assert [h.parsed_number for h in headers] == [1]


def test_the_solutions_part_may_number_itself_over_again() -> None:
    # A paper that states its questions in Hebrew and works them out in
    # English keeps both runs; the splitter cuts between them at the reset.
    lines = [
        *(_line(f"תרגיל{n} - ( 25 נקודות)", page=0, y=100 + n * 60) for n in (1, 2)),
        *(_line(f"Question {n}", page=1, y=100 + n * 60) for n in (1, 2)),
        *_body_text(),
    ]

    headers = find_headers(lines)

    assert [h.parsed_number for h in headers] == [1, 2, 1, 2]


@pytest.mark.parametrize(
    ("text", "number"),
    [
        ("Solution to Q1:", 1),
        ("Solution for question 2:", 2),
        ("Solution:", None),
        ("פתרון המבחן", None),
        ("פתרון 3", 3),
    ],
)
def test_solution_markers_are_read_with_the_question_they_answer(
    text: str, number: int | None
) -> None:
    assert sol_marker(text) == (True, number)


@pytest.mark.parametrize(
    "text",
    [
        # The title of a solution file says more than a marker does.
        "Solution to exam A in compressible fluid mechanics spring semester 2023",
        "Question 1:",
        "1. The solution to the wave equation is given by",
    ],
)
def test_a_line_that_is_not_only_a_marker_is_not_one(text: str) -> None:
    assert sol_marker(text) == (False, None)


@pytest.mark.parametrize(
    ("stem", "moed"),
    [
        ("MoedA - Winter 2016", "A"),
        ("MoedB  - Winter 2016", "B"),
        ("2018 A", "A"),
        ("2023A", "A"),
        ("Exam A 2021", "A"),
        # "vr 2" is a draft number, not a sitting.
        ("Exam A vr 2", "A"),
        ("Exam B vr 4", "B"),
        ("ExamA 2024 V2 with solutions", "A"),
        ("ExamB 2025 V1", "B"),
    ],
)
def test_the_moed_is_read_from_the_file_name(stem: str, moed: str) -> None:
    assert _name_sitting(Path(stem + ".pdf")).moed == moed


@pytest.mark.parametrize(
    ("text", "year"),
    [("תשע\"ו", 2016), ('חורף תשע ח', 2018), ("תשע\"ט", 2019), ("חורף תש פ\"ב", 2022)],
)
def test_a_hebrew_academic_year_reads_as_the_year_it_ends_in(
    text: str, year: int
) -> None:
    assert _hebrew_year(text) == year


def test_the_year_and_season_come_from_the_paper_when_the_name_omits_them() -> None:
    # "Exam A vr 4.pdf" says only which moed it is; the paper says the rest.
    lines = [_line('מועד א | זרימה דחיסה036008 | "חורף תשע ט')]

    sitting = _sitting(Path("Exam A vr 4.pdf"), lines)

    assert (sitting.year, sitting.season, sitting.moed) == (2019, "Winter", "A")
    assert sitting.slug == "fluids-2019-winter-a"


def test_the_date_of_the_exam_says_which_semester_it_closed() -> None:
    # Winter-semester exams are held in February and March, spring-semester
    # ones from July to October.
    assert _paper_sitting([_line("Final Exam-Term B 25/03/25")]).season == "Winter"
    assert _paper_sitting([_line("Final Exam-Term A 26/09/24")]).season == "Spring"


def test_a_sitting_without_a_season_still_labels_and_names_itself() -> None:
    sitting = _sitting(Path("ExamA 2024 V2 with solutions.pdf"), [_line("Question 1:")])

    assert sitting.label == "2024 — Moed A"
    assert sitting.slug == "fluids-2024-a"


def test_the_paper_does_not_overrule_the_name_about_the_moed() -> None:
    # The 2022 Moed B paper is headed "מועד א", and the 2024 Moed B paper
    # "Term A"; the names are the consistent record.
    sitting = _sitting(Path("2022 B.pdf"), [_line('מועד א |זרימה דחיסה 036008 | חורף תשפ"ב')])

    assert (sitting.year, sitting.season, sitting.moed) == (2022, "Winter", "B")


def test_the_partial_solution_is_placed_with_the_paper_it_answers() -> None:
    # Named for 2019, but every question it works out is a 2021 Moed A one.
    sitting = _sitting(Path("partial solution 2019.pdf"), [])

    assert sitting.slug == "fluids-2021-spring-a"


@pytest.mark.parametrize(
    "stem", ["Solution 2023A", "ExamB 2024 V1 solutions", "partial solution 2019"]
)
def test_solution_files_are_recognized(stem: str) -> None:
    assert ANSWER_HINT_RE.search(stem)


@pytest.mark.parametrize("stem", ["2023A", "Exam A 2021", "MoedA - Winter 2016"])
def test_exam_files_are_not_taken_for_solutions(stem: str) -> None:
    assert not ANSWER_HINT_RE.search(stem)
