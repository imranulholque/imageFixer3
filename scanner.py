"""Document scanning maths (no GUI): find the paper in a photo, and straighten it."""
import math

import numpy as np
from PIL import Image, ImageDraw, ImageFilter

import core

_DEBUG = None                # self-test hook: a list collects every candidate with its score
LABEL_SIDE = 280            # long side of the picture used to find the paper


def _otsu(g):
    hist = np.bincount(g.reshape(-1), minlength=256).astype(np.float64)
    total = hist.sum()
    sum_all = (np.arange(256) * hist).sum()
    wb = 0.0
    sb = 0.0
    best, thr = -1.0, 128
    for t in range(256):
        wb += hist[t]
        if wb == 0:
            continue
        wf = total - wb
        if wf == 0:
            break
        sb += t * hist[t]
        mb, mf = sb / wb, (sum_all - sb) / wf
        between = wb * wf * (mb - mf) ** 2
        if between > best:
            best, thr = between, t
    return thr


def _components(mask):
    """Connected regions of a boolean mask (4-neighbours). Returns (labels (h,w) int32, 0 = background)."""
    h, w = mask.shape
    big = h * w + 1
    lab = np.where(mask, np.arange(1, h * w + 1, dtype=np.int32).reshape(h, w), big).astype(np.int32)
    for _ in range(h + w + 10):
        new = lab.copy()
        np.minimum(new[1:], lab[:-1], out=new[1:])
        np.minimum(new[:-1], lab[1:], out=new[:-1])
        np.minimum(new[:, 1:], lab[:, :-1], out=new[:, 1:])
        np.minimum(new[:, :-1], lab[:, 1:], out=new[:, :-1])
        new = np.where(mask, new, big)
        # pointer jumping speeds up long thin shapes
        flat = new.reshape(-1)
        idx = np.where(flat < big, flat - 1, 0)
        jumped = np.where(flat < big, np.minimum(flat, flat[idx]), big)
        new = jumped.reshape(h, w)
        if np.array_equal(new, lab):
            break
        lab = new
    lab = np.where(mask, lab, 0)
    return lab


def _hull(points):
    pts = sorted(set(points))
    if len(pts) < 3:
        return pts

    def cross(o, a, b):
        return (a[0] - o[0]) * (b[1] - o[1]) - (a[1] - o[1]) * (b[0] - o[0])
    lo = []
    for p in pts:
        while len(lo) >= 2 and cross(lo[-2], lo[-1], p) <= 0:
            lo.pop()
        lo.append(p)
    up = []
    for p in reversed(pts):
        while len(up) >= 2 and cross(up[-2], up[-1], p) <= 0:
            up.pop()
        up.append(p)
    return lo[:-1] + up[:-1]


def _poly_area(p):
    return abs(sum(p[i][0] * p[(i + 1) % len(p)][1] - p[(i + 1) % len(p)][0] * p[i][1] for i in range(len(p)))) / 2.0


def _simplify_to_quad(hull):
    """Remove the least important corner of the convex outline until 4 remain (Visvalingam)."""
    p = list(hull)
    while len(p) > 4:
        best, bi = 1e18, 0
        for i in range(len(p)):
            a, b, c = p[i - 1], p[i], p[(i + 1) % len(p)]
            ar = abs((b[0] - a[0]) * (c[1] - a[1]) - (c[0] - a[0]) * (b[1] - a[1])) / 2.0
            if ar < best:
                best, bi = ar, i
        p.pop(bi)
    return p


def _refine_quad(quad, outline):
    """Fit a straight line to the outline pixels along each side and use the lines' crossings as corners
    (the simplified outline cuts corners; this puts them back)."""
    pts = np.array(outline, dtype=np.float64)
    q = np.array(quad, dtype=np.float64)
    lines = []
    for i in range(4):
        a, b = q[i], q[(i + 1) % 4]
        d = b - a
        L = np.hypot(*d)
        if L < 1e-6:
            return quad
        n = np.array([-d[1], d[0]]) / L
        dist = np.abs((pts - a) @ n)
        t = ((pts - a) @ d) / (L * L)
        sel = pts[(dist < max(2.5, 0.03 * L)) & (t > -0.05) & (t < 1.05)]
        if len(sel) < 6:
            return quad
        c = sel.mean(0)
        _, _, vt = np.linalg.svd(sel - c)
        lines.append((c, vt[0]))
    out = []
    for i in range(4):
        c1, d1 = lines[i - 1]
        c2, d2 = lines[i]
        M = np.array([d1, -d2]).T
        if abs(np.linalg.det(M)) < 1e-6:
            return quad
        t = np.linalg.solve(M, c2 - c1)
        out.append(tuple(c1 + t[0] * d1))
    return out



