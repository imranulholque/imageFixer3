"""ID card from a flatbed scan: find the card on the glass, cut it out, straighten it, and put FRONT and BACK together
on one page (every card = its own layer, real size).

A flatbed scan is the easy case: the background is the scanner lid (one colour, white or black) and the card is a rectangle
of a known real size (about 85.6 x 54 mm; the scan DPI turns that into pixels).  So the card is looked for as a rectangle of
about that size, in two ways (colour difference from the lid, and edges - a white card on a white lid only has edges).
Nothing here needs the window: it works on PIL pictures, so it can be tested without a scanner."""
import math

import cv2
import numpy as np
from PIL import Image, ImageDraw

CARD_ASPECT = 85.6 / 54.0                 # ID-1 (bank card, NID, driving licence)
CORNER_MM = 3.18                          # corner radius of an ID-1 card (ISO 7810)
ROUND_PCT = 100                           # default corner roundness: 100 = the real card (ISO 7810), 0 = square, 400 = very round
ROUND_MAX = 400
# page sizes in millimetres
PAGES_MM = {"A4": (210.0, 297.0), "A5": (148.0, 210.0), "Letter": (215.9, 279.4), "Legal": (215.9, 355.6)}


def _rect_score(cnt, rect, scale, dpi, strict):
    (cx, cy), (w, h), ang = rect
    if w < 8 or h < 8:
        return 0.0
    area = cv2.contourArea(cnt)
    fill = area / float(w * h)
    if fill < 0.80:
        return 0.0
    long_, short = max(w, h), min(w, h)
    asp = long_ / short
    if strict:
        inch = long_ / scale / float(dpi)                 # the long side in real inches (an ID card is 3.37")
        if not (2.0 <= inch <= 5.4) or not (1.25 <= asp <= 2.0):
            return 0.0
        return fill * math.exp(-abs(math.log(asp / CARD_ASPECT)) * 2.5) * (0.5 + min(1.0, long_ * short / 90000.0))
    return fill * min(1.0, w * h / 60000.0)


