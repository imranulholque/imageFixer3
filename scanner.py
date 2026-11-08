"""Document scanning maths (no GUI): find the paper in a photo, and straighten it."""
import math

import numpy as np
from PIL import Image, ImageDraw, ImageFilter

import core

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
    gx = np.zeros_like(G)
    gy = np.zeros_like(G)
    gx[:, 1:-1] = (G[:, 2:] - G[:, :-2]) * 0.5
    gy[1:-1] = (G[2:] - G[:-2]) * 0.5
    return gx, gy, np.hypot(gx, gy)


def _sample(a, xs, ys):
    """Nearest-neighbour samples of a 2-D array at float positions (clipped to the picture)."""
    h, w = a.shape
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
            along = np.abs(_sample(gx, xs, ys) * n[0] + _sample(gy, xs, ys) * n[1])        # step across the side
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
            along = np.abs(_sample(gx, xs, ys) * n[0] + _sample(gy, xs, ys) * n[1])
            score[k] = along * (1.0 - 0.35 * abs(o) / max(1.0, search))                    # prefer the nearest strong edge
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
    best = None
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
        if best is None or sc > best[0]:
            best = (sc, q, edge, con)
    gh, gw = G.shape
    if best is not None and best[0] > 0.25 and best[2] > 0.18:
        q = best[1]
        # snap to the edges at a larger size
        s2 = min(2.5, 900.0 / float(max(W, H)) / s) if s * max(W, H) < 900 else 1.0
        if s2 > 1.2:
            big = img.convert("L").resize((max(8, round(W * s * s2)), max(8, round(H * s * s2))), Image.Resampling.BOX)
            Gb = _blur_u8(np.asarray(big, np.float32), 1.0)
            qb = [(x * s2, y * s2) for x, y in q]
            qb = _refine_on_edges(qb, Gb, search=max(4.0, 0.025 * max(Gb.shape)))
            q = [(x / s2, y / s2) for x, y in qb]
        pts = core.order_quad(q)
        res = [(x / s, y / s) for x, y in pts]
        return (res, True, best[0]) if return_score else (res, True)
    mx_, my_ = 0.04 * W, 0.04 * H
    res = [(mx_, my_), (W - mx_, my_), (W - mx_, H - my_), (mx_, H - my_)]
    return (res, False, 0.0) if return_score else (res, False)


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
