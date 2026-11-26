"""Grade scanned 100-question bubble sheets (Sheet100QA4 layout).

Usage:
    omr-grader SCANS... --key KEY [--out DIR] [--num-questions N]

SCANS are PDFs (one sheet per page) or image files. KEY is either
  - a CSV/text file with lines "version,answers", e.g. "322,ABCDDCBA...",
  - a scanned/filled key sheet (PDF or image); its Key bubbles give the version, or
  - the exam's LaTeX file (exam class, \\correctchoice marks the answer). Give it
    as 322=exam.tex to tie it to a version; without one it grades every sheet.
Repeat --key for several versions.

Outputs in DIR (default: graded/ next to the first scan): key.csv, results.csv,
item_analysis.csv, review.pdf. With no SCANS, only key.csv is written (default ./graded).
"""

import argparse
import csv
import re
import sys
from collections import defaultdict
from pathlib import Path

import cv2
import pymupdf as fitz
import numpy as np

from . import canvas
from . import handwriting as hw

# ---------------------------------------------------------------------------
# Sheet layout, in pixels of the blank template rendered at 200 dpi.
# Measured from Sheet100QA4.pdf by detecting the printed circles.
# ---------------------------------------------------------------------------
DPI = 200
PAGE_W, PAGE_H = 1654, 2339
BUBBLE_R = 14.5

# Centers of the six solid alignment squares (corners and mid-sides).
FIDUCIALS = np.array([
    [105.5, 180.5], [1547.5, 180.5],
    [105.5, 1169.5], [1547.5, 1169.5],
    [105.5, 2158.5], [1547.5, 2158.5],
], dtype=np.float32)

CHOICES = "ABCD"
ANS_X = [  # four column groups, choice A..D in each
    [410.0, 454.5, 499.0, 543.5],
    [643.5, 688.0, 732.5, 777.0],
    [877.0, 921.5, 965.5, 1010.0],
    [1110.0, 1154.5, 1199.0, 1243.5],
]
ANS_Y = [  # 25 rows, in five blocks of five
    854.0, 893.0, 931.5, 970.5, 1009.5,
    1093.0, 1131.5, 1170.5, 1209.5, 1248.5,
    1331.5, 1370.5, 1409.5, 1448.5, 1487.5,
    1570.5, 1609.5, 1648.5, 1687.5, 1726.0,
    1809.5, 1848.5, 1887.5, 1926.0, 1965.0,
]
DIGIT_Y = [289.5, 328.5, 367.5, 406.5, 445.0, 484.0, 523.0, 562.0, 600.5, 639.5]
ID_X = [941.0, 985.5, 1030.0, 1074.5, 1119.0, 1163.5]
KEY_X = [1252.5, 1296.5, 1341.0]

# Timing marks run down the left edge only; used to detect upside-down scans.
TIMING_STRIP = (85, 125, 1420, 1900)  # x0, x1, y0, y1

# Bubble decision thresholds on mean darkness (0 = paper, 1 = black).
MARK_T = 0.35        # a bubble at least this dark counts as marked
RELATIVE_T = 0.6     # among marked bubbles, keep those >= 0.6 x the darkest


def question_center(q, c):
    """Template center of choice c (0..3) for question q (1..100)."""
    col, row = divmod(q - 1, 25)
    return ANS_X[col][c], ANS_Y[row]


# ---------------------------------------------------------------------------
# Loading
# ---------------------------------------------------------------------------
def load_pages(path):
    """Yield (label, grayscale image) for every page/image in path."""
    path = Path(path)
    if path.suffix.lower() == ".pdf":
        doc = fitz.open(path)
        for i, page in enumerate(doc):
            pix = page.get_pixmap(dpi=DPI, colorspace=fitz.csGRAY)
            img = np.frombuffer(pix.samples, np.uint8).reshape(pix.h, pix.w)
            yield f"{path.name}#p{i + 1}", img.copy()
    else:
        img = cv2.imread(str(path), cv2.IMREAD_GRAYSCALE)
        if img is None:
            raise ValueError(f"cannot read image {path}")
        yield path.name, img


