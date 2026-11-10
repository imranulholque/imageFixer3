"""Visual feedback: every action shows that it really happened.

  Toast        pill at the bottom of the picture: spinner while a job runs -> ring closes + tick when it is done
  KeyChip      the pressed shortcut (Ctrl + Z ...) and what it did, at the top of the picture
  Effects      what lives on one picture: pulse / sweep / wash / ring effects on the changed area, the little
               chip that follows the mouse while dragging (size, distance, angle ...), the zoom badge
  Ripple       a soft wave on every button you press
  DropOverlay  the window dims and shows a drop zone while files are dragged over it
"""
import math
import time

from PySide6.QtCore import QEvent, QObject, QPointF, QRectF, Qt, QTimer
from PySide6.QtGui import (QBrush, QColor, QCursor, QFont, QFontMetrics, QLinearGradient, QPainter, QPainterPath,
                           QPen, QPixmap, QPolygonF)
from PySide6.QtWidgets import QPushButton, QToolButton, QWidget

# False = no text messages for every change (toast pill, shortcut chip).  A light animation on the picture shows that
# the work was done instead.  Warnings and errors are still shown as text.  Set to True for the old behaviour.
SHOW_MESSAGES = False
_NO_ANIM = {"tool", "view", "zoom", "info", "warn", "fail", "compare", "undo", "redo"}   # nothing changed on the picture

COLORS = {
    "copy": "#a78bfa", "move": "#60a5fa", "resize": "#818cf8", "rotate": "#e879f9", "erase": "#fb7185",
    "delete": "#f87171", "fill": "#fbbf24", "paint": "#f472b6", "select": "#22d3ee", "crop": "#fbbf24",
    "undo": "#94a3b8", "redo": "#94a3b8", "tool": "#818cf8", "save": "#34d399", "shadow": "#fcd34d",
    "zoom": "#94a3b8", "info": "#818cf8", "warn": "#fbbf24", "fail": "#f87171", "ok": "#34d399",
    "pick": "#2dd4bf", "layer": "#38bdf8", "clean": "#a5b4fc", "adjust": "#818cf8", "drop": "#818cf8",
    "view": "#94a3b8", "compare": "#38bdf8",
}


def _clamp(v, lo=0.0, hi=1.0):
    return lo if v < lo else hi if v > hi else v


def out_cubic(t):
    t = _clamp(t)
    return 1.0 - (1.0 - t) ** 3


def out_back(t, s=1.9):
    t = _clamp(t) - 1.0
    return 1.0 + t * t * ((s + 1.0) * t + s)


def kc(kind):
    return QColor(COLORS.get(kind, COLORS["info"]))


def _a(color, alpha):
    c = QColor(color)
    c.setAlpha(int(_clamp(alpha, 0, 255)))
    return c


# ------------------------------------------------------------------------------------------------ icons
def _pl(p, pts):
    p.drawPolyline(QPolygonF([QPointF(x, y) for x, y in pts]))


def _pg(p, pts):
    p.drawPolygon(QPolygonF([QPointF(x, y) for x, y in pts]))


def _ln(p, x1, y1, x2, y2):
    p.drawLine(QPointF(x1, y1), QPointF(x2, y2))


def _head(p, x, y, dx, dy, size=5.0):
    """Arrow head with its tip at (x, y), pointing in direction (dx, dy)."""
    d = math.hypot(dx, dy) or 1.0
    dx, dy = dx / d, dy / d
    for sgn in (1, -1):
        a = math.radians(40.0 * sgn)
        ca, sa = math.cos(a), math.sin(a)
        bx, by = -(dx * ca - dy * sa), -(dx * sa + dy * ca)
        p.drawLine(QPointF(x, y), QPointF(x + bx * size, y + by * size))


def _ic_copy(p, c):
    p.drawRoundedRect(QRectF(9, 9, 11, 11), 2.5, 2.5)
    _pl(p, [(5, 15), (5, 6.5), (6.5, 5), (15, 5)])


def _ic_move(p, c):
    _ln(p, 12, 3, 12, 21)
    _ln(p, 3, 12, 21, 12)
    _head(p, 12, 3, 0, -1)
    _head(p, 12, 21, 0, 1)
    _head(p, 3, 12, -1, 0)
    _head(p, 21, 12, 1, 0)


def _ic_resize(p, c):
    _ln(p, 6, 18, 18, 6)
    _head(p, 18, 6, 1, -1, 6)
    _head(p, 6, 18, -1, 1, 6)


def _ic_rotate(p, c):
    p.drawArc(QRectF(5, 5, 14, 14), 110 * 16, 250 * 16)
    _head(p, 19, 12, 0, -1, 5)


def _ic_erase(p, c):
    p.save()
    p.translate(11, 11)
    p.rotate(-45)
    p.drawRoundedRect(QRectF(-8, -4.5, 16, 9), 2, 2)
    _ln(p, -1.5, -4.5, -1.5, 4.5)
    p.restore()
    _ln(p, 12, 20.5, 21, 20.5)


