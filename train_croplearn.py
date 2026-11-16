"""Re-train the auto-crop ranking (croplearn.py) from the Crop Learner's samples.

    python train_croplearn.py [learned_data.json]

For every sample the finder is run on the stored thumbnail, its outline candidates are compared with the outline the user
accepted (overlap = IoU), and the 20 weights are trained so that the candidate with the best overlap gets the best score.
A 9-fold cross-validation (on pictures the model has not seen) is printed next to the old edge-score order, then the new
W / MU / SD are written into croplearn.py (the old file is kept as croplearn.py.bak).  Takes about half a minute.
"""
import base64, io, json, os, re, shutil, sys
import numpy as np
import cv2
from PIL import Image
from scipy.optimize import minimize

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import croplearn
import scanner

LAM, TAU, FOLDS = 3.0, 0.03, 9


def iou(q1, q2, W, H, side=256):
    k = side / max(W, H)
    w, h = max(8, int(W * k)), max(8, int(H * k))
    ms = []
    for q in (q1, q2):
        a = np.zeros((h, w), np.uint8)
        cv2.fillPoly(a, [np.rint(np.asarray(q, np.float32) * k).astype(np.int32)], 1)
        ms.append(a)
    return float((ms[0] & ms[1]).sum() / max((ms[0] | ms[1]).sum(), 1))


def collect(samples):
    rows, cur = [], {}
    real = croplearn.scores

    def spy(rgb, cands):
        cur["rgb"], cur["c"] = rgb, [(float(v), [tuple(map(float, p)) for p in q]) for v, q in cands]
        return None
    croplearn.scores = spy
    try:
        for s in samples:
            im = Image.open(io.BytesIO(base64.b64decode(s["image_thumb_jpg"]))).convert("RGB")
            k = scanner.FAST_SIDE / max(im.size)
            im = im.resize((max(8, round(im.width * k)), max(8, round(im.height * k))), Image.BICUBIC)
            cur.clear()
            try:
                scanner._detect_fast(im)
            except Exception:
                pass
            if len(cur.get("c", [])) < 2:
                continue
            rgb = cur["rgb"]
            sc = rgb.shape[1] / im.width                       # candidates are in the coordinates of the picture the finder used
            T = [(x * rgb.shape[1], y * rgb.shape[0]) for x, y in s["quad_norm"]]
            rows.append(dict(X=croplearn.features(rgb, cur["c"]),
                             iou=np.array([iou(q, T, rgb.shape[1], rgb.shape[0]) for _, q in cur["c"]])))
    finally:
        croplearn.scores = real
    return rows


def fit(rows, idx):
    Xs = np.vstack([rows[i]["X"] for i in idx])
    mu, sd = Xs.mean(0), Xs.std(0) + 1e-6
    Z = [(rows[i]["X"] - mu) / sd for i in idx]
    T = []
    for i in idx:
        t = np.exp((rows[i]["iou"] - rows[i]["iou"].max()) / TAU)
        T.append(t / t.sum())

    def f(w):
        L, g = 0.0, np.zeros_like(w)
        for z, t in zip(Z, T):
            s = z @ w
            s -= s.max()
            p = np.exp(s)
            p /= p.sum()
            L -= (t * np.log(p + 1e-12)).sum()
            g += z.T @ (p - t)
        n = len(Z)
        return L / n + LAM * (w @ w) / n, g / n + 2 * LAM * w / n
    return minimize(f, np.zeros(Xs.shape[1]), jac=True, method="L-BFGS-B").x, mu, sd


def main():
    path = sys.argv[1] if len(sys.argv) > 1 else os.path.join(HERE, "learned_data.json")
    samples = json.load(open(path, encoding="utf-8"))
    rows = collect(samples)
    n = len(rows)
    print("%d samples with at least 2 outline candidates" % n)
    if n < 15:
        print("too few samples to train safely - collect more corrected crops first")
        return
    first = np.array([r["iou"][0] for r in rows])
    best = np.array([r["iou"].max() for r in rows])
    got = np.zeros(n)
    for a in range(FOLDS):                                     # contiguous folds: neighbouring pictures are often the same page
        te = list(range(a * n // FOLDS, (a + 1) * n // FOLDS))
        w, mu, sd = fit(rows, [i for i in range(n) if i not in te])
        for i in te:
            got[i] = rows[i]["iou"][int(np.argmax(((rows[i]["X"] - mu) / sd) @ w))]
    print("mean overlap with your crops:  old edge-score order %.3f | new model (unseen pictures) %.3f | best possible %.3f"
          % (first.mean(), got.mean(), best.mean()))
    if got.mean() < first.mean():
        print("the new model is not better than the old order on unseen pictures - croplearn.py left unchanged")
        return
    w, mu, sd = fit(rows, list(range(n)))
    src = open(os.path.join(HERE, "croplearn.py"), encoding="utf-8").read()
    fmt = lambda a: "np.array([" + ", ".join("%.4f" % x for x in a) + "])"
    for name, v in (("W", w), ("MU", mu), ("SD", sd)):
        src, c = re.subn(r"^%s = np\.array\(\[.*?\]\)$" % name, "%s = %s" % (name, fmt(v)), src, flags=re.M)
        assert c == 1, name
    shutil.copy(os.path.join(HERE, "croplearn.py"), os.path.join(HERE, "croplearn.py.bak"))
    open(os.path.join(HERE, "croplearn.py"), "w", encoding="utf-8").write(src)
    print("croplearn.py updated (old one saved as croplearn.py.bak)")


if __name__ == "__main__":
    main()
