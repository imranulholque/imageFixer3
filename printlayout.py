"""Print layout engine of Image Fixer's built-in print screen.

No Qt in this file: only Pillow and maths, so it can be tested alone (and it never makes the window wait).
  * scaling      : fit / fill / actual size / custom percent, pictures turned to fit the paper
  * n-up         : 2, 4, 6, 8, 12, 16 pages on one sheet (+ the "copies fill the sheet" rule of PC Print)
  * page numbers : stamped on the page (Times New Roman), also written as real text into a PDF
  * Transform    : free layout (move / resize / rotate / copy pictures on a sheet) as plain data + the maths for it
Ported from PC Print (PrintMerged), so the printout is the same.
"""
import copy
import functools
import math
import os

from PIL import Image, ImageDraw, ImageFont

NUP_CHOICES = (2, 4, 6, 8, 12, 16)      # pages on one sheet (1 = off)
NUP_GAP = 2.0                            # mm of empty space between the small pages
NUP_TURN = 1.15                          # a page is turned 90 degrees only if that makes it at least 15% bigger
SCALES = ("fit", "fill", "actual", "custom")
MAX_PX = 8000.0                          # safety size of one drawn picture
PRINT_MAX_DPI = 600.0                    # the printer is fed at most this many dots per inch
PREVIEW_SIDE = 1800                      # longest side of the screen copy of a picture
MIN_ITEM_MM, MAX_ITEM_MM = 2.0, 3000.0


def _num(v, default):
    try:
        v = float(v)
        return v if math.isfinite(v) else default
    except Exception:
        return default


def _clamp(v, lo, hi):
    return max(lo, min(hi, v))


# ------------------------------------------------------------------------------------------------ page numbers
class NumSpec:
    """Where and how the page number is printed."""
    POS = ("bottom", "top")
    ALIGN = ("left", "center", "right")

    def __init__(self, on=False, pos="bottom", align="center", size=12.0, start=1, margin=8.0, bold=False, keep_own=True):
        self.on = bool(on)
        self.pos = pos if pos in self.POS else "bottom"
        self.align = align if align in self.ALIGN else "center"
        self.size = _clamp(_num(size, 12.0), 6.0, 72.0)                    # points
        self.start = int(_clamp(_num(start, 1), -9999, 99999))             # the number of the first page
        self.margin = _clamp(_num(margin, 8.0), 2.0, 40.0)                 # mm from the edge
        self.bold = bool(bold)
        self.keep_own = bool(keep_own)      # print only some pages: each keeps the number it has in the whole list

    def text(self, n):
        return str(self.start - 1 + int(n))

    def scaled(self, k):
        """Same place, smaller / bigger in the ratio k (the small pages of an n-up sheet)."""
        c = copy.copy(self)
        c.size, c.margin = self.size * k, self.margin * k
        return c

    def to_dict(self):
        return {"on": self.on, "pos": self.pos, "align": self.align, "size": self.size, "start": self.start,
                "margin": self.margin, "bold": self.bold, "keep_own": self.keep_own}

    @classmethod
    def from_dict(cls, d):
        d = d or {}
        return cls(**{k: d[k] for k in ("on", "pos", "align", "size", "start", "margin", "bold", "keep_own") if k in d})


def numbering(order, checked, keep_own=True):
    """{page key: serial number}.  keep_own: the place in the whole list (so one page keeps its number when it is the
    only one printed); otherwise the place among the printed (ticked) pages."""
    nums, n = {}, 0
    for i, p in enumerate(order):
        if keep_own:
            nums[p.key] = i + 1
        elif p.key in checked:
            n += 1
            nums[p.key] = n
    return nums


@functools.lru_cache(maxsize=64)
def get_font(px, bold=False):
    win = os.environ.get("WINDIR", r"C:\Windows")
    names = (["timesbd.ttf", "Times New Roman Bold.ttf", "LiberationSerif-Bold.ttf", "DejaVuSerif-Bold.ttf"]
             if bold else
             ["times.ttf", "Times New Roman.ttf", "LiberationSerif-Regular.ttf", "DejaVuSerif.ttf"])
    for name in [os.path.join(win, "Fonts", names[0])] + names:
        try:
            return ImageFont.truetype(name, max(6, int(px)))
        except Exception:
            pass
    try:
        return ImageFont.load_default(size=max(6, int(px)))
    except Exception:
        return ImageFont.load_default()


