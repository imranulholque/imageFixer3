"""One consistent line-icon set, drawn with QPainter on a 24 x 24 grid (no image files, crisp at any size / DPI).

    icon("save")                      -> QIcon (normal / hover / checked / disabled colours built in)
    style_button(btn, "save", "Save", "Ctrl+S")   -> icon only + tooltip "Save   Ctrl+S"
"""
from PySide6.QtCore import QPointF, QRectF, QSize, Qt
from PySide6.QtGui import QColor, QIcon, QPainter, QPainterPath, QPen, QPixmap, QPolygonF

NORMAL, HOVER, ON, OFF, DANGER = "#c4cadb", "#ffffff", "#ffffff", "#565d70", "#ff8a98"


def _pts(*xy):
    return QPolygonF([QPointF(xy[i], xy[i + 1]) for i in range(0, len(xy), 2)])


def _line(p, *xy):
    p.drawPolyline(_pts(*xy))


def _rrect(p, x, y, w, h, r=2.5):
    p.drawRoundedRect(QRectF(x, y, w, h), r, r)


def _dot(p, x, y, r=1.1, fill=True):
    if fill:
        p.setBrush(p.pen().color())
    p.drawEllipse(QPointF(x, y), r, r)
    p.setBrush(Qt.BrushStyle.NoBrush)


def _arc(p, cx, cy, r, start, span):
    path = QPainterPath()
    path.arcMoveTo(QRectF(cx - r, cy - r, 2 * r, 2 * r), start)
    path.arcTo(QRectF(cx - r, cy - r, 2 * r, 2 * r), start, span)
    p.drawPath(path)


# ---- the glyphs: each one draws with the pen / brush already set -------------------------------------------------
def g_add(p): _line(p, 12, 5, 12, 19); _line(p, 5, 12, 19, 12)
def g_blank(p): _rrect(p, 5, 3, 14, 18); _line(p, 9, 8, 15, 8); _line(p, 9, 12, 15, 12)
def g_scan(p): _rrect(p, 3, 13, 18, 7); _line(p, 3, 10, 21, 10); _line(p, 6, 6, 18, 6); _dot(p, 17, 16.5)
def g_phone(p): _rrect(p, 7, 2.5, 10, 19, 2.5); _line(p, 10.5, 18.5, 13.5, 18.5)
def g_apply(p): _line(p, 4, 8, 20, 8); _line(p, 4, 16, 20, 16); _dot(p, 9, 8, 2.3, False); _dot(p, 15, 16, 2.3, False)
def g_save(p): _rrect(p, 4, 4, 16, 16, 2.5); _line(p, 8, 4, 8, 9, 15, 9, 15, 4); _line(p, 8, 20, 8, 14, 16, 14, 16, 20)
def g_saveall(p): _rrect(p, 3, 7, 14, 14, 2.5); _line(p, 7, 3, 20, 3, 21, 4, 21, 16); _line(p, 7, 12, 13, 12)
def g_pdf(p): _rrect(p, 5, 3, 14, 18); _line(p, 8.5, 15.5, 8.5, 11, 10.5, 11); _line(p, 13, 15.5, 13, 11); _line(p, 15.5, 15.5, 15.5, 11, 17, 11)
def g_folder(p): _line(p, 3, 7, 3, 19, 21, 19, 21, 9, 11, 9, 9, 6, 4, 6, 3, 7)
def g_open(p): _line(p, 14, 4, 20, 4, 20, 10); _line(p, 20, 4, 11, 13); _line(p, 17, 14, 17, 20, 4, 20, 4, 7, 10, 7)
def g_trash(p): _line(p, 4, 7, 20, 7); _line(p, 9, 7, 9, 4, 15, 4, 15, 7); _line(p, 6, 7, 7, 20, 17, 20, 18, 7); _line(p, 10, 11, 10, 16); _line(p, 14, 11, 14, 16)
def g_more(p): _dot(p, 5, 12, 1.5); _dot(p, 12, 12, 1.5); _dot(p, 19, 12, 1.5)
def g_help(p): p.drawEllipse(QPointF(12, 12), 9, 9); _arc(p, 12, 9.6, 2.6, 160, -240); _line(p, 12, 12.2, 12, 14); _dot(p, 12, 17, 0.9)
def g_gear(p):
    p.drawEllipse(QPointF(12, 12), 3.2, 3.2)
    import math
    for i in range(8):
        a = math.radians(i * 45)
        _line(p, 12 + 6.4 * math.cos(a), 12 + 6.4 * math.sin(a), 12 + 9 * math.cos(a), 12 + 9 * math.sin(a))
    p.drawEllipse(QPointF(12, 12), 6.4, 6.4)
