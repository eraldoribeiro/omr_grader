"""MCP server for the grader: lets a local assistant (LM Studio, Goose, ...) run it.

omr-grader-mcp   (or python -m omrgrader.mcp_server)

The assistant gets a few tools that run the same grader as the command line and
the window, on exam folders on this computer. They can read results and write
corrections.csv, but cannot change a score directly or delete anything.
Relative folder names are resolved against OMR_EXAMS_DIR (default: the folder
the server was started from).

Needs the MCP Python SDK: pip install "omr-grader[mcp]" (or pip install mcp).
"""

import csv
import os
import platform
import shlex
import subprocess
import sys
import tempfile
from pathlib import Path

from mcp.server.mcpserver import MCPServer
from mcp.server.mcpserver.exceptions import ToolError

from . import grader as g

PKG_PARENT = Path(__file__).resolve().parent.parent
CORRECTIONS = "corrections.csv"

server = MCPServer(
    name="omr-grader",
    instructions=(
        "Grades scanned bubble-sheet exams. Each exam has a folder holding the scans "
        "(PDF), the key (key.csv or the exam .tex), optionally a Canvas gradebook export "
        "(CSV) and a corrections.csv; results go to its graded/ subfolder. Start with "
        "list_exam_files to see what is there. After grading, use show_flagged to list "
        "the sheets that need a look, and set_correction to fix an answer, ID or version "
        "the grader misread, then grade_exam again. Never invent scores: report what the "
        "tools return."),
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def exam_dir(folder):
    d = Path(folder.strip().strip("'\"") or ".").expanduser()
    if not d.is_absolute():
        d = Path(os.environ.get("OMR_EXAMS_DIR") or Path.cwd()) / d
    if not d.is_dir():
        raise ToolError(f"no folder {d}")
    return d


def run_grader(args):
    """Run the grader; return (command line, output, ok)."""
    env = dict(os.environ)
    env["PYTHONPATH"] = os.pathsep.join(filter(None, [str(PKG_PARENT), env.get("PYTHONPATH")]))
    p = subprocess.run([sys.executable, "-m", "omrgrader.grader"] + args,
                       capture_output=True, text=True, env=env)
    log = (p.stdout + ("\n" + p.stderr if p.stderr.strip() else "")).strip()
    return shlex.join(["omr-grader"] + args), log, p.returncode == 0


def is_gradebook(path):
    try:
        with open(path, newline="", encoding="utf-8-sig") as f:
            return "SIS User ID" in next(csv.reader(f), [])
    except OSError:
        return False


def find_files(d):
    """Guess the role of each file in an exam folder."""
    roles = {"scans": [], "key": [], "gradebook": [], "other": []}
    for f in sorted(d.iterdir()):
        if f.is_dir() or f.name.startswith("."):
            continue
        name, suffix = f.name.lower(), f.suffix.lower()
        if f.name == CORRECTIONS:
            continue
        if suffix == ".pdf" and not any(w in name for w in ("key_sheet", "student_sheet")):
            roles["scans"].append(f.name)
        elif suffix == ".tex" or name == "key.csv" or name == "keys.csv":
            roles["key"].append(f.name)
        elif suffix == ".csv" and is_gradebook(f):
            roles["gradebook"].append(f.name)
        else:
            roles["other"].append(f.name)
    return roles


def results(d):
    path = d / "graded" / "results.csv"
    if not path.exists():
        raise ToolError(f"no results in {d / 'graded'} yet; run grade_exam first")
    with open(path, newline="") as f:
        return list(csv.DictReader(f))


def describe(r):
    """One line about a graded sheet."""
    who = r.get("name") or f"ID {r.get('student_id') or '?'}"
    if r.get("score") in (None, ""):
        score = "not graded"
    else:
        score = f"{r['score']}/{r['total']} ({r['percent']}%)"
    flags = f"; flags: {r['flags']}" if r.get("flags") else ""
    return f"{r['page']}: {who}, version {r.get('version') or '?'}, {score}{flags}"


def num_questions(d):
    """Questions in the exam's key (graded/key.csv, else key.csv), or the sheet's 100."""
    for path in (d / "graded" / "key.csv", d / "key.csv"):
        if path.exists():
            lines = [ln for ln in path.read_text().splitlines()[1:] if "," in ln]
            if lines:
                return max(len(ln.split(",", 1)[1].strip()) for ln in lines)
    return len(g.ANS_X) * len(g.ANS_Y)


def load_fixes(d):
    path = d / CORRECTIONS
    if not path.exists():
        return {}
    try:
        return g.load_corrections(path, len(g.ANS_X) * len(g.ANS_Y), 1)
    except SystemExit as e:
        raise ToolError(f"{path} has an error: {e}") from None


def fix_rows(fixes):
    rows = []
    for page, fix in fixes.items():
        rows += [[page, str(q), a] for q, a in sorted(fix["answers"].items())]
        rows += [[page, w, fix[w]] for w in ("id", "version") if fix[w]]
    return rows


def write_fixes(path, fixes):
    with open(path, "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["page", "question", "answer"])
        w.writerows(fix_rows(fixes))


# ---------------------------------------------------------------------------
# Tools
# ---------------------------------------------------------------------------
@server.tool()
def list_exam_files(folder: str) -> str:
    """List an exam folder's files by role (scans, key, gradebook), and say whether it
    has been graded and has corrections. folder: the exam folder, e.g. "exam2"."""
    d = exam_dir(folder)
    roles = find_files(d)
    lines = [f"Exam folder: {d}"]
    for role, label in [("scans", "Scans (PDF)"), ("key", "Key (key.csv or .tex)"),
                        ("gradebook", "Canvas gradebook"), ("other", "Other files")]:
        lines.append(f"{label}: {', '.join(roles[role]) or 'none'}")
    lines.append(f"Corrections: {CORRECTIONS if (d / CORRECTIONS).exists() else 'none'}")
    graded = d / "graded" / "results.csv"
    lines.append(f"Graded: {'yes, results in graded/' if graded.exists() else 'not yet'}")
    return "\n".join(lines)


@server.tool()
def prepare_exam(folder: str, tex_file: str, key_id: str, exam_name: str = "",
                 course: str = "") -> str:
    """Before the exam: read the answer key from the exam's LaTeX file and write key.csv,
    key_sheet.pdf (the key bubbled in, for checking) and student_sheet.pdf (to print) in
    the exam folder. key_id: the 3-digit key ID students bubble, e.g. "322"."""
    d = exam_dir(folder)
    args = ["--key", str(d / tex_file), "--key-id", key_id, "--out", str(d),
            "--key-sheet", str(d / "key_sheet.pdf"),
            "--student-sheet", str(d / "student_sheet.pdf")]
    if exam_name:
        args += ["--exam-name", exam_name]
    if course:
        args += ["--course", course]
    command, log, ok = run_grader(args)
    head = "Done." if ok else "The grader stopped with an error."
    key = (d / "key.csv").read_text().strip() if ok else ""
    return f"{head}\nCommand: {command}\n\n{log}\n\nkey.csv:\n{key}".strip()


@server.tool()
def grade_exam(folder: str, scans: list[str] | str | None = None, key: str = "",
               gradebook: str = "", assignment: str = "", cancel: str = "",
               canceled: str = "drop", mark_threshold: float = 0.35) -> str:
    """Grade the scanned sheets of an exam folder; results go to its graded/ subfolder.
    Files are names in the folder; leave scans, key or gradebook empty to use the only
    one of that kind in the folder (for the key, key.csv when there is one). gradebook (a Canvas export) needs assignment, the
    column to fill, e.g. "Exam 2". cancel: questions to cancel, e.g. "5,12";
    canceled: "drop" (left out of the total) or "credit" (everyone gets the point).
    The folder's corrections.csv, if any, is applied."""
    d = exam_dir(folder)
    roles = find_files(d)

    def pick(given, role, what):
        if given:
            return [given] if isinstance(given, str) else list(given)
        if len(roles[role]) == 1:
            return roles[role]
        if not roles[role]:
            raise ToolError(f"no {what} found in {d}")
        raise ToolError(f"several {what} in {d}: {', '.join(roles[role])}; say which")

    args = [str(d / s) for s in pick(scans, "scans", "scan PDFs")]
    if not key and "key.csv" in roles["key"]:
        key = "key.csv"   # the prepared key, made from the .tex by prepare_exam
    args += ["--key", str(d / pick(key, "key", "keys")[0]), "--out", str(d / "graded"),
             "--canceled", canceled, "--mark-threshold", f"{mark_threshold:g}"]
    if cancel:
        args += ["--cancel", cancel.replace(" ", "")]
    if gradebook or (assignment and roles["gradebook"]):
        if not assignment:
            raise ToolError("a gradebook needs assignment, the column to fill")
        args += ["--canvas", str(d / pick(gradebook, "gradebook", "gradebooks")[0]),
                 "--canvas-assignment", assignment]
    if (d / CORRECTIONS).exists():
        args += ["--corrections", str(d / CORRECTIONS)]
    command, log, ok = run_grader(args)
    if not ok:
        return f"The grader stopped with an error.\nCommand: {command}\n\n{log}"

    rows = results(d)
    graded = [r for r in rows if r.get("score") not in (None, "")]
    flagged = [r for r in rows if r.get("flags")]
    pct = sorted(float(r["percent"]) for r in graded if r.get("percent"))
    lines = [f"Graded {len(graded)} of {len(rows)} sheets; {len(flagged)} flagged.",
             f"Command: {command}"]
    if pct:
        mid = len(pct) // 2
        median = pct[mid] if len(pct) % 2 else (pct[mid - 1] + pct[mid]) / 2
        lines.append(f"Mean {sum(pct) / len(pct):.1f}%, median {median:.1f}%, "
                     f"lowest {pct[0]:.1f}%, highest {pct[-1]:.1f}%.")
    lines += [ln for ln in log.splitlines() if ln.startswith(("canvas:", "warning", "  warning"))]
    if (d / CORRECTIONS).exists():
        lines.append(f"Corrections applied from {CORRECTIONS}.")
    if flagged:
        lines.append("Flagged sheets:")
        lines += ["- " + describe(r) for r in flagged]
    lines.append(f"Files: {d / 'graded'} (results.csv, review.pdf, item_analysis.csv"
                 + (", canvas_import.csv, absent.csv" if "--canvas" in args else "") + ")")
    return "\n".join(lines)


@server.tool()
def show_flagged(folder: str) -> str:
    """List the graded sheets that have flags (blank, faint or multiple marks, ID
    problems, corrections), with their scores."""
    rows = [r for r in results(exam_dir(folder)) if r.get("flags")]
    return "\n".join(describe(r) for r in rows) or "No sheets are flagged."


@server.tool()
def show_results(folder: str, student: str = "") -> str:
    """Show the graded sheets, or only those whose name, student ID or page label
    contains student. Each line: page, student, version, score, flags."""
    rows = results(exam_dir(folder))
    s = student.strip().lower()
    if s:
        rows = [r for r in rows if s in " ".join(
            [r.get("name", ""), r.get("student_id", ""), r.get("sis_user_id", ""),
             r["page"]]).lower()]
    return "\n".join(describe(r) for r in rows) or f"No sheet matches {student!r}."


@server.tool()
def hardest_questions(folder: str, count: int = 10) -> str:
    """The questions the fewest students got right, per version (from
    item_analysis.csv). A very low percentage can mean a wrong key or an unclear
    question."""
    path = exam_dir(folder) / "graded" / "item_analysis.csv"
    if not path.exists():
        raise ToolError("no item analysis yet; run grade_exam first")
    with open(path, newline="") as f:
        rows = [r for r in csv.DictReader(f) if r["percent_correct"] not in ("", None)]
    rows.sort(key=lambda r: float(r["percent_correct"]))
    return "\n".join(f"Version {r['version']}, Q{r['question']} (key {r['key']}): "
                     f"{r['percent_correct']}% correct" for r in rows[:count])


@server.tool()
def set_correction(folder: str, page: str, question: str, answer: str) -> str:
    """Record a correction in the exam folder's corrections.csv; it is applied the next
    time grade_exam runs. page: the label from the results, e.g. "scans.pdf#p2".
    question: a question number with answer A-D (or "-" to clear a stray mark); "id"
    with the student ID (last digits, or the full SIS User ID); or "version" with the
    3-digit key ID. An empty answer removes that correction."""
    d = exam_dir(folder)
    fixes = load_fixes(d)
    what, value = question.strip().lower(), answer.strip().upper()
    if value:
        # validate the one line with the grader's own reader
        with tempfile.NamedTemporaryFile("w", suffix=".csv", delete=False) as t:
            t.write(f"{page},{what},{value}\n")
        try:
            new = g.load_corrections(t.name, num_questions(d), 1)[page]
        except SystemExit as e:
            raise ToolError(str(e).replace(t.name + ", line 1: ", "")) from None
        finally:
            os.unlink(t.name)
        fix = fixes.setdefault(page, {"answers": {}, "id": None, "version": None})
        fix["answers"].update(new["answers"])
        fix["id"] = new["id"] or fix["id"]
        fix["version"] = new["version"] or fix["version"]
        done = f"Set {page} {what} to {value}."
    else:
        fix = fixes.get(page)
        if fix is None:
            return f"No corrections for {page}."
        if what in ("id", "version"):
            fix[what] = None
        else:
            fix["answers"].pop(int(what) if what.isdigit() else what, None)
        if not g.fix_count(fix):
            del fixes[page]
        done = f"Removed the correction for {page} {what}."
    write_fixes(d / CORRECTIONS, fixes)
    return f"{done} {CORRECTIONS} now has {len(fix_rows(fixes))} corrections; " \
           f"run grade_exam to apply them."


@server.tool()
def show_corrections(folder: str) -> str:
    """List the corrections in the exam folder's corrections.csv."""
    rows = fix_rows(load_fixes(exam_dir(folder)))
    return "\n".join(f"{p}: {q} = {a}" for p, q, a in rows) or "No corrections."


@server.tool()
def open_review(folder: str) -> str:
    """Open the graded review.pdf (each sheet with its answers marked) in the
    computer's PDF viewer, for the instructor to look at."""
    path = exam_dir(folder) / "graded" / "review.pdf"
    if not path.exists():
        raise ToolError("no review.pdf yet; run grade_exam first")
    if platform.system() == "Darwin":
        subprocess.Popen(["open", str(path)])
    elif platform.system() == "Windows":
        os.startfile(str(path))  # noqa: S606
    else:
        subprocess.Popen(["xdg-open", str(path)])
    return f"Opened {path}."


def main():
    server.run()


if __name__ == "__main__":
    main()