def _ic_delete(p, c):
    _ln(p, 4.5, 7, 19.5, 7)
    _pl(p, [(9.5, 7), (9.5, 4), (14.5, 4), (14.5, 7)])
    _pl(p, [(6.5, 7), (7.5, 20), (16.5, 20), (17.5, 7)])
    _ln(p, 10.5, 11, 10.5, 16)
    _ln(p, 13.5, 11, 13.5, 16)


def _ic_fill(p, c):
    path = QPainterPath()
    path.moveTo(12, 3)
    path.cubicTo(15, 7, 19, 11, 19, 15)
    path.cubicTo(19, 19, 16, 21, 12, 21)
    path.cubicTo(8, 21, 5, 19, 5, 15)
    path.cubicTo(5, 11, 9, 7, 12, 3)
    p.setBrush(_a(c, 70))
    p.drawPath(path)


def _ic_paint(p, c):
    pen = p.pen()
    pen.setWidthF(4.4)
    p.setPen(pen)
    _ln(p, 10, 14, 18, 6)
    pen.setWidthF(2.3)
    p.setPen(pen)
    p.setBrush(_a(c, 255))
    p.drawEllipse(QPointF(7, 17), 3, 3)


def _ic_select(p, c):
    pen = p.pen()
    pen.setDashPattern([2.2, 2.2])
    p.setPen(pen)
    p.drawRect(QRectF(4, 5, 16, 14))


def _ic_crop(p, c):
    _pl(p, [(7, 3), (7, 17), (21, 17)])
    _pl(p, [(3, 7), (17, 7), (17, 21)])


def _ic_undo(p, c):
    path = QPainterPath()
    path.moveTo(5, 10)
    path.lineTo(14, 10)
    path.cubicTo(20, 10, 20, 19, 14, 19)
    path.lineTo(9, 19)
    p.drawPath(path)
    _pl(p, [(9, 5), (4, 10), (9, 15)])


def _ic_redo(p, c):
    p.translate(24, 0)
    p.scale(-1, 1)
    _ic_undo(p, c)


def _ic_tool(p, c):
    p.setBrush(_a(c, 80))
    _pg(p, [(6, 3), (6, 19), (10.5, 15), (14, 21), (16.5, 20), (13, 14), (19, 14)])


def _ic_check(p, c):
    _pl(p, [(5, 12.5), (10, 17.5), (19, 7)])


def _ic_warn(p, c):
    _pg(p, [(12, 4), (21, 20), (3, 20)])
    _ln(p, 12, 10, 12, 14)
    _ln(p, 12, 17, 12, 17.2)


def _ic_info(p, c):
    p.drawEllipse(QPointF(12, 12), 9, 9)
    _ln(p, 12, 11, 12, 16.5)
    _ln(p, 12, 7.5, 12, 7.7)


def _ic_fail(p, c):
    _ln(p, 7, 7, 17, 17)
    _ln(p, 17, 7, 7, 17)


def _ic_pick(p, c):
    pen = p.pen()
    pen.setWidthF(2.6)
    p.setPen(pen)
    _ln(p, 7, 17, 14, 10)
    p.setBrush(_a(c, 255))
    p.drawEllipse(QPointF(16.5, 7.5), 3.2, 3.2)
    pen.setWidthF(1.6)
    p.setPen(pen)
    _ln(p, 5, 19, 7, 17)


def _ic_shadow(p, c):
    p.drawEllipse(QPointF(12, 12), 4, 4)
    for i in range(8):
        a = math.radians(i * 45.0)
        _ln(p, 12 + math.cos(a) * 7, 12 + math.sin(a) * 7, 12 + math.cos(a) * 10, 12 + math.sin(a) * 10)


def _ic_zoom(p, c):
    p.drawEllipse(QPointF(10.5, 10.5), 6.5, 6.5)
    _ln(p, 15.5, 15.5, 20.5, 20.5)


def _ic_layer(p, c):
    p.drawRoundedRect(QRectF(4, 5, 16, 14), 2.5, 2.5)
    _pl(p, [(4.5, 17), (10, 11.5), (14, 15.5), (16.5, 13), (19.5, 16)])
    p.drawEllipse(QPointF(9, 9.5), 1.4, 1.4)


def _ic_clean(p, c):
    p.setBrush(_a(c, 60))
    _pg(p, [(12, 3), (14.2, 9.8), (21, 12), (14.2, 14.2), (12, 21), (9.8, 14.2), (3, 12), (9.8, 9.8)])


def _ic_drop(p, c):
    _ln(p, 12, 4, 12, 15)
    _head(p, 12, 15, 0, 1, 6)
    _pl(p, [(5, 15), (5, 20), (19, 20), (19, 15)])


def _ic_save(p, c):
    p.drawRoundedRect(QRectF(4, 4, 16, 16), 2.5, 2.5)
    p.drawRect(QRectF(8, 4, 7, 5))
    p.drawRect(QRectF(7, 13, 10, 7))


