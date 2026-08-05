from pdf_splitter.study_server import exam_label, sort_key


def _order(sittings: list[tuple[str, dict]]) -> list[str]:
    """The order the viewer would list these exams in."""
    return [
        dirname
        for dirname, _ in sorted(sittings, key=lambda e: sort_key(e[0], e[1]))
    ]


def _sitting(year: int, season: str, moed: str = "A") -> tuple[str, dict]:
    name = f"{year}-{season.lower()}-{moed.lower()}"
    return name, {"year": year, "season": season, "moed": moed}


def test_the_newest_year_comes_first() -> None:
    assert _order([_sitting(2019, "Winter"), _sitting(2026, "Winter")]) == [
        "2026-winter-a",
        "2019-winter-a",
    ]


def test_the_semesters_of_a_year_run_backwards_like_the_years_do() -> None:
    # Winter is examined in February, spring in June, summer in September, so
    # newest first means summer, then spring, then winter.
    order = _order(
        [_sitting(2025, "Winter"), _sitting(2025, "Summer"), _sitting(2025, "Spring")]
    )

    assert order == ["2025-summer-a", "2025-spring-a", "2025-winter-a"]


def test_moed_a_comes_before_moed_b_of_the_same_sitting() -> None:
    # The one place the list does not run backwards: the two are the same exam
    # twice, and A is the one to study first.
    order = _order(
        [
            _sitting(2024, "Spring", "C"),
            _sitting(2024, "Spring", "B"),
            _sitting(2024, "Spring", "A"),
        ]
    )

    assert order == ["2024-spring-a", "2024-spring-b", "2024-spring-c"]


def test_a_sitting_with_no_season_follows_the_dated_ones_of_its_year() -> None:
    undated = ("2007-3", {"year": 2007, "season": None, "moed": "A"})

    assert _order([undated, _sitting(2007, "Winter")]) == ["2007-winter-a", "2007-3"]


def test_a_sitting_with_no_year_at_all_goes_last() -> None:
    unknown = ("scan0001", {})

    assert _order([unknown, _sitting(2005, "Spring")]) == ["2005-spring-a", "scan0001"]


def test_a_course_that_records_no_sitting_is_ordered_by_its_directory_names() -> None:
    names = [
        ("Sol_044105_Moed_B_Winter_2024", {}),
        ("Sol_044105_Moed_A_Winter_2024", {}),
        ("Sol_044105_Moed_A_Spring_2024", {}),
    ]

    assert _order(names) == [
        "Sol_044105_Moed_A_Spring_2024",
        "Sol_044105_Moed_A_Winter_2024",
        "Sol_044105_Moed_B_Winter_2024",
    ]


def test_a_directory_name_that_carries_the_sitting_is_labelled_by_it() -> None:
    assert (
        exam_label("Sol_044105_Moed_A_Spring_2024") == "2024 Spring — Moed A"
    )


def test_a_directory_name_that_carries_no_sitting_is_its_own_label() -> None:
    assert exam_label("extra-problems") == "extra-problems"
