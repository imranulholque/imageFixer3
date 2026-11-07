"""Original vs edited: one window, a slider you drag (or side by side).  Mouse wheel = zoom, drag = move."""
from PySide6.QtCore import QPointF, QRectF, Qt
from PySide6.QtGui import QColor, QPainter, QPen
from PySide6.QtWidgets import (QApplication, QButtonGroup, QDialog, QHBoxLayout, QLabel, QPushButton, QVBoxLayout, QWidget)


class CompareView(QWidget):
    def __init__(self, orig, edited, parent=None):
        super().__init__(parent)
        self.a, self.b = orig, edited               # QImage: original, edited
        self.mode = "split"
        self.pos = 0.5                              # where the line is (0..1), slider mode
        self.z = 1.0
        self.pan = QPointF(0, 0)
        self._drag = None
        self._last = None
        self.setMouseTracking(True)
        self.setMinimumSize(420, 300)

    def set_mode(self, m):
        self.mode = m
        self.update()

    # ---- geometry
    def _full(self):
        return QRectF(self.rect()).adjusted(8, 8, -8, -8)

    def _areas(self):
        full = self._full()
        if self.mode == "split":
            return full, full
        half = full.width() / 2.0
        return (QRectF(full.left(), full.top(), half - 4, full.height()),
                QRectF(full.left() + half + 4, full.top(), half - 4, full.height()))

    def _div_x(self):
        f = self._full()
        return f.left() + f.width() * self.pos

    @staticmethod
    def _fit(img, area):
        s = min(area.width() / max(1, img.width()), area.height() / max(1, img.height()))
        w, h = img.width() * s, img.height() * s
        return QRectF(area.center().x() - w / 2, area.center().y() - h / 2, w, h)

    def _xf(self, r, area):
        c, z = area.center(), self.z
        return QRectF(c.x() + (r.left() - c.x()) * z + self.pan.x(), c.y() + (r.top() - c.y()) * z + self.pan.y(),
                      r.width() * z, r.height() * z)

    # ---- painting
    def _tag(self, p, text, x, y, right=False):
        fm = p.fontMetrics()
        w, h = fm.horizontalAdvance(text) + 22, fm.height() + 10
        r = QRectF(x - (w if right else 0), y, w, h)
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QColor(10, 12, 16, 210))
        p.drawRoundedRect(r, 8, 8)
        p.setPen(QColor("white"))
        p.drawText(r, int(Qt.AlignmentFlag.AlignCenter), text)

    def paintEvent(self, e):
        p = QPainter(self)
        p.fillRect(self.rect(), QColor("#0a0c10"))
        p.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform, True)
        p.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        al, ar = self._areas()
        ra = self._xf(self._fit(self.a, al), al)
        rb = self._xf(self._fit(self.b, ar), ar)
        if self.mode == "split":
            x = self._div_x()
            p.save()
            p.setClipRect(al)
            p.drawImage(rb, self.b)                                   # edited everywhere ...
            p.setClipRect(QRectF(al.left(), al.top(), x - al.left(), al.height()))
            p.drawImage(ra, self.a)                                   # ... original on the left of the line
            p.restore()
            p.setPen(QPen(QColor(0, 0, 0, 160), 4))
            p.drawLine(QPointF(x, al.top()), QPointF(x, al.bottom()))
            p.setPen(QPen(QColor(255, 255, 255), 2))
            p.drawLine(QPointF(x, al.top()), QPointF(x, al.bottom()))
            cy = al.center().y()
            p.setPen(QPen(QColor(255, 255, 255), 2))
            p.setBrush(QColor("#6366f1"))
            p.drawEllipse(QPointF(x, cy), 16, 16)
            p.drawText(QRectF(x - 16, cy - 16, 32, 32), int(Qt.AlignmentFlag.AlignCenter), "\u25c2\u25b8")
            self._tag(p, "Original", al.left() + 8, al.top() + 8)
            self._tag(p, "Edited", al.right() - 8, al.top() + 8, right=True)
        else:
            for area, rect, img, name in ((al, ra, self.a, "Original"), (ar, rb, self.b, "Edited")):
                p.save()
                p.setClipRect(area)
                p.drawImage(rect, img)
                p.restore()
                self._tag(p, name, area.left() + 8, area.top() + 8)
        p.end()

    # ---- mouse
    def _near_line(self, pt):
        return self.mode == "split" and abs(pt.x() - self._div_x()) <= 16

    def mousePressEvent(self, e):
        if e.button() != Qt.MouseButton.LeftButton:
            return
        pt = e.position()
        if self._near_line(pt):
            self._drag = "div"
        else:
            self._drag = "pan"
            self._last = pt
        self.mouseMoveEvent(e)

    def mouseMoveEvent(self, e):
        pt = e.position()
        if self._drag == "div":
            f = self._full()
            self.pos = min(0.99, max(0.01, (pt.x() - f.left()) / max(1.0, f.width())))
            self.update()
        elif self._drag == "pan":
            if self.z > 1.001:
                self.pan = self.pan + (pt - self._last)
                self.update()
            self._last = pt
        else:
            self.setCursor(Qt.CursorShape.SplitHCursor if self._near_line(pt) else Qt.CursorShape.OpenHandCursor)

    def mouseReleaseEvent(self, e):
        self._drag = None

    def mouseDoubleClickEvent(self, e):
        self.z = 1.0
        self.pan = QPointF(0, 0)
        self.update()

    def wheelEvent(self, e):
        dy = e.angleDelta().y()
        if not dy:
            return
        nz = min(16.0, max(1.0, self.z * (1.15 ** (dy / 120.0))))
        if nz == self.z:
            return
        if self.mode == "split":                                       # zoom towards the mouse
            c = self._full().center()
            cur = e.position()
            self.pan = (cur - c) - (cur - c - self.pan) * (nz / self.z)
        else:
            self.pan = self.pan * (nz / self.z)
        self.z = nz
        if self.z <= 1.001:
            self.z, self.pan = 1.0, QPointF(0, 0)
        self.update()


class CompareDialog(QDialog):
    def __init__(self, orig, edited, name, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Original vs Edited  -  " + name)
        v = QVBoxLayout(self)
        top = QHBoxLayout()
        self.view = CompareView(orig, edited)
        grp = QButtonGroup(self)
        grp.setExclusive(True)
        for i, (text, mode) in enumerate((("Slider", "split"), ("Side by side", "side"))):
            b = QPushButton(text)
            b.setCheckable(True)
            b.setChecked(i == 0)
            grp.addButton(b, i)
            b.clicked.connect(lambda _=False, m=mode: self.view.set_mode(m))
            top.addWidget(b)
        hint = QLabel("Slider: drag the line.   Mouse wheel = zoom,  drag = move,  double-click = back to fit.")
        hint.setObjectName("muted")
        top.addSpacing(12)
        top.addWidget(hint, 1)
        close = QPushButton("Close")
        close.clicked.connect(self.accept)
        top.addWidget(close)
        v.addLayout(top)
        v.addWidget(self.view, 1)
        scr = QApplication.primaryScreen()
        if scr is not None:
            g = scr.availableGeometry()
            self.resize(int(g.width() * 0.82), int(g.height() * 0.82))
