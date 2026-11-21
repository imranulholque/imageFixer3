"""Page numbers: ONE setting for everything.

  * the main window's "Numbers" tab (right panel, every picture)
  * the print screen (PC Print page, see printweb.py)  - the two follow each other live
  * "All pages as one PDF"
The values live in QSettings ("pagenum/...").  NumStore tells everybody when they change.
"""
from PySide6.QtCore import QObject, QSettings, Signal
from PySide6.QtWidgets import QCheckBox, QComboBox, QGridLayout, QLabel, QSpinBox, QVBoxLayout, QWidget

import printlayout as pl

WEB_KEYS = ("on", "pos", "align", "size", "start", "bold", "margin")      # what the PC Print page knows


def load_numspec(settings):
    g = lambda k, d, t: settings.value("pagenum/" + k, d, type=t)
    return pl.NumSpec(on=g("on", False, bool), pos=g("pos", "bottom", str), align=g("align", "center", str),
                      size=g("size", 12.0, float), start=g("start", 1, int), margin=g("margin", 8.0, float),
                      bold=g("bold", False, bool), keep_own=g("keep_own", True, bool))


def _write(settings, spec):
    for k, v in spec.to_dict().items():
        settings.setValue("pagenum/" + k, v)


def save_numspec(settings, spec):
    """Saves the page numbers and tells every open panel / the print screen."""
    store = NumStore._inst
    if store is not None:
        store.set_spec(spec)
    else:
        _write(settings, spec)


def spec_to_web(spec):
    d = spec.to_dict()
    return {k: d[k] for k in WEB_KEYS}


def spec_from_web(d, base):
    """The page's numbers -> a NumSpec; 'keep each page's own number' is not on that page, so it stays as it was."""
    return pl.NumSpec(on=d.get("on", base.on), pos=d.get("pos", base.pos), align=d.get("align", base.align),
                      size=d.get("size", base.size), start=d.get("start", base.start), margin=d.get("margin", base.margin),
                      bold=d.get("bold", base.bold), keep_own=base.keep_own)


class NumStore(QObject):
    """The one place that knows the page number setting. changed(source): who changed it (None = unknown)."""
    changed = Signal(object)
    _inst = None

    def __init__(self, settings):
        super().__init__()
        self.settings = settings

    @classmethod
    def get(cls):
        if cls._inst is None:
            cls._inst = NumStore(QSettings("ImageFixer", "ImageFixer"))
        return cls._inst

    def spec(self):
        return load_numspec(self.settings)

    def set_spec(self, spec, source=None):
        if self.spec().to_dict() == spec.to_dict():
            return
        _write(self.settings, spec)
        self.changed.emit(source)


class PageNumberPanel(QWidget):
    """The page number settings: on / off, top or bottom, left / centre / right, size, first number, distance, bold."""
    changed = Signal()

    def __init__(self, spec, show_keep_own=False, parent=None):
        super().__init__(parent)
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(6)
        self.chkOn = QCheckBox("Print page numbers")
        root.addWidget(self.chkOn)
        self.body = QWidget()
        g = QGridLayout(self.body)
        g.setContentsMargins(0, 0, 0, 0)
        g.setHorizontalSpacing(8)
        g.setVerticalSpacing(5)
        self.cbPos = QComboBox()
        self.cbPos.addItem("Bottom", "bottom")
        self.cbPos.addItem("Top", "top")
        self.cbAlign = QComboBox()
        for t, v in (("Left", "left"), ("Center", "center"), ("Right", "right")):
            self.cbAlign.addItem(t, v)
        self.spSize = QSpinBox()
        self.spSize.setRange(6, 72)
        self.spSize.setSuffix(" pt")
        self.spStart = QSpinBox()
        self.spStart.setRange(1, 99999)
        self.spMargin = QSpinBox()
        self.spMargin.setRange(2, 40)
        self.spMargin.setSuffix(" mm")
        self.chkBold = QCheckBox("Bold")
        self.chkOwn = QCheckBox("Keep each page's own number when only some pages are ticked")
        self.chkOwn.setToolTip("On: page 3 of the list always gets the number 3, even when only that page is printed.\n"
                               "Off: the printed pages are numbered 1, 2, 3 ... one after another.")
        rows = (("Place", self.cbPos), ("Side", self.cbAlign), ("Size", self.spSize), ("First number", self.spStart),
                ("Distance from edge", self.spMargin))
        for r, (t, w) in enumerate(rows):
            g.addWidget(QLabel(t), r, 0)
            g.addWidget(w, r, 1)
        g.addWidget(self.chkBold, len(rows), 0, 1, 2)
        if show_keep_own:
            g.addWidget(self.chkOwn, len(rows) + 1, 0, 1, 2)
        else:
            self.chkOwn.hide()
        g.setColumnStretch(1, 1)
        root.addWidget(self.body)
        self.set_spec(spec)
        self.chkOn.toggled.connect(self._edited)
        for w in (self.cbPos, self.cbAlign):
            w.currentIndexChanged.connect(self._edited)
        for w in (self.spSize, self.spStart, self.spMargin):
            w.valueChanged.connect(self._edited)
        for w in (self.chkBold, self.chkOwn):
            w.toggled.connect(self._edited)

    def _edited(self, *_):
        self.body.setEnabled(self.chkOn.isChecked())
        self.changed.emit()

    def set_spec(self, spec):
        for w in (self.chkOn, self.cbPos, self.cbAlign, self.spSize, self.spStart, self.spMargin, self.chkBold, self.chkOwn):
            w.blockSignals(True)
        self.chkOn.setChecked(spec.on)
        self.cbPos.setCurrentIndex(max(0, self.cbPos.findData(spec.pos)))
        self.cbAlign.setCurrentIndex(max(0, self.cbAlign.findData(spec.align)))
        self.spSize.setValue(int(round(spec.size)))
        self.spStart.setValue(spec.start)
        self.spMargin.setValue(int(round(spec.margin)))
        self.chkBold.setChecked(spec.bold)
        self.chkOwn.setChecked(spec.keep_own)
        for w in (self.chkOn, self.cbPos, self.cbAlign, self.spSize, self.spStart, self.spMargin, self.chkBold, self.chkOwn):
            w.blockSignals(False)
        self.body.setEnabled(spec.on)

    def spec(self):
        return pl.NumSpec(on=self.chkOn.isChecked(), pos=self.cbPos.currentData(), align=self.cbAlign.currentData(),
                          size=self.spSize.value(), start=self.spStart.value(), margin=self.spMargin.value(),
                          bold=self.chkBold.isChecked(), keep_own=self.chkOwn.isChecked())


class NumberTab(QWidget):
    """The 'Numbers' tab of the right panel. Same setting as the print screen: change it here or there, both follow."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.store = NumStore.get()
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 4, 4, 4)
        lay.setSpacing(10)
        note = QLabel("Page numbers for Print (Ctrl+P) and \"All pages as one PDF\". Pages are numbered in the order of "
                      "the thumbnails. The print screen has the same setting: change it here or there, both follow at once.")
        note.setWordWrap(True)
        lay.addWidget(note)
        self.panel = PageNumberPanel(self.store.spec())
        lay.addWidget(self.panel)
        lay.addStretch(1)
        self.panel.changed.connect(self._edited)
        self.store.changed.connect(self._follow)

    def _edited(self):
        self.store.set_spec(self.panel.spec(), source=self)

    def _follow(self, source):
        if source is not self:
            self.panel.set_spec(self.store.spec())
