"""Editable nodes for a brush stroke (Color brush / Erase brush).

A stroke is reduced to a few nodes; a smooth curve (Catmull-Rom spline) runs through them.  Drag a node to reshape the
stroke, click on the curve to add a node, right-click a node to remove it.  Nothing here touches Qt, so it is easy to test.
Points are (x, y) in any pixel space.
"""
import math

import numpy as np

MIN_NODES = 2


def _arr(pts):
    return np.array([(float(x), float(y)) for x, y in pts], dtype=np.float64)


def _rdp_idx(a, eps):
    """Indexes of the points kept by Ramer-Douglas-Peucker (iterative, always keeps both ends)."""
    n = len(a)
    keep = np.zeros(n, dtype=bool)
    keep[0] = keep[-1] = True
    stack = [(0, n - 1)]
    while stack:
        i, j = stack.pop()
        if j <= i + 1:
            continue
        p, q = a[i], a[j]
        seg = q - p
        L = math.hypot(seg[0], seg[1])
        mid = a[i + 1:j]
        if L < 1e-9:
            d = np.hypot(mid[:, 0] - p[0], mid[:, 1] - p[1])
        else:
            d = np.abs(seg[0] * (mid[:, 1] - p[1]) - seg[1] * (mid[:, 0] - p[0])) / L
        k = int(np.argmax(d))
        if d[k] > eps:
            m = i + 1 + k
            keep[m] = True
            stack.append((i, m))
            stack.append((m, j))
    return np.nonzero(keep)[0]


def path_length(pts):
    a = _arr(pts)
    if len(a) < 2:
        return 0.0
    return float(np.hypot(*np.diff(a, axis=0).T).sum())


def is_closed(pts):
    """A stroke that ends where it started (a circle, a box ...)."""
    if len(pts) < 8:
        return False
    L = path_length(pts)
    a = _arr(pts)
    return L > 1e-6 and math.hypot(*(a[-1] - a[0])) < max(2.0, 0.04 * L)


def fit_nodes(pts, max_nodes=12):
    """Few nodes that follow the stroke.  Returns (nodes, closed)."""
    a = _arr(pts)
    if len(a) < 2:
        return [(float(a[0][0]), float(a[0][1]))] * 2 if len(a) == 1 else [], False
    closed = is_closed(pts)
    if closed:
        a = a[:-1]
    if len(a) < 3:
        return [tuple(map(float, p)) for p in a], False
    L = path_length(a)
    eps = max(0.6, L * 0.002)
    idx = _rdp_idx(a, eps)
    limit = max(MIN_NODES + (2 if closed else 0), int(max_nodes))
    guard = 0
    while len(idx) > limit and guard < 40:
        eps *= 1.35
        idx = _rdp_idx(a, eps)
        guard += 1
    if closed and len(idx) < 4:                          # a loop needs a few nodes to stay round
        sel = np.unique(np.linspace(0, len(a) - 1, 4).astype(int))
        idx = sel
    nodes = [(float(a[i][0]), float(a[i][1])) for i in idx]
    return nodes, closed


def sample(nodes, step=2.0, closed=False, with_index=False):
    """The smooth curve through the nodes as a polyline.  with_index=True also gives, for every point, the number of
    the segment (between node k and k+1) it belongs to."""
    n = len(nodes)
    if n == 0:
        return ([], []) if with_index else []
    if n == 1:
        pt = (float(nodes[0][0]), float(nodes[0][1]))
        return ([pt], [0]) if with_index else [pt]
    P = _arr(nodes)
    out, seg_of = [], []
    count = n if closed else n - 1
    for k in range(count):
        if closed:
            p0, p1, p2, p3 = P[(k - 1) % n], P[k % n], P[(k + 1) % n], P[(k + 2) % n]
        else:
            p0 = P[k - 1] if k > 0 else 2 * P[0] - P[1]
            p1, p2 = P[k], P[k + 1]
            p3 = P[k + 2] if k + 2 < n else 2 * P[-1] - P[-2]
        chord = float(np.hypot(*(p2 - p1)))
        m = max(2, int(math.ceil(chord / max(0.5, step))))
        t = np.linspace(0.0, 1.0, m, endpoint=False)[:, None]
        t2, t3 = t * t, t * t * t
        pts = 0.5 * ((2 * p1) + (-p0 + p2) * t + (2 * p0 - 5 * p1 + 4 * p2 - p3) * t2 + (-p0 + 3 * p1 - 3 * p2 + p3) * t3)
        for q in pts:
            out.append((float(q[0]), float(q[1])))
            seg_of.append(k)
    end = P[0] if closed else P[-1]
    out.append((float(end[0]), float(end[1])))
    seg_of.append(count - 1)
    return (out, seg_of) if with_index else out


def hit_node(nodes, p, r):
    """Index of the node within r of p (the nearest one), else None."""
    best, bi = r, None
    for i, (x, y) in enumerate(nodes):
        d = math.hypot(x - p[0], y - p[1])
        if d <= best:
            best, bi = d, i
    return bi


def hit_curve(nodes, p, r, closed=False, step=2.0):
    """(segment, point) where the curve passes within r of p, else None."""
    pts, seg = sample(nodes, step, closed, True)
    if not pts:
        return None
    a = _arr(pts)
    d = np.hypot(a[:, 0] - p[0], a[:, 1] - p[1])
    k = int(np.argmin(d))
    if d[k] <= r:
        return seg[k], pts[k]
    return None


def insert_node(nodes, seg, pt):
    """A new node on segment `seg` (between node seg and seg+1)."""
    out = list(nodes)
    out.insert(seg + 1, (float(pt[0]), float(pt[1])))
    return out


def remove_node(nodes, i, closed=False):
    """The node i is removed (never below the least a stroke needs)."""
    least = 3 if closed else MIN_NODES
    if len(nodes) <= least or not (0 <= i < len(nodes)):
        return list(nodes)
    return [p for k, p in enumerate(nodes) if k != i]
