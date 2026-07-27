"""Build a static study website from the Split directory.

Renders every question/answer PDF to a PNG and emits a self-contained
site (index.html + exams.json + img/) that needs no server: progress is
kept per-user in the browser's localStorage.

    python -m pdf_splitter.build_site [--root Split] [--out site] [--dpi 130]

Incremental: a PNG is re-rendered only when its source PDF is newer.
"""

from __future__ import annotations

import argparse
import io
import json
import shutil
from pathlib import Path

import fitz
from PIL import Image, ImageChops

from .study_server import expand_indexed_images, scan_exams

STATIC_FLAG = "window.STATIC_SITE = false;"
CONFIG_FLAG = (
    'window.STUDY_CONFIG = {"title":"Electrical Circuits",'
    '"storageKey":"study-progress","assetVersion":""};'
)


def _neutralize_colors(pix: fitz.Pixmap) -> bytes:
    """Map every RGB pixel to its darkest channel.

    Legacy solution copies mark the correct choice in red. Their question
    crops keep the text but should not keep that color cue; using the darkest
    channel turns red/blue text black while preserving black text and white
    paper.
    """
    rgb = fitz.Pixmap(fitz.csRGB, pix)
    image = Image.frombytes("RGB", (rgb.width, rgb.height), rgb.samples)
    red, green, blue = image.split()
    darkest = ImageChops.darker(ImageChops.darker(red, green), blue)
    neutral = Image.merge("RGB", (darkest, darkest, darkest))
    output = io.BytesIO()
    neutral.save(output, format="PNG", optimize=True)
    return output.getvalue()


def render_pdf(
    pdf_path: Path,
    png_path: Path,
    dpi: int,
    *,
    neutralize_colors: bool = False,
) -> bool:
    """Render page 1 of pdf_path to png_path. Returns True if rendered."""
    if png_path.is_file() and png_path.stat().st_mtime >= pdf_path.stat().st_mtime:
        return False
    doc = fitz.open(pdf_path)
    try:
        expand_indexed_images(doc)
        pix = doc[0].get_pixmap(dpi=dpi)
        if neutralize_colors:
            png = _neutralize_colors(pix)
        else:
            png = pix.tobytes("png")
    finally:
        doc.close()
    png_path.write_bytes(png)
    return True


def build(
    root: Path,
    out: Path,
    dpi: int,
    *,
    title: str = "Electrical Circuits",
    storage_key: str = "study-progress",
    neutralize_question_colors: bool = False,
) -> None:
    exams = scan_exams(root)
    if not exams:
        raise SystemExit(f"no split exams found in {root}")

    out.mkdir(parents=True, exist_ok=True)
    (out / "exams.json").write_text(
        json.dumps(exams, ensure_ascii=False), encoding="utf-8"
    )

    page = Path(__file__).with_name("study.html").read_text(encoding="utf-8")
    if STATIC_FLAG not in page:
        raise SystemExit("study.html is missing the STATIC_SITE flag")
    if CONFIG_FLAG not in page:
        raise SystemExit("study.html is missing the STUDY_CONFIG flag")
    asset_version = str(
        max(
            pdf.stat().st_mtime_ns
            for exam in exams
            for pdf in (root / exam["dir"]).glob("*.pdf")
        )
    )
    config = json.dumps(
        {
            "title": title,
            "storageKey": storage_key,
            "assetVersion": asset_version,
        },
        ensure_ascii=False,
        separators=(",", ":"),
    )
    (out / "index.html").write_text(
        page.replace(STATIC_FLAG, "window.STATIC_SITE = true;").replace(
            CONFIG_FLAG, f"window.STUDY_CONFIG = {config};"
        ),
        encoding="utf-8",
    )

    rendered = skipped = 0
    for exam in exams:
        src_dir = root / exam["dir"]
        img_dir = out / "img" / exam["dir"]
        img_dir.mkdir(parents=True, exist_ok=True)
        for pdf in sorted(src_dir.glob("*.pdf")):
            if render_pdf(
                pdf,
                img_dir / (pdf.stem + ".png"),
                dpi,
                neutralize_colors=(
                    neutralize_question_colors
                    and pdf.name.startswith("question_")
                ),
            ):
                rendered += 1
            else:
                skipped += 1
        print(f"{exam['dir']}: done")

    # Drop image dirs for exams that no longer exist in Split.
    live = {e["dir"] for e in exams}
    img_root = out / "img"
    for d in img_root.iterdir():
        if d.is_dir() and d.name not in live:
            shutil.rmtree(d)
            print(f"removed stale {d.name}")

    total_mb = sum(f.stat().st_size for f in out.rglob("*") if f.is_file()) / 2**20
    print(f"\n{rendered} rendered, {skipped} up to date; site size {total_mb:.0f} MB -> {out}")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", default="Split", help="directory of split exams")
    parser.add_argument("--out", default="site", help="output directory")
    parser.add_argument("--dpi", type=int, default=130)
    parser.add_argument("--title", default="Electrical Circuits")
    parser.add_argument("--storage-key", default="study-progress")
    parser.add_argument(
        "--neutralize-question-colors",
        action="store_true",
        help="render question images without colored answer cues",
    )
    args = parser.parse_args()
    build(
        Path(args.root).resolve(),
        Path(args.out).resolve(),
        args.dpi,
        title=args.title,
        storage_key=args.storage_key,
        neutralize_question_colors=args.neutralize_question_colors,
    )


if __name__ == "__main__":
    main()
