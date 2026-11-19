"""Brush stroke clean-up: smoothing + snap to a clean shape when the stroke is close to one.

Points are (x, y) tuples in any pixel space.  Nothing here touches Qt, so it is easy to test.
"""
import math

import numpy as np


def _arr(pts):
    return np.array([(float(x), float(y)) for x, y in pts], dtype=np.float64)


def resample(pts, step):
    """Even spacing along the stroke (keeps the first and last point)."""
    a = _arr(pts)
    if len(a) < 2:
        return a
    seg = np.hypot(*np.diff(a, axis=0).T)
    d = np.concatenate([[0.0], np.cumsum(seg)])
    total = d[-1]
    if total < 1e-6:
        return a[:1]
    n = max(2, int(total / max(0.5, step)) + 1)
    t = np.linspace(0.0, total, n)
    return np.column_stack([np.interp(t, d, a[:, 0]), np.interp(t, d, a[:, 1])])


def smooth(pts, amount, step=2.0, scale=1.0):
    """amount 0..100.  0 = unchanged, 100 = very smooth.  The ends stay where the mouse started / stopped.
    scale = picture pixels per SCREEN pixel (1 / zoom): the smoothing is measured on the screen, so it looks the
    same whether the picture is zoomed in or out (before, zoomed-out pictures got almost no smoothing)."""
    if len(pts) < 3 or amount <= 0:
        return [(float(x), float(y)) for x, y in pts]
    a = resample(pts, step * max(0.05, scale))
    if len(a) < 4:
        return [(float(p[0]), float(p[1])) for p in a]
    sigma = max(0.01, amount / 100.0 * 10.0)               # in samples (1 sample = `step` px)
    half = int(math.ceil(sigma * 3))
    k = np.exp(-0.5 * (np.arange(-half, half + 1) / sigma) ** 2)
    k /= k.sum()
    h = min(half, len(a) - 1)
    head = 2 * a[0] - a[1:h + 1][::-1]                      # mirror around the end points so they do not shrink
    tail = 2 * a[-1] - a[-h - 1:-1][::-1]
    if h < half:                                            # very short stroke: pad by repeating the ends
        head = np.vstack([np.repeat(a[:1], half - h, axis=0), head])
        tail = np.vstack([tail, np.repeat(a[-1:], half - h, axis=0)])
    ext = np.vstack([head, a, tail])
    out = np.column_stack([np.convolve(ext[:, 0], k, mode="valid"), np.convolve(ext[:, 1], k, mode="valid")])
    out[0], out[-1] = a[0], a[-1]
    return [(float(p[0]), float(p[1])) for p in out]


def _dist_to_segments(p, poly):
    """Distance of every point in p to the closed polygon poly."""
    best = np.full(len(p), np.inf)
    for i in range(len(poly)):
        a, b = poly[i], poly[(i + 1) % len(poly)]
        ab = b - a
        l2 = float(ab @ ab) or 1e-9
        t = np.clip(((p - a) @ ab) / l2, 0.0, 1.0)
        best = np.minimum(best, np.hypot(*(p - (a + t[:, None] * ab)).T))
    return best


def _rdp(a, eps):
    if len(a) < 3:
        return a
    s, e = a[0], a[-1]
    se = e - s
    l = math.hypot(*se)
    if l < 1e-9:
        d = np.hypot(*(a - s).T)
    else:
        d = np.abs(se[0] * (a[:, 1] - s[1]) - se[1] * (a[:, 0] - s[0])) / l
    i = int(np.argmax(d))
    if d[i] > eps:
        return np.vstack([_rdp(a[:i + 1], eps)[:-1], _rdp(a[i:], eps)])
    return np.array([s, e])


