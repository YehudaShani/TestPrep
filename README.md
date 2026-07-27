# PDF Horizontal Splitter

Desktop tool to open a PDF, click horizontal split lines on each page preview, and export one PDF per vertical strip between consecutive boundaries.

## Auto-splitter (exam PDFs)

`pdf_splitter.auto_split` detects "שאלה מספר N" / "N. (5%)" question headers in
exam PDFs and exports `question_NN.pdf` (with its shared circuit setup) and
`answer_NN.pdf` (worked solution, stitched across page breaks) plus an
`index.json` with page provenance and warnings:

```bash
python -m pdf_splitter.auto_split "Unorganized tests\2024\Sol_044105_Moed_A_Spring_2024.pdf" -o "Split\Sol_044105_Moed_A_Spring_2024"
```

Files it cannot fully parse are reported (`no question headers found` /
warnings in `index.json`); use the GUI to split those manually.

## Physics 3 collection

Physics 3 uses a different layout from the electrical-circuits exams. The
collection includes exam/solution pairs, solution-only copies, broken RTL
heading extraction, and combined pages where the worked solution follows the
multiple-choice options. The Physics 3 batch command handles those cases,
removes answer-color cues from question mode, and preserves the complete
question/answer pairing:

```bash
python -m pdf_splitter.physics3 "physics 3 tests" --out Physics3Split
```

Exam names in that collection are spelled a dozen ways (`Sp2013MoedA`,
`ExamA-Winter2016`, `Winter_moedA_2025-26`), so the preparation reads each one
into its sittings and labels it like the circuits exams: `2016 Spring — Moed B
+ Summer — Moed A`. Refreshing those labels does not need a re-split:

```bash
python -m pdf_splitter.physics3 --relabel --out Physics3Split
```

That preparation command is only needed when the source collection changes.
Normal study sessions serve the already-separated questions and answers
directly from `Physics3Split/`:

```powershell
.\serve-physics3.ps1
```

Then open <http://127.0.0.1:8765>. No splitting or site build runs when the
viewer starts. Each exam folder contains `question_NN.pdf`, `answer_NN.pdf`,
and `index.json`; `physics3-report.json` summarizes the full prepared
collection.

Use `.\serve-physics3.ps1 -Port 9000` if port 8765 is already occupied.

The generated `Physics3Split/` directory is ignored by Git because it contains
hundreds of derived PDFs.

## Courses

`src/pdf_splitter/courses.py` is the single registry of courses: an id, a
display name, the directory of split exams, and the localStorage bucket its
progress lives in. Both the viewer and the site build read it, so adding a
course means adding one entry there (plus the split-exam directory).

```bash
python -m pdf_splitter.study_server              # every prepared course
python -m pdf_splitter.study_server --course physics3
```

The viewer shows a course picker whenever more than one course is served, and
each course keeps its own exam list, progress, and deep links
(`#<course>/<exam>/<question>/<q|a>`).

## Static site

```bash
python -m pdf_splitter.build_site --out site     # every prepared course
python -m pdf_splitter.build_site --course circuits --out site
```

The build renders each course into `site/<course>/` (`exams.json` plus
`img/<exam>/*.webp`) and writes one `index.html` that carries the course list.
Pages are stored as lossless WebP — pixel-identical to the PNGs they replaced
at about a third of the bytes (~62 MB for both courses). Rendering is
incremental: a page is rebuilt only when its source PDF is newer, so adding a
course does not re-render the existing ones.

## Deploying

```powershell
.\deploy-site.ps1
```

That rebuilds the site, commits it, and force-pushes it to the `gh-pages`
branch of <https://github.com/YehudaShani/TestPrep>, which serves
<https://yehudashani.github.io/TestPrep/>.

Each deploy is a single orphan commit rather than a commit on top of the last
one. The site is a build artifact, and keeping its history would mean every
re-render of the ~2,700 pages stayed in the repository forever; squashing keeps
the clone about the size of the site. That is why the push must be forced.

Use `-SkipBuild` to deploy what is already in `site/`, and `-NoPush` to stage
the commit and inspect it before it goes out.

## Requirements

- Python 3.10+
- Windows (also works on macOS/Linux)

## Install

```bash
cd TestPreparer
python -m venv .venv
.venv\Scripts\activate
pip install -e .
```

## Run

```bash
pdf-splitter
```

Or:

```bash
python -m pdf_splitter
```

## Usage

1. Click **Open PDF** and choose a file.
2. Navigate pages with **Previous** / **Next**.
3. **Left-click** on the page preview to add a horizontal split line.
4. **Right-click** near a line to remove it, or use the side panel **Remove** / **Clear all**.
5. Click **Export…**, choose an output folder. The current page is exported into one PDF per strip (`{name}_page{NN}_part{MM}.pdf`).

Split lines are stored per page. Strips are defined from the top of the page (0) through your lines to the bottom.
