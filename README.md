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

## Signals & Systems collection

Twenty years of Signals & Systems (044131) papers, in every shape the course
has used: exam and solution as separate files, a single Word-era file that
works each question out under its own "פתרון", and English LaTeX solutions
that number their questions by section heading alone.

```bash
python -m pdf_splitter.signals "signals tests" --out SignalsSplit
```

These are open questions rather than multiple choice, and each sub-section
carries its own weight, so the header search that serves the other two
collections reads sub-sections as questions. This one goes by the word
"שאלה" and by type size — every paper sets its question headings larger than
anything else that mentions a question — and takes the multiple-choice part
some years append when it follows the last full-size heading.

The command prints a line per sitting and lists the ones it could not read;
`SignalsSplit/signals-report.json` holds the same summary.

Every sitting was reviewed against its source. Eight papers cannot be cropped
from their headings at all — some carry questions and solutions under the same
`שאלה N` heading, one heads its bonus question `שאלת בונוס`, two close with a
multiple-choice part whose items are lettered rather than numbered — so their
boundaries are written out in `SEGMENT_OVERRIDES` in `signals.py`, as
`(page, y0, y1)` slices in PDF points with 0-based pages. `SEGMENT_DROPS`
removes a crop whose heading turned out to head a solution.

What remains is source-side: 2006 Spring Moed C and 2007 Winter Moed B are
scans or use a legacy Hebrew encoding and yield nothing; 2006 Spring Moed A/B
and 2019 Winter Moed A have questions but no usable solution file. Papers
split as `solution copy only` state each question and work it out with nothing
between, so their question crop carries the solution too.

## Medical Imaging collection

Fourteen sittings of Medical Imaging (046831), 2014-2025.

```bash
python -m pdf_splitter.medical "Medical Imaging Tests" --out MedicalSplit
```

These papers are divided into lettered parts, which none of the other
collections are. Part א is multiple choice and numbers its questions 1..N;
part ב is open questions and starts counting at 1 all over again; 2017 and
2018 add a part ג of guest-lecture questions that carries on from part א.
Nothing in a number alone says which part it belongs to, so the split reads
the parts first — a title above body size that says what its part asks, not
the cover page's plan of how long to spend on each — and then numbers the
questions straight through the paper.

Three shapes of answer: an exam paired with a solution copy that is the same
paper with the answers written into it (2017-2023), a single file that works
each question out under its own "פתרון" or "התשובה הינה" (2024-2025), and an
exam whose only answers are a key page at the end (2014-2016), which yields
questions and no answers. The key page is not part of the last question, and
neither is the correction page one year closes with.

The 2014 papers and the 2017 exam were typeset with a font whose digits
extract as other characters — the 2017 date reads "62...602.", and every full
stop in 2014 Moed B comes out as a "9" — so no run of question numbers can be
found in them. Those fall back to taking the numbered lines in page order,
which finds the questions but cannot tell a heading it missed from one it
invented; the command says which sittings that was, as does
`MedicalSplit/medical-report.json`. It costs the 2017 paper one question of
its fourteen and 2014 Moed B one of its twenty.

Two sittings have questions and no answers for reasons in the source: 2016 is
an exam with no solution at all, and the 2020 Moed B answers are a single page
of multiple-choice results that answers no question on its own.

## Probability collection

Fourteen sittings of Introduction to Probability (104034), 2023-2026.

```bash
python -m pdf_splitter.probability "Probability Tests" --out ProbabilitySplit
```

**These are exam papers only.** No sitting in the collection comes with a
solution, an answer key, or a marked-up copy, so every sitting yields questions
and no answers, and the viewer shows "no answer" against each one. That is a
gap in the source, not in the split.

It is also the only collection with nothing to read: nine of the fourteen
papers are photographs of the printed exam with no text layer at all, and of
the five that carry text, two extract as mojibake. The questions are therefore
found in the picture of the page.

Each paper is eight multiple-choice questions and two or three open ones. The
multiple-choice questions are read by what closes them — a row of five or six
short alternatives spread across the page, which no other line of an exam looks
like and which the layout keeps whether the paper was typeset in Word or in
LaTeX. Counting those rows counts the questions, and it also says which pages
hold them: the cover page of instructions and standard normal table has none,
and neither does the page of announcements one paper opens with. Two papers
have no cover page at all and open on question 1.

The open questions carry no alternatives and are read instead by the hanging
indent every paper sets its question numbers with: the number sits about
eighteen points further right than the text it heads. Two things get in the way
of measuring that. The phone-scanned papers are keystoned, so the right margin
drifts by as much as fifty points down a page — more than the indent itself —
and is therefore fitted as a line rather than taken as a constant. And each of
those papers carries a dark curl of the sheet's own edge in the top right corner
of the page, which is exactly where the question numbers are; where it falls
beside a line of text it lands in the same band, and that line is measured again
without it rather than thrown away, since the line it landed beside is often a
heading.

Even measured, the indent cannot tell an open question's number from the
sub-sections under it: "א." hangs about half as far as "10.", and half of
eighteen points is inside the error the keystone leaves. What can tell them
apart is the page. An open question needs a page of ruled space to be answered
in, so each one starts a fresh page, and the first indented line on a page opens
a question while the rest of that page's are its sub-sections. Two open
questions set on one page would come out as a single crop holding both, which is
the better way to be wrong: a crop of the whole question is still worth
studying, where a sub-section cut away from the question it belongs to says
nothing on its own.

