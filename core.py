"""Image Fixer - all picture maths (no GUI code here, so it can be tested on its own)."""
import colorsys
import math
import os
import uuid

import numpy as np
from PIL import Image, ImageChops, ImageDraw, ImageOps
from PIL import ImageFilter as _PilFilter


class _RankFilter(_PilFilter.Filter):
    """Min / Max filter that does not keep Python's lock while it works (Pillow's does, which froze the window)."""
    def __init__(self, size, use_max):
        self.size, self.use_max = int(size), use_max

    def filter(self, image):
        a = np.asarray(Image.Image()._new(image))
        return Image.fromarray(min_filter(a, self.size, self.use_max)).im


class ImageFilter:                                  # the names used in this file; the rank filters are our own
    GaussianBlur = _PilFilter.GaussianBlur

    @staticmethod
    def MaxFilter(size=3):
        return _RankFilter(size, True)

    @staticmethod
    def MinFilter(size=3):
        return _RankFilter(size, False)


Image.MAX_IMAGE_PIXELS = 400_000_000

IMG_EXT = {".jpg", ".jpeg", ".jfif", ".png", ".webp", ".bmp", ".gif", ".tif", ".tiff"}
try:  # iPhone photos (optional)
    import pillow_heif

    pillow_heif.register_heif_opener()
    IMG_EXT |= {".heic", ".heif"}
except Exception:
    pass

PDF_EXT = ".pdf"
PDF_DPI = 200               # a PDF page becomes a picture of about this resolution
PDF_MAX_SIDE = 4200         # ... but never longer than this (pixels), so a big poster page does not eat all memory

PREVIEW_MAX = 2400          # long side of the on-screen working copy (set at start-up from the screen, see below)
QUALITY_LEVELS = {"fast": 1800, "balanced": 2400, "sharp": 3200, "max": 4096}


def set_preview_max(n):
    """Size of the on-screen working copy.  Bigger = sharper when zoomed in, a little slower to adjust."""
    global PREVIEW_MAX
    PREVIEW_MAX = int(max(800, min(8192, n)))


def auto_preview_max(screen_long_px, cpus=None):
    """A working copy that is a bit larger than the screen can show: sharp on this machine, not wasteful on a small one."""
    cpus = cpus or os.cpu_count() or 4
    n = int(max(1.0, float(screen_long_px)) * 1.15)
    n = max(2400, min(n, 3600))                  # never below the old default, larger on big / sharp screens
    if cpus <= 4:
        n = min(n, 2800)
    return n


def quality_preview_max(level, screen_long_px=1920, cpus=None):
    """'auto' (from the screen and the number of processor cores) or one of QUALITY_LEVELS."""
    if level in QUALITY_LEVELS:
        return QUALITY_LEVELS[level]
    return auto_preview_max(screen_long_px, cpus)
NEUTRAL_C = 14.0            # chroma below this counts as black / gray
NEUTRAL_SOFT = 12.0
BAND_EDGE = 4.0             # softness between two neighbouring gray bands (L units)


# ----------------------------------------------------------------- loading
def load_image(path):
    """Open any picture as plain RGB. WhatsApp / phone photos are rotated by EXIF, so fix that."""
    im = Image.open(path)
    im.load()
    try:
        im = ImageOps.exif_transpose(im)
    except Exception:
        pass
    if im.mode in ("I;16", "I;16L", "I;16B", "I", "F"):
        arr = np.asarray(im, dtype=np.float32)
        top = float(arr.max()) or 1.0
        arr = arr / (65535.0 if top > 255 else 255.0) * 255.0
        return Image.fromarray(np.clip(arr, 0, 255).astype(np.uint8)).convert("RGB")
    if im.mode in ("RGBA", "LA", "PA") or (im.mode == "P" and "transparency" in im.info):
        rgba = im.convert("RGBA")
        bg = Image.new("RGB", rgba.size, (255, 255, 255))
        bg.paste(rgba, mask=rgba.split()[3])
        return bg
    return im.convert("RGB")


def make_preview(img):
    """(uint8 array, scale). scale = preview size / real size (1.0 for normal pictures)."""
    w, h = img.size
    m = max(w, h)
    if m <= PREVIEW_MAX:
        return np.array(img), 1.0
    s = PREVIEW_MAX / m
    small = img.resize((max(1, round(w * s)), max(1, round(h * s))), Image.Resampling.LANCZOS)
    return np.array(small), small.size[0] / w


# ------------------------------------------------------------------- colour
_x = np.arange(256, dtype=np.float32) / 255.0
_LUT = np.where(_x <= 0.04045, _x / 12.92, ((_x + 0.055) / 1.055) ** 2.4).astype(np.float32)
_M = np.array([[0.4124564, 0.3575761, 0.1804375],
               [0.2126729, 0.7151522, 0.0721750],
               [0.0193339, 0.1191920, 0.9503041]], dtype=np.float32)
_WHITE = np.array([0.95047, 1.0, 1.08883], dtype=np.float32)
_MT = np.ascontiguousarray((_M / _WHITE[:, None]).T)


def rgb_to_lab(rgb):
    """uint8 (...,3) -> float32 Lab (...,3), D65."""
    shape = rgb.shape
    lin = _LUT[rgb.reshape(-1, 3)]
    xyz = lin @ _MT
    f = np.where(xyz > 0.008856, np.cbrt(xyz), 7.787 * xyz + 16.0 / 116.0).astype(np.float32)
    out = np.empty_like(f)
    out[:, 0] = 116.0 * f[:, 1] - 16.0
    out[:, 1] = 500.0 * (f[:, 0] - f[:, 1])
    out[:, 2] = 200.0 * (f[:, 1] - f[:, 2])
    return out.reshape(shape)


def L_to_gray(L):
    fy = (L + 16.0) / 116.0
    y = fy ** 3 if fy ** 3 > 0.008856 else (fy - 16.0 / 116.0) / 7.787
    v = 12.92 * y if y <= 0.0031308 else 1.055 * (y ** (1 / 2.4)) - 0.055
    return int(round(255 * min(1.0, max(0.0, v))))


def hex_of(rgb):
    return "#%02x%02x%02x" % tuple(int(v) for v in rgb)


def color_name(rgb):
    r, g, b = [v / 255.0 for v in rgb]
    h, s, v = colorsys.rgb_to_hsv(r, g, b)
    d = h * 360.0
    if 15 <= d < 50 and v < 0.55:
        return "Brown"
    for lim, name in ((15, "Red"), (45, "Orange"), (70, "Yellow"), (165, "Green"), (200, "Cyan"),
                      (255, "Blue"), (290, "Purple"), (340, "Pink"), (361, "Red")):
        if d < lim:
            return name
    return "Color"


def band_name(L):
    for lim, name in ((12, "Deep black"), (26, "Black"), (42, "Dark ash"), (58, "Ash"),
                      (74, "Light ash"), (88, "Pale ash"), (101, "Near white")):
        if L < lim:
            return name
    return "Near white"


def _smooth(x):
    x = np.clip(x, 0.0, 1.0)
    return x * x * (3.0 - 2.0 * x)


# -------------------------------------------------------------- adjustments
# An adjustment is a plain dict, so it can be copied for undo:
#   colour : {"type":"color","target":[L,a,b],"tol":30,"k":100,"feather":0,...}
#   band   : {"type":"band","lo":..,"hi":..,"center":L,"k":100,"feather":0,...}   (black / gray shades)
# k = 100 -> unchanged
# k < 100 -> that colour is removed and replaced by what surrounds it (0 = fully gone)
# k > 100 -> colours get stronger and clearer;  black / gray shades get darker
def new_color_adj(rgb, lab_pt, tol=30.0, feather=0):
    rgb = [int(v) for v in rgb]
    return {"id": uuid.uuid4().hex[:8], "type": "color",
            "target": [float(lab_pt[0]), float(lab_pt[1]), float(lab_pt[2])],
            "rgb": rgb, "tol": float(tol), "k": 100, "feather": int(feather),
            "label": "%s  %s" % (color_name(rgb), hex_of(rgb))}


def new_band_adj(center, lo, hi, feather=0):
    g = L_to_gray(center)
    return {"id": uuid.uuid4().hex[:8], "type": "band", "lo": lo, "hi": hi,
            "center": float(center), "rgb": [g, g, g], "k": 100, "feather": int(feather),
            "label": band_name(center)}


def adj_key(a):
    f = int(a.get("feather", 0))
    if a["type"] == "color":
        t = a["target"]
        return ("c", round(t[0], 1), round(t[1], 1), round(t[2], 1), round(a["tol"], 1), f)
    return ("b", None if a["lo"] is None else round(a["lo"], 1), None if a["hi"] is None else round(a["hi"], 1), f)


def _neutral_factor(lab):
    C = np.hypot(lab[..., 1], lab[..., 2])
    return 1.0 - _smooth((C - NEUTRAL_C) / NEUTRAL_SOFT)


def color_match(lab, t, tol):
    """Pixels that have the SAME COLOUR as target t (Lab), whatever their lightness: same hue, enough colour strength.
    A black pixel with a hint of red, or a grey one, is NOT matched (it has too little colour)."""
    Ct = math.hypot(t[1], t[2])
    a, b = lab[..., 1], lab[..., 2]
    C = np.hypot(a, b)
    dh = np.abs((np.arctan2(b, a) - math.atan2(t[2], t[1]) + math.pi) % (2 * math.pi) - math.pi)
    dh = np.degrees(dh)
    hw = 12.0 + tol * 0.5
    hue_g = 1.0 - _smooth((dh - hw * 0.75) / (hw * 0.5))
    chroma_g = _smooth((C - 0.32 * Ct) / (0.25 * Ct))
    dL = np.abs(lab[..., 0] - t[0])
    light_g = 1.0 - _smooth((dL - tol * 1.6) / (tol * 0.8))
    return (hue_g * chroma_g * light_g).astype(np.float32)


def color_match(lab, t, tol):
    """0..1: how much each pixel IS colour t=(L,a,b).  Hue and chroma decide first, lightness counts less, and
    grey / black pixels (even with a faint colour cast) never match a strong colour, so selecting red does not
    take the black writing with it."""
    L, a, b = lab[..., 0], lab[..., 1], lab[..., 2]
    da, db = a - t[1], b - t[2]
    dL = (L - t[0]) * 0.5
    d = np.sqrt(dL * dL + da * da + db * db)
    inner, outer = tol * 0.7, tol * 1.3
    m = 1.0 - _smooth((d - inner) / (outer - inner))
    Ct = math.hypot(t[1], t[2])
    if Ct > 18.0:
        C = np.hypot(a, b)
        m = m * _smooth((C - 0.2 * Ct) / (0.2 * Ct))                      # must be coloured too
        cosang = (a * t[1] + b * t[2]) / (C * Ct + 1e-3)
        dh = np.degrees(np.arccos(np.clip(cosang, -1.0, 1.0)))
        m = m * (1.0 - _smooth((dh - (22.0 + tol * 0.5)) / 15.0))            # same hue
    return m.astype(np.float32)


def adj_mask(lab, a):
    """float32 0..1 : how strongly each pixel belongs to this colour / shade (no feather)."""
    if a["type"] == "color":
        t = a["target"]
        Ct = math.hypot(t[1], t[2])
        if Ct >= 22.0:
            return color_match(lab, t, a["tol"])
        dL = (lab[..., 0] - t[0]) * 0.8
        da = lab[..., 1] - t[1]
        db = lab[..., 2] - t[2]
        d = np.sqrt(dL * dL + da * da + db * db)
        inner, outer = a["tol"] * 0.7, a["tol"] * 1.3
        return (1.0 - _smooth((d - inner) / (outer - inner))).astype(np.float32)
    L = lab[..., 0]
    m = _neutral_factor(lab)
    if a["lo"] is not None:
        m = m * _smooth((L - (a["lo"] - BAND_EDGE / 2)) / BAND_EDGE)
    if a["hi"] is not None:
        m = m * (1.0 - _smooth((L - (a["hi"] - BAND_EDGE / 2)) / BAND_EDGE))
    return m.astype(np.float32)


def feather_mask(m, amount, long_side):
    """Soft edge like Photoshop's Feather. amount 0..100, relative to the picture size."""
    if amount <= 0:
        return m
    sigma = amount / 100.0 * 0.02 * long_side
    if sigma < 0.4:
        return m
    im = Image.fromarray((np.clip(m, 0, 1) * 255 + 0.5).astype(np.uint8)).filter(ImageFilter.GaussianBlur(sigma))
    return np.asarray(im, dtype=np.float32) / 255.0


def get_mask(rgb, a, lab=None, cache=None):
    """Selection of one colour / shade, with feather. Cached for the live preview."""
    key = ("m",) + adj_key(a)
    if cache is not None and key in cache:
        return cache[key]
    H, W = rgb.shape[:2]
    if lab is not None:
        m = adj_mask(lab, a)
    else:                                   # big picture: no full Lab copy, work in strips
        m = np.empty((H, W), dtype=np.float32)
        for y in range(0, H, 500):
            m[y:y + 500] = adj_mask(rgb_to_lab(rgb[y:y + 500]), a)
    m = feather_mask(m, a.get("feather", 0), max(H, W))
    if cache is not None:
        cache[key] = m
    return m


# ------------------------------------------------------ fill from surroundings
def _pool2(a):
    return a[0::2, 0::2] + a[1::2, 0::2] + a[0::2, 1::2] + a[1::2, 1::2]


def _up(a, h, w):
    """bilinear resize of a float32 (h0,w0) or (h0,w0,C) array."""
    if a.ndim == 3:
        return np.stack([_up(a[..., c], h, w) for c in range(a.shape[2])], -1)
    im = Image.fromarray(np.ascontiguousarray(a, dtype=np.float32))
    return np.asarray(im.resize((w, h), Image.Resampling.BILINEAR), dtype=np.float32)


