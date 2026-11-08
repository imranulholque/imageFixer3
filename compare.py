"""Original vs edited, IN the picture: a layer over the picture that shows the original on the left of a line you drag
(Slider), or both side by side.  Click Compare again and it is gone.  Slider mode follows the picture's zoom and
position (wheel / drag move the picture, the original moves with it)."""
from PySide6.QtCore import QEvent, QPointF, QRectF, QSizeF, Qt, QTimer
from PySide6.QtGui import QColor, QPainter, QPen
from PySide6.QtWidgets import QApplication, QWidget


class CompareOverlay(QWidget):
    def __init__(self, canvas, orig):
        super().__init__(canvas)
        self.canvas = canvas
        self.a = orig                                # QImage: the original
        self.mode = "slider"                         # 'slider' | 'side'
        self.pos = 0.5                               # the line, 0..1 of the picture's width (slider)
        self.z = 1.0                                 # side by side: own zoom / move
        self.pan = QPointF(0, 0)
        self._drag = None
        self._last = None
        self._sig = None
        self.setMouseTracking(True)
        self.setAttribute(Qt.WidgetAttribute.WA_NoSystemBackground, True)
        self.setAttribute(Qt.WidgetAttribute.WA_OpaquePaintEvent, False)
        self.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        canvas.installEventFilter(self)
        self.setGeometry(canvas.rect())
        self._timer = QTimer(self)                   # repaint when the picture is moved / zoomed / changed underneath
        self._timer.setInterval(30)
        self._timer.timeout.connect(self._poll)
        self._timer.start()
        self.show()
        self.raise_()

    def set_mode(self, m):
        self.mode = m
        self.z, self.pan = 1.0, QPointF(0, 0)
        self.update()

    def eventFilter(self, obj, e):
        if obj is self.canvas and e.type() == QEvent.Type.Resize:
            self.setGeometry(self.canvas.rect())
        return False

    # ---- geometry
    def _edited(self):
        return self.canvas._img

    def _view_rect(self):
        """Where the canvas shows the edited picture (widget coordinates)."""
        c = self.canvas
        return QRectF(c.off.x(), c.off.y(), c._iw * c.zoom, c._ih * c.zoom)

    @staticmethod
    def _fit(img, area):
        s = min(area.width() / max(1, img.width()), area.height() / max(1, img.height()))
        w, h = img.width() * s, img.height() * s
        return QRectF(area.center().x() - w / 2, area.center().y() - h / 2, w, h)

    def _poll(self):
        c = self.canvas
        img = self._edited()
        sig = (round(c.zoom, 4), round(c.off.x(), 1), round(c.off.y(), 1), None if img is None else img.cacheKey(), self.mode)
        if sig != self._sig:
            self._sig = sig
            self.update()

    def _line_x(self):
        r = self._view_rect()
        return r.left() + r.width() * self.pos

    def _areas(self):
        full = QRectF(self.rect()).adjusted(8, 8, -8, -8)
        half = full.width() / 2.0
        return (QRectF(full.left(), full.top(), half - 4, full.height()),
                QRectF(full.left() + half + 4, full.top(), half - 4, full.height()))

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
        p.setBrush(QColor(10, 12, 16, 215))
        p.drawRoundedRect(r, 8, 8)
        p.setPen(QColor("white"))
        p.drawText(r, int(Qt.AlignmentFlag.AlignCenter), text)

    def paintEvent(self, e):
        b = self._edited()
        if b is None or self.a is None:
            return
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform, True)
        p.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        if self.mode == "slider":
            vr = self._view_rect()
            ra = self._fit(self.a, vr)
            x = self._line_x()
            p.save()
            p.setClipRect(QRectF(vr.left(), vr.top(), max(0.0, x - vr.left()), vr.height()).intersected(QRectF(self.rect())))
            p.drawImage(ra, self.a)
            p.restore()
            top, bot = max(0.0, vr.top()), min(float(self.height()), vr.bottom())
            p.setPen(QPen(QColor(0, 0, 0, 160), 4))
            p.drawLine(QPointF(x, top), QPointF(x, bot))
            p.setPen(QPen(QColor(255, 255, 255), 2))
            p.drawLine(QPointF(x, top), QPointF(x, bot))
            cy = min(max((top + bot) / 2, 30.0), self.height() - 30.0)
            p.setBrush(QColor("#6366f1"))
            p.drawEllipse(QPointF(x, cy), 16, 16)
            p.drawText(QRectF(x - 16, cy - 16, 32, 32), int(Qt.AlignmentFlag.AlignCenter), "\u25c2\u25b8")
            self._tag(p, "Original", max(vr.left(), 0.0) + 10, max(vr.top(), 0.0) + 10)
            self._tag(p, "Edited", min(vr.right(), float(self.width())) - 10, max(vr.top(), 0.0) + 10, right=True)
        else:
            p.fillRect(self.rect(), QColor("#0a0c10"))
            al, ar = self._areas()
            for area, img, name in ((al, self.a, "Original"), (ar, b, "Edited")):
                p.save()
                p.setClipRect(area)
                p.drawImage(self._xf(self._fit(img, area), area), img)
                p.restore()
                self._tag(p, name, area.left() + 8, area.top() + 8)
        p.end()

    # ---- mouse
    def _near_line(self, pt):
        return self.mode == "slider" and abs(pt.x() - self._line_x()) <= 16

    def _forward(self, e):
        QApplication.sendEvent(self.canvas, e)
        self.update()

    def mousePressEvent(self, e):
        pt = e.position()
        if self.mode == "slider":
            if e.button() == Qt.MouseButton.LeftButton and self._near_line(pt):
                self._drag = "line"
                self.mouseMoveEvent(e)
            else:
                self._drag = "canvas"
                self._forward(e)
            return
        if e.button() == Qt.MouseButton.LeftButton:
            self._drag, self._last = "pan", pt

    def mouseMoveEvent(self, e):
        pt = e.position()
        if self._drag == "line":
            r = self._view_rect()
            self.pos = min(0.99, max(0.01, (pt.x() - r.left()) / max(1.0, r.width())))
            self.update()
        elif self._drag == "canvas":
            self._forward(e)
        elif self._drag == "pan":
            if self.z > 1.001:
                self.pan = self.pan + (pt - self._last)
                self.update()
            self._last = pt
        elif self.mode == "slider":
            if self._near_line(pt):
                self.setCursor(Qt.CursorShape.SplitHCursor)
            else:
                self.unsetCursor()
                self._forward(e)
        else:
            self.setCursor(Qt.CursorShape.OpenHandCursor)

    def mouseReleaseEvent(self, e):
        if self._drag == "canvas":
            self._forward(e)
        self._drag = None

    def mouseDoubleClickEvent(self, e):
        if self.mode == "side":
            self.z, self.pan = 1.0, QPointF(0, 0)
            self.update()

    def wheelEvent(self, e):
        if self.mode == "slider":
            self._forward(e)
            return
        dy = e.angleDelta().y()
        if not dy:
            return
        nz = min(16.0, max(1.0, self.z * (1.15 ** (dy / 120.0))))
        self.pan = self.pan * (nz / self.z)
        self.z = nz
        if self.z <= 1.001:
            self.z, self.pan = 1.0, QPointF(0, 0)
        self.update()

    def close_overlay(self):
        self._timer.stop()
        try:
            self.canvas.removeEventFilter(self)
        except Exception:
            pass
        self.hide()
        self.deleteLater()
