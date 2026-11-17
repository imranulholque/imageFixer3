"""A small drop-down to choose a colour: a few ready colours (for example Black / White) and 'Other color ...'.
Used where a solid colour is needed: the page of an ID card scan, the outside of a rounded crop."""
from PySide6.QtCore import Signal
from PySide6.QtGui import QColor, QPixmap, QIcon
from PySide6.QtWidgets import QColorDialog, QComboBox


def _icon(rgb):
    pm = QPixmap(16, 16)
    pm.fill(QColor(*rgb) if rgb is not None else QColor(205, 209, 219))
    return QIcon(pm)


class ColorChoice(QComboBox):
    """presets = [(label, (r, g, b) or None), ...].  None = 'automatic' (the caller decides, e.g. the paper colour).
    rgb() -> the chosen (r, g, b) or None.  changed is sent when the person picks something."""
    changed = Signal()

    def __init__(self, presets, parent=None):
        super().__init__(parent)
        self._custom = (128, 128, 128)
        self._last = 0
        for label, rgb in presets:
            self.addItem(_icon(rgb), label, rgb)
        self.addItem(_icon(self._custom), "Other color ...", "other")
        self.currentIndexChanged.connect(self._picked)

    def _picked(self, i):
        d = self.itemData(i)
        if d == "other":
            c = QColorDialog.getColor(QColor(*self._custom), self, "Choose a color")
            if not c.isValid():                         # cancelled: back to what it was
                self.blockSignals(True)
                self.setCurrentIndex(self._last)
                self.blockSignals(False)
                return
            self._custom = (c.red(), c.green(), c.blue())
            self.setItemIcon(i, _icon(self._custom))
            self.setItemText(i, "Other: #%02X%02X%02X" % self._custom)
        self._last = i
        self.changed.emit()

    def rgb(self):
        d = self.currentData()
        return self._custom if d == "other" else d

    def set_rgb(self, rgb):
        """Select a colour from outside (saved settings); a colour that is not a preset becomes the 'Other' entry."""
        self.blockSignals(True)
        try:
            want = None if rgb is None else tuple(int(v) for v in rgb)
            for i in range(self.count()):
                d = self.itemData(i)
                if d != "other" and d == want:
                    self.setCurrentIndex(i)
                    self._last = i
                    return
            if want is not None:
                self._custom = want
                i = self.count() - 1
                self.setItemIcon(i, _icon(want))
                self.setItemText(i, "Other: #%02X%02X%02X" % want)
                self.setCurrentIndex(i)
                self._last = i
        finally:
            self.blockSignals(False)

    def hex(self):
        c = self.rgb()
        return "" if c is None else "#%02x%02x%02x" % tuple(c)

    def set_hex(self, text):
        try:
            t = str(text or "").lstrip("#")
            self.set_rgb((int(t[0:2], 16), int(t[2:4], 16), int(t[4:6], 16)))
        except Exception:
            pass
