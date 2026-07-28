"""Build a static study website from the split-exam directories.

Renders every question/answer PDF to a PNG and emits a self-contained
site that needs no server: progress is kept per-user in the browser's
localStorage. One site can carry several courses, each in its own
subdirectory, and the viewer offers a course picker:

    site/index.html
    site/<course>/exams.json
    site/<course>/img/<exam>/question_NN.png

    python -m pdf_splitter.build_site [--course ID[=ROOT] ...] [--out site]

Without --course, every course in ``courses.py`` whose source directory
exists is built. Incremental: a PNG is re-rendered only when its source
PDF is newer.
"""

from __future__ import annotations

import argparse
import io
import json
import shutil
from pathlib import Path

import fitz
from PIL import Image, ImageChops

from .courses import ANALYTICS_CODE, SITE_TITLE, Course
from .courses import resolve as resolve_courses
from .study_server import CONFIG_FLAG, expand_indexed_images, scan_exams

STATIC_FLAG = "window.STATIC_SITE = false;"
# Must match the suffix study.html builds its image URLs with.
IMAGE_SUFFIX = ".webp"


def _neutralize_colors(image: Image.Image) -> Image.Image:
    """Map every RGB pixel to its darkest channel.

    Legacy solution copies mark the correct choice in red. Their question
    crops keep the text but should not keep that color cue; using the darkest
    channel turns red/blue text black while preserving black text and white
    paper.
    """
    red, green, blue = image.split()
    darkest = ImageChops.darker(ImageChops.darker(red, green), blue)
    return Image.merge("RGB", (darkest, darkest, darkest))


def _encode(image: Image.Image) -> bytes:
    """Lossless WebP: pixel-identical to PNG at roughly a third the bytes."""
    output = io.BytesIO()
    image.save(output, format="WEBP", lossless=True, quality=80, method=4)
    return output.getvalue()


def render_pdf(
    pdf_path: Path,
    img_path: Path,
    dpi: int,
    *,
    neutralize_colors: bool = False,
) -> bool:
    """Render page 1 of pdf_path to img_path. Returns True if rendered."""
    if img_path.is_file() and img_path.stat().st_mtime >= pdf_path.stat().st_mtime:
        return False
    doc = fitz.open(pdf_path)
    try:
        expand_indexed_images(doc)
        pix = fitz.Pixmap(fitz.csRGB, doc[0].get_pixmap(dpi=dpi))
        image = Image.frombytes("RGB", (pix.width, pix.height), pix.samples)
    finally:
        doc.close()
    if neutralize_colors:
        image = _neutralize_colors(image)
    img_path.write_bytes(_encode(image))
    return True


def build_course(course: Course, root: Path, out: Path, dpi: int) -> dict:
    """Render one course into ``out/<course.id>/``; returns its viewer config."""
    exams = scan_exams(root)
    if not exams:
        raise SystemExit(f"no split exams found in {root}")

    course_out = out / course.id
    course_out.mkdir(parents=True, exist_ok=True)
    (course_out / "exams.json").write_text(
        json.dumps(exams, ensure_ascii=False), encoding="utf-8"
    )

    rendered = skipped = 0
    for exam in exams:
        src_dir = root / exam["dir"]
        img_dir = course_out / "img" / exam["dir"]
        img_dir.mkdir(parents=True, exist_ok=True)
        live_images = set()
        for pdf in sorted(src_dir.glob("*.pdf")):
            img_path = img_dir / (pdf.stem + IMAGE_SUFFIX)
            live_images.add(img_path.name)
            if render_pdf(
                pdf,
                img_path,
                dpi,
                neutralize_colors=(
                    course.neutralize_question_colors
                    and pdf.name.startswith("question_")
                ),
            ):
                rendered += 1
            else:
                skipped += 1
        # Images of questions that vanished, and renders in a retired format.
        for stale in img_dir.iterdir():
            if stale.is_file() and stale.name not in live_images:
                stale.unlink()
        print(f"{course.id}/{exam['dir']}: done")

    # Drop image dirs for exams that no longer exist in the source.
    live = {e["dir"] for e in exams}
    for d in (course_out / "img").iterdir():
        if d.is_dir() and d.name not in live:
            shutil.rmtree(d)
            print(f"removed stale {course.id}/{d.name}")

    print(f"{course.title}: {rendered} rendered, {skipped} up to date")
    return {
        "id": course.id,
        "title": course.title,
        "storageKey": course.storage_key,
        # Cache-buster: newest source PDF in the whole course.
        "assetVersion": str(
            max(
                pdf.stat().st_mtime_ns
                for exam in exams
                for pdf in (root / exam["dir"]).glob("*.pdf")
            )
        ),
    }


def build(courses: list[Course], out: Path, dpi: int, *, base: Path) -> None:
    page = Path(__file__).with_name("study.html").read_text(encoding="utf-8")
    if STATIC_FLAG not in page:
        raise SystemExit("study.html is missing the STATIC_SITE flag")
    if CONFIG_FLAG not in page:
        raise SystemExit("study.html is missing the STUDY_CONFIG flag")

    out.mkdir(parents=True, exist_ok=True)
    entries = [
        build_course(course, (base / course.root).resolve(), out, dpi)
        for course in courses
    ]

    config = json.dumps(
        {"title": SITE_TITLE, "courses": entries, "analytics": ANALYTICS_CODE},
        ensure_ascii=False,
        separators=(",", ":"),
    )
    (out / "index.html").write_text(
        page.replace(STATIC_FLAG, "window.STATIC_SITE = true;").replace(
            CONFIG_FLAG, f"window.STUDY_CONFIG = {config};"
        ),
        encoding="utf-8",
    )

    total_mb = sum(f.stat().st_size for f in out.rglob("*") if f.is_file()) / 2**20
    names = ", ".join(c.title for c in courses)
    print(f"\n{names}; site size {total_mb:.0f} MB -> {out}")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--course",
        action="append",
        metavar="ID[=ROOT]",
        help="course to build (repeatable); default: all with a source dir",
    )
    parser.add_argument("--out", default="site", help="output directory")
    parser.add_argument("--dpi", type=int, default=130)
    args = parser.parse_args()
    base = Path.cwd()
    build(
        resolve_courses(args.course, base=base),
        Path(args.out).resolve(),
        args.dpi,
        base=base,
    )


if __name__ == "__main__":
    main()
