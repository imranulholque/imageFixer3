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


def _order(pts):
    """The 4 corners as top-left, top-right, bottom-right, bottom-left (clockwise, starting at the corner nearest the picture's
    top-left).  Walks round the middle, so a very tilted or skewed page never gets a twisted (bow-tie) order."""
    p = [(float(x), float(y)) for x, y in pts]
    cx = sum(a for a, _ in p) / len(p)
    cy = sum(b for _, b in p) / len(p)
    p.sort(key=lambda v: math.atan2(v[1] - cy, v[0] - cx))
    i = min(range(len(p)), key=lambda k: p[k][0] + p[k][1])
    return p[i:] + p[:i]


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


def _simplify_enclosing(hull):
    """Reduce a convex outline to 4 corners by EXTENDING the neighbouring sides (never cutting a corner off), always removing
    the side whose removal adds the least area.  Gives the tight quadrilateral around the shape - a cut corner (a fold, a
    finger, a bent card corner) does not skew the sheet like dropping a vertex does."""
    p = [tuple(map(float, v)) for v in hull]
    if len(p) < 4:
        return p
    while len(p) > 4:
        n = len(p)
        best, bi, bx = 1e18, None, None
        for i in range(n):
            a, b = p[i], p[(i + 1) % n]
            pa, nb = p[i - 1], p[(i + 2) % n]
            d1 = (a[0] - pa[0], a[1] - pa[1])
            d2 = (b[0] - nb[0], b[1] - nb[1])
            den = d1[0] * d2[1] - d1[1] * d2[0]
            if abs(den) < 1e-9:
                continue
            t = ((nb[0] - pa[0]) * d2[1] - (nb[1] - pa[1]) * d2[0]) / den
            X = (pa[0] + t * d1[0], pa[1] + t * d1[1])
            if t < 1.0:                                           # the sides diverge on this side: cannot close it
                continue
            ar = abs((a[0] - X[0]) * (b[1] - X[1]) - (b[0] - X[0]) * (a[1] - X[1])) / 2.0
            if ar < best:
                best, bi, bx = ar, i, X
        if bi is None:
            return _simplify_to_quad(hull)
        n = len(p)
        j = (bi + 1) % n
        p[bi] = bx
        p.pop(j)
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
        if counts[cid] < 0.015 * h * w:
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
        if ha < 0.02 * h * w or ha > 0.985 * h * w:
            continue
        for simp in (_simplify_enclosing, _simplify_to_quad):
            quad = simp(hull)
            if len(quad) != 4:
                continue
            quad = _order(quad)
            if not _is_convex(quad):
                continue
            qa = _poly_area(quad)
            if qa > 1.35 * ha or qa < 0.9 * ha:                 # the quad must really hug the shape
                continue
            try:
                q2 = _refine_quad(quad, pts)
                if len(q2) == 4 and 0.85 < _poly_area(q2) / max(_poly_area(quad), 1.0) < 1.2:
                    quad = _order(q2)
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
    out = _order(out)
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
    out = _order(out)
    ratio = _poly_area(out) / max(_poly_area(q), 1.0)
    if (not _is_convex(out) or not (0.9 < ratio < 2.6)
            or np.abs(np.asarray(out) - q).max() > 0.24 * max(h, w)):
        return quad, 0
    return out, moved



def _detect_document_legacy(img, return_score=False):
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
    # ---- judge every outline with the common judge, slide the best few onto the page, snap them on the real edges
    ge_x, ge_y, ge_m = gx, gy, gm
    gm2, ang2, nms2 = _edge_maps(rgbf)
    J = _Judge(rgbf, gm2, nms2, ang2)
    pool = []
    for c in cands:
        q = c["quad"]
        if not _quad_ok(q, h, w):
            continue
        v, j = _quad_value(J, q)
        if _DEBUG is not None:
            _DEBUG.append((v, c["tag"], q, j["edge_mean"], j["split_mean"], c["solid"], c["area"]))
        pool.append((v, q))
    try:                                                # outlines made of straight lines that really run along edges
        ln = _hough_lines(gm2, ang2, nms2, max_lines=24)
        for q, ar, vt in _line_quads(ln, h, w, limit=500):
            if not _quad_ok(q, h, w):
                continue
            v, j = _quad_value(J, q)
            if _DEBUG is not None:
                _DEBUG.append((v, "lines", q, j["edge_mean"], j["split_mean"], 1.0, ar))
            pool.append((v, q))
    except Exception:
        pass
    pool.sort(key=lambda r: -r[0])
    starts = []
    for v, q in pool:
        if any(_poly_overlap(q, o) > 0.85 for o in starts):
            continue
        starts.append(q)
        if len(starts) >= 10:
            break
    coarse = []
    for q in starts:                                    # first a rough slide for many outlines ...
        q1, v1, j1 = _climb(J, q, steps=(9.0, 4.5), rounds=2)
        coarse.append((v1 * _purity_factor(J, q1), q1))
    coarse.sort(key=lambda r: -r[0])
    climbed = []
    for v0, q in coarse[:6]:                            # ... then the exact slide for the best few
        q1, v1, j1 = _climb(J, q, steps=(2.2, 1.0), rounds=3)
        climbed.append((v1 * _purity_factor(J, q1), q1, j1))
    climbed.sort(key=lambda r: -r[0])
    if len(climbed) > 1:
        # a fold, a printed frame or a header inside the page also has strong edges: when a bigger outline that holds the best
        # one comes close in value, it is the whole sheet - take it
        b0 = climbed[0]
        a0 = _poly_area(b0[1])
        pick = b0
        m0 = _poly_mask(b0[1], (h, w))
        wholes = []
        for c in climbed[1:]:
            ac = _poly_area(c[1])
            if ac < 1.25 * a0 or ac > 4.5 * a0 or c[0] < 0.8 * b0[0]:
                continue
            mc = _poly_mask(c[1], (h, w))
            if (m0 & mc).sum() >= 0.8 * m0.sum():
                wholes.append(c)
        if wholes:
            pick = max(wholes, key=lambda r: r[0])
        if pick is not b0:
            climbed.remove(pick)
            climbed.insert(0, pick)
    best = None
    if climbed and climbed[0][0] > 0.07:
        s2 = min(2.5, 900.0 / float(max(W, H)) / s) if s * max(W, H) < 900 else 1.0
        if s2 > 1.2:
            big = img.convert("L").resize((max(8, round(W * s * s2)), max(8, round(H * s * s2))), Image.Resampling.BOX)
        else:
            s2 = 1.0
            big = img.convert("L").resize((sw, sh), Image.Resampling.BOX)
        Gb = np.asarray(big, np.float32)
        for v1, q1, j1 in climbed[:1]:
            qb = [(x * s2, y * s2) for x, y in q1]
            qs = _refine_on_edges(qb, Gb, search=max(3.0, 0.012 * max(Gb.shape[:2])))
            qs = [(x / s2, y / s2) for x, y in qs]
            if _quad_ok(qs, h, w):
                v2, j2 = _quad_value(J, qs)
                if v2 >= 0.97 * v1:
                    q1, v1, j1 = qs, v2, j2
            if best is None or v1 > best[0]:
                best = (v1, q1, j1["edge_mean"], j1["split_mean"], v1)
    if best is not None:
        # a picture that is edges everywhere (noise, fine texture) has no sheet: its sides must stand out from the general edge level
        base = float(J.Estack.max(0).mean())
        if best[2] < 1.12 * base:
            best = None
    if best is not None:
        pts = _order(best[1])
        try:                                           # page edge that is nearly the table's colour: find it by colour
            labs = core.rgb_to_lab(np.asarray(img.convert("RGB").resize(big.size, Image.Resampling.BOX)))
            ex, n_moved = _expand_to_paper(labs, [(x * s2, y * s2) for x, y in pts])
            if n_moved:
                pts = _order([(x / s2, y / s2) for x, y in ex])
        except Exception:
            pass
        res = [(x / s, y / s) for x, y in pts]
        info = {"score": min(1.2, best[4] / 0.28), "edge": best[2], "contrast": best[3]}
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
    tl, tr, br, bl = _order(quad)
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


# ======================================================================= v4: straight-line (Hough) candidates + one common judge
def _lab_small(rgbf):
    return core.rgb_to_lab(np.clip(rgbf, 0, 255).astype(np.uint8))


