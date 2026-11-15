"""Learned re-ranking for the auto crop (no GUI).

Trained on the user's own corrected crops (learned_data.json: picture + the 4 corners the user finally accepted).
The finder produces several outline candidates; the old rule took the one with the best edge score, which often picked a
header / photo / barcode block INSIDE the page.  This learns what the accepted outlines have in common (they hold the other
good candidates, are big, differ in colour from what surrounds them, do not hug the border with every corner ...) and
re-orders the candidates with a tiny linear model (10 numbers, ~1 ms).  Features are always computed on a 128 px copy,
so the numbers mean the same for a phone photo and for a thumbnail.
"""
import numpy as np

try:
    import cv2
except Exception:                                    # pragma: no cover
    cv2 = None

ENABLED = True
FE = ["v", "vrel", "area", "contrast", "bdiff", "edr", "dev", "touch", "std_in", "holds"]
W = np.array([0.0481, 0.4199, 0.6052, 0.4479, -0.0028, -0.0148, -0.0473, -0.3763, 0.1707, 0.7854])
MU = np.array([0.1002, 0.6106, 0.4327, 0.6031, 0.1371, 0.5073, 0.1131, 0.3493, 0.3011, 0.7567])
SD = np.array([0.0914, 0.3006, 0.2615, 0.3584, 0.2349, 0.1646, 0.1271, 0.3226, 0.168, 0.28])
SIDE = 128


def _mask(q, h, w):
    m = np.zeros((h, w), np.uint8)
    cv2.fillPoly(m, [np.rint(np.asarray(q, np.float32)).astype(np.int32)], 1)
    return m


def _feats(rgb, lab, edge, q, m):
    h, w = rgb.shape[:2]
    q = np.asarray(q, np.float32)
    area = m.sum() / float(h * w)
    k = max(3, int(0.06 * max(h, w)) | 1)
    ring = cv2.dilate(m, np.ones((k, k), np.uint8)) - m
    inner = cv2.erode(m, np.ones((3, 3), np.uint8))
    ins = lab[inner > 0] if inner.sum() > 5 else lab[m > 0]
    out = lab[ring > 0] if ring.sum() > 5 else np.zeros((1, 3), np.float32)
    mi, mo = ins.mean(0), out.mean(0)
    contrast = float(np.linalg.norm(mi - mo)) / 100
    bdiff = float(mi[0] - mo[0]) / 255
    ed_in = float(edge[inner > 0].mean()) if inner.sum() else 0.0
    ed_out = float(edge[ring > 0].mean()) if ring.sum() else 0.0
    edr = ed_in / (ed_in + ed_out + 1e-3)
    dev = 0.0
    for i in range(4):
        a, b = q[i - 1] - q[i], q[(i + 1) % 4] - q[i]
        dev += abs(float(np.dot(a, b)) / (np.linalg.norm(a) * np.linalg.norm(b) + 1e-6))
    dev /= 4
    mg = 0.02 * max(h, w)
    touch = float(np.mean([(x < mg) or (y < mg) or (x > w - mg) or (y > h - mg) for x, y in q]))
    std_in = float(ins[:, 0].std()) / 100
    return [area, contrast, bdiff, edr, dev, touch, std_in]


def scores(rgb, cands):
    """rgb: the picture used to find the page; cands: [(score, quad in rgb pixels)].  Returns one learned score per candidate
    (higher = more like the outlines the user accepted), or None when it cannot be computed."""
    if not ENABLED or cv2 is None or len(cands) < 2:
        return None
    H, W_ = rgb.shape[:2]
    k = SIDE / float(max(H, W_))
    small = cv2.resize(rgb, (max(8, int(round(W_ * k))), max(8, int(round(H * k)))), interpolation=cv2.INTER_AREA)
    h, w = small.shape[:2]
    lab = cv2.cvtColor(small, cv2.COLOR_RGB2LAB).astype(np.float32)
    edge = (cv2.Canny(cv2.cvtColor(small, cv2.COLOR_RGB2GRAY), 60, 140) > 0).astype(np.float32)
    qs = [[(x * k, y * k) for x, y in q] for _, q in cands]
    ms = [_mask(q, h, w) for q in qs]
    vmax = max(float(v) for v, _ in cands) + 1e-9
    vrel = [float(v) / vmax for v, _ in cands]
    X = []
    for i, (v, _) in enumerate(cands):
        a, contrast, bdiff, edr, dev, touch, std_in = _feats(small, lab, edge, qs[i], ms[i])
        tot = n = 0.0
        for j in range(len(cands)):
            if j != i:
                tot += vrel[j] * float((ms[i] & ms[j]).sum()) / max(float(ms[j].sum()), 1.0)
                n += vrel[j]
        holds = tot / max(n, 1e-6)
        X.append([float(v), vrel[i], a, contrast, bdiff, edr, dev, touch, std_in, holds])
    return (((np.asarray(X) - MU) / SD) @ W).tolist()