def _ic_compare(p, c):
    p.drawRoundedRect(QRectF(4, 5, 16, 14), 2.5, 2.5)
    _ln(p, 12, 5, 12, 19)
    p.setBrush(_a(c, 90))
    p.drawRect(QRectF(4.8, 5.8, 6.4, 12.4))


def _ic_adjust(p, c):
    for y, x in ((7, 9), (12, 15), (17, 8)):
        _ln(p, 4, y, 20, y)
        p.setBrush(QColor("#151a26"))
        p.drawEllipse(QPointF(x, y), 2.4, 2.4)


def _ic_dot(p, c):
    p.drawEllipse(QPointF(12, 12), 4, 4)


_ICONS = {
    "copy": _ic_copy, "move": _ic_move, "resize": _ic_resize, "rotate": _ic_rotate, "erase": _ic_erase,
    "delete": _ic_delete, "fill": _ic_fill, "paint": _ic_paint, "select": _ic_select, "crop": _ic_crop,
    "undo": _ic_undo, "redo": _ic_redo, "tool": _ic_tool, "ok": _ic_check, "warn": _ic_warn, "info": _ic_info,
    "fail": _ic_fail, "pick": _ic_pick, "shadow": _ic_shadow, "zoom": _ic_zoom, "layer": _ic_layer,
    "clean": _ic_clean, "drop": _ic_drop, "save": _ic_save, "compare": _ic_compare, "adjust": _ic_adjust,
    "view": _ic_move,
}


def draw_icon(p, name, cx, cy, size, color, width=2.3):
    """Vector icon of the given size, centred at (cx, cy)."""
    p.save()
    s = size / 24.0
    p.translate(cx - size / 2.0, cy - size / 2.0)
    p.scale(s, s)
    p.setPen(QPen(QColor(color), width, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap, Qt.PenJoinStyle.RoundJoin))
    p.setBrush(Qt.BrushStyle.NoBrush)
    _ICONS.get(name, _ic_dot)(p, QColor(color))
    p.restore()


def _font(base, px, bold=False):
    f = QFont(base)
    f.setPixelSize(px)
    f.setWeight(QFont.Weight.DemiBold if bold else QFont.Weight.Normal)
    return f


def _shadow(p, rect, radius, strength=1.0):
    p.setPen(Qt.PenStyle.NoPen)
    for i in range(1, 8):
        p.setBrush(QColor(0, 0, 0, int((26 - i * 3) * strength)))
        p.drawRoundedRect(rect.adjusted(-i, -i + 3, i, i + 3), radius + i, radius + i)


def draw_chip(p, x, y, kind, text, opacity=1.0, base_font=None):
    """Small pill with an icon and a text (used for the drag HUD and the zoom badge). Returns its rectangle."""
    f = _font(base_font or p.font(), 12, True)
    fm = QFontMetrics(f)
    tw = fm.horizontalAdvance(text)
    w, h = 12 + 16 + 7 + tw + 13, 28
    rect = QRectF(x, y, w, h)
    col = kc(kind)
    p.save()
    p.setOpacity(_clamp(opacity))
    p.setRenderHint(QPainter.RenderHint.Antialiasing, True)
    _shadow(p, rect, 14, 0.8)
    g = QLinearGradient(0, rect.top(), 0, rect.bottom())
    g.setColorAt(0, QColor(31, 36, 50, 245))
    g.setColorAt(1, QColor(20, 24, 35, 245))
    p.setPen(QPen(_a(col, 150), 1.1))
    p.setBrush(QBrush(g))
    p.drawRoundedRect(rect, 14, 14)
    draw_icon(p, kind, rect.left() + 12 + 8, rect.center().y(), 16, col, 2.4)
    p.setFont(f)
    p.setPen(QColor("#f1f3f9"))
    p.drawText(QPointF(rect.left() + 12 + 16 + 7, rect.center().y() + (fm.ascent() - fm.descent()) / 2.0), text)
    p.restore()
    return rect


