"""Click the number next to a slider, type a value, press Enter: the slider jumps to exactly that value.

Use:   valuedit.attach(label, slider)
The label keeps whatever text the program puts into it ("30 px", "22%", "4" ...).  Nothing else has to change: the typed
number is simply given to the slider (clamped to its range), so every existing "valueChanged" handler runs as usual.
Enter / click elsewhere = apply, Esc = cancel.  Bangla digits (০-৯) work too.  Up / Down arrow = +1 / -1 (Shift = 10)."""
import re

from PySide6.QtCore import QEvent, QObject, Qt
from PySide6.QtWidgets import QLineEdit

_NUM = re.compile(r"-?\d+(?:\.\d+)?")
_BN = str.maketrans("০১২৩৪৫৬৭৮৯", "0123456789")
_STYLE = ("QLineEdit { background: #10141f; border: 1px solid #818cf8; border-radius: 8px; padding: 0px 6px;"
          " color: #e5e7eb; font-weight: 600; selection-background-color: #6366f1; }")


def parse_number(text):
    """First number inside the text ('28 px', '50%', ' -12 ', '৩০') -> float, or None."""
    m = _NUM.search(str(text).translate(_BN).replace(",", "."))
    return float(m.group(0)) if m else None


class _Editor(QLineEdit):
    def __init__(self, label, slider):
        super().__init__(label.parentWidget() or label)
        self._label, self._sl, self._done = label, slider, False
        self.setStyleSheet(_STYLE)
        self.setMaxLength(8)
        self.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        self.setToolTip("Type a number (%d - %d).  Enter = apply,  Esc = cancel." % (slider.minimum(), slider.maximum()))
        w = max(label.width(), 58)
        h = max(label.height(), 24)
        self.setGeometry(label.x() + label.width() - w, label.y() + (label.height() - h) // 2, w, h)
        self.setText(str(slider.value()))
        self.raise_()
        self.show()
        self.setFocus(Qt.FocusReason.MouseFocusReason)
        self.selectAll()

    def _finish(self, apply):
        if self._done:
            return
        self._done = True
        if apply:
            v = parse_number(self.text())
            if v is not None:
                self._sl.setValue(int(max(self._sl.minimum(), min(self._sl.maximum(), round(v)))))
        self.hide()
        self.deleteLater()

    def event(self, e):
        # the window has its own Enter / Esc shortcuts (apply crop / cancel): while typing, these keys belong to us
        if e.type() == QEvent.Type.ShortcutOverride and e.key() in (
                Qt.Key.Key_Return, Qt.Key.Key_Enter, Qt.Key.Key_Escape, Qt.Key.Key_Up, Qt.Key.Key_Down):
            e.accept()
            return True
        return super().event(e)

    def keyPressEvent(self, e):
        k = e.key()
        if k in (Qt.Key.Key_Return, Qt.Key.Key_Enter):
            self._finish(True)
            e.accept()
        elif k == Qt.Key.Key_Escape:
            self._finish(False)
            e.accept()
        elif k in (Qt.Key.Key_Up, Qt.Key.Key_Down):
            step = 10 if (e.modifiers() & Qt.KeyboardModifier.ShiftModifier) else 1
            v = parse_number(self.text())
            v = self._sl.value() if v is None else v
            v = max(self._sl.minimum(), min(self._sl.maximum(), int(round(v)) + (step if k == Qt.Key.Key_Up else -step)))
            self._sl.setValue(v)                              # live: the picture follows while the arrows are used
            self.setText(str(v))
            self.selectAll()
            e.accept()
        else:
            super().keyPressEvent(e)

    def focusOutEvent(self, e):
        super().focusOutEvent(e)
        self._finish(True)


class _Click(QObject):
    def __init__(self, label, slider):
        super().__init__(label)
        self._label, self._sl = label, slider

    def eventFilter(self, obj, e):
        if e.type() == QEvent.Type.MouseButtonRelease and e.button() == Qt.MouseButton.LeftButton:
            if self._label.isVisible() and self._label.isEnabled() and self._sl.isEnabled():
                self.open()
            return True
        return False

    def open(self):
        for old in (self._label.parentWidget() or self._label).findChildren(_Editor):
            old._finish(True)
        _Editor(self._label, self._sl)


def attach(label, slider):
    """Make `label` (the number shown next to `slider`) clickable: click, type a number, Enter."""
    if getattr(label, "_valuedit", None) is not None:
        return label
    label._valuedit = _Click(label, slider)
    label.installEventFilter(label._valuedit)
    label.setCursor(Qt.CursorShape.IBeamCursor)
    if not label.toolTip():
        label.setToolTip("Click and type a number")
    return label
