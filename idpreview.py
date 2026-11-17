"""ID card scan: the small PREVIEW (how a side will look on the page) and the CORNER CROP (drag the 4 corners onto the
4 corners of the card when the automatic cut-out was wrong).  Both are plain dialogs; the scan window (scandlg) decides
what happens after them.  The preview can be switched off in the Scan window."""
import numpy as np
from PIL import Image
from PySide6.QtCore import QPointF, QRectF, Qt
from PySide6.QtGui import QColor, QImage, QPainter, QPen, QPixmap, QPolygonF
from PySide6.QtWidgets import QDialog, QHBoxLayout, QLabel, QPushButton, QVBoxLayout, QWidget


def pil_to_pixmap(img):
    img = img.convert("RGB")
    arr = np.ascontiguousarray(np.asarray(img))
    qi = QImage(arr.data, arr.shape[1], arr.shape[0], arr.shape[1] * 3, QImage.Format.Format_RGB888).copy()
    return QPixmap.fromImage(qi)


# ------------------------------------------------------------------------------------------------ corner crop
class _CornerView(QWidget):
    """The whole scan with 4 corner points.  Drag a point (or click near it) onto a corner of the card."""
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
        poly = QPolygonF([self._to_screen(q) for q in self.pts])
        p.setPen(QPen(QColor("#22d3ee"), 2))
        p.setBrush(QColor(34, 211, 238, 40))
        p.drawPolygon(poly)
        for i, q in enumerate(self.pts):
            c = self._to_screen(q)
            p.setPen(QPen(QColor("white"), 2))
            p.setBrush(QColor("#6366f1") if i != self._drag else QColor("#f59e0b"))
            p.drawEllipse(c, 10, 10)
        p.end()

    def _nearest(self, pos):
        best, bd = None, self.GRAB
        for i, q in enumerate(self.pts):
            c = self._to_screen(q)
            d = ((c.x() - pos.x()) ** 2 + (c.y() - pos.y()) ** 2) ** 0.5
            if d < bd:
                best, bd = i, d
        return best

    def _move(self, i, pos):
        k, ox, oy = self._geom()
        x = min(max((pos.x() - ox) / k + self.vr[0], 0.0), self.full_w - 1.0)
        y = min(max((pos.y() - oy) / k + self.vr[1], 0.0), self.full_h - 1.0)
        self.pts[i] = [x, y]
        self.update()

    def mousePressEvent(self, e):
        if e.button() == Qt.MouseButton.LeftButton:
            self._drag = self._nearest(e.position())
            if self._drag is not None:
                self._move(self._drag, e.position())

    def mouseMoveEvent(self, e):
        if self._drag is not None:
            self._move(self._drag, e.position())
        else:
            self.setCursor(Qt.CursorShape.OpenHandCursor if self._nearest(e.position()) is not None
                           else Qt.CursorShape.ArrowCursor)

    def mouseReleaseEvent(self, e):
        self._drag = None
        self.update()


class CornerDialog(QDialog):
    """'Corner crop': the same idea as the Straighten tool (4 corners).  quad() = the corners in scan pixels."""

    def __init__(self, parent, img, quad, title="Corner crop"):
        super().__init__(parent)
        self.setWindowTitle(title)
        self.setModal(True)
        v = QVBoxLayout(self)
        t = QLabel("Drag the 4 points onto the 4 corners of the card, then press Apply.")
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