def g_undo(p): _line(p, 9, 5, 4, 10, 9, 15); _line(p, 4, 10, 14, 10); _arc(p, 14, 15, 5, 90, -180)
def g_redo(p): _line(p, 15, 5, 20, 10, 15, 15); _line(p, 20, 10, 10, 10); _arc(p, 10, 15, 5, 90, 180)
def g_reset(p): _arc(p, 12, 12, 8, 120, 280); _line(p, 4, 4.5, 4.5, 9.5, 9.5, 9)
def g_compare(p): _rrect(p, 3, 5, 18, 14); _line(p, 12, 3, 12, 21); p.setBrush(p.pen().color()); p.drawRect(QRectF(5, 7, 5, 10)); p.setBrush(Qt.BrushStyle.NoBrush)
def g_eye(p): _arc(p, 12, 17, 9, 51, 78); _arc(p, 12, 7, 9, 231, 78); p.drawEllipse(QPointF(12, 12), 3, 3)
def g_upscale(p): _line(p, 14, 4, 20, 4, 20, 10); _line(p, 10, 20, 4, 20, 4, 14); _line(p, 20, 4, 13, 11); _line(p, 4, 20, 11, 13)
def g_close(p): _line(p, 6, 6, 18, 18); _line(p, 18, 6, 6, 18)
def g_panel(p): _rrect(p, 3, 4, 18, 16); _line(p, 15, 4, 15, 20)
def g_hand(p): _line(p, 8, 13, 8, 6.5); _line(p, 11, 12, 11, 4.5); _line(p, 14, 12, 14, 5.5); _line(p, 17, 13, 17, 8); _line(p, 8, 13, 5.5, 11, 4.5, 13, 8.5, 20, 16, 20, 17.5, 16, 17, 13)
def g_dropper(p): _line(p, 15, 4, 20, 9); _line(p, 17, 7, 9, 15); _line(p, 12, 6, 18, 12); _line(p, 9.5, 14.5, 5, 19, 5, 20, 6, 20, 10.5, 15.5)
def g_brush(p): _line(p, 20, 4, 11, 13); _line(p, 11, 13, 7, 14, 5, 17, 5, 20, 8.5, 20, 10, 18, 11, 13)
def g_select(p):
    pen = QPen(p.pen()); pen.setStyle(Qt.PenStyle.DashLine); pen.setDashPattern([2.2, 2.2]); p.setPen(pen)
    _rrect(p, 4, 4, 16, 16, 1)
def g_move(p):
    _line(p, 12, 3, 12, 21); _line(p, 3, 12, 21, 12); _line(p, 9, 6, 12, 3, 15, 6); _line(p, 9, 18, 12, 21, 15, 18)
    _line(p, 6, 9, 3, 12, 6, 15); _line(p, 18, 9, 21, 12, 18, 15)