def _box_mean(a, k):
    """Mean over a k x k window (integral image, edges replicated)."""
    r = k // 2
    pa = np.pad(a.astype(np.float64), r + 1, mode="edge")
    c = pa.cumsum(0).cumsum(1)
    h, w = a.shape
    s = c[k:k + h, k:k + w] - c[:h, k:k + w] - c[k:k + h, :w] + c[:h, :w]
    return (s / float(k * k)).astype(np.float32)


def _morph(mask, grow, shrink=None):
    """Binary close (grow then shrink) / open with PIL's rank filters. Sizes are odd window widths."""
    im = Image.fromarray((mask * 255).astype(np.uint8))
    if grow:
        im = im.filter(ImageFilter.MaxFilter(grow))
    if shrink:
        im = im.filter(ImageFilter.MinFilter(shrink))
    return np.asarray(im) > 127


def _border_model_candidates(rgbf, lum, h, w, cands):
    """The photo's frame is nearly always table / wall / cloth: learn its colours (up to 3 groups) and mark every
    pixel that is clearly NOT like them.  Finds a sheet on a coloured, wooden or patterned surface even when the
    sheet is not brighter than the surface."""
    b = max(3, int(0.045 * min(h, w)))
    ring = np.zeros((h, w), bool)
    ring[:b] = ring[-b:] = True
    ring[:, :b] = ring[:, -b:] = True
    feat = np.stack([lum / 255.0, (rgbf[..., 0] - rgbf[..., 1]) / 128.0, (rgbf[..., 1] - rgbf[..., 2]) / 128.0], -1)
    feat = feat * np.array([1.0, 1.6, 1.6], np.float32)
    bp = feat[ring]
    if len(bp) < 50:
        return
    cen = [np.median(bp, 0)]
    for _ in range(2):                                                        # add the border colour farthest from the groups so far
        d = np.min([((bp - c) ** 2).sum(1) for c in cen], 0)
        far = bp[d > np.percentile(d, 85)]
        if len(far) < 30:
            break
        cen.append(np.median(far, 0))
    for _ in range(4):                                                        # a few k-means rounds on the border pixels
        dd = np.stack([((bp - c) ** 2).sum(1) for c in cen], 1)
        lab = dd.argmin(1)
        cen = [bp[lab == i].mean(0) if (lab == i).sum() > 20 else cen[i] for i in range(len(cen))]
    dd = np.stack([((bp - c) ** 2).sum(1) for c in cen], 1)
    lab = dd.argmin(1)
    # each group: its centre and how spread it is (a wavy wood is spread, a plain wall is not)
    sp = []
    for i in range(len(cen)):
        di = np.sqrt(dd[lab == i, i]) if (lab == i).sum() > 20 else np.array([0.05])
        sp.append(max(0.035, float(np.percentile(di, 92))))
    fl = feat.reshape(-1, 3)
    dist = np.min([np.sqrt(((fl - c) ** 2).sum(1)) / sp[i] for i, c in enumerate(cen)], 0).reshape(h, w)
    dist = _blur_u8(np.clip(dist * 20.0, 0, 255), 1.5) / 20.0
    d8 = np.clip(dist * 20.0, 0, 255).astype(np.uint8)
    t = _otsu(d8) / 20.0
    for thr in sorted({max(1.6, t), max(2.2, t * 1.4), 3.2}):
        m = dist > thr
        m = _morph(m, 5, 5)                                                   # close the writing
        m = _morph(m, None, 5)                                                # open: drop thin bits of clutter
        m = _morph(m, 5, None)
        _candidates(m, h, w, cands, "border")


def _smooth_candidates(lum, h, w, cands):
    """A sheet / card is smooth, a wooden table, cloth or a busy surface is not: regions with little fine texture."""
    G = _blur_u8(lum, 0.8)
    m1, m2 = _box_mean(G, 5), _box_mean(G * G, 5)
    sd = np.sqrt(np.maximum(m2 - m1 * m1, 0.0))
    for pct in (45, 60):
        t = float(np.percentile(sd, pct))
        for k in (9, 15):
            m = sd < max(t, 3.0)
            m = _morph(m, k, k + 2)
            m = _morph(m, None, 7)
            m = _morph(m, 7, None)
            _candidates(m, h, w, cands, "smooth")


