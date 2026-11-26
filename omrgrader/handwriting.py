"""Read handwritten digits from the Student ID boxes (fallback for blank bubbles).

Needs only NumPy and OpenCV. The network is trained by train_digits.py, which
saves its weights to digits_model.npz; inference here is plain NumPy.

Geometry is in template pixels at 200 dpi, the same frame as grader.py.
"""

from pathlib import Path

import cv2
import numpy as np

# ID boxes: six cells 16 pt wide drawn at x = 330.8 + 16 i pt, y = 73.6 to 87.6 pt.
# The 4-digit sheet shows cells 2-5 (above bubble columns 3-6).
_PX = 200 / 72
BOX_LEFT = [(330.8 + 16 * i) * _PX for i in range(6)]
BOX_W = 16 * _PX                       # ~44.4 px
BOX_TOP, BOX_BOTTOM = 73.6 * _PX, 87.6 * _PX   # ~204.4, ~243.3 px

# Patch cut around each box: a few px past the side/top lines, more below
# (digits written too big spill downward into the empty gap above the bubbles).
PAD_SIDE, PAD_TOP, PAD_BOTTOM = 5, 5, 12
MIN_INK = 30        # dark pixels needed for a box to count as written in
MODEL_PATH = Path(__file__).resolve().parent / "digits_model.npz"


def patch_geometry():
    """Patch size and the line positions inside it (x0, x1, y0, y1)."""
    w = int(round(BOX_W)) + 2 * PAD_SIDE
    h = int(round(BOX_BOTTOM - BOX_TOP)) + PAD_TOP + PAD_BOTTOM
    lines = (PAD_SIDE, PAD_SIDE + BOX_W, PAD_TOP, PAD_TOP + (BOX_BOTTOM - BOX_TOP))
    return (h, w), lines


def cut_patch(warped, cell):
    """Grayscale patch around ID box `cell` (0-5) of an aligned sheet."""
    (h, w), _ = patch_geometry()
    x0 = int(round(BOX_LEFT[cell])) - PAD_SIDE
    y0 = int(round(BOX_TOP)) - PAD_TOP
    return warped[y0:y0 + h, x0:x0 + w]


def to_mnist(patch):
    """Clean a box patch into a 28x28 MNIST-style image in [0, 1], or None if empty.

    Removes the box lines and anything outside the box (except below it), drops
    specks, then scales the ink to fit 20x20 and centers it by mass, as MNIST does.
    """
    _, (lx0, lx1, ly0, ly1) = patch_geometry()
    p = patch.astype(np.float32)
    white = max(np.percentile(p, 90), 1.0)
    d = np.clip(1.0 - p / white, 0, 1)
    h, w = d.shape
    dark = d > 0.2

    # Find the printed box lines near where they should be: rows dark across the
    # patch width, columns dark across the box height. Fall back to the expected
    # positions when a line is not found (e.g. hidden under heavy writing).
    def find(profile, expect, axis_len):
        lo, hi = max(int(expect) - 7, 0), min(int(expect) + 8, axis_len)
        hits = [i for i in range(lo, hi) if profile[i] > 0.6]
        return (min(hits), max(hits)) if hits else (int(round(expect)) - 1, int(round(expect)) + 1)

    rows = dark.mean(axis=1)
    y0a, y0b = find(rows, ly0, h)
    y1a, y1b = find(rows, ly1, h)
    cols = dark[int(ly0):int(ly1) + 1].mean(axis=0)
    x0a, x0b = find(cols, lx0, w)
    x1a, x1b = find(cols, lx1, w)

    keep = np.zeros_like(dark)
    keep[y0b + 2:, x0b + 2:x1a - 1] = True      # inside the box, plus below it
    keep[y1a - 1:y1b + 2, :] = False            # the bottom line itself
    d = np.where(keep, d, 0)
    d[d < 0.15] = 0

    n, lab, stats, _ = cv2.connectedComponentsWithStats((d > 0).astype(np.uint8), 8)
    for i in range(1, n):
        if stats[i, cv2.CC_STAT_AREA] < 6:
            d[lab == i] = 0
    if (d > 0).sum() < MIN_INK:
        return None

    ys, xs_ = np.nonzero(d)
    d = d[ys.min():ys.max() + 1, xs_.min():xs_.max() + 1]
    s = 20.0 / max(d.shape)
    d = cv2.resize(d, (max(1, round(d.shape[1] * s)), max(1, round(d.shape[0] * s))),
                   interpolation=cv2.INTER_AREA)
    out = np.zeros((28, 28), np.float32)
    yy, xx = np.mgrid[:d.shape[0], :d.shape[1]]
    cy, cx = (yy * d).sum() / d.sum(), (xx * d).sum() / d.sum()   # center of mass
    oy = int(round(14 - cy)); ox = int(round(14 - cx))
    oy = min(max(oy, 0), 28 - d.shape[0]); ox = min(max(ox, 0), 28 - d.shape[1])
    out[oy:oy + d.shape[0], ox:ox + d.shape[1]] = d
    return out / max(out.max(), 1e-6)