def _edge_maps(rgbf, sigma=1.0):
    """Colour edge map of a small picture: strength (0..1 after scaling), gradient angle, thinned (non-maximum suppressed)
    strength per orientation bin (4 bins, slightly spread so a side that is 1 px off still hits)."""
    h, w = rgbf.shape[:2]
    b = _blur_rgb(rgbf, sigma)
    gx = np.zeros_like(b)
    gy = np.zeros_like(b)
    gx[:, 1:-1] = (b[:, 2:] - b[:, :-2]) * 0.5
    gy[1:-1] = (b[2:] - b[:-2]) * 0.5
    jxx, jyy, jxy = (gx * gx).sum(2), (gy * gy).sum(2), (gx * gy).sum(2)
    tr = jxx + jyy
    disc = np.sqrt(np.maximum((jxx - jyy) ** 2 + 4 * jxy * jxy, 0.0))
    lam = 0.5 * (tr + disc)
    gm = np.sqrt(np.maximum(lam, 0.0)) / 1.7320508                       # ~ per-channel step per pixel
    ang = 0.5 * np.arctan2(2 * jxy, jxx - jyy)                          # direction of the steepest change, mod pi
    ang = np.where(ang < 0, ang + np.pi, ang)
    q = (np.rint(ang / (np.pi / 4)).astype(int)) % 4
    nms = np.zeros_like(gm, bool)
    offs = {0: (0, 1), 1: (1, 1), 2: (1, 0), 3: (1, -1)}                  # (dy, dx) of the neighbours along the gradient
    gp = np.pad(gm, 1, mode="constant")
    for k, (dy, dx) in offs.items():
        a = gp[1 + dy:1 + dy + h, 1 + dx:1 + dx + w]
        c = gp[1 - dy:1 - dy + h, 1 - dx:1 - dx + w]
        nms |= (q == k) & (gm >= a) & (gm >= c)
    return gm, ang, nms & (gm > 0)


def _hough_lines(gm, ang, nms, max_lines=36):
    """The strongest straight lines of the picture: every thinned edge pixel votes for the lines through it that are
    perpendicular to its own gradient (so only lines that really run ALONG edges collect votes).
    Returns [(phi, rho, votes)]: points with x*cos(phi) + y*sin(phi) = rho, phi in [0, pi)."""
    h, w = gm.shape
    cap = float(np.percentile(gm[nms], 90)) if nms.any() else 1.0
    thr = max(1.5, 0.12 * float(np.percentile(gm, 99.5)))
    ys, xs = np.nonzero(nms & (gm > thr))
    if len(xs) < 20:
        return []
    wgt = np.minimum(1.0, gm[ys, xs] / max(cap, 1e-3)).astype(np.float32)
    phi = np.rad2deg(ang[ys, xs])                                          # 0..180
    diag = int(math.ceil(math.hypot(h, w))) + 2
    R = 2 * diag + 1
    A = np.zeros(180 * R, np.float32)
    k0 = np.rint(phi).astype(int)
    for dk in range(-9, 10):                                                # the measured angle of a thin edge is noisy: vote on a fan of angles
        f = 1.0 - abs(dk) / 10.0
        k = k0 + dk
        rho = xs * np.cos(np.deg2rad(k)) + ys * np.sin(np.deg2rad(k))
        neg = (k < 0) | (k >= 180)
        rho = np.where(neg, -rho, rho)
        kk = k % 180
        ri = np.rint(rho).astype(int) + diag
        A += np.bincount(kk * R + ri, weights=wgt * f, minlength=180 * R).astype(np.float32)
    A = A.reshape(180, R)
    As = A.copy()
    for s in (1, -1):                                                      # 3-wide smoothing along rho
        As[:, 1:-1] += 0.5 * np.roll(A, s, 1)[:, 1:-1]
    lines = []
    minv = 0.10 * min(h, w)
    for _ in range(max_lines):
        i = int(np.argmax(As))
        k, ri = divmod(i, R)
        v = float(As[k, ri])
        if v < minv:
            break
        lines.append((math.radians(k), float(ri - diag), v))
        for dk in range(-5, 6):                                            # suppress the neighbourhood (wraps with a sign flip)
            kk = k + dk
            lo, hi = ri - 7, ri + 8
            if kk < 0:
                kk += 180
                lo, hi = 2 * diag - (ri + 7), 2 * diag - (ri - 8) + 1
            elif kk >= 180:
                kk -= 180
                lo, hi = 2 * diag - (ri + 7), 2 * diag - (ri - 8) + 1
            As[kk, max(0, lo):max(0, hi)] = 0
    return lines


def _line_pts(l):
    phi, rho, _ = l
    n = np.array([math.cos(phi), math.sin(phi)])
    return n * rho, np.array([-n[1], n[0]])                                # a point on the line, its direction


def _meet(l1, l2):
    p1, d1 = _line_pts(l1)
    p2, d2 = _line_pts(l2)
    M = np.array([d1, -d2]).T
    det = np.linalg.det(M)
    if abs(det) < 0.12:                                                    # nearly parallel (< ~7 deg)
        return None
    t = np.linalg.solve(M, p2 - p1)
    return p1 + t[0] * d1


def _line_quads(lines, h, w, limit=1500):
    """Every four-sided shape made of two 'opposite' pairs of lines that is a plausible sheet."""
    n = len(lines)
    if n < 4:
        return []
    ph = np.array([l[0] for l in lines])
    rh = np.array([l[1] for l in lines])
    nx, ny = np.cos(ph), np.sin(ph)
    mn = min(h, w)
    pairs = []
    for i in range(n):
        for j in range(i + 1, n):
            da = abs(((ph[i] - ph[j]) + np.pi / 2) % np.pi - np.pi / 2)
            if da > math.radians(28):
                continue
            # distance between the two lines, measured on the average normal
            m = 0.5 * (np.array([nx[i], ny[i]]) + np.sign(nx[i] * nx[j] + ny[i] * ny[j]) * np.array([nx[j], ny[j]]))
            m /= (np.hypot(*m) + 1e-9)
            pi, _ = _line_pts(lines[i])
            pj, _ = _line_pts(lines[j])
            sep = abs(float((pj - pi) @ m))
            if sep < 0.09 * mn:
                continue
            pairs.append((i, j, 0.5 * (ph[i] + ph[j]) if da < np.pi / 2 else ph[i], sep))
    out = []
    lo_x, hi_x, lo_y, hi_y = -0.05 * w, 1.05 * w, -0.05 * h, 1.05 * h
    P = len(pairs)
    for a in range(P):
        ia, ja, pa, _ = pairs[a]
        for b in range(a + 1, P):
            ib, jb, pb, _ = pairs[b]
            if len({ia, ja, ib, jb}) < 4:
                continue
            ang = abs(((pa - pb) + np.pi / 2) % np.pi - np.pi / 2)      # 0 = same direction, 90 deg = square
            if np.pi / 2 - ang > math.radians(52):                      # the two directions must be clearly different
                continue
            c = [_meet(lines[ia], lines[ib]), _meet(lines[ib], lines[ja]), _meet(lines[ja], lines[jb]), _meet(lines[jb], lines[ia])]
            if any(p is None for p in c):
                continue
            if any(not (lo_x <= p[0] <= hi_x and lo_y <= p[1] <= hi_y) for p in c):
                continue
            q = [tuple(p) for p in c]
            if not _is_convex(q):
                continue
            ar = _poly_area(q) / float(h * w)
            if ar < 0.025 or ar > 0.985:
                continue
            sd = [math.hypot(q[i][0] - q[(i + 1) % 4][0], q[i][1] - q[(i + 1) % 4][1]) for i in range(4)]
            if min(sd) < 0.09 * mn or max(sd) / max(min(sd), 1e-6) > 8:
                continue
            out.append((_order(q), ar, lines[ia][2] + lines[ib][2] + lines[ja][2] + lines[jb][2]))
    out.sort(key=lambda r: -r[2])
    return out[:limit]