def _score(mask):
    h, w = mask.shape
    lab = _components(mask)
    counts = np.bincount(lab.reshape(-1))
    counts[0] = 0
    if counts.max() == 0:
        return None
    best = None
    for cid in np.argsort(counts)[::-1][:3]:
        area = int(counts[cid])
        f = area / float(h * w)
        if f < 0.10:
            break
        comp = lab == cid
        # outline pixels only (fast hull)
        er = comp.copy()
        er[1:-1, 1:-1] &= comp[:-2, 1:-1] & comp[2:, 1:-1] & comp[1:-1, :-2] & comp[1:-1, 2:]
        ys, xs = np.nonzero(comp & ~er)
        hull = _hull(list(zip(xs.tolist(), ys.tolist())))
        if len(hull) < 3:
            continue
        ha = _poly_area(hull)
        solidity = area / max(ha, 1.0)
        sides = int(comp[0].any()) + int(comp[-1].any()) + int(comp[:, 0].any()) + int(comp[:, -1].any())
        if f > 0.97 or sides >= 4:
            continue
        sc = (min(1.0, solidity) ** 3) * (f ** 0.5) * (1.0 - 0.12 * sides)
        if best is None or sc > best[0]:
            best = (sc, hull, list(zip(xs.tolist(), ys.tolist())))
    return best


# --------------------------------------------------------------------------- finding the paper (v2)
def _blur_u8(a, sigma):
    return np.asarray(Image.fromarray(np.clip(a, 0, 255).astype(np.uint8)).filter(ImageFilter.GaussianBlur(sigma)), np.float32)


def _grad(G):
    """Gradient of a grey (h,w) or colour (h,w,3) picture. For colour the magnitude is the length of the colour vector,
    so an edge between two colours of the same brightness (a dark card on a dark, shadowed table) is still an edge."""
    gx = np.zeros_like(G)
    gy = np.zeros_like(G)
    gx[:, 1:-1] = (G[:, 2:] - G[:, :-2]) * 0.5
    gy[1:-1] = (G[2:] - G[:-2]) * 0.5
    if G.ndim == 3:
        return gx, gy, np.sqrt((gx * gx + gy * gy).sum(2))
    return gx, gy, np.hypot(gx, gy)


def _across(gx, gy, xs, ys, n):
    """Size of the step across a side (normal n) at the given positions."""
    a = _sample(gx, xs, ys) * n[0] + _sample(gy, xs, ys) * n[1]
    return np.sqrt((a * a).sum(-1)) if a.ndim == 2 else np.abs(a)


def _blur_rgb(a, sigma):
    return np.stack([_blur_u8(a[..., i], sigma) for i in range(3)], -1)


def _sample(a, xs, ys):
    """Nearest-neighbour samples of a 2-D array at float positions (clipped to the picture)."""
    h, w = a.shape[:2]
    return a[np.clip(np.rint(ys).astype(int), 0, h - 1), np.clip(np.rint(xs).astype(int), 0, w - 1)]


def _quad_sides(q):
    """For each side of the quad (TL,TR,BR,BL order): start, end, outward normal (unit)."""
    q = np.asarray(q, np.float64)
    cen = q.mean(0)
    out = []
    for i in range(4):
        a, b = q[i], q[(i + 1) % 4]
        d = b - a
        L = float(np.hypot(*d)) or 1.0
        n = np.array([d[1], -d[0]]) / L
        if np.dot(n, (a + b) / 2 - cen) < 0:
            n = -n
        out.append((a, b, n, L))
    return out


def _edge_support(q, gx, gy, gm, scale, band):
    """0..1: how strongly the four sides of the quad lie on real edges of the photo (bright/dark step, running across the side)."""
    sides = _quad_sides(q)
    vals = []
    offs = np.arange(-band, band + 1, 1.0)
    for a, b, n, L in sides:
        t = np.linspace(0.08, 0.92, 40)
        px = a[0] + (b[0] - a[0]) * t
        py = a[1] + (b[1] - a[1]) * t
        best = np.zeros(len(t))
        for o in offs:
            xs, ys = px + n[0] * o, py + n[1] * o
            g = _sample(gm, xs, ys)
            along = _across(gx, gy, xs, ys, n)        # step across the side
            ok = along > 0.6 * np.maximum(g, 1e-6)
            best = np.maximum(best, np.where(ok, along, 0.0))
        vals.append(float(np.mean(np.minimum(1.0, best / scale))))
    v = np.array(vals)
    return 0.5 * float(v.mean()) + 0.5 * float(v.min()), v


def _poly_mask(q, size):
    m = Image.new("L", (size[1], size[0]), 0)
    ImageDraw.Draw(m).polygon([tuple(p) for p in q], fill=255)
    return np.asarray(m) > 0


def _contrast(q, G, ring):
    h, w = G.shape
    inside = _poly_mask(q, (h, w))
    k = max(3, int(ring) | 1)
    grown = np.asarray(Image.fromarray((inside * 255).astype(np.uint8)).filter(ImageFilter.MaxFilter(k))) > 0
    shrunk = np.asarray(Image.fromarray((inside * 255).astype(np.uint8)).filter(ImageFilter.MinFilter(k))) > 0
    outer = grown & ~inside
    inner = shrunk
    if outer.sum() < 20 or inner.sum() < 20:
        return 0.0, 0.0
    mi, mo = float(G[inner].mean()), float(G[outer].mean())
    return abs(mi - mo) / 255.0, mi - mo