# ------------------------------------------------------------------------------------------------ floating pills
class _Float(QWidget):
    """A pill that floats over its host: slides in with a little overshoot, stays, fades away. Never takes the mouse."""
    M = 18            # room around the pill for its shadow
    H = 44
    BOTTOM = True
    GAP = 22

    def __init__(self, host):
        super().__init__(host)
        self.host = host
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
        self._born = 0.0
        self._dead = 0.0
        self._leaving = False
        self._leave_t = 0.0
        self._inner_w = 120
        self._timer = QTimer(self)
        self._timer.setInterval(15)
        self._timer.timeout.connect(self._tick)
        host.installEventFilter(self)
        self.hide()

    def eventFilter(self, obj, ev):
        if obj is self.host and ev.type() == QEvent.Type.Resize and self.isVisible():
            self._place()
        return False

    def _place(self):
        w, h = int(self._inner_w + 2 * self.M), int(self.H + 2 * self.M)
        x = (self.host.width() - w) // 2
        y = (self.host.height() - h - self.GAP + self.M) if self.BOTTOM else (self.GAP - self.M)
        self.setGeometry(x, y, w, h)

    def _present(self, life):
        now = time.monotonic()
        fresh = not self.isVisible() or self._leaving
        if fresh:
            self._born = now
        self._leaving = False
        self._dead = now + life
        self._place()
        self.raise_()
        self.show()
        self._timer.start()
        return fresh

    def dismiss(self):
        if self.isVisible() and not self._leaving:
            self._leaving = True
            self._leave_t = time.monotonic()

    def _tick(self):
        now = time.monotonic()
        if not self._leaving and now >= self._dead:
            self._leaving = True
            self._leave_t = now
        if self._leaving and now - self._leave_t >= 0.3:
            self._timer.stop()
            self.hide()
            return
        self.update()

    def _draw(self, p, rect, now):
        pass

    def paintEvent(self, e):
        now = time.monotonic()
        a = _clamp((now - self._born) / 0.32)
        ease = out_back(a)
        op = _clamp(a * 2.2)
        lv = 0.0
        if self._leaving:
            lv = _clamp((now - self._leave_t) / 0.28)
            op *= 1.0 - lv
        if op <= 0.01:
            return
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        p.setRenderHint(QPainter.RenderHint.TextAntialiasing, True)
        p.setOpacity(op)
        rect = QRectF(self.M, self.M, self._inner_w, self.H)
        sgn = 1.0 if self.BOTTOM else -1.0
        p.translate(0, sgn * ((1.0 - ease) * 16.0 + lv * 8.0))
        sc = 0.94 + 0.06 * min(ease, 1.15)
        c = rect.center()
        p.translate(c)
        p.scale(sc, sc)
        p.translate(-c.x(), -c.y())
        _shadow(p, rect, self.H / 2.0)
        g = QLinearGradient(0, rect.top(), 0, rect.bottom())
        g.setColorAt(0, QColor(32, 37, 52, 247))
        g.setColorAt(1, QColor(20, 24, 35, 247))
        p.setPen(QPen(QColor(255, 255, 255, 34), 1.0))
        p.setBrush(QBrush(g))
        p.drawRoundedRect(rect, self.H / 2.0, self.H / 2.0)
        self._draw(p, rect, now)


