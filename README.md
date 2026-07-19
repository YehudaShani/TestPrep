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