def _bil(arr, xx, yy, sel=None):
    """Bilinear lookup of arr (h, w[, c]) at float positions (clamped to the picture); sel picks a map of a stack per row."""
    h, w = arr.shape[-3:-1] if arr.ndim == 4 or (arr.ndim == 3 and sel is None) else arr.shape[-2:]
    x = np.clip(xx, 0, w - 1.001)
    y = np.clip(yy, 0, h - 1.001)
    x0 = x.astype(int)
    y0 = y.astype(int)
    fx = x - x0
    fy = y - y0
    if sel is None:
        g = lambda yi, xi: arr[yi, xi]
    else:
        g = lambda yi, xi: arr[sel, yi, xi]
    a = g(y0, x0)
    b = g(y0, x0 + 1)
    c = g(y0 + 1, x0)
    d = g(y0 + 1, x0 + 1)
    if a.ndim == 3:
        fx = fx[..., None]
        fy = fy[..., None]
    return (a * (1 - fx) + b * fx) * (1 - fy) + (c * (1 - fx) + d * fx) * fy


class _Judge:
    """One common judge for every candidate outline (from brightness, colour, texture, edges or straight lines):
      edge  - how much of the four sides lies on a real, thin edge running along the side
      split - how different the colour just inside each side is from the colour just outside it (a real sheet differs from
              the table on ALL four sides; the printed area of a page, a pattern line on a cloth or a tile edge does not)
      flat  - the inside is one paper (little colour change between the four inner strips)."""

    def __init__(self, rgbf, gm, nms, ang):
        self.h, self.w = rgbf.shape[:2]
        self.lab = _lab_blur(_lab_small(rgbf), 1)
        try:
            self.bg = _bg_distance(rgbf)
        except Exception:
            self.bg = None
        cap = float(np.percentile(gm[nms], 92)) if nms.any() else 1.0
        self.cap = max(cap, 1e-3)
        q = (np.rint(ang / (np.pi / 4)).astype(int)) % 4
        E = np.where(nms, np.minimum(1.0, gm / self.cap), 0.0).astype(np.float32)
        self.E = []
        for k in range(4):                                                 # orientation bin k also accepts its two neighbours
            m = np.where((q == k) | (q == (k + 1) % 4) | (q == (k - 1) % 4), E, 0.0)
            mm = np.where(q == k, E, 0.0)
            # 3x3 spread: the side may be a pixel off
            e = np.maximum(mm, 0.8 * m)
            pe = np.pad(e, 1)
            sp = np.max([pe[1 + dy:1 + dy + self.h, 1 + dx:1 + dx + self.w] for dy in (-1, 0, 1) for dx in (-1, 0, 1)], 0)
            self.E.append(sp)
        self.Estack = np.stack(self.E, 0)

    def judge(self, q, S=36):
        h, w = self.h, self.w
        Q = np.asarray(q, np.float64)
        cen = Q.mean(0)
        A, B = Q, np.roll(Q, -1, 0)
        Dv = B - A
        Ls = np.maximum(np.hypot(Dv[:, 0], Dv[:, 1]), 1e-6)
        N = np.stack([Dv[:, 1], -Dv[:, 0]], 1) / Ls[:, None]
        flip = ((N * ((A + B) / 2 - cen)).sum(1) < 0)
        N = np.where(flip[:, None], -N, N)
        t = np.linspace(0.07, 0.93, S)
        X = A[:, 0:1] + t[None] * Dv[:, 0:1]                                # (4, S)
        Y = A[:, 1:2] + t[None] * Dv[:, 1:2]
        d_in = max(3.0, 0.016 * max(h, w))
        ok = (X >= 0) & (X <= w - 1) & (Y >= 0) & (Y <= h - 1)
        m_ = 0.012 * max(h, w) + 1.5
        near_rim = ((X < m_) | (X > w - 1 - m_) | (Y < m_) | (Y > h - 1 - m_))
        EX, EY = X + N[:, 0:1] * 2.2 * d_in, Y + N[:, 1:2] * 2.2 * d_in
        out_of = (EX < 0) | (EX > w - 1) | (EY < 0) | (EY > h - 1)
        on_rim = (near_rim.mean(1) > 0.8) | ((~ok).mean(1) > 0.5) | (out_of.mean(1) > 0.6)

        def ix(xx, yy):
            return np.clip(np.rint(yy).astype(int), 0, h - 1), np.clip(np.rint(xx).astype(int), 0, w - 1)
        kb = (np.rint((np.arctan2(N[:, 1], N[:, 0]) % np.pi) / (np.pi / 4)).astype(int)) % 4
        iy, ixx = ix(X, Y)
        e = np.where(ok, _bil(self.Estack, X, Y, sel=kb[:, None]), 0.0)
        e.sort(1)
        sup_all = e[:, int(0.15 * S):].mean(1)
        def lab_at(o):
            return _bil(self.lab, X + N[:, 0:1] * o, Y + N[:, 1:2] * o)       # (4, S, 3)
        i1 = (lab_at(-d_in) + lab_at(-2.2 * d_in)) / 2
        u = (lab_at(d_in) + lab_at(2.2 * d_in)) / 2
        de_all = np.sqrt(((i1 - u) ** 2).sum(2))
        if self.bg is not None:
            bi_all = _bil(self.bg, X - N[:, 0:1] * 1.6 * d_in, Y - N[:, 1:2] * 1.6 * d_in)
            ox, oy = X + N[:, 0:1] * 1.6 * d_in, Y + N[:, 1:2] * 1.6 * d_in
            bo_all = _bil(self.bg, ox, oy)
            near_edge = np.minimum(np.minimum(ox, w - 1 - ox), np.minimum(oy, h - 1 - oy))
        ringw = max(3, int(0.045 * min(h, w))) * 1.3
        sup, spl, bgs, inn = [], [], [], []
        rim = 0
        for i in range(4):
            if on_rim[i]:                                                    # the sheet runs on beyond the photo: this side is the photo's edge
                rim += 1
                sup.append(0.6); spl.append(0.5); bgs.append(0.5)
                inn.append(np.median(i1[i], 0))
                continue
            if ok[i].sum() < 6:
                sup.append(0.0); spl.append(0.0); bgs.append(0.0); inn.append(None)
                continue
            sup.append(float(sup_all[i]))
            good = ok[i] & ~out_of[i]
            if not good.any():
                spl.append(0.6)
            else:
                spl.append(float(np.median(de_all[i][good])) / 9.0)
            inn.append(np.median(i1[i][ok[i]], 0))
            if self.bg is None or not good.any():
                bgs.append(0.6)
            elif float(np.median(near_edge[i])) < ringw:                     # this strip lies in the frame the colours were learned from: no evidence
                bgs.append(0.5)
            else:
                bgs.append(float(np.clip((np.median(bi_all[i][good]) - np.median(bo_all[i][good])) / 3.0, 0.0, 1.0)))
        iv = [v for v in inn if v is not None]
        flat = 0.0
        if len(iv) >= 3:
            flat = max(float(np.sqrt(((iv[a] - iv[b]) ** 2).sum())) for a in range(len(iv)) for b in range(a + 1, len(iv)))
        sup = np.array(sup)
        spl = np.clip(np.array(spl), 0.0, 1.0)
        return {"edge_mean": float(sup.mean()), "edge_min": float(sup.min()), "split_mean": float(spl.mean()),
                "split_min": float(spl.min()), "flat": flat, "sup": sup, "spl": spl,
                "bg_mean": float(np.mean(bgs)), "bg_min": float(np.min(bgs)), "rim": rim}


def _judged_score(j, area):
    """One number for a judged candidate."""
    edge = 0.5 * j["edge_mean"] + 0.5 * j["edge_min"]
    split = 0.5 * j["split_mean"] + 0.5 * j["split_min"]
    sz = min(1.0, area / 0.16) ** 0.55 * (min(1.0, area / 0.9) ** 0.3) * (1.0 - max(0.0, area - 0.93) * 6)
    flat = 1.0 / (1.0 + max(0.0, j["flat"] - 18.0) / 28.0)
    bg = 0.5 * j["bg_mean"] + 0.5 * j["bg_min"]
    return (0.10 + edge) ** 1.3 * (0.07 + split) ** 0.9 * sz * (0.5 + 0.5 * flat) * (0.30 + 0.70 * bg) * (1.0, 0.92, 0.72, 0.36, 0.12)[min(4, j["rim"])]


