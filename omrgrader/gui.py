"""A local browser window for the grader: omr-grader-gui (or python gui.py)

Runs the grader with the options picked in the form. The page is served only
to this computer (127.0.0.1), nothing is shared, and Gradio's usage statistics are
off. Uploaded files are copied to a temporary folder for each run and deleted
after it; results are written to the exam folder. The Review tab writes
corrections.csv in the exam folder from clicks on the graded pages.

Needs gradio (pip install gradio) in addition to the grader's packages.
"""

import csv
import os
import platform
import re
import shlex
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

import cv2
import numpy as np

from . import grader as g   # bubble positions, shared with the grader

os.environ.setdefault("GRADIO_ANALYTICS_ENABLED", "False")
import gradio as gr  # noqa: E402

GUIDE_URL = "https://github.com/eraldoribeiro/omr_grader/blob/main/docs/GUIDE.md"
PKG_PARENT = Path(__file__).resolve().parent.parent   # so "-m omrgrader.grader" works
# Force Gradio's dark theme whatever the browser or system setting: Gradio uses
# the dark theme when the address has ?__theme=dark, so add it on load.
DARK = ("<script>const u = new URL(location.href);"
        "if (u.searchParams.get('__theme') !== 'dark') {"
        "u.searchParams.set('__theme', 'dark'); location.replace(u); }</script>")
TABLE_COLS = ["page", "student_id", "name", "version", "score", "total", "percent", "flags"]
CORRECTIONS = "corrections.csv"   # written by the Review tab, in the exam folder
PURPLE = (170, 0, 170)            # RGB, the colour the grader uses for HAND


def exam_dir(text):
    """The exam folder typed in the window (relative to where the window started)."""
    d = Path(text.strip().strip("'\"") or ".").expanduser()
    return d if d.is_absolute() else Path.cwd() / d


def open_path(path):
    """Open a file or folder with the system viewer (Preview, Finder, ...)."""
    path = Path(path)
    if not path.exists():
        return f"{path} does not exist yet."
    if platform.system() == "Darwin":
        subprocess.Popen(["open", str(path)])
    elif platform.system() == "Windows":
        os.startfile(str(path))  # noqa: S606
    else:
        subprocess.Popen(["xdg-open", str(path)])
    return f"Opened {path}"


def files_of(value):
    """Paths from a gr.File value (None, one path, or a list)."""
    if not value:
        return []
    return [str(v) for v in (value if isinstance(value, list) else [value])]


def stage(paths, folder):
    """Copy uploads into one folder under their own names, so an exam's .aux file
    sits next to its .tex (the grader uses it to check the question count)."""
    out = []
    for p in paths:
        dst = Path(folder) / Path(p).name
        shutil.copy(p, dst)
        out.append(dst)
    return out


def key_args(key_files, key_id, folder):
    """--key options for the uploaded key files (.aux files only travel along)."""
    keys = [p for p in stage(key_files, folder) if p.suffix.lower() != ".aux"]
    if not keys:
        raise gr.Error("Add a key: the exam .tex, a key CSV, or a scanned key sheet.")
    args = []
    for k in keys:
        args += ["--key", str(k)]
    if key_id.strip():
        args += ["--key-id", key_id.strip()]
    return args


def run(args):
    """Run the grader; return (command line, output, ok)."""
    cmd = [sys.executable, "-m", "omrgrader.grader"] + args
    env = dict(os.environ)
    env["PYTHONPATH"] = os.pathsep.join(filter(None, [str(PKG_PARENT), env.get("PYTHONPATH")]))
    p = subprocess.run(cmd, capture_output=True, text=True, env=env)
    log = (p.stdout + ("\n" + p.stderr if p.stderr.strip() else "")).strip()
    return shlex.join(["omr-grader"] + args), log, p.returncode == 0


def assignments(gradebook):
    """Assignment names in a Canvas gradebook export, for the dropdown."""
    paths = files_of(gradebook)
    if not paths:
        return gr.update(choices=[], value=None)
    with open(paths[0], newline="", encoding="utf-8-sig") as f:
        header = next(csv.reader(f), [])
    names = [re.sub(r"\s*\(\d+\)\s*$", "", h).strip() for h in header
             if re.search(r"\(\d+\)\s*$", h)]
    return gr.update(choices=names, value=names[0] if len(names) == 1 else None)


