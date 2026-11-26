"""Match graded sheets to a Canvas gradebook export and write a grade-import CSV.

Students write the last digits of their SIS User ID (university student number)
on the sheet; those digits are matched against the gradebook's SIS User ID column.
"""

import csv
import re
from collections import defaultdict

IDENTITY = ["Student", "ID", "SIS User ID", "SIS Login ID", "Section"]


class Gradebook:
    def __init__(self, path, assignment, id_digits):
        with open(path, newline="", encoding="utf-8-sig") as f:
            rows = list(csv.reader(f))
        if not rows:
            raise ValueError(f"{path} is empty")
        self.header = header = rows[0]
        missing = [c for c in IDENTITY if c not in header]
        if missing:
            raise ValueError(f"{path} is not a Canvas gradebook export (no {missing})")

        # Assignment column: "Exam 2" matches "Exam 2 (5805405)".
        def bare(h):
            return re.sub(r"\s*\(\d+\)\s*$", "", h).strip().lower()
        cands = [h for h in header if bare(h) == assignment.strip().lower()]
        if len(cands) != 1:
            names = [h for h in header if re.search(r"\(\d+\)\s*$", h)]
            raise ValueError(f"no single gradebook column named {assignment!r}; "
                             f"assignments are: {', '.join(names)}")
        self.column = cands[0]
        col = header.index(self.column)

        self.points = None
        self.students = []
        for r in rows[1:]:
            r = r + [""] * (len(header) - len(r))
            name = r[0].strip()
            if name.lower() == "points possible":
                self.points = float(r[col])
                continue
            sis = re.sub(r"\D", "", r[header.index("SIS User ID")])
            if not name or not sis:   # blank, "Manual Posting" and Test Student rows
                continue
            self.students.append(dict(zip(header, r)))
        if not self.points:
            raise ValueError(f"no Points Possible for {self.column!r} in {path}")

        self.id_digits = id_digits
        self.by_key = defaultdict(list)
        for s in self.students:
            self.by_key[self.key(s)].append(s)
        self.shared = {k: v for k, v in self.by_key.items() if len(v) > 1}

    def key(self, student):
        """The digits a student writes on the sheet: last id_digits of the SIS ID."""
        return re.sub(r"\D", "", student["SIS User ID"])[-self.id_digits:]

    def match(self, sid):
        """Students whose key fits sid ('?' matches any digit)."""
        if len(sid) != self.id_digits:
            return []
        return [s for k, group in self.by_key.items()
                if all(c in ("?", d) for c, d in zip(sid, k)) for s in group]

    def resolve(self, sid, handwritten=None):
        """Return (student or None, ID used, flags) for an ID read from a sheet."""
        m = self.match(sid)
        names = lambda ss: "; ".join(s["Student"] for s in ss)
        if "?" not in sid:
            if len(m) == 1:
                return m[0], sid, []
            if len(m) > 1:
                return None, sid, [f"ID {sid} belongs to {len(m)} students ({names(m)}); "
                                   f"enter this grade by hand"]
            alt = self.match(handwritten) if handwritten and "?" not in handwritten else []
            if handwritten != sid and len(alt) == 1:
                return alt[0], handwritten, [
                    f"bubbled ID {sid} not in gradebook; used handwritten {handwritten} "
                    f"({alt[0]['Student']}), check"]
            return None, sid, [f"ID {sid} not in gradebook"]
        if set(sid) == {"?"}:
            return None, sid, ["no readable student ID"]
        if len(m) == 1:
            k = self.key(m[0])
            return m[0], k, [f"ID {sid} completed from gradebook as {k} "
                             f"({m[0]['Student']}), check"]
        return None, sid, [f"ID {sid} matches {len(m)} students in gradebook"
                           + (f" ({names(m)})" if 1 < len(m) <= 4 else "")]

    def write_import(self, path, grades):
        """Canvas import file: identity columns plus the assignment, one row per
        student in `grades` ({SIS User ID: points})."""
        with open(path, "w", newline="") as f:
            w = csv.writer(f)
            w.writerow(IDENTITY + [self.column])
            for s in self.students:
                if s["SIS User ID"] in grades:
                    w.writerow([s[c] for c in IDENTITY] + [f"{grades[s['SIS User ID']]:.2f}"])