class Toast(_Float):
    """busy(): a ring turns around the icon.  done(): the ring closes, a tick pops up.  info(): the icon pops."""

    def __init__(self, host):
        super().__init__(host)
        self.kind, self.title, self.detail, self.state = "info", "", "", "info"
        self._state_t = 0.0
        self._pending = None
        self._delay = QTimer(self)
        self._delay.setSingleShot(True)
        self._delay.timeout.connect(self._reveal)
        self._ft = _font(self.font(), 13, True)
        self._fd = _font(self.font(), 12, False)

    def _set(self, kind, title, detail, state, life):
        was_busy = self.isVisible() and not self._leaving and self.state == "busy"
        self.kind, self.state = kind, state
        mw = max(160, self.host.width() - 130)
        fmt, fmd = QFontMetrics(self._ft), QFontMetrics(self._fd)
        self.title = fmt.elidedText(title, Qt.TextElideMode.ElideRight, mw)
        tw = fmt.horizontalAdvance(self.title)
        left = mw - tw - 12
        self.detail = fmd.elidedText(detail, Qt.TextElideMode.ElideRight, left) if (detail and left > 60) else ""
        dw = fmd.horizontalAdvance(self.detail) if self.detail else 0
        self._inner_w = 14 + 32 + 11 + tw + ((11 + dw) if dw else 0) + 20
        self._state_t = time.monotonic()
        self._present(life)
        self._was_busy = was_busy

    def _reveal(self):
        if self._pending is not None:
            kind, title = self._pending
            self._set(kind, title, "", "busy", 120.0)

    def busy(self, kind, title):
        """A job started: the toast appears only if it takes a moment (no flicker for quick ones)."""
        self._pending = (kind, title)
        self._delay.start(150)

    def cancel_busy(self):
        self._delay.stop()
        self._pending = None
        if self.isVisible() and self.state == "busy":
            self.dismiss()

    def done(self, kind, title, detail="", life=2.6):
        self._delay.stop()
        self._pending = None
        self._set(kind, title, detail, "done", life)

    def info(self, kind, title, detail="", life=2.2):
        self._delay.stop()
        self._pending = None
        self._set(kind, title, detail, "info", life)

    def fail(self, title, detail="", life=4.5):
        self._delay.stop()
        self._pending = None
        self._set("fail", title, detail, "fail", life)

    def _draw(self, p, rect, now):
        col = kc(self.kind)
        cx, cy = rect.left() + 14 + 16, rect.center().y()
        k = _clamp((now - self._state_t) / 0.4)
        # soft glow of the kind colour behind the icon
        p.save()
        path = QPainterPath()
        path.addRoundedRect(rect, self.H / 2.0, self.H / 2.0)
        p.setClipPath(path)
        glow = QLinearGradient(rect.left(), 0, rect.left() + 130, 0)
        glow.setColorAt(0, _a(col, 52))
        glow.setColorAt(1, _a(col, 0))
        p.fillRect(rect, QBrush(glow))
        p.restore()
        # badge + ring
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(_a(col, 34))
        p.drawEllipse(QPointF(cx, cy), 16, 16)
        ring = QRectF(cx - 15, cy - 15, 30, 30)
        if self.state == "busy":
            ang = -((now * 420.0) % 360.0)
            p.setPen(QPen(_a(col, 70), 2.2))
            p.setBrush(Qt.BrushStyle.NoBrush)
            p.drawEllipse(ring)
            p.setPen(QPen(col, 2.6, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap))
            p.drawArc(ring, int((ang + 90) * 16), int(-110 * 16))
        else:
            ring_col = QColor("#f87171") if self.state == "fail" else col
            p.setPen(QPen(ring_col, 2.4, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap))
            p.setBrush(Qt.BrushStyle.NoBrush)
            p.drawArc(ring, 90 * 16, int(-360 * 16 * out_cubic(k * 1.3)))
        pop = 1.0 + 0.28 * math.sin(math.pi * k) if self.state != "busy" else 1.0
        draw_icon(p, "fail" if self.state == "fail" else self.kind, cx, cy, 17 * pop, col if self.state != "fail" else QColor("#f87171"))
        if self.state == "done":                                   # tick badge pops in after the ring has closed
            kb = _clamp((now - self._state_t - 0.18) / 0.3)
            if kb > 0:
                s = out_back(kb)
                bx, by = cx + 11, cy + 11
                p.setPen(QPen(QColor("#151a26"), 2.0))
                p.setBrush(QColor("#22c55e"))
                p.drawEllipse(QPointF(bx, by), 7.2 * s, 7.2 * s)
                draw_icon(p, "ok", bx, by, 9 * s, QColor("white"), 3.2)
        # text
        tx = rect.left() + 14 + 32 + 11
        fmt = QFontMetrics(self._ft)
        base = cy + (fmt.ascent() - fmt.descent()) / 2.0
        p.setFont(self._ft)
        p.setPen(QColor("#f4f6fb"))
        p.drawText(QPointF(tx, base), self.title)
        if self.detail:
            p.setFont(self._fd)
            p.setPen(QColor("#9aa3b8"))
            p.drawText(QPointF(tx + fmt.horizontalAdvance(self.title) + 11, base), self.detail)
        # time left
        if self.state != "busy":
            life = max(0.001, self._dead - self._born)
            left = _clamp((self._dead - now) / life)
            p.save()
            p.setClipPath(self._pill_path(rect))
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(_a(col, 150))
            p.drawRect(QRectF(rect.left(), rect.bottom() - 2.4, rect.width() * left, 2.4))
            p.restore()

    def _pill_path(self, rect):
        path = QPainterPath()
        path.addRoundedRect(rect, self.H / 2.0, self.H / 2.0)
        return path


class KeyChip(_Float):
    """The shortcut that was just pressed, drawn as key caps, and what it does."""
    BOTTOM = False
    GAP = 18
    H = 40

    def __init__(self, host):
        super().__init__(host)
        self.keys, self.label = [], ""
        self._press_t = 0.0
        self._fl = _font(self.font(), 13, True)
        self._fk = _font(self.font(), 12, True)

    def show_keys(self, keys, label):
        fmk = QFontMetrics(self._fk)
        fml = QFontMetrics(self._fl)
        self.keys, self.label = list(keys), label
        w = 12
        for k in self.keys:
            w += max(26, fmk.horizontalAdvance(k) + 16) + 5
        self._inner_w = w + 6 + fml.horizontalAdvance(label) + 18
        self._press_t = time.monotonic()
        self._present(1.25)

    def _draw(self, p, rect, now):
        x = rect.left() + 12
        press = 1.0 - _clamp((now - self._press_t) / 0.16)         # the caps are pushed down for a moment
        fm = QFontMetrics(self._fk)
        p.setFont(self._fk)
        for k in self.keys:
            w = max(26, fm.horizontalAdvance(k) + 16)
            cap = QRectF(x, rect.center().y() - 13 + 2.5 * press, w, 24)
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(QColor(12, 15, 22))
            p.drawRoundedRect(cap.adjusted(0, 3, 0, 3 - 2.5 * press), 6, 6)
            g = QLinearGradient(0, cap.top(), 0, cap.bottom())
            g.setColorAt(0, QColor(78, 86, 112))
            g.setColorAt(1, QColor(56, 63, 86))
            p.setPen(QPen(QColor(255, 255, 255, 40), 1))
            p.setBrush(QBrush(g))
            p.drawRoundedRect(cap, 6, 6)
            p.setPen(QColor("#ffffff"))
            p.drawText(QPointF(cap.left() + (w - fm.horizontalAdvance(k)) / 2.0,
                               cap.center().y() + (fm.ascent() - fm.descent()) / 2.0), k)
            x += w + 5
        fml = QFontMetrics(self._fl)
        p.setFont(self._fl)
        p.setPen(QColor("#dfe3f0"))
        p.drawText(QPointF(x + 6, rect.center().y() + (fml.ascent() - fml.descent()) / 2.0), self.label)