def _quad_ok(q, h, w):
    if not _is_convex(q):
        return False
    a = _poly_area(q) / float(h * w)
    if a < 0.012 or a > 0.985:
        return False
    sd = [math.hypot(q[i][0] - q[(i + 1) % 4][0], q[i][1] - q[(i + 1) % 4][1]) for i in range(4)]
    thick = _poly_area(q) / max(max(sd), 1e-6)                    # area / longest side: a sliver has no thickness
    return min(sd) > 0.06 * min(h, w) and max(sd) / max(min(sd), 1e-6) < 9 and thick > 0.07 * min(h, w)


def _quad_value(J, q):
    a = _poly_area(q) / float(J.h * J.w)
    j = J.judge(q)
    return _judged_score(j, a), j


def _extend_to_rim(a, b, w, h):
    """The point where the line a->b, carried on past b, leaves the picture (None if b is already on the rim)."""
    dx, dy = b[0] - a[0], b[1] - a[1]
    L = math.hypot(dx, dy)
    if L < 1e-6:
        return None
    dx, dy = dx / L, dy / L
    t = 1e9
    if dx > 1e-6:
        t = min(t, ((w - 1) - b[0]) / dx)
    elif dx < -1e-6:
        t = min(t, (0 - b[0]) / dx)
    if dy > 1e-6:
        t = min(t, ((h - 1) - b[1]) / dy)
    elif dy < -1e-6:
        t = min(t, (0 - b[1]) / dy)
    if t < 2.0 or t > 1e8:
        return None
    return (b[0] + dx * t, b[1] + dy * t)


def _climb(J, q, steps=(9.0, 4.5, 2.2, 1.0), rounds=3):
    """Slide the four corners of an outline around (big steps first, then small) while the judge likes the result more:
    a corner that sits a bit inside the page, on a fold or on a finger walks onto the real corner."""
    h, w = J.h, J.w
    q = [tuple(map(float, p)) for p in q]
    best, bj = _quad_value(J, q)
    dirs = [(1, 0), (-1, 0), (0, 1), (0, -1), (0.7, 0.7), (-0.7, 0.7), (0.7, -0.7), (-0.7, -0.7)]
    k = max(h, w) / 280.0
    for st in steps:
        st *= k
        for _ in range(rounds):
            moved = False
            for i in range(4):
                cand_best, cq = best, None
                moves = [(q[i][0] + dx * st, q[i][1] + dy * st) for dx, dy in dirs]
                if st >= 4.0 * k:                                         # also: run the corner on along a side until the photo's edge
                    for nb in (q[i - 1], q[(i + 1) % 4]):
                        ex = _extend_to_rim(nb, q[i], w, h)
                        if ex is not None:
                            moves.append(ex)
                for mv in moves:
                    n = list(q)
                    n[i] = mv
                    if not (-0.04 * w <= n[i][0] <= 1.04 * w and -0.04 * h <= n[i][1] <= 1.04 * h):
                        continue
                    if not _quad_ok(n, h, w):
                        continue
                    v, j = _quad_value(J, n)
                    if v > cand_best * 1.003:
                        cand_best, cq, cj = v, n, j
                if cq is not None:
                    q, best, bj, moved = cq, cand_best, cj, True
            if not moved:
                break
    return q, best, bj


def _bg_distance(rgbf):
    """How far every pixel is from the colours of the picture's frame (table / wall / cloth), in units of how much the frame
    itself varies.  About 0..1.5 = looks like the surroundings, 3 and more = something else (a sheet, a card)."""
    h, w = rgbf.shape[:2]
    lum = rgbf[..., 0] * 0.299 + rgbf[..., 1] * 0.587 + rgbf[..., 2] * 0.114
    b = max(3, int(0.045 * min(h, w)))
    ring = np.zeros((h, w), bool)
    ring[:b] = ring[-b:] = True
    ring[:, :b] = ring[:, -b:] = True
    feat = np.stack([lum / 255.0, (rgbf[..., 0] - rgbf[..., 1]) / 128.0, (rgbf[..., 1] - rgbf[..., 2]) / 128.0], -1)
    feat = feat * np.array([1.0, 1.6, 1.6], np.float32)
    bp = feat[ring]
    if len(bp) < 50:
        return None
    cen = [np.median(bp, 0)]
    for _ in range(2):
        d = np.min([((bp - c) ** 2).sum(1) for c in cen], 0)
        far = bp[d > np.percentile(d, 85)]
        if len(far) < 30:
            break
        cen.append(np.median(far, 0))
    for _ in range(4):
        dd = np.stack([((bp - c) ** 2).sum(1) for c in cen], 1)
        lab = dd.argmin(1)
        cen = [bp[lab == i].mean(0) if (lab == i).sum() > 20 else cen[i] for i in range(len(cen))]
    dd = np.stack([((bp - c) ** 2).sum(1) for c in cen], 1)
    lab = dd.argmin(1)
    sp = []
    for i in range(len(cen)):
        di = np.sqrt(dd[lab == i, i]) if (lab == i).sum() > 20 else np.array([0.05])
        sp.append(max(0.035, float(np.percentile(di, 92))))
    fl = feat.reshape(-1, 3)
    dist = np.min([np.sqrt(((fl - c) ** 2).sum(1)) / sp[i] for i, c in enumerate(cen)], 0).reshape(h, w)
    return _blur_u8(np.clip(dist * 20.0, 0, 255), 1.2) / 20.0


