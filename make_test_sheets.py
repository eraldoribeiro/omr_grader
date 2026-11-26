"""Make synthetic filled sheets for testing omr_grader.py.

python make_test_sheets.py TEMPLATE.pdf OUTDIR
Writes OUTDIR/students.pdf, OUTDIR/key_sheet.pdf, OUTDIR/keys.csv, OUTDIR/truth.csv.
"""

import csv
import sys
from pathlib import Path

import cv2
import fitz
import numpy as np

import omr_grader as g

RNG = np.random.default_rng(0)
S = 1.5  # draw at 300 dpi (template coords are 200 dpi)


def render_template(path):
    pix = fitz.open(path)[0].get_pixmap(dpi=300, colorspace=fitz.csGRAY)
    return np.frombuffer(pix.samples, np.uint8).reshape(pix.h, pix.w).copy()


def fill(img, x, y, level=60, partial=1.0):
    """Pencil-like fill: irregular gray blob covering the bubble."""
    r = int(g.BUBBLE_R * S * partial)
    jitter = RNG.normal(0, 1.5, 2)
    cv2.ellipse(img, (int(x * S + jitter[0]), int(y * S + jitter[1])),
                (r, int(r * RNG.uniform(0.85, 1.0))), RNG.uniform(0, 180),
                0, 360, int(level), -1)


def erase(img, x, y):
    fill(img, x, y, level=205)  # erased mark leaves a light smudge
    cv2.circle(img, (int(x * S), int(y * S)), int(g.BUBBLE_R * S), 120, 2)


def set_version(img, version):
    for col, x in enumerate(g.KEY_X):  # white out the pre-printed key marks
        for y in g.DIGIT_Y:
            cv2.circle(img, (int(x * S), int(y * S)), int(g.BUBBLE_R * S) - 2, 255, -1)
        fill(img, x, g.DIGIT_Y[int(version[col])], level=0)


def distort(img, upside_down=False):
    h, w = img.shape
    src = np.float32([[0, 0], [w, 0], [0, h], [w, h]])
    dst = src + RNG.normal(0, 15, src.shape).astype(np.float32)
    dst += np.float32([40, 30])
    H = cv2.getPerspectiveTransform(src, dst)
    angle = RNG.uniform(-2, 2)
    R = np.vstack([cv2.getRotationMatrix2D((w / 2, h / 2), angle, 1.0), [0, 0, 1]])
    out = cv2.warpPerspective(img, R @ H, (w + 80, h + 60), borderValue=235)
    out = cv2.GaussianBlur(out, (3, 3), 0)
    out = np.clip(out.astype(np.float32) * 0.92 + RNG.normal(0, 8, out.shape), 0, 255)
    out = out.astype(np.uint8)
    if upside_down:
        out = cv2.rotate(out, cv2.ROTATE_180)
    return out


def to_pdf(images, path):
    doc = fitz.open()
    for im in images:
        im = cv2.resize(im, None, fx=1 / S, fy=1 / S, interpolation=cv2.INTER_AREA)  # 200 dpi
        ok, buf = cv2.imencode(".jpg", im, [cv2.IMWRITE_JPEG_QUALITY, 85])
        page = doc.new_page(width=595, height=842)
        page.insert_image(page.rect, stream=buf.tobytes())
    doc.save(path)


def main(template, outdir):
    outdir = Path(outdir)
    outdir.mkdir(parents=True, exist_ok=True)
    base = render_template(template)
    n = 40
    keys = {v: "".join(RNG.choice(list("ABCD"), n)) for v in ["322", "117"]}

    # Key sheet for version 117.
    key_img = base.copy()
    set_version(key_img, "117")
    for q, a in enumerate(keys["117"], 1):
        fill(key_img, *g.question_center(q, "ABCD".index(a)), level=20)
    to_pdf([distort(key_img)], outdir / "key_sheet.pdf")

    with open(outdir / "keys.csv", "w") as f:
        f.write("version,answers\n")
        for v, k in keys.items():
            f.write(f"{v},{k}\n")

    pages, truth = [], []
    for s in range(6):
        img = base.copy()
        version = "322" if s % 2 == 0 else "117"
        if version != "322":
            set_version(img, version)
        sid = "".join(str(d) for d in RNG.integers(0, 10, 6))
        for col, d in enumerate(sid):
            fill(img, g.ID_X[col], g.DIGIT_Y[int(d)], level=RNG.uniform(40, 110))
        answers = []
        for q in range(1, n + 1):
            correct = keys[version][q - 1]
            a = correct if RNG.random() < 0.7 else RNG.choice(list("ABCD"))
            if s == 1 and q == 5:       # blank
                answers.append("-")
                continue
            if s == 2 and q == 7:       # double mark
                fill(img, *g.question_center(q, 0)), fill(img, *g.question_center(q, 2))
                answers.append("*")
                continue
            if s == 3 and q == 9:       # erased answer, then a new one
                wrong = "ABCD"[("ABCD".index(a) + 1) % 4]
                fill(img, *g.question_center(q, "ABCD".index(wrong)))
                erase(img, *g.question_center(q, "ABCD".index(wrong)))
            # pencil darkness and sloppy partial fills vary by student
            fill(img, *g.question_center(q, "ABCD".index(a)),
                 level=RNG.uniform(30, 120), partial=RNG.uniform(0.8, 1.05))
            answers.append(a)
        score = sum(a == k for a, k in zip(answers, keys[version]))
        truth.append([sid, version, score, "".join(answers)])
        pages.append(distort(img, upside_down=(s == 4)))

    to_pdf(pages, outdir / "students.pdf")
    with open(outdir / "truth.csv", "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["student_id", "version", "score", "answers"])
        w.writerows(truth)


if __name__ == "__main__":
    main(sys.argv[1], sys.argv[2])
