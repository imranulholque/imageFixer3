"""Learned re-ranking for the auto crop (no GUI).

Trained on the user's own corrected crops (learned_data.json: picture + the 4 corners the user finally accepted).
The finder produces several outline candidates; the old rule took the one with the best edge score, which often picked a
header / photo / barcode block INSIDE the page.  This re-orders the candidates with a small linear model (20 numbers, a few ms).

v2: 20 features instead of 10 (added: how strong and steady the edge is along each side, whether the table around the outline
is even and the paper inside is even, how close the shape is to a sheet / card, how central it is, agreement with the other
candidates ...) and the weights are trained as a ranking (soft-max over the candidates of one picture, target = overlap with
the accepted crop), checked with cross-validation on 54 pictures: mean overlap with the accepted crop 0.79 (old edge score)
-> 0.85 on pictures the model had NOT seen.  Features are always computed on a 128 px copy, so the numbers mean the same
for a phone photo and for a thumbnail.  Re-train with train_croplearn.py after the Crop Learner has collected more samples.
"""
import numpy as np

try:
    import cv2
except Exception:                                    # pragma: no cover
    cv2 = None

ENABLED = True
FE = ["v", "vrel", "area", "arel", "contrast", "bdiff", "edr", "dev", "touch", "std_in", "holds", "iou_c", "side_min",
      "side_mean", "nfree", "ring_sd", "in_sd", "bright", "asp_pen", "cen"]
W = np.array([0.2738, 0.4405, 0.0844, 0.3872, 0.0107, -0.1375, 0.3140, -0.3730, -0.0861, -0.0327, 0.2912, 0.0645, 0.3089, 0.1783, 0.2027, -0.6317, -0.0938, 0.0123, -0.4244, 0.3848])
MU = np.array([0.0935, 0.5209, 0.4612, 0.6716, 0.5995, 0.1476, 0.5164, 0.1162, 0.4027, 0.3026, 0.7558, 0.6175, 0.5263, 0.8349, 0.7997, 0.1610, 0.1241, 0.6708, 0.1869, 0.8280])
SD = np.array([0.0989, 0.3059, 0.2714, 0.3004, 0.3593, 0.2271, 0.1653, 0.1223, 0.3609, 0.1630, 0.2782, 0.2408, 0.4016, 0.4169, 0.2421, 0.0759, 0.0654, 0.1384, 0.2933, 0.1660])
SIDE = 128


def _mask(q, h, w):
    m = np.zeros((h, w), np.uint8)
    cv2.fillPoly(m, [np.rint(np.asarray(q, np.float32)).astype(np.int32)], 1)
    return m


def _side_edge(gm, q, n=24):
    """(edge strength, share lying on the picture border) for each of the 4 sides."""
    h, w = gm.shape
    q = np.asarray(q, np.float32)
    mx = cv2.dilate(gm, np.ones((3, 3), np.uint8))               # 3x3 maximum: a side may be a pixel off
    t = np.linspace(0.08, 0.92, n)[:, None]
    out = []
    for i in range(4):
        a, b = q[i], q[(i + 1) % 4]
        p = a + (b - a) * t
        x = np.clip(np.rint(p[:, 0]).astype(int), 0, w - 1)
        y = np.clip(np.rint(p[:, 1]).astype(int), 0, h - 1)
        on = float(((x <= 1) | (x >= w - 2) | (y <= 1) | (y >= h - 2)).mean())
        out.append((float(mx[y, x].mean()), on))
    return out