def g_erase(p): _line(p, 4, 16, 13, 5, 20, 12, 12, 20, 8, 20, 4, 16); _line(p, 8, 9, 16, 17); _line(p, 12, 20, 21, 20)
def g_crop(p): _line(p, 7, 2.5, 7, 17, 21.5, 17); _line(p, 2.5, 7, 17, 7, 17, 21.5)
def g_layer(p): _rrect(p, 8, 8, 12, 12, 2); _line(p, 4, 15, 4, 4, 15, 4)
def g_text(p): _line(p, 5, 6, 5, 4, 19, 4, 19, 6); _line(p, 12, 4, 12, 20); _line(p, 9, 20, 15, 20)
def g_prev(p): _line(p, 15, 5, 8, 12, 15, 19)
def g_next(p): _line(p, 9, 5, 16, 12, 9, 19)
def g_pl(p): _line(p, 11, 6, 5, 12, 11, 18); _line(p, 5, 12, 20, 12)
def g_pr(p): _line(p, 13, 6, 19, 12, 13, 18); _line(p, 19, 12, 4, 12)
def g_drop(p): _line(p, 12, 3, 6.5, 11); _line(p, 12, 3, 17.5, 11); _arc(p, 12, 14, 6.2, 207, -234)
def g_sun(p):
    import math
    p.drawEllipse(QPointF(12, 12), 4, 4)
    for i in range(8):
        a = math.radians(i * 45)
        _line(p, 12 + 6.6 * math.cos(a), 12 + 6.6 * math.sin(a), 12 + 9 * math.cos(a), 12 + 9 * math.sin(a))
def g_sharp(p): _line(p, 12, 3, 14, 10, 21, 12, 14, 14, 12, 21, 10, 14, 3, 12, 10, 10, 12, 3)
def g_image(p): _rrect(p, 3, 4, 18, 16); _dot(p, 8.5, 9.5, 1.8, False); _line(p, 4, 18, 10, 12, 14, 16, 17, 13, 20, 16)
def g_aa(p): _line(p, 2.5, 19, 7.5, 5, 12.5, 19); _line(p, 4.4, 14.5, 10.6, 14.5); p.drawEllipse(QPointF(17.5, 15.2), 3.3, 3.3); _line(p, 20.8, 11.5, 20.8, 19)
def g_layers(p): _line(p, 12, 3, 21, 8, 12, 13, 3, 8, 12, 3); _line(p, 3, 12.5, 12, 17.5, 21, 12.5); _line(p, 3, 16.5, 12, 21.5, 21, 16.5)
def g_border(p): _rrect(p, 4, 4, 16, 16, 3); _rrect(p, 8, 8, 8, 8, 1)
def g_group(p): _rrect(p, 3, 3, 9, 9, 2); _rrect(p, 12, 12, 9, 9, 2); pen = QPen(p.pen()); pen.setStyle(Qt.PenStyle.DashLine); pen.setDashPattern([1.8, 1.8]); p.setPen(pen); _rrect(p, 1.5, 1.5, 21, 21, 3)
def g_ungroup(p): _rrect(p, 2.5, 2.5, 9, 9, 2); _rrect(p, 12.5, 12.5, 9, 9, 2); _line(p, 15, 4, 20, 9); _line(p, 20, 4, 15, 9)
def g_check(p): _line(p, 5, 12.5, 10, 17.5, 19, 7)
def g_keys(p): _rrect(p, 2.5, 6, 19, 12, 2.5); [_dot(p, x, 10, 0.8) for x in (7, 11, 15, 19)]; _line(p, 7, 14.5, 17, 14.5)


GLYPHS = {k[2:]: v for k, v in list(globals().items()) if k.startswith("g_") and callable(v)}
GLYPHS.update({"adjust": g_apply, "color": g_drop, "shadow": g_sun, "textfix": g_sharp, "picture": g_image,
               "files": g_add, "phone": g_phone, "pageL": g_pl, "pageR": g_pr, "settings": g_gear, "shortcuts": g_keys})


def _pixmap(name, color, size, caret, dpr=2.0):
    px = int(size * dpr)
    pm = QPixmap(px, px)
    pm.fill(Qt.GlobalColor.transparent)
    p = QPainter(pm)
    p.setRenderHint(QPainter.RenderHint.Antialiasing, True)
    p.scale(px / 24.0, px / 24.0)
    pen = QPen(QColor(color), 1.7, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap, Qt.PenJoinStyle.RoundJoin)
    p.setPen(pen)
    p.setBrush(Qt.BrushStyle.NoBrush)
    if caret:                                   # a tiny arrow in the corner = "this opens a menu"
        p.save()
        p.translate(-2.2, -2.2)
        p.scale(0.86, 0.86)
        GLYPHS.get(name, g_more)(p)
        p.restore()
        p.setPen(QPen(QColor(color), 1.5, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap, Qt.PenJoinStyle.RoundJoin))
        _line(p, 17.3, 19, 20, 21.6, 22.7, 19)
    else:
        GLYPHS.get(name, g_more)(p)
    p.end()
    pm.setDevicePixelRatio(dpr)
    return pm


