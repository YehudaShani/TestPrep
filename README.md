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