def features(rgb, cands):
    """rgb: the picture used to find the page; cands: [(score, quad in rgb pixels)] -> (n, 20) feature matrix."""
    H, W_ = rgb.shape[:2]
    k = SIDE / float(max(H, W_))
    small = cv2.resize(rgb, (max(8, int(round(W_ * k))), max(8, int(round(H * k)))), interpolation=cv2.INTER_AREA)
    h, w = small.shape[:2]
    lab = cv2.cvtColor(small, cv2.COLOR_RGB2LAB).astype(np.float32)
    gray = cv2.cvtColor(small, cv2.COLOR_RGB2GRAY)
    gb = cv2.GaussianBlur(gray, (3, 3), 0)
    gm = (np.sqrt(cv2.Sobel(gb, cv2.CV_32F, 1, 0) ** 2 + cv2.Sobel(gb, cv2.CV_32F, 0, 1) ** 2) / 255.0).astype(np.float32)
    edge = (cv2.Canny(gray, 60, 140) > 0).astype(np.float32)
    qs = [[(x * k, y * k) for x, y in q] for _, q in cands]
    ms = [_mask(q, h, w) for q in qs]
    vmax = max(float(v) for v, _ in cands) + 1e-9
    vrel = [float(v) / vmax for v, _ in cands]
    areas = [float(m.sum()) / (h * w) for m in ms]
    amax = max(areas) + 1e-9
    kk = max(3, int(0.06 * max(h, w)) | 1)
    mg = 0.02 * max(h, w)
    X = []
    for i, (v, _) in enumerate(cands):
        m, q = ms[i], np.asarray(qs[i], np.float32)
        ring = cv2.dilate(m, np.ones((kk, kk), np.uint8)) - m
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
        for j in range(4):
            a, b = q[j - 1] - q[j], q[(j + 1) % 4] - q[j]
            dev += abs(float(np.dot(a, b)) / (np.linalg.norm(a) * np.linalg.norm(b) + 1e-6))
        dev /= 4
        touch = float(np.mean([(x < mg) or (y < mg) or (x > w - mg) or (y > h - mg) for x, y in q]))
        std_in = float(ins[:, 0].std()) / 100
        tot = n = iou_c = nn = 0.0
        for j in range(len(cands)):
            if j != i:
                inter = float((m & ms[j]).sum())
                tot += vrel[j] * inter / max(float(ms[j].sum()), 1.0)
                n += vrel[j]
                iou_c += vrel[j] * inter / max(float((m | ms[j]).sum()), 1.0)
                nn += vrel[j]
        holds, iou_c = tot / max(n, 1e-6), iou_c / max(nn, 1e-6)
        se = _side_edge(gm, q)
        free = np.array([a for a, o in se if o < 0.5])             # a side that lies on the picture border has no edge to show
        side_min = float(free.min()) if len(free) else 0.0
        side_mean = float(free.mean()) if len(free) else 0.0
        nfree = len(free) / 4.0
        ring_sd = float(out.std(0).mean()) / 100 if ring.sum() > 5 else 0.5
        in_sd = float(ins.std(0).mean()) / 100
        bright = float(mi[0]) / 255
        l1 = (np.linalg.norm(q[1] - q[0]) + np.linalg.norm(q[2] - q[3])) / 2
        l2 = (np.linalg.norm(q[3] - q[0]) + np.linalg.norm(q[2] - q[1])) / 2
        asp = max(l1, l2) / max(min(l1, l2), 1e-6)
        asp_pen = min(abs(np.log(asp / 1.414)), abs(np.log(asp / 1.585)), abs(np.log(asp)))     # A4 / ID card / square
        cen = 1 - np.hypot(q[:, 0].mean() / w - .5, q[:, 1].mean() / h - .5) * 1.4142
        X.append([float(v), vrel[i], areas[i], areas[i] / amax, contrast, bdiff, edr, dev, touch, std_in, holds, iou_c,
                  side_min, side_mean, nfree, ring_sd, in_sd, bright, asp_pen, cen])
    return np.asarray(X, np.float64)


def scores(rgb, cands):
    """rgb: the picture used to find the page; cands: [(score, quad in rgb pixels)].  Returns one learned score per candidate
    (higher = more like the outlines the user accepted), or None when it cannot be computed."""
    if not ENABLED or cv2 is None or len(cands) < 2:
        return None
    return (((features(rgb, cands) - MU) / SD) @ W).tolist()