# ------------------------------------------------------------------------------------------------ per picture effects
class Effects:
    """Everything that is drawn ON the picture or floats over it. One per canvas (canvas.fx)."""

    def __init__(self, canvas):
        self.c = canvas
        self.toast = Toast(canvas)
        self.keychip = KeyChip(canvas)
        self._items = []
        self._badge = None
        self._hud_t0 = None
        self._timer = QTimer(canvas)
        self._timer.setInterval(16)
        self._timer.timeout.connect(self._tick)

    # -- toasts / keys
    def busy(self, kind, title):
        if SHOW_MESSAGES:
            self.toast.busy(kind, title)

    def cancel_busy(self):
        self.toast.cancel_busy()

    def done(self, kind, title, detail=""):
        if SHOW_MESSAGES:
            self.toast.done(kind, title, detail)
        else:
            self._soft(kind)

    def info(self, kind, title, detail=""):
        if SHOW_MESSAGES:
            self.toast.info(kind, title, detail)
        else:
            self._soft(kind)

    def _soft(self, kind):
        """No message: a soft flash over the picture says 'done'.  A specific effect (pulse / sweep on the changed area)
        that comes a moment before or after takes its place, so nothing flashes twice."""
        if kind in _NO_ANIM:
            return
        t_call = time.monotonic()
        QTimer.singleShot(60, lambda: self._soft_now(kind, t_call))

    def _soft_now(self, kind, t_call):
        try:
            if any(it["t0"] > t_call - 0.35 for it in self._items):
                return
            self.pulse(kind, None)
        except RuntimeError:                       # the picture was closed meanwhile
            pass

    def warn(self, title, detail=""):
        self.toast.info("warn", title, detail, life=3.8)

    def fail(self, title, detail=""):
        self.toast.fail(title, detail)

    def key(self, keys, label):
        if SHOW_MESSAGES:
            self.keychip.show_keys(keys, label)

    # -- effects on the picture (coordinates: preview pixels = the same as the selection mask)
    def pulse(self, kind, rect=None, effect="pulse", dur=0.75):
        """rect = QRectF in preview pixels; None = the whole picture (a soft flash)."""
        if self.c._img is None:
            return
        if rect is None:
            effect, rect = "wash", QRectF(0, 0, self.c._iw, self.c._ih)
            dur = 0.4
        self._add(dict(k=effect, kind=kind, rect=QRectF(rect), pt=None, dur=dur))

    def ring(self, kind, x, y, dur=0.65):
        if self.c._img is None:
            return
        self._add(dict(k="ring", kind=kind, rect=None, pt=(float(x), float(y)), dur=dur))

    def zoom_badge(self, text):
        self._badge = dict(text=text, t0=time.monotonic(), life=1.2)
        self._start()

    def _add(self, it):
        it["t0"] = time.monotonic()
        self._items.append(it)
        self._start()

    def _start(self):
        if not self._timer.isActive():
            self._timer.start()
        self.c.update()

    def _wrect(self, it):
        c = self.c
        if it["pt"] is not None:
            p = c.to_wid(QPointF(*it["pt"]))
            return QRectF(p.x() - 60, p.y() - 60, 120, 120)
        r = it["rect"]
        a, b = c.to_wid(QPointF(r.left(), r.top())), c.to_wid(QPointF(r.right(), r.bottom()))
        out = QRectF(a, b).normalized()
        if out.width() < 12:
            out.adjust(-(12 - out.width()) / 2, 0, (12 - out.width()) / 2, 0)
        if out.height() < 12:
            out.adjust(0, -(12 - out.height()) / 2, 0, (12 - out.height()) / 2)
        return out

    def _tick(self):
        now = time.monotonic()
        keep = [it for it in self._items if now - it["t0"] < it["dur"]]
        region = None
        for it in self._items:
            r = self._wrect(it).adjusted(-30, -30, 30, 30)
            region = r if region is None else region.united(r)
        self._items = keep
        if self._badge is not None:
            r = QRectF(self.c.width() - 160, 0, 160, 60)
            region = r if region is None else region.united(r)
            if now - self._badge["t0"] >= self._badge["life"]:
                self._badge = None
        if region is not None:
            self.c.update(region.toAlignedRect())
        if not self._items and self._badge is None:
            self._timer.stop()

    def paint(self, p):
        if not self._items and self._badge is None:
            return
        now = time.monotonic()
        p.save()
        p.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        for it in list(self._items):
            t = (now - it["t0"]) / it["dur"]
            if 0.0 <= t < 1.0:
                getattr(self, "_p_" + it["k"])(p, it, t)
        if self._badge is not None:
            age = now - self._badge["t0"]
            life = self._badge["life"]
            op = 1.0 if age < life - 0.3 else _clamp((life - age) / 0.3)
            f = _font(p.font(), 12, True)
            w = 12 + 16 + 7 + QFontMetrics(f).horizontalAdvance(self._badge["text"]) + 13
            slide = (1.0 - out_cubic(age / 0.18)) * 8.0
            draw_chip(p, self.c.width() - w - 14, 14 - slide, "zoom", self._badge["text"], op)
        p.restore()

    def _p_pulse(self, p, it, t):
        col, r = kc(it["kind"]), self._wrect(it)
        fade = 1.0 - t
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(_a(col, 80 * fade * fade))
        p.drawRoundedRect(r, 4, 4)
        p.setBrush(Qt.BrushStyle.NoBrush)
        for k, delay in enumerate((0.0, 0.14)):
            if t < delay:
                continue
            tt = _clamp((t - delay) / (1.0 - delay))
            g = 16.0 * out_cubic(tt)
            p.setPen(QPen(_a(col, 255 * (1.0 - tt) ** 1.4 * (1.0 if k == 0 else 0.55)), 2.6 * (1.0 - tt) + 0.8))
            p.drawRoundedRect(r.adjusted(-g, -g, g, g), 4 + g * 0.5, 4 + g * 0.5)

    def _p_sweep(self, p, it, t):
        col, r = kc(it["kind"]), self._wrect(it)
        p.save()
        path = QPainterPath()
        path.addRoundedRect(r, 4, 4)
        p.setClipPath(path)
        bw = max(30.0, r.width() * 0.45)
        x = r.left() - bw + (r.width() + bw * 2.0) * out_cubic(t)
        g = QLinearGradient(x - bw / 2.0, 0, x + bw / 2.0, 0)
        g.setColorAt(0.0, _a(col, 0))
        g.setColorAt(0.5, _a(QColor("white"), 160 * (1.0 - t)))
        g.setColorAt(1.0, _a(col, 0))
        p.fillRect(r, QBrush(g))
        p.restore()
        p.setPen(QPen(_a(col, 230 * (1.0 - t)), 2.0))
        p.setBrush(Qt.BrushStyle.NoBrush)
        p.drawRoundedRect(r, 4, 4)

    def _p_wash(self, p, it, t):
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(_a(kc(it["kind"]), 72 * (1.0 - t) ** 2))
        p.drawRect(self._wrect(it))

    def _p_ring(self, p, it, t):
        col = kc(it["kind"])
        c = self.c.to_wid(QPointF(*it["pt"]))
        rad = 6.0 + 44.0 * out_cubic(t)
        p.setBrush(Qt.BrushStyle.NoBrush)
        p.setPen(QPen(_a(col, 255 * (1.0 - t)), 2.6 * (1.0 - t) + 0.8))
        p.drawEllipse(c, rad, rad)
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(_a(col, 210 * (1.0 - t)))
        p.drawEllipse(c, 4.5 * (1.0 - t) + 1.0, 4.5 * (1.0 - t) + 1.0)

    # -- the chip that follows the mouse while dragging
    def hud(self, p, pos, kind, text, bounds):
        now = time.monotonic()
        if self._hud_t0 is None:
            self._hud_t0 = now
        op = _clamp((now - self._hud_t0) / 0.12)
        f = _font(p.font(), 12, True)
        w, h = 12 + 16 + 7 + QFontMetrics(f).horizontalAdvance(text) + 13, 28
        x, y = pos.x() + 18, pos.y() + 22
        if x + w > bounds.right() - 6:
            x = pos.x() - 18 - w
        if y + h > bounds.bottom() - 6:
            y = pos.y() - 22 - h
        draw_chip(p, max(4.0, x), max(4.0, y), kind, text, op)
        if op < 1.0:
            QTimer.singleShot(16, self.c.update)

    def hud_off(self):
        self._hud_t0 = None