def _filled_area(comp):
    """Area of a region with its holes (text!) filled, from the first / last pixel of every row and column."""
    rows = comp.any(1)
    cols = comp.any(0)
    r0 = comp.argmax(1)
    r1 = comp.shape[1] - 1 - comp[:, ::-1].argmax(1)
    c0 = comp.argmax(0)
    c1 = comp.shape[0] - 1 - comp[::-1].argmax(0)
    ar = float(((r1 - r0 + 1) * rows).sum())
    ac = float(((c1 - c0 + 1) * cols).sum())
    return 0.5 * (ar + ac)


def _candidates(mask, h, w, out, tag):
    """Quad candidates from the biggest regions of a mask."""
    lab = _components(mask)
    counts = np.bincount(lab.reshape(-1))
    counts[0] = 0
    if counts.max() == 0:
        return
    for cid in np.argsort(counts)[::-1][:3]:
        if counts[cid] < 0.06 * h * w:
            break
        comp = lab == cid
        sides = int(comp[0].any()) + int(comp[-1].any()) + int(comp[:, 0].any()) + int(comp[:, -1].any())
        if sides >= 4:
            continue
        er = comp.copy()
        er[1:-1, 1:-1] &= comp[:-2, 1:-1] & comp[2:, 1:-1] & comp[1:-1, :-2] & comp[1:-1, 2:]
        ys, xs = np.nonzero(comp & ~er)
        pts = list(zip(xs.tolist(), ys.tolist()))
        hull = _hull(pts)
        if len(hull) < 3:
            continue
        ha = _poly_area(hull)
        fa = _filled_area(comp)
        solidity = min(1.0, fa / max(ha, 1.0))
        if ha < 0.08 * h * w or ha > 0.985 * h * w:
            continue
        quad = _simplify_to_quad(hull)
        if len(quad) != 4:
            continue
        quad = core.order_quad(quad)
        try:
            q2 = _refine_quad(quad, pts)
            if len(q2) == 4 and 0.85 < _poly_area(q2) / max(_poly_area(quad), 1.0) < 1.2:
                quad = core.order_quad(q2)
        except Exception:
            pass
        out.append({"quad": quad, "solid": solidity, "sides": sides, "area": _poly_area(quad) / float(h * w), "tag": tag})


def _poly_overlap(q1, q2):
    """Rough IoU of two quads (rasterised on a small grid)."""
    pts = np.asarray(list(q1) + list(q2), np.float64)
    x0, y0 = pts.min(0)
    x1, y1 = pts.max(0)
    f = 80.0 / max(x1 - x0, y1 - y0, 1.0)
    n = (int((y1 - y0) * f) + 2, int((x1 - x0) * f) + 2)
    m = [_poly_mask([((x - x0) * f, (y - y0) * f) for x, y in q], n) for q in (q1, q2)]
    return float((m[0] & m[1]).sum()) / max(1.0, float((m[0] | m[1]).sum()))


def _is_convex(q):
    s = []
    for i in range(4):
        a, b, c = q[i], q[(i + 1) % 4], q[(i + 2) % 4]
        s.append((b[0] - a[0]) * (c[1] - b[1]) - (b[1] - a[1]) * (c[0] - b[0]))
    return all(v > 0 for v in s) or all(v < 0 for v in s)


def _refine_on_edges(quad, G, search):
    """Move each side of the quad onto the strongest brightness step near it (in a larger picture) and cross the four
    fitted lines.  Safe: gives the old quad back if the result looks wrong."""
    gx, gy, gm = _grad(G)
    sides = _quad_sides(quad)
    lines = []
    for a, b, n, L in sides:
        t = np.linspace(0.06, 0.94, 60)
        px = a[0] + (b[0] - a[0]) * t
        py = a[1] + (b[1] - a[1]) * t
        offs = np.arange(-search, search + 1, 1.0)
        score = np.zeros((len(offs), len(t)))
        for k, o in enumerate(offs):
            xs, ys = px + n[0] * o, py + n[1] * o
            along = _across(gx, gy, xs, ys, n)
            # step over +-1.5 px against the step over +-6 px: ~1 for a sharp edge, small for a soft shadow
            na, nb = _sample(G, xs + n[0] * 1.5, ys + n[1] * 1.5), _sample(G, xs - n[0] * 1.5, ys - n[1] * 1.5)
            wa, wb = _sample(G, xs + n[0] * 6.0, ys + n[1] * 6.0), _sample(G, xs - n[0] * 6.0, ys - n[1] * 6.0)
            crisp = np.clip(np.abs(na - nb) / (np.abs(wa - wb) + 3.0), 0.0, 1.0)
            score[k] = along * (0.35 + 0.65 * crisp) * (1.0 - 0.35 * abs(o) / max(1.0, search))   # prefer the nearest sharp edge
        k = score.argmax(0)
        strong = score.max(0) > 0.25 * (score.max() + 1e-6)
        if strong.sum() < 8:
            return quad
        P = np.stack([px + n[0] * offs[k], py + n[1] * offs[k]], 1)[strong]
        for _ in range(3):                                                                 # drop points that are not on the line
            c = P.mean(0)
            _, _, vt = np.linalg.svd(P - c)
            d = vt[0]
            res = np.abs((P - c) @ np.array([-d[1], d[0]]))
            keep = res <= max(1.2, 1.8 * np.median(res))
            if keep.sum() < 8:
                return quad
            P = P[keep]
        c = P.mean(0)
        _, _, vt = np.linalg.svd(P - c)
        lines.append((c, vt[0]))
    out = []
    for i in range(4):
        c1, d1 = lines[i - 1]
        c2, d2 = lines[i]
        M = np.array([d1, -d2]).T
        if abs(np.linalg.det(M)) < 1e-6:
            return quad
        t = np.linalg.solve(M, c2 - c1)
        out.append(tuple(c1 + t[0] * d1))
    out = core.order_quad(out)
    old = np.asarray(quad, np.float64)
    if (not _is_convex(out) or np.abs(np.asarray(out) - old).max() > 2.5 * search
            or not (0.88 < _poly_area(out) / max(_poly_area(quad), 1.0) < 1.12)):
        return quad
    return out


