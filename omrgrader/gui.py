"""A local browser window for the grader: omr-grader-gui (or python gui.py)

Runs the grader with the options picked in the form. The page is served only
to this computer (127.0.0.1), nothing is shared, and Gradio's usage statistics are
off. Uploaded files are copied to a temporary folder for each run and deleted
after it; results are written to the output folder you choose.

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
        if files_of(corrections):
            args += ["--corrections", files_of(corrections)[0]]
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
    return (summary(rows, log) + f"\n\nResults are in `{out}`.",
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


def build():
    with gr.Blocks(title="Bubble-sheet grader", delete_cache=(1800, 1800)) as app:
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