def stamp(img, text, spec, ppmm):
    """Prints the page number directly on the picture, at the chosen place."""
    d = ImageDraw.Draw(img)
    fnt = get_font(round(spec.size * 25.4 / 72 * ppmm), spec.bold)
    left, top, right, bottom = d.textbbox((0, 0), text, font=fnt)
    tw, th = right - left, bottom - top
    W, H = img.size
    m = int(spec.margin * ppmm)
    if spec.align == "left":
        x = m
    elif spec.align == "right":
        x = W - m - tw
    else:
        x = (W - tw) // 2
    y = m if spec.pos == "top" else H - m - th
    d.text((x - left, y - top), text, font=fnt, fill=0 if img.mode == "L" else (0, 0, 0))


# ------------------------------------------------------------------------------------------------ pages
def flatten(img):
    """Any picture -> plain RGB (transparent parts become white paper)."""
    if img.mode in ("RGBA", "LA", "PA") or (img.mode == "P" and "transparency" in img.info):
        rgba = img.convert("RGBA")
        bg = Image.new("RGB", rgba.size, "white")
        bg.paste(rgba, mask=rgba.getchannel("A"))
        return bg
    return img.convert("RGB")


class Page:
    """One picture of the print list.  loader() makes the full quality picture (called when it is needed, in a worker
    thread); preview is a small copy for the screen.  blank = an added empty page."""

    def __init__(self, key, name="", loader=None, blank=False, dpi=300.0, size=None):
        self.key, self.name, self.loader, self.blank = key, name, loader, blank
        self.dpi = dpi if dpi and dpi >= 1 else 300.0
        self.size = size                  # (w, h) pixels of the full picture, known after load / make_preview
        self.preview = None
        self.error = None

    def load(self):
        if self.blank or self.loader is None:
            return None
        return flatten(self.loader())

    def make_preview(self):
        """Loads the picture once, keeps only a small copy.  Returns False when it could not be read."""
        if self.blank:
            return True
        try:
            img = self.load()
            self.size = img.size
            pv = img
            if max(img.size) > PREVIEW_SIDE:
                pv = img.copy()
                pv.thumbnail((PREVIEW_SIDE, PREVIEW_SIDE), Image.LANCZOS)
            self.preview = pv
            return True
        except Exception as ex:
            self.error = str(ex)
            return False

    @property
    def aspect(self):
        """width / height"""
        if self.size and self.size[0] > 0 and self.size[1] > 0:
            return self.size[0] / float(self.size[1])
        return 210 / 297.0

    def natural_mm(self):
        """Size at 100% in mm."""
        if self.size and self.size[0] > 0 and self.size[1] > 0:
            return self.size[0] / self.dpi * 25.4, self.size[1] / self.dpi * 25.4
        return 210.0, 297.0


class Cache:
    """The last few full pictures of a job: a page that appears twice (copies in n-up) is made only once."""

    def __init__(self, n=2, use_preview=False):
        self.n, self.use_preview, self._d = n, use_preview, {}

    def get(self, page):
        if page is None or page.blank:
            return None
        if self.use_preview:
            return page.preview
        if page.key in self._d:
            return self._d[page.key]
        img = page.load()
        if len(self._d) >= self.n:
            self._d.pop(next(iter(self._d)))
        self._d[page.key] = img
        return img


# ------------------------------------------------------------------------------------------------ one page on one sheet
def pick_scale(mode, W, H, fw, fh, ppmm, dpi, pct):
    """Pixels per source pixel for a picture fw x fh on an area W x H."""
    if mode == "fill":
        s = max(W / fw, H / fh)
    elif mode == "actual" and ppmm:
        s = ppmm * 25.4 / dpi
    elif mode == "custom" and ppmm:
        s = ppmm * 25.4 / dpi * pct / 100.0
    else:
        s = min(W / fw, H / fh)
    return min(s, MAX_PX / max(fw, fh))


