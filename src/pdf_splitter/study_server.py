"""Local study viewer for split exams.

Serves a single-page app over one or more course directories: pick a
course, pick an exam, pick a question, and toggle between the question
and its worked answer.

    python -m pdf_splitter.study_server [--course ID[=ROOT] ...] [--port 8765]

Without --course, every course in ``courses.py`` whose directory exists
is served and the viewer shows a course picker.
"""

from __future__ import annotations

import argparse
import json
import re
from dataclasses import dataclass
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

import fitz

from .courses import SITE_TITLE, Course
from .auth_config import auth_config
from .courses import resolve as resolve_courses

RENDER_DPI = 130
CONFIG_FLAG = (
    'window.STUDY_CONFIG = {"title":"Exam Prep","courses":['
    '{"id":"circuits","title":"Electrical Circuits",'
    '"storageKey":"study-progress","assetVersion":""}]};'
)
YEAR_RE = re.compile(r"(20\d\d)")
# [ /Indexed <base> <hival> <lookup stream ref> ] — capture hival + lookup xref
INDEXED_CS_RE = re.compile(r"/Indexed\s.*?\s(\d+)\s+(\d+)\s+0\s+R\s*\]\s*$", re.DOTALL)
SEASON_RE = re.compile(r"spring|winter|summer", re.IGNORECASE)
# Within a calendar year the winter semester is examined in February, the
# spring one in June and the summer one in September, so a newest-first list
# runs summer, spring, winter. A sitting whose season is unknown goes last.
SEASON_RANK = {"Summer": 0, "Spring": 1, "Winter": 2}
UNDATED_SEASON = 3
MOED_RE = re.compile(r"moed[_ ]?([ab])", re.IGNORECASE)
SAFE_NAME_RE = re.compile(r"^[\w\-. ]+$")


def exam_label(dirname: str) -> str:
    year = YEAR_RE.search(dirname)
    season = SEASON_RE.search(dirname)
    moed = MOED_RE.search(dirname)
    if year and season and moed:
        return f"{year.group(1)} {season.group(0).title()} — Moed {moed.group(1).upper()}"
    return dirname


def sort_key(dirname: str, metadata: dict) -> tuple:
    """Newest sitting first, and within a sitting Moed A first.

    Years run backwards and so do the semesters inside them, which is the
    order the sittings were held in; the moed does not, because Moed A and
    Moed B are the same exam twice and A is the one to study first.

    Courses whose index.json records the sitting (year/season/moed) are
    ordered by that; the rest fall back to reading the directory name.
    """
    year = metadata.get("year")
    season = metadata.get("season")
    if year is None:
        match = YEAR_RE.search(dirname)
        year = int(match.group(1)) if match else 0
    if season is None:
        match = SEASON_RE.search(dirname)
        season = match.group(0).title() if match else ""
    return (
        -year,
        SEASON_RANK.get(season, UNDATED_SEASON),
        metadata.get("moed") or "",
        dirname,
    )


