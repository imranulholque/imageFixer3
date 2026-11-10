"""Real-quality preview: shows the picture exactly as Save / Copy / drag-out will give it (every real pixel).

While you edit, the program works on a smaller copy of a big picture, so it stays fast.  This overlay lies on the
picture area and draws the FULL-resolution result at the same place, zoom and position as the working copy (wheel and
drag keep working, because the overlay lets the mouse through).  Zoom to 100 % (Ctrl+1) to see the real pixels."""
from PySide6.QtCore import QPointF, QRectF, Qt, QTimer
from PySide6.QtGui import QColor, QPainter
from PySide6.QtWidgets import QWidget


class FullViewOverlay(QWidget):
    def __init__(self, canvas, qimg, size):
        super().__init__(canvas)
        self.canvas = canvas
        self.img = qimg                               # QImage: the full-resolution result
        self.size_px = size                           # (w, h) of that result
        self._sig = None
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
        self.setAttribute(Qt.WidgetAttribute.WA_NoSystemBackground, True)
        self.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        canvas.installEventFilter(self)
        self.setGeometry(canvas.rect())
        self._timer = QTimer(self)                    # repaint when the picture is zoomed / moved underneath
        self._timer.setInterval(30)
        self._timer.timeout.connect(self._poll)
        self._timer.start()
        self.show()
        self.raise_()

    def eventFilter(self, obj, e):
        from PySide6.QtCore import QEvent
        if obj is self.canvas and e.type() == QEvent.Type.Resize:
            self.setGeometry(self.canvas.rect())
        return False

    def _view_rect(self):
        c = self.canvas
        return QRectF(c.off.x(), c.off.y(), c._iw * c.zoom, c._ih * c.zoom)

    def _poll(self):
        c = self.canvas
        sig = (round(c.zoom, 4), round(c.off.x(), 1), round(c.off.y(), 1), c._iw, c._ih, self.width(), self.height())
        if sig != self._sig:
            self._sig = sig
            self.update()

    def real_percent(self):
        """Zoom of the real picture in percent (100 = one picture pixel per screen pixel)."""
        vr = self._view_rect()
        return 100.0 * vr.width() / max(1, self.img.width())

    def paintEvent(self, e):
        if self.img is None or self.img.isNull():
            return
        vr = self._view_rect()
        if vr.width() < 1 or vr.height() < 1:
            return
        vis = vr.intersected(QRectF(self.rect()))
        if vis.isEmpty():
            return
        sx = self.img.width() / vr.width()
        sy = self.img.height() / vr.height()
        src = QRectF((vis.left() - vr.left()) * sx, (vis.top() - vr.top()) * sy, vis.width() * sx, vis.height() * sy)
        p = QPainter(self)
        # real pixels stay square when zoomed in; a smooth shrink when more picture pixels than screen pixels
        smooth = sx > 1.0 and self.img.width() * self.img.height() <= 40_000_000
        p.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform, bool(smooth))
        p.drawImage(vis, self.img, src)
        text = "Real quality  %d × %d px  ·  %d%%   (what Save, Copy and drag give)" % (
            self.size_px[0], self.size_px[1], round(self.real_percent()))
        fm = p.fontMetrics()
        w, h = fm.horizontalAdvance(text) + 24, fm.height() + 10
        r = QRectF(self.width() / 2.0 - w / 2.0, 10, w, h)
        p.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QColor(10, 12, 16, 215))
        p.drawRoundedRect(r, 8, 8)
        p.setPen(QColor("white"))
        p.drawText(r, int(Qt.AlignmentFlag.AlignCenter), text)

    def close_overlay(self):
        self._timer.stop()
        try:
            self.canvas.removeEventFilter(self)
        except Exception:
            pass
        self.hide()
        self.deleteLater()
