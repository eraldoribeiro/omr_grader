# Grading an exam with omr_grader

This guide follows one exam from the answer key to the Canvas gradebook, with the
commands, what they print, and what to do with each kind of flagged sheet. The
[README](../README.md) is the reference for every option.

Each step is shown on the command line and in the window (`omr-grader-gui`). Both run
the same grader, and the window shows the equivalent command under "Grader output".
The examples use `omr-grader`, the installed command; in a clone of the repository,
type `python omr_grader.py` instead.

Contents:

1. [Set up a folder for the exam](#1-set-up-a-folder-for-the-exam)
2. [Before the exam: the key and the sheets](#2-before-the-exam-the-key-and-the-sheets)
3. [Scan the sheets](#3-scan-the-sheets)
4. [Grade](#4-grade)
5. [Read the results](#5-read-the-results)
6. [Flags and what to do about them](#6-flags-and-what-to-do-about-them)
7. [Fix and grade again](#7-fix-and-grade-again)
8. [Import into Canvas](#8-import-into-canvas)
9. [Practice on test sheets](#9-practice-on-test-sheets)

## 1. Set up a folder for the exam

Keep everything for one exam in one folder on your own computer:

```text
exam2/
  exam2.tex        the exam (LaTeX, exam class)
  exam2.aux        written by LaTeX; used to check the question count
  gradebook.csv    Canvas export (Grades > Export > CSV), for step 4
  scans.pdf        the scanned sheets, after the exam
```

The scans and the gradebook hold student names and IDs, so keep this folder off shared
drives and out of git. The grader never sends anything over the network, and the window
is served only to your own computer.

## 2. Before the exam: the key and the sheets

### The key from the LaTeX

The grader reads the correct answers from the exam source. Questions are numbered in
document order and the answer is the choice marked `\correctchoice` (or
`\CorrectChoice`):

```latex
\question Which system call creates a new process?
\begin{choices}
  \choice exec
  \correctchoice fork
  \choice wait
  \choice exit
\end{choices}
```

Choose a 3-digit key ID for this exam (students bubble it in the Key field; here 322)
and make the key and both printable sheets in one step:

```bash
cd exam2
omr-grader --key exam2.tex --key-id 322 --exam-name "Exam 2" \
    --course "CSE4001 Fall 2026" --key-sheet key_sheet.pdf \
    --student-sheet student_sheet.pdf --out .
```

```text
grading 78 questions; canceled questions are left out of the total
  key 322: canceled none
wrote key_sheet.pdf
wrote student_sheet.pdf
wrote key.csv
```

- `key.csv` is the key in plain text (`322,BCADCCCCAD...`). The grading step uses it.
- `key_sheet.pdf` is the answer sheet with the key bubbled in. Look it over against the
  exam: a wrong `\correctchoice` shows up here before it costs anyone points.
- `student_sheet.pdf` is the blank sheet to print, with the exam name, course and key
  ID filled in.

Warnings to look for in the output:

- `found 78 questions but exam2.aux says 80`: a question was skipped or counted twice,
  often one inside a comment or a code listing. Compile the LaTeX again and check.
- A question with no correct choice, or more than one, is canceled with a warning and
  shows as `canceled [n]`.

In the window: set "Exam folder" at the top to the `exam2` folder (in Finder,
Option-Command-C copies a folder's path). Then, in the **Prepare exam** tab, add
`exam2.tex` and `exam2.aux`, type the key ID, exam name and course, keep both sheet
boxes ticked, and click "Make key and sheets". The key and sheets are written to the
exam folder, and the key appears ten questions per line for checking.

### Several versions

Give each version its own key ID:

```bash
omr-grader --key 322=exam2_a.tex --key 117=exam2_b.tex --exam-name "Exam 2" \
    --course "CSE4001 Fall 2026" --key-sheet key_sheet.pdf \
    --student-sheet student_sheet.pdf --out .
```

Each PDF then has one page per version, and `key.csv` one line per version. When
grading, each sheet is graded with the key whose ID the student bubbled.

### Without LaTeX

- **A key CSV written by hand**, one line per version; `X` cancels a question:

  ```text
  version,answers
  322,BCADCCCCADBBCBXC...
  ```

- **A filled key sheet**: bubble the answers and the Key ID on a blank sheet, scan it,
  and give the scan as the key (`--key key_scan.pdf`). Leave a question blank or fill
  all four bubbles to cancel it.

## 3. Scan the sheets

- Put all sheets in one PDF, one sheet per page (several PDFs also work).
- 200 to 300 dpi, grayscale or black and white.
- Order and orientation don't matter: crooked and upside-down pages are straightened.
  The six black corner squares must be on the page, so don't crop the edges.
- Scan the sheets as they are. Students who wrote their ID in the boxes without
  bubbling it are handled in step 6.

## 4. Grade

```bash
omr-grader scans.pdf --key key.csv \
    --canvas gradebook.csv --canvas-assignment "Exam 2"
```

The results go to a `graded` folder next to the scans, here `exam2/graded`; `--out`
picks another folder. Leave out the last line to grade without the gradebook. The output (from the test sheets
in section 9):

```text
gradebook: 13 students; column 'Exam 2 (5805405)' (100 points)
  warning: Foxtrot, Fay, Golf, Gus share ID digits 3040; their sheets cannot be matched automatically
grading 40 questions; canceled questions are left out of the total
  key 322: canceled none
  key 117: canceled none
students.pdf#p1: ID 2869  Alpha, Ann  v322  31/40  77.5%
students.pdf#p2: ID 0621  Bravo, Bo  v117  28/40  70.0%  [blank: [5]]
students.pdf#p3: ID 4924  Charlie, Cy  v322  29/40  72.5%  [multiple: [7]]
students.pdf#p4: ID 3448  Delta, Di  v117  30/40  75.0%
students.pdf#p5: ID 8213  Echo, Ed  v322  28/40  70.0%
students.pdf#p6: ID 3040  v117  34/40  85.0%  [ID 3040 belongs to 2 students (Foxtrot, Fay; Golf, Gus); enter this grade by hand]
canvas: 5 grades in graded/canvas_import.csv; 1 sheet(s) not matched, 0 student(s) with several sheets (see flags); 8 student(s) without a sheet (graded/absent.csv)
wrote graded/results.csv, graded/item_analysis.csv, graded/review.pdf
```

Read the first lines before anything else:

- The gradebook line names the column that will be filled and its points. If the
  column name is wrong, the grader stops and lists the assignments it found.
- A `share ID digits` warning means two students in the class have the same last four
  digits. Their sheets are never matched automatically; enter those grades by hand.
- The key lines say how many questions are graded and which are canceled.

In the window: **Grade** tab, with the same exam folder at the top. Add `scans.pdf`,
`key.csv` and `gradebook.csv`, pick "Exam 2" in the column dropdown (it lists the
gradebook's assignments), and click "Grade". The results go to `exam2/graded`. The
summary shows the mean, median and range, then the sheets to check and all sheets.

## 5. Read the results

Everything is written to `graded/` in the exam folder.

### results.csv

One row per page:

```text
page,student_id,name,sis_user_id,version,score,total,percent,answers,flags
students.pdf#p1,2869,"Alpha, Ann",900002869,322,31,40,77.5,DCCBBAAABDCDCC...,
students.pdf#p2,0621,"Bravo, Bo",900010621,117,28,40,70.0,BAAB-BDCBCABBD...,blank: [5]
students.pdf#p3,4924,"Charlie, Cy",900024924,322,29,40,72.5,DBCBBA*CBCCADC...,multiple: [7]
```

- `answers` is what was read: a letter, `-` for blank, `*` for several marks.
- `total` is the number of questions that count; canceled questions are left out
  unless you grade with `--canceled credit`.
- `name` and `sis_user_id` are there only with `--canvas`.
- `flags` is empty when there is nothing to check. Section 6 lists every flag.

Open it in Excel or Numbers and sort by `flags` to see the sheets to check together.

### review.pdf

One page per sheet, straightened, in the same order as `results.csv`:

![A page of review.pdf](review_example.jpg)

- Green circle: the correct answer (filled in when the student chose it).
- Red cross: the student's wrong choice.
- `BLANK` and `MULTI` in orange, left of the question number.
- A line through a question: canceled (Q12 here).
- At the bottom of the page: ID, name, version and score, as in the console output.
- When the ID came from handwriting or the gradebook had to repair it, the enlarged ID
  boxes are pasted at the bottom so you can read them yourself.

Look at every page that has a flag. The other pages are worth a quick scroll the first
time you use the grader on a new scanner.

### item_analysis.csv

The percentage of students who got each question right, per version:

```text
version,question,key,percent_correct
322,1,D,66.7
322,2,C,66.7
322,3,C,100.0
```

A question that almost nobody got right is worth a second look: the key may be wrong,
or the question may be ambiguous. Section 7 shows how to cancel it.

### canvas_import.csv and absent.csv

With `--canvas`, `canvas_import.csv` is the file you import (section 8). It has one row
per student whose sheet was matched, with the score scaled to the assignment's points:
31/40 on a 100-point assignment becomes 77.50.

`absent.csv` lists the students in the gradebook with no sheet: absent, or a sheet that
could not be matched. Compare it with the sheets flagged as not matched.

## 6. Flags and what to do about them

Answers:

| Flag | Meaning | What to do |
| --- | --- | --- |
| `blank: [5]` | No bubble marked for question 5. Counted wrong. | Nothing, unless `review.pdf` shows a mark (see the next row). |
| `faint mark, check: [5]` | Question 5 read as blank, but one bubble is darker than an empty one. | Look at the page. If it is a light answer, grade again with `--mark-threshold 0.28`, or adjust that score by hand. |
| `multiple: [7]` | Two or more bubbles marked for question 7. Counted wrong. | Look at the page. A poorly erased answer reads as a second mark; if the student's choice is clear, adjust the score by hand. |

Student ID:

| Flag | Meaning | What to do |
| --- | --- | --- |
| `check student ID` | Some ID digits could not be read (shown as `?`). Without a gradebook only. | Read the ID on the page. With `--canvas`, partial IDs are completed for you. |
| `marks left of the 4 ID columns (...)` | Bubbles marked outside the four ID columns. | Check that the ID read is the student's. |
| `ID from handwriting, check: bubbles 31?7, boxes 3157` | A column had no bubble, so the digit written in the box was used. | Compare with the enlarged boxes at the bottom of the page. |
| `handwritten ID unclear: col 3 looks like 5 (72%)` | A written digit could not be read with confidence; it stays `?`. | Read the boxes yourself. |
| `handwritten ID 3157 differs from bubbles 3167` | The boxes and the bubbles disagree. The bubbles are used. | Check which one the student meant. |

Gradebook (with `--canvas`):

| Flag | Meaning | What to do |
| --- | --- | --- |
| `ID 31?7 completed from gradebook as 3157 (Name), check` | Only one student fits the readable digits. Exported. | Check the name against the sheet. |
| `bubbled ID 3167 not in gradebook; used handwritten 3157 (Name), check` | The bubbles match nobody, the written boxes match one student. Exported. | Check the name against the sheet. |
| `ID 3167 not in gradebook` | Nobody has these last digits. Not exported. | Find the student (usually a mis-bubbled digit) and enter the grade by hand. |
| `ID 3040 belongs to 2 students (...); enter this grade by hand` | Two students share these digits. Not exported. | Read the name on the sheet and enter the grade by hand. |
| `ID 4?7? matches 3 students in gradebook` | Too few digits readable to tell. Not exported. | Read the ID or name on the sheet and enter the grade by hand. |
| `no readable student ID` | No ID digits at all. Not exported. | Read the name on the sheet and enter the grade by hand. |
| `same student as students.pdf#p9; not exported, enter by hand` | Two sheets matched the same student, for example a re-scanned page. | Keep the right one and enter that grade by hand. |

Version and page:

| Flag | Meaning | What to do |
| --- | --- | --- |
| `version 3?2 unreadable, used 322` | The Key bubbles could not be read and there is only one key, so it was used. | Nothing, if there is only one version. |
| `unknown version` | The Key bubbles name a version with no key. The sheet is skipped. | Read the version on the page. Add its key, or grade the sheet on its own (section 7). |
| `unreadable: ...` | The page could not be lined up, usually because a corner square is missing or covered. | Re-scan the page without cropping. |

## 7. Fix and grade again

Grading takes a few seconds, and running the same command again replaces everything in
`graded/`. So the usual way to fix something is to change the input and grade
again, not to edit the results.

**Cancel a question** (after the item analysis, or a mistake found in class):

```bash
omr-grader scans.pdf --key key.csv \
    --canvas gradebook.csv --canvas-assignment "Exam 2" \
    --cancel 12 --canceled credit
```

`--canceled credit` gives everyone the point; without it the question is left out of the
total. To cancel in one version only: `--cancel 322:12`. In the window: the "Cancel
questions" box and the "Canceled questions" choice.

**Fix a wrong answer in the key**: correct the `\correctchoice` in the LaTeX and make the
key again (section 2), or edit the letter in `key.csv`, then grade again.

**Light pencil marks**: grade again with `--mark-threshold 0.28` (window: "More options").
Empty bubbles read about 0.02 and erased marks about 0.2, so stay above 0.22.

**A version bubbled wrong or not at all**: a key whose version is `any` grades every
sheet, whatever is bubbled. Scan that sheet into its own PDF, copy the right line of
`key.csv` into a new file with the version changed to `any`, and grade the sheet on
its own:

```text
version,answers
any,BCADCCCCADBBCBXC...
```

```bash
omr-grader late_sheet.pdf --key key_any.csv --out graded_late
```

Give it its own `--out`: by default it would write to `graded/` and replace the results
of the other sheets.

(A `.tex` key given without a key ID does the same.) When everyone took the same version,
grade all the sheets this way.

**One student's score**: for a single sheet that needs a judgment call (a smudge, a
clear answer with a poor erasure), enter the corrected score in Canvas after the import.

## 8. Import into Canvas

1. In Canvas, open **Grades > Import** and choose `graded/canvas_import.csv`.
2. Canvas shows the changes before saving. Check that the column is the right
   assignment and the number of changed grades matches the `canvas:` line of the
   output.
3. Save. Then enter by hand the grades for the sheets flagged "Not exported" in
   section 6.

Students not in the import file keep their current grade, so importing never erases
grades you entered by hand. After grading again (section 7), import the new file the same
way; it replaces the grades of the students it contains.

## 9. Practice on test sheets

The repository can make six synthetic sheets with known answers, so you can try every
step before the real exam. From a clone of the repository:

```bash
python make_test_sheets.py test
omr-grader test/students.pdf --key test/keys.csv
```

`test/truth.csv` holds the answers and scores that were drawn on the sheets;
`test/graded/results.csv` should match it. The pages are skewed and noisy and include
a blank (sheet 2, Q5), a double mark (sheet 3, Q7), an erased-and-changed answer
(sheet 4, Q9) and an upside-down page (5). `test/key_sheet.pdf` is a filled key sheet
for trying `--key key_sheet.pdf`.

To try the gradebook steps, export the gradebook of a course you teach and add rows whose
SIS User IDs end in the test IDs (2869, 0621, 4924, 3448, 8213, 3040). Do this in a copy
of the file, not in Canvas.
