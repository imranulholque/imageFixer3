"""A layout that puts widgets in a row and wraps to the next row when the window is narrow,
so no button is ever cut off or hidden.  Also: a label that shortens long text with '...'."""
from PySide6.QtCore import QPoint, QRect, QSize, Qt
from PySide6.QtGui import QFontMetrics
from PySide6.QtWidgets import QLabel, QLayout, QSizePolicy, QSpacerItem


class FlowLayout(QLayout):
    """Left to right, top to bottom.  addStretch() is accepted (and ignored) so it can replace a QHBoxLayout."""

    def __init__(self, parent=None, hspacing=6, vspacing=6):
        super().__init__(parent)
        self._items = []
        self._hs = hspacing
        self._vs = vspacing
        self.setContentsMargins(0, 0, 0, 0)

    # --- QLayout interface
    def addItem(self, item):
        self._items.append(item)
        self.invalidate()

    def addSpacing(self, n):
        self._items.append(QSpacerItem(int(n), 0, QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Minimum))
        self.invalidate()

    def addStretch(self, _n=0):
        pass

    def setSpacing(self, n):
        self._hs = self._vs = int(n)
        self.invalidate()

    def spacing(self):
        return self._hs

    def count(self):
        return len(self._items)

    def itemAt(self, i):
        return self._items[i] if 0 <= i < len(self._items) else None

    def takeAt(self, i):
        if 0 <= i < len(self._items):
            it = self._items.pop(i)
            self.invalidate()
            return it
        return None

    def expandingDirections(self):
        return Qt.Orientation(0)

    def hasHeightForWidth(self):
        return True

    def heightForWidth(self, width):
        return self._layout(QRect(0, 0, width, 0), True)

    def setGeometry(self, rect):
        super().setGeometry(rect)
        self._layout(rect, False)

    def sizeHint(self):
        """Natural size = everything in one row (so a flow inside a flow asks for its full width first)."""
        w = h = 0
        n = 0
        for it in self._items:
            if self._skip(it):
                continue
            s = it.sizeHint()
            w += s.width()
            h = max(h, s.height())
            n += 1
        m = self.contentsMargins()
        if n > 1:
            w += self._hs * (n - 1)
        return QSize(w + m.left() + m.right(), h + m.top() + m.bottom())

    def minimumSize(self):
        """Smallest = the widest single item (everything else can wrap)."""
        w = h = 0
        for it in self._items:
            if self._skip(it):
                continue
            s = it.minimumSize()
            w = max(w, s.width())
            h = max(h, s.height())
        m = self.contentsMargins()
        return QSize(w + m.left() + m.right(), h + m.top() + m.bottom())

    # --- the work
    @staticmethod
    def _skip(it):
        wd = it.widget()
        return wd is not None and wd.isHidden()

    def _layout(self, rect, test_only):
        m = self.contentsMargins()
        x0 = rect.x() + m.left()
        y = rect.y() + m.top()
        avail = max(1, rect.width() - m.left() - m.right())
        x = x0
        line_h = 0
        for it in self._items:
            if self._skip(it):
                continue
            hint = it.sizeHint()
            w = min(hint.width(), avail)                  # an item wider than the row is squeezed, never cut off
            h = it.heightForWidth(w) if it.hasHeightForWidth() else hint.height()
            if h < 0:
                h = hint.height()
            if x > x0 and (x - x0) + w > avail:           # does not fit: next row
                x = x0
                y += line_h + self._vs
                line_h = 0
            if not test_only:
                it.setGeometry(QRect(QPoint(x, y), QSize(w, h)))
            x += w + self._hs
            line_h = max(line_h, h)
        return y + line_h + m.bottom() - rect.y()


class ElidedLabel(QLabel):
    """A label that never forces the window wider: long text ends with '...' (full text in the tooltip)."""

    def __init__(self, text="", parent=None):
        super().__init__(parent)
        self._full = ""
        self.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Preferred)
        self.setText(text)

    def setText(self, text):
        self._full = text or ""
        self.setToolTip(self._full)
        self._elide()

    def text(self):
        return self._full

    def resizeEvent(self, e):
        super().resizeEvent(e)
        self._elide()

    def sizeHint(self):
        fm = QFontMetrics(self.font())
        return QSize(fm.horizontalAdvance(self._full) + 4, QLabel.sizeHint(self).height())

    def minimumSizeHint(self):
        return QSize(48, QLabel.minimumSizeHint(self).height())

    def _elide(self):
        fm = QFontMetrics(self.font())
        w = max(10, self.width() - 2)
        QLabel.setText(self, fm.elidedText(self._full, Qt.TextElideMode.ElideMiddle, w))