# every feature family has its own colour, so the eye finds a tool without reading
COLORS = {
    "add": "#a5b4fc", "files": "#a5b4fc", "blank": "#60a5fa", "scan": "#22d3ee", "phone": "#34d399", "apply": "#a3e635",
    "save": "#4ade80", "saveall": "#4ade80", "pdf": "#f87171", "folder": "#fbbf24", "open": "#fcd34d", "trash": "#fb7185",
    "more": "#94a3b8", "help": "#38bdf8", "settings": "#94a3b8", "gear": "#94a3b8", "shortcuts": "#38bdf8",
    "undo": "#a78bfa", "redo": "#a78bfa", "reset": "#fb923c", "compare": "#22d3ee", "eye": "#38bdf8", "upscale": "#c084fc",
    "close": "#fb7185", "panel": "#94a3b8", "hand": "#fcd34d", "dropper": "#2dd4bf", "brush": "#f472b6",
    "select": "#60a5fa", "move": "#a78bfa", "erase": "#fb923c", "crop": "#4ade80", "layer": "#c084fc", "group": "#2dd4bf", "ungroup": "#fb923c", "text": "#fbbf24",
    "prev": "#a5b4fc", "next": "#a5b4fc", "pageL": "#94a3b8", "pageR": "#94a3b8", "check": "#4ade80",
    "adjust": "#818cf8", "color": "#f472b6", "shadow": "#fbbf24", "textfix": "#34d399", "picture": "#38bdf8",
    "aa": "#fb923c", "layers": "#c084fc", "border": "#2dd4bf",
}


def color_of(name):
    return COLORS.get(name, NORMAL)


def _lighter(hexcolor, f=0.45):
    c = QColor(hexcolor)
    return QColor(int(c.red() + (255 - c.red()) * f), int(c.green() + (255 - c.green()) * f),
                  int(c.blue() + (255 - c.blue()) * f)).name()


_CACHE = {}


def icon(name, size=20, caret=False, normal=None):
    normal = normal or color_of(name)
    key = (name, size, caret, normal)
    ic = _CACHE.get(key)
    if ic is None:
        ic = QIcon()
        ic.addPixmap(_pixmap(name, normal, size, caret), QIcon.Mode.Normal, QIcon.State.Off)
        ic.addPixmap(_pixmap(name, _lighter(normal) if normal != '#ffffff' else HOVER, size, caret), QIcon.Mode.Active, QIcon.State.Off)
        ic.addPixmap(_pixmap(name, ON, size, caret), QIcon.Mode.Normal, QIcon.State.On)
        ic.addPixmap(_pixmap(name, ON, size, caret), QIcon.Mode.Active, QIcon.State.On)
        ic.addPixmap(_pixmap(name, OFF, size, caret), QIcon.Mode.Disabled, QIcon.State.Off)
        ic.addPixmap(_pixmap(name, OFF, size, caret), QIcon.Mode.Disabled, QIcon.State.On)
        _CACHE[key] = ic
    return ic


def tip(text, keys=""):
    return text + ("      " + keys if keys else "")


def style_button(btn, name, text, keys="", size=20, label=None, caret=False, extra_tip=""):
    """Icon button.  `label` (optional) keeps a short text next to the icon; otherwise it is icon only.
    The full name + shortcut always sit in the tooltip."""
    btn.setIcon(icon(name, size, caret, normal="#ffffff" if btn.property("accent") or btn.property("good") else None))
    btn.setIconSize(QSize(size, size))
    t = tip(text, keys)
    if extra_tip:
        t += "\n" + extra_tip
    btn.setToolTip(t)
    btn.setAccessibleName(text)
    if label is None:
        btn.setText("")
        btn.setProperty("ico", True)
    else:
        btn.setText(label)
    btn.style().unpolish(btn)
    btn.style().polish(btn)
    return btn
