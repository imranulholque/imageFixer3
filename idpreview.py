"""ID card scan: the small PREVIEW (how a side will look on the page) and the CORNER CROP (drag the 4 corners onto the
4 corners of the card when the automatic cut-out was wrong).  Both are plain dialogs; the scan window (scandlg) decides
what happens after them.  The preview can be switched off in the Scan window."""
import numpy as np
from PIL import Image
from PySide6.QtCore import QPointF, QRectF, Qt
from PySide6.QtGui import QBrush, QColor, QImage, QPainter, QPainterPath, QPen, QPixmap, QPolygonF
from PySide6.QtWidgets import QDialog, QHBoxLayout, QLabel, QPushButton, QVBoxLayout, QWidget


def pil_to_pixmap(img):
    img = img.convert("RGB")
    arr = np.ascontiguousarray(np.asarray(img))
    qi = QImage(arr.data, arr.shape[1], arr.shape[0], arr.shape[1] * 3, QImage.Format.Format_RGB888).copy()
    return QPixmap.fromImage(qi)


# ------------------------------------------------------------------------------------------------ corner crop
class _CornerView(QWidget):
    """The whole scan with 4 corner points.  Drag a corner circle onto a corner of the card; drag a middle square to move a whole side."""
    GRAB = 26                                       # how near (screen pixels) a click must be to take a point

    def __init__(self, img, quad, parent=None):
        super().__init__(parent)
        self.setMinimumSize(560, 400)
        self.setMouseTracking(True)
        self.full_w, self.full_h = img.size
        f = min(1.0, 1400.0 / max(img.size))
        small = img.convert("RGB") if f >= 1.0 else img.convert("RGB").resize(
            (max(1, int(img.width * f)), max(1, int(img.height * f))), Image.Resampling.LANCZOS)
        self.f = small.width / float(self.full_w)                  # shown picture / full picture
        self.pix = pil_to_pixmap(small)
        self.pts = [[float(x), float(y)] for x, y in quad]          # full-size picture pixels
        self.start = [list(p) for p in self.pts]
        self._drag = None
        self._last = None
        self.vr = [0.0, 0.0, float(self.full_w), float(self.full_h)]   # the part of the scan that is shown (full pixels)
        xs, ys = [p[0] for p in self.pts], [p[1] for p in self.pts]
        if (max(xs) - min(xs)) * (max(ys) - min(ys)) < 0.8 * self.full_w * self.full_h:
            self.zoom_card()                                        # a small card on a big scan: start zoomed in

    def zoomed(self):
        return self.vr != [0.0, 0.0, float(self.full_w), float(self.full_h)]

    def zoom_card(self):
        xs, ys = [p[0] for p in self.start], [p[1] for p in self.start]
        pad = 0.30 * max(max(xs) - min(xs), max(ys) - min(ys)) + 40
        self.vr = [max(0.0, min(xs) - pad), max(0.0, min(ys) - pad),
                   min(float(self.full_w), max(xs) + pad), min(float(self.full_h), max(ys) + pad)]
        self.update()

    def zoom_all(self):
        self.vr = [0.0, 0.0, float(self.full_w), float(self.full_h)]
        self.update()

    def reset(self):
        self.pts = [list(p) for p in self.start]
        self.update()

    def quad(self):
        return [(p[0], p[1]) for p in self.pts]

    def _geom(self):
        vw, vh = self.vr[2] - self.vr[0], self.vr[3] - self.vr[1]
        k = min(self.width() / vw, self.height() / vh)
        ox = (self.width() - vw * k) / 2.0
        oy = (self.height() - vh * k) / 2.0
        return k, ox, oy

    def _to_screen(self, p):
        k, ox, oy = self._geom()
        return QPointF(ox + (p[0] - self.vr[0]) * k, oy + (p[1] - self.vr[1]) * k)

    def paintEvent(self, e):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.fillRect(self.rect(), QColor("#0f1219"))
        k, ox, oy = self._geom()
        vw, vh = self.vr[2] - self.vr[0], self.vr[3] - self.vr[1]
        p.drawPixmap(QRectF(ox, oy, vw * k, vh * k), self.pix,
                     QRectF(self.vr[0] * self.f, self.vr[1] * self.f, vw * self.f, vh * self.f))
        o = [self._to_screen(q) for q in self.pts]
        path = QPainterPath()                                       # dim only OUTSIDE the card (no tint over the card)
        path.addRect(QRectF(ox, oy, vw * k, vh * k))
        path.addPolygon(QPolygonF(o))
        path.closeSubpath()
        path.setFillRule(Qt.FillRule.OddEvenFill)
        p.fillPath(path, QBrush(QColor(0, 0, 0, 140)))

        def lerp(a, b, t):
            return QPointF(a.x() + (b.x() - a.x()) * t, a.y() + (b.y() - a.y()) * t)

        tl, tr, br, bl = o
        p.setBrush(Qt.BrushStyle.NoBrush)
        p.setPen(QPen(QColor(165, 180, 252, 110), 1))               # the thirds grid
        for i in (1, 2):
            t = i / 3.0
            p.drawLine(lerp(tl, tr, t), lerp(bl, br, t))
            p.drawLine(lerp(tl, bl, t), lerp(tr, br, t))
        p.setPen(QPen(QColor("#a5b4fc"), 1.8))                      # the purple outline
        p.drawPolygon(QPolygonF(o))
        p.setPen(QPen(QColor("#4f46e5"), 2))
        for i in range(4):                                          # white corner circles
            p.setBrush(QBrush(QColor("#f59e0b") if self._drag == ("c", i) else QColor("white")))
            p.drawEllipse(o[i], 8.0, 8.0)
        for i in range(4):                                          # a square in the middle of every side
            m = lerp(o[i], o[(i + 1) % 4], 0.5)
            p.setBrush(QBrush(QColor("#f59e0b") if self._drag == ("e", i) else QColor("white")))
            p.drawRect(QRectF(m.x() - 6.0, m.y() - 6.0, 12.0, 12.0))
        p.end()

    def _nearest(self, pos):
        """('c', i) for a corner circle, ('e', i) for the square in the middle of side i -> i+1, else None."""
        best, bd = None, self.GRAB
        o = [self._to_screen(q) for q in self.pts]
        for i in range(4):                                          # corners win over side squares
            d = ((o[i].x() - pos.x()) ** 2 + (o[i].y() - pos.y()) ** 2) ** 0.5
            if d < bd:
                best, bd = ("c", i), d
        if best is not None:
            return best
        for i in range(4):
            a, b = o[i], o[(i + 1) % 4]
            mx, my = (a.x() + b.x()) / 2.0, (a.y() + b.y()) / 2.0
            d = ((mx - pos.x()) ** 2 + (my - pos.y()) ** 2) ** 0.5
            if d < bd:
                best, bd = ("e", i), d
        return best

    def _to_full(self, pos):
        k, ox, oy = self._geom()
        return ((pos.x() - ox) / k + self.vr[0], (pos.y() - oy) / k + self.vr[1])

    def _clamp(self, x, y):
        return min(max(x, 0.0), self.full_w - 1.0), min(max(y, 0.0), self.full_h - 1.0)

    def _move(self, hit, pos):
        kind, i = hit
        x, y = self._to_full(pos)
        if kind == "c":
            self.pts[i] = list(self._clamp(x, y))
        else:                                                       # drag a whole side: both of its corners move together
            j = (i + 1) % 4
            if self._last is None:
                self._last = (x, y)
            dx, dy = x - self._last[0], y - self._last[1]
            for n in (i, j):
                self.pts[n] = list(self._clamp(self.pts[n][0] + dx, self.pts[n][1] + dy))
            self._last = (x, y)
        self.update()

    def mousePressEvent(self, e):
        if e.button() == Qt.MouseButton.LeftButton:
            self._drag = self._nearest(e.position())
            self._last = None
            if self._drag is not None:
                if self._drag[0] == "c":
                    self._move(self._drag, e.position())
                else:
                    self._last = self._to_full(e.position())
                self.update()

    def mouseMoveEvent(self, e):
        if self._drag is not None:
            self._move(self._drag, e.position())
        else:
            h = self._nearest(e.position())
            self.setCursor(Qt.CursorShape.ArrowCursor if h is None else
                           Qt.CursorShape.OpenHandCursor if h[0] == "c" else Qt.CursorShape.SizeAllCursor)

    def mouseReleaseEvent(self, e):
        self._drag = None
        self._last = None
        self.update()