def summary(rows, log):
    graded = [r for r in rows if r.get("score") not in (None, "")]
    flagged = [r for r in rows if r.get("flags")]
    if not graded:
        return f"**No sheets graded.** {len(rows)} page(s) read; see the output below."
    pct = sorted(float(r["percent"]) for r in graded if r.get("percent"))
    lines = [f"**{len(graded)} sheets graded**, {len(flagged)} flagged for a look."]
    if pct:
        mid = len(pct) // 2
        median = pct[mid] if len(pct) % 2 else (pct[mid - 1] + pct[mid]) / 2
        lines.append(f"Mean {sum(pct) / len(pct):.1f}%, median {median:.1f}%, "
                     f"lowest {pct[0]:.1f}%, highest {pct[-1]:.1f}%.")
    canvas_line = next((ln for ln in log.splitlines() if ln.startswith("canvas:")), None)
    if canvas_line:
        lines.append(canvas_line.replace("canvas:", "Canvas:", 1))
    return "\n\n".join(lines)


def grade(scans, key_files, key_id, gradebook, assignment, corrections, cancel, canceled,
          num_questions, threshold, id_digits, handwriting, folder):
    scans, keys = files_of(scans), files_of(key_files)
    if not scans:
        raise gr.Error("Add the scanned sheets (PDFs or images).")
    out = exam_dir(folder) / "graded"
    with tempfile.TemporaryDirectory(prefix="omr_") as tmp:
        args = [str(p) for p in stage(scans, tmp)]
        args += key_args(keys, key_id, tmp)
        args += ["--out", str(out), "--canceled", canceled,
                 "--mark-threshold", f"{threshold:g}", "--id-digits", str(int(id_digits))]
        if cancel.strip():
            args += ["--cancel", cancel.replace(" ", "")]
        if num_questions:
            args += ["--num-questions", str(int(num_questions))]
        if not handwriting:
            args.append("--no-handwriting")
        fix_file = (files_of(corrections) or
                    [str(p) for p in [exam_dir(folder) / CORRECTIONS] if p.exists()])
        if fix_file:
            args += ["--corrections", fix_file[0]]
        if files_of(gradebook):
            if not assignment:
                raise gr.Error("Pick the gradebook column to fill.")
            args += ["--canvas", files_of(gradebook)[0], "--canvas-assignment", assignment]
        command, log, ok = run(args)

    log = f"$ {command}\n\n{log}"
    if not ok:
        return ("**The grader stopped with an error** (see the output below).",
                None, None, log)
    with open(out / "results.csv", newline="") as f:
        rows = list(csv.DictReader(f))
    cols = [c for c in TABLE_COLS if rows and c in rows[0]]
    table = [[r.get(c, "") for c in cols] for r in rows]
    flagged = [[r.get(c, "") for c in cols] for r in rows if r.get("flags")]
    applied = f"\n\nCorrections applied from `{fix_file[0]}`." if fix_file else ""
    return (summary(rows, log) + applied + f"\n\nResults are in `{out}`.",
            gr.update(value=table, headers=cols), gr.update(value=flagged, headers=cols), log)


def prepare(key_files, key_id, exam_name, course, cancel, folder, key_sheet, student_sheet):
    out = exam_dir(folder)
    tex = any(Path(p).suffix.lower() == ".tex" for p in files_of(key_files))
    if tex and (key_sheet or student_sheet) and not key_id.strip():
        raise gr.Error("Enter the key ID: it is bubbled on the printed sheets.")
    with tempfile.TemporaryDirectory(prefix="omr_") as tmp:
        args = key_args(files_of(key_files), key_id, tmp) + ["--out", str(out)]
        if cancel.strip():
            args += ["--cancel", cancel.replace(" ", "")]
        if key_sheet or student_sheet:
            if exam_name.strip():
                args += ["--exam-name", exam_name.strip()]
            if course.strip():
                args += ["--course", course.strip()]
        if key_sheet:
            args += ["--key-sheet", str(out / "key_sheet.pdf")]
        if student_sheet:
            args += ["--student-sheet", str(out / "student_sheet.pdf")]
        command, log, ok = run(args)
    log = f"$ {command}\n\n{log}"
    key_rows = []
    if ok and (out / "key.csv").exists():
        with open(out / "key.csv", newline="") as f:
            for r in csv.DictReader(f):
                ans = r["answers"]
                key_rows += [[r["version"] if i == 0 else "",
                              f"{i + 1}-{min(i + 10, len(ans))}", " ".join(ans[i:i + 10])]
                             for i in range(0, len(ans), 10)]
                key_rows.append(["", "", ""])
    status = (f"**Done.** Written to `{out}`. The key below is what the grader will use "
              "(X = canceled), ten questions per line." if ok else
              "**The grader stopped with an error** (see the output below).")
    return status, key_rows[:-1], log


