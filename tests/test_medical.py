from pathlib import Path

import pytest

from pdf_splitter.auto_split import Line
from pdf_splitter.medical import (
    ANSWER_HINT_RE,
    SOLUTION_RE,
    _headings,
    _numbered,
    _one_stop_headings,
    _parts,
    _sitting,
)

BODY = 12.0
TITLE = 16.0
OPEN = 18.0


def _body_text(count: int = 6, page: int = 0) -> list[Line]:
    """Enough ordinary text that the body size is unambiguous."""
    return [
        Line(page, 200 + i * 20, 212 + i * 20, "טקסט רגיל של גוף השאלה " * 3, size=BODY)
        for i in range(count)
    ]


def _choices(page: int, top: float) -> list[Line]:
    return [
        Line(page, top + i * 16, top + 12 + i * 16, f"{letter}. אפשרות", size=BODY)
        for i, letter in enumerate("אבגדה")
    ]


@pytest.mark.parametrize(
    ("stem", "sitting"),
    [
        ("046831 Spring2021 Moed A sol", (2021, "A")),
        ("046831 Spring2020 Moed B closed questions sol", (2020, "B")),
        ("מבחן מועד א 2024 עם פתרון", (2024, "A")),
        ("מועד ב + פתרון 2014", (2014, "B")),
        # The only paper of its year names no sitting letter.
        ("046831 Spring2016", (2016, "A")),
    ],
)
def test_names_read_as_sittings(stem: str, sitting: tuple) -> None:
    read = _sitting(Path(stem + ".pdf"))

    assert (read.year, read.moed) == sitting


def test_a_sitting_labels_and_names_itself() -> None:
    sitting = _sitting(Path("046831 Spring2022 Moed A.pdf"))

    assert sitting.label == "2022 Spring — Moed A"
    assert sitting.slug == "medical-2022-a"


@pytest.mark.parametrize(
    "stem",
    ["046831 Spring2022 Moed A Sol", "מבחן מועד א 2024 עם פתרון", "046831_Spring2018 תשובות"],
)
def test_solution_copies_are_recognized(stem: str) -> None:
    assert ANSWER_HINT_RE.search(stem)


@pytest.mark.parametrize("stem", ["046831 Spring2022 Moed A", "מועד א - 2017"])
def test_exam_files_are_not_taken_for_solution_copies(stem: str) -> None:
    assert not ANSWER_HINT_RE.search(stem)


def test_the_cover_page_time_plan_is_not_a_part_title() -> None:
    lines = [
        Line(0, 335, 347, ":המלצה לתכנון זמן הבחינה חלק 'א– 25 דקות", size=BODY),
        Line(0, 359, 371, "'חלק ב– 50 דקות", size=BODY),
        *_body_text(),
        Line(1, 36, 52, "'חלק א- שאלות רב-ברירה (כל שאלה5 'נק)", size=TITLE),
        Line(4, 36, 52, "'חלק ב- שאלות פתוחות (כל שאלה20 'נק)", size=TITLE),
    ]

    parts, titles = _parts(lines, BODY)

    assert len(parts) == 2
    assert [page for page, _ in titles] == [1, 4]


def test_each_part_numbers_its_own_questions_and_the_split_runs_them_on() -> None:
    # Part א counts 1..3 and part ב starts over at 1; the questions come out
    # numbered 1..5 in page order.
    lines = [
        Line(1, 36, 52, "'חלק א- שאלות רב-ברירה", size=TITLE),
        *(
            Line(1, 90 + n * 120, 102 + n * 120, f"{n + 1}. שאלה סגורה", size=BODY)
            for n in range(3)
        ),
        *_body_text(page=1),
        Line(4, 36, 52, "'חלק ב- שאלות פתוחות", size=TITLE),
        Line(4, 90, 108, "1 . שאלתCT פתוחה ( 20 )'נק", size=OPEN),
        Line(5, 90, 108, "2 . שאלתMRI פתוחה ( 20 )'נק", size=OPEN),
    ]

    tops, _, fallback = _headings(lines)

    assert [page for page, _ in tops] == [1, 1, 1, 4, 5]
    assert not fallback


