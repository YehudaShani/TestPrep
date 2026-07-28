from pathlib import Path

import pytest

from pdf_splitter.auto_split import Header, Line, _own_body_end
from pdf_splitter.signals import (
    ANSWER_HINT_RE,
    _leading_run,
    _named_headers,
    _priced_headers,
    _section_headers,
    _sitting,
    find_headers,
)

BODY = 12.0


def _body_text(count: int = 6, page: int = 0) -> list[Line]:
    """Enough ordinary text that the body size is unambiguous."""
    return [
        Line(page, 200 + i * 20, 212 + i * 20, "טקסט רגיל של גוף השאלה " * 3, size=BODY)
        for i in range(count)
    ]


@pytest.mark.parametrize(
    ("stem", "sitting"),
    [
        ("2016_Spring_Term_A", (2016, "Spring", "A")),
        ("2016_Spring_Term_A_Sol", (2016, "Spring", "A")),
        # A trailing underscore must not hide the sitting letter.
        ("2016_Winter_Term_B_", (2016, "Winter", "B")),
        ("2007 Winter Term A Sol2", (2007, "Winter", "A")),
        ("2014 Winter Term A Sol v2", (2014, "Winter", "A")),
        ("2021_Spring_Final_A", (2021, "Spring", "A")),
        ("מבחן חורף 2024 מועד א", (2024, "Winter", "A")),
        ("פתרון מבחן אביב 2023 מועד ב", (2023, "Spring", "B")),
    ],
)
def test_names_read_as_sittings(stem: str, sitting: tuple) -> None:
    read = _sitting(Path(stem + ".pdf"))

    assert (read.year, read.season, read.moed) == sitting


def test_a_sitting_labels_and_names_itself() -> None:
    sitting = _sitting(Path("2016_Winter_Term_B_Sol.pdf"))

    assert sitting.label == "2016 Winter — Moed B"
    assert sitting.slug == "signals-2016-winter-b"


@pytest.mark.parametrize(
    "stem",
    ["2016_Spring_Term_A_Sol", "2007 Winter Term A Sol2", "פתרון מבחן חורף 2024 מועד א"],
)
def test_solution_files_are_recognized(stem: str) -> None:
    assert ANSWER_HINT_RE.search(stem)


@pytest.mark.parametrize("stem", ["2016_Spring_Term_A", "מבחן חורף 2024 מועד א"])
def test_exam_files_are_not_taken_for_solutions(stem: str) -> None:
    assert not ANSWER_HINT_RE.search(stem)


def test_cover_page_weights_are_not_question_headings() -> None:
    # The cover lists what each question is worth, in body type; the headings
    # themselves are set larger.
    lines = [
        *(
            Line(0, 100 + i * 20, 112 + i * 20, f"25% - {i + 1}– שאלה", size=BODY)
            for i in range(4)
        ),
        *_body_text(),
        *(
            Line(page, 28, 45, f"(25%) {page}שאלה", size=17.2)
            for page in (1, 2, 3, 4)
        ),
    ]

    assert len(_named_headers(lines)) == 4


