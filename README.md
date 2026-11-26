# omr_grader

Grades scanned 100-question bubble sheets (`Sheet100QA4.pdf` layout).

Requires `opencv-python`, `numpy`, `pymupdf`.

```bash
python omr_grader.py scans.pdf --key exam.tex --out graded
```

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
- **Student ID**: the first 4 ID columns are read (the sheet asks for the last 4 digits
  of the student number). Use `--id-digits 6` for all six. Marks in the unused columns
  are flagged.
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

`make_test_sheets.py TEMPLATE.pdf OUTDIR` builds synthetic filled sheets with known answers
for testing.