def _fill_pyramid(S0, W0, want):
    """Multi-scale 'average of the trusted pixels around'. Returns {level: estimate (padded size)}."""
    H, W = W0.shape
    L, hh, ww = 0, H, W
    while min(hh, ww) >= 8 and L < 9:
        hh, ww, L = (hh + 1) // 2, (ww + 1) // 2, L + 1
    mult = 1 << L
    ph, pw = -(-H // mult) * mult, -(-W // mult) * mult
    S = np.pad(S0, ((0, ph - H), (0, pw - W), (0, 0)))
    Wt = np.pad(W0, ((0, ph - H), (0, pw - W)))
    levels = [(S, Wt)]
    for _ in range(L):
        S, Wt = _pool2(S), _pool2(Wt)
        levels.append((S, Wt))
    total = float(levels[-1][1].sum())
    gmean = (levels[-1][0].sum((0, 1)) / total).astype(np.float32) if total > 1e-6 else np.full(3, 255.0, np.float32)
    out, F = {}, None
    low = min(want)
    for l in range(L, low - 1, -1):
        S_l, W_l = levels[l]
        has = (W_l > 1e-6)[..., None]
        avg = S_l / np.maximum(W_l, 1e-6)[..., None]
        if F is None:
            F = np.where(has, avg, gmean).astype(np.float32)
        else:
            up = _up(F, S_l.shape[0], S_l.shape[1])
            frac = np.clip(W_l / (4.0 ** l), 0, 1)[..., None]
            F = np.where(has, frac * avg + (1 - frac) * up, up).astype(np.float32)
        out[l] = F
    return out, L


def fill_background(rgb, valid):
    """What should be where pixels were removed?  rgb uint8 (H,W,3); valid (H,W) 1 = trusted background pixel.
    Returns uint8 (H,W,3): a smooth picture of the surroundings (colour of paper, table, wall ...)."""
    H, W = valid.shape
    v = np.clip(valid, 0, 1).astype(np.float32)
    rgbf = rgb.astype(np.float32)
    base = 1 if max(H, W) > 700 else 0
    out, L = _fill_pyramid(rgbf * v[..., None], v, {base})
    nb_level = max(base, min(3, L))
    nb = _up(out[nb_level][: -(-H // (1 << nb_level)), : -(-W // (1 << nb_level))], H, W)
    # second pass: trust only pixels that look like the background around them (ignores nearby text / lines)
    d = np.abs(rgbf - nb).sum(2)
    w2 = (v * np.exp(-(d / 45.0) ** 2)).astype(np.float32)
    out2, _ = _fill_pyramid(rgbf * w2[..., None], w2, {base})
    F = out2[base]
    F = _up(F[: -(-H // (1 << base)), : -(-W // (1 << base))], H, W) if base else F[:H, :W]
    return np.clip(F + 0.5, 0, 255).astype(np.uint8)


def get_fill(rgb, masks, keys, cache=None):
    key = ("f",) + tuple(keys)
    if cache is not None and key in cache:
        return cache[key]
    H, W = rgb.shape[:2]
    hole = np.maximum.reduce(masks) > 0.03
    hole_img = Image.fromarray((hole * 255).astype(np.uint8)).filter(ImageFilter.MaxFilter(9))
    valid = 1.0 - np.asarray(hole_img, dtype=np.float32) / 255.0
    if H * W > 6_000_000:                   # huge picture: the fill is smooth, so work on a smaller copy
        f = (H * W / 3_000_000.0) ** 0.5
        sw, sh = max(8, int(W / f)), max(8, int(H / f))
        small = np.asarray(Image.fromarray(rgb).resize((sw, sh), Image.Resampling.BOX))
        sv = np.asarray(Image.fromarray((valid * 255).astype(np.uint8)).resize((sw, sh), Image.Resampling.BOX),
                        dtype=np.float32) / 255.0
        sv = (sv > 0.999).astype(np.float32)
        F = fill_background(small, sv)
        F = np.asarray(Image.fromarray(F).resize((W, H), Image.Resampling.BILINEAR))
    else:
        F = fill_background(rgb, valid)
    if cache is not None:
        cache[key] = F
    return F


# ------------------------------------------------------- saturation / clarity
def _saturate(x, sat, vib=0):
    """General saturation, the way a picture editor does it.
    sat 100 = unchanged, 0 = a true gray picture (same brightness), 200 = twice the colour strength.
    Brightness and hue never change: the colour is scaled away from / towards the gray of the same brightness.
    Going up, a colour that would run out of range (clip, change hue) is eased in smoothly instead.
    Gray, black and white (text, paper) are never touched.
    vib = vibrance -100..100: the same, but weak colours move more than already strong ones."""
    if sat == 100 and not vib:
        return x
    gray = (x[..., 0] * 0.299 + x[..., 1] * 0.587 + x[..., 2] * 0.114)[..., None]
    d = x - gray
    s = np.float32(max(0.0, sat / 100.0))
    if vib:
        mx, mn = x.max(2), x.min(2)
        S = np.clip((mx - mn) / np.maximum(mx, 1.0), 0.0, 1.0)
        v = vib / 100.0
        s = s * (1.0 + v * (1.0 - S) * 1.5 if v > 0 else 1.0 + v * S)       # weak colours first / strong ones first
    s = np.asarray(s, np.float32)
    if s.ndim == 0:
        s = np.full(x.shape[:2], float(s), np.float32)
    if float(s.max()) > 1.0:                                                 # room left before a channel clips
        inv = np.float32(1.0) / np.maximum(np.abs(d), np.float32(1e-3))
        lim = np.where(d > 0, np.float32(255.0) - gray, gray) * inv               # how far each channel may still go
        m = np.maximum(lim.min(2), 1.0)
        m2, s2 = m * m, s * s
        soft = s * m / np.sqrt(np.sqrt(s2 * s2 + m2 * m2))                    # smooth minimum of the wish and the room
        s = np.where(s > 1.0, np.maximum(soft, 1.0), s).astype(np.float32)      # never weaker than the original
    return gray + d * s[..., None]


# ---- rank filters in pure numpy.  Pillow's Min / Max / Median filters keep the Python lock while they run, so the whole
# window froze during a text / shadow preview.  numpy releases it, so the window stays alive.
def _shift_stack(a, k):
    r = k // 2
    p = np.pad(a, [(r, r), (r, r)] + [(0, 0)] * (a.ndim - 2), mode="edge")
    return p, r


def min_filter(a, k, use_max=False):
    """k x k minimum (or maximum) of a 2-D / 3-D array (same shape back)."""
    if k <= 1:
        return a
    op = np.maximum if use_max else np.minimum
    p, r = _shift_stack(a, k)
    H, W = a.shape[:2]
    rows = p[0:H]
    for i in range(1, k):                               # vertical pass
        rows = op(rows, p[i:i + H])
    out = rows[:, 0:W]
    for j in range(1, k):                               # horizontal pass
        out = op(out, rows[:, j:j + W])
    return out


def max_filter(a, k):
    return min_filter(a, k, True)


def median3(a):
    """3 x 3 median (2-D array), sorting network."""
    p = np.pad(a, 1, mode="edge")
    H, W = a.shape
    v = [p[i:i + H, j:j + W] for i in range(3) for j in range(3)]

    def s(i, j):
        lo, hi = np.minimum(v[i], v[j]), np.maximum(v[i], v[j])
        v[i], v[j] = lo, hi
    for i, j in ((1, 2), (4, 5), (7, 8), (0, 1), (3, 4), (6, 7), (1, 2), (4, 5), (7, 8), (0, 3), (5, 8), (4, 7),
                 (3, 6), (1, 4), (2, 5), (4, 7), (4, 2), (6, 4), (4, 2)):
        s(i, j)
    return v[4]


def _mask_filter(m, k, use_max=True):
    """Min / max filter of a uint8 / bool mask given as a numpy array."""
    return min_filter(np.asarray(m), k, use_max)


def _local_paper(Y, long):
    """Brightness of the paper around every pixel (block maximum, smoothed): writing is what is darker than this."""
    b = max(4, int(0.012 * long))
    H, W = Y.shape
    ph, pw = (-H) % b, (-W) % b
    pad = np.pad(Y, ((0, ph), (0, pw)), mode="edge")
    mx = pad.reshape(pad.shape[0] // b, b, pad.shape[1] // b, b).max(axis=(1, 3)).astype(np.float32)
    mx = max_filter(mx, 3)
    im = Image.fromarray(mx, "F").resize((pad.shape[1], pad.shape[0]), Image.Resampling.BILINEAR)
    return np.asarray(im, dtype=np.float32)[:H, :W]


def clarity(rgb, look):
    """Text / line / shape sharpness, faint strokes, darker text, cleaner specks.  Only the lightness is changed,
    with a limit against halos, so shapes are never bent or moved."""
    sharp, objs, detail = look["sharp"], look["objs"], look["detail"]
    dark, clear = look["dark"], look["clear"]
    if not clarity_active(look):
        return rgb
    H, W = rgb.shape[:2]
    long = max(H, W)
    x = rgb.astype(np.float32)
    Y = x[..., 0] * 0.299 + x[..., 1] * 0.587 + x[..., 2] * 0.114
    Yf = Y.copy()

    def u8(a):
        return Image.fromarray(np.clip(a + 0.5, 0, 255).astype(np.uint8))

    def unsharp(Yf, r, amount, noise):
        im = u8(Yf)
        u = np.asarray(im)
        blur = np.asarray(im.filter(ImageFilter.GaussianBlur(r)), dtype=np.float32)
        d = Yf - blur
        if noise:
            d = np.sign(d) * np.maximum(np.abs(d) - noise, 0.0)
        Ys = Yf + amount * d
        k = max(3, int(2 * math.ceil(r) + 1))
        mn = min_filter(u, k).astype(np.float32)
        mx = max_filter(u, k).astype(np.float32)
        span = mx - mn
        return np.clip(Ys, mn - 0.5 * span - 3.0, mx + 0.35 * span + 2.0)

    # noise level of the picture (paper grain, camera noise): detail below this is not writing and is never sharpened
    sm1 = np.asarray(u8(Y).filter(ImageFilter.GaussianBlur(1.0)), dtype=np.float32)
    nf = float(np.clip(2.6 * 1.4826 * np.median(np.abs(Y - sm1)), 3.0, 14.0))
    if clear > 0:                                   # remove specks / noise
        med = median3(np.asarray(u8(Yf))).astype(np.float32)
        a = clear / 100.0
        Yf = Yf * (1 - a) + med * a
    if detail > 0:                                  # local contrast with a wide radius = faint strokes
        r2 = max(6.0, 0.02 * long)
        blur = np.asarray(u8(Yf).filter(ImageFilter.GaussianBlur(r2)), dtype=np.float32)
        dd = Yf - blur
        dd = np.sign(dd) * np.maximum(np.abs(dd) - max(4.0, nf), 0.0)                    # paper grain / noise is not "faint writing"
        Yf = np.clip(Yf + (detail / 100.0 * 3.2) * np.minimum(dd, 0.0) + (detail / 100.0 * 0.6) * np.maximum(dd, 0.0), 0.0, 255.0)
    if objs > 0:                                    # shapes / thick lines: medium radius
        Yf = np.clip(unsharp(Yf, max(1.5, 0.004 * long), objs / 100.0 * 2.8, nf), 0.0, 255.0)
    if sharp > 0:                                   # text edges: small radius
        r0 = float(np.clip(0.0008 * long, 0.6, 3.0))
        Yf = np.clip(unsharp(Yf, r0, sharp / 100.0 * 3.2, nf), 0.0, 255.0)
        Yf = np.clip(unsharp(Yf, r0 * 2.2, sharp / 100.0 * 1.6, nf), 0.0, 255.0)   # a second, wider pass: blurred text gets steep edges
    if dark > 0:                                    # text darker, paper untouched - relative to the paper AROUND it
        Yf = np.clip(Yf, 0.0, 255.0)                # the steps above may overshoot; the paper level must not
        bg = _local_paper(Y, long)                  # paper level of the ORIGINAL (the steps above never move the paper)
        diff = np.maximum(bg - Yf, 0.0)
        a = dark / 100.0
        r_t = max(1.6, 0.004 * long)                # thin and dark compared with what is around it = writing / lines
        hp = np.asarray(u8(Yf).filter(ImageFilter.GaussianBlur(r_t)), dtype=np.float32) - Yf
        t = _smooth((diff - 0.07 * bg) / (0.06 * bg + 4.0)) * _smooth((hp - 3.0) / 14.0)    # (paper grain, smooth photos, big dark areas stay)
        gain = 1.0 + 4.0 * a * (1.0 - _smooth((diff - 90.0) / 110.0))           # faint writing gets the most, black stays black
        Yf = Yf * (1.0 - t) + np.clip(bg - diff * gain, 0.0, 255.0) * t
    out = x + (Yf - Y)[..., None]
    return np.clip(out + 0.5, 0, 255).astype(np.uint8)


def _grow(m):
    """Selection grown by ~2 px so anti-aliased edge pixels of a stamp are included."""
    im = Image.fromarray(max_filter((np.clip(m, 0, 1) * 255 + 0.5).astype(np.uint8), 5))
    return np.asarray(im.filter(ImageFilter.GaussianBlur(0.8)), dtype=np.float32) / 255.0


def unmix_ink(x, P, ink, w, r, tol):
    """Takes ink out of the pixels: pixel = paper*(1-a) + ink*a, so we find a and give back that part of the paper.
    Edges fade out smoothly, paper texture stays, nothing gets a new colour.  r = 0..1 how much to remove."""
    u = ink[None, None, :] - P
    v = x - P
    uu = (u * u).sum(2) + 1e-3
    al = (v * u).sum(2) / uu
    perp = np.sqrt(np.maximum(((v - al[..., None] * u) ** 2).sum(2), 0.0))
    t = max(14.0, tol * 1.1)
    gate = 1.0 - _smooth((perp - t) / t)
    gate *= _smooth(np.sqrt(uu) / 45.0)
    al = np.clip((al - 0.04) / 0.96, 0.0, 1.5)
    return x - u * (al * gate * w * r)[..., None]


def adj_active(a):
    return a["k"] != 100 or bool(a.get("to"))


def active_adjs(adjs):
    return [a for a in adjs if adj_active(a)]


# ----------------------------------------------------------------- look (global sliders)
# Every value is "0 = unchanged" except sat (100 = unchanged).  They act ONLY inside the selection (if there is one).
DEFAULT_LOOK = {"sat": 100, "bright": 0, "contrast": 0, "hue": 0, "warm": 0, "vib": 0,
                "sharp": 0, "objs": 0, "detail": 0, "dark": 0, "clear": 0,
                # manual shadow removal (works without the one-click button)
                "flat": 0, "flat_size": 50, "shlift": 0, "white": 0, "black": 0,
                # newsprint / book photo: text and shapes stay, the page becomes white (one click + this slider)
                "news": 0, "news_keep": 50}


def new_look(**kw):
    d = dict(DEFAULT_LOOK)
    d.update(kw)
    return d


def tone_active(l):
    return (l["sat"] != 100 or l["bright"] != 0 or l["contrast"] != 0 or l["hue"] != 0 or l["warm"] != 0
            or l.get("vib", 0) != 0)


def shadow_active(l):
    """Manual shadow removal in use ('flat_size' alone does nothing)."""
    return bool(l.get("flat", 0) or l.get("shlift", 0) or l.get("white", 0) or l.get("black", 0) or l.get("news", 0))


def clarity_active(l):
    return l["sharp"] > 0 or l["objs"] > 0 or l["detail"] > 0 or l["dark"] > 0 or l["clear"] > 0


def look_edited(l):
    return tone_active(l) or clarity_active(l) or shadow_active(l)


def apply_tone(x, l):
    """x float32 (...,3) 0..255 -> warmth, hue, saturation, brightness, contrast."""
    if l["warm"]:
        w = l["warm"] * 0.6
        x = x + np.array([w, 0.0, -w], np.float32)
    if l["hue"]:
        a = -math.radians(l["hue"])
        c, s_ = math.cos(a), math.sin(a)
        Y = x[..., 0] * 0.299 + x[..., 1] * 0.587 + x[..., 2] * 0.114
        I = x[..., 0] * 0.596 - x[..., 1] * 0.274 - x[..., 2] * 0.322
        Q = x[..., 0] * 0.211 - x[..., 1] * 0.523 + x[..., 2] * 0.312
        I2, Q2 = I * c - Q * s_, I * s_ + Q * c
        x = np.stack([Y + 0.956 * I2 + 0.621 * Q2, Y - 0.272 * I2 - 0.647 * Q2, Y - 1.106 * I2 + 1.703 * Q2], -1)
    x = _saturate(x, l["sat"], l.get("vib", 0))
    if l["bright"]:
        t = np.clip(x, 0, 255) / 255.0
        x = (t ** (2.0 ** (-l["bright"] / 50.0))) * 255.0
    if l["contrast"]:
        c = l["contrast"]
        f = (100.0 + c) / 100.0 if c < 0 else 1.0 + c / 50.0
        x = (x - 128.0) * f + 128.0
    return x


def shadow_gain(rgb, size, cache=None):
    """How much brighter every spot of the paper must get to be as bright as the paper's lightest parts (low
    resolution, float32 h,w,3).  'size' 0..100: small shadows ... wide soft shadows.  Cached for the live preview."""
    key = ("sg", int(size))
    if cache is not None and key in cache:
        return cache[key]
    H, W = rgb.shape[:2]
    ws = 1400.0 / max(H, W)
    small = np.asarray(Image.fromarray(rgb).resize((max(8, round(W * ws)), max(8, round(H * ws))), Image.Resampling.BOX)) \
        if ws < 1.0 else rgb
    bg = paper_light(small, None, 0.022 * 2.0 ** ((size - 50) / 40.0))
    gain = np.clip(255.0 / np.maximum(bg, 1.0), 1.0, 6.0).astype(np.float32)
    if cache is not None:
        cache[key] = gain
    return gain


def gain_rows(gain, y0, y1, H, W):
    """The low resolution gain map, stretched over rows y0..y1 of the picture."""
    gh, gw = gain.shape[:2]
    box = (0.0, y0 * gh / H, float(gw), y1 * gh / H)
    return np.stack([np.asarray(Image.fromarray(np.ascontiguousarray(gain[..., c])).resize(
        (W, y1 - y0), Image.Resampling.BILINEAR, box=box), dtype=np.float32) for c in range(3)], -1)


def _apply_gain(x, g, strength=1.0):
    """Brighten the shaded paper (gain map g) but not the writing: pixels much darker than the paper around them (ink,
    dark shapes) are left as they are, so black stays black and a dark box does not turn white."""
    gl = g.mean(2)
    L = x[..., 0] * 0.299 + x[..., 1] * 0.587 + x[..., 2] * 0.114
    r = L * gl / 255.0                                       # 1 = as light as the paper around it, small = ink
    w = _smooth((r - 0.40) / 0.32) * strength
    return np.minimum(x * (1.0 + (g - 1.0) * w[..., None]), 255.0)


def _newsprint(x, g, s, keep):
    """Newsprint / book page: the light is evened out, then everything that is clearly lighter than the writing becomes
    white (grain, yellow paper, writing showing through from the back).  Dark writing and dark / coloured shapes keep
    their colour and edges.  s 0..1 = how strongly (how dark a grey may still be turned white), keep 0..1 = how much
    light writing / thin lines are protected."""
    if g is not None:
        x = _apply_gain(x, np.minimum(g, 2.6))               # a limited lift: a big dark picture is not mistaken for a shadow
    L = x[..., 0] * 0.299 + x[..., 1] * 0.587 + x[..., 2] * 0.114
    t1 = 238.0 - 92.0 * s                                   # at / above this the pixel is white
    t0 = t1 - (38.0 + 34.0 * (1.0 - keep))                   # at / below this nothing changes
    m = _smooth((L - t0) / max(1.0, t1 - t0))
    mx, mn = x.max(2), x.min(2)
    S = (mx - mn) / np.maximum(mx, 1.0)
    prot = 1.0 - 0.92 * _smooth((S - 0.22) / 0.30) * (1.0 - _smooth((L - 215.0) / 30.0))   # strong colours (a red box) stay
    return x + (255.0 - x) * (m * prot)[..., None]


def _otsu8(a):
    """Otsu threshold of a uint8 picture."""
    h = np.bincount(a.reshape(-1), minlength=256).astype(np.float64)
    tot = h.sum()
    if tot == 0:
        return 128
    w0 = np.cumsum(h)
    m0 = np.cumsum(h * np.arange(256))
    mt = m0[-1]
    w1 = tot - w0
    with np.errstate(divide="ignore", invalid="ignore"):
        var = (mt * w0 - m0 * tot) ** 2 / (w0 * w1)
    var[~np.isfinite(var)] = 0
    return int(np.argmax(var))


def auto_news_strength(rgb):
    """The strength (0..100) for the one click: found from the picture itself (where the writing ends and the page begins)."""
    H, W = rgb.shape[:2]
    s = 1000.0 / max(H, W)
    small = np.asarray(Image.fromarray(rgb).resize((max(8, round(W * s)), max(8, round(H * s))), Image.Resampling.BOX)) if s < 1 else rgb
    sg = np.minimum(shadow_gain(small, 50, None), 2.6)
    g = _apply_gain(small.astype(np.float32), sg)
    L = g[..., 0] * 0.299 + g[..., 1] * 0.587 + g[..., 2] * 0.114
    t = _otsu8(np.clip(L, 0, 255).astype(np.uint8))
    return int(np.clip(round((238.0 - (t + 12.0)) / 92.0 * 100.0), 35, 92))


def apply_shadow_manual(x, g, l):
    """Hand-made shadow removal on float32 (...,3) 0..255.
    flat   = even out the light (uses the measured paper light 'g'), flat_size = how wide the shadows are
    shlift = lift shadows: dull / shaded paper gets lighter, real black writing stays
    white  = paper whiteness (the paper's grey becomes white)    black = text darkness"""
    f = l.get("flat", 0)
    if f and g is not None:
        x = _apply_gain(x, g, f / 100.0)
    n = l.get("news", 0)
    if n:
        x = _newsprint(x, g, n / 100.0, l.get("news_keep", 50) / 100.0)
    k = l.get("shlift", 0) / 100.0
    if k > 0:
        v = x.max(2) / 255.0
        w = _smooth((v - 0.16) / 0.34)                      # deep ink (dark) is left alone
        x = x + (255.0 - x) * (k * w)[..., None]
    wh = l.get("white", 0)
    if wh:
        x = np.minimum(x * (255.0 / (255.0 - wh * 0.9)), 255.0)
    bl = l.get("black", 0)
    if bl:
        bp = bl * 0.6
        x = np.clip((x - bp) * (255.0 / (255.0 - bp)), 0.0, 255.0)
    return x


def sel_bbox(sel, pad=0, thr=0.004):
    """(y0, y1, x0, x1) of the selected area (None if empty)."""
    ys = np.nonzero(sel.max(1) > thr)[0]
    xs = np.nonzero(sel.max(0) > thr)[0]
    if ys.size == 0 or xs.size == 0:
        return None
    H, W = sel.shape
    return (max(0, int(ys[0]) - pad), min(H, int(ys[-1]) + 1 + pad),
            max(0, int(xs[0]) - pad), min(W, int(xs[-1]) + 1 + pad))


def render(rgb, adjs, look=None, lab=None, cache=None, chunk_rows=0, sel=None):
    """The whole edit chain on a uint8 picture.  lab / cache let the live preview reuse earlier work.
    sel = optional float mask (same size) 0..1: EVERYTHING (colour edits, brightness, saturation, sharpness ...) acts
    only inside it.  Rows that no edit touches are copied as they are, so a small selection costs almost nothing."""
    look = look or DEFAULT_LOOK
    act = active_adjs(adjs)
    dec = [a for a in act if a["k"] < 100]
    if cache is not None:
        keep = {("m",) + adj_key(a) for a in adjs}
        keep.add(("f",) + tuple(adj_key(a) for a in dec))
        keep |= {("g",) + adj_key(a) for a in dec}
        if look.get("flat", 0) or look.get("news", 0):
            keep.add(("sg", int(look.get("flat_size", 50))))
        for k in [k for k in cache if k not in keep]:
            del cache[k]
    tone_on, clar_on, shad_on = tone_active(look), clarity_active(look), shadow_active(look)
    if not act and not tone_on and not clar_on and not shad_on:
        return rgb
    H, W = rgb.shape[:2]
    gainmap = (shadow_gain(rgb, look.get("flat_size", 50), cache)
               if (shad_on and (look.get("flat", 0) or look.get("news", 0))) else None)
    masks = {a["id"]: get_mask(rgb, a, lab, cache) for a in act}
    # the fill must avoid ALL matching pixels, also the ones outside the selection
    F = get_fill(rgb, [masks[a["id"]] for a in dec], [adj_key(a) for a in dec], cache) if dec else None
    grown = {}
    for a in dec:
        if a["type"] == "color":
            gk = ("g",) + adj_key(a)
            g = cache.get(gk) if cache is not None else None
            if g is None:
                g = np.maximum(masks[a["id"]], _grow(masks[a["id"]]))
                if cache is not None:
                    cache[gk] = g
            grown[a["id"]] = g
    rowmax = {k: m.max(1) for k, m in masks.items()}
    sel_rows = None if sel is None else sel.max(1)
    out8 = np.empty_like(rgb)
    rows = chunk_rows or 256
    for y0 in range(0, H, rows):
        y1 = min(H, y0 + rows)
        touched = tone_on or shad_on or any(float(r[y0:y1].max()) > 0.004 for r in rowmax.values())
        if touched and sel_rows is not None and float(sel_rows[y0:y1].max()) <= 0.004:
            touched = False
        if not touched:
            out8[y0:y1] = rgb[y0:y1]
            continue
        x = rgb[y0:y1].astype(np.float32)
        s = None if sel is None else sel[y0:y1]
        for a in act:
            m = masks[a["id"]][y0:y1]
            if s is not None:
                m = m * s
            if a.get("to"):                       # change this colour into another one (shading stays)
                x = x + (np.array(a["to"], np.float32) - np.array(a["rgb"], np.float32)) * m[..., None]
            if a["k"] < 100 and a["type"] == "color":
                gm = grown[a["id"]][y0:y1]
                if s is not None:
                    gm = gm * s
                x = unmix_ink(x, F[y0:y1].astype(np.float32), np.array(a["rgb"], np.float32),
                              gm, (100 - a["k"]) / 100.0, a["tol"])
            elif a["k"] < 100:
                x += (F[y0:y1].astype(np.float32) - x) * (m * ((100 - a["k"]) / 100.0))[..., None]
            elif a["k"] > 100:
                t = min((a["k"] - 100) / 100.0, 1.0)
                mm = (m * t)[..., None]
                if a["type"] == "color":      # clearer, stronger colour
                    g = (x[..., 0] * 0.299 + x[..., 1] * 0.587 + x[..., 2] * 0.114)[..., None]
                    x = g + (x - g) * (1.0 + 1.3 * mm)
                    x *= (1.0 - 0.28 * mm)
                else:                          # black / gray shade: darker
                    x *= (1.0 - mm)
        if tone_on or shad_on:
            xt = x
            if shad_on:
                xt = apply_shadow_manual(xt, None if gainmap is None else gain_rows(gainmap, y0, y1, H, W), look)
            if tone_on:
                xt = apply_tone(xt, look)
            x = xt if s is None else x + (xt - x) * s[..., None]
        out8[y0:y1] = np.clip(x + 0.5, 0, 255).astype(np.uint8)
    if clar_on:
        if sel is None:
            out8 = clarity(out8, look)
        else:
            bb = sel_bbox(sel, pad=16)
            if bb is not None:
                y0, y1, x0, x1 = bb
                crop = out8[y0:y1, x0:x1]
                c = clarity(crop, look).astype(np.float32)
                s = sel[y0:y1, x0:x1][..., None]
                out8[y0:y1, x0:x1] = np.clip(crop.astype(np.float32) + (c - crop) * s + 0.5, 0, 255).astype(np.uint8)
    return out8


def render_full(img, adjs, look=None, sel=None, layers=None):
    """Full resolution result as a PIL image.  sel = selection mask at preview size (or None).
    layers = pictures placed on the page (signature, stamp ...), put on top after the page settings."""
    look = look or DEFAULT_LOOK
    if not active_adjs(adjs) and not look_edited(look):
        out = img
    else:
        arr = np.asarray(img)
        full_sel = None if sel is None else resize_mask(sel, img.size)
        out = Image.fromarray(render(arr, adjs, look, chunk_rows=700, sel=full_sel))
    return composite_layers(out, layers) if layers else out


# -------------------------------------------------------- shade detection
def detect_bands(lab, max_bands=6):
    """Find the different black / gray shades of this picture (deep black ... light ash).
    Returns band adjustments that split the whole 0..100 lightness range without overlap."""
    L = lab[..., 0].reshape(-1)
    C = np.hypot(lab[..., 1], lab[..., 2]).reshape(-1)
    Ln = L[C < NEUTRAL_C + 6]
    if Ln.size < 200:
        return []
    if Ln.size > 400000:
        Ln = Ln[::Ln.size // 400000]
    lo_p, hi_p = np.percentile(Ln, [0.5, 99.5])
    if hi_p - lo_p < 6:
        return [new_band_adj(float(Ln.mean()), None, None)]
    k = max_bands
    centers = np.linspace(lo_p, hi_p, k).astype(np.float64)
    for _ in range(30):
        idx = np.abs(Ln[:, None] - centers[None, :]).argmin(1)
        new = centers.copy()
        for j in range(k):
            sel = idx == j
            if sel.any():
                new[j] = Ln[sel].mean()
        if np.allclose(new, centers, atol=0.05):
            centers = new
            break
        centers = new
    idx = np.abs(Ln[:, None] - centers[None, :]).argmin(1)
    counts = np.bincount(idx, minlength=k)
    keep = counts >= max(3, 0.002 * Ln.size)
    centers, counts = centers[keep], counts[keep]
    order = np.argsort(centers)
    merged = []
    for c, n in zip(centers[order], counts[order]):
        if merged and c - merged[-1][0] < 7.0:
            tot = merged[-1][1] + n
            merged[-1][0] = (merged[-1][0] * merged[-1][1] + c * n) / tot
            merged[-1][1] = tot
        else:
            merged.append([float(c), int(n)])
    cs = [m[0] for m in merged]
    bands = []
    for i, c in enumerate(cs):
        lo = None if i == 0 else (cs[i - 1] + c) / 2.0
        hi = None if i == len(cs) - 1 else (c + cs[i + 1]) / 2.0
        bands.append(new_band_adj(c, lo, hi))
    return bands


def band_index_for(bands, L):
    for i, b in enumerate(bands):
        lo = -1e9 if b["lo"] is None else b["lo"]
        hi = 1e9 if b["hi"] is None else b["hi"]
        if lo <= L < hi:
            return i
    return 0


def classify_pick(lab_img, rgb):
    """What did the user click on?  ('bands', [...], L)  for black / gray,  ('color', [adj], L)  otherwise."""
    pt = rgb_to_lab(np.array([[list(rgb)]], dtype=np.uint8))[0, 0]
    if math.hypot(float(pt[1]), float(pt[2])) < NEUTRAL_C:
        bands = detect_bands(lab_img)
        if not bands:
            L = float(pt[0])
            bands = [new_band_adj(L, L - 10, L + 10)]
        return "bands", bands, float(pt[0])
    return "color", [new_color_adj(rgb, pt)], float(pt[0])


# ------------------------------------------------------------ shadow removal
def _max_filter_bool(m, r):
    """grow a boolean mask by r pixels (fast: PIL does the work)."""
    if r <= 0:
        return m
    im = Image.fromarray((m * 255).astype(np.uint8)).filter(ImageFilter.MaxFilter(2 * int(r) + 1))
    return np.asarray(im) > 127


def _block_max(a, f):
    h, w = a.shape[:2]
    ph, pw = -(-h // f) * f, -(-w // f) * f
    pad = np.pad(a, ((0, ph - h), (0, pw - w), (0, 0)), mode="edge")
    return pad.reshape(ph // f, f, pw // f, f, a.shape[2]).max(axis=(1, 3))


def _filter_rgb(a, filt):
    """PIL filter on each channel of a float picture (values 0..255)."""
    return np.stack([np.asarray(Image.fromarray(np.clip(a[..., c] + 0.5, 0, 255).astype(np.uint8)).filter(filt),
                                dtype=np.float32) for c in range(a.shape[2])], -1)


def estimate_paper(small, progress=None):
    """Smooth picture of the paper lighting (float32 h,w,3) for a working-size uint8 picture.
    Pass 1 = coarse guess from block maxima.  Pass 2 = throw away everything that is ink OR sits on an edge
    (text, stamps, lines, and a margin around them where soft shadows / blur live) and fill the holes
    from the clean paper around - so ink and its fringe never leak into the lighting estimate."""
    h, w = small.shape[:2]
    a = small.astype(np.float32)
    f = max(4, int(round(max(h, w) / 60)))
    cm = _block_max(a, f)
    cm = _filter_rgb(cm, ImageFilter.MaxFilter(3))
    bg1 = _filter_rgb(_up(cm, h, w), ImageFilter.GaussianBlur(f * 0.6))
    bg1 = np.maximum(bg1, 40.0)
    if progress:
        progress(0.35, "Looking for text and edges")
    lum = a[..., 0] * 0.299 + a[..., 1] * 0.587 + a[..., 2] * 0.114
    lum1 = bg1[..., 0] * 0.299 + bg1[..., 1] * 0.587 + bg1[..., 2] * 0.114
    ratio = lum / lum1
    ink = ratio < 0.90
    sm = np.asarray(Image.fromarray(np.clip(lum + 0.5, 0, 255).astype(np.uint8)).filter(ImageFilter.GaussianBlur(0.8)),
                    dtype=np.float32)
    gy, gx = np.gradient(sm)
    edge = np.hypot(gx, gy) / lum1 > 0.035                       # relative edge strength: works in dark corners too
    bad = _max_filter_bool(ink | edge, max(2, int(round(0.004 * max(h, w)))))
    if progress:
        progress(0.55, "Rebuilding the paper lighting")
    if bad.mean() > 0.92:                                        # almost no clean paper: keep the coarse guess
        return bg1
    valid = (~bad).astype(np.float32)
    bg = fill_background(small, valid).astype(np.float32)
    # where the paper is dark because of a real shadow the fill follows it; never brighter than the lightest paper
    return np.maximum(bg, 40.0)


def _sliding_max(a, k, axis):
    """Maximum over a window of k pixels along one axis (doubling trick: only log2(k) passes)."""
    out = a
    span = 1
    while span * 2 <= k:
        sh = np.roll(out, -span, axis=axis)
        # roll wraps around: put the edge values back instead of the wrapped ones
        idx = [slice(None)] * a.ndim
        idx[axis] = slice(-span, None)
        sh[tuple(idx)] = out[tuple(idx)]
        out = np.maximum(out, sh)
        span *= 2
    if span < k:
        sh = np.roll(out, -(k - span), axis=axis)
        idx = [slice(None)] * a.ndim
        idx[axis] = slice(-(k - span), None)
        sh[tuple(idx)] = out[tuple(idx)]
        out = np.maximum(out, sh)
    return out


def _centered_max(a, k):
    """max filter, window k (odd), both axes, centred."""
    h = k // 2
    p = np.pad(a, ((h, h), (h, h)) + ((0, 0),) * (a.ndim - 2), mode="edge")
    p = _sliding_max(_sliding_max(p, k, 0), k, 1)
    return p[:a.shape[0], :a.shape[1]] if h == 0 else p[h - h:h - h + a.shape[0], h - h:h - h + a.shape[1]]


def paper_light(small, progress=None, kfrac=0.022):
    """Brightness of the blank paper everywhere (float32 h,w,3) - soft AND hard-edged shadows (window blinds, a hand).
    Method: 'closing' of the picture: the lightest value in a window wider than any pen stroke removes the writing,
    shrinking it back again keeps shadow edges exactly where they are."""
    h, w = small.shape[:2]
    a = small.astype(np.float32)
    k = int(max(9, round(max(h, w) * kfrac))) | 1
    sm = np.stack([np.asarray(Image.fromarray(small[..., c]).filter(ImageFilter.GaussianBlur(1.0)), dtype=np.float32)
                   for c in range(3)], -1)
    mx = np.stack([_centered_max(sm[..., c], k) for c in range(3)], -1)
    if progress:
        progress(0.4, "Measuring the light")
    neg = 255.0 - mx
    bg = 255.0 - np.stack([_centered_max(neg[..., c], k) for c in range(3)], -1)   # erosion = min
    # the lightest value over-estimates a little (noise): take the typical paper level of the neighbourhood
    bg = np.stack([np.asarray(Image.fromarray(np.clip(bg[..., c] + 0.5, 0, 255).astype(np.uint8)).filter(
        ImageFilter.GaussianBlur(max(1.0, k / 6.0))), dtype=np.float32) for c in range(3)], -1)
    return np.maximum(bg * 0.985, 30.0)


def photo_mask(img):
    """Where are photos (a face on an ID card, a picture in a form)?  float32 mask (picture size, 0..1).
    A photo is a big, dense, roughly rectangular area that is not paper-white; writing and stamps are thin."""
    W, H = img.size
    sc = min(1.0, 400.0 / max(W, H))
    small = np.asarray(img.resize((max(16, round(W * sc)), max(16, round(H * sc))), Image.Resampling.BOX)) \
        if sc < 1.0 else np.asarray(img)
    h, w = small.shape[:2]
    lum = small.astype(np.float32).mean(2)
    # paper level: 90th percentile in big blocks, then the lightest neighbour block (a photo fills whole blocks)
    gb = 6
    paper = np.zeros((gb, gb), np.float32)
    for i in range(gb):
        for j in range(gb):
            blk = lum[i * h // gb:(i + 1) * h // gb, j * w // gb:(j + 1) * w // gb]
            paper[i, j] = np.percentile(blk, 90) if blk.size else 255.0
    paper = np.asarray(Image.fromarray(paper).filter(ImageFilter.MaxFilter(3)), dtype=np.float32)
    paper = _up(paper, h, w)
    nonpaper = (lum / np.maximum(paper, 30.0) < 0.88).astype(np.float32)
    cell = 12
    gh, gw = -(-h // cell), -(-w // cell)
    pad = np.pad(nonpaper, ((0, gh * cell - h), (0, gw * cell - w)))
    dens = pad.reshape(gh, cell, gw, cell).mean((1, 3))
    dense = Image.fromarray(((dens > 0.55) * 255).astype(np.uint8))
    dense = dense.filter(ImageFilter.MinFilter(3)).filter(ImageFilter.MaxFilter(3))
    g = np.asarray(dense) > 127
    # skin: a face / neck / hands. Medium dark and warm; the pale gold of a watermark does not count
    f = small.astype(np.float32)
    R, G, B = f[..., 0], f[..., 1], f[..., 2]
    Cb = 128 - 0.168736 * R - 0.331264 * G + 0.5 * B
    Cr = 128 + 0.5 * R - 0.418688 * G - 0.081312 * B
    ratio = lum / np.maximum(paper, 30.0)
    skin = (Cb > 77) & (Cb < 127) & (Cr > 133) & (Cr < 173) & (ratio > 0.22) & (ratio < 0.80) & (R > G) & (G > B * 0.85)
    seen = np.zeros_like(g)
    out = np.zeros((h, w), np.float32)
    for y in range(gh):
        for x in range(gw):
            if g[y, x] and not seen[y, x]:
                stack, cells = [(y, x)], []
                seen[y, x] = True
                while stack:
                    cy, cx = stack.pop()
                    cells.append((cy, cx))
                    for ny, nx in ((cy + 1, cx), (cy - 1, cx), (cy, cx + 1), (cy, cx - 1)):
                        if 0 <= ny < gh and 0 <= nx < gw and g[ny, nx] and not seen[ny, nx]:
                            seen[ny, nx] = True
                            stack.append((ny, nx))
                ys = [c_[0] for c_ in cells]
                xs = [c_[1] for c_ in cells]
                box = (max(ys) - min(ys) + 1) * (max(xs) - min(xs) + 1)
                if not (len(cells) >= max(6, 0.012 * gh * gw) and len(cells) / box >= 0.55):
                    continue
                cy0, cy1, cx0, cx1 = min(ys), max(ys) + 1, min(xs), max(xs) + 1
                # where inside this block is a person?  cells that are mostly skin, grouped; the photo is the
                # rectangle around such a group (a watermark / text next to the photo is not part of it)
                spad = np.pad(skin, ((0, gh * cell - h), (0, gw * cell - w)))
                sd = spad.reshape(gh, cell, gw, cell).mean((1, 3))
                sk = sd > 0.25
                sk[:cy0], sk[cy1:], sk[:, :cx0], sk[:, cx1:] = False, False, False, False
                sk = np.asarray(Image.fromarray((sk * 255).astype(np.uint8)).filter(ImageFilter.MaxFilter(3))) > 127
                seen2 = np.zeros_like(sk)
                for sy in range(gh):
                    for sx in range(gw):
                        if not sk[sy, sx] or seen2[sy, sx]:
                            continue
                        st2, cl = [(sy, sx)], []
                        seen2[sy, sx] = True
                        while st2:
                            qy, qx = st2.pop()
                            cl.append((qy, qx))
                            for ny in (qy - 1, qy, qy + 1):
                                for nx in (qx - 1, qx, qx + 1):
                                    if 0 <= ny < gh and 0 <= nx < gw and sk[ny, nx] and not seen2[ny, nx]:
                                        seen2[ny, nx] = True
                                        st2.append((ny, nx))
                        if len(cl) < 6:
                            continue
                        qys = [q[0] for q in cl]
                        qxs = [q[1] for q in cl]
                        y0 = max(cy0, min(qys) - 1) * cell
                        y1 = min(h, min(cy1, max(qys) + 2) * cell)
                        x0 = max(cx0, min(qxs) - 1) * cell
                        x1 = min(w, min(cx1, max(qxs) + 2) * cell)
                        npx = nonpaper[y0:y1, x0:x1] > 0.5
                        while y1 - y0 > 8 and npx[0].mean() < 0.5:                 # trim paper-coloured rim
                            y0 += 1; npx = nonpaper[y0:y1, x0:x1] > 0.5
                        while y1 - y0 > 8 and npx[-1].mean() < 0.5:
                            y1 -= 1; npx = nonpaper[y0:y1, x0:x1] > 0.5
                        while x1 - x0 > 8 and npx[:, 0].mean() < 0.5:
                            x0 += 1; npx = nonpaper[y0:y1, x0:x1] > 0.5
                        while x1 - x0 > 8 and npx[:, -1].mean() < 0.5:
                            x1 -= 1; npx = nonpaper[y0:y1, x0:x1] > 0.5
                        out[y0:y1, x0:x1] = 1.0
    if not out.any():
        return np.zeros((H, W), np.float32)
    big = Image.fromarray((out * 255).astype(np.uint8)).resize((W, H), Image.Resampling.BILINEAR)
    big = big.filter(ImageFilter.GaussianBlur(max(0.8, 0.0025 * max(W, H))))
    return np.asarray(big, dtype=np.float32) / 255.0


SHADOW_DEFAULTS = {"strength": 100, "white": 50, "ink": 50, "scale": 50, "mode": "color", "protect": 0,
                   "even": 100, "depth": 60, "tint": 0, "clean": 0}


def remove_shadow(img, strength=1.0, progress=None, opts=None):
    """'Scan' look: shadows and grey paper become white, writing / lines / shapes stay and get clearer.
    strength 0..1: how far the paper is pushed to white and the ink darkened.
    opts: white 0..100 (paper whiteness), ink 0..100 (text darkness), scale 0..100 (small shadows ... big shadows),
          mode 'color' | 'gray' | 'bw',
          even 0..100 (how much of the uneven light is evened out), depth 0..100 (how dark a shadow may be and still
          be lifted), tint 0..100 (keep the paper's own colour), clean 0..100 (snap the almost-white paper to clean white)."""
    o = dict(SHADOW_DEFAULTS)
    o.update(opts or {})
    arr = np.asarray(img)
    H, W = arr.shape[:2]
    ws = 1400.0 / max(H, W)
    small = np.asarray(img.resize((max(8, round(W * ws)), max(8, round(H * ws))), Image.Resampling.BOX)) \
        if ws < 1.0 else arr
    if progress:
        progress(0.1, "Measuring the light")
    bg = paper_light(small, progress, 0.022 * 2.0 ** ((o["scale"] - 50) / 40.0))
    s = float(max(0.0, min(1.0, strength)))
    maxg = float(np.clip(6.0 * 2.0 ** ((o["depth"] - 60) / 30.0), 1.5, 14.0))
    gain = np.clip(255.0 / np.maximum(bg, 1.0), 1.0, maxg).astype(np.float32)
    if o["even"] < 100:                                        # only part of the uneven light is removed
        gain = 1.0 + (gain - 1.0) * (o["even"] / 100.0)
    if o["tint"] > 0:                                          # keep (some of) the paper's colour: same gain for r, g, b
        mg = gain.mean(2, keepdims=True)
        gain = gain + (mg - gain) * (o["tint"] / 100.0)
    gh, gw = gain.shape[:2]
    lum_small = small.astype(np.float32).mean(2) * 255.0 / np.maximum(bg.mean(2), 1.0)
    bp = float(np.clip(np.percentile(lum_small, 0.5), 0, 90))
    wp = 250.0 - 38.0 * s - (o["white"] - 50) * 0.45
    wp = float(max(bp + 20.0, min(254.0, wp)))
    gam = (1.0 + 0.35 * s) * (1.0 + (o["ink"] - 50) / 100.0 * 0.9)
    out = np.empty_like(arr)
    pm = photo_mask(img) if o.get("protect", 0) else None      # photos (faces ...): only the light is evened out
    if pm is not None and float(pm.max()) < 0.05:
        pm = None
    step = 600
    for y0 in range(0, H, step):
        y1 = min(H, y0 + step)
        box = (0.0, y0 * gh / H, float(gw), y1 * gh / H)
        g = np.stack([np.asarray(Image.fromarray(np.ascontiguousarray(gain[..., c])).resize(
            (W, y1 - y0), Image.Resampling.BILINEAR, box=box), dtype=np.float32) for c in range(3)], -1)
        x = np.minimum(arr[y0:y1].astype(np.float32) * g, 255.0)
        x = np.clip((x - bp) / max(1.0, wp - bp), 0.0, 1.0) ** gam
        if o["mode"] in ("gray", "bw"):
            gray = (x[..., 0] * 0.299 + x[..., 1] * 0.587 + x[..., 2] * 0.114)
            if o["mode"] == "bw":
                gray = np.clip((gray - 0.45) / 0.12 + 0.5, 0.0, 1.0)      # hard but anti-aliased threshold
            x = np.repeat(gray[..., None], 3, 2)
        if o["clean"] > 0:                                     # almost-white paper (grain, faint stains) becomes clean white
            lo = 1.0 - 0.02 - 0.16 * (o["clean"] / 100.0)
            x = x + (1.0 - x) * _smooth((x - lo) / max(1e-3, 1.0 - lo))
        res = np.clip(x * 255.0 + 0.5, 0, 255)
        if pm is not None and float(pm[y0:y1].max()) > 0.01:
            gp = np.minimum(1.0 + 0.6 * (g - 1.0), 1.3)           # soft correction: no contrast / ink changes
            xp = np.minimum(arr[y0:y1].astype(np.float32) * gp, 255.0)
            if o["mode"] in ("gray", "bw"):
                xp = np.repeat((xp[..., 0] * 0.299 + xp[..., 1] * 0.587 + xp[..., 2] * 0.114)[..., None], 3, 2)
            a_ = pm[y0:y1, :, None]
            res = res * (1.0 - a_) + xp * a_
        out[y0:y1] = np.clip(res + 0.5, 0, 255).astype(np.uint8)
        if progress:
            progress(0.6 + 0.4 * y1 / H, "Applying")
    res = Image.fromarray(out)
    return res


# --------------------------------------------------------------------- crop
def order_quad(pts):
    """Any 4 points -> top-left, top-right, bottom-right, bottom-left."""
    p = [(float(x), float(y)) for x, y in pts]
    s = [x + y for x, y in p]
    d = [x - y for x, y in p]
    tl, br = p[int(np.argmin(s))], p[int(np.argmax(s))]
    tr, bl = p[int(np.argmax(d))], p[int(np.argmin(d))]
    return [tl, tr, br, bl]


def _coeffs(out_pts, in_pts):
    A, B = [], []
    for (x, y), (X, Y) in zip(out_pts, in_pts):
        A.append([x, y, 1, 0, 0, 0, -X * x, -X * y])
        B.append(X)
        A.append([0, 0, 0, x, y, 1, -Y * x, -Y * y])
        B.append(Y)
    return np.linalg.solve(np.array(A, dtype=np.float64), np.array(B, dtype=np.float64)).tolist()


def warp_quad(img, pts):
    """Straighten the area inside 4 points into a perfect rectangle (like Photoshop's perspective crop)."""
    tl, tr, br, bl = order_quad(pts)
    dist = lambda a, b: math.hypot(a[0] - b[0], a[1] - b[1])
    w = max(1, int(round(max(dist(tl, tr), dist(bl, br)))))
    h = max(1, int(round(max(dist(tl, bl), dist(tr, br)))))
    if w < 4 or h < 4:
        raise ValueError("The 4 points are too close together")
    try:
        co = _coeffs([(0, 0), (w, 0), (w, h), (0, h)], [tl, tr, br, bl])
    except np.linalg.LinAlgError:
        raise ValueError("The 4 points do not make a proper shape")
    return img.transform((w, h), Image.Transform.PERSPECTIVE, co, Image.Resampling.BICUBIC)


def outline_mask(size, shape, radius=0.22):
    """Soft-edged mask (PIL 'L', 255 inside) of a shape that fills the whole picture:
    'rect', 'oval' (an ellipse; a circle when the picture is square), 'round' (rounded corners)."""
    W, H = int(size[0]), int(size[1])
    ss = max(1, min(4, int(math.sqrt(36_000_000.0 / max(1, W * H)))))
    big = Image.new("L", (W * ss, H * ss), 0)
    d = ImageDraw.Draw(big)
    box = (0, 0, W * ss - 1, H * ss - 1)
    if shape == "oval":
        d.ellipse(box, fill=255)
    elif shape == "round":
        d.rounded_rectangle(box, int(min(W, H) * ss * radius), fill=255)
    else:
        d.rectangle(box, fill=255)
    return big.resize((W, H), Image.Resampling.LANCZOS) if ss > 1 else big


def crop_to_shape(img, alpha, box, shape, mask=None, fill=None):
    """Cut 'box' out of the picture in a shape.  Returns (rgb, alpha).
    alpha (PIL 'L' or None) = the picture's transparency: outside the shape it becomes transparent.
    With alpha None the outside is filled with 'fill' (the paper colour) instead.
    mask = an own mask of the whole picture (selection shape) instead of a ready-made shape."""
    rgb = crop_box(img, box)
    if mask is not None:
        m = crop_box(mask.convert("L"), box).convert("L")
    else:
        m = outline_mask(rgb.size, shape)
    if alpha is not None:
        a = crop_box(alpha.convert("L"), box).convert("L")
        return rgb, ImageChops.multiply(a, m)
    bg = Image.new("RGB", rgb.size, fill or border_color(img))
    return Image.composite(rgb, bg, m), None


def crop_box(img, box):
    l, t, r, b = box
    W, H = img.size
    l, t = max(0, int(round(l))), max(0, int(round(t)))
    r, b = min(W, int(round(r))), min(H, int(round(b)))
    if r - l < 2 or b - t < 2:
        raise ValueError("The crop area is too small")
    return img.crop((l, t, r, b))


# ------------------------------------------------------------- eraser / clone
def make_mask(size, shape, data, brush=20.0):
    """Mask (PIL 'L', 255 = erase) from what the user drew, in real picture pixels.
    shape: 'brush' (list of points, brush = width), 'rect' / 'ellipse' ((x0,y0,x1,y1)), 'lasso' (list of points)."""
    m = Image.new("L", size, 0)
    d = ImageDraw.Draw(m)
    if shape == "brush":
        w = max(1.0, float(brush))
        r = w / 2.0
        pts = [(float(x), float(y)) for x, y in data]
        if len(pts) > 1:
            d.line(pts, fill=255, width=int(round(w)), joint="curve")
        for x, y in pts[:: max(1, len(pts) // 400)] + pts[-1:]:
            d.ellipse((x - r, y - r, x + r, y + r), fill=255)
    elif shape == "rect":
        x0, y0, x1, y1 = data
        d.rectangle((min(x0, x1), min(y0, y1), max(x0, x1), max(y0, y1)), fill=255)
    elif shape == "ellipse":
        x0, y0, x1, y1 = data
        d.ellipse((min(x0, x1), min(y0, y1), max(x0, x1), max(y0, y1)), fill=255)
    elif shape == "lasso":
        pts = [(float(x), float(y)) for x, y in data]
        if len(pts) >= 3:
            d.polygon(pts, fill=255)
    return m


def _gray_from_L(L):
    fy = (L + 16.0) / 116.0
    y = np.where(fy ** 3 > 0.008856, fy ** 3, (fy - 16.0 / 116.0) / 7.787)
    v = np.where(y <= 0.0031308, 12.92 * y, 1.055 * np.power(np.maximum(y, 1e-6), 1 / 2.4) - 0.055)
    return np.clip(v, 0, 1) * 255.0


def _writing_color(reg, F, hm, exclude=None):
    """The real colour of the dark writing AROUND the removed place (outside it and its halo), as float RGB, or None.
    Used to give the writing under a stamp exactly the colour the same writing has everywhere else - so it never turns
    grey / bluish / lighter just because it was covered by a stamp.  exclude = pixels that must not count (stamp coloured)."""
    ys, xs = np.nonzero(hm)
    if len(ys) == 0:
        return None
    r = int(max(3, min(12, 0.03 * max(ys.max() - ys.min(), xs.max() - xs.min()))))
    z = Image.fromarray((hm * 255).astype(np.uint8)).filter(ImageFilter.MaxFilter(2 * r + 1)).filter(ImageFilter.MaxFilter(2 * r + 1))
    outside = np.asarray(z) < 128
    L = rgb_to_lab(reg)[..., 0]
    Lf = rgb_to_lab(np.clip(F, 0, 255).astype(np.uint8))[..., 0]
    dark = outside & ((Lf - L) > 30.0)
    if exclude is not None:
        dark &= ~exclude
    n = int(dark.sum())
    if n < 40:
        return None
    pix = reg[dark].astype(np.float32)
    k = max(20, int(0.35 * n))                              # the darkest part = the core of the strokes, not the soft edges
    core_px = pix[np.argsort(L[dark])[:k]]
    return np.median(core_px, axis=0).astype(np.float32)


def _keep_dark_ink(reg, hole_img, res, wc=None):
    """A coloured stamp lies on top of black writing: where the removed pixels are clearly DARKER than the stamp ink,
    the writing is still there - keep it instead of painting paper over it.  It gets the colour of the writing around
    (wc); when there is no writing around, a neutral dark."""
    hm = np.asarray(hole_img) > 127
    if not hm.any():
        return res
    lab = rgb_to_lab(reg)
    L, C = lab[..., 0], np.hypot(lab[..., 1], lab[..., 2])
    ink = hm & (C > 18.0)
    if int(ink.sum()) < 20 or int(ink.sum()) < 0.04 * int(hm.sum()):
        return res                                   # what is removed is not coloured ink: nothing to keep
    Lref = float(np.median(L[ink]))
    s = np.clip((Lref - L - 6.0) / (0.4 * Lref + 1.0), 0.0, 1.0) ** 0.7
    s = (s * hm)[..., None].astype(np.float32)
    if wc is not None:
        dark = np.empty_like(res)
        dark[...] = np.asarray(wc, np.float32)[None, None, :]
    else:
        dark = np.repeat(_gray_from_L(L)[..., None], 3, 2)
    return res * (1 - s) + dark * s


def _top_dir(A):
    """Main direction (unit vector, pointing to positive values) of the rows of A, and how much of the energy it holds (0..1)."""
    M = A.T @ A / max(1, len(A))
    w, ev = np.linalg.eigh(M)
    k = ev[:, -1].astype(np.float32)
    if k.sum() < 0:
        k = -k
    return k, float(w[-1]) / max(float(w.sum()), 1e-9)


def _unmix_stamp(reg, F, hard):
    """Smart stamp removal.  One coloured stamp lies over writing of ANY other colour (black, grey, blue ink, red ...).
    Printing is multiplicative, so in log space every pixel is a sum:
        -log(pixel / paper)  =  t * kt  +  d * ks
    kt = the writing's colour, ks = the stamp's colour (unit vectors), t / d = how much writing / stamp is in that pixel.
    ks is learned from the removed (selected) pixels, kt from the writing OUTSIDE the stamp (grey if there is none).
    Per pixel t and d are solved (both >= 0), then the writing is put back (paper * exp(-t kt)) and the stamp is gone: the
    letters under the stamp are RECONSTRUCTED.  The faint halo of the stamp around the selection is cleaned the same way.
    It cannot work when the writing and the stamp have the same colour (then None -> the older method is used).
    Returns (T, W, wc, cover) or None: T = writing transmittance (h, w, 3), W = where to apply it (h, w, 0..1),
    wc = the writing's real colour (or None), cover = how much writing is in each pixel (0..1, or None).
    reg = uint8 (h, w, 3) region, F = paper estimate (h, w, 3), hard = PIL 'L' mask of the removed thing (same region)."""
    hm = np.asarray(hard) > 127
    nh = int(hm.sum())
    if nh < 30:
        return None
    x = np.log(np.maximum(reg.astype(np.float32), 1.0) / np.maximum(F.astype(np.float32), 1.0))
    A = np.maximum(-x, 0.0)                              # how much each channel is darkened by ink (>= 0)
    mag = np.sqrt((A ** 2).sum(2))
    # chroma of the removed pixels must exist (else it is not a coloured stamp: the old method handles it)
    chroma = np.sqrt(((A - A.mean(2, keepdims=True)) ** 2).sum(2))
    est = hm & (mag > 0.08)
    n = int(est.sum())
    if n < 30 or n < 0.04 * nh or int((hm & (chroma > 0.06)).sum()) < 20:
        return None
    # the writing's colour kt: dark / coloured ink pixels outside the stamp and its halo (grey if there is none).
    # The stamp's colour ks: what is left of the removed pixels when the writing direction is taken out - the writing only
    # moves a pixel along kt, so the pixels are spread between kt and ks, and the stamp-only pixels sit on the ks end.
    bb = hard.getbbox()
    r = int(max(3, min(12, 0.03 * max(bb[2] - bb[0], bb[3] - bb[1]))))
    hard_b = hard.point(lambda q: 255 if q > 127 else 0)
    zone_img = hard_b.filter(ImageFilter.MaxFilter(2 * r + 1))
    zone = np.asarray(zone_img) > 127
    zone2 = np.asarray(zone_img.filter(ImageFilter.MaxFilter(2 * r + 1))) > 127
    outside = (~zone2) & (mag > 0.12)
    Ae = A[est]
    kt = np.ones(3, np.float32) / np.sqrt(3.0)
    ks = None
    for _ in range(2):
        a_ = Ae @ kt
        P = Ae - a_[:, None] * kt[None, :]
        u, ratio_u = _top_dir(P)
        if ratio_u < 0.7:
            return None                                   # the removed thing has no single colour
        if float((P @ u).mean()) < 0:
            u = -u
        th = np.arctan2(np.sqrt((P ** 2).sum(1)), a_)
        big = np.sqrt((Ae ** 2).sum(1)) > 0.25
        ths = float(np.percentile(th[big] if int(big.sum()) >= 20 else th, 70))
        ks = (np.cos(ths) * kt + np.sin(ths) * u).astype(np.float32)
        ks /= max(float(np.linalg.norm(ks)), 1e-6)
        cosk = (A @ ks) / np.maximum(mag, 1e-6)
        txt = outside & (cosk < 0.95)
        if int(txt.sum()) < 40:
            if int((outside & (cosk >= 0.95)).sum()) >= 150:
                return None                               # the writing around has the stamp's colour: cannot be told apart
            break
        k2, ratio_t = _top_dir(A[txt])
        if ratio_t < 0.8:
            break
        kt = k2
    c = float(kt @ ks)
    if c > 0.975:
        return None                                       # writing and stamp look the same: cannot be told apart
    det = 1.0 - c * c
    b1, b2 = A @ kt, A @ ks
    t = (b1 - c * b2) / det
    d = (b2 - c * b1) / det
    t = np.where(t < 0, 0.0, np.where(d < 0, np.maximum(b1, 0.0), t))      # both must be >= 0
    d = np.where(t <= 0, np.maximum(b2, 0.0), np.where(d < 0, 0.0, d))
    d95 = float(np.percentile(d[est], 95))
    if d95 < 0.05:
        return None
    t = np.maximum(t - 0.06, 0.0)                        # a little darkening is noise, not writing
    T = np.exp(-t[..., None] * kt[None, None, :]).astype(np.float32)
    presence = np.clip(d / (0.2 * d95), 0.0, 1.0)
    W = np.where(zone, np.maximum(hm.astype(np.float32), presence), 0.0).astype(np.float32)
    # the writing's real colour and full strength, from the writing OUTSIDE the stamp: the letters under the stamp are
    # then drawn in exactly that colour (amount = t / full strength) instead of a colour rebuilt from the mixed pixels
    cosk = (A @ ks) / np.maximum(mag, 1e-6)
    txt = outside & (cosk < 0.95)
    wc, cover = None, None
    if int(txt.sum()) >= 40:
        wc = _writing_color(reg, F, hm, exclude=(cosk >= 0.95))
        t_full = float(np.percentile(A[txt] @ kt, 90)) - 0.06
        if wc is not None and t_full > 0.12:
            # strokes are solid: a pixel that is mostly writing is full writing (the stamp makes the estimate a bit weak)
            cover = np.clip((t / t_full - 0.08) / 0.72, 0.0, 1.0).astype(np.float32)
    return T, W, wc, cover


def _ink_like(reg, F):
    """Pixels that clearly differ from the paper estimate F (dark / coloured: writing, lines, stamps)."""
    d = np.abs(reg.astype(np.float32) - F.astype(np.float32)).sum(2)
    return d > 22.0


def _grow_over_ink(reg, hole):
    """The brush covers most of a letter, but its soft anti-aliased edge sticks out. Grow the hole 1-3 px, but only
    over pixels that are ink-like, so it follows the strokes and does not eat paper."""
    h0 = hole > 0.5
    F = fill_background(reg, (~h0).astype(np.float32))
    ink = _ink_like(reg, F)
    cur = h0
    for _ in range(3):
        grow = np.asarray(Image.fromarray((cur * 255).astype(np.uint8)).filter(ImageFilter.MaxFilter(3))) > 127
        cur = cur | (grow & ink)
    out = cur.astype(np.float32)
    return out, Image.fromarray((cur * 255).astype(np.uint8))


def _paper_like(reg, valid):
    """1 where a valid pixel looks like plain paper (not ink, not the dim halo around ink)."""
    F = fill_background(reg, valid)
    ink = _ink_like(reg, F)
    ink = np.asarray(Image.fromarray((ink * 255).astype(np.uint8)).filter(ImageFilter.MaxFilter(5))) > 127
    return (~ink).astype(np.float32)


def erase_region(img, mask, offset=None, feather=1.5, keep_ink=False):
    """Removes what is under the mask and puts there what surrounds it, so it looks as if nothing was there.
    offset=(dx,dy): copy from that spot instead (clone / heal): the texture comes from (x+dx, y+dy),
    the brightness and colour are matched to the surroundings."""
    hard = mask.point(lambda v: 255 if v > 16 else 0)
    bbox = hard.getbbox()
    if bbox is None:
        return img
    W, H = img.size
    bw, bh = bbox[2] - bbox[0], bbox[3] - bbox[1]
    m = max(24, int(0.6 * max(bw, bh)))
    box = (max(0, bbox[0] - m), max(0, bbox[1] - m), min(W, bbox[2] + m), min(H, bbox[3] + m))
    full = np.asarray(img)
    reg = full[box[1]:box[3], box[0]:box[2]].copy()
    hole_img = hard.crop(box).filter(ImageFilter.MaxFilter(5))
    hole = np.asarray(hole_img, dtype=np.float32) / 255.0
    if offset is None:
        hole, hole_img = _grow_over_ink(reg, hole)           # the soft edge of the writing goes too: no grey ghost
    valid = 1.0 - hole
    if offset is None:                                       # only real paper counts for the fill, never ink / halo
        valid = valid * _paper_like(reg, valid)
    F = fill_background(reg, valid).astype(np.float32)

    # soft edge: grow a little, then blur, so nothing of the old picture is left at the border
    f = max(0.8, float(feather))
    k = 2 * int(math.ceil(f)) + 1
    soft = np.asarray(mask.crop(box).filter(ImageFilter.MaxFilter(max(3, k))).filter(ImageFilter.GaussianBlur(f)),
                      dtype=np.float32) / 255.0
    soft = np.clip(soft, 0, 1)[..., None]

    ring = (np.asarray(hole_img.filter(ImageFilter.MaxFilter(25)), dtype=np.float32) / 255.0 > 0.5) & (hole < 0.5)
    if offset is None:
        # grain: add noise like the surrounding picture has, so the patch does not look too flat
        sm = np.asarray(Image.fromarray(reg).filter(ImageFilter.GaussianBlur(1.2)), dtype=np.float32)
        hf = reg.astype(np.float32) - sm
        res = F
        if int(ring.sum()) > 80:
            h = hf[ring]
            sig = 1.4826 * np.median(np.abs(h - np.median(h, axis=0)), axis=0)     # robust: text edges do not count
            rng = np.random.default_rng(12345)
            nz = rng.standard_normal(F.shape).astype(np.float32)
            p_ = np.pad(nz, ((1, 1), (1, 1), (0, 0)), mode="edge")             # slight blur: grain, not white noise
            nz = (2 * nz + p_[:-2, 1:-1] + p_[2:, 1:-1] + p_[1:-1, :-2] + p_[1:-1, 2:]) / 6.0
            nz /= max(1e-3, float(nz.std()))
            res = F + nz * sig * 0.9
    else:
        dx, dy = int(round(offset[0])), int(round(offset[1]))
        ys = np.clip(np.arange(box[1], box[3]) + dy, 0, H - 1)
        xs = np.clip(np.arange(box[0], box[2]) + dx, 0, W - 1)
        src = full[np.ix_(ys, xs)].astype(np.float32)
        res = src
        if int(ring.sum()) > 50:                   # heal: same texture, but the tone of the new surroundings
            res = src + (np.median(reg[ring].astype(np.float32), axis=0) - np.median(src[ring], axis=0))
    if keep_ink and offset is None:
        hbox = hard.crop(box)
        un = _unmix_stamp(reg, F, hbox)
        if un is not None:                                   # coloured stamp over writing: put the writing back
            T, Wz, wc, cover = un
            if wc is not None and cover is not None:         # ... in the colour the writing really has
                tw = np.clip(wc[None, None, :] / np.maximum(F, 1.0), 0.02, 1.0)    # that colour as 'paper x tw' (light-proof)
                res = res * np.power(tw, cover[..., None])
            else:
                res = res * T
            soft = np.maximum(soft, Wz[..., None])
        else:
            wc = _writing_color(reg, F, np.asarray(hbox) > 127)
            res = _keep_dark_ink(reg, hbox, res, wc)
    out = reg.astype(np.float32) * (1 - soft) + res * soft
    result = img.copy()
    result.paste(Image.fromarray(np.clip(out + 0.5, 0, 255).astype(np.uint8)), (box[0], box[1]))
    return result


# --------------------------------------------------------------------- save
SAVE_EXT = {".jpg", ".jpeg", ".png", ".webp", ".bmp", ".tif", ".tiff", ".pdf"}


def out_ext(src_name):
    e = os.path.splitext(src_name)[1].lower()
    return e if e in SAVE_EXT else ".png"


ORIG_DIR = os.path.join(os.environ.get("TEMP") or os.environ.get("TMPDIR") or "/tmp", "imagefixer_originals")


def backup_original(path, uid):
    """Before 'Save' replaces a file: a copy of the original is kept (temporarily) so Reset and Compare still show it."""
    import shutil
    os.makedirs(ORIG_DIR, exist_ok=True)
    dst = os.path.join(ORIG_DIR, "%s_%s" % (uid, os.path.basename(path)))
    if not os.path.exists(dst):
        shutil.copy2(path, dst)
    return dst


def write_replacing(img, path):
    """Writes the picture to path through a temporary file, so a failure never leaves a broken original."""
    d, n = os.path.split(path)
    tmp = os.path.join(d, ".~if_" + n)
    try:
        save_image(img, tmp)
        os.replace(tmp, path)
    finally:
        if os.path.exists(tmp):
            try:
                os.remove(tmp)
            except OSError:
                pass


def purge_dir(folder, older_than=None):
    """Removes the temporary files (all of them, or only those older than that many seconds)."""
    import time
    if not os.path.isdir(folder):
        return
    now = time.time()
    for n in os.listdir(folder):
        p = os.path.join(folder, n)
        try:
            if os.path.isfile(p) and (older_than is None or now - os.path.getmtime(p) > older_than):
                os.remove(p)
        except OSError:
            pass


def unique_path(folder, base, ext):
    p = os.path.join(folder, base + ext)
    n = 2
    while os.path.exists(p):
        p = os.path.join(folder, "%s (%d)%s" % (base, n, ext))
        n += 1
    return p


def save_image(img, path):
    e = os.path.splitext(path)[1].lower()
    if e in (".jpg", ".jpeg"):
        img.save(path, "JPEG", quality=95, subsampling=0, optimize=True)   # 4:4:4 keeps thin text sharp
    elif e == ".webp":
        img.save(path, "WEBP", quality=95)
    elif e in (".tif", ".tiff"):
        img.save(path, "TIFF", compression="tiff_lzw")
    elif e == ".bmp":
        img.save(path, "BMP")
    elif e == ".pdf":
        save_pdf([img], path)
    else:
        img.save(path, "PNG")


# ------------------------------------------------------------------- selection
# A selection is a float32 mask (0..1) at preview size, or None = nothing selected (edits act on the whole picture).
def resize_mask(m, size):
    """size = (width, height) in pixels."""
    w, h = size
    if m.shape[1] == w and m.shape[0] == h:
        return m
    im = Image.fromarray(np.ascontiguousarray(m, dtype=np.float32))
    return np.asarray(im.resize((w, h), Image.Resampling.BILINEAR), dtype=np.float32)


def sel_combine(old, new, mode):
    """mode: 'new' | 'add' | 'sub'.  Returns the new selection (None when empty)."""
    if mode == "add" and old is not None:
        out = np.maximum(old, new)
    elif mode == "int" and old is not None:
        out = old * new
    elif mode == "sub":
        if old is None:
            return None
        out = old * (1.0 - new)
    else:
        out = new
    return out.astype(np.float32) if float(out.max()) > 0.02 else None


def sel_coverage(m):
    return 0.0 if m is None else float((m > 0.5).mean())


def shape_mask(size, shape, data, brush=20.0):
    """Float mask from a drawn rectangle / ellipse / lasso / brush (coordinates in the same pixels as size=(w,h))."""
    return np.asarray(make_mask(size, shape, data, brush), dtype=np.float32) / 255.0


def color_range_mask(lab, rgb, tol=30.0):
    """Every pixel whose colour matches rgb (like Photoshop's Color Range). Coloured targets match by hue + colour
    strength (so a dark pixel with a faint tint is not taken); white / grey / black match by lightness."""
    pt = rgb_to_lab(np.array([[list(rgb)]], dtype=np.uint8))[0, 0]
    return adj_mask(lab, {"type": "color", "target": [float(pt[0]), float(pt[1]), float(pt[2])], "tol": float(tol)})


def fuzz_to_tol(fuzz):
    """Photoshop-like 'Fuzziness' 0..200 -> the colour distance used by color_range_mask."""
    return max(1.0, float(fuzz) * 0.55)


def color_range_fuzzy(lab, rgb, fuzz=50, center=None, local=0.0):
    """Color range with a fuzziness slider (0..200).  local > 0 (fraction of the picture's long side): only around 'center'
    (x, y) - 'localized color clusters', so the same colour far away is not taken."""
    m = color_range_mask(lab, rgb, fuzz_to_tol(fuzz))
    if center is not None and local > 0:
        H, W = m.shape
        R = max(8.0, local * max(H, W))
        yy, xx = np.ogrid[0:H, 0:W]
        d = np.sqrt((xx - center[0]) ** 2 + (yy - center[1]) ** 2)
        m = m * (1.0 - _smooth((d - R * 0.85) / (R * 0.3 + 1e-3))).astype(np.float32)
    return m


def smart_deselect(sel, lab, stroke, tol=28.0, size=30.0):
    """The deselect brush: paint over the part that was selected by mistake.  Only what looks like what was brushed
    (and touches the brush) is taken out, so the edge between two colours stays exactly where it is - like the subtract
    mode of Photoshop's quick selection.  Returns the mask of what to remove (0..1)."""
    H, W = stroke.shape
    core_m = stroke > 0.5
    if not core_m.any():
        return np.zeros((H, W), np.float32)
    inside = core_m & (sel > 0.3)
    if int(inside.sum()) < 6:
        return np.asarray(stroke, np.float32)
    vals = lab[inside]
    ref1 = np.median(vals, axis=0)
    dv = np.sqrt(((vals[:, 0] - ref1[0]) * 0.8) ** 2 + (vals[:, 1] - ref1[1]) ** 2 + (vals[:, 2] - ref1[2]) ** 2)
    refs = [ref1]
    far = dv > tol * 1.4
    if int(far.sum()) > 0.15 * len(vals):                 # the brush crossed two colours: use both
        refs.append(np.median(vals[far], axis=0))
    spread = float(np.mean(1.4826 * np.median(np.abs(vals - ref1), axis=0)))
    thr = tol + min(18.0, 2.0 * spread)
    ys, xs = np.nonzero(stroke > 0.05)
    reach = int(max(6, 0.9 * size))
    y0, y1 = max(0, int(ys.min()) - reach), min(H, int(ys.max()) + 1 + reach)
    x0, x1 = max(0, int(xs.min()) - reach), min(W, int(xs.max()) + 1 + reach)
    sub = lab[y0:y1, x0:x1]
    d = None
    for r in refs:
        dr = np.sqrt(((sub[..., 0] - r[0]) * 0.8) ** 2 + (sub[..., 1] - r[1]) ** 2 + (sub[..., 2] - r[2]) ** 2)
        d = dr if d is None else np.minimum(d, dr)
    soft = (1.0 - _smooth((d - thr * 0.75) / (thr * 0.5))).astype(np.float32)
    st = core_m[y0:y1, x0:x1]
    near = np.asarray(Image.fromarray((st * 255).astype(np.uint8)).filter(ImageFilter.MaxFilter(2 * reach + 1))) > 0
    allow = ((soft > 0.5) & near & (sel[y0:y1, x0:x1] > 0.05))
    seed = st & allow
    if not seed.any():
        seed = st
    cur = Image.fromarray((seed * 255).astype(np.uint8))
    allow_im = Image.fromarray((allow * 255).astype(np.uint8))
    prev_n = int(seed.sum())
    for _ in range(max(4, reach)):                        # grows only through matching pixels that touch the brush
        nxt = ImageChops.multiply(cur.filter(ImageFilter.MaxFilter(3)), allow_im)
        n = int(np.count_nonzero(np.asarray(nxt)))
        cur = nxt
        if n == prev_n:
            break
        prev_n = n
    grown = np.asarray(cur, np.float32) / 255.0
    m = np.zeros((H, W), np.float32)
    keep_core = (core_m[y0:y1, x0:x1] & (d < thr * 1.8)).astype(np.float32)
    m[y0:y1, x0:x1] = np.maximum(grown * soft, keep_core)
    return np.asarray(Image.fromarray((np.clip(m, 0, 1) * 255).astype(np.uint8)).filter(ImageFilter.GaussianBlur(0.7)), np.float32) / 255.0


def quick_select(lab, stroke, tol=28.0):
    """Brush over an object: the selection grows from the stroke over connected pixels that look like what was
    brushed, and stops at the object's boundary (where the colour changes)."""
    H, W = stroke.shape
    ys, xs = np.nonzero(stroke > 0.5)
    if ys.size == 0:
        return np.zeros((H, W), np.float32)
    margin = int(max(60, 0.3 * max(H, W)))
    y0, y1 = max(0, int(ys.min()) - margin), min(H, int(ys.max()) + 1 + margin)
    x0, x1 = max(0, int(xs.min()) - margin), min(W, int(xs.max()) + 1 + margin)
    sub = lab[y0:y1, x0:x1]
    st = stroke[y0:y1, x0:x1] > 0.5
    vals = sub[st]
    ref = np.median(vals, axis=0)
    spread = float(np.mean(1.4826 * np.median(np.abs(vals - ref), axis=0)))
    thr = tol + min(18.0, 2.0 * spread)
    d = np.sqrt(((sub[..., 0] - ref[0]) * 0.8) ** 2 + (sub[..., 1] - ref[1]) ** 2 + (sub[..., 2] - ref[2]) ** 2)
    soft = 1.0 - _smooth((d - thr * 0.75) / (thr * 0.5))
    cand = soft > 0.5
    seed = st & cand
    if not seed.any():
        seed = st
    cur = Image.fromarray((seed * 255).astype(np.uint8))
    allow = Image.fromarray((cand * 255).astype(np.uint8))
    prev_n = int(seed.sum())
    for _ in range(max(6, margin // 2)):               # grow only through matching pixels (stays connected)
        nxt = ImageChops.multiply(cur.filter(ImageFilter.MaxFilter(5)), allow)
        n = int(np.count_nonzero(np.asarray(nxt)))
        cur = nxt
        if n == prev_n:
            break
        prev_n = n
    region = np.asarray(cur, dtype=np.float32) / 255.0
    region = np.maximum(region * soft, st.astype(np.float32))
    region = np.asarray(Image.fromarray((region * 255).astype(np.uint8)).filter(ImageFilter.GaussianBlur(0.8)),
                        dtype=np.float32) / 255.0
    out = np.zeros((H, W), np.float32)
    out[y0:y1, x0:x1] = region
    return out


def shift_mask(m, dx, dy):
    """The same selection moved by (dx, dy) pixels; what leaves the picture is cut off."""
    H, W = m.shape
    out = np.zeros_like(m)
    sx0, sx1 = max(0, -dx), min(W, W - dx)
    sy0, sy1 = max(0, -dy), min(H, H - dy)
    if sx1 > sx0 and sy1 > sy0:
        out[sy0 + dy:sy1 + dy, sx0 + dx:sx1 + dx] = m[sy0:sy1, sx0:sx1]
    return out


def move_region(img, mask, dx, dy, copy=False, feather=1.5):
    """Takes the selected part of the picture to another place.  mask = PIL 'L' mask at the picture's size.
    copy=False: the old place is filled from its surroundings (like erasing it); copy=True: it stays."""
    hard = mask.point(lambda v: 255 if v > 16 else 0)
    bbox = hard.getbbox()
    if bbox is None:
        return img
    base = img.copy() if copy else erase_region(img, mask, None, feather)
    patch = img.crop(bbox)
    pm = mask.crop(bbox).filter(ImageFilter.GaussianBlur(0.8))
    base.paste(patch, (bbox[0] + int(dx), bbox[1] + int(dy)), pm)
    return base


# --------------------------------------------------------------------- rotate
def border_color(img):
    a = np.asarray(img)
    edge = np.concatenate([a[0], a[-1], a[:, 0], a[:, -1]], 0)
    return tuple(int(v) for v in np.median(edge, axis=0))


def rotate_image(img, deg_ccw):
    """Turn the picture counter-clockwise by deg_ccw degrees. Multiples of 90 are lossless; any other angle
    makes the picture bigger and fills the corners with the colour of the picture's border."""
    d = float(deg_ccw) % 360.0
    if abs(d) < 1e-6 or abs(d - 360.0) < 1e-6:
        return img
    for angle, t in ((90.0, Image.Transpose.ROTATE_90), (180.0, Image.Transpose.ROTATE_180),
                     (270.0, Image.Transpose.ROTATE_270)):
        if abs(d - angle) < 1e-6:
            return img.transpose(t)
    return img.rotate(d, resample=Image.Resampling.BICUBIC, expand=True, fillcolor=border_color(img))


# ------------------------------------------------------------------ PDF as pictures (like Photoshop)
def is_pdf(path):
    return os.path.splitext(str(path))[1].lower() == PDF_EXT


def pdf_to_pictures(path, out_dir, progress=None, dpi=PDF_DPI, max_side=PDF_MAX_SIDE):
    """Every page of a PDF becomes its own PNG picture in out_dir.  Returns the list of file paths (page order).
    progress(fraction, text) is optional.  Needs the pypdfium2 package (it brings its own PDF engine)."""
    try:
        import pypdfium2 as pdfium
    except Exception as ex:
        raise RuntimeError("PDF support is missing (pypdfium2 is not installed): %s" % ex)
    os.makedirs(out_dir, exist_ok=True)
    base = os.path.splitext(os.path.basename(path))[0] or "PDF"
    try:
        doc = pdfium.PdfDocument(path)
    except Exception as ex:
        msg = str(ex)
        if "password" in msg.lower():
            raise RuntimeError("This PDF is protected by a password.")
        raise RuntimeError("Cannot open this PDF: %s" % msg)
    out = []
    try:
        n = len(doc)
        digits = max(2, len(str(n)))
        for i in range(n):
            if progress:
                progress(i / max(1, n), "%s  page %d / %d" % (base, i + 1, n))
            page = doc[i]
            try:
                w, h = page.get_size()                      # PDF points (1/72 inch)
                scale = dpi / 72.0
                if max(w, h) * scale > max_side:
                    scale = max_side / max(w, h)
                bmp = page.render(scale=scale, rotation=0, may_draw_forms=True)
                im = bmp.to_pil().convert("RGB")           # pages are drawn on white paper
            finally:
                page.close()
            p = os.path.join(out_dir, "%s_page%0*d.png" % (base, digits, i + 1))
            im.save(p, "PNG")
            out.append(p)
    finally:
        doc.close()
    if not out:
        raise RuntimeError("This PDF has no pages.")
    return out


# ------------------------------------------------------------------------ PDF
PAGE_SIZES = {"a4": (595.276, 841.89), "letter": (612.0, 792.0)}


def pdf_page(w, h, mode):
    """(page width, page height, image x, y, drawn width, drawn height) in PDF points.
    'fit'  : the page has the picture's own proportions (nothing is cut or padded)
    'a4' / 'letter' : standard paper, turned to match the picture, picture centred and as big as it fits."""
    if mode in PAGE_SIZES:
        pw, ph = PAGE_SIZES[mode]
        if w > h:
            pw, ph = ph, pw
        sc = min(pw / w, ph / h)
        dw, dh = w * sc, h * sc
        return pw, ph, (pw - dw) / 2, (ph - dh) / 2, dw, dh
    long = 841.89                                      # long side = A4 long side, short side follows the picture
    sc = long / max(w, h)
    return w * sc, h * sc, 0.0, 0.0, w * sc, h * sc


def save_pdf(sources, path, mode="fit", quality=95, progress=None):
    """One PDF, one page per picture, in the given order.  sources = list of PIL images or functions that return one
    (so a long list never sits in memory all at once).  The picture is embedded at full resolution as high-quality
    JPEG (4:4:4, nothing is resized)."""
    import io
    n = len(sources)
    if n == 0:
        raise ValueError("Nothing to save")
    offsets = {}
    with open(path, "wb") as f:
        f.write(b"%PDF-1.4\n%\xe2\xe3\xcf\xd3\n")

        def put(num, body):
            offsets[num] = f.tell()
            f.write(b"%d 0 obj\n" % num + body + b"\nendobj\n")

        for i, src in enumerate(sources):
            if progress:
                progress(i / n, "Page %d of %d" % (i + 1, n))
            im = src() if callable(src) else src
            im = im.convert("RGB")
            w, h = im.size
            buf = io.BytesIO()
            im.save(buf, "JPEG", quality=quality, subsampling=0, optimize=True)
            data = buf.getvalue()
            pw, ph, x, y, dw, dh = pdf_page(w, h, mode)
            base = 3 + 3 * i
            put(base, ("<< /Type /Page /Parent 2 0 R /MediaBox [0 0 %.2f %.2f] /Resources << /XObject << /Im0 %d 0 R >> "
                       "/ProcSet [/PDF /ImageC] >> /Contents %d 0 R >>" % (pw, ph, base + 2, base + 1)).encode())
            content = ("q %.3f 0 0 %.3f %.3f %.3f cm /Im0 Do Q" % (dw, dh, x, y)).encode()
            put(base + 1, b"<< /Length %d >>\nstream\n" % len(content) + content + b"\nendstream")
            put(base + 2, b"<< /Type /XObject /Subtype /Image /Width %d /Height %d /ColorSpace /DeviceRGB "
                          b"/BitsPerComponent 8 /Filter /DCTDecode /Length %d >>\nstream\n" % (w, h, len(data))
                + data + b"\nendstream")
            del im, data, buf
        kids = " ".join("%d 0 R" % (3 + 3 * i) for i in range(n))
        put(2, ("<< /Type /Pages /Kids [%s] /Count %d >>" % (kids, n)).encode())
        put(1, b"<< /Type /Catalog /Pages 2 0 R >>")
        total = 3 + 3 * n
        xref = f.tell()
        f.write(b"xref\n0 %d\n0000000000 65535 f \n" % total)
        for num in range(1, total):
            f.write(b"%010d 00000 n \n" % offsets[num])
        f.write(b"trailer\n<< /Size %d /Root 1 0 R >>\nstartxref\n%d\n%%%%EOF\n" % (total, xref))
    if progress:
        progress(1.0, "Done")


def sel_invert(m, shape):
    """Everything that is not selected.  With no selection: the whole picture."""
    if m is None:
        return np.ones(shape, np.float32)
    out = (1.0 - m).astype(np.float32)
    return out if float(out.max()) > 0.02 else None


def sel_grow(m, px):
    """Selection bigger (px > 0) or smaller (px < 0) by about px pixels."""
    if m is None or px == 0:
        return m
    im = Image.fromarray((np.clip(m, 0, 1) * 255 + 0.5).astype(np.uint8))
    rem = abs(int(px))
    filt = ImageFilter.MaxFilter if px > 0 else ImageFilter.MinFilter
    while rem > 0:
        r = min(rem, 6)
        im = im.filter(filt(2 * r + 1))
        rem -= r
    im = im.filter(ImageFilter.GaussianBlur(0.7))
    out = np.asarray(im, dtype=np.float32) / 255.0
    return out if float(out.max()) > 0.02 else None


def paint_brush(img, pts, brush, rgb, opacity=1.0, softness=0.0, sel=None, alpha=None):
    """Color brush (like a paint brush in a picture editor).  pts = brush stroke in real picture pixels, brush = width.
    Paints `rgb` along the stroke.  sel = selection (float 0..1, any size) -> paint only inside it; None = everywhere.
    alpha = PIL 'L' transparency of a placed picture -> paint only where the picture is (transparent stays transparent).
    opacity 0..1, softness 0 (hard edge) .. 100 (very soft).  Only the part around the stroke is processed (fast)."""
    img = img if img.mode == "RGB" else img.convert("RGB")
    W, H = img.size
    if not pts:
        return img
    w = max(1.0, float(brush))
    rad = max(0.8, float(softness) / 100.0 * w * 0.5)
    xs = [float(p[0]) for p in pts]
    ys = [float(p[1]) for p in pts]
    pad = w / 2.0 + rad * 3.0 + 3.0
    x0, y0 = max(0, int(math.floor(min(xs) - pad))), max(0, int(math.floor(min(ys) - pad)))
    x1, y1 = min(W, int(math.ceil(max(xs) + pad))), min(H, int(math.ceil(max(ys) + pad)))
    if x1 <= x0 or y1 <= y0:
        return img
    local = [(x - x0, y - y0) for x, y in zip(xs, ys)]
    m = make_mask((x1 - x0, y1 - y0), "brush", local, brush=w).filter(_PilFilter.GaussianBlur(rad))
    a = np.asarray(m, dtype=np.float32) / 255.0
    if sel is not None:
        a = a * resize_mask(sel, (W, H))[y0:y1, x0:x1]
    if alpha is not None:
        a = a * (np.asarray(alpha.crop((x0, y0, x1, y1)), dtype=np.float32) / 255.0)
    a = a * float(max(0.0, min(1.0, opacity)))
    reg = np.asarray(img.crop((x0, y0, x1, y1)), dtype=np.float32)
    col = np.array([int(v) for v in rgb], dtype=np.float32)
    res = reg + (col - reg) * a[..., None]
    out = img.copy()
    out.paste(Image.fromarray(np.clip(res + 0.5, 0, 255).astype(np.uint8)), (x0, y0))
    return out


def fill_region(img, mask, rgb):
    """Paints the masked area with one colour. mask = PIL 'L' at the picture's size."""
    solid = Image.new("RGB", img.size, tuple(int(v) for v in rgb))
    return Image.composite(solid, img, mask)


def transform_region(img, mask, cx, cy, sx, sy, ang_cw, soft=1.0):
    """Scales / rotates the selected part around its own centre and puts it at (cx, cy) (picture pixels).
    The old place is filled from its surroundings.  mask = PIL 'L'.  Returns (new picture, new mask 'L')."""
    hard = mask.point(lambda v: 255 if v > 16 else 0)
    bbox = hard.getbbox()
    if bbox is None:
        return img, mask
    base = erase_region(img, mask, None, soft)
    patch = img.crop(bbox).convert("RGBA")
    pm = mask.crop(bbox)
    patch.putalpha(pm)
    w, h = patch.size
    nw, nh = max(1, int(round(w * sx))), max(1, int(round(h * sy)))
    patch = patch.resize((nw, nh), Image.Resampling.BICUBIC)
    patch = patch.rotate(-ang_cw, resample=Image.Resampling.BICUBIC, expand=True)
    px, py = int(round(cx - patch.width / 2.0)), int(round(cy - patch.height / 2.0))
    base.paste(patch.convert("RGB"), (px, py), patch.split()[3])
    newmask = Image.new("L", img.size, 0)
    newmask.paste(patch.split()[3], (px, py))
    return base, newmask


def homography(src, dst):
    """3x3 matrix H with  dst ~ H @ src  for 4 point pairs."""
    A, b = [], []
    for (x, y), (u, v) in zip(src, dst):
        A.append([x, y, 1, 0, 0, 0, -u * x, -u * y]); b.append(u)
        A.append([0, 0, 0, x, y, 1, -v * x, -v * y]); b.append(v)
    h = np.linalg.solve(np.array(A, np.float64), np.array(b, np.float64))
    return np.append(h, 1.0).reshape(3, 3)


def warp_region(img, mask, grid, n, soft=1.0):
    """Perspective / distort / skew / warp of the selected part.
    grid = (n+1)*(n+1) points (x, y) in picture pixels, row by row: where the corners of the selection (n = 1) or
    the points of a 4x4 net (n = 3) are now.  The selected part is bent onto them; the old place is filled from the
    surroundings.  Returns (new picture, new mask 'L')."""
    hard = mask.point(lambda v: 255 if v > 16 else 0)
    bbox = hard.getbbox()
    if bbox is None:
        return img, mask
    base = erase_region(img, mask, None, soft).convert("RGBA")
    patch = img.crop(bbox).convert("RGBA")
    patch.putalpha(mask.crop(bbox))
    layer = warp_layer(patch, img.size, grid, n)
    out = Image.alpha_composite(base, layer).convert("RGB")
    return out, layer.split()[3]


def warp_layer(patch, size, grid, n):
    """An RGBA patch bent onto a net of points (n = 1: 4 corners, n = 3: 4x4 points, row by row, picture pixels).
    Returns an RGBA layer of the given size."""
    pw, ph = patch.size
    W, H = size
    layer = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    if patch.mode == "RGBA":
        patch = patch.convert("RGBa")                    # premultiplied: no pale halo from the colour under transparent pixels
    g = [[grid[i * (n + 1) + j] for j in range(n + 1)] for i in range(n + 1)]
    for i in range(n):
        for j in range(n):
            sx0, sx1 = j * pw / n, (j + 1) * pw / n
            sy0, sy1 = i * ph / n, (i + 1) * ph / n
            dq = [g[i][j], g[i][j + 1], g[i + 1][j + 1], g[i + 1][j]]
            xs = [p[0] for p in dq]; ys = [p[1] for p in dq]
            bx0, by0 = max(0, int(math.floor(min(xs)))), max(0, int(math.floor(min(ys))))
            bx1, by1 = min(W, int(math.ceil(max(xs)))), min(H, int(math.ceil(max(ys))))
            if bx1 - bx0 < 1 or by1 - by0 < 1:
                continue
            m = 0 if n == 1 else 2                       # a little overlap so the cells have no seams
            cx0, cy0 = max(0, int(sx0) - m), max(0, int(sy0) - m)
            cx1, cy1 = min(pw, int(math.ceil(sx1)) + m), min(ph, int(math.ceil(sy1)) + m)
            crop = patch.crop((cx0, cy0, cx1, cy1))
            sq = [(sx0 - cx0, sy0 - cy0), (sx1 - cx0, sy0 - cy0), (sx1 - cx0, sy1 - cy0), (sx0 - cx0, sy1 - cy0)]
            dloc = [(p[0] - bx0, p[1] - by0) for p in dq]
            try:
                Hm = homography(dloc, sq)                # output -> source, what PIL wants
            except np.linalg.LinAlgError:
                continue
            Hm = Hm / Hm[2, 2]
            co = (Hm[0, 0], Hm[0, 1], Hm[0, 2], Hm[1, 0], Hm[1, 1], Hm[1, 2], Hm[2, 0], Hm[2, 1])
            piece = crop.transform((bx1 - bx0, by1 - by0), Image.Transform.PERSPECTIVE, co, Image.Resampling.BICUBIC)
            layer.alpha_composite(piece.convert("RGBA"), (bx0, by0))
    return layer


def warp_layer_hq(patch, size, grid, n, ss=3, max_px=36_000_000):
    """warp_layer, but sharp: the picture is bent at 'ss' times the size and then shrunk with a proper filter
    (the plain transform takes one sample per pixel, which breaks thin pen strokes into dots).
    Only the box around the picture is worked on.  Returns an RGBA layer of the given size."""
    W, H = size
    xs = [p[0] for p in grid]; ys = [p[1] for p in grid]
    bx0, by0 = max(0, int(math.floor(min(xs))) - 2), max(0, int(math.floor(min(ys))) - 2)
    bx1, by1 = min(W, int(math.ceil(max(xs))) + 2), min(H, int(math.ceil(max(ys))) + 2)
    bw, bh = bx1 - bx0, by1 - by0
    out = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    if bw < 1 or bh < 1:
        return out
    # a bent picture (n = 3) may reach outside the box of its 4 outer points only a little; the box uses all points
    ss = max(1, min(ss, int(math.sqrt(max_px / float(bw * bh)))))
    if ss == 1:
        return warp_layer(patch, size, grid, n)
    g2 = [((x - bx0) * ss, (y - by0) * ss) for x, y in grid]
    big = warp_layer(patch, (bw * ss, bh * ss), g2, n)
    small = big.resize((bw, bh), Image.Resampling.LANCZOS)   # Pillow shrinks RGBA premultiplied
    out.paste(small, (bx0, by0))
    return out


# ------------------------------------------------------------------ picture layers (signature, stamp ...)
def _box_sum(a, r):
    """Sum of a (float32 h,w) in the (2r+1) x (2r+1) window around every pixel (integral image, exact)."""
    H, W = a.shape
    p = np.pad(a, ((r + 1, r), (r + 1, r)), mode="constant").astype(np.float64)
    s = p.cumsum(0).cumsum(1)
    k = 2 * r + 1
    return (s[k:k + H, k:k + W] - s[:H, k:k + W] - s[k:k + H, :W] + s[:H, :W]).astype(np.float32)


def despeckle_alpha(a, level):
    """Takes dust and paper grain out of a cut-out: a spot is kept only when enough 'ink' is around it, so strokes (long,
    even faint ones) stay and isolated specks go.  level 0..100."""
    if level <= 0:
        return a
    H, W = a.shape
    r = int(np.clip(round(0.007 * max(H, W)), 3, 12))
    mass = _box_sum(np.clip(a * 2.5, 0.0, 1.0), r)
    T = (2 * r + 1) ** 2 * (0.02 + 0.16 * level / 100.0)
    keep = _smooth((mass - 0.6 * T) / (0.6 * T))
    return a * keep


def remove_background(img, alpha=None, strength=50, soft=30, uneven=False, tint="orig", opacity=100, remove=True,
                      specks=50, fine=70):
    """PIL RGB (+ optional 'L' alpha of its own) -> PIL RGBA with the background taken away.
    The background colour is read from the border; with 'uneven' (grey print paper, shading) it is followed across the
    picture.  Edge pixels are un-mixed from the paper colour, so a signature / stamp has no light halo."""
    rgb = np.asarray(img.convert("RGB"))
    H, W = rgb.shape[:2]
    x = rgb.astype(np.float32)
    a = np.ones((H, W), np.float32)
    C = x
    if remove:
        lab = rgb_to_lab(rgb)
        k = max(1, min(4, H // 8, W // 8))
        border = np.concatenate([rgb[:k].reshape(-1, 3), rgb[-k:].reshape(-1, 3),
                                 rgb[:, :k].reshape(-1, 3), rgb[:, -k:].reshape(-1, 3)])
        bgc = np.median(border, axis=0).astype(np.uint8)
        bl = rgb_to_lab(bgc[None, None, :])[0, 0]
        # the border is not always paper (ink touching it, a table around the picture): then the paper colour is the
        # middle colour of the lightest half of the picture
        Lall = lab[..., 0]
        step = max(1, int(Lall.size // 200000))
        lf, rf = Lall.reshape(-1)[::step], rgb.reshape(-1, 3)[::step]
        light = rf[lf >= np.percentile(lf, 50)]
        if len(light) > 16:
            bgc2 = np.median(light, axis=0).astype(np.uint8)
            bl2 = rgb_to_lab(bgc2[None, None, :])[0, 0]
            if float(np.sqrt(((bl - bl2) ** 2).sum())) > 12.0:
                bgc, bl = bgc2, bl2
        B = np.broadcast_to(bgc.astype(np.float32), x.shape)
        Bl = bl
        if uneven:
            d0 = np.sqrt(((lab - bl) ** 2).sum(2))
            valid = (d0 < 30.0).astype(np.float32)
            if valid.sum() > 16:
                F = fill_background(rgb, valid)
                r = max(3.0, 0.01 * max(H, W))
                F = np.stack([np.asarray(Image.fromarray(F[..., ch]).filter(_PilFilter.GaussianBlur(r)))
                              for ch in range(3)], -1)
                B = F.astype(np.float32)
                Bl = rgb_to_lab(F)
        dist = np.sqrt(((lab - Bl) ** 2).sum(2))
        pap = dist[dist < 25.0]
        P = float(np.percentile(pap[::max(1, pap.size // 100000)], 90)) if pap.size > 64 else 6.0     # how noisy the paper is
        t0 = (max(3.0, 1.15 * P) + 0.18 * strength) * (1.35 - 0.7 * fine / 100.0)
        t1 = t0 + 6.0 + 0.5 * soft
        a = _smooth((dist - t0) / (t1 - t0))
        a = despeckle_alpha(a, specks)
        a3 = a[..., None]
        C = np.where(a3 > 0.97, x, np.clip((x - B * (1.0 - a3)) / np.maximum(a3, 0.08), 0, 255))
    if alpha is not None:
        a = a * (np.asarray(alpha.convert("L"), dtype=np.float32) / 255.0)
    if tint == "black":
        C = np.broadcast_to(np.array([22, 22, 24], np.float32), x.shape)
    elif tint == "blue":
        C = np.broadcast_to(np.array([24, 52, 170], np.float32), x.shape)
    a = a * (opacity / 100.0)
    out = np.empty((H, W, 4), np.uint8)
    out[..., :3] = np.clip(C + 0.5, 0, 255)
    out[..., 3] = np.clip(a * 255.0 + 0.5, 0, 255)
    return Image.fromarray(out, "RGBA")


def content_box(alpha, pad=0.03, thr=14):
    """Box of everything that is not transparent, with a little room (None if empty)."""
    b = alpha.point(lambda v: 255 if v > thr else 0).getbbox()
    if b is None:
        return None
    w, h = alpha.size
    px = int(max(2, pad * max(b[2] - b[0], b[3] - b[1])))
    return (max(0, b[0] - px), max(0, b[1] - px), min(w, b[2] + px), min(h, b[3] + px))


def layer_rgba(l, pend=True):
    """RGBA of a layer dict (rgb, a, opacity; an unfinished edit 'pend' = (adjs, look, sel) is applied too)."""
    rgb = l["rgb"]
    if pend and l.get("pend"):
        adjs, look, sel = l["pend"]
        rgb = render_full(rgb, adjs, look, sel)
    out = rgb.convert("RGBA")
    a = l["a"]
    if l.get("opacity", 100) < 100:
        a = a.point(lambda v, f=l["opacity"] / 100.0: int(v * f))
    out.putalpha(a)
    return out


def composite_layers(base, layers, hide=None):
    """PIL RGB page + layers (placed with a normalised net 'grid', n) -> PIL RGB."""
    if not layers:
        return base
    W, H = base.size
    out = base.convert("RGBA")
    for l in layers:
        if not l.get("visible", True) or l["id"] == hide:
            continue
        n = l["n"]
        grid = [(x * W, y * H) for x, y in l["grid"]]
        patch = layer_rgba(l)
        tw = max(math.dist(grid[0], grid[n]), math.dist(grid[n * (n + 1)], grid[-1]))
        # Never shrink the picture below the size it is shown at (the supersampled warp below does the final,
        # filtered shrink): keep 3x the shown width, at most the picture's own size.
        want = int(tw * 3)
        if 8 <= want < patch.width:
            patch = patch.resize((want, max(1, int(round(want * patch.height / patch.width)))), Image.Resampling.LANCZOS)
        elif patch.width < tw * 1.05 and tw < 9000:           # shown LARGER than its pixels: upscale it with a good filter first
            up = int(tw * 1.5)
            patch = patch.resize((up, max(1, int(round(up * patch.height / patch.width)))), Image.Resampling.LANCZOS)
        out = Image.alpha_composite(out, warp_layer_hq(patch, (W, H), grid, n))
    return out.convert("RGB")


def checker(w, h, cell=10):
    yy, xx = np.mgrid[0:h, 0:w]
    c = (((yy // cell) + (xx // cell)) % 2).astype(np.float32)
    v = 205.0 + 35.0 * c
    return np.stack([v, v, v], -1)


def show_alpha(rgb, alpha):
    """Layer picture on a chess-board, for the layer editor.  rgb uint8 (H,W,3), alpha 'L' (any size)."""
    H, W = rgb.shape[:2]
    a = np.asarray(alpha.resize((W, H), Image.Resampling.BILINEAR), dtype=np.float32)[..., None] / 255.0
    return np.clip(rgb.astype(np.float32) * a + checker(W, H) * (1 - a) + 0.5, 0, 255).astype(np.uint8)


def remove_stamp(img, mask):
    """Removes a stamp / seal (the selected part) but KEEPS a black signature that crosses it.
    The stamp becomes paper again (filled from around); dark neutral strokes inside the selection are kept, their
    blue tint is taken out, and small gaps in the strokes are bridged so the signature looks complete.
    mask = PIL 'L' selection at the picture's size."""
    hard = mask.point(lambda v: 255 if v > 40 else 0)
    bbox = hard.getbbox()
    if bbox is None:
        return img
    W, H = img.size
    m = 14
    box = (max(0, bbox[0] - m), max(0, bbox[1] - m), min(W, bbox[2] + m), min(H, bbox[3] + m))
    full = np.asarray(img)
    reg = full[box[1]:box[3], box[0]:box[2]].copy()
    hole_img = hard.crop(box).filter(ImageFilter.MaxFilter(7))
    hole = np.asarray(hole_img) > 127
    F = fill_background(reg, (~hole).astype(np.float32)).astype(np.float32)
    lab = rgb_to_lab(reg)
    Lp = np.maximum(rgb_to_lab(np.clip(F, 0, 255).astype(np.uint8))[..., 0], 40.0)
    L = lab[..., 0]
    C = np.hypot(lab[..., 1], lab[..., 2])
    sig = hole & (L < 0.58 * Lp) & (C < 45.0)                    # dark, hardly coloured = the signature
    gray = (reg[..., 0] * 0.299 + reg[..., 1] * 0.587 + reg[..., 2] * 0.114).astype(np.float32)
    out = F.copy()
    if sig.any():
        a0 = np.clip((0.75 * Lp - L) / (0.30 * Lp), 0.0, 1.0)
        a0 = np.where(sig, a0, 0.0)[..., None]
        sg = np.repeat(gray[..., None], 3, 2)
        out = out * (1 - a0) + sg * a0
        # bridge small gaps in the strokes (the stamp ink was hiding them)
        s_img = Image.fromarray((sig * 255).astype(np.uint8))
        closed = np.asarray(s_img.filter(ImageFilter.MaxFilter(5)).filter(ImageFilter.MinFilter(5))) > 127
        gap = closed & ~sig & hole
        if gap.any():
            col = float(np.median(gray[sig]))
            gsoft = np.asarray(Image.fromarray((gap * 255).astype(np.uint8)).filter(ImageFilter.GaussianBlur(0.6)),
                               dtype=np.float32)[..., None] / 255.0 * 0.85
            out = out * (1 - gsoft) + col * gsoft
    soft = np.asarray(mask.crop(box).filter(ImageFilter.MaxFilter(7)).filter(ImageFilter.GaussianBlur(1.5)),
                      dtype=np.float32)[..., None] / 255.0
    res = reg.astype(np.float32) * (1 - soft) + out * soft
    result = img.copy()
    result.paste(Image.fromarray(np.clip(res + 0.5, 0, 255).astype(np.uint8)), (box[0], box[1]))
    return result