# ------------------------------------------------------------------------------------------------ cursors
def rotate_cursor():
    """Curved arrow: shown outside a Transform box (drag = rotate). Hot spot = the middle."""
    pm = QPixmap(32, 32)
    pm.fill(Qt.GlobalColor.transparent)
    p = QPainter(pm)
    p.setRenderHint(QPainter.RenderHint.Antialiasing, True)
    for width, color in ((5.2, "#ffffff"), (2.4, "#1b2030")):
        pen = QPen(QColor(color), width, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap, Qt.PenJoinStyle.RoundJoin)
        p.setPen(pen)
        p.drawArc(QRectF(8, 8, 16, 16), 100 * 16, 250 * 16)
        _head(p, 24, 16, 0, -1, 6.0)
    p.end()
    return QCursor(pm, 16, 16)


# ------------------------------------------------------------------------------------------------ button ripple
class _Ripple(QWidget):
    def __init__(self, btn, pos):
        super().__init__(btn)
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
        self.setGeometry(btn.rect())
        self._c = QPointF(pos)
        self._t0 = time.monotonic()
        w, h = btn.width(), btn.height()
        self._R = math.hypot(max(pos.x(), w - pos.x()), max(pos.y(), h - pos.y()))
        self._timer = QTimer(self)
        self._timer.setInterval(15)
        self._timer.timeout.connect(self.update)
        self._timer.start()
        self.show()
        self.raise_()

    def paintEvent(self, e):
        t = (time.monotonic() - self._t0) / 0.5
        if t >= 1.0:
            self._timer.stop()
            self.hide()
            self.deleteLater()
            return
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        path = QPainterPath()
        path.addRoundedRect(QRectF(self.rect()), 8, 8)
        p.setClipPath(path)
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QColor(255, 255, 255, int(78 * (1.0 - t) ** 1.5)))
        r = self._R * out_cubic(t)
        p.drawEllipse(self._c, r, r)


