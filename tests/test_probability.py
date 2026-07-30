from pathlib import Path

import pytest

from pdf_splitter.auto_split import PageBounds
from pdf_splitter.probability import (
    Band,
    _bounds,
    _choice_block,
    _choice_rows,
    _indent,
    _margin,
    _merged,
    _open_headings,
    _overhangs,
    _running,
    _sitting,
)

MARGIN = 470.0
INDENT = 18.0


def _text(page: int, y: float, x1: float = MARGIN, x0: float = 110.0) -> Band:
    return Band(page, y, y + 12.0, x0, x1)


def _body(page: int, count: int = 8, top: float = 100.0) -> list[Band]:
    """Enough justified body text that the right margin is unambiguous."""
    return [_text(page, top + i * 20.0) for i in range(count)]


@pytest.mark.parametrize(
    ("stem", "sitting"),
    [
        ("הסתברות 23 אביב א", (2023, "Spring", "A")),
        ("הסתברות 24 חורף ב", (2024, "Winter", "B")),
        ("הסתברות 25 קיץ א", (2025, "Summer", "A")),
        ("הסתברות 26 חורף א", (2026, "Winter", "A")),
    ],
)
def test_names_read_as_sittings(stem: str, sitting: tuple) -> None:
    read = _sitting(Path(stem + ".pdf"))

    assert (read.year, read.season, read.moed) == sitting


def test_a_sitting_labels_and_names_itself() -> None:
    sitting = _sitting(Path("הסתברות 25 קיץ ב.pdf"))

    assert sitting.label == "2025 Summer — Moed B"
    assert sitting.slug == "probability-2025-summer-b"


def test_a_name_without_a_sitting_reads_as_none() -> None:
    assert not _sitting(Path("scan0001.pdf"))


def test_alternatives_set_two_decks_high_count_as_one_row() -> None:
    # "3/8" over its rule breaks into three bands at the same height.
    choices = {1: [188.0, 200.0, 212.0, 494.0, 506.0]}

    assert _choice_rows(choices) == [(1, 212.0), (1, 506.0)]


def test_rows_on_the_same_page_stay_apart_when_they_are_whole_questions() -> None:
    choices = {2: [209.0, 379.0, 557.0, 749.0]}

    assert len(_choice_rows(choices)) == 4


def test_the_multiple_choice_part_is_the_unbroken_run_of_pages() -> None:
    # Pages 2 and 3 carry the alternatives; page 5 is an open question whose
    # formula happens to be shaped like a row of them, with page 4 in between.
    rows = [(2, 209.0), (2, 379.0), (3, 214.0), (3, 415.0), (5, 196.0), (5, 566.0)]

    assert _choice_block(rows) == rows[:4]


def test_a_paper_with_no_alternatives_at_all_reads_as_no_block() -> None:
    assert _choice_block([]) == []


def test_a_heading_of_two_lines_counts_once() -> None:
    # "(מבוססת על תרגילי הבית)" sits on a line of its own above the question,
    # hanging in the same margin.
    assert _merged([(1, 72.0), (1, 92.0), (1, 217.0)]) == [(1, 72.0), (1, 217.0)]


def test_headings_on_different_pages_are_never_merged() -> None:
    assert len(_merged([(1, 700.0), (2, 20.0)])) == 2


def test_the_right_margin_is_the_line_the_most_text_ends_on() -> None:
    # A short formula, a centred title and two hanging question numbers must
    # not pull the margin away from the body text.
    bands = [
        *_body(1),
        Band(1, 60.0, 74.0, 300.0, 380.0),
        Band(1, 400.0, 414.0, 240.0, 300.0),
        _text(1, 88.0, x1=MARGIN + INDENT),
        _text(1, 300.0, x1=MARGIN + INDENT),
    ]

    slope, intercept, support = _margin(bands)

    assert intercept == pytest.approx(MARGIN, abs=1.0)
    assert slope == pytest.approx(0.0, abs=0.01)
    assert support == 8


def test_a_keystoned_page_has_its_margin_read_as_a_slope() -> None:
    # A phone scan drifts the margin down the page by more than the indent
    # being measured, so a constant margin would read the drift as a heading.
    bands = [
        Band(1, 100.0 + i * 60.0, 112.0 + i * 60.0, 110.0, 520.0 - i * 8.0)
        for i in range(8)
    ]
    hanging = Band(1, 640.0, 652.0, 400.0, 520.0 - 9.0 * 8.0 + INDENT)

    overhangs = _overhangs([*bands, hanging], set())

    assert overhangs[hanging] == pytest.approx(INDENT, abs=2.0)
    assert all(abs(overhangs[b]) < 2.0 for b in bands)


def test_a_repeated_header_is_told_from_a_question_that_opens_a_page() -> None:
    # The same header across four pages, and a heading at the same height on
    # each: only the header keeps its width from page to page.
    header = [Band(p, 40.0, 52.0, 200.0, 400.0) for p in range(4)]
    headings = [
        Band(p, 72.0, 84.0, 110.0 + p * 60.0, MARGIN + INDENT) for p in range(4)
    ]
    tails = [Band(p, 700.0, 712.0, 110.0 + p * 40.0, 380.0) for p in range(4)]

    running = _running([*header, *headings, *tails])

    assert running == set(header)


def test_content_bounds_leave_the_running_header_out() -> None:
    class _Doc:
        def __init__(self) -> None:
            self._pages = [type("P", (), {"rect": type("R", (), {"height": 842.0})})()]

        def __len__(self) -> int:
            return 1

        def __getitem__(self, i: int):
            return self._pages[i]

    header = Band(0, 40.0, 52.0, 200.0, 400.0)
    bands = [header, _text(0, 100.0), _text(0, 700.0)]

    bounds = _bounds(bands, {header}, _Doc())

    assert bounds[0].top == pytest.approx(94.0)
    assert bounds[0].bottom == pytest.approx(718.0)


def test_the_indent_is_measured_off_the_questions_already_placed() -> None:
    starts = [(1, 60.0), (1, 240.0)]
    bands = [
        *_body(1),
        _text(1, 72.0, x1=MARGIN + 17.0),
        _text(1, 252.0, x1=MARGIN + 19.0),
    ]

    overhangs = _overhangs(bands, set())

    assert _indent(overhangs, starts) == pytest.approx(18.0, abs=1.0)


def test_a_sub_section_hanging_half_as_far_does_not_open_an_open_question() -> None:
    question = _text(2, 100.0, x1=MARGIN + INDENT)
    sub_section = _text(2, 300.0, x1=MARGIN + INDENT / 2)
    overhangs = _overhangs([*_body(2, top=140.0), question, sub_section], set())

    tops = _open_headings(overhangs, (2, 90.0), INDENT)

    assert tops == [(2, question.y0 - 3.0)]


def test_nothing_before_the_last_row_of_alternatives_opens_an_open_question() -> None:
    early = _text(2, 100.0, x1=MARGIN + INDENT)
    late = _text(2, 600.0, x1=MARGIN + INDENT)
    overhangs = _overhangs([*_body(2, top=140.0), early, late], set())

    tops = _open_headings(overhangs, (2, 500.0), INDENT)

    assert tops == [(2, late.y0 - 3.0)]
