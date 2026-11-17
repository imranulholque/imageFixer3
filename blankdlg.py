"""A blank page of one solid colour - like a new page in Picsart or MS Word.

Pick any colour and a size, then either
  * open it as a NEW page (a new picture in the row, ready to work on), or
  * put it on the page you are working on as an OVERLAY picture (a layer you can move, resize, turn, fade).
"""
from PIL import Image
from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QColor
from PySide6.QtWidgets import (QColorDialog, QComboBox, QDialog, QGridLayout, QHBoxLayout, QLabel, QPushButton, QSpinBox,
                               QVBoxLayout, QWidget)

# (label, hex).  The first row are the everyday paper colours; "Other color ..." opens the full colour picker.
PRESETS = [("White", "#ffffff"), ("Black", "#000000"), ("Light grey", "#d9d9d9"), ("Dark grey", "#3a3a3a"),
           ("Cream", "#f5efdc"), ("Red", "#e03131"), ("Orange", "#f08c00"), ("Yellow", "#fcc419"),
           ("Green", "#2f9e44"), ("Blue", "#1c7ed6"), ("Purple", "#7048e8"), ("Pink", "#e64980")]

# (label, width px, height px).  Paper sizes at 200 dpi, like the pages of a PDF are drawn.
SIZES = [("A4 portrait  (1654 x 2339)", 1654, 2339), ("A4 landscape  (2339 x 1654)", 2339, 1654),
         ("A5 portrait  (1169 x 1654)", 1169, 1654), ("Letter portrait  (1700 x 2200)", 1700, 2200),
         ("Square  (1080 x 1080)", 1080, 1080), ("Full HD  (1920 x 1080)", 1920, 1080),
         ("Phone story  (1080 x 1920)", 1080, 1920), ("ID card page, A4 at 300 dpi  (2480 x 3508)", 2480, 3508)]
MAX_SIDE = 8000
MIN_SIDE = 16


def make_page(rgb, size):
    """A solid page: PIL RGB of the given colour and (width, height)."""
    w = max(MIN_SIDE, min(MAX_SIDE, int(size[0])))
    h = max(MIN_SIDE, min(MAX_SIDE, int(size[1])))
    return Image.new("RGB", (w, h), tuple(int(v) for v in rgb)[:3])


