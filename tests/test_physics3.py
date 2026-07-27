from pathlib import Path

import fitz
import pytest
from PIL import Image

from pdf_splitter.auto_split import (
    Header,
    Line,
    PageBounds,
    _assign_numbers,
    _build_question_segments,
    _find_exam_headers,
)
from pdf_splitter.build_site import _neutralize_colors
from pdf_splitter.physics3 import (
    SEGMENT_OVERRIDES,
    _apply_segment_overrides,
    _exam_info,
    _identity,
)


def test_physics_headers_survive_rtl_and_broken_digits() -> None:
    lines = [
        Line(0, 50, 62, "1 :", bold=True),
        Line(0, 200, 212, "ש אלה2 :", bold=True),
        Line(1, 50, 62, "הלאש 3 :", bold=True),
        Line(1, 200, 212, "שאלה4 :", bold=True),
        Line(2, 50, 62, "שאלה5 :", bold=True),
        Line(2, 200, 212, "שאלה6 :", bold=True),
        Line(3, 50, 62, "שאלה7 :", bold=True),
        Line(3, 200, 212, "שאלה8 :", bold=True),
        Line(4, 50, 62, "שאלה9 :", bold=True),
        Line(4, 200, 212, "שאלה10 :", bold=True),
        Line(5, 50, 62, "שאלה1 1 :", bold=True),
        Line(5, 200, 212, "שאלה2 1 :", bold=True),
        Line(6, 50, 62, "13 :", bold=True),
    ]

    numbered = _assign_numbers(_find_exam_headers(lines))

    assert [number for _, number, _ in numbered] == list(range(1, 14))


def test_unreliable_legacy_header_digits_fall_back_to_position() -> None:
    lines = [
        Line(0, 50 + i * 40, 62 + i * 40, text, bold=True)
        for i, text in enumerate(
            [
                "שאלה5",
                "שאלה2",
                "שאלה3",
                "שאלה1",
                "שאלה1",
                "שאלה6",
                "שאלה7",
                "שאלה8",
                "שאלה9",
                "שאלה54",
                "שאלה55",
                "שאלה52",
                "שאלה53",
            ]
        )
    ]

    numbered = _assign_numbers(_find_exam_headers(lines))

    assert [number for _, number, _ in numbered] == list(range(1, 14))


def test_physics_question_stops_before_solution_without_shared_setup() -> None:
    headers = [
        (Header(0, 50, 1), 1, []),
        (Header(0, 300, 2), 2, []),
    ]
    lines = [
        Line(0, 50, 62, "שאלה1"),
        Line(0, 100, 112, "א. first"),
        Line(0, 120, 132, "ב. second"),
        Line(0, 140, 152, "ג. third"),
        Line(0, 160, 172, "ד. fourth"),
        Line(0, 180, 192, "ה. fifth"),
        Line(0, 200, 212, "ו. sixth"),
        Line(0, 230, 242, "פתרון"),
        Line(0, 300, 312, "שאלה2"),
    ]

    segments = _build_question_segments(
        headers,
        {0: PageBounds(20, 780)},
        (0, 780),
        lines=lines,
        include_shared_setup=False,
        trim_after_options=True,
    )

    assert segments[0].slices == [(0, 50, 216)]
    assert segments[1].slices == [(0, 300, 780)]


def test_solution_and_exam_names_pair() -> None:
    exam = Path("ExamA-Winter2016.pdf")
    solution = Path("-ExamA-Winter2016-solution.pdf")

    assert _identity(exam) == _identity(solution)


def test_question_color_neutralization_turns_red_black() -> None:
    source = Image.frombytes("RGB", (2, 1), bytes([255, 0, 0, 255, 255, 255]))
    image = _neutralize_colors(source)

    assert [image.getpixel((x, 0)) for x in range(2)] == [
        (0, 0, 0),
        (255, 255, 255),
    ]


def test_collection_segment_override_stacks_requested_slices(
    tmp_path: Path, monkeypatch
) -> None:
    source = tmp_path / "source.pdf"
    source_doc = fitz.open()
    source_doc.new_page(width=100, height=200)
    source_doc.new_page(width=100, height=200)
    source_doc.save(source)
    source_doc.close()

    exam_dir = tmp_path / "physics3-test-override"
    exam_dir.mkdir()
    monkeypatch.setitem(
        SEGMENT_OVERRIDES,
        exam_dir.name,
        {"question": {4: [(0, 10, 40), (1, 50, 90)]}},
    )

    _apply_segment_overrides(exam_dir, source, None)

    result = fitz.open(exam_dir / "question_04.pdf")
    try:
        assert len(result) == 1
        assert result[0].rect == fitz.Rect(0, 0, 100, 70)
    finally:
        result.close()


@pytest.mark.parametrize(
    ("stem", "label"),
    [
        ("Sp2013MoedA ", "2013 Spring — Moed A"),
        ("Wn2015MoedB", "2015 Winter — Moed B"),
        ("ExamA-Winter2016", "2016 Winter — Moed A"),
        ("Spring 2022 Moed A + Solution", "2022 Spring — Moed A"),
        ("Exam B winter 2024 - sol", "2024 Winter — Moed B"),
        # Academic years name the sitting held in the later calendar year.
        ("Winter_moedA_20242025+Solution", "2025 Winter — Moed A"),
        ("Winter_moedA_2025-26+Solution", "2026 Winter — Moed A"),
        # Papers covering two sittings state the shared year once.
        ("Exam 2016- Spring B-Summer A- Solution ", "2016 Spring — Moed B + Summer — Moed A"),
        ("SummerA-SpringB-2018-With solution", "2018 Summer — Moed A + Spring — Moed B"),
        ("Summer Exam B  Spring Exam C with solution ", "Summer — Moed B + Spring — Moed C"),
    ],
)
def test_exam_labels_are_normalized(stem: str, label: str) -> None:
    assert _exam_info(Path(stem + ".pdf"))["label"] == label


def test_exam_info_reports_the_primary_sitting() -> None:
    info = _exam_info(Path("Spring 2020 Moed B+ Summer 2020 Moed A.pdf"))
    assert (info["year"], info["season"], info["moed"]) == (2020, "Spring", "B")


def test_unparseable_name_keeps_a_readable_label() -> None:
    assert _exam_info(Path("mystery-paper.pdf"))["label"] == "mystery paper"