class CornerDialog(QDialog):
    """'Corner crop': the same idea as the Straighten tool (4 corners).  quad() = the corners in scan pixels."""

    def __init__(self, parent, img, quad, title="Corner crop"):
        super().__init__(parent)
        self.setWindowTitle(title)
        self.setModal(True)
        v = QVBoxLayout(self)
        t = QLabel("Drag the 4 corner circles onto the 4 corners of the card (the middle squares move a whole side), then press Apply.")
        t.setStyleSheet("font-weight: 600;")
        t.setWordWrap(True)
        v.addWidget(t)
        self.view = _CornerView(img, quad)
        v.addWidget(self.view, 1)
        row = QHBoxLayout()
        ok = QPushButton("Apply")
        ok.setProperty("accent", True)
        ok.setDefault(True)
        ok.clicked.connect(self.accept)
        rs = QPushButton("Back to the first corners")
        rs.clicked.connect(self.view.reset)
        self.btnZoom = QPushButton("")
        self.btnZoom.clicked.connect(self._toggle_zoom)
        self._zoom_text()
        cn = QPushButton("Cancel")
        cn.clicked.connect(self.reject)
        row.addWidget(ok)
        row.addWidget(rs)
        row.addWidget(self.btnZoom)
        row.addStretch(1)
        row.addWidget(cn)
        v.addLayout(row)
        self.resize(900, 680)

    def _zoom_text(self):
        self.btnZoom.setText("Show the whole scan" if self.view.zoomed() else "Zoom to the card")

    def _toggle_zoom(self):
        self.view.zoom_all() if self.view.zoomed() else self.view.zoom_card()
        self._zoom_text()

    def quad(self):
        return self.view.quad()