# ---------------------------------------------------------------------------
# Alignment
# ---------------------------------------------------------------------------
def find_squares(img):
    """Centers of solid squares about the size of the alignment marks."""
    h, w = img.shape
    blur = cv2.GaussianBlur(img, (5, 5), 0)
    _, bw = cv2.threshold(blur, 0, 255, cv2.THRESH_BINARY_INV + cv2.THRESH_OTSU)
    cnts, _ = cv2.findContours(bw, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    side = 45.0 / PAGE_W * w  # expected side length at this scan's scale
    found = []
    for c in cnts:
        x, y, bw_, bh = cv2.boundingRect(c)
        if not (0.75 * side < bw_ < 1.5 * side and 0.75 * side < bh < 1.5 * side):
            continue
        rect = cv2.minAreaRect(c)
        (cx, cy), (rw, rh), _ = rect
        if min(rw, rh) == 0 or max(rw, rh) / min(rw, rh) > 1.3:
            continue
        if cv2.contourArea(c) / (rw * rh) < 0.85:  # solid, not a circle or box
            continue
        found.append((cx, cy))
    return np.array(found, dtype=np.float32)


def align(img):
    """Warp a scan onto the template frame. Returns the warped image."""
    pts = find_squares(img)
    if len(pts) < 4:
        raise RuntimeError(f"found only {len(pts)} alignment squares")
    h, w = img.shape
    # The four corner squares are the candidates nearest the image corners.
    corners = []
    for cx, cy in [(0, 0), (w, 0), (0, h), (w, h)]:
        d = np.hypot(pts[:, 0] - cx, pts[:, 1] - cy)
        corners.append(pts[np.argmin(d)])
    corners = np.array(corners)
    if len({tuple(c) for c in corners}) < 4:
        raise RuntimeError("could not identify the four corner squares")
    tmpl = FIDUCIALS[[0, 1, 4, 5]]
    H = cv2.getPerspectiveTransform(corners, tmpl)

    # Refine with the mid-side squares when they are found where expected.
    mapped = cv2.perspectiveTransform(pts[None], H)[0]
    src, dst = [], []
    for t in FIDUCIALS:
        d = np.hypot(*(mapped - t).T)
        i = np.argmin(d)
        if d[i] < 30:
            src.append(pts[i])
            dst.append(t)
    if len(src) >= 5:
        H, _ = cv2.findHomography(np.array(src), np.array(dst))

    warped = cv2.warpPerspective(img, H, (PAGE_W, PAGE_H),
                                 borderValue=255, flags=cv2.INTER_AREA)
    if is_upside_down(warped):
        warped = cv2.rotate(warped, cv2.ROTATE_180)
    return warped


def is_upside_down(warped):
    x0, x1, y0, y1 = TIMING_STRIP
    left = warped[y0:y1, x0:x1].mean()
    right = warped[PAGE_H - y1:PAGE_H - y0, PAGE_W - x1:PAGE_W - x0].mean()
    return right < left - 20


# ---------------------------------------------------------------------------
# Reading bubbles
# ---------------------------------------------------------------------------
_yy, _xx = np.mgrid[-15:16, -15:16]
_INNER = (_xx ** 2 + _yy ** 2) <= (BUBBLE_R - 4.5) ** 2  # skip printed outline


def darkness(norm, x, y):
    """Mean darkness inside the bubble centered at template (x, y)."""
    cx, cy = int(round(x)), int(round(y))
    patch = norm[cy - 15:cy + 16, cx - 15:cx + 16]
    return float(patch[_INNER].mean())


def normalize(warped):
    """Map to darkness in [0, 1] relative to the paper's white level."""
    white = np.percentile(warped, 90)
    return np.clip(1.0 - warped.astype(np.float32) / max(white, 1), 0, 1)


def pick(scores):
    """Indices of the marked bubbles in a row/column of darkness scores."""
    scores = np.asarray(scores)
    top = scores.max()
    if top < MARK_T:
        return []
    return [i for i, s in enumerate(scores) if s >= MARK_T and s >= RELATIVE_T * top]


def read_digits(norm, xs):
    """Read a column-per-digit grid; '?' for blank or ambiguous columns."""
    out = ""
    for x in xs:
        sel = pick([darkness(norm, x, y) for y in DIGIT_Y])
        out += str(sel[0]) if len(sel) == 1 else "?"
    return out


def read_sheet(img, num_questions=100):
    warped = align(img)
    norm = normalize(warped)
    answers, top = [], []
    for q in range(1, num_questions + 1):
        scores = [darkness(norm, *question_center(q, c)) for c in range(4)]
        top.append(max(scores))
        sel = pick(scores)
        if not sel:
            answers.append("-")      # blank
        elif len(sel) > 1:
            answers.append("*")      # multiple marks
        else:
            answers.append(CHOICES[sel[0]])
    return {
        "student_id": read_digits(norm, ID_X),
        "version": read_digits(norm, KEY_X),
        "answers": "".join(answers),
        "top": top,  # darkest bubble per question
        "warped": warped,
    }


# ---------------------------------------------------------------------------
# Answer keys
# ---------------------------------------------------------------------------
CANCELED = "X"  # key character for a canceled question
ANY = "any"      # version of a key that grades every sheet (LaTeX key, no version given)

_TEX_TOKEN = re.compile(
    r"\\(?P<q>question|titledquestion|bonusquestion|bonustitledquestion)(?![A-Za-z])"
    r"|\\(?P<c>choice|correctchoice|CorrectChoice)(?![A-Za-z])"
    r"|\\(?P<be>begin|end)\{(?P<env>choices|oneparchoices|checkboxes|oneparcheckboxes)\}"
)
_TEX_VERBATIM = re.compile(
    r"\\begin\{(lstlisting|verbatim|Verbatim|minted|comment)\}.*?\\end\{\1\}", re.S)


def parse_tex_key(path):
    """Read the answer key from an exam-class LaTeX file.

    Questions are numbered in document order, which matches the PDF when the
    question counter carries over between questions environments. Commented
    lines and code listings are ignored. Returns (answers, warnings); a question
    with no correct choice, several, or a choice past D is canceled.
    """
    text = Path(path).read_text(encoding="utf-8", errors="replace")
    text = "\n".join(re.sub(r"(?<!\\)%.*", "", line) for line in text.splitlines())
    text = _TEX_VERBATIM.sub("", text)
    start = text.find(r"\begin{document}")
    text = text[start:] if start >= 0 else text

    questions = []   # per question: list of choice flags (True = correct)
    in_choices = False
    for m in _TEX_TOKEN.finditer(text):
        if m["q"]:
            questions.append([])
            in_choices = False
        elif m["be"]:
            in_choices = m["be"] == "begin"
        elif m["c"] and in_choices and questions:
            questions[-1].append(m["c"] != "choice")

    answers, warnings = [], []
    for q, flags in enumerate(questions, start=1):
        right = [i for i, f in enumerate(flags) if f]
        if len(right) != 1:
            warnings.append(f"Q{q}: {'no' if not right else len(right)} correct choices, canceled")
            answers.append(CANCELED)
        elif right[0] >= len(CHOICES):
            warnings.append(f"Q{q}: correct choice is #{right[0] + 1}, sheet has only "
                            f"{CHOICES[0]}-{CHOICES[-1]}; canceled")
            answers.append(CANCELED)
        else:
            if len(flags) > len(CHOICES):
                warnings.append(f"Q{q}: {len(flags)} choices, sheet has only {len(CHOICES)}")
            answers.append(CHOICES[right[0]])

    # Cross-check against the question count LaTeX recorded when it compiled.
    aux = Path(path).with_suffix(".aux")
    if aux.exists():
        m = re.search(r"\\gdef\\exam@numquestions\{(\d+)\}", aux.read_text(errors="replace"))
        if m and int(m[1]) != len(answers):
            warnings.append(f"found {len(answers)} questions but {aux.name} says {m[1]}; "
                            f"check the numbering (or recompile)")
    return "".join(answers), warnings


def load_keys(spec):
    """Return {version: answer string}; canceled questions are 'X'.

    spec is a path, optionally prefixed with a version: '322=exam.tex'.
    On a key sheet, a question left blank or with several bubbles filled is
    canceled. Trailing blanks end the key, so a canceled question at the very
    end needs to be all filled, or use --num-questions / --cancel.
    In a CSV key, 'X', '-' or '*' cancels a question.
    A .tex key is read from the exam's \\correctchoice marks; without a version
    prefix it grades every sheet whatever its Key bubbles say.
    """
    version, sep, path = spec.partition("=")
    if not sep or not version.isdigit():
        version, path = None, spec
    path = Path(path)
    keys = {}
    if path.suffix.lower() == ".tex":
        ans, warnings = parse_tex_key(path)
        for w in warnings:
            print(f"warning: {path.name}: {w}")
        keys[version or ANY] = ans
    elif path.suffix.lower() in {".pdf", ".png", ".jpg", ".jpeg", ".tif", ".tiff"}:
        for label, img in load_pages(path):
            sheet = read_sheet(img)
            if "?" in sheet["version"]:
                raise ValueError(f"{label}: cannot read the key version")
            ans = sheet["answers"].rstrip("-")
            keys[sheet["version"]] = "".join(a if a in CHOICES else CANCELED for a in ans)
    else:
        with open(path, newline="") as f:
            for row in csv.reader(f):
                if not row or row[0].strip().startswith("#"):
                    continue
                version = row[0].strip()
                if version.lower() == "version":
                    continue
                ans = "".join(row[1:]).replace(" ", "").upper()
                keys[version] = "".join(CANCELED if a in "-*" else a for a in ans)
    if not keys:
        raise ValueError(f"no keys found in {path}")
    return keys


def apply_cancels(keys, spec):
    """Cancel questions given as '5,12' (all versions) or '322:5,117:9'."""
    keys = {v: list(k) for v, k in keys.items()}
    for item in filter(None, (s.strip() for s in spec.split(","))):
        version, _, q = item.rpartition(":")
        targets = [version] if version else list(keys)
        for v in targets:
            if v not in keys:
                sys.exit(f"--cancel {item}: no key for version {v}")
            q_i = int(q)
            if q_i < 1:
                sys.exit(f"--cancel {item}: bad question number")
            keys[v] += [CANCELED] * (q_i - len(keys[v]))
            keys[v][q_i - 1] = CANCELED
    return {v: "".join(k) for v, k in keys.items()}


# ---------------------------------------------------------------------------
# Grading and reporting
# ---------------------------------------------------------------------------
def grade(answers, key, policy):
    """Return (per-question correctness or None if canceled, score, total).

    policy 'drop': canceled questions are left out of the total.
    policy 'credit': canceled questions count as correct for everyone.
    """
    correct = [None if k == CANCELED else a == k for a, k in zip(answers, key)]
    n_canceled = correct.count(None)
    score = correct.count(True)
    if policy == "credit":
        return correct, score + n_canceled, len(key)
    return correct, score, len(key) - n_canceled


def load_corrections(path, num_questions, id_digits):
    """Read a corrections CSV: lines "page,question,answer".

    page is the label from results.csv (e.g. "scans.pdf#p2"). question is a
    question number with answer A-D, or "-" to clear a stray mark; "id" with the
    student ID (the last id_digits digits, or the full SIS User ID); or "version"
    with the 3-digit key ID. Returns {page: {"answers": {q: a}, "id": .., "version": ..}}.
    """
    fixes = {}
    with open(path, newline="", encoding="utf-8-sig") as f:
        for n, row in enumerate(csv.reader(f), start=1):
            row = [c.strip() for c in row]
            if not any(row) or row[0].startswith("#") or row[0].lower() == "page":
                continue
            if len(row) < 3 or not row[0]:
                sys.exit(f"{path}, line {n}: expected page,question,answer")
            page, what, value = row[0], row[1].lower(), row[2].upper()
            fix = fixes.setdefault(page, {"answers": {}, "id": None, "version": None})
            where = f"{path}, line {n}"
            if what == "id":
                if not (value.isdigit() and len(value) >= id_digits):
                    sys.exit(f"{where}: the ID must be at least {id_digits} digits, got {value!r}")
                fix["id"] = value
            elif what == "version":
                if not (value.isdigit() and len(value) == len(KEY_X)):
                    sys.exit(f"{where}: the version must be {len(KEY_X)} digits, got {value!r}")
                fix["version"] = value
            elif what.isdigit() and 1 <= int(what) <= num_questions:
                if value not in CHOICES and value != "-":
                    sys.exit(f"{where}: the answer must be one of {', '.join(CHOICES)} or -, "
                             f"got {value!r}")
                if int(what) in fix["answers"]:
                    sys.exit(f"{where}: question {what} of {page} is corrected twice")
                fix["answers"][int(what)] = value
            else:
                sys.exit(f"{where}: the question must be 1-{num_questions}, id or version, "
                         f"got {row[1]!r}")
    return fixes


def annotate(warped, answers, key, correct, header, by_hand=()):
    out = cv2.cvtColor(warped, cv2.COLOR_GRAY2BGR)
    green, red, orange, blue = (0, 170, 0), (0, 0, 220), (0, 140, 255), (200, 80, 0)
    gray = (130, 130, 130)
    for q, (a, k, ok) in enumerate(zip(answers, key, correct), start=1):
        if ok is None:
            x, y = question_center(q, 0)
            cv2.line(out, (int(x) - 20, int(y)), (int(question_center(q, 3)[0]) + 20, int(y)),
                     gray, 2)
            continue
        if k in CHOICES:
            x, y = question_center(q, CHOICES.index(k))
            cv2.circle(out, (int(x), int(y)), 18, green, 3)
        if a in CHOICES and not ok:
            x, y = question_center(q, CHOICES.index(a))
            cv2.line(out, (int(x) - 14, int(y) - 14), (int(x) + 14, int(y) + 14), red, 3)
            cv2.line(out, (int(x) - 14, int(y) + 14), (int(x) + 14, int(y) - 14), red, 3)
        if a in "-*" and q not in by_hand:
            x, y = question_center(q, 0)
            cv2.putText(out, "BLANK" if a == "-" else "MULTI", (int(x) - 120, int(y) + 8),
                        cv2.FONT_HERSHEY_SIMPLEX, 0.6, orange, 2)
    purple = (170, 0, 170)
    for q in by_hand:   # answers set from the corrections file
        x, y = question_center(q, 0)
        cv2.putText(out, "HAND", (int(x) - 120, int(y) + 8),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.6, purple, 2)
        a = answers[q - 1]
        if a in CHOICES:
            x, y = question_center(q, CHOICES.index(a))
            cv2.rectangle(out, (int(x) - 22, int(y) - 22), (int(x) + 22, int(y) + 22), purple, 2)
    cv2.putText(out, header, (60, 2290), cv2.FONT_HERSHEY_SIMPLEX, 1.1, blue, 3)
    return out


# ---------------------------------------------------------------------------
# Handwritten Student ID boxes (fallback for unbubbled columns)
# ---------------------------------------------------------------------------
HAND_T = 0.9   # min network confidence to use a handwritten digit (99%+ right in tests)


def merge_handwritten_id(bubbled, hand, threshold=HAND_T):
    """Fill '?' ID columns from confident handwriting. Returns (id, flags, boxes).

    boxes is the handwritten ID on its own ('?' where unclear or empty).

    hand is [(digit or None, confidence)] for the same columns as `bubbled`.
    Every use of handwriting is flagged, and so is handwriting that disagrees
    with the bubbles.
    """
    out = list(bubbled)
    boxes = "".join(str(d) if d is not None and c >= threshold else "?" for d, c in hand)
    unsure, differ = [], False
    for i, (b, (d, c)) in enumerate(zip(bubbled, hand)):
        if d is None:
            continue
        if b == "?":
            if c >= threshold:
                out[i] = str(d)
            else:
                unsure.append(f"col {i + 1} looks like {d} ({c:.0%})")
        elif c >= threshold and str(d) != b:
            differ = True
    merged = "".join(out)
    flags = []
    if merged != bubbled:
        flags.append(f"ID from handwriting, check: bubbles {bubbled}, boxes {boxes}")
    if unsure:
        flags.append(f"handwritten ID unclear: {', '.join(unsure)}")
    if differ:
        flags.append(f"handwritten ID {boxes} differs from bubbles {bubbled}")
    return merged, flags, boxes


def paste_id_boxes(img_out, warped, id_digits):
    """Paste an enlarged view of the ID boxes at the bottom of a review page."""
    x0 = int(hw.BOX_LEFT[len(ID_X) - id_digits]) - 8
    x1 = int(hw.BOX_LEFT[-1] + hw.BOX_W) + 8
    crop = cv2.cvtColor(warped[195:262, x0:x1], cv2.COLOR_GRAY2BGR)
    big = cv2.resize(crop, None, fx=1.9, fy=1.9, interpolation=cv2.INTER_CUBIC)
    y, x = 2020, 600
    img_out[y:y + big.shape[0], x:x + big.shape[1]] = big
    blue = (200, 80, 0)
    cv2.rectangle(img_out, (x - 2, y - 2), (x + big.shape[1] + 1, y + big.shape[0] + 1), blue, 2)
    cv2.putText(img_out, "ID boxes:", (x - 170, y + 75), cv2.FONT_HERSHEY_SIMPLEX, 0.9, blue, 2)


# ---------------------------------------------------------------------------
# Printable sheets (key sheet / student sheet) drawn onto the template PDF
# ---------------------------------------------------------------------------
DEFAULT_TEMPLATE = Path(__file__).resolve().parent / "Sheet100QA4.pdf"
PT = 72 / DPI           # template pixels -> PDF points
FILL_R = 4.6            # filled-bubble radius in points (printed outline is ~5)
# Writing areas in points: right of the "Quiz :", "Full name:", "Class :" labels.
FIELDS = {"exam": (130, 91.5, 284), "name": (146, 128.5, 284), "course": (130, 166, 284)}
KEY_BOX_Y = 84.5        # baseline of the digits written above the Key bubbles


def _fit_text(page, x, y, x_max, text, size=13):
    while size > 6 and fitz.get_text_length(text, "helv", size) > x_max - x:
        size -= 0.5
    page.insert_text((x, y), text, fontname="helv", fontsize=size)


def _bubble(page, x, y):
    page.draw_circle(fitz.Point(x * PT, y * PT), FILL_R, color=(0, 0, 0), fill=(0, 0, 0))


def write_sheets(template, out_path, sheets):
    """Write one page per sheet onto copies of the template.

    Each sheet is a dict with exam, course, key_id (3 digits) and answers (a key
    string, or None for a blank student sheet). A canceled question gets all
    four bubbles filled, which reads back as canceled.
    """
    src = fitz.open(template)
    doc = fitz.open()
    for s in sheets:
        doc.insert_pdf(src)
        page = doc[-1]
        if s.get("exam"):
            _fit_text(page, *FIELDS["exam"][:2], FIELDS["exam"][2], s["exam"])
        if s.get("course"):
            _fit_text(page, *FIELDS["course"][:2], FIELDS["course"][2], s["course"])
        for x, d in zip(KEY_X, s["key_id"]):
            w = fitz.get_text_length(d, "helv", 12)
            page.insert_text((x * PT - w / 2, KEY_BOX_Y), d, fontname="helv", fontsize=12)
            _bubble(page, x, DIGIT_Y[int(d)])
        if s.get("answers") is not None:
            _fit_text(page, *FIELDS["name"][:2], FIELDS["name"][2], "ANSWER KEY")
            for q, a in enumerate(s["answers"], start=1):
                for c in (range(4) if a == CANCELED else [CHOICES.index(a)]):
                    _bubble(page, *question_center(q, c))
    doc.save(out_path, garbage=3, deflate=True)


def write_canvas(gradebook, rows, out):
    """Write OUT/canvas_import.csv and OUT/absent.csv; flag duplicate sheets.

    Only students with exactly one matched, graded sheet go into the import, so
    Canvas leaves everyone else's grade unchanged.
    """
    pages = defaultdict(list)
    for r in rows:
        if r.get("sis_user_id"):
            pages[r["sis_user_id"]].append(r)
    grades = {}
    for sis, rs in pages.items():
        if len(rs) > 1:
            for r in rs:
                others = ", ".join(o["page"] for o in rs if o is not r)
                r["flags"] = "; ".join(filter(None, [
                    r["flags"], f"same student as {others}; not exported, enter by hand"]))
        elif rs[0]["total"]:
            grades[sis] = rs[0]["score"] / rs[0]["total"] * gradebook.points

    gradebook.write_import(out / "canvas_import.csv", grades)
    absent = [s for s in gradebook.students if s["SIS User ID"] not in pages]
    with open(out / "absent.csv", "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["Student", "SIS User ID"])
        w.writerows([s["Student"], s["SIS User ID"]] for s in absent)
    unmatched = sum(1 for r in rows if not r.get("sis_user_id"))
    print(f"canvas: {len(grades)} grades in {out / 'canvas_import.csv'}; "
          f"{unmatched} sheet(s) not matched, {len(pages) - len(grades)} student(s) with "
          f"several sheets (see flags); {len(absent)} student(s) without a sheet "
          f"({out / 'absent.csv'})")


def main(argv=None):
    global MARK_T
    ap = argparse.ArgumentParser(prog="omr-grader", description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("scans", nargs="*",
                    help="scanned PDFs or images (none: just write the key to OUT/key.csv)")
    ap.add_argument("--key", required=True, action="append",
                    help="key CSV, filled key sheet, or exam LaTeX file; "
                         "prefix a .tex with its version, e.g. 322=exam.tex, "
                         "and repeat --key for several versions")
    ap.add_argument("--out", help="output directory (default: a graded folder next to "
                                      "the first scan; with no scans, ./graded)")
    ap.add_argument("--num-questions", type=int,
                    help="number of questions (default: length of the key); "
                         "questions past the end of a key are canceled")
    ap.add_argument("--cancel", default="",
                    help="questions to cancel: '5,12' for all versions, or '322:5,117:9'")
    ap.add_argument("--canceled", choices=["drop", "credit"], default="drop",
                    help="drop: leave canceled questions out of the total (default); "
                         "credit: give everyone the point")
    ap.add_argument("--mark-threshold", type=float, default=MARK_T,
                    help=f"darkness (0 = paper, 1 = black) at which a bubble counts as "
                         f"marked (default {MARK_T}); lower it to pick up lighter marks, "
                         f"but keep it above erasures (about 0.2)")
    ap.add_argument("--id-digits", type=int, default=4, choices=range(1, len(ID_X) + 1),
                    metavar=f"1-{len(ID_X)}",
                    help="number of Student ID digits (default 4); they are read from the right-hand columns, where the 4-digit sheet prints them")
    ap.add_argument("--no-handwriting", action="store_true",
                    help="do not read the handwritten ID boxes")
    ap.add_argument("--corrections", metavar="CSV",
                    help="answers, IDs or versions set by hand, lines 'page,question,answer' "
                         "(page as in results.csv, e.g. scans.pdf#p2; question 1-100, id or "
                         "version); every correction is flagged")
    cv = ap.add_argument_group("Canvas gradebook")
    cv.add_argument("--canvas", metavar="CSV",
                    help="Canvas gradebook export: match sheets to students by the last "
                         "--id-digits of their SIS User ID and write OUT/canvas_import.csv")
    cv.add_argument("--canvas-assignment", metavar="NAME",
                    help="gradebook column to fill, e.g. 'Exam 2' (required with --canvas)")
    sheets = ap.add_argument_group("printable sheets")
    sheets.add_argument("--key-sheet", metavar="PDF",
                        help="write a filled answer-key sheet (one page per version)")
    sheets.add_argument("--student-sheet", metavar="PDF",
                        help="write a blank student sheet with the exam, course and key "
                             "ID filled in (one page per version)")
    sheets.add_argument("--exam-name", help="text for the Quiz field, e.g. 'Exam 2'")
    sheets.add_argument("--course", help="text for the Class field, e.g. 'CSE4001 Fall 2026'")
    sheets.add_argument("--key-id", help="3-digit key ID for a key that has none "
                                         "(a .tex key given without VERSION=)")
    sheets.add_argument("--template", default=str(DEFAULT_TEMPLATE),
                        help="blank sheet PDF (default: the Sheet100QA4.pdf that comes with the grader)")
    args = ap.parse_args(argv)

    MARK_T = args.mark_threshold
    hw_reader = None if args.no_handwriting else hw.reader()
    if hw_reader is None and not args.no_handwriting:
        print(f"note: {hw.MODEL_PATH.name} not found; handwritten IDs are not read")
    faint_t = max(0.12, MARK_T / 2)  # blank questions darker than this get flagged

    gradebook = None
    if args.canvas:
        if not args.canvas_assignment:
            sys.exit("--canvas needs --canvas-assignment, e.g. --canvas-assignment 'Exam 2'")
        try:
            gradebook = canvas.Gradebook(args.canvas, args.canvas_assignment, args.id_digits)
        except ValueError as e:
            sys.exit(str(e))
        print(f"gradebook: {len(gradebook.students)} students; column "
              f"{gradebook.column!r} ({gradebook.points:g} points)")
        for k, group in gradebook.shared.items():
            print(f"  warning: {', '.join(s['Student'] for s in group)} share ID digits {k}; "
                  f"their sheets cannot be matched automatically")

    keys = {}
    for spec in args.key:
        for v, k in load_keys(spec).items():
            if v in keys:
                sys.exit(f"two keys for version {v}")
            keys[v] = k
    if args.key_id:
        if not re.fullmatch(r"\d{%d}" % len(KEY_X), args.key_id):
            sys.exit(f"--key-id must be {len(KEY_X)} digits, got {args.key_id!r}")
        if ANY not in keys:
            sys.exit("--key-id is only for a key without a version (a .tex given without "
                     "VERSION=); the other keys already carry theirs")
        if args.key_id in keys:
            sys.exit(f"two keys for version {args.key_id}")
        keys[args.key_id] = keys.pop(ANY)
    keys = apply_cancels(keys, args.cancel)
    for v, k in keys.items():
        bad = set(k) - set(CHOICES + CANCELED)
        if bad:
            sys.exit(f"key {v}: unexpected characters {sorted(bad)}")
    n = args.num_questions or max(len(k) for k in keys.values())
    keys = {v: k[:n].ljust(n, CANCELED) for v, k in keys.items()}
    policy = "left out of the total" if args.canceled == "drop" else "credited to everyone"
    print(f"grading {n} questions; canceled questions are {policy}")
    for v, k in keys.items():
        canceled = [i + 1 for i, a in enumerate(k) if a == CANCELED]
        print(f"  key {v}: canceled {canceled if canceled else 'none'}")

    if args.out:
        out = Path(args.out)
    else:  # results go with the exam: exam2/scans.pdf -> exam2/graded
        out = Path(args.scans[0]).parent / "graded" if args.scans else Path("graded")
    out.mkdir(parents=True, exist_ok=True)
    with open(out / "key.csv", "w") as f:  # the key actually used, for the record
        f.write("version,answers\n")
        for v, k in keys.items():
            f.write(f"{v},{k}\n")

    if args.key_sheet or args.student_sheet:
        if ANY in keys:
            sys.exit("the key has no ID to print on the sheet; give --key-id "
                     "(or the .tex as VERSION=exam.tex)")
        for path, with_answers in [(args.key_sheet, True), (args.student_sheet, False)]:
            if path:
                write_sheets(args.template, path, [
                    {"exam": args.exam_name, "course": args.course, "key_id": v,
                     "answers": k if with_answers else None}
                    for v, k in keys.items()])
                print(f"wrote {path}")

    if not args.scans:
        print(f"wrote {out / 'key.csv'}")
        return
    corrections = load_corrections(args.corrections, n, args.id_digits) if args.corrections else {}
    used = set()
    review = fitz.open()
    rows = []
    item_right = {v: np.zeros(n, int) for v in keys}
    item_count = {v: 0 for v in keys}

    for scan in args.scans:
        for label, img in load_pages(scan):
            flags = []
            try:
                sheet = read_sheet(img, n)
            except Exception as e:  # noqa: BLE001 - report and keep going
                print(f"{label}: FAILED ({e})")
                rows.append({"page": label, "flags": f"unreadable: {e}"})
                continue

            # The ID is in the right-hand --id-digits of the six column positions: the
            # 4-digit sheet prints only positions 3-6. Marks to their left are flagged.
            full_id = sheet["student_id"]
            unused = len(full_id) - args.id_digits
            sheet["student_id"] = full_id[unused:]
            if any(d != "?" for d in full_id[:unused]):
                flags.append(f"marks left of the {args.id_digits} ID columns ({full_id})")

            fix = corrections.get(label)
            if fix:
                used.add(label)
            fixed_id = fix["id"] if fix else None
            if fixed_id:
                flags.append(f"ID set by hand: {fixed_id} (read as {sheet['student_id']})")
                sheet["student_id"] = fixed_id
            if fix and fix["version"]:
                flags.append(f"version set by hand: {fix['version']} "
                             f"(read as {sheet['version']})")
                sheet["version"] = fix["version"]

            # Handwritten digits in the ID boxes fill in unbubbled columns (flagged).
            show_boxes, boxes = False, None
            if hw_reader is not None and not fixed_id:
                hand = hw_reader.read(sheet["warped"], range(unused, len(full_id)))
                sheet["student_id"], hw_flags, boxes = merge_handwritten_id(
                    sheet["student_id"], hand, HAND_T)
                flags += hw_flags
                show_boxes = bool(hw_flags)

            # Match to a student in the gradebook (may complete or correct the ID).
            student = None
            if gradebook is not None and fixed_id and len(fixed_id) > args.id_digits:
                student = gradebook.by_sis(fixed_id)   # a full SIS User ID set by hand
                if student is None:
                    flags.append(f"SIS User ID {fixed_id} not in gradebook")
                else:
                    sheet["student_id"] = gradebook.key(student)
            elif gradebook is not None:
                student, sheet["student_id"], gb_flags = gradebook.resolve(
                    sheet["student_id"], boxes)
                flags += gb_flags
                show_boxes = show_boxes or bool(gb_flags)

            version = sheet["version"]  # as bubbled on the sheet
            kv = version if version in keys else ANY if ANY in keys else None
            if kv is None:
                if len(keys) == 1 and "?" in version:
                    kv = next(iter(keys))
                    flags.append(f"version {version} unreadable, used {kv}")
                else:
                    how = " (set by hand)" if fix and fix["version"] else ""
                    print(f"{label}: unknown version {version}{how}, skipped")
                    rows.append({"page": label, "student_id": sheet["student_id"],
                                 "version": version, "flags": f"unknown version{how}"})
                    continue
            key = keys[kv]
            answers = sheet["answers"]
            by_hand = sorted(fix["answers"]) if fix else []
            if by_hand:
                read = {"-": "blank", "*": "multiple"}
                changes = []
                for q in by_hand:
                    old, new = answers[q - 1], fix["answers"][q]
                    changes.append(f"Q{q} {'blank' if new == '-' else new} "
                                   f"(read as {read.get(old, old)})")
                    answers = answers[:q - 1] + new + answers[q:]
                flags.append("set by hand: " + ", ".join(changes))
            correct, score, total = grade(answers, key, args.canceled)
            item_right[kv] += np.array([c is True for c in correct], int)
            item_count[kv] += 1

            if "?" in sheet["student_id"] and gradebook is None:
                flags.append("check student ID")
            # questions set by hand are already flagged above
            live = [i for i, c in enumerate(correct) if c is not None and i + 1 not in by_hand]
            blanks = [i + 1 for i in live if answers[i] == "-"]
            multis = [i + 1 for i in live if answers[i] == "*"]
            faint = [q for q in blanks if sheet["top"][q - 1] >= faint_t]
            if blanks:
                flags.append(f"blank: {blanks}")
            if faint:
                flags.append(f"faint mark, check: {faint}")
            if multis:
                flags.append(f"multiple: {multis}")

            rows.append({
                "page": label, "student_id": sheet["student_id"],
                "name": student["Student"] if student else "",
                "sis_user_id": student["SIS User ID"] if student else "",
                "version": version, "score": score, "total": total,
                "percent": round(100 * score / total, 1) if total else "",
                "answers": answers, "flags": "; ".join(flags),
            })
            pct = f"  {100 * score / total:.1f}%" if total else ""
            who = f"  {student['Student']}" if student else ""
            header = f"ID {sheet['student_id']}{who}  v{version}  {score}/{total}{pct}"
            print(f"{label}: {header}" + (f"  [{'; '.join(flags)}]" if flags else ""))

            img_out = annotate(sheet["warped"], answers, key, correct, header, by_hand)
            if show_boxes:
                paste_id_boxes(img_out, sheet["warped"], args.id_digits)
            ok, buf = cv2.imencode(".jpg", img_out, [cv2.IMWRITE_JPEG_QUALITY, 80])
            page = review.new_page(width=595, height=842)
            page.insert_image(page.rect, stream=buf.tobytes())

    unused = [p for p in corrections if p not in used]
    if unused:
        print(f"warning: corrections for pages that were not graded: {', '.join(unused)} "
              f"(use the page labels from results.csv)")

    if gradebook is not None:
        write_canvas(gradebook, rows, out)

    fields = ["page", "student_id", "version", "score", "total", "percent", "answers", "flags"]
    if gradebook is not None:
        fields[2:2] = ["name", "sis_user_id"]
    with open(out / "results.csv", "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=fields, extrasaction="ignore")
        w.writeheader()
        w.writerows(rows)

    with open(out / "item_analysis.csv", "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["version", "question", "key", "percent_correct"])
        for v in keys:
            if item_count[v]:
                for q in range(n):
                    if keys[v][q] == CANCELED:
                        w.writerow([v, q + 1, "canceled", ""])
                    else:
                        pct = 100 * item_right[v][q] / item_count[v]
                        w.writerow([v, q + 1, keys[v][q], round(pct, 1)])

    if len(review):
        review.save(out / "review.pdf")
    print(f"wrote {out / 'results.csv'}, {out / 'item_analysis.csv'}, {out / 'review.pdf'}")


if __name__ == "__main__":
    main()