def snap(pts, tol=1.0):
    """If the stroke is close to a line / circle / ellipse / triangle / rectangle, returns (kind, clean_points).
    Otherwise None.  tol scales how forgiving it is (1.0 = normal)."""
    if len(pts) < 8:
        return None
    a = resample(pts, 1.0)
    if len(a) < 12:
        return None
    n = 96
    a = resample(pts, max(0.5, float(np.sum(np.hypot(*np.diff(a, axis=0).T))) / n))
    seg = np.hypot(*np.diff(a, axis=0).T)
    length = float(seg.sum())
    mn, mx = a.min(axis=0), a.max(axis=0)
    diag = float(math.hypot(*(mx - mn)))
    if diag < 12 or length < 20:
        return None
    gap = float(math.hypot(*(a[0] - a[-1])))
    closed = gap < 0.22 * length

    if not closed:                                          # ---- straight line?
        se = a[-1] - a[0]
        l = math.hypot(*se)
        if l < 10:
            return None
        dev = np.abs(se[0] * (a[:, 1] - a[0, 1]) - se[1] * (a[:, 0] - a[0, 0])) / l
        if float(dev.max()) / l < 0.055 * tol and length / l < 1.12:
            return "line", [(float(a[0, 0]), float(a[0, 1])), (float(a[-1, 0]), float(a[-1, 1]))]
        return None

    # ---- closed shapes.  Close the loop first.
    best = None                                             # (error, kind, points)

    c = (mn + mx) / 2.0                                     # ellipse / circle (principal axes)
    cov = np.cov((a - a.mean(axis=0)).T)
    w, v = np.linalg.eigh(cov)
    ax = v[:, ::-1]                                         # major axis first
    uv = (a - c) @ ax
    lo, hi = uv.min(axis=0), uv.max(axis=0)
    cu = (lo + hi) / 2.0
    ra, rb = (hi - lo) / 2.0
    if ra > 3 and rb > 3:
        q = (uv - cu) / np.array([ra, rb])
        err = float(np.mean(np.abs(np.hypot(q[:, 0], q[:, 1]) - 1.0)))
        if err < 0.09 * tol:
            if min(ra, rb) / max(ra, rb) > 0.86:            # nearly round -> a real circle
                r = (ra + rb) / 2.0
                ra = rb = r
            t = np.linspace(0, 2 * math.pi, 97)
            e = np.column_stack([cu[0] + ra * np.cos(t), cu[1] + rb * np.sin(t)])
            e = e @ ax.T + c
            kind = "circle" if abs(ra - rb) < 1e-6 else "ellipse"
            best = (err, kind, [(float(p[0]), float(p[1])) for p in e])

    loop = np.vstack([a, a[:1]])                            # triangle / rectangle (or any 3-4 corner shape)
    corners = _rdp(loop, 0.045 * diag * tol)[:-1]
    if len(corners) >= 5:                                   # merge corners that are almost on a line
        corners = _rdp(loop, 0.07 * diag * tol)[:-1]
    if 3 <= len(corners) <= 4:
        perr = float(np.mean(_dist_to_segments(a, corners))) / diag
        if perr < 0.025 * tol and (best is None or perr * 3.2 < best[0]):
            poly = corners
            if len(poly) == 4:                              # make a rectangle when the angles are about 90 degrees
                ok = True
                for i in range(4):
                    p0, p1, p2 = poly[i - 1], poly[i], poly[(i + 1) % 4]
                    v1, v2 = p0 - p1, p2 - p1
                    cosang = float(v1 @ v2) / ((np.hypot(*v1) * np.hypot(*v2)) or 1e-9)
                    if abs(cosang) > 0.30:
                        ok = False
                        break
                if ok:
                    d1 = poly[1] - poly[0]
                    ang = math.atan2(d1[1], d1[0])
                    # keep the tilt, but make it upright if it is almost upright
                    if abs(math.degrees(ang) % 90) < 6 or abs(math.degrees(ang) % 90) > 84:
                        ang = 0.0
                    cc = poly.mean(axis=0)
                    ux, uy = np.array([math.cos(ang), math.sin(ang)]), np.array([-math.sin(ang), math.cos(ang)])
                    pr = np.column_stack([(poly - cc) @ ux, (poly - cc) @ uy])
                    x0, y0 = pr.min(axis=0)
                    x1, y1 = pr.max(axis=0)
                    poly = np.array([cc + ux * x + uy * y for x, y in ((x0, y0), (x1, y0), (x1, y1), (x0, y1))])
                    kind = "rectangle"
                else:
                    kind = "quad"
            else:
                kind = "triangle"
            best = (perr * 3.2, kind, [(float(p[0]), float(p[1])) for p in np.vstack([poly, poly[:1]])])
    if best is None:
        return None
    return best[1], best[2]


def clean(pts, smooth_amount=50, snap_on=True, tol=1.0, scale=1.0):
    """What the Color Brush should draw for the raw mouse points."""
    if snap_on:
        s = snap(pts, tol)
        if s is not None:
            return s[1]
    return smooth(pts, smooth_amount, scale=scale)