# ------------------------------------------------------------------------------------------------ preview
class PreviewDialog(QDialog):
    """A small picture of the card side(s) as they will look on the page.
    After exec(): .action = "ok" | "again" | ("crop", index of the side)."""

    def __init__(self, parent, title, text, image, names, crop=True):
        super().__init__(parent)
        self.setWindowTitle(title)
        self.setModal(True)
        self.action = "again"                              # closing the window = scan this side again
        v = QVBoxLayout(self)
        t = QLabel(text)
        t.setWordWrap(True)
        t.setStyleSheet("font-weight: 600;")
        v.addWidget(t)
        pic = QLabel()
        pic.setPixmap(pil_to_pixmap(image))
        pic.setAlignment(Qt.AlignmentFlag.AlignCenter)
        v.addWidget(pic)
        if crop:
            row = QHBoxLayout()
            for i, n in enumerate(names):
                b = QPushButton("Corner crop: %s" % n if len(names) > 1 else "Corner crop (fix the 4 corners)")
                b.setToolTip("The card was cut out wrong? Drag the 4 corners onto the card yourself.")
                b.clicked.connect(lambda _=False, i=i: self._set(("crop", i)))
                row.addWidget(b, 1)
            v.addLayout(row)
        row = QHBoxLayout()
        ok = QPushButton("OK")
        ok.setProperty("accent", True)
        ok.setDefault(True)
        ok.clicked.connect(lambda: self._set("ok"))
        ag = QPushButton("Scan again")
        ag.clicked.connect(lambda: self._set("again"))
        row.addWidget(ok, 1)
        row.addWidget(ag, 1)
        v.addLayout(row)

    def _set(self, action):
        self.action = action
        self.accept()
