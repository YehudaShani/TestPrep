"""Local study viewer for split exams.

Serves a single-page app over the Split directory: pick an exam, pick a
question, and toggle between the question and its worked answer.

    python -m pdf_splitter.study_server [--root Split] [--port 8765]
"""

from __future__ import annotations

import argparse
import json
import re
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

import fitz

RENDER_DPI = 130
YEAR_RE = re.compile(r"(20\d\d)")
SEASON_RE = re.compile(r"spring|winter", re.IGNORECASE)
MOED_RE = re.compile(r"moed[_ ]?([ab])", re.IGNORECASE)
SAFE_NAME_RE = re.compile(r"^[\w\-. ]+$")


def exam_label(dirname: str) -> str:
    year = YEAR_RE.search(dirname)
    season = SEASON_RE.search(dirname)
    moed = MOED_RE.search(dirname)
    if year and season and moed:
        return f"{year.group(1)} {season.group(0).title()} — Moed {moed.group(1).upper()}"
    return dirname


def scan_exams(root: Path) -> list[dict]:
    exams = []
    for d in sorted(p for p in root.iterdir() if p.is_dir()):
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
                "label": exam_label(d.name),
                "questions": questions,
                "answers": sorted(answers),
            }
        )
    # Newest first, winter before spring within a year (winter moed is earlier).
    def sort_key(e: dict) -> tuple:
        year = YEAR_RE.search(e["dir"])
        season = SEASON_RE.search(e["dir"])
        return (
            -int(year.group(1)) if year else 0,
            0 if season and season.group(0).lower() == "winter" else 1,
            e["dir"],
        )

    exams.sort(key=sort_key)
    return exams


class StudyHandler(BaseHTTPRequestHandler):
    root: Path
    progress_path: Path
    progress: dict[str, dict] = {}
    progress_lock = threading.Lock()
    render_cache: dict[str, bytes] = {}

    def do_GET(self) -> None:  # noqa: N802 (http.server API)
        parsed = urlparse(self.path)
        if parsed.path == "/":
            self._serve_page()
        elif parsed.path == "/api/exams":
            self._send(200, "application/json", json.dumps(scan_exams(self.root)).encode())
        elif parsed.path == "/api/progress":
            with self.progress_lock:
                body = json.dumps(self.progress).encode()
            self._send(200, "application/json", body)
        elif parsed.path == "/api/render":
            self._serve_render(parse_qs(parsed.query))
        else:
            self._send(404, "text/plain", b"not found")

    def do_POST(self) -> None:  # noqa: N802 (http.server API)
        if urlparse(self.path).path != "/api/progress":
            self._send(404, "text/plain", b"not found")
            return
        try:
            length = int(self.headers.get("Content-Length", 0))
            data = json.loads(self.rfile.read(length))
            exam = data["exam"]
            n = str(int(data["n"]))
            done = bool(data.get("done"))
            flagged = bool(data.get("flagged"))
            if not SAFE_NAME_RE.match(exam):
                raise ValueError(exam)
        except (KeyError, TypeError, ValueError, json.JSONDecodeError):
            self._send(400, "text/plain", b"bad request")
            return
        with self.progress_lock:
            marks = self.progress.setdefault(exam, {})
            if done or flagged:
                marks[n] = {"done": done, "flagged": flagged}
            else:
                marks.pop(n, None)
                if not marks:
                    self.progress.pop(exam, None)
            tmp = self.progress_path.with_suffix(".json.tmp")
            tmp.write_text(
                json.dumps(self.progress, indent=1, sort_keys=True), encoding="utf-8"
            )
            tmp.replace(self.progress_path)
        self._send(200, "application/json", b'{"ok": true}')

    def _serve_page(self) -> None:
        page = Path(__file__).with_name("study.html").read_bytes()
        self._send(200, "text/html; charset=utf-8", page)

    def _serve_render(self, params: dict) -> None:
        exam = params.get("exam", [""])[0]
        file = params.get("file", [""])[0]
        if not (SAFE_NAME_RE.match(exam) and SAFE_NAME_RE.match(file)) or not file.endswith(".pdf"):
            self._send(400, "text/plain", b"bad request")
            return
        pdf_path = self.root / exam / file
        if not pdf_path.is_file():
            self._send(404, "text/plain", b"not found")
            return
        # mtime in the key so re-split PDFs are never served stale
        key = f"{exam}/{file}/{pdf_path.stat().st_mtime_ns}"
        png = self.render_cache.get(key)
        if png is None:
            doc = fitz.open(pdf_path)
            try:
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
    parser.add_argument("--root", default="Split", help="directory of split exams")
    parser.add_argument("--port", type=int, default=8765)
    args = parser.parse_args()

    root = Path(args.root).resolve()
    if not root.is_dir():
        raise SystemExit(f"root directory not found: {root}")

    StudyHandler.root = root
    StudyHandler.progress_path = root / "progress.json"
    if StudyHandler.progress_path.is_file():
        StudyHandler.progress = json.loads(
            StudyHandler.progress_path.read_text(encoding="utf-8")
        )
    server = ThreadingHTTPServer(("127.0.0.1", args.port), StudyHandler)
    print(f"Study viewer on http://127.0.0.1:{args.port} (root: {root})")
    server.serve_forever()


if __name__ == "__main__":
    main()