class BlankPageDialog(QDialog):
    """newPage(rgb, (w, h))       - open it as a new page.
    newOverlay(rgb, (w, h) | None) - put it on the current page; None = exactly as big as that page (fills it)."""
    newPage = Signal(tuple, tuple)
    newOverlay = Signal(tuple, object)

    def __init__(self, parent=None, page_size=None, can_overlay=False, start_rgb=(255, 255, 255)):
        """page_size = (w, h) of the page that is open now (or None);  can_overlay = a page is open to put it on."""
        super().__init__(parent)
        self.setWindowTitle("New blank page")
        self.setModal(True)
        self._rgb = tuple(start_rgb)
        self._page_size = tuple(page_size) if page_size else None
        root = QVBoxLayout(self)
        root.setSpacing(10)
        root.addWidget(QLabel("Choose a color and a size. The page is one solid color - draw, paste, type or place pictures on it."))

        # --- colour
        root.addWidget(QLabel("Page color"))
        grid = QGridLayout()
        grid.setSpacing(6)
        self._swatches = []
        for i, (label, hx) in enumerate(PRESETS):
            b = QPushButton("")
            b.setFixedSize(46, 30)
            b.setToolTip(label)
            b.setCursor(Qt.CursorShape.PointingHandCursor)
            b.clicked.connect(lambda _=False, h=hx: self._set_color(_hex_rgb(h)))
            b.setStyleSheet("QPushButton { background: %s; border: 1px solid #4a5268; border-radius: 6px; }"
                            "QPushButton:hover { border: 2px solid #a5b4fc; }" % hx)
            grid.addWidget(b, i // 6, i % 6)
            self._swatches.append(b)
        root.addLayout(grid)
        row = QHBoxLayout()
        self.btnOther = QPushButton("Other color ...")
        self.btnOther.setToolTip("Any color you like")
        self.btnOther.clicked.connect(self._pick_other)
        self.lblNow = QLabel("")
        self.lblNow.setMinimumHeight(30)
        self.lblNow.setMinimumWidth(150)
        row.addWidget(self.btnOther)
        row.addWidget(self.lblNow, 1)
        root.addLayout(row)

        # --- size
        root.addWidget(QLabel("Size"))
        self.cmbSize = QComboBox()
        if self._page_size:
            self.cmbSize.addItem("Same as this page  (%d x %d)" % self._page_size, "same")
        for label, w, h in SIZES:
            self.cmbSize.addItem(label, (w, h))
        self.cmbSize.addItem("Custom size ...", "custom")
        self.cmbSize.currentIndexChanged.connect(self._size_changed)
        root.addWidget(self.cmbSize)
        self.customRow = QWidget()
        cr = QHBoxLayout(self.customRow)
        cr.setContentsMargins(0, 0, 0, 0)
        self.spW = QSpinBox()
        self.spH = QSpinBox()
        for sp, v in ((self.spW, 1654), (self.spH, 2339)):
            sp.setRange(MIN_SIDE, MAX_SIDE)
            sp.setValue(v)
            sp.setSuffix(" px")
            sp.setKeyboardTracking(False)
        cr.addWidget(QLabel("Width"))
        cr.addWidget(self.spW)
        cr.addWidget(QLabel("Height"))
        cr.addWidget(self.spH)
        cr.addStretch(1)
        root.addWidget(self.customRow)
        self.customRow.setVisible(False)

        # --- buttons
        btns = QHBoxLayout()
        self.btnPage = QPushButton("New page")
        self.btnPage.setProperty("accent", True)
        self.btnPage.setToolTip("Open the blank page as a new picture in the row")
        self.btnPage.clicked.connect(self._make_page)
        self.btnOver = QPushButton("Add on this page (overlay)")
        self.btnOver.setToolTip("Put the colour on the page you are working on, as a layer: move, resize, turn, change its opacity")
        self.btnOver.setEnabled(bool(can_overlay))
        self.btnOver.clicked.connect(self._make_overlay)
        self.btnCancel = QPushButton("Cancel")
        self.btnCancel.clicked.connect(self.reject)
        btns.addStretch(1)
        btns.addWidget(self.btnCancel)
        btns.addWidget(self.btnOver)
        btns.addWidget(self.btnPage)
        root.addLayout(btns)
        self.setMinimumWidth(430)
        self._paint_now()

    # ------------------------------------------------------------------ colour
    def _set_color(self, rgb):
        self._rgb = tuple(int(v) for v in rgb)
        self._paint_now()

    def _pick_other(self):
        c = QColorDialog.getColor(QColor(*self._rgb), self, "Choose a page color")
        if c.isValid():
            self._set_color((c.red(), c.green(), c.blue()))

    def _paint_now(self):
        r, g, b = self._rgb
        dark = (0.299 * r + 0.587 * g + 0.114 * b) < 128
        self.lblNow.setText("#%02X%02X%02X" % self._rgb)
        self.lblNow.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.lblNow.setStyleSheet("background: rgb(%d,%d,%d); color: %s; border: 1px solid #4a5268; border-radius: 6px;"
                                  % (r, g, b, "#ffffff" if dark else "#000000"))

    # -------------------------------------------------------------------- size
    def _size_changed(self, _i=0):
        self.customRow.setVisible(self.cmbSize.currentData() == "custom")

    def _size(self):
        """(w, h) in pixels, or None = 'same as this page'."""
        d = self.cmbSize.currentData()
        if d == "same":
            return None
        if d == "custom":
            return (self.spW.value(), self.spH.value())
        return tuple(d)

    def _make_page(self):
        sz = self._size() or self._page_size or (1654, 2339)
        self.newPage.emit(self._rgb, tuple(sz))
        self.accept()

    def _make_overlay(self):
        self.newOverlay.emit(self._rgb, self._size())          # None = as big as the page (fills it)
        self.accept()


def _hex_rgb(h):
    t = h.lstrip("#")
    return (int(t[0:2], 16), int(t[2:4], 16), int(t[4:6], 16))