class _ButtonFilter(QObject):
    def eventFilter(self, obj, ev):
        try:
            if ev.type() == QEvent.Type.MouseButtonPress and isinstance(obj, (QPushButton, QToolButton)) \
                    and obj.isEnabled() and ev.button() == Qt.MouseButton.LeftButton \
                    and obj.width() > 6 and obj.height() > 6:
                _Ripple(obj, ev.position())
        except Exception:
            pass
        return False


def install_ripples(app):
    """Every push button / tool button gives a soft wave where it was pressed."""
    if getattr(app, "_ifx_ripples", None) is None:
        app._ifx_ripples = _ButtonFilter(app)
        app.installEventFilter(app._ifx_ripples)


# ------------------------------------------------------------------------------------------------ drop zone
class DropOverlay(QWidget):
    """While files are dragged over the window: it dims and shows where to drop."""

    def __init__(self, host):
        super().__init__(host)
        self.host = host
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
        self._t0 = 0.0
        self._leaving = False
        self._leave_t = 0.0
        self._timer = QTimer(self)
        self._timer.setInterval(16)
        self._timer.timeout.connect(self._tick)
        host.installEventFilter(self)
        self.hide()

    def eventFilter(self, obj, ev):
        if obj is self.host and ev.type() == QEvent.Type.Resize and self.isVisible():
            self.setGeometry(self.host.rect())
        return False

    def present(self):
        self._t0 = time.monotonic()
        self._leaving = False
        self.setGeometry(self.host.rect())
        self.raise_()
        self.show()
        self._timer.start()

    def dismiss(self):
        if self.isVisible() and not self._leaving:
            self._leaving = True
            self._leave_t = time.monotonic()

    def _tick(self):
        if self._leaving and time.monotonic() - self._leave_t > 0.2:
            self._timer.stop()
            self.hide()
            return
        self.update()

    def paintEvent(self, e):
        now = time.monotonic()
        a = out_cubic((now - self._t0) / 0.2)
        if self._leaving:
            a *= 1.0 - _clamp((now - self._leave_t) / 0.2)
        if a <= 0.01:
            return
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        p.fillRect(self.rect(), QColor(8, 10, 16, int(200 * a)))
        frame = QRectF(self.rect()).adjusted(26, 26, -26, -26)
        pen = QPen(_a(QColor("#818cf8"), 255 * a), 2.6)
        pen.setDashPattern([6, 5])
        pen.setDashOffset(-now * 14.0)
        p.setPen(pen)
        p.setBrush(_a(QColor("#6366f1"), 26 * a))
        p.drawRoundedRect(frame, 28, 28)
        c = frame.center()
        bob = math.sin(now * 5.0) * 5.0
        g = QLinearGradient(c.x() - 44, c.y() - 90, c.x() + 44, c.y() + 0)
        g.setColorAt(0, _a(QColor("#6366f1"), 255 * a))
        g.setColorAt(0.55, _a(QColor("#a855f7"), 255 * a))
        g.setColorAt(1, _a(QColor("#ec4899"), 255 * a))
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QBrush(g))
        p.drawRoundedRect(QRectF(c.x() - 44, c.y() - 76, 88, 88), 24, 24)
        draw_icon(p, "drop", c.x(), c.y() - 32 + bob, 46, _a(QColor("white"), 255 * a), 2.2)
        p.setOpacity(a)
        p.setFont(_font(self.font(), 22, True))
        p.setPen(QColor("#f4f6fb"))
        p.drawText(QRectF(frame.left(), c.y() + 28, frame.width(), 34), int(Qt.AlignmentFlag.AlignCenter),
                   "Drop to add")
        p.setFont(_font(self.font(), 13, False))
        p.setPen(QColor("#9aa3b8"))
        p.drawText(QRectF(frame.left(), c.y() + 62, frame.width(), 24), int(Qt.AlignmentFlag.AlignCenter),
                   "Pictures, PDF files or a copied picture")