def test_multiple_choice_part_is_taken_after_the_open_questions() -> None:
    lines = [
        Line(1, 97, 113, "שאלה1 ( 30% )", size=16.0),
        *_body_text(page=1),
        Line(2, 72, 88, "שאלה2 ( 30% )", size=16.0),
        *(
            Line(3 + n // 2, 72 + (n % 2) * 300, 84, f"שאלה{n + 1}", size=BODY)
            for n in range(10)
        ),
    ]

    assert len(_named_headers(lines)) == 12


def test_a_smaller_run_before_the_headings_is_not_a_choice_part() -> None:
    lines = [
        *(
            Line(0, 100 + n * 20, 112 + n * 20, f"שאלה{n + 1}", size=BODY)
            for n in range(4)
        ),
        *_body_text(),
        Line(1, 28, 45, "שאלה1 ( 25% )", size=17.2),
        Line(2, 28, 45, "שאלה2 ( 25% )", size=17.2),
    ]

    assert len(_named_headers(lines)) == 2


def test_a_heading_extracted_backwards_still_heads_its_question() -> None:
    lines = [*_body_text(), Line(0, 50, 64, "־ זמן רציף1שאלה", size=14.3)]

    assert len(_named_headers(lines)) == 1


def test_a_sentence_ending_in_a_question_number_is_not_a_heading() -> None:
    lines = [*_body_text(), Line(0, 50, 62, "תרגיל בית10 שאלה4", size=BODY)]

    assert _named_headers(lines) == []


def test_a_solution_heading_is_not_a_question_heading() -> None:
    lines = [
        *_body_text(),
        Line(0, 50, 64, "שאלה1 ( 35 )'נק", size=14.0),
        Line(1, 50, 64, "שאלה1 – פתרון", size=14.0),
    ]

    assert len(_named_headers(lines)) == 1


def test_headings_with_no_question_word_are_found_by_weight_and_size() -> None:
    # A couple of papers lose the word "שאלה" to a broken font encoding and
    # leave only the number and the weight; sub-sections are priced the same
    # way but stay in body type.
    lines = [
        Line(1, 72, 88, "1 ( 28% )", size=16.0),
        *_body_text(page=1),
        Line(1, 569, 581, "א 4% האם המערכת יציבה", size=BODY),
        Line(2, 72, 88, "2 ( 26% )", size=16.0),
        Line(2, 457, 469, "א 3% יש לחשב את התמרת פורייה", size=BODY),
        Line(3, 318, 334, "3 ( 24% )", size=16.0),
    ]

    assert [h.page for h in _priced_headers(lines)] == [1, 2, 3]


def test_a_priced_heading_out_of_sequence_is_not_taken() -> None:
    lines = [
        *_body_text(),
        Line(1, 72, 88, "1 ( 28% )", size=16.0),
        Line(2, 72, 88, "4 ( 26% )", size=16.0),
    ]

    assert len(_priced_headers(lines)) == 1


def test_latex_solution_sections_are_found_by_size() -> None:
    lines = [
        Line(0, 50, 64, "1", size=14.3),
        *_body_text(),
        Line(1, 50, 64, "2 Q2", size=14.3),
        # An enumerated item inside a solution is set in body type.
        Line(1, 300, 312, "3", size=BODY),
        Line(2, 50, 64, "3", size=14.3),
    ]

    assert [h.page for h in _section_headers(lines)] == [0, 1, 2]


def test_sub_sections_past_the_last_question_are_dropped() -> None:
    headers = [Header(0, 50, n) for n in (1, 2, 3, 4, 7, 26)]

    assert [h.parsed_number for h in _leading_run(headers)] == [1, 2, 3, 4]


def test_named_headings_win_over_the_generic_search() -> None:
    lines = [
        *_body_text(),
        Line(0, 50, 66, "שאלה1 ( 30% )", size=16.0),
        # The generic search reads every priced sub-section as a question.
        Line(0, 120, 132, "א. ( 5% ) יש לרשום את פונקציית התמסורת", size=BODY),
        Line(0, 140, 152, "ב. ( 5% ) האם המערכת יציבה", size=BODY),
        Line(1, 50, 66, "שאלה2 ( 30% )", size=16.0),
    ]

    assert len(find_headers(lines)) == 2


def test_open_question_keeps_the_sub_sections_labelled_like_choices() -> None:
    # Sub-sections are lettered א through ו, exactly like a choice block, so
    # the choice scan has to stay out of an open question's way.
    lines = [
        Line(0, 50, 62, "שאלה1 ( 25 נקודות )"),
        *(
            Line(0, 100 + i * 20, 112 + i * 20, f"{letter}. ( 4% ) סעיף")
            for i, letter in enumerate("אבגדהו")
        ),
        Line(0, 260, 272, "חשבו את פונקציית התמסורת של המערכת"),
        Line(0, 300, 312, "פתרון:"),
    ]
    header = Header(0, 50, 1)

    assert _own_body_end(header, (0, 780), lines, use_choices=False) == (0, 298.0)
    assert _own_body_end(header, (0, 780), lines) == (0, 216.0)