Eight of the fourteen sittings come out with nothing to report against them, at
exactly the ten questions their cover page says they carry. The command says
what is wrong with the rest, as does
`ProbabilitySplit/probability-report.json`: two lose a row of alternatives to a
scan, which merges two of their questions into one; the two LaTeX-typeset
sittings set their numbers flush with the text and give the open questions no
indent to be found by at all, so those stay on the end of the last
multiple-choice question; and one carries an open question over onto a second
page, which opens that page with a sub-section and reads as a question of its
own.

## Mechatronics collection

Twenty-four sittings of Introduction to Mechatronics (034022), 2005-2021.

```bash
python -m pdf_splitter.mechatronics "Mechatronics Tests" --out MechatronicsSplit
```

The course examined two ways, and the collection holds both: multiple-choice
papers of twenty items grouped under topic titles ("שאלות 3-1 – תופעות מעבר"),
and open papers of three or four questions with lettered sub-sections. Which
one a paper is decides how it is cut, so the split reads that first: six or
more numbered items with alternatives under them means a multiple-choice paper,
and anything less means the "שאלה N" headings are the questions rather than the
titles of the parts.

Almost every multiple-choice paper here is a marked copy — the correct choice
highlighted in yellow — and that highlight is the only answer most of these
sittings have. The question crop drops it and the answer crop keeps it, the
same as the circuits Sol_ files, except that half these papers paint the
highlight into the page instead of leaving it as an annotation.

The item numbers cannot be read at all in half the multiple-choice papers:
they are typeset in fonts whose character map is wrong, so 10 extracts as ".4"
and 20 as "04". The items are found instead by what closes them — a block of
four to six lettered alternatives, which nothing else on an exam page looks
like — and numbered by their position. Reading the alternatives is most of the
work: the papers write a label six different ways ("א.", ".א", "( א5"), a
choice that says "answers ב and ג are both right" ends in something shaped
exactly like the next label, and a fraction puts its denominator on a line of
its own in the middle of the block.

Two things break the reading. A choice that says "answers ב and ג are both
right" ends in something shaped exactly like the next label, so a label out of
order is not one; and one paper letters straight through two questions on a
page, as far as ט, which is also how a scrambled "פשט" comes out at the head of
a question — so the further letters are only read with their bracket after
them.

What the reading cannot find at all is a question answered by a picture — five
graphs to choose between, or five truth tables. Those are recovered by the
space around them: a numbered line with almost no text between it and the
question after it is a question whose alternatives are a drawing.

Five papers, the 2005 and 2006 ones, are photocopies with no text layer at all.
Their questions and the handwritten solutions after them are written out in
`SEGMENT_OVERRIDES` in `mechatronics.py`, as `(page, y0, y1)` slices in PDF
points with 0-based pages, and `DRAWN_HEADINGS` does the same for two
statements of the 2007 paper that were scanned back in with its solutions.

Twenty-four sittings come out with 230 questions and 200 answers, every paper
at the number of questions it says it has. What is missing is source-side: six
sittings are exam papers with no answers of any kind, the 2005 Moed B paper is
missing its third question, and the 2005 Moed C paper the solution to its
third. The 2011 paper is a worked solution with no exam file to go with it, so
each of its question crops opens with the tail of the previous answer — the two
stand on the page with nothing between them.
`MechatronicsSplit/mechatronics-report.json` says what each sitting came out
with.

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

Exams are listed newest sitting first, which means the semesters inside a year
run backwards too — summer, then spring, then winter, the order they were
examined in. The one thing that does not run backwards is the moed: Moed A and
Moed B are the same exam twice, and A is the one to study first. A sitting
whose season the collection does not record follows the dated ones of its year.

## Static site

```bash
python -m pdf_splitter.build_site --out site     # every prepared course
python -m pdf_splitter.build_site --course circuits --out site
```

The build renders each course into `site/<course>/` (`exams.json` plus
`img/<exam>/*.webp`) and writes one `index.html` that carries the course list.
Pages are stored as lossless WebP — pixel-identical to the PNGs they replaced
at about a third of the bytes (~236 MB for the five courses). Rendering is
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

## Traffic counting

GitHub Pages keeps no logs and the repository's Insights → Traffic panel counts
views of the repository, not of the site, so visits are counted in the page
itself. Set `ANALYTICS_CODE` in `src/pdf_splitter/courses.py` to the site code
of a [GoatCounter](https://www.goatcounter.com/) account (the `<code>` in
`<code>.goatcounter.com`) and deploy; leaving it empty disables counting.

Only `build_site.py` passes the code to the page, so the local study server
never reports anything and the dashboard stays free of your own testing.
GoatCounter sets no cookies and stores no personal data, which is why the site
needs no consent banner.

Two things are counted: the visit itself, and the exam a visitor opens
(`/TestPrep/<course>/<exam-dir>`, once per exam per visit — moving between
questions inside an exam is not reported). Ad blockers suppress some share of
the hits, so read the numbers as trends rather than as a headcount.

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