def _contours(mask, min_area):
    mask = cv2.morphologyEx(mask, cv2.MORPH_CLOSE, cv2.getStructuringElement(cv2.MORPH_RECT, (7, 7)))
    mask = cv2.morphologyEx(mask, cv2.MORPH_OPEN, cv2.getStructuringElement(cv2.MORPH_RECT, (3, 3)))
    cs, _ = cv2.findContours(mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    cs = [c for c in cs if cv2.contourArea(c) >= min_area]
    cs.sort(key=cv2.contourArea, reverse=True)
    return cs[:6]


def _masks(small):
    """Yields masks (uint8 0/255) that may contain the card: colour difference from the lid, then closed edges."""
    h, w = small.shape[:2]
    k = max(2, int(min(h, w) * 0.02))
    ring = np.concatenate([small[:k].reshape(-1, 3), small[-k:].reshape(-1, 3),
                           small[:, :k].reshape(-1, 3), small[:, -k:].reshape(-1, 3)])
    bg = np.median(ring, axis=0)
    blur = cv2.GaussianBlur(small, (5, 5), 0)
    dist = np.abs(blur.astype(np.float32) - bg.astype(np.float32)).max(axis=2)
    d8 = np.clip(dist * 2.5, 0, 255).astype(np.uint8)
    t, m = cv2.threshold(d8, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)
    if t < 30:                                            # nothing clearly different: a faint card, use a gentle limit
        _, m = cv2.threshold(d8, 30, 255, cv2.THRESH_BINARY)
    yield m
    _, m2 = cv2.threshold(d8, 22, 255, cv2.THRESH_BINARY)         # a lower limit: pale cards / pale lids
    yield m2
    gray = cv2.cvtColor(blur, cv2.COLOR_RGB2GRAY)
    edges = cv2.Canny(gray, 25, 80)
    edges = cv2.dilate(edges, cv2.getStructuringElement(cv2.MORPH_RECT, (3, 3)))
    cs, _ = cv2.findContours(edges, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    filled = np.zeros_like(edges)
    cv2.drawContours(filled, cs, -1, 255, thickness=cv2.FILLED)
    yield filled


def find_card_box(img, dpi=300):
    """-> 4 corner points (full-size pixel coordinates, float32 4x2) of the card, or None."""
    rgb = np.asarray(img.convert("RGB"))
    H, W = rgb.shape[:2]
    scale = min(1.0, 1000.0 / max(H, W))
    small = cv2.resize(rgb, (max(8, int(W * scale)), max(8, int(H * scale))), interpolation=cv2.INTER_AREA) if scale < 1 else rgb
    min_area = small.shape[0] * small.shape[1] * 0.004
    for strict in (True, False):
        best, best_s = None, 0.0
        for m in _masks(small):
            for c in _contours(m, min_area):
                rect = cv2.minAreaRect(c)
                s = _rect_score(c, rect, scale, dpi, strict)
                if s > best_s:
                    best, best_s = rect, s
        if best is not None:
            (cx, cy), (w, h), ang = best
            pts = cv2.boxPoints(((cx / scale, cy / scale), (w / scale, h / scale), ang))
            return pts.astype(np.float32)
    return None


def _order(pts):
    """top-left, top-right, bottom-right, bottom-left"""
    pts = np.asarray(pts, np.float32)
    s = pts.sum(axis=1)
    d = np.diff(pts, axis=1).reshape(-1)
    return np.array([pts[np.argmin(s)], pts[np.argmin(d)], pts[np.argmax(s)], pts[np.argmax(d)]], np.float32)


def _edge_pos(P, lo, hi, default, floor=9.0):
    """P = (n, 3) mean colour along one line across an edge.  The place in [lo, hi) where the colour changes most = the card
    edge.  No clear change: `default`."""
    n = len(P)
    lo, hi = max(1, int(lo)), min(n - 1, int(hi))
    if hi - lo < 3:
        return default
    g = np.abs(P[2:] - P[:-2]).sum(axis=1)               # g[i-1] = change around i
    g = np.convolve(g, np.ones(3) / 3.0, mode="same")
    seg = g[lo - 1:hi - 1]
    if seg.size == 0 or seg.max() < floor:
        return default
    return lo + int(np.argmax(seg))


def already_card_sized(img, dpi):
    """True when the scan is itself about card sized (2 - 5.6 inch long side, card-like shape): many scanners detect the
    paper size and give only the card, so there is nothing to cut."""
    long_, short = max(img.size), min(img.size)
    inch = long_ / float(max(dpi, 1))
    return 2.0 <= inch <= 5.6 and 1.25 <= long_ / float(short) <= 2.0


def cut_card(img, dpi=300, keep_orientation=False):
    """Scan (PIL) -> (card picture, found).  The card is straightened; its four edges are then found again at full
    resolution (the first box is a little too big), and it is cut a hair inside its edge (no lid / shadow line).
    Not found: the whole scan comes back with found = False."""
    if already_card_sized(img, dpi):                       # the scanner itself cut out only the card (auto paper size)
        card = img.convert("RGB")
        if card.height > card.width and not keep_orientation:
            card = card.rotate(90, expand=True)
        return card, True
    pts = find_card_box(img, dpi)
    if pts is None:
        return img.convert("RGB"), False
    q = _order(pts)
    w = float((np.linalg.norm(q[1] - q[0]) + np.linalg.norm(q[2] - q[3])) / 2)
    h = float((np.linalg.norm(q[3] - q[0]) + np.linalg.norm(q[2] - q[1])) / 2)
    if w < 40 or h < 40:
        return img.convert("RGB"), False
    m = max(12, int(dpi * 0.12))                           # look this far outside / inside the first box
    pw, ph = int(round(w)) + 2 * m, int(round(h)) + 2 * m
    dst = np.array([[m, m], [m + w - 1, m], [m + w - 1, m + h - 1], [m, m + h - 1]], np.float32)
    M = cv2.getPerspectiveTransform(q, dst)
    rgb = np.asarray(img.convert("RGB"))
    patch = cv2.warpPerspective(rgb, M, (pw, ph), flags=cv2.INTER_CUBIC, borderMode=cv2.BORDER_REPLICATE).astype(np.float32)
    iw, ih = int(round(w)), int(round(h))
    ya, yb = m + int(ih * 0.2), m + int(ih * 0.8)          # stay away from the rounded corners
    xa, xb = m + int(iw * 0.2), m + int(iw * 0.8)
    colsP = patch[ya:yb].mean(axis=0)                      # colour along x  (left / right edges)
    rowsP = patch[:, xa:xb].mean(axis=1)                   # colour along y  (top / bottom edges)
    xl = _edge_pos(colsP, 0, 2 * m, m)
    xr = _edge_pos(colsP, iw, iw + 2 * m, m + iw)
    yt = _edge_pos(rowsP, 0, 2 * m, m)
    yb2 = _edge_pos(rowsP, ih, ih + 2 * m, m + ih)
    inset = max(2, int(round(dpi * 0.010)))                # about 0.25 mm inside the edge
    x0, x1, y0, y1 = xl + inset, xr - inset, yt + inset, yb2 - inset
    if x1 - x0 < 40 or y1 - y0 < 40:
        x0, x1, y0, y1 = m + inset, m + iw - inset, m + inset, m + ih - inset
    card = Image.fromarray(np.clip(patch[y0:y1, x0:x1], 0, 255).astype(np.uint8))
    if not keep_orientation and card.height > card.width:  # a card is shown lying down (turn with R if it is upside down)
        card = card.rotate(90, expand=True)
    return card, True


def corner_radius(size, pct=ROUND_PCT, extra=1.06):
    """Corner radius in pixels of a card picture of this size.  pct = 100 is the real ID-1 radius (a hair bigger: no dark
    crumbs left), 0 = no rounding (0.0), 200 = twice as round ..."""
    try:
        pct = max(0.0, min(float(ROUND_MAX), float(pct)))
    except Exception:
        pct = float(ROUND_PCT)
    if pct <= 0:
        return 0.0
    w, h = size
    r = CORNER_MM / 85.6 * max(w, h) * extra * pct / 100.0
    return max(2.0, min(r, min(w, h) / 2.0))


def round_alpha(size, pct=ROUND_PCT):
    """The transparency (PIL 'L') of a card of this size with its corners cut round (smooth, anti-aliased).
    pct = 0: nothing is cut (all 255)."""
    w, h = int(size[0]), int(size[1])
    r = corner_radius((w, h), pct)
    if r <= 0:
        return Image.new("L", (w, h), 255)
    S = 4                                                # drawn 4 times bigger, then reduced = smooth edge
    m = Image.new("L", (w * S, h * S), 0)
    ImageDraw.Draw(m).rounded_rectangle((0, 0, w * S - 1, h * S - 1), radius=r * S, fill=255)
    return m.resize((w, h), Image.Resampling.LANCZOS)


def rounded_card(card, extra=1.06, pct=ROUND_PCT):
    """The card as RGBA with the real rounded corners: the four corners of a rectangular cut are scanner lid / shadow /
    smeared edge, they become transparent (smooth, anti-aliased) so the card looks like the real card on the page.
    pct = how round (100 = real card, 0 = square corners, bigger = rounder)."""
    card = card.convert("RGB")
    out = card.convert("RGBA")
    out.putalpha(round_alpha(card.size, pct))
    return out


def page_pixels(page, dpi):
    mm = PAGES_MM.get(page, PAGES_MM["A4"])
    return int(round(mm[0] / 25.4 * dpi)), int(round(mm[1] / 25.4 * dpi))


def compose_page(front, back, dpi=300, page="A4", place="center", gap_mm=8.0, rounded=True, round_pct=ROUND_PCT,
                 page_color=(255, 255, 255)):
    """FRONT above BACK, real size, centred on a page of one solid colour (page_color: white, black, any colour).  rounded = the corners are cut round like a real card
    (transparent corners; every picture is then RGBA).  round_pct = how round (100 = real card, 0 = square, 400 = very round).
    -> (page picture, [(name, picture, (x0, y0, x1, y1) as fractions of the page), ...])"""
    dpi = max(150, int(dpi))
    PW, PH = page_pixels(page, dpi)
    cards = [("Front", front.convert("RGB"))]
    if back is not None:
        cards.append(("Back", back.convert("RGB")))
    # both sides of one card are the same size: a few pixels of difference are crop noise, make them equal
    if len(cards) == 2:
        a, b = cards[0][1], cards[1][1]
        if abs(a.width - b.width) <= 0.06 * a.width and abs(a.height - b.height) <= 0.06 * a.height:
            cards[1] = (cards[1][0], b.resize(a.size, Image.Resampling.LANCZOS))
    maxw = PW * 0.90
    f = min(1.0, maxw / max(c.width for _, c in cards))
    if f < 1.0:
        cards = [(n, c.resize((max(1, int(c.width * f)), max(1, int(c.height * f))), Image.Resampling.LANCZOS)) for n, c in cards]
    if rounded:
        cards = [(n, rounded_card(c, pct=round_pct)) for n, c in cards]
    gap = int(round(gap_mm / 25.4 * dpi))
    total = sum(c.height for _, c in cards) + gap * (len(cards) - 1)
    if place == "top":
        y = int(round(15.0 / 25.4 * dpi))
    else:
        y = max(0, (PH - total) // 2)
    items = []
    for name, c in cards:
        x = (PW - c.width) // 2
        items.append((name, c, (x / PW, y / PH, (x + c.width) / PW, (y + c.height) / PH)))
        y += c.height + gap
    try:
        pc = tuple(max(0, min(255, int(v))) for v in page_color)[:3]
        if len(pc) != 3:
            raise ValueError
    except Exception:
        pc = (255, 255, 255)
    return Image.new("RGB", (PW, PH), pc), items


def paste_preview(page_img, items):
    """What the finished page looks like (for tests / a quick look)."""
    out = page_img.copy()
    W, H = out.size
    for _, c, (x0, y0, x1, y1) in items:
        out.paste(c, (int(round(x0 * W)), int(round(y0 * H))), c if c.mode == "RGBA" else None)
    return out


# ------------------------------------------------------------------------------------------ corner crop (by hand)
def initial_quad(img, dpi=300):
    """-> (4 corner points TL, TR, BR, BL of the card in the scan as float32 4x2, found).  These are the corners the
    corner-crop window starts with: what the card finder saw; the whole picture when the scanner itself gave only the
    card; the whole picture (found = False) when nothing was seen."""
    W, H = img.size
    whole = np.array([[0, 0], [W - 1, 0], [W - 1, H - 1], [0, H - 1]], np.float32)
    if already_card_sized(img, dpi):
        return whole, True
    pts = find_card_box(img, dpi)
    if pts is None:
        return whole, False
    return _order(pts), True


def cut_quad(img, quad, keep_orientation=False):
    """The area inside 4 corners (picture pixels, any order) becomes a straight card: a flatbed has no perspective, so
    the card's own edge lengths are its size.  A card standing up is turned to lie down (like cut_card)."""
    q = _order(quad)
    w = float((np.linalg.norm(q[1] - q[0]) + np.linalg.norm(q[2] - q[3])) / 2)
    h = float((np.linalg.norm(q[3] - q[0]) + np.linalg.norm(q[2] - q[1])) / 2)
    w, h = max(40, int(round(w))), max(40, int(round(h)))
    dst = np.array([[0, 0], [w - 1, 0], [w - 1, h - 1], [0, h - 1]], np.float32)
    M = cv2.getPerspectiveTransform(q, dst)
    rgb = np.asarray(img.convert("RGB"))
    out = cv2.warpPerspective(rgb, M, (w, h), flags=cv2.INTER_CUBIC, borderMode=cv2.BORDER_REPLICATE)
    card = Image.fromarray(out)
    if not keep_orientation and card.height > card.width:
        card = card.rotate(90, expand=True)
    return card


# ------------------------------------------------------------------------------------------ border + preview
BORDER_COLOR = "#878787"                  # the grey frame: a white card is seen on the white page
BORDER_W = 6                              # thickness: 6 = 0.6 % of the card's long side (1 .. 40)


def make_border(on=True, color=BORDER_COLOR, width=BORDER_W):
    """Settings -> the 'border' of a layer: ((r, g, b), thickness as a fraction of the long side), or None (no border)."""
    if not on:
        return None
    try:
        c = str(color).lstrip("#")
        rgb = (int(c[0:2], 16), int(c[2:4], 16), int(c[4:6], 16))
    except Exception:
        rgb = (135, 135, 135)
    try:
        w = max(1, min(40, int(width)))
    except Exception:
        w = BORDER_W
    return rgb, w / 1000.0


def preview_image(sides, border=None, width=360, bg=(226, 230, 238), pad=14, round_pct=ROUND_PCT):
    """How the cards will look on the page (round corners + border), side by side, small.
    sides = [(card picture, found), ...] -> PIL RGB.  round_pct as in rounded_card (0 = square corners)."""
    import core
    tiles = []
    for card, found in sides:
        c = card.convert("RGB")
        if c.width != width:
            c = c.resize((width, max(1, int(round(c.height * width / float(c.width))))), Image.Resampling.LANCZOS)
        t = rounded_card(c, pct=round_pct) if found else c.convert("RGBA")
        if border:
            try:
                t = core._apply_border(t, border)
            except Exception:
                pass
        tiles.append(t)
    W = sum(t.width for t in tiles) + pad * (len(tiles) + 1)
    H = max(t.height for t in tiles) + 2 * pad
    out = Image.new("RGB", (W, H), bg)
    x = pad
    for t in tiles:
        out.paste(t, (x, (H - t.height) // 2), t)
        x += t.width + pad
    return out