def scan_exams(root: Path) -> list[dict]:
    exams = []
    keys: dict[str, tuple] = {}
    for d in sorted(p for p in root.iterdir() if p.is_dir()):
        metadata = {}
        index_path = d / "index.json"
        if index_path.is_file():
            try:
                metadata = json.loads(index_path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                metadata = {}
        questions = sorted(
            int(m.group(1))
            for f in d.glob("question_*.pdf")
            if (m := re.match(r"question_(\d+)\.pdf$", f.name))
        )
        if not questions:
            continue
        answers = {
            int(m.group(1))
            for f in d.glob("answer_*.pdf")
            if (m := re.match(r"answer_(\d+)\.pdf$", f.name))
        }
        exams.append(
            {
                "dir": d.name,
                "label": metadata.get("label") or exam_label(d.name),
                "course": metadata.get("course"),
                "questions": questions,
                "answers": sorted(answers),
            }
        )
        keys[d.name] = sort_key(d.name, metadata)

    exams.sort(key=lambda e: keys[e["dir"]])
    return exams


def expand_indexed_images(doc: fitz.Document) -> None:
    """Rewrite Indexed-colorspace images in place as plain RGB.

    MuPDF misrenders the palette of Word-produced indexed images in some
    2019-2021 Sol PDFs, painting solid black boxes over circuit labels.
    Expanding the palette ourselves is lossless and sidesteps the bug.
    """
    for page in doc:
        for img in page.get_images(full=True):
            xref, bpc, cs = img[0], img[4], img[5]
            if cs != "Indexed" or bpc != 8:
                continue
            cs_val = doc.xref_get_key(xref, "ColorSpace")[1]
            m = INDEXED_CS_RE.search(cs_val)
            if not m:
                continue
            hival, lookup_xref = int(m.group(1)), int(m.group(2))
            palette = doc.xref_stream(lookup_xref)
            data = doc.xref_stream(xref)  # filters decoded -> palette indices
            w = int(doc.xref_get_key(xref, "Width")[1])
            h = int(doc.xref_get_key(xref, "Height")[1])
            # exactly 3 bytes per entry => an RGB-family base; skip anything else
            if len(palette) != 3 * (hival + 1) or len(data) != w * h:
                continue
            rgb = bytes(c for i in data for c in palette[3 * i : 3 * i + 3])
            doc.update_stream(xref, rgb)
            doc.xref_set_key(xref, "ColorSpace", "/DeviceRGB")
            doc.xref_set_key(xref, "Decode", "null")


@dataclass
class ServedCourse:
    """One course directory the server exposes."""

    course: Course
    root: Path


class StudyHandler(BaseHTTPRequestHandler):
    courses: dict[str, ServedCourse] = {}
    render_cache: dict[str, bytes] = {}

    def _course(self, course_id: str | None) -> ServedCourse | None:
        """Look up a course; no id means the first one (single-course use)."""
        if not course_id:
            return next(iter(self.courses.values()), None)
        return self.courses.get(course_id)

    def do_GET(self) -> None:  # noqa: N802 (http.server API)
        parsed = urlparse(self.path)
        params = parse_qs(parsed.query)
        if parsed.path == "/":
            self._serve_page()
            return
        served = self._course(params.get("course", [""])[0])
        if served is None:
            self._send(404, "text/plain", b"unknown course")
        elif parsed.path == "/api/exams":
            body = json.dumps(scan_exams(served.root)).encode()
            self._send(200, "application/json", body)
        elif parsed.path == "/api/render":
            self._serve_render(served, params)
        else:
            self._send(404, "text/plain", b"not found")

    def _serve_page(self) -> None:
        page = Path(__file__).with_name("study.html").read_text(encoding="utf-8")
        if CONFIG_FLAG not in page:
            self._send(500, "text/plain", b"study page configuration is missing")
            return
        config = json.dumps(
            {
                "auth": auth_config(),
                "title": (
                    self.courses[next(iter(self.courses))].course.title
                    if len(self.courses) == 1
                    else SITE_TITLE
                ),
                "courses": [
                    {
                        "id": served.course.id,
                        "title": served.course.title,
                        "storageKey": served.course.storage_key,
                        "assetVersion": "",
                    }
                    for served in self.courses.values()
                ],
            },
            ensure_ascii=False,
            separators=(",", ":"),
        )
        page = page.replace(CONFIG_FLAG, f"window.STUDY_CONFIG = {config};")
        self._send(200, "text/html; charset=utf-8", page.encode("utf-8"))

    def _serve_render(self, served: ServedCourse, params: dict) -> None:
        exam = params.get("exam", [""])[0]
        file = params.get("file", [""])[0]
        if not (SAFE_NAME_RE.match(exam) and SAFE_NAME_RE.match(file)) or not file.endswith(".pdf"):
            self._send(400, "text/plain", b"bad request")
            return
        pdf_path = served.root / exam / file
        if not pdf_path.is_file():
            self._send(404, "text/plain", b"not found")
            return
        # mtime in the key so re-split PDFs are never served stale
        key = f"{served.course.id}/{exam}/{file}/{pdf_path.stat().st_mtime_ns}"
        png = self.render_cache.get(key)
        if png is None:
            doc = fitz.open(pdf_path)
            try:
                expand_indexed_images(doc)
                pix = doc[0].get_pixmap(dpi=RENDER_DPI)
                png = pix.tobytes("png")
            finally:
                doc.close()
            if len(self.render_cache) > 200:
                self.render_cache.clear()
            self.render_cache[key] = png
        self._send(200, "image/png", png)

    def _send(self, status: int, ctype: str, body: bytes) -> None:
        self.send_response(status)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, format: str, *args) -> None:  # noqa: A002
        pass


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--course",
        action="append",
        metavar="ID[=ROOT]",
        help="course to serve (repeatable); default: all with a source dir",
    )
    parser.add_argument("--port", type=int, default=8765)
    args = parser.parse_args()

    auth_config()  # Fail early on incomplete or unsafe browser settings.
    base = Path.cwd()
    served = {}
    for course in resolve_courses(args.course, base=base):
        root = (base / course.root).resolve()
        if not root.is_dir():
            raise SystemExit(f"course directory not found: {root}")
        entry = ServedCourse(course, root)
        served[course.id] = entry

    StudyHandler.courses = served
    server = ThreadingHTTPServer(("127.0.0.1", args.port), StudyHandler)
    names = ", ".join(f"{e.course.title} ({e.root})" for e in served.values())
    print(f"Study viewer on http://127.0.0.1:{args.port} — {names}")
    server.serve_forever()


if __name__ == "__main__":
    main()
