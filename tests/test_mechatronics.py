from pathlib import Path

import pytest

from pdf_splitter.auto_split import Line
from pdf_splitter.mechatronics import (
    _choice_blocks,
    _item_headers,
    _named_headers,
    _options_on,
    _priced_headers,
    _sitting,
    _solution_headers,
    find_headers,
)


def _lines(page: int, texts: list[str], top: float = 100.0) -> list[Line]:
    return [
        Line(page, top + i * 20.0, top + i * 20.0 + 12.0, text, False, 12.0)
        for i, text in enumerate(texts)
    ]


def _choices(labels: str = "אבגדה") -> list[str]:
    return [f"{letter}) 12 וולט" for letter in labels]


@pytest.mark.parametrize(
    ("stem", "sitting"),
    [
        ("אביב 2009 מועד א", (2009, "Spring", "A")),
        ("מועד ב - חורף 2014 תשובות", (2014, "Winter", "B")),
        # Hebrew year: תשע"ב is the academic year examined in 2012.
        ("מועד א - חורף תשעב", (2012, "Winter", "A")),
        # The winter semester of 2015 is examined in February 2016.
        ("FinalA w2015 sol", (2016, "Winter", "A")),
    ],
)
def test_names_read_as_sittings(stem: str, sitting: tuple) -> None:
    read = _sitting(Path(stem + ".pdf"))

    assert (read.year, read.season, read.moed) == sitting


def test_a_sitting_labels_and_names_itself() -> None:
    sitting = _sitting(Path("מועד ג 2005 אביב.pdf"))

    assert sitting.label == "2005 Spring — Moed C"
    assert sitting.slug == "mechatronics-2005-spring-c"


def test_a_letter_standing_alone_after_the_year_is_the_sitting() -> None:
    read = _sitting(Path("מבחן 2007 א שאלה 3.pdf"))

    assert (read.year, read.moed) == (2007, "A")
    # No season in the name and no cover page to read one off.
    assert not read


@pytest.mark.parametrize(
    "text",
    ["א) 6 Ω", "א. 4.3[V]", "א( ] [0 A", ".א2 R C = L", "‏א) 18 וולט", "[ ] ( א6 V"],
)
def test_every_way_the_papers_write_an_alternative_reads_as_one(text: str) -> None:
    assert _options_on(text) == ["א"]


def test_a_choice_that_names_another_choice_does_not_count_as_one() -> None:
    # "ד) answers ב and ג are both right" ends in something shaped like a label.
    assert _options_on(".ד ג נכונות+ תשובות ב.") == ["ד"]


def test_lettering_carried_on_past_the_fifth_choice_is_still_lettering() -> None:
    # One paper sets two questions to a page and letters straight through.
    assert _options_on("ט. .אף תשובה אינה נכונה") == ["ט"]


def test_a_scrambled_word_that_opens_with_a_letter_is_not_a_choice() -> None:
    # "פשט" ("simplify") comes out of the extraction as "ט פש".
    assert _options_on("20 . ט פש :את הביטוי הבא") == []


def test_a_whole_choice_block_set_as_one_row_reads_as_four() -> None:
    assert _options_on(") (א ) (ב ) (ג ) (ד") == ["א", "ב", "ג", "ד"]


def test_two_questions_a_line_apart_are_two_blocks() -> None:
    lines = _lines(1, [*_choices(), "5 ( מה עושה המעגל ?", *_choices()])

    assert len(_choice_blocks(lines)) == 2


def test_the_question_above_a_block_is_its_heading() -> None:
    lines = _lines(1, ["נתון המעגל הבא:", "1 ( מהו הזרם ?", *_choices()])

    headers = _item_headers(lines)

    assert [(h.page, h.parsed_number) for h in headers] == [(1, 1)]
    assert headers[0].y == pytest.approx(lines[1].y0 - 2.0)


def test_the_cover_page_list_of_instructions_is_not_a_question() -> None:
    cover = _lines(0, ["1 . מותר להשתמש בכל חומר עזר", "2 . משך המבחן שלוש שעות"])
    body = _lines(1, ["1 ( מהו הזרם ?", *_choices()])

    headers = _item_headers([*cover, *body])

    assert [h.page for h in headers] == [1]