# ----------------------------------------------------------------- colour-based edge refinement (v3)
def _lab_blur(lab, passes=2):
    """Light smoothing of a Lab picture (two 3x3 box passes): kills sensor noise, keeps a real colour step."""
    out = np.empty_like(lab)
    for i in range(3):
        a = lab[..., i]
        for _ in range(passes):
            a = _box_mean(a, 3)
        out[..., i] = a
    return out


def _fit_line(P, tol=3.0):
    """Robust straight line through points (trims the ones that are not on it). Returns (centre, direction) or None."""
    P = np.asarray(P, np.float64)
    if len(P) < 4:
        return None
    for _ in range(4):
        c = P.mean(0)
        _, _, vt = np.linalg.svd(P - c)
        d = vt[0]
        res = np.abs((P - c) @ np.array([-d[1], d[0]]))
        keep = res <= max(1.0, 2.0 * np.median(res))
        if keep.all():
            break
        if keep.sum() < 4:
            return None
        P = P[keep]
    c = P.mean(0)
    _, _, vt = np.linalg.svd(P - c)
    if np.abs((P - c) @ np.array([-vt[0][1], vt[0][0]])).max() > tol:      # the points must really lie on one straight line
        return None
    return c, vt[0], len(P)


def _expand_to_paper(lab, quad, max_grow=0.17, chunks=12):
    """The first outline is often only the PRINTED AREA of the page (dark text gives much stronger edges than a page
    edge that is the same colour as the table).  Here every side is moved outwards through the white margin until
    the COLOUR really changes (paper colour -> table colour).  The change is looked for with the middle value of
    many points along the side, so noise and the grain of a wooden table do not disturb it, and the paper colour is
    measured locally, so a shadow or uneven light on the page does not matter.
    Returns (quad, number of sides that moved)."""
    h, w = lab.shape[:2]
    q = np.asarray(quad, np.float64)
    cen = q.mean(0)
    lb = _lab_blur(lab)
    # paper colour of the whole inside (the lighter 60 %: ink is left out) and how much it varies by itself
    inner = _poly_mask(cen + (q - cen) * 0.92, (h, w))
    if inner.sum() < 200:
        return quad, 0
    pix = lb[inner]
    keep = pix[:, 0] >= np.percentile(pix[:, 0], 40)
    pc_g = np.median(pix[keep], 0)
    far = ~_poly_mask(cen + (q - cen) * 1.28, (h, w))
    if far.sum() < 0.015 * h * w:
        return quad, 0                                      # the page fills the picture: nothing to find outside
    S = int(max(8, max_grow * max(h, w)))
    sustain = max(5, int(0.011 * max(h, w)))
    sides = _quad_sides(q)
    lines, moved = [], 0
    for a, b, n, L in sides:
        if L < 20:
            return quad, 0
        d = (b - a) / L
        pts, hit_off = [], []
        for k in range(chunks):
            t0, t1 = 0.06 + 0.88 * k / chunks, 0.06 + 0.88 * (k + 1) / chunks
            m = max(10, int(L * (t1 - t0)))
            tt = np.linspace(t0, t1, m)
            px = a[0] + (b[0] - a[0]) * tt
            py = a[1] + (b[1] - a[1]) * tt
            offs = np.arange(-10, S + 1, 1.0)
            X = px[:, None] + n[0] * offs[None, :]
            Y = py[:, None] + n[1] * offs[None, :]
            inside_img = (X >= 0) & (X <= w - 1) & (Y >= 0) & (Y <= h - 1)
            lim = np.where(inside_img.all(0))[0]
            if len(lim) < 4:
                continue
            last = int(lim.max()) if lim.max() == len(offs) - 1 else int(np.argmin(inside_img.all(0))) - 1
            col = lb[np.clip(np.rint(Y).astype(int), 0, h - 1), np.clip(np.rint(X).astype(int), 0, w - 1)]   # (m, O, 3)
            # the local paper colour: lighter half of what lies just inside the outline
            ref = col[:, 0:9].reshape(-1, 3)
            ref = ref[ref[:, 0] >= np.median(ref[:, 0])]
            pc = np.median(ref, 0)
            if np.sqrt(((pc - pc_g) ** 2).sum()) > 25.0:
                pc = pc_g
            prof = np.median(np.sqrt(((col - pc) ** 2).sum(2)), 0)                                         # (O,)
            prof = np.convolve(np.pad(prof, 1, mode="edge"), np.ones(3) / 3.0, mode="valid")
            top = min(len(prof), last + 1)
            if top - 10 < 3 * sustain:
                continue
            third = max(sustain, (top - 10) // 3)
            inner_lv = float(np.percentile(prof[10:10 + third], 25))                                      # paper margin
            outer_lv = float(np.median(prof[top - third:top]))                                             # what lies beyond
            step = outer_lv - inner_lv
            if step < 2.0:
                continue
            thr = inner_lv + 0.5 * step                                                                    # the edge is half way up
            hit = None
            for i in range(10, top - sustain):
                if prof[i:i + sustain].min() >= thr and (prof[i:top] >= thr).mean() >= 0.88:      # and it STAYS changed (an ink row does not)
                    hit = i
                    break
            if hit is None:
                continue
            if hit > 10:                                       # sub-pixel: where the profile crosses the threshold
                p0, p1 = prof[hit - 1], prof[hit]
                hit = hit - 1 + (thr - p0) / max(p1 - p0, 1e-6)
            o = offs[0] + hit
            pts.append((px.mean() + n[0] * o, py.mean() + n[1] * o))
            hit_off.append(o)
        ln = _fit_line(pts) if len(pts) >= max(5, chunks // 2) else None
        if ln is not None and ln[2] >= max(5, chunks // 2) - 1 and abs(float(np.median(hit_off))) >= 3.0:   # a tiny move is not worth trusting
            lines.append((ln[0], ln[1]))
            moved += 1
        else:
            lines.append((np.asarray(a, np.float64), d))
    if moved == 0:
        return quad, 0
    out = []
    for i in range(4):
        c1, d1 = lines[i - 1]
        c2, d2 = lines[i]
        M = np.array([d1, -d2]).T
        if abs(np.linalg.det(M)) < 1e-6:
            return quad, 0
        t = np.linalg.solve(M, c2 - c1)
        out.append(tuple(c1 + t[0] * d1))
    out = core.order_quad(out)
    ratio = _poly_area(out) / max(_poly_area(q), 1.0)
    if (not _is_convex(out) or not (0.9 < ratio < 2.6)
            or np.abs(np.asarray(out) - q).max() > 0.24 * max(h, w)):
        return quad, 0
    return out, moved



def detect_document(img, return_score=False):
    """Finds the 4 corners of the paper. Returns (corners [(x,y) TL,TR,BR,BL in picture pixels], found: bool).
    Works with many ways of segmenting the photo (brightness, 'paper-ness' = bright and colourless, and the area
    closed by strong edges); every candidate outline is judged by how well its four sides lie on real edges, how
    different the inside is from the surroundings and how much like a sheet it looks.  The winner is then snapped
    onto the real edges.  If nothing is found the corners are the picture's own corners (the user fixes them by hand)."""
    W, H = img.size
    rgbf = None
    s = LABEL_SIDE / float(max(W, H))
    sw, sh = max(8, round(W * s)), max(8, round(H * s))
    small = img.convert("RGB").resize((sw, sh), Image.Resampling.BOX)
    rgbf = np.asarray(small, np.float32)
    lum = rgbf[..., 0] * 0.299 + rgbf[..., 1] * 0.587 + rgbf[..., 2] * 0.114
    G = _blur_u8(lum, 1.1)
    mx, mn = rgbf.max(2), rgbf.min(2)
    sat = (mx - mn) / np.maximum(mx, 1.0)
    P = _blur_u8(lum * (1.0 - 0.75 * sat), 1.1)                           # paper-ness: bright and colourless
    gx, gy, gm = _grad(G)
    gscale = float(np.percentile(gm, 94)) + 1e-3
    h, w = G.shape
    cands = []
    for arr, both in ((G, True), (P, False)):
        t = _otsu(np.clip(arr, 0, 255).astype(np.uint8))
        for dt in (0, -20, 20, -40, 40):
            thr = int(min(250, max(5, t + dt)))
            _candidates(arr > thr, h, w, cands, "light")
            if both:
                _candidates(arr <= thr, h, w, cands, "dark")
    # the area enclosed by strong edges (finds a paper on a table of nearly the same brightness)
    for pct, grow in ((70, 3), (80, 3), (88, 3), (80, 5)):
        e = gm > np.percentile(gm, pct)
        e = np.asarray(Image.fromarray((e * 255).astype(np.uint8)).filter(ImageFilter.MaxFilter(grow))) > 0
        _candidates(~e, h, w, cands, "edge")
    # two groups of colour (paper / the rest): brightness AND colour tint, so a white sheet on a warm table separates
    feat = np.stack([lum / 255.0, (rgbf[..., 0] - rgbf[..., 1]) / 128.0, (rgbf[..., 1] - rgbf[..., 2]) / 128.0], -1).reshape(-1, 3)
    feat = feat * np.array([1.0, 1.6, 1.6], np.float32)
    c0 = feat[int(np.argmax(feat[:, 0]))]                                    # start: the brightest and the darkest pixel
    c1 = feat[int(np.argmin(feat[:, 0]))]
    for _ in range(8):
        d0 = ((feat - c0) ** 2).sum(1)
        d1 = ((feat - c1) ** 2).sum(1)
        lab2 = d0 < d1
        if lab2.all() or (~lab2).all():
            break
        c0, c1 = feat[lab2].mean(0), feat[~lab2].mean(0)
    if not (lab2.all() or (~lab2).all()):
        m2 = lab2.reshape(h, w)
        m2 = np.asarray(Image.fromarray((m2 * 255).astype(np.uint8)).filter(ImageFilter.MedianFilter(5))) > 0
        _candidates(m2, h, w, cands, "kmeans")
        _candidates(~m2, h, w, cands, "kmeans")
    try:
        _border_model_candidates(rgbf, lum, h, w, cands)
        _smooth_candidates(lum, h, w, cands)
    except Exception:                                   # the extra ways are a bonus: never break the main one
        pass
    scored = []
    ring = max(5, int(0.03 * max(h, w)))
    for c in cands:
        q = c["quad"]
        if not _is_convex(q):
            continue
        edge, per = _edge_support(q, gx, gy, gm, gscale, 3)
        con, signed = _contrast(q, G, ring)
        a = c["area"]
        sz = min(1.0, a / 0.20) ** 0.6 * (1.0 - max(0.0, a - 0.92) * 4)
        sc = (min(1.0, c["solid"]) ** 3) * (0.12 + edge) ** 1.3 * (0.10 + min(1.0, con * 4.0)) ** 0.45 * sz * (1.0 - 0.10 * c["sides"])
        if _DEBUG is not None:
            _DEBUG.append((sc, c["tag"], q, edge, con, c["solid"], a))
        scored.append((sc, q, edge, con))
    scored.sort(key=lambda r: -r[0])
    best = None
    if scored and scored[0][0] > 0.25 and scored[0][2] > 0.18:
        # the best few outlines are snapped onto the real edges (in a larger picture) and judged AGAIN on the snapped
        # sides, so a shape that is a bit too big / too small (shadow, hand, table edge) loses to the one that is exact
        s2 = min(2.5, 900.0 / float(max(W, H)) / s) if s * max(W, H) < 900 else 1.0
        if s2 > 1.2:
            big = img.convert("L").resize((max(8, round(W * s * s2)), max(8, round(H * s * s2))), Image.Resampling.BOX)
        else:
            s2 = 1.0
            big = img.convert("L").resize((sw, sh), Image.Resampling.BOX)
        Gb = np.asarray(big, np.float32)
        bx, by, bm = _grad(Gb)
        bscale = float(np.percentile(bm, 94)) + 1e-3
        seen = []
        for sc, q, edge, con in scored[:12]:
            if sc < 0.25 or edge < 0.18:
                break
            if any(_poly_overlap(q, o) > 0.985 for o in seen):
                continue
            seen.append(q)
            qb = [(x * s2, y * s2) for x, y in q]
            q1 = _refine_on_edges(qb, Gb, search=max(4.0, 0.025 * max(Gb.shape[:2])))
            q1 = _refine_on_edges(q1, Gb, search=max(2.5, 0.008 * max(Gb.shape[:2])))       # second, finer pass
            e1, _ = _edge_support(q1, bx, by, bm, bscale, 2)
            fin = sc * (0.25 + e1)
            if best is None or fin > best[0]:
                best = (fin, [(x / s2, y / s2) for x, y in q1], e1, con, sc)
            if len(seen) >= 4:
                break
    if best is not None:
        pts = core.order_quad(best[1])
        try:                                           # page edge that is nearly the table's colour: find it by colour
            labs = core.rgb_to_lab(np.asarray(img.convert("RGB").resize(big.size, Image.Resampling.BOX)))
            ex, n_moved = _expand_to_paper(labs, [(x * s2, y * s2) for x, y in pts])
            if n_moved:
                pts = core.order_quad([(x / s2, y / s2) for x, y in ex])
        except Exception:
            pass
        res = [(x / s, y / s) for x, y in pts]
        info = {"score": best[4], "edge": best[2], "contrast": best[3]}
        return (res, True, info) if return_score else (res, True)
    mx_, my_ = 0.04 * W, 0.04 * H
    res = [(mx_, my_), (W - mx_, my_), (W - mx_, H - my_), (mx_, H - my_)]
    return (res, False, {"score": 0.0, "edge": 0.0, "contrast": 0.0}) if return_score else (res, False)


def aspect_ratio(quad, size):
    """width / height of the real sheet, from its photographed corners (TL,TR,BR,BL) - the camera angle is worked out
    from the four corners, so a tilted photo of an A5 page comes out as A5, not as a stretched rectangle."""
    W, H = size
    u0, v0 = W / 2.0, H / 2.0
    tl, tr, br, bl = quad
    m1, m2, m3, m4 = [np.array([x, y, 1.0]) for x, y in (tl, tr, bl, br)]
    dist = lambda a, b: math.hypot(a[0] - b[0], a[1] - b[1])
    simple = (dist(tl, tr) + dist(bl, br)) / max(1e-6, (dist(tl, bl) + dist(tr, br)))
    try:
        k2 = np.dot(np.cross(m1, m4), m3) / np.dot(np.cross(m2, m4), m3)
        k3 = np.dot(np.cross(m1, m4), m2) / np.dot(np.cross(m3, m4), m2)
        n2 = k2 * m2 - m1
        n3 = k3 * m3 - m1
        den = n2[2] * n3[2]
        if abs(den) < 1e-9:                      # camera looks straight at the page
            return simple
        f2 = -((n2[0] * n3[0] - (n2[0] * n3[2] + n2[2] * n3[0]) * u0 + n2[2] * n3[2] * u0 * u0) +
               (n2[1] * n3[1] - (n2[1] * n3[2] + n2[2] * n3[1]) * v0 + n2[2] * n3[2] * v0 * v0)) / den
        if not np.isfinite(f2) or f2 <= 0:
            return simple
        f = math.sqrt(f2)
        A = np.array([[f, 0, u0], [0, f, v0], [0, 0, 1.0]])
        Ai = np.linalg.inv(A)
        M = Ai.T @ Ai
        r = math.sqrt(max(1e-12, float(n2 @ M @ n2)) / max(1e-12, float(n3 @ M @ n3)))
        if not np.isfinite(r) or r < 0.2 or r > 5.0 or abs(math.log(r / simple)) > 0.45:
            return simple                        # estimate is unreliable: fall back to the edge lengths
        return r
    except Exception:
        return simple


def straighten(img, quad):
    """Crop the paper out and remove the perspective. Keeps the sheet's real proportions (A4, A5, Letter, ID card ...)."""
    tl, tr, br, bl = core.order_quad(quad)
    dist = lambda a, b: math.hypot(a[0] - b[0], a[1] - b[1])
    ratio = aspect_ratio((tl, tr, br, bl), img.size)
    w0 = max(dist(tl, tr), dist(bl, br))
    h0 = max(dist(tl, bl), dist(tr, br))
    if w0 / max(h0, 1e-6) > ratio:               # use the larger side as the measure so nothing loses detail
        h = h0
        w = h0 * ratio
        if w < w0:
            w, h = w0, w0 / ratio
    else:
        w = w0
        h = w0 / ratio
        if h < h0:
            w, h = h0 * ratio, h0
    w, h = max(8, int(round(w))), max(8, int(round(h)))
    co = core._coeffs([(0, 0), (w, 0), (w, h), (0, h)], [tl, tr, br, bl])
    return img.transform((w, h), Image.Transform.PERSPECTIVE, co, Image.Resampling.BICUBIC)


LOOKS = ("orig", "clean", "gray", "bw")


def enhance(img, look):
    """How the straightened page looks: 'orig' as photographed, 'clean' = white paper, even light (colour kept),
    'gray' = clean + gray, 'bw' = clean black on white."""
    if look not in LOOKS or look == "orig":
        return img
    opts = dict(core.SHADOW_DEFAULTS)
    opts["protect"] = 0
    if look == "bw":
        opts.update(mode="bw", white=60, ink=60)
    else:
        opts.update(mode="gray" if look == "gray" else "color", white=60, ink=55, clean=40)
    out = core.remove_shadow(img.convert("RGB"), 1.0, None, opts)
    return out.convert("RGB") if out.mode != "RGB" else out