# ---------------------------------------------------------------------------
# Review tab: click bubbles on the graded pages to write corrections.csv
# ---------------------------------------------------------------------------
def review_load(folder, flagged_only, fixes=None):
    """Read the exam folder's graded pages and corrections.csv.

    Returns (dropdown update, pages, number of questions, fixes, message)."""
    graded = exam_dir(folder) / "graded"
    if not (graded / "results.csv").exists() or not (graded / "review.pdf").exists():
        return (gr.update(choices=[], value=None), [], 0, {},
                f"No graded sheets in `{graded}` yet. Grade the exam first.")
    with open(graded / "results.csv", newline="") as f:
        rows = list(csv.DictReader(f))
    pages, i = [], 0
    for r in rows:   # review.pdf has a page for each graded sheet, in this order
        graded_sheet = r.get("score") not in (None, "")
        pages.append({"label": r["page"], "row": r, "image": i if graded_sheet else None})
        i += graded_sheet
    n = 0
    if (graded / "key.csv").exists():
        lines = (graded / "key.csv").read_text().splitlines()[1:]
        n = max((len(ln.split(",", 1)[1].strip()) for ln in lines if "," in ln), default=0)

    msg = ""
    if fixes is None:
        fixes, path = {}, exam_dir(folder) / CORRECTIONS
        if path.exists():
            try:
                fixes = g.load_corrections(path, len(g.ANS_X) * len(g.ANS_Y), 1)
            except SystemExit as e:
                return (gr.update(choices=[], value=None), pages, n, {},
                        f"**{path.name} has an error:** {e}")
            msg = f"Loaded {sum(map(fix_count, fixes.values()))} corrections from `{path}`."
    choices = [(page_title(pg), pg["label"]) for pg in pages
               if not flagged_only or pg["row"].get("flags") or pg["label"] in fixes]
    first = choices[0][1] if choices else None
    return (gr.update(choices=choices, value=first), pages, n, fixes,
            msg or f"{len(pages)} sheets graded; {len(choices)} listed.")


def fix_count(fix):
    return len(fix["answers"]) + bool(fix["id"]) + bool(fix["version"])


def page_title(pg):
    r = pg["row"]
    who = r.get("name") or f"ID {r.get('student_id', '?')}"
    score = f"{r['score']}/{r['total']}" if r.get("score") not in (None, "") else "not graded"
    flags = r.get("flags", "")
    return f"{pg['label']}  ·  {who}  ·  {score}" + (f"  ·  {flags[:70]}" if flags else "")


def review_image(folder, pg, fix):
    """The page from review.pdf (template frame, RGB), with pending corrections drawn."""
    doc = g.fitz.open(exam_dir(folder) / "graded" / "review.pdf")
    xref = doc[pg["image"]].get_images()[0][0]
    img = cv2.imdecode(np.frombuffer(doc.extract_image(xref)["image"], np.uint8),
                       cv2.IMREAD_COLOR)
    img = cv2.cvtColor(img, cv2.COLOR_BGR2RGB)
    for q, a in (fix or {}).get("answers", {}).items():
        x, y = g.question_center(q, 0)
        if a in g.CHOICES:
            x, y = g.question_center(q, g.CHOICES.index(a))
            cv2.rectangle(img, (int(x) - 24, int(y) - 24), (int(x) + 24, int(y) + 24), PURPLE, 4)
        else:
            cv2.line(img, (int(x) - 20, int(y)), (int(g.question_center(q, 3)[0]) + 20,
                     int(y)), PURPLE, 4)
    return img


def review_show(label, pages, fixes, folder):
    """Image, ID, version and description of the chosen sheet."""
    pg = next((p for p in pages if p["label"] == label), None)
    if pg is None:
        return None, "", "", ""
    fix = fixes.get(label, {})
    r = pg["row"]
    info = f"**{page_title(pg)}**"
    if pg["image"] is None:
        info += "\n\nThis sheet was not graded, so there is no page to click; set its " \
                "version or ID on the right."
        img = None
    else:
        img = review_image(folder, pg, fix)
    return img, fix.get("id") or "", fix.get("version") or "", info