def synth_patch(digit28, rng):
    """A simulated scanned box with an MNIST digit written in it (for training).

    Jittered box lines, varied size/position/pen width/ink, sometimes spilling
    past the box, then blur and noise.
    """
    (h, w), (lx0, lx1, ly0, ly1) = patch_geometry()
    bg = rng.uniform(215, 250)
    img = np.full((h, w), bg, np.float32)
    if digit28 is not None:
        g = digit28.astype(np.float32) / 255.0
        k = rng.integers(-1, 2)
        if k:
            op = cv2.dilate if k > 0 else cv2.erode
            g = op(g, np.ones((2, 2), np.uint8))
        ys, xs = np.nonzero(g > 0.1)
        if len(ys):
            g = g[ys.min():ys.max() + 1, xs.min():xs.max() + 1]
        th = rng.uniform(20, 42)                     # box interior is ~39 px tall
        s = th / g.shape[0]
        g = cv2.resize(g, (max(2, round(g.shape[1] * s * rng.uniform(0.8, 1.2))),
                           max(2, round(th))), interpolation=cv2.INTER_LINEAR)
        g = g[:, :int(lx1 - lx0) + 4]
        cx = (lx0 + lx1) / 2 + rng.normal(0, 3)
        cy = (ly0 + ly1) / 2 + rng.normal(0, 3) + (th - 30) * rng.uniform(0, 0.4)
        y0 = int(round(cy - g.shape[0] / 2)); x0 = int(round(cx - g.shape[1] / 2))
        ink = rng.uniform(0, 110)
        ya, yb = max(y0, 0), min(y0 + g.shape[0], h)
        xa, xb = max(x0, 0), min(x0 + g.shape[1], w)
        if ya < yb and xa < xb:
            stroke = bg - g[ya - y0:yb - y0, xa - x0:xb - x0] * (bg - ink)
            img[ya:yb, xa:xb] = np.minimum(img[ya:yb, xa:xb], stroke)
    lw = rng.uniform(1.0, 2.6)
    lc = rng.uniform(0, 120)
    jx, jy = rng.normal(0, 1.2, 2)
    for x in (lx0, lx1):
        cv2.line(img, (int(round(x + jx)), int(round(ly0 + jy))),
                 (int(round(x + jx)), int(round(ly1 + jy))), lc, max(1, int(round(lw))))
    for y in (ly0, ly1):
        cv2.line(img, (0, int(round(y + jy))), (w - 1, int(round(y + jy))), lc,
                 max(1, int(round(lw))))
    if rng.random() < 0.7:
        img = cv2.GaussianBlur(img, (3, 3), rng.uniform(0.3, 1.0))
    img += rng.normal(0, rng.uniform(2, 9), img.shape)
    return np.clip(img, 0, 255).astype(np.uint8)


# ---------------------------------------------------------------------------
# NumPy inference for the small CNN in train_digits.py
# ---------------------------------------------------------------------------
def _conv(x, w, b):
    win = np.lib.stride_tricks.sliding_window_view(x, w.shape[2:], axis=(1, 2))
    return np.einsum("chwij,ocij->ohw", win, w, optimize=True) + b[:, None, None]


def _pool(x):
    c, h, w = x.shape
    x = x[:, :h // 2 * 2, :w // 2 * 2]
    return x.reshape(c, h // 2, 2, w // 2, 2).max(axis=(2, 4))


class DigitReader:
    def __init__(self, path=MODEL_PATH):
        self.p = dict(np.load(path))

    def probs(self, img28):
        p = self.p
        x = img28[None].astype(np.float32)
        x = _pool(np.maximum(_conv(x, p["c1w"], p["c1b"]), 0))
        x = _pool(np.maximum(_conv(x, p["c2w"], p["c2b"]), 0))
        x = np.maximum(p["f1w"] @ x.reshape(-1) + p["f1b"], 0)
        z = p["f2w"] @ x + p["f2b"]
        e = np.exp(z - z.max())
        return e / e.sum()

    def read(self, warped, cells):
        """[(digit or None, confidence)] for each ID box cell."""
        out = []
        for cell in cells:
            img = to_mnist(cut_patch(warped, cell))
            if img is None:
                out.append((None, 0.0))
            else:
                pr = self.probs(img)
                out.append((int(pr.argmax()), float(pr.max())))
        return out


_reader = None


def reader():
    """Shared DigitReader, or None when the model file is missing."""
    global _reader
    if _reader is None and MODEL_PATH.exists():
        _reader = DigitReader()
    return _reader