def _purity_factor(J, q):
    """A sheet is mostly ONE colour (paper) with some print on it; a rectangle that follows a tablecloth's pattern or that holds
    half the table is a mixture of colours.  1.0 = clearly one dominant colour, down to 0.55 for a patchwork."""
    try:
        m = _poly_mask(q, (J.h, J.w))
        px = J.lab[m]
        if len(px) < 30:
            return 1.0
        if len(px) > 4000:
            px = px[:: len(px) // 4000 + 1]
        ref = np.median(px, 0)
        dom = float((np.sqrt(((px - ref) ** 2).sum(1)) < 12.0).mean())
        return 0.55 + 0.45 * float(np.clip((dom - 0.35) / 0.4, 0.0, 1.0))
    except Exception:
        return 1.0


# ======================================================================= v5: fast OpenCV page finder
# The v1-v4 finder above builds ~100 candidate outlines with pure numpy (slow connected-components, thousands of judge calls
# for a hill-climb).  This one does the same job with OpenCV primitives (connected components, contours, LSD line segments,
# Sobel) and replaces the corner hill-climb by a direct least-squares fit of each side to the strongest real edge.
# If OpenCV is missing, or this finder is not confident, the old finder is used.
try:
    import cv2
except Exception:                                    # no OpenCV: the old finder still works
    cv2 = None

FAST_SIDE = 640                                      # long side of the picture used to FIND the outline
REFINE_SIDE = 1100                                   # long side of the picture used to place the 4 sides exactly
FAST_LINES = "never"                                 # straight-line outlines: always / weak (only when the other ways found nothing good) / never
FAST_LINES_T = 0.25
FAST_OUT = 0.15                                      # a corner may lie this share of the picture outside it (a page cut off by the photo)
FAST_WHOLE = 0.8                                     # a bigger outline holding the best one wins when it scores at least this share of it
FAST_CONFIDENT = 0.30                                # below this the old (slow) finder is asked as well


def _f_otsu(u8):
    return float(cv2.threshold(u8, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)[0])


def _f_resize(rgb, side):
    h, w = rgb.shape[:2]
    s = min(1.0, side / float(max(h, w)))
    if s >= 1.0:
        return rgb, 1.0
    return cv2.resize(rgb, (max(8, int(round(w * s))), max(8, int(round(h * s)))), interpolation=cv2.INTER_AREA), s


def _f_iou(q1, q2, size=96):
    pts = np.asarray(list(q1) + list(q2), np.float64)
    x0, y0 = pts.min(0)
    x1, y1 = pts.max(0)
    f = size / max(x1 - x0, y1 - y0, 1.0)
    n = (int((y1 - y0) * f) + 3, int((x1 - x0) * f) + 3)
    ms = []
    for q in (q1, q2):
        m = np.zeros(n, np.uint8)
        cv2.fillPoly(m, [np.rint(((np.asarray(q) - (x0, y0)) * f)).astype(np.int32)], 1)
        ms.append(m > 0)
    return float((ms[0] & ms[1]).sum()) / max(1.0, float((ms[0] | ms[1]).sum()))


class _FJudge:
    """Scores an outline: do its 4 sides lie on real colour edges, is the colour different just inside / outside every side,
    does the outside look like the picture's frame (table), is the inside one paper colour.  All vectorised."""

    def __init__(self, small):
        self.h, self.w = small.shape[:2]
        f = small.astype(np.float32) / 255.0
        lab = cv2.GaussianBlur(cv2.cvtColor(f, cv2.COLOR_RGB2LAB), (0, 0), 1.0)
        self.lab = lab
        self.gx = cv2.Sobel(lab, cv2.CV_32F, 1, 0, ksize=3) / 8.0
        self.gy = cv2.Sobel(lab, cv2.CV_32F, 0, 1, ksize=3) / 8.0
        self.gm = np.sqrt((self.gx ** 2 + self.gy ** 2).sum(2))
        self.gscale = max(float(np.percentile(self.gm, 95)), 2.5)
        self.base = float(np.mean(np.minimum(1.0, self.gm / self.gscale)))
        try:
            self.bg = _bg_distance(small.astype(np.float32))
        except Exception:
            self.bg = None

    def value(self, q, S=40):
        h, w = self.h, self.w
        Q = np.asarray(q, np.float64)
        cen = Q.mean(0)
        B = np.roll(Q, -1, 0)
        D = B - Q
        Ls = np.maximum(np.hypot(D[:, 0], D[:, 1]), 1e-6)
        N = np.stack([D[:, 1], -D[:, 0]], 1) / Ls[:, None]
        N = np.where(((N * ((Q + B) / 2 - cen)).sum(1) < 0)[:, None], -N, N)
        t = np.linspace(0.07, 0.93, S)
        X = Q[:, 0:1] + t[None] * D[:, 0:1]
        Y = Q[:, 1:2] + t[None] * D[:, 1:2]
        mx = max(h, w)
        d_in = max(3.0, 0.016 * mx)
        band = max(2, int(round(0.007 * mx)))
        ok = (X >= 0) & (X <= w - 1) & (Y >= 0) & (Y <= h - 1)
        m_ = 0.012 * mx + 1.5
        near_rim = (X < m_) | (X > w - 1 - m_) | (Y < m_) | (Y > h - 1 - m_)
        EX, EY = X + N[:, 0:1] * 2.2 * d_in, Y + N[:, 1:2] * 2.2 * d_in
        out_of = (EX < 0) | (EX > w - 1) | (EY < 0) | (EY > h - 1)
        on_rim = (near_rim.mean(1) > 0.8) | ((~ok).mean(1) > 0.5) | (out_of.mean(1) > 0.6)

        offs = np.arange(-band, band + 1, 1.0)
        XX = X[:, :, None] + N[:, 0, None, None] * offs
        YY = Y[:, :, None] + N[:, 1, None, None] * offs
        xi = np.clip(np.rint(XX).astype(np.intp), 0, w - 1)
        yi = np.clip(np.rint(YY).astype(np.intp), 0, h - 1)
        dn = self.gx[yi, xi] * N[:, 0, None, None, None] + self.gy[yi, xi] * N[:, 1, None, None, None]
        mag = np.sqrt((dn * dn).sum(-1))
        aligned = mag > 0.55 * self.gm[yi, xi]
        val = np.where(aligned, mag, 0.0).max(2)
        sup_s = np.minimum(1.0, val / self.gscale)
        sup_s = np.where(ok, sup_s, 0.0)
        sup_s.sort(1)
        sup_all = sup_s[:, int(0.15 * S):].mean(1)

        def lab_at(o):
            a = np.clip(np.rint(X + N[:, 0:1] * o).astype(np.intp), 0, w - 1)
            b = np.clip(np.rint(Y + N[:, 1:2] * o).astype(np.intp), 0, h - 1)
            return self.lab[b, a]
        i1 = (lab_at(-d_in) + lab_at(-2.2 * d_in)) / 2
        u = (lab_at(d_in) + lab_at(2.2 * d_in)) / 2
        de_all = np.sqrt(((i1 - u) ** 2).sum(2))
        if self.bg is not None:
            bi = self.bg[np.clip(np.rint(Y - N[:, 1:2] * 1.6 * d_in).astype(np.intp), 0, h - 1),
                         np.clip(np.rint(X - N[:, 0:1] * 1.6 * d_in).astype(np.intp), 0, w - 1)]
            ox, oy = X + N[:, 0:1] * 1.6 * d_in, Y + N[:, 1:2] * 1.6 * d_in
            bo = self.bg[np.clip(np.rint(oy).astype(np.intp), 0, h - 1), np.clip(np.rint(ox).astype(np.intp), 0, w - 1)]
            near_edge = np.minimum(np.minimum(ox, w - 1 - ox), np.minimum(oy, h - 1 - oy))
        ringw = max(3, int(0.045 * min(h, w))) * 1.3
        # paper colour = the lighter half of a coarse grid inside the outline; just OUTSIDE a real page edge the colour is NOT
        # paper (table, wall).  Outside the top of a text block it still is - which is how a text row is told from the page edge.
        uu = np.linspace(0.12, 0.88, 7)
        gx_, gy_ = np.meshgrid(uu, uu)
        top = Q[0] + (Q[1] - Q[0]) * gx_[..., None]
        bot = Q[3] + (Q[2] - Q[3]) * gx_[..., None]
        gp = top + (bot - top) * gy_[..., None]
        gl = self.lab[np.clip(np.rint(gp[..., 1]).astype(np.intp), 0, h - 1), np.clip(np.rint(gp[..., 0]).astype(np.intp), 0, w - 1)].reshape(-1, 3)
        pcol = np.median(gl[gl[:, 0] >= np.median(gl[:, 0])], 0)
        o1 = lab_at(0.7 * d_in)                                           # close to the side: the white margin of a page is narrow
        o2 = lab_at(1.3 * d_in)
        od_all = np.minimum(np.sqrt(((o1 - pcol) ** 2).sum(2)), np.sqrt(((o2 - pcol) ** 2).sum(2)))     # ink on the page does not count as 'not paper'
        outs = []
        n1 = lab_at(-0.25 * d_in)                                          # just INSIDE a side: must still be paper
        n2 = lab_at(-0.5 * d_in)
        nd_all = np.minimum(np.minimum(np.sqrt(((n1 - pcol) ** 2).sum(2)), np.sqrt(((n2 - pcol) ** 2).sum(2))),
                            np.minimum(np.sqrt(((n1 - i1) ** 2).sum(2)), np.sqrt(((n2 - i1) ** 2).sum(2))))   # paper colour: global OR just inside this side (uneven light)
        insd = []
        sup, spl, bgs, inn = [], [], [], []
        rim = 0
        for i in range(4):
            if on_rim[i]:
                rim += 1
                outs.append(0.6); insd.append(0.7)
                sup.append(0.6); spl.append(0.5); bgs.append(0.5)
                inn.append(np.median(i1[i], 0))
                continue
            if ok[i].sum() < 6:
                outs.append(0.0); insd.append(0.0)
                sup.append(0.0); spl.append(0.0); bgs.append(0.0); inn.append(None)
                continue
            outs.append(float(np.clip((np.median(od_all[i][ok[i] & ~out_of[i]] if (ok[i] & ~out_of[i]).any() else od_all[i]) - 3.0) / 7.0, 0.0, 1.0)))
            insd.append(float(np.clip(1.0 - (float(np.median(nd_all[i][ok[i]])) - 6.0) / 10.0, 0.0, 1.0)))
            sup.append(float(sup_all[i]))
            good = ok[i] & ~out_of[i]
            spl.append(float(np.median(de_all[i][good])) / 9.0 if good.any() else 0.6)
            inn.append(np.median(i1[i][ok[i]], 0))
            if self.bg is None or not good.any():
                bgs.append(0.6)
            elif float(np.median(near_edge[i])) < ringw:
                bgs.append(0.5)
            else:
                bgs.append(float(np.clip((np.median(bi[i][good]) - np.median(bo[i][good])) / 3.0, 0.0, 1.0)))
        iv = [v for v in inn if v is not None]
        flat = 0.0
        if len(iv) >= 3:
            flat = max(float(np.sqrt(((iv[a] - iv[b]) ** 2).sum())) for a in range(len(iv)) for b in range(a + 1, len(iv)))
        sup = np.array(sup)
        spl = np.clip(np.array(spl), 0.0, 1.0)
        # the corners of a sheet are about right angles, even in perspective (slightly relaxed)
        ang = []
        for i in range(4):
            a_, b_, c_ = Q[i - 1] - Q[i], Q[(i + 1) % 4] - Q[i], None
            ca = float(a_ @ b_) / (np.hypot(*a_) * np.hypot(*b_) + 1e-9)
            ang.append(math.degrees(math.acos(max(-1.0, min(1.0, ca)))))
        dev = max(abs(a - 90.0) for a in ang)
        shape = 1.0 - 0.45 * float(np.clip((dev - 22.0) / 30.0, 0.0, 1.0))
        area = _poly_area(Q) / float(h * w)
        sz = min(1.0, area / 0.16) ** 0.55 * (min(1.0, area / 0.9) ** 0.3) * (1.0 - max(0.0, area - 0.93) * 6)
        edge = 0.5 * float(sup.mean()) + 0.5 * float(sup.min())
        split = 0.5 * float(spl.mean()) + 0.5 * float(spl.min())
        fl = 1.0 / (1.0 + max(0.0, flat - 18.0) / 28.0)
        bgv = 0.5 * float(np.mean(bgs)) + 0.5 * float(np.min(bgs))
        outv = 0.5 * float(np.mean(outs)) + 0.5 * float(np.min(outs))
        insv = 0.5 * float(np.mean(insd)) + 0.5 * float(np.min(insd))
        v = (0.25 + 0.75 * outv) * (0.35 + 0.65 * insv) * ((0.10 + edge) ** 1.3 * (0.07 + split) ** 0.9 * sz * (0.5 + 0.5 * fl) * (0.30 + 0.70 * bgv) * shape
             * (1.0, 0.92, 0.72, 0.36, 0.12)[min(4, rim)])
        return v, {"edge_mean": float(sup.mean()), "edge_min": float(sup.min()), "split_mean": float(spl.mean()),
                   "bg_mean": float(np.mean(bgs)), "rim": rim, "area": area, "sup": sup, "spl": spl, "out": outs, "ins": insd}

    def purity(self, q):
        """1.0 = clearly one dominant colour inside (a sheet), down to 0.55 for a patchwork (cloth pattern, half a table)."""
        m = np.zeros((self.h, self.w), np.uint8)
        cv2.fillPoly(m, [np.rint(np.asarray(q)).astype(np.int32)], 1)
        px = self.lab[m > 0]
        if len(px) < 30:
            return 1.0
        if len(px) > 4000:
            px = px[:: len(px) // 4000 + 1]
        ref = np.median(px, 0)
        dom = float((np.sqrt(((px - ref) ** 2).sum(1)) < 12.0).mean())
        return 0.55 + 0.45 * float(np.clip((dom - 0.35) / 0.4, 0.0, 1.0))


def _f_quads_from_mask(mask, tag, out, topk=3, hit=None):
    h, w = mask.shape
    m = mask.astype(np.uint8)
    n, lab, stats, _ = cv2.connectedComponentsWithStats(m, connectivity=4)
    if n <= 1:
        return
    order = np.argsort(-stats[1:, cv2.CC_STAT_AREA])[:topk] + 1
    if hit is not None:                                      # only the region that holds the seed
        ov = np.bincount(lab[hit > 0].ravel(), minlength=n)
        ov[0] = 0
        order = [int(np.argmax(ov))] if ov.max() > 0 else []
    for cid in order:
        if stats[cid, cv2.CC_STAT_AREA] < 0.015 * h * w:
            break
        x, y, bw, bh = stats[cid, :4]
        sides = int(y == 0) + int(y + bh == h) + int(x == 0) + int(x + bw == w)
        if sides >= 4:
            continue
        comp = (lab == cid).astype(np.uint8)
        cnts, _ = cv2.findContours(comp, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_NONE)
        if not cnts:
            continue
        c = max(cnts, key=cv2.contourArea)
        hull_i = cv2.convexHull(c)
        if len(hull_i) < 3:
            continue
        hull = [tuple(map(float, p)) for p in hull_i[:, 0, :]]
        ha = _poly_area(hull)
        if ha < 0.02 * h * w or ha > 0.985 * h * w:
            continue
        solid = min(1.0, float(cv2.contourArea(c)) / max(ha, 1.0))
        pts = c[:, 0, :]
        quads = []
        quads.append(_simplify_enclosing(hull))
        quads.append(_simplify_to_quad(hull))
        quads.append([tuple(map(float, p)) for p in cv2.boxPoints(cv2.minAreaRect(hull_i))])
        eps = 0.01
        while eps < 0.12:                                       # Douglas-Peucker down to 4 corners
            ap = cv2.approxPolyDP(hull_i, eps * cv2.arcLength(hull_i, True), True)
            if len(ap) <= 4:
                if len(ap) == 4:
                    quads.append([tuple(map(float, p)) for p in ap[:, 0, :]])
                break
            eps *= 1.5
        for quad in quads:
            if len(quad) != 4:
                continue
            quad = _order(quad)
            if not _is_convex(quad):
                continue
            qa = _poly_area(quad)
            if qa > 1.4 * ha or qa < 0.88 * ha:
                continue
            out.append({"quad": quad, "solid": solid, "tag": tag})


def _f_dedupe(cands, tol):
    """Many ways of segmenting give the very same outline: keep one (corner distance below tol px)."""
    keep, arrs = [], []
    for c in cands:
        a = np.asarray(c["quad"], np.float64)
        if arrs and (np.abs(np.stack(arrs) - a).max(axis=(1, 2)) < tol).any():
            continue
        keep.append(c)
        arrs.append(a)
    return keep


def _f_clean(mask, close, open_=0):
    m = mask.astype(np.uint8)
    if close:
        m = cv2.morphologyEx(m, cv2.MORPH_CLOSE, cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (close, close)))
    if open_:
        m = cv2.morphologyEx(m, cv2.MORPH_OPEN, cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (open_, open_)))
    return m