def test_a_solution_heading_does_not_head_a_second_open_question() -> None:
    lines = [
        Line(0, 36, 52, "'חלק ב- שאלות פתוחות", size=TITLE),
        Line(0, 67, 85, "1 . שאלתCT פתוחה ( 20 )'נק", size=OPEN),
        *_body_text(),
        Line(1, 39, 57, "1 . שאלתCT פתוחה – פתרון", size=OPEN),
        Line(2, 67, 85, "2 . שאלתMRI פתוחה ( 20 )'נק", size=OPEN),
    ]

    tops, _, _ = _headings(lines)

    assert [page for page, _ in tops] == [0, 2]


@pytest.mark.parametrize(
    "text",
    [
        # The date at the head of every paper.
        "1.7.2016",
        # A font whose digits extract as other characters turns it into this.
        "62...602.",
        # A decimal in the body text.
        "0.5) = σ , ( N I",
    ],
)
def test_a_line_of_numbers_is_not_a_question_heading(text: str) -> None:
    assert _numbered([Line(0, 72, 84, text, size=BODY)]) == []


def test_a_numbered_line_with_no_text_of_its_own_still_heads_its_question() -> None:
    # One paper sets the question itself as an image and leaves only "1."
    assert len(_numbered([Line(1, 67, 79, "1.", size=BODY)])) == 1


def test_a_heading_may_open_on_a_quantity_of_its_own() -> None:
    # 2014 Moed B again: "2." for question 17, then the "9" its font makes of
    # the stop, then the question. A digit standing on its own is not the
    # rest of the number the line opened with.
    assert len(_numbered([Line(7, 246, 258, "2. 9 טכנאי מתקין מערכתMRI", size=BODY)])) == 1


def test_sub_sections_do_not_extend_the_run_of_questions() -> None:
    lines = [
        Line(0, 36, 52, "'חלק ב- שאלות פתוחות", size=TITLE),
        *(
            Line(n, 67, 85, f"{n + 1}. שאלת שערוך פתוחה", size=OPEN)
            for n in range(3)
        ),
        *_body_text(page=2),
        # Numbered sub-sections inside the last question, in body type.
        Line(2, 418, 430, "1. ?מהו מספר נקודות ההתאמה המינימלי", size=BODY),
        Line(2, 435, 447, "2. כמהinliers צפויים להיות", size=BODY),
    ]

    tops, _, _ = _headings(lines)

    assert len(tops) == 3


def test_a_paper_whose_full_stop_extracts_as_a_digit_is_read_by_that_stop() -> None:
    # 2014 Moed B: "2." comes out as "29", "20." as "20 9". The choices are the
    # one thing the mangled stop is never mistaken for.
    lines = [
        Line(0, 439, 451, "29 ( הקולימטוריםcollimators ) נועדו", size=BODY),
        *(
            Line(0, 455 + i * 15, 467 + i * 15, f"{i + 1}) אפשרות ראשונה", size=BODY)
            for i in range(4)
        ),
        Line(1, 142, 154, "99 בסריקתCT של רגל, קיים צורך להבחין", size=BODY),
        Line(2, 72, 84, "20 9 נתונה התמונה הבינארית הבאה", size=BODY),
    ]

    assert [ln.page for ln in _one_stop_headings(lines)] == [0, 1, 2]


def test_the_stop_reading_is_only_reached_when_the_numbers_cannot_be_read() -> None:
    lines = [
        *_body_text(),
        *(
            Line(n, 72, 84, f"{n + 1}. שאלה רגילה עם מספר קריא", size=BODY)
            for n in range(4)
        ),
        *_choices(0, 100),
    ]

    tops, _, fallback = _headings(lines)

    assert len(tops) == 4
    assert not fallback


@pytest.mark.parametrize(
    "text",
    [
        ":פתרון",
        ": פתרון",
        ":פתרון תשובה ב נכונה, 𝑇1 הוא זמן המאפיין את הרלקסציה",
        "התשובה הינה ב׳. כאשר נשתמש בסיקוונס שלSingle-Shot",
        "'תשובה א – רעש פואסוני, עם",
        ":התשובה שאינה נכונה הינה",
    ],
)
def test_worked_solutions_are_recognized(text: str) -> None:
    assert SOLUTION_RE.match(text)


@pytest.mark.parametrize(
    "text",
    [
        # A question that asks for the right answers, not one that gives them.
        ":התשובות הנכונות הינן",
        "ו. .)יש להחזיר את הדפים הסופיים של התשובות (ללא דפי טיוטה",
        "ב. מסוימת, תהליך הפתרון יהיה זהה",
    ],
)
def test_question_text_is_not_taken_for_a_solution(text: str) -> None:
    assert not SOLUTION_RE.match(text)