def locate(x, y, n):
    """The (question, answer) a click on the page means, or None.

    A click on a bubble sets that answer; a click just left of the A bubble, on
    the question number, clears the question."""
    best, hit = 1e9, None
    for q in range(1, n + 1):
        for c in range(len(g.CHOICES)):
            bx, by = g.question_center(q, c)
            d = np.hypot(x - bx, y - by)
            if d < best:
                best, hit = d, (q, g.CHOICES[c])
        ax, ay = g.question_center(q, 0)
        if abs(y - ay) <= 16 and ax - 85 <= x <= ax - 20:
            return q, "-"
    return hit if best <= g.BUBBLE_R * 1.7 else None


def review_click(evt: gr.SelectData, label, pages, fixes, n, folder):
    pg = next((p for p in pages if p["label"] == label), None)
    if pg is None or pg["image"] is None:
        return gr.update(), fixes, corrections_table(fixes), "Pick a graded sheet first."
    x, y = evt.index
    hit = locate(x, y, n)
    if hit is None:
        return gr.update(), fixes, corrections_table(fixes), \
            "Click on a bubble (or on the question number to clear the question)."
    q, a = hit
    fixes = {k: {"answers": dict(v["answers"]), "id": v["id"], "version": v["version"]}
             for k, v in fixes.items()}
    fix = fixes.setdefault(label, {"answers": {}, "id": None, "version": None})
    if fix["answers"].get(q) == a:      # the same click again undoes it
        del fix["answers"][q]
        msg = f"Q{q}: correction removed."
    else:
        fix["answers"][q] = a
        msg = f"Q{q} set to {'blank' if a == '-' else a} (not saved yet)."
    if not fix_count(fix):
        del fixes[label]
    return review_image(folder, pg, fixes.get(label)), fixes, corrections_table(fixes), msg


def review_field(value, what, label, fixes):
    """Typing in the ID or version box."""
    if not label:
        return fixes, corrections_table(fixes)
    fixes = {k: {"answers": dict(v["answers"]), "id": v["id"], "version": v["version"]}
             for k, v in fixes.items()}
    fix = fixes.setdefault(label, {"answers": {}, "id": None, "version": None})
    fix[what] = value.strip() or None
    if not fix_count(fix):
        del fixes[label]
    return fixes, corrections_table(fixes)


def corrections_table(fixes):
    rows = []
    for label, fix in fixes.items():
        rows += [[label, str(q), a] for q, a in sorted(fix["answers"].items())]
        rows += [[label, w, fix[w]] for w in ("id", "version") if fix[w]]
    return rows


def review_save(fixes, folder, id_digits, label=None, id_value="", version_value=""):
    """Write corrections.csv in the exam folder (including the ID and version boxes of
    the sheet on screen); returns (message, fixes)."""
    if label:
        fixes, _ = review_field(id_value or "", "id", label, fixes)
        fixes, _ = review_field(version_value or "", "version", label, fixes)
    for label, fix in fixes.items():
        if fix["id"] and not (fix["id"].isdigit() and len(fix["id"]) >= int(id_digits)):
            raise gr.Error(f"{label}: the ID must be at least {int(id_digits)} digits.")
        if fix["version"] and not (fix["version"].isdigit()
                                   and len(fix["version"]) == len(g.KEY_X)):
            raise gr.Error(f"{label}: the version must be {len(g.KEY_X)} digits.")
    path = exam_dir(folder) / CORRECTIONS
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["page", "question", "answer"])
        w.writerows(corrections_table(fixes))
    return f"Saved {len(corrections_table(fixes))} corrections to `{path}`.", fixes