def _f_candidates(small, J):
    h, w = small.shape[:2]
    gray = cv2.cvtColor(small, cv2.COLOR_RGB2GRAY)
    gb = cv2.GaussianBlur(gray, (0, 0), 1.2)
    mx, mn = small.max(2).astype(np.float32), small.min(2).astype(np.float32)
    sat = (mx - mn) / np.maximum(mx, 1.0)
    P = cv2.GaussianBlur(np.clip(gray.astype(np.float32) * (1.0 - 0.75 * sat), 0, 255).astype(np.uint8), (0, 0), 1.2)
    c = []
    k5 = 5
    for arr, both in ((gb, True), (P, False)):
        t = _f_otsu(arr)
        for dt in (0, -20, 20, -40, 40):
            thr = int(min(250, max(5, t + dt)))
            _f_quads_from_mask(_f_clean(arr > thr, k5, k5), "light", c)
            if both:
                _f_quads_from_mask(_f_clean(arr <= thr, k5, k5), "dark", c)
    # colour edges (brightness AND hue): the area they enclose
    labu = cv2.cvtColor(small, cv2.COLOR_RGB2LAB)
    for ch in (gb, labu[..., 1], labu[..., 2]):
        chb = cv2.GaussianBlur(ch, (0, 0), 1.0)
        med = float(np.median(chb))
        for sg in (0.33, 0.6):
            e = cv2.Canny(chb, max(8, (1 - sg) * med * 0.5), max(24, (1 + sg) * med * 0.5 + 20))
            e = cv2.dilate(e, np.ones((3, 3), np.uint8))
            _f_quads_from_mask(e == 0, "edge", c)
    # what is NOT like the frame of the picture (table, wall, cloth)
    if J.bg is not None:
        d8 = np.clip(J.bg * 20.0, 0, 255).astype(np.uint8)
        t = _f_otsu(d8) / 20.0
        for thr in sorted({max(1.6, t), max(2.2, t * 1.4), 3.2}):
            _f_quads_from_mask(_f_clean(_f_clean(J.bg > thr, 5, 0), 0, 5), "border", c)
    # two colour groups (paper / the rest)
    try:
        lab = J.lab
        feat = np.stack([lab[..., 0] / 100.0, lab[..., 1] / 90.0 * 1.6, lab[..., 2] / 90.0 * 1.6], -1).reshape(-1, 3)
        sub = np.ascontiguousarray(feat[:: max(1, len(feat) // 6000)], np.float32)
        crit = (cv2.TERM_CRITERIA_EPS + cv2.TERM_CRITERIA_MAX_ITER, 12, 0.5)
        _, _, cen = cv2.kmeans(sub, 2, None, crit, 2, cv2.KMEANS_PP_CENTERS)
        d0 = ((feat - cen[0]) ** 2).sum(1)
        d1 = ((feat - cen[1]) ** 2).sum(1)
        m2 = cv2.medianBlur((d0 < d1).reshape(h, w).astype(np.uint8) * 255, 5) > 0
        _f_quads_from_mask(m2, "kmeans", c)
        _f_quads_from_mask(~m2, "kmeans", c)
    except Exception:
        pass
    # smooth regions (a sheet is smooth, a wooden table is not)
    try:
        m1 = cv2.blur(gb.astype(np.float32), (5, 5))
        sd = np.sqrt(np.maximum(cv2.blur(gb.astype(np.float32) ** 2, (5, 5)) - m1 * m1, 0.0))
        for pct in (45, 60):
            t = max(float(np.percentile(sd, pct)), 3.0)
            _f_quads_from_mask(_f_clean(_f_clean(sd < t, 13, 0), 0, 7), "smooth", c)
    except Exception:
        pass
    return c


def _f_grow(J, seed_quad, shrink, out, tag="grow"):
    """Paper colour from inside a seed outline, then every connected pixel of that colour: the sheet itself, without the
    laminate, the table or a ruler next to it (their colour differs a little, and that is enough)."""
    h, w = J.h, J.w
    q = np.asarray(seed_quad, np.float64)
    cen = q.mean(0)
    m0 = np.zeros((h, w), np.uint8)
    cv2.fillPoly(m0, [np.rint(cen + (q - cen) * shrink).astype(np.int32)], 1)
    pix = J.lab[m0 > 0]
    if len(pix) < 50:
        return
    pc = np.median(pix[pix[:, 0] >= np.percentile(pix[:, 0], 45)], 0)
    d = np.sqrt(((J.lab - pc) ** 2).sum(2))
    for T in (7.0, 11.0, 16.0):
        m = _f_clean(_f_clean(d < T, 7, 0), 0, 5)
        _f_quads_from_mask(m, tag, out, topk=1, hit=m0)


def _f_lines(small, max_lines=16):
    """Straight lines that run along real edges: LSD segments of brightness and of both colour channels, merged into lines."""
    h, w = small.shape[:2]
    labu = cv2.cvtColor(small, cv2.COLOR_RGB2LAB)
    chans = [cv2.GaussianBlur(labu[..., i], (0, 0), 0.8) for i in range(3)]
    segs = []
    try:
        lsd = cv2.createLineSegmentDetector(cv2.LSD_REFINE_STD)
        for ch in chans:
            r = lsd.detect(ch)[0]
            if r is not None:
                segs.extend(np.asarray(r).reshape(-1, 4).tolist())          # (N,1,4) in OpenCV 4, (N,4) in 5
    except Exception:
        for ch in chans:
            e = cv2.Canny(ch, 30, 90)
            r = cv2.HoughLinesP(e, 1, np.pi / 180, 40, minLineLength=int(0.12 * max(h, w)), maxLineGap=6)
            if r is not None:
                segs.extend(np.asarray(r).reshape(-1, 4).tolist())
    minlen = 0.05 * max(h, w)
    groups = []                                           # [nx, ny, rho, weight]
    for x1, y1, x2, y2 in sorted(segs, key=lambda s: -math.hypot(s[2] - s[0], s[3] - s[1])):
        L = math.hypot(x2 - x1, y2 - y1)
        if L < minlen:
            break
        nx, ny = (y1 - y2) / L, (x2 - x1) / L
        rho = nx * (x1 + x2) / 2 + ny * (y1 + y2) / 2
        for g in groups:
            dot = nx * g[0] + ny * g[1]
            s_ = 1.0 if dot >= 0 else -1.0
            if abs(dot) > 0.9976 and abs(s_ * rho - g[2]) < 0.012 * math.hypot(h, w):        # < 4 deg and ~1 % of the diagonal
                wsum = g[3] + L
                g[0] = (g[0] * g[3] + s_ * nx * L) / wsum
                g[1] = (g[1] * g[3] + s_ * ny * L) / wsum
                nn = math.hypot(g[0], g[1]) or 1.0
                g[0] /= nn; g[1] /= nn
                g[2] = (g[2] * g[3] + s_ * rho * L) / wsum
                g[3] = wsum
                break
        else:
            groups.append([nx, ny, rho, L])
    groups.sort(key=lambda g: -g[3])
    lines = []
    for nx, ny, rho, wt in groups[:max_lines]:
        phi = math.atan2(ny, nx)
        if phi < 0:
            phi += math.pi
            rho = -rho
        if phi >= math.pi:
            phi -= math.pi
            rho = -rho
        lines.append((phi, rho, wt))
    return lines


class _FRefiner:
    """Places the 4 sides exactly: for many points along a side finds the sharpest colour step across it (within a small
    distance of where the side is now), fits ONE straight line through those steps (outliers - a printed frame, a fold, a
    finger - are trimmed away), and crosses the four lines.  Replaces the old corner-by-corner hill-climb."""

    def __init__(self, mid):
        f = mid.astype(np.float32) / 255.0
        lab = cv2.GaussianBlur(cv2.cvtColor(f, cv2.COLOR_RGB2LAB), (0, 0), 1.2)
        self.h, self.w = mid.shape[:2]
        self.gx = cv2.Sobel(lab, cv2.CV_32F, 1, 0, ksize=3) / 8.0
        self.gy = cv2.Sobel(lab, cv2.CV_32F, 0, 1, ksize=3) / 8.0
        self.L = lab[..., 0]

    def _at(self, a, X, Y):
        return a[np.clip(np.rint(Y).astype(np.intp), 0, self.h - 1), np.clip(np.rint(X).astype(np.intp), 0, self.w - 1)]

    def refine(self, quad, search):
        h, w = self.h, self.w
        lines = []
        M = 80
        for a, b, n, L in _quad_sides(quad):
            t = np.linspace(0.06, 0.94, M)
            px = a[0] + (b[0] - a[0]) * t
            py = a[1] + (b[1] - a[1]) * t
            offs = np.arange(-search, search + 0.01, 1.0)
            X = px[:, None] + n[0] * offs[None, :]
            Y = py[:, None] + n[1] * offs[None, :]
            dn = self._at(self.gx, X, Y) * n[0] + self._at(self.gy, X, Y) * n[1]            # (M, O, 3)
            mag = np.sqrt((dn * dn).sum(-1))
            s1 = np.abs(self._at(self.L, X + n[0] * 1.5, Y + n[1] * 1.5) - self._at(self.L, X - n[0] * 1.5, Y - n[1] * 1.5))
            s2 = np.abs(self._at(self.L, X + n[0] * 6.0, Y + n[1] * 6.0) - self._at(self.L, X - n[0] * 6.0, Y - n[1] * 6.0))
            crisp = np.clip(s1 / (s2 + 3.0), 0.0, 1.0)                                      # sharp edge ~1, soft shadow small
            score = mag * (0.35 + 0.65 * crisp) * (1.0 - 0.35 * np.abs(offs)[None, :] / max(1.0, search))
            k = score.argmax(1)
            best = score.max(1)
            strong = best > 0.25 * (best.max() + 1e-6)
            ob = offs[k]
            sel = strong.copy()
            fit = None
            for _ in range(5):
                if sel.sum() < 0.35 * M:
                    fit = None
                    break
                fit = np.polyfit(t[sel], ob[sel], 1, w=best[sel])
                res = np.abs(ob - np.polyval(fit, t))
                nsel = strong & (res <= max(1.0, 2.0 * float(np.median(res[sel]))))
                if (nsel == sel).all():
                    break
                sel = nsel
            if fit is None or sel.sum() < 0.35 * M:
                lines.append((np.asarray(a, np.float64), (np.asarray(b) - np.asarray(a)) / max(L, 1e-9)))
                continue
            o0, o1 = float(np.polyval(fit, 0.0)), float(np.polyval(fit, 1.0))
            o0, o1 = max(-search, min(search, o0)), max(-search, min(search, o1))
            p0 = np.asarray(a, np.float64) + n * o0
            p1 = np.asarray(b, np.float64) + n * o1
            d = p1 - p0
            lines.append((p0, d / max(np.hypot(*d), 1e-9)))
        out = []
        for i in range(4):
            c1, d1 = lines[i - 1]
            c2, d2 = lines[i]
            Mx = np.array([d1, -d2]).T
            if abs(np.linalg.det(Mx)) < 1e-6:
                return quad
            tt = np.linalg.solve(Mx, c2 - c1)
            out.append(tuple(c1 + tt[0] * d1))
        out = _order(out)
        old = np.asarray(quad, np.float64)
        if (not _is_convex(out) or np.abs(np.asarray(out) - old).max() > 2.5 * search
                or not (0.85 < _poly_area(out) / max(_poly_area(quad), 1.0) < 1.18)):
            return quad
        return out


def _f_margin_is_paper(lab, old, new, tol=14.0, need=0.7):
    """The band between the old and the widened outline must be PAPER (the white margin the first outline missed).  If it is
    table / cloth / wall the widening ran over the edge of the page and is thrown away."""
    h, w = lab.shape[:2]
    mo = np.zeros((h, w), np.uint8)
    me = np.zeros((h, w), np.uint8)
    cv2.fillPoly(mo, [np.rint(np.asarray(old)).astype(np.int32)], 1)
    cv2.fillPoly(me, [np.rint(np.asarray(new)).astype(np.int32)], 1)
    ring = (me > 0) & (mo == 0)
    inner = cv2.erode(mo, np.ones((9, 9), np.uint8)) > 0
    if ring.sum() < 20 or inner.sum() < 50:
        return True
    pix = lab[inner]
    pc = np.median(pix[pix[:, 0] >= np.percentile(pix[:, 0], 40)], 0)
    d = np.sqrt(((lab[ring] - pc) ** 2).sum(1))
    return float((d < tol).mean()) >= need


def _f_inside(q, h, w, m=None):
    """No corner far outside the picture (the sides of a hull cut by the picture's edge get extended into nowhere)."""
    m = FAST_OUT if m is None else m
    return all(-m * w <= x <= (1 + m) * w and -m * h <= y <= (1 + m) * h for x, y in q)


def _detect_fast(img, want_debug=None):
    """Returns (corners in picture pixels, found, info) or None."""
    rgb = np.asarray(img.convert("RGB"))
    H, W = rgb.shape[:2]
    small, s = _f_resize(rgb, FAST_SIDE)
    h, w = small.shape[:2]
    J = _FJudge(small)
    pool = []
    for c in _f_dedupe(_f_candidates(small, J), 0.006 * max(h, w)):
        q = c["quad"]
        if _quad_ok(q, h, w):
            pool.append((J.value(q)[0], q, c["tag"]))
    # paper-colour growth from the best few outlines and from the middle of the picture
    seeds = [(q_, 0.6) for _, q_, _t in sorted(pool, key=lambda r: -r[0])[:4]]
    seeds.append(([(0.3 * w, 0.3 * h), (0.7 * w, 0.3 * h), (0.7 * w, 0.7 * h), (0.3 * w, 0.7 * h)], 1.0))
    grown = []
    for sq, sh in seeds:
        try:
            _f_grow(J, sq, sh, grown)
        except Exception:
            pass
    for c in _f_dedupe(grown, 0.006 * max(h, w)):
        q = c["quad"]
        if _quad_ok(q, h, w):
            pool.append((J.value(q)[0], q, c["tag"]))
    pool = [r for r in pool if _f_inside(r[1], h, w)]
    best_region = max([p_[0] for p_ in pool], default=0.0)
    if FAST_LINES == "always" or (FAST_LINES == "weak" and best_region < FAST_LINES_T):
        try:
            for q, ar, vt in _line_quads(_f_lines(small), h, w, limit=200):
                if _quad_ok(q, h, w):
                    pool.append((J.value(q)[0], q, "lines"))
        except Exception:
            pass
    if not pool:
        return None
    pool.sort(key=lambda r: -r[0])
    starts = []
    for v, q, tag in pool:
        if any(_f_iou(q, o[1]) > 0.9 for o in starts):
            continue
        starts.append((v, q, tag))
        if len(starts) >= 10:
            break
    if want_debug is not None:
        want_debug.extend([(v, tag, [(x / s, y / s) for x, y in q]) for v, q, tag in pool[:60]])
    mid, sm = _f_resize(rgb, REFINE_SIDE)
    f = sm / s                                                 # small-picture pixels -> refine-picture pixels
    R = _FRefiner(mid)
    search = max(3.0, 0.012 * max(mid.shape[:2]))
    scored = []
    for v0, q, tag in starts[:7]:
        q0 = q
        pu = J.purity(q)
        try:
            qm = R.refine([(x * f, y * f) for x, y in q], search)
            q1 = [(x / f, y / f) for x, y in qm]
            if _quad_ok(q1, h, w):
                v1 = J.value(q1)[0]
                if v1 >= 0.97 * v0:
                    q, v0 = q1, v1
        except Exception:
            pass
        scored.append((v0 * pu, q, q0))
    scored.sort(key=lambda r: -r[0])
    if want_debug is not None:
        want_debug.append(("scored", [(r[0], [(x / s, y / s) for x, y in r[1]]) for r in scored], None))
    pick = scored[0]
    # a fold, a printed frame or a header inside the page also has strong edges: when a bigger outline that holds the best one
    # comes close in value, it is the whole sheet - take it
    if len(scored) > 1:
        a0 = _poly_area(pick[1])
        m0 = np.zeros((h, w), np.uint8)
        cv2.fillPoly(m0, [np.rint(np.asarray(pick[1])).astype(np.int32)], 1)
        wholes = []
        for c in scored[1:]:
            ac = _poly_area(c[1])
            if ac < 1.25 * a0 or ac > 4.5 * a0 or c[0] < FAST_WHOLE * pick[0]:
                continue
            mc = np.zeros((h, w), np.uint8)
            cv2.fillPoly(mc, [np.rint(np.asarray(c[1])).astype(np.int32)], 1)
            if (m0 & mc).sum() >= 0.8 * m0.sum():
                wholes.append(c)
        if wholes:
            pick = max(wholes, key=lambda r: r[0])
    val, quad = pick[0], pick[1]
    v_, info_ = J.value(quad)
    if val <= 0.07 or info_["edge_mean"] < 1.12 * J.base:
        return None
    pts = _order(quad)
    try:                                                       # page edge that is nearly the table's colour: find it by colour
        labs = cv2.cvtColor(mid.astype(np.float32) / 255.0, cv2.COLOR_RGB2LAB)
        q_old = [(x * f, y * f) for x, y in pts]
        ex, moved = _expand_to_paper(labs, q_old)
        if moved and _f_margin_is_paper(labs, q_old, ex):
            pts = _order([(x / f, y / f) for x, y in ex])
    except Exception:
        pass
    res = [(min(max(x / s, 0.0), W - 1.0), min(max(y / s, 0.0), H - 1.0)) for x, y in pts]
    info = {"score": min(1.2, val / 0.20), "edge": info_["edge_mean"], "contrast": info_["split_mean"], "engine": "fast"}
    return res, True, info


FAST_RESCUE = True                                   # not confident -> also ask the old, slower finder and keep the better one
FAST_SURE = 0.55                                     # score (as in the 'info' dict) from which the fast answer is trusted


def detect_document(img, return_score=False):
    """Finds the 4 corners of the paper. Returns (corners [(x,y) TL,TR,BR,BL in picture pixels], found: bool).
    First the fast OpenCV finder (a fraction of a second); only when it is not sure the old finder (several seconds) is asked as
    well and the better answer is kept."""
    best = None
    if cv2 is not None:
        try:
            best = _detect_fast(img)
        except Exception:
            best = None
    if best is None:                                  # the fast finder found no sheet at all: ask the old, slower one
        best = _detect_document_legacy(img, True)
    res, found, info = best
    return (res, found, info) if return_score else (res, found)