def compose(img, W, H, mono, rotate=True, mode="fit", ppmm=None, pct=100.0, dpi=300.0):
    """One picture on a white sheet of W x H pixels.
    fit: whole picture inside the area | fill: covers the area, the rest is cut | actual: real size | custom: real size x pct."""
    white = 255 if mono else (255, 255, 255)
    canvas = Image.new("L" if mono else "RGB", (W, H), white)
    if img is None:
        return canvas
    iw, ih = img.size
    rot = bool(rotate) and ((iw > ih) != (W > H))
    fw, fh = (ih, iw) if rot else (iw, ih)
    s = pick_scale(mode, W, H, fw, fh, ppmm, dpi, pct)
    tile = img.resize((max(1, int(iw * s)), max(1, int(ih * s))), Image.LANCZOS)
    if mono:
        tile = tile.convert("L")
    if rot:
        tile = tile.rotate(90, expand=True)
    canvas.paste(tile, ((W - tile.width) // 2, (H - tile.height) // 2))     # a bigger picture is cut at the edges
    return canvas


def fit_geometry(page, aw, ah, mode, pct, rotate):
    """What compose() would draw, as a Transform picture: (w mm, h mm, rotation clockwise degrees), centred on the area."""
    nw, nh = page.natural_mm()
    rot = bool(rotate) and ((nw > nh) != (aw > ah))
    fw, fh = (nh, nw) if rot else (nw, nh)
    if mode == "fill":
        s = max(aw / fw, ah / fh)
    elif mode == "actual":
        s = 1.0
    elif mode == "custom":
        s = pct / 100.0
    else:
        s = min(aw / fw, ah / fh)
    return nw * s, nh * s, (270.0 if rot else 0.0)


# ------------------------------------------------------------------------------------------------ n-up
def get_nup(v):
    try:
        v = int(float(v))
    except Exception:
        return 1
    return v if v in NUP_CHOICES else 1


def _nup_fit(cw, ch, asp):
    """Biggest page height inside a cw x ch cell, and whether the page has to be turned 90 degrees for that."""
    s1 = min(cw / asp, ch)
    s2 = min(cw, ch / asp)
    return (s2, True) if s2 > s1 * NUP_TURN else (s1, False)


def nup_grid(n, aw, ah, asp):
    """(columns, rows) for n pages on an aw x ah mm area: the split that makes every page biggest."""
    best = None
    for c in range(1, n + 1):
        if n % c:
            continue
        r = n // c
        cw = (aw - NUP_GAP * (c - 1)) / c
        ch = (ah - NUP_GAP * (r - 1)) / r
        if cw <= 1 or ch <= 1:
            continue
        s, _ = _nup_fit(cw, ch, asp)
        if best is None or s > best[0] * 1.0001:
            best = (s, c, r)
    return (best[1], best[2]) if best else (n, 1)


def nup_plan(pages, n, copies):
    """Sheets for 'n pages on one sheet' + copies.  Returns (sheets = list of page lists, copies for the printer).
    The whole job fits on ONE sheet: the copies fill the empty places (1 page, 2 on a sheet, 2 copies = the page twice) and the
    printer gets copies = 1.  A longer job: every copy is printed as its own set of sheets."""
    pages = list(pages)
    m = len(pages)
    groups = lambda seq: [seq[i:i + n] for i in range(0, len(seq), n)]
    if m == 0 or m > n or copies <= 1:
        return groups(pages), copies
    per = n // m
    out, left = [], copies
    while left > 0:
        take = min(per, left)
        out.append(pages * take)
        left -= take
    return out, 1


def nup_sheet(pages, nums, W, H, mono, ppmm, grid, spec, cache):
    """Several pages on one sheet.  Every page is fitted whole into its own cell (turned 90 degrees when that is bigger);
    the page number sits on each page itself, smaller in the same ratio."""
    cols, rows = grid
    white = 255 if mono else (255, 255, 255)
    canvas = Image.new("L" if mono else "RGB", (W, H), white)
    gap = max(0, round(NUP_GAP * ppmm))
    cw, ch = max(1, (W - gap * (cols - 1)) // cols), max(1, (H - gap * (rows - 1)) // rows)
    k = min(1.0, math.sqrt((cw * ch) / float(W * H)))
    for i, pg in enumerate(pages[:cols * rows]):
        r, c = divmod(i, cols)
        asp = pg.aspect
        _, turn = _nup_fit(cw, ch, asp)
        tw, th = (ch, cw) if turn else (cw, ch)
        s = min(tw / asp, th)
        pw, ph = max(1, round(s * asp)), max(1, round(s))
        tile = compose(cache.get(pg), pw, ph, mono, False, "fit", ppmm, 100.0, pg.dpi)     # the tile is exactly the page
        n = nums[i] if i < len(nums) else None
        if spec is not None and spec.on and n is not None:
            stamp(tile, spec.text(n), spec.scaled(k), ppmm)
        if turn:
            tile = tile.rotate(90, expand=True)
        canvas.paste(tile, (c * (cw + gap) + (cw - tile.width) // 2, r * (ch + gap) + (ch - tile.height) // 2))
    return canvas


# ------------------------------------------------------------------------------------------------ Transform: items
# An item = one picture put on a sheet: centre (cx, cy) and size (w, h) in mm from the printable area's top-left corner,
# rot = clockwise degrees, pk = key of the page it shows, id = unique number.
def compose_items(W, H, mono, ppmm, items, pages, cache):
    """White sheet with every picture where it was put, each with its own size and rotation (later ones on top)."""
    canvas = Image.new("L" if mono else "RGB", (W, H), 255 if mono else (255, 255, 255))
    for it in items:
        pg = pages.get(it["pk"])
        src = cache.get(pg) if pg is not None else None
        if src is None:
            continue
        tw, th = max(1, round(it["w"] * ppmm)), max(1, round(it["h"] * ppmm))
        if tw * th > 60_000_000:
            continue
        im = src if src.size == (tw, th) else src.resize((tw, th), Image.LANCZOS)
        rot = it["rot"] % 360
        if 0.01 < rot < 359.99:
            rgba = im.convert("RGBA").rotate(-rot, expand=True, resample=Image.BICUBIC)
            tile, mask = rgba.convert(canvas.mode), rgba.getchannel("A")
        else:
            tile, mask = (im.convert("L") if mono else im), None
        x = round(it["cx"] * ppmm - tile.width / 2)
        y = round(it["cy"] * ppmm - tile.height / 2)
        canvas.paste(tile, (x, y), mask)
    return canvas


# ------------------------------------------------------------------------------------------------ sheets
class Sheet:
    """One printed sheet.  kind: 'page' (one picture) | 'nup' (several pages) | 'blank' (an added empty page).
    nums = the page number of each page on it; items = Transform pictures (None = the normal way)."""

    def __init__(self, kind, pages, nums, key=None, items=None):
        self.kind, self.pages, self.nums, self.key, self.items = kind, list(pages), list(nums), key, items

    def signature(self):
        return (self.kind, tuple(p.key for p in self.pages), tuple(self.nums),
                None if self.items is None else repr([(i["pk"], i["cx"], i["cy"], i["w"], i["h"], i["rot"]) for i in self.items]))


class Options:
    """Everything that decides how a sheet looks (copied, so a worker thread never sees it change)."""

    def __init__(self, mode="fit", pct=100.0, rotate=True, spec=None, grid=(1, 1), pages=None):
        self.mode = mode if mode in SCALES else "fit"
        self.pct = _clamp(_num(pct, 100.0), 10.0, 400.0)
        self.rotate = bool(rotate)
        self.spec = spec
        self.grid = grid
        self.pages = pages or {}           # key -> Page (for Transform pictures)


def plan(pages, nums, nup, copies, lay=None):
    """The sheets of a job.  pages = the ticked pages in print order.  lay = Transform layout {page key: [items]} or None.
    Returns (sheets, copies for the printer)."""
    if nup > 1:
        groups, pcopies = nup_plan(pages, nup, copies)
        return [Sheet("nup", g, [nums.get(p.key) for p in g]) for g in groups], pcopies
    sheets = []
    for p in pages:
        items = None
        if lay is not None:
            items = lay.get(p.key, [])
        sheets.append(Sheet("blank" if p.blank else "page", [p], [nums.get(p.key)], key=p.key, items=items))
    return sheets, copies


def display_sheets(sheets):
    """For the screen: sheets that look the same are shown once.  Returns [(sheet, how many times)]."""
    out = []
    for s in sheets:
        if out and out[-1][0].signature() == s.signature():
            out[-1][1] += 1
        else:
            out.append([s, 1])
    return [(s, n) for s, n in out]


def sequence(n_sheets, copies, two_sided):
    """Order of the sheets sent to the printer.  None = a blank back side, so that the next copy starts on a new sheet."""
    seq = []
    for k in range(copies):
        seq += list(range(n_sheets))
        if two_sided and n_sheets % 2 == 1 and k < copies - 1:
            seq.append(None)
    return seq


def render_sheet(sheet, W, H, mono, ppmm, opt, cache):
    """The picture of one sheet, W x H pixels (ppmm = pixels per mm)."""
    spec = opt.spec
    if sheet.kind == "nup":
        return nup_sheet(sheet.pages, sheet.nums, W, H, mono, ppmm, opt.grid, spec, cache)
    pg = sheet.pages[0] if sheet.pages else None
    if sheet.items is not None:
        img = compose_items(W, H, mono, ppmm, sheet.items, opt.pages, cache)
    elif sheet.kind == "blank" or pg is None:
        img = compose(None, W, H, mono)
    else:
        img = compose(cache.get(pg), W, H, mono, opt.rotate, opt.mode, ppmm, opt.pct, pg.dpi)
    n = sheet.nums[0] if sheet.nums else None
    if spec is not None and spec.on and n is not None:
        stamp(img, spec.text(n), spec, ppmm)
    return img


# ------------------------------------------------------------------------------------------------ Transform: maths
def _rot_pt(x, y, deg):
    a = math.radians(deg)
    c, s = math.cos(a), math.sin(a)
    return x * c - y * s, x * s + y * c


def item_corners(it):
    """The 4 corners (nw, ne, se, sw) in sheet mm."""
    out = []
    for sx, sy in ((-1, -1), (1, -1), (1, 1), (-1, 1)):
        x, y = _rot_pt(sx * it["w"] / 2, sy * it["h"] / 2, it["rot"])
        out.append((it["cx"] + x, it["cy"] + y))
    return out


def to_local(it, x, y):
    """A point of the sheet -> the picture's own (unrotated, centre = 0,0) coordinates."""
    return _rot_pt(x - it["cx"], y - it["cy"], -it["rot"])


def hit_item(it, x, y):
    lx, ly = to_local(it, x, y)
    return abs(lx) <= it["w"] / 2 and abs(ly) <= it["h"] / 2


HANDLES = ("nw", "n", "ne", "e", "se", "s", "sw", "w")
_DIR = {"nw": (-1, -1), "n": (0, -1), "ne": (1, -1), "e": (1, 0), "se": (1, 1), "s": (0, 1), "sw": (-1, 1), "w": (-1, 0)}


def handle_points(it, rot_offset=8.0):
    """{handle name: (x, y)} in sheet mm; 'rot' = the turn handle, rot_offset mm above the top edge."""
    pts = {}
    for name, (dx, dy) in _DIR.items():
        x, y = _rot_pt(dx * it["w"] / 2, dy * it["h"] / 2, it["rot"])
        pts[name] = (it["cx"] + x, it["cy"] + y)
    x, y = _rot_pt(0, -it["h"] / 2 - rot_offset, it["rot"])
    pts["rot"] = (it["cx"] + x, it["cy"] + y)
    return pts


def handle_at(it, x, y, radius, rot_offset=8.0):
    """The handle within radius mm of (x, y), or None.  The turn handle is checked first."""
    pts = handle_points(it, rot_offset)
    order = ["rot"] + list(HANDLES)
    best, bd = None, radius
    for name in order:
        px, py = pts[name]
        d = math.hypot(px - x, py - y)
        if d <= bd:
            best, bd = name, d
            if name == "rot":
                break
    return best


def resize_item(it0, handle, x, y, keep_aspect=True):
    """The picture it0 with the handle pulled to (x, y).  The opposite corner / edge stays where it is.  Returns a new dict."""
    dx, dy = _DIR[handle]
    w0, h0 = it0["w"], it0["h"]
    px, py = to_local(it0, x, y)
    fx, fy = -dx * w0 / 2, -dy * h0 / 2                 # the fixed point (local)
    if dx and dy:
        nw = (px - fx) * dx
        nh = (py - fy) * dy
        if keep_aspect:
            f = max(nw / w0, nh / h0)
            nw, nh = w0 * f, h0 * f
    elif dx:
        nw, nh = (px - fx) * dx, h0
    else:
        nw, nh = w0, (py - fy) * dy
    nw = _clamp(nw, MIN_ITEM_MM, MAX_ITEM_MM)
    nh = _clamp(nh, MIN_ITEM_MM, MAX_ITEM_MM)
    ccx = fx + dx * nw / 2                               # new centre (local)
    ccy = fy + dy * nh / 2
    ox, oy = _rot_pt(ccx, ccy, it0["rot"])
    out = dict(it0)
    out.update(w=nw, h=nh, cx=it0["cx"] + ox, cy=it0["cy"] + oy)
    return out


def rotation_for(it, x, y, snap=False):
    """Clockwise degrees that make the turn handle point at (x, y)."""
    deg = math.degrees(math.atan2(x - it["cx"], -(y - it["cy"])))
    if snap:
        deg = round(deg / 15.0) * 15.0
    return deg % 360.0


def snap_straight(dx, dy):
    """Shift while moving: straight sideways, up / down or exactly diagonal (nearest of the 8 directions)."""
    d = math.hypot(dx, dy)
    if d < 1e-9:
        return 0.0, 0.0
    ang = math.atan2(dy, dx)
    snap = round(ang / (math.pi / 4)) * (math.pi / 4)
    L = d * math.cos(ang - snap)
    return L * math.cos(snap), L * math.sin(snap)


class TransformModel:
    """The Transform layout of a print job as data: {sheet key: [items]}, the selection, undo / redo, copy / paste."""

    def __init__(self):
        self.lay = {}
        self.sel = []                # ids of the selected pictures
        self.edited = set()          # sheet keys the user changed (their layout is kept when the paper / scale changes)
        self.clip = []
        self._n = 0
        self._undo, self._redo = [], []

    # ---- items
    def new_item(self, pk, cx, cy, w, h, rot=0.0):
        self._n += 1
        return {"id": self._n, "pk": pk, "cx": float(cx), "cy": float(cy), "w": float(w), "h": float(h), "rot": float(rot)}

    def find(self, iid):
        for key, items in self.lay.items():
            for it in items:
                if it["id"] == iid:
                    return key, it
        return None, None

    def selected(self):
        out = []
        for iid in self.sel:
            key, it = self.find(iid)
            if it is not None:
                out.append((key, it))
        return out

    def select(self, ids, add=False):
        ids = list(ids)
        self.sel = (list(self.sel) + [i for i in ids if i not in self.sel]) if add else ids

    def toggle(self, iid):
        if iid in self.sel:
            self.sel.remove(iid)
        else:
            self.sel.append(iid)

    def hit(self, key, x, y):
        """Id of the top-most picture of sheet key at (x, y), or None."""
        for it in reversed(self.lay.get(key, [])):
            if hit_item(it, x, y):
                return it["id"]
        return None

    def in_rect(self, key, x0, y0, x1, y1):
        """Ids of the pictures of the sheet that touch the rectangle (used by the rubber band)."""
        x0, x1 = min(x0, x1), max(x0, x1)
        y0, y1 = min(y0, y1), max(y0, y1)
        out = []
        for it in self.lay.get(key, []):
            xs = [c[0] for c in item_corners(it)]
            ys = [c[1] for c in item_corners(it)]
            if max(xs) >= x0 and min(xs) <= x1 and max(ys) >= y0 and min(ys) <= y1:
                out.append(it["id"])
        return out

    # ---- history
    def push(self, extra=None):
        self._undo.append((copy.deepcopy(self.lay), list(self.sel), set(self.edited), copy.deepcopy(extra)))
        del self._undo[:-100]
        self._redo.clear()

    def _swap(self, src, dst, extra):
        if not src:
            return None
        dst.append((copy.deepcopy(self.lay), list(self.sel), set(self.edited), copy.deepcopy(extra)))
        lay, sel, edited, ex = src.pop()
        self.lay, self.sel, self.edited = lay, [i for i in sel if self.find(i)[1] is not None], edited
        return (ex,)

    def undo(self, extra=None):
        """Returns None (nothing to undo) or a 1-tuple with the 'extra' state saved with that step."""
        return self._swap(self._undo, self._redo, extra)

    def redo(self, extra=None):
        return self._swap(self._redo, self._undo, extra)

    @property
    def can_undo(self):
        return bool(self._undo)

    @property
    def can_redo(self):
        return bool(self._redo)

    # ---- edits (the caller does push() first)
    def touch(self, key):
        self.edited.add(key)

    def delete_selected(self):
        n = 0
        for key, items in self.lay.items():
            keep = [it for it in items if it["id"] not in self.sel]
            if len(keep) != len(items):
                n += len(items) - len(keep)
                self.edited.add(key)
                items[:] = keep
        self.sel = []
        return n

    def duplicate(self, ids, dx=0.0, dy=0.0):
        """Copies of the pictures (same sheet, on top).  Returns the new ids."""
        new = []
        for iid in ids:
            key, it = self.find(iid)
            if it is None:
                continue
            c = dict(it)
            self._n += 1
            c["id"] = self._n
            c["cx"] += dx
            c["cy"] += dy
            self.lay[key].append(c)
            self.edited.add(key)
            new.append(c["id"])
        return new

    def copy_selected(self):
        self.clip = [dict(it) for _k, it in self.selected()]
        return len(self.clip)

    def paste(self, key, dx=6.0, dy=6.0):
        """Puts the copied pictures on sheet key (a little aside).  Returns the new ids."""
        if not self.clip or key not in self.lay:
            return []
        new = []
        for it in self.clip:
            c = dict(it)
            self._n += 1
            c["id"] = self._n
            c["cx"] += dx
            c["cy"] += dy
            self.lay[key].append(c)
            new.append(c["id"])
        self.edited.add(key)
        self.sel = new
        return new

    def move_to_sheet(self, iid, key, cx, cy):
        """The picture goes to another sheet (on top), centre at (cx, cy) of that sheet."""
        old, it = self.find(iid)
        if it is None or key not in self.lay:
            return
        if old != key:
            self.lay[old].remove(it)
            self.lay[key].append(it)
            self.edited.add(old)
        it["cx"], it["cy"] = cx, cy
        self.edited.add(key)

    def center_selected(self, aw, ah):
        for key, it in self.selected():
            it["cx"], it["cy"] = aw / 2.0, ah / 2.0
            self.edited.add(key)

    def turn_selected(self, deg):
        for key, it in self.selected():
            it["rot"] = (it["rot"] + deg) % 360.0
            self.edited.add(key)

    def forget_unedited(self):
        """Paper / scale changed: sheets the user never touched get their normal look again."""
        for key in [k for k in self.lay if k not in self.edited]:
            del self.lay[key]
        self.sel = [i for i in self.sel if self.find(i)[1] is not None]


# ------------------------------------------------------------------------------------------------ PDF with page numbers
_TIMES_W = {"-": 0.333}                  # Times-Roman / Times-Bold: every digit is 0.5 em wide
_CAP = 0.676                             # height of a digit in em


def _text_width(text, size):
    return sum(_TIMES_W.get(ch, 0.5) for ch in text) * size


def save_pdf_numbered(sources, path, mode, spec, pdf_page=None, quality=95, progress=None, numbers=None):
    """Same PDF as core.save_pdf (one page per picture, JPEG, nothing resized) plus the page number written as real text
    (Times) at the paper's edge, so it stays sharp at any zoom.  sources = PIL images or functions that return one.
    numbers = the number of each page (default 1, 2, 3 ...).  pdf_page = core.pdf_page."""
    import io
    if pdf_page is None:
        import core
        pdf_page = core.pdf_page
    n = len(sources)
    if n == 0:
        raise ValueError("Nothing to save")
    numbers = list(numbers) if numbers is not None else list(range(1, n + 1))
    offsets = {}
    font_regular, font_bold = 3 + 3 * n, 4 + 3 * n
    with open(path, "wb") as f:
        f.write(b"%PDF-1.4\n%\xe2\xe3\xcf\xd3\n")

        def put(num, body):
            offsets[num] = f.tell()
            f.write(b"%d 0 obj\n" % num + body + b"\nendobj\n")

        for i, src in enumerate(sources):
            if progress:
                progress(i / n, "Page %d of %d" % (i + 1, n))
            im = src() if callable(src) else src
            im = flatten(im)
            w, h = im.size
            buf = io.BytesIO()
            im.save(buf, "JPEG", quality=quality, subsampling=0, optimize=True)
            data = buf.getvalue()
            pw, ph, x, y, dw, dh = pdf_page(w, h, mode)
            base = 3 + 3 * i
            text = ""
            if spec is not None and spec.on:
                label = spec.text(numbers[i] if i < len(numbers) else i + 1)
                m = spec.margin * 72.0 / 25.4
                tw = _text_width(label, spec.size)
                tx = m if spec.align == "left" else (pw - m - tw if spec.align == "right" else (pw - tw) / 2.0)
                ty = (ph - m - _CAP * spec.size) if spec.pos == "top" else m
                text = " BT 0 g /F%d %.2f Tf %.2f %.2f Td (%s) Tj ET" % (2 if spec.bold else 1, spec.size, tx, ty, label)
            put(base, ("<< /Type /Page /Parent 2 0 R /MediaBox [0 0 %.2f %.2f] /Resources << /XObject << /Im0 %d 0 R >> "
                       "/Font << /F1 %d 0 R /F2 %d 0 R >> /ProcSet [/PDF /Text /ImageC] >> /Contents %d 0 R >>"
                       % (pw, ph, base + 2, font_regular, font_bold, base + 1)).encode())
            content = ("q %.3f 0 0 %.3f %.3f %.3f cm /Im0 Do Q%s" % (dw, dh, x, y, text)).encode()
            put(base + 1, b"<< /Length %d >>\nstream\n" % len(content) + content + b"\nendstream")
            put(base + 2, b"<< /Type /XObject /Subtype /Image /Width %d /Height %d /ColorSpace /DeviceRGB "
                          b"/BitsPerComponent 8 /Filter /DCTDecode /Length %d >>\nstream\n" % (w, h, len(data))
                + data + b"\nendstream")
            del im, data, buf
        put(font_regular, b"<< /Type /Font /Subtype /Type1 /BaseFont /Times-Roman /Encoding /WinAnsiEncoding >>")
        put(font_bold, b"<< /Type /Font /Subtype /Type1 /BaseFont /Times-Bold /Encoding /WinAnsiEncoding >>")
        kids = " ".join("%d 0 R" % (3 + 3 * i) for i in range(n))
        put(2, ("<< /Type /Pages /Kids [%s] /Count %d >>" % (kids, n)).encode())
        put(1, b"<< /Type /Catalog /Pages 2 0 R >>")
        total = 5 + 3 * n
        xref = f.tell()
        f.write(b"xref\n0 %d\n0000000000 65535 f \n" % total)
        for num in range(1, total):
            f.write(b"%010d 00000 n \n" % offsets[num])
        f.write(b"trailer\n<< /Size %d /Root 1 0 R >>\nstartxref\n%d\n%%%%EOF\n" % (total, xref))
    if progress:
        progress(1.0, "Done")
