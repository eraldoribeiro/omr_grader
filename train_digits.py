"""Train the handwritten-digit reader used for the Student ID boxes.

python train_digits.py MNIST_RAW_DIR [--epochs 8]

MNIST_RAW_DIR holds the four MNIST idx files (train/t10k images and labels,
optionally .gz). Each digit is pasted into a simulated scanned ID box and run
through the same clean-up the grader applies to real boxes, so the network
trains on what it will see. Needs PyTorch (training only); the weights are
saved to digits_model.npz for the NumPy reader in handwriting.py.
"""

import argparse
import gzip
from pathlib import Path

import numpy as np
import torch
import torch.nn as nn

import handwriting as hw


def read_idx(path):
    path = Path(path)
    if not path.exists():
        path = path.with_name(path.name + ".gz")
    data = (gzip.open if path.suffix == ".gz" else open)(path, "rb").read()
    ndim = data[3]
    shape = [int.from_bytes(data[4 + 4 * i:8 + 4 * i], "big") for i in range(ndim)]
    return np.frombuffer(data, np.uint8, offset=4 + 4 * ndim).reshape(shape)


def synthesize(images, labels, copies, rng):
    xs, ys = [], []
    for _ in range(copies):
        for img, lab in zip(images, labels):
            x = hw.to_mnist(hw.synth_patch(img, rng))
            if x is not None:
                xs.append(x)
                ys.append(lab)
    return np.stack(xs)[:, None].astype(np.float32), np.array(ys, np.int64)


def make_model():
    return nn.Sequential(
        nn.Conv2d(1, 16, 3), nn.ReLU(), nn.MaxPool2d(2),
        nn.Conv2d(16, 32, 3), nn.ReLU(), nn.MaxPool2d(2),
        nn.Flatten(), nn.Linear(32 * 5 * 5, 64), nn.ReLU(), nn.Dropout(0.2),
        nn.Linear(64, 10))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("mnist_dir")
    ap.add_argument("--epochs", type=int, default=8)
    ap.add_argument("--copies", type=int, default=2, help="simulated boxes per training digit")
    args = ap.parse_args()
    rng = np.random.default_rng(0)
    torch.manual_seed(0)
    d = Path(args.mnist_dir)

    tr_x, tr_y = synthesize(read_idx(d / "train-images-idx3-ubyte"),
                            read_idx(d / "train-labels-idx1-ubyte"), args.copies, rng)
    te_img, te_lab = read_idx(d / "t10k-images-idx3-ubyte"), read_idx(d / "t10k-labels-idx1-ubyte")
    te_x, te_y = synthesize(te_img, te_lab, 1, rng)
    empty = sum(hw.to_mnist(hw.synth_patch(None, rng)) is None for _ in range(2000))
    print(f"train {len(tr_x)}, test {len(te_x)} of {len(te_img)} boxes non-empty; "
          f"empty boxes read as empty: {empty}/2000")

    dev = "mps" if torch.backends.mps.is_available() else "cpu"
    model = make_model().to(dev)
    opt = torch.optim.Adam(model.parameters(), 1e-3)
    sched = torch.optim.lr_scheduler.CosineAnnealingLR(opt, args.epochs)
    X, Y = torch.from_numpy(tr_x), torch.from_numpy(tr_y)
    for ep in range(args.epochs):
        model.train()
        perm = torch.randperm(len(X))
        for i in range(0, len(X), 256):
            idx = perm[i:i + 256]
            loss = nn.functional.cross_entropy(model(X[idx].to(dev)), Y[idx].to(dev))
            opt.zero_grad()
            loss.backward()
            opt.step()
        sched.step()
        model.eval()
        with torch.no_grad():
            pr = torch.cat([model(torch.from_numpy(te_x[j:j + 2048]).to(dev)).softmax(1).cpu()
                            for j in range(0, len(te_x), 2048)]).numpy()
        print(f"epoch {ep + 1}: test accuracy {100 * (pr.argmax(1) == te_y).mean():.2f}%")

    conf, ok = pr.max(1), pr.argmax(1) == te_y
    for t in (0.5, 0.8, 0.9, 0.95, 0.99):
        m = conf >= t
        print(f"  confidence >= {t}: accepts {100 * m.mean():.1f}%, "
              f"accuracy {100 * ok[m].mean():.2f}%")

    sd = {k: v.detach().cpu().numpy() for k, v in model.state_dict().items()}
    out = Path(__file__).resolve().parent / "digits_model.npz"
    np.savez_compressed(out, c1w=sd["0.weight"], c1b=sd["0.bias"], c2w=sd["3.weight"],
                        c2b=sd["3.bias"], f1w=sd["7.weight"], f1b=sd["7.bias"],
                        f2w=sd["10.weight"], f2b=sd["10.bias"])
    r = hw.DigitReader(out)   # the NumPy reader must agree with PyTorch
    npy = np.array([r.probs(x[0]) for x in te_x[:500]])
    print(f"wrote {out} ({out.stat().st_size // 1024} KB); NumPy vs PyTorch max diff "
          f"{np.abs(npy - pr[:500]).max():.2e}")


if __name__ == "__main__":
    main()
