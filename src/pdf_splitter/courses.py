"""The courses the study viewer knows about.

Each course is a directory of split exams (``question_NN.pdf`` /
``answer_NN.pdf`` per exam folder) plus the metadata the viewer needs to
show it: a display name, the localStorage bucket its progress lives in,
and whether question crops must have their answer-color cues removed.

Both the local server and the static site build take their course list
from here, so a new course only has to be registered once.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

SITE_TITLE = "Exam Prep"

# GoatCounter site code ("<code>.goatcounter.com") for counting visits to the
# published site. Only the static build passes it to the page, so the local
# server never reports anything; empty here disables counting everywhere.
ANALYTICS_CODE = "testprepper"


@dataclass(frozen=True)
class Course:
    id: str
    title: str
    root: str
    storage_key: str
    # Legacy solution copies mark the correct choice in red; those exams need
    # the color dropped from question mode so it is not a giveaway.
    neutralize_question_colors: bool = False


COURSES: tuple[Course, ...] = (
    Course("circuits", "Electrical Circuits", "Split", "study-progress"),
    Course(
        "physics3",
        "Physics 3",
        "Physics3Split",
        "physics3-study-progress",
        neutralize_question_colors=True,
    ),
)

BY_ID = {course.id: course for course in COURSES}


def resolve(specs: list[str] | None, *, base: Path | None = None) -> list[Course]:
    """Resolve ``--course`` specs (``id`` or ``id=root``) to courses.

    Without specs, every registered course whose root directory exists is
    returned, so a machine that only has one collection prepared still
    builds and serves.
    """
    base = base or Path.cwd()
    if not specs:
        found = [c for c in COURSES if (base / c.root).is_dir()]
        if not found:
            roots = ", ".join(c.root for c in COURSES)
            raise SystemExit(f"no course directory found (looked for: {roots})")
        return found

    resolved = []
    for spec in specs:
        cid, _, root = spec.partition("=")
        course = BY_ID.get(cid)
        if course is None:
            known = ", ".join(BY_ID)
            raise SystemExit(f"unknown course {cid!r} (known: {known})")
        resolved.append(
            Course(
                course.id,
                course.title,
                root or course.root,
                course.storage_key,
                course.neutralize_question_colors,
            )
        )
    return resolved