def test_a_value_inside_a_question_is_not_the_number_of_one() -> None:
    # "0.7  V בתחום הפעיל" opens with a number and a stop, like an item does.
    lines = _lines(
        1, ["0.7  V בתחום הפעיל", "11 . מה יהיה המתח ?", *_choices()]
    )

    headers = _item_headers(lines)

    assert len(headers) == 1
    assert headers[0].y == pytest.approx(lines[1].y0 - 2.0)


def test_numbers_that_do_not_run_1_2_3_are_left_to_position() -> None:
    # A font whose character map is wrong renders 10 as ".4" and 20 as "04".
    lines = _lines(
        1,
        [".) חשב את הזרם הזורם", *_choices(), "04 ) מהו המתח הנופל", *_choices()],
    )

    assert [h.parsed_number for h in _item_headers(lines)] == [None, None]


def test_a_question_answered_by_a_drawing_is_found_by_the_space_around_it() -> None:
    # Its five alternatives are graphs, so there is no block to find it by.
    lines = [
        *_lines(1, ["1 ( מהו הזרם ?", *_choices()]),
        *_lines(2, ["2 ( בחר גרף מתאים להתנהגות הזרם", "הגרפים שלהלן"]),
        *_lines(3, ["3 ( מהו המתח ?", *_choices()]),
    ]

    assert [h.page for h in _item_headers(lines)] == [1, 2, 3]


def test_a_paper_of_three_open_questions_is_read_by_its_headings() -> None:
    lines = [
        *_lines(1, ["שאלה1", "נתון המעגל הבא"]),
        *_lines(2, ["שאלה2 – תגובת מעבר", "נתון המעגל הבא"]),
        *_lines(3, ["(30%) שאלה3 – מגברי שרת"]),
    ]

    assert [h.parsed_number for h in _named_headers(lines)] == [1, 2, 3]


def test_headings_numbered_by_a_broken_font_are_taken_in_order() -> None:
    lines = [
        *_lines(1, ["שאלה 5 : ניתוח מעגלי DC ( % 1 2)"]),
        *_lines(2, ["שאלה0 : תגובת מעבר ( % 21 )"]),
        *_lines(3, ["שאלה2 : מגברים ( % 24 )"]),
    ]

    assert [h.page for h in _named_headers(lines)] == [1, 2, 3]


def test_a_heading_that_says_solution_heads_the_solution() -> None:
    lines = _lines(1, ["שאלה1 – מעגלים )35%(", "שאלה1 – פתרו"])

    assert [h.page for h in _named_headers(lines)] == [1]
    assert [h.parsed_number for h in _solution_headers(lines)] == [1]


def test_a_sentence_about_a_solution_is_not_a_heading() -> None:
    lines = _lines(1, ["פתרון", "פתרון הומוגני דועך של המשוואה"])

    assert len(_solution_headers(lines)) == 1


def test_a_question_named_only_by_its_number_and_weight_is_still_a_question() -> None:
    lines = [
        *_lines(1, ["1 ) [ ניתוח מעגלים% 30 :]", "נתון המעגל הבא"]),
        *_lines(2, ["2 ) [ תגובת תדר% 30 :]", "נתון המעגל הבא"]),
    ]

    assert [h.parsed_number for h in _priced_headers(lines)] == [1, 2]
    assert [h.parsed_number for h in find_headers(lines)] == [1, 2]


def test_the_topic_titles_of_a_multiple_choice_paper_are_not_its_questions() -> None:
    # Every item is headed by a number, and the six of them are grouped under
    # two "שאלה N" topic titles, which the naming search would read as two.
    lines = [
        *_lines(1, ["שאלה1: ניתוח מעגלים"]),
        *[ln for i in range(3) for ln in _lines(2 + i, [f"{i + 1} . מהו הזרם ?", *_choices()])],
        *_lines(5, ["שאלה2: מגברי שרת"]),
        *[ln for i in range(3) for ln in _lines(6 + i, [f"{i + 4} . מהו המתח ?", *_choices()])],
    ]

    assert len(find_headers(lines)) == 6