def build():
    with gr.Blocks(title="Bubble-sheet grader", delete_cache=(1800, 4 * 3600)) as app:
        gr.Markdown("# Bubble-sheet grader\nRuns on this computer only. "
                    "Uploaded files are deleted after each run. "
                    f"[Step-by-step guide]({GUIDE_URL}).")
        exam = gr.Textbox(label="Exam folder", value=str(Path.cwd()),
                          info="Prepare exam writes the key and sheets here; grading writes "
                               "to its graded folder. In Finder, Option-Command-C copies a "
                               "folder's path.")
        with gr.Tab("Grade"):
            with gr.Row():
                with gr.Column():
                    scans = gr.File(label="Scanned sheets (PDF or images)", file_count="multiple",
                                    file_types=[".pdf", ".png", ".jpg", ".jpeg", ".tif", ".tiff"])
                    keys = gr.File(label="Answer key: exam .tex (and its .aux), key CSV, "
                                         "or scanned key sheet", file_count="multiple",
                                   file_types=[".tex", ".aux", ".csv", ".pdf", ".png", ".jpg"])
                    key_id = gr.Textbox(label="Key ID for a .tex key (optional)",
                                        placeholder="e.g. 322; leave empty to grade every "
                                                    "sheet with the .tex key")
                with gr.Column():
                    book = gr.File(label="Canvas gradebook export (optional)",
                                   file_types=[".csv"])
                    assignment = gr.Dropdown(label="Gradebook column to fill", choices=[],
                                             allow_custom_value=True)
                    corrections = gr.File(
                        label="Corrections (optional): CSV of page,question,answer, e.g. "
                              "students.pdf#p2,5,C (see the guide)", file_types=[".csv"])
                    cancel = gr.Textbox(label="Cancel questions (optional)",
                                        placeholder="5,12  or per version: 322:5,117:9")
                    canceled = gr.Radio([("Leave out of the total", "drop"),
                                         ("Give everyone the point", "credit")],
                                        value="drop", label="Canceled questions")
            with gr.Accordion("More options", open=False):
                with gr.Row():
                    nq = gr.Number(label="Number of questions (empty: key length)",
                                   precision=0, value=None)
                    thr = gr.Slider(0.2, 0.5, value=0.35, step=0.01,
                                    label="Mark threshold (lower picks up lighter marks; "
                                          "keep above 0.22)")
                    digits = gr.Number(label="Student ID digits", value=4, precision=0,
                                       minimum=1, maximum=6)
                    hand = gr.Checkbox(label="Read handwritten ID digits when bubbles "
                                             "are blank", value=True)
            go = gr.Button("Grade", variant="primary")
            status = gr.Markdown()
            with gr.Row():
                open_review = gr.Button("Open review.pdf")
                open_folder = gr.Button("Open graded folder")
                open_msg = gr.Markdown()
            gr.Markdown("### Sheets to check")
            flagged = gr.Dataframe(interactive=False, wrap=True)
            gr.Markdown("### All sheets")
            table = gr.Dataframe(interactive=False, wrap=True)
            with gr.Accordion("Grader output", open=False):
                log = gr.Textbox(lines=14, show_label=False)

            book.change(assignments, book, assignment)
            go.click(grade, [scans, keys, key_id, book, assignment, corrections, cancel,
                             canceled, nq,
                             thr, digits, hand, exam],
                     [status, table, flagged, log])
            open_review.click(lambda d: open_path(exam_dir(d) / "graded" / "review.pdf"),
                              exam, open_msg)
            open_folder.click(lambda d: open_path(exam_dir(d) / "graded"), exam, open_msg)

        with gr.Tab("Review"):
            gr.Markdown(
                "Click the bubble the student meant to set that answer, or the question "
                "number to clear a stray mark; click the same place again to undo. The "
                "corrections are saved to `corrections.csv` in the exam folder, and the "
                "Grade tab applies that file whenever its Corrections box is empty.")
            with gr.Row():
                r_load = gr.Button("Load graded sheets")
                r_flagged = gr.Checkbox(label="Only sheets with flags or corrections",
                                        value=True)
            r_page = gr.Dropdown(label="Sheet", choices=[])
            r_info = gr.Markdown()
            with gr.Row():
                with gr.Column(scale=3):
                    r_img = gr.Image(label="Click a bubble", interactive=False,
                                     type="numpy", height=1000)
                with gr.Column(scale=1):
                    r_id = gr.Textbox(label="Student ID (last digits, or the full SIS "
                                            "User ID)")
                    r_version = gr.Textbox(label="Version (3-digit key ID)")
                    r_table = gr.Dataframe(headers=["page", "question", "answer"],
                                           interactive=False, label="Corrections")
                    r_save = gr.Button("Save corrections")
                    r_regrade = gr.Button("Save and grade again", variant="primary")
                    r_msg = gr.Markdown()
            r_pages, r_n, r_fixes = gr.State([]), gr.State(0), gr.State({})

            def load(folder, flagged_only):
                dd, pages, n, fixes, msg = review_load(folder, flagged_only)
                return dd, pages, n, fixes, corrections_table(fixes), msg

            def must_be_loaded(pages):
                if not pages:   # else an empty list would replace the saved file
                    raise gr.Error("Click \"Load graded sheets\" first, so the "
                                   "corrections already saved are kept.")

            def save(fixes, folder, id_digits, label, id_value, version_value, pages):
                must_be_loaded(pages)
                msg, fixes = review_save(fixes, folder, id_digits, label, id_value,
                                         version_value)
                return msg, fixes, corrections_table(fixes)

            def regrade(fixes, pages, folder, flagged_only, label, id_value, version_value,
                        *grade_inputs):
                must_be_loaded(pages)
                msg, fixes = review_save(fixes, folder, grade_inputs[10], label, id_value,
                                         version_value)
                # grade with the file just saved, not one left in the upload box
                inputs = list(grade_inputs)
                inputs[5] = None
                status, tbl, flg, lg = grade(*inputs, folder)
                dd, pages, n, _, _ = review_load(folder, flagged_only, fixes)
                dd["value"] = label   # stay on the same sheet
                return (status, tbl, flg, lg, dd, pages, n, fixes, corrections_table(fixes),
                        msg + " Graded again; the Grade tab has the new results.")

            r_load.click(load, [exam, r_flagged],
                         [r_page, r_pages, r_n, r_fixes, r_table, r_msg])
            r_flagged.change(lambda f, fl, fx: review_load(f, fl, fx)[0],
                             [exam, r_flagged, r_fixes], r_page)
            r_page.change(review_show, [r_page, r_pages, r_fixes, exam],
                          [r_img, r_id, r_version, r_info])
            r_img.select(review_click, [r_page, r_pages, r_fixes, r_n, exam],
                         [r_img, r_fixes, r_table, r_msg])
            for box, what in [(r_id, "id"), (r_version, "version")]:
                for event in (box.blur, box.submit):   # leaving the box, or Enter
                    event(lambda v, l, f, w=what: review_field(v, w, l, f),
                          [box, r_page, r_fixes], [r_fixes, r_table])
            r_save.click(save, [r_fixes, exam, digits, r_page, r_id, r_version, r_pages],
                         [r_msg, r_fixes, r_table])
            r_regrade.click(
                regrade,
                [r_fixes, r_pages, exam, r_flagged, r_page, r_id, r_version, scans, keys,
                 key_id,
                 book, assignment, corrections, cancel, canceled, nq, thr, digits, hand],
                [status, table, flagged, log, r_page, r_pages, r_n, r_fixes, r_table,
                 r_msg])

        with gr.Tab("Prepare exam"):
            gr.Markdown("Make the key from the exam LaTeX (or a CSV), and the sheets to print.")
            with gr.Row():
                with gr.Column():
                    p_keys = gr.File(label="Exam .tex (and its .aux) or key CSV",
                                     file_count="multiple", file_types=[".tex", ".aux", ".csv"])
                    p_id = gr.Textbox(label="Key ID (3 digits, bubbled on the sheets)",
                                      placeholder="e.g. 322")
                    p_cancel = gr.Textbox(label="Cancel questions (optional)",
                                          placeholder="5,12")
                with gr.Column():
                    p_exam = gr.Textbox(label="Exam name", placeholder="Exam 2")
                    p_course = gr.Textbox(label="Course", placeholder="CSE4001 Fall 2026")
                    p_key_sheet = gr.Checkbox(label="Key sheet (answers bubbled)", value=True)
                    p_student_sheet = gr.Checkbox(label="Student sheet to print", value=True)
            p_go = gr.Button("Make key and sheets", variant="primary")
            p_status = gr.Markdown()
            with gr.Row():
                p_open = gr.Button("Open exam folder")
                p_msg = gr.Markdown()
            p_table = gr.Dataframe(headers=["version", "questions", "answers"],
                                   interactive=False)
            with gr.Accordion("Grader output", open=False):
                p_log = gr.Textbox(lines=10, show_label=False)

            p_go.click(prepare, [p_keys, p_id, p_exam, p_course, p_cancel, exam,
                                 p_key_sheet, p_student_sheet],
                       [p_status, p_table, p_log])
            p_open.click(lambda d: open_path(exam_dir(d)), exam, p_msg)
    return app


def main():
    build().launch(server_name="127.0.0.1", inbrowser="--no-browser" not in sys.argv,
                   share=False, footer_links=[], head=DARK)


if __name__ == "__main__":
    main()
