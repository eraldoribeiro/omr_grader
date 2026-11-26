# omr_grader

Grades scanned 100-question bubble sheets (the `Sheet100QA4.pdf` layout in this repo).

Requires `opencv-python`, `numpy`, `pymupdf`.

Typical use for one exam:

```bash
# 1. Before the exam: the key from the LaTeX, a key sheet to check, the sheet to print
python omr_grader.py --key exam2.tex --key-id 322 --exam-name "Exam 2" \
    --course "CSE4001 Fall 2026" --key-sheet exam2/key_sheet.pdf \
    --student-sheet exam2/student_sheet.pdf --out exam2

# 2. After the exam: grade the scans and fill the Canvas gradebook column
python omr_grader.py exam2/scans.pdf --key exam2/key.csv --out exam2/graded \
    --canvas exam2/gradebook.csv --canvas-assignment "Exam 2"
```

Step 2 without `--canvas` just grades. Check the `flags` column of
`exam2/graded/results.csv` and the flagged pages of `review.pdf`, then import
`exam2/graded/canvas_import.csv` in Canvas (Grades > Import).

To use a form in the browser instead of the command line, see [Local window](#local-window).

- **Scans**: PDFs (one sheet per page) or images. Pages may be skewed or upside down.
- **Key**: the exam's LaTeX file (below), a CSV with lines `version,answers`
  (e.g. `322,ABCDDCBA...`), or a filled key sheet scanned as PDF/image (one page per
  version; the Key bubbles give the version). Repeat `--key` to combine several.
- **Key from the exam LaTeX**: pass the `.tex` file itself (exam class; the answer is
  the `\correctchoice` / `\CorrectChoice`). Questions are numbered in document order,
  skipping commented lines and code listings, and the count is checked against the
  `.aux` file. A question with no correct choice, several, or one past D is canceled
  with a warning. Without a version prefix the key grades every sheet; for several
  versions use `--key 322=exam_a.tex --key 117=exam_b.tex`. To check the key before
  the exam, run with no scans: `python omr_grader.py --key exam.tex --out exam2` writes
  only `exam2/key.csv`.
- **Canceled questions**: on a key sheet, leave the question blank or fill all its bubbles.
  A blank at the very end of the key just shortens it, so pass `--num-questions` to keep
  it. In a CSV key use `X`, `-` or `*`. You can also cancel from the command line:
  `--cancel 5,12` (all versions) or `--cancel 322:5,117:9`. By default canceled questions
  are left out of the total (`--canceled drop`); `--canceled credit` gives everyone the point.
- **Student ID**: 4 digits by default (the sheet asks for the last 4 digits of the
  student number). The 4-digit sheet prints its ID columns where the old 6-column sheet
  had columns 3-6, so the digits are read from the right-hand columns. For an old
  6-column sheet use `--id-digits 6`. Marks to the left of the ID columns are flagged.
- **Handwritten ID**: when an ID column has no bubble marked, the digit written in the
  box above it is read instead, if the digit reader is at least 90% sure. Every such ID
  is flagged (`ID from handwriting, check`) and the enlarged boxes are shown at the
  bottom of its `review.pdf` page; unclear digits stay `?` with a guess in the flags, and
  handwriting that disagrees with the bubbles is flagged too. On synthetic scans, 95% of
  digits were read right, 4% left as `?` and about 1% misread, hence the flag. Turn it
  off with `--no-handwriting`.
- The 3-digit Key field on each student sheet selects which answer key grades it
  (except a `.tex` key given without a version, which grades every sheet).
- The number of questions graded is the key length unless `--num-questions` is given.

Outputs in `--out`:

- `key.csv`: the key actually used, after cancellations.
- `results.csv`: student ID, version, score, total, percent, the answers read (`-` blank,
  `*` multiple marks), and flags for unreadable ID digits, marks in unused ID columns,
  blanks, faint marks and multiple marks.
- `item_analysis.csv`: percent correct per question and version.
- `review.pdf`: each sheet aligned, correct answers circled in green, wrong choices
  crossed in red, blanks and multiple marks labelled.

A bubble counts as marked when its inner darkness is at least `MARK_T` (0.35) and at least
`RELATIVE_T` (0.6) of the darkest bubble in the same row, so light erasures are ignored.
To pick up lighter marks, lower the threshold with `--mark-threshold 0.25`. On the test
sheets empty bubbles read about 0.02, an erased mark about 0.2 and pencil marks 0.5 or more,
so going below about 0.22 risks counting an erased-and-left-empty question as answered.
A question read as blank whose darkest bubble is still above half the threshold is
flagged `faint mark, check` so you can look at it in `review.pdf`.

## Canvas gradebook

Export the gradebook from Canvas (Grades > Export > CSV) and pass it with the assignment
to fill:

```bash
python omr_grader.py scans.pdf --key exam2/key.csv --out exam2/graded \
    --canvas exam2/gradebook.csv --canvas-assignment "Exam 2"
```

- Each sheet's ID is matched to the last digits (`--id-digits`, default 4) of a
  student's **SIS User ID**, the university student number. The name and SIS User ID are
  added to `results.csv`.
- The gradebook also repairs IDs, always flagged: an ID with unreadable digits (`47?1`)
  is completed when exactly one student fits, and when the bubbled ID matches nobody but
  the handwritten one does, the handwritten one is used.
- Not exported, flagged in `results.csv`: IDs matching no student, IDs shared by two
  students in the class (also warned at startup), and students with more than one sheet.
- `canvas_import.csv`: the identity columns plus the assignment column, one row per
  matched student, score scaled to the assignment's points possible. Import it with
  Grades > Import. Students not in the file keep their current Canvas grade.
- `absent.csv`: students in the gradebook with no sheet.

The gradebook holds student names and IDs, so keep it out of the repo (the `.gitignore`
already does).

## Printable sheets

The blank template has no course, exam or key filled in, so it works for any course.
The grader can fill it in for you:

```bash
python omr_grader.py --key exam.tex --key-id 322 --exam-name "Exam 2" \
    --course "CSE4001 Fall 2026" --key-sheet key_sheet.pdf --student-sheet student_sheet.pdf
```

- `--key-sheet`: the answer key bubbled in, with "ANSWER KEY" as the name, for checking
  the key by hand or grading by hand. Canceled questions have all four bubbles filled.
  The file can be given back to the grader as `--key key_sheet.pdf`.
- `--student-sheet`: the sheet to print for students, with the exam name, course and
  key ID filled in and no answers.
- `--key-id` is the 3-digit ID bubbled in the Key field. A `.tex` key needs it (or
  `322=exam.tex`); CSV and scanned keys carry their own. Several versions give one page
  each.
- `--template` picks a different blank sheet; the default is `Sheet100QA4.pdf` here.

Without scans, only these files and `key.csv` are written.

## Local window

`gui.py` puts the same steps in a form that opens in your browser. It needs Gradio as
well (`pip install gradio`, already in a standard Anaconda `base`):

```bash
python gui.py
```

- **Grade** tab: add the scans and the key (the exam `.tex`, with its `.aux` for the
  question-count check, a key CSV or a scanned key sheet), optionally the Canvas
  gradebook (its assignment columns fill the dropdown), cancellations and the output
  folder. After grading it shows a summary, the sheets to check and all sheets, with
  buttons that open `review.pdf` and the output folder.
- **Prepare exam** tab: the key, key sheet and student sheet from the LaTeX or a CSV,
  with the key shown ten questions per line for checking.
- "More options" holds the mark threshold, number of questions, ID digits and the
  handwritten-ID switch. The grader's output, starting with the equivalent command line,
  is under "Grader output".

The page is served only to this computer (`127.0.0.1`), with no share link and Gradio's
usage statistics off. Each run copies the uploads to a temporary folder deleted when the
run ends; Gradio's own copies of uploads (in the system temporary folder) are deleted after
30 minutes while it runs, and all of them the next time `gui.py` starts. Results are written to the output folder you choose.

## Digit reader

`handwriting.py` reads the ID boxes with a small CNN (two convolution layers and two
fully connected layers). PyTorch is used only to train it; the trained weights are saved
in `digits_model.npz` (about 200 KB), and the grader runs the network in NumPy: the
convolutions, max-pooling, matrix products and softmax take about fifteen lines. On the
test digits the NumPy and PyTorch outputs agree to within 1e-6.

Running the network in NumPy keeps grading free of a deep-learning library:

- PyTorch is about 330 MB installed and is not part of a standard Anaconda `base`
  environment; NumPy is already needed for the image processing.
- PyTorch builds are tied to particular Python versions and hardware, which makes it the
  install most likely to fail on another machine (a TA's laptop, another course).
- An upgrade of a large library cannot change or break how a digit is read.
- Importing PyTorch would add a second or two to every run, for four digits per sheet.

So grading needs only the three packages above, plus the weights file in this repo.

The network itself is written out in `handwriting.py`; the weights file holds only the
learned numbers:

- `_conv`: a convolution layer (each 3x3 filter slid over the image with NumPy's
  `sliding_window_view` and `einsum`, plus the bias).
- `_pool`: 2x2 max-pooling (reshape into 2x2 blocks, keep each block's maximum).
- `DigitReader` loads the weights with `np.load`; its `probs` method chains the layers
  (convolution, ReLU, pooling, twice; then two matrix products and a softmax) and returns
  the ten digit probabilities.
- `DigitReader.read` is what the grader calls: for each ID box it cuts the box out,
  cleans it, runs `probs`, and returns the likeliest digit and its confidence.

The same network is defined in PyTorch in `make_model` in `train_digits.py`, for
training. The two definitions must match layer for layer, or the saved weights would be
read into the wrong layers, so `train_digits.py` checks after saving that the NumPy
version gives the same probabilities as PyTorch.

To retrain the weights (needs PyTorch and the MNIST idx files):

```bash
python train_digits.py path/to/MNIST/raw
```

Training pastes MNIST digits into simulated scanned boxes (box lines, blur, noise,
digits too big or off-center) and runs them through the same clean-up as real boxes.

## Testing

`make_test_sheets.py TEMPLATE.pdf OUTDIR` makes 6 synthetic student sheets with known
answers (two versions, 40 questions), a filled key sheet, `keys.csv` and `truth.csv`.
The pages are skewed and noisy, and include a blank (sheet 2, Q5), a double mark
(sheet 3, Q7), an erased-and-changed answer (sheet 4, Q9) and an upside-down page (5).
The blank template, `Sheet100QA4.pdf`, is in the repo.

```bash
python make_test_sheets.py Sheet100QA4.pdf test
python omr_grader.py test/students.pdf --key test/keys.csv --out test/graded
```

`test/graded/results.csv` should match `test/truth.csv`.
