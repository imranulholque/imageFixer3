"""Image Fixer's own print screen (Ctrl+P), so no other program has to be opened.

  * a live preview of every sheet (what you see is what is printed)
  * printer, paper, orientation, colour / black & white, two-sided, copies, scaling (fit / fill / actual size / custom %),
    several pages on one sheet (n-up), page numbers, tick / untick / reorder pages, added blank pages
  * Transform: move, resize, turn, copy (Alt + drag), paste, delete pictures on a sheet, drag them to another sheet,
    Shift + drag = dead straight, undo / redo, add a picture of another page onto a sheet
  * "Printer properties..." opens the driver's own window (trays, quality ...)
Pages are made and painted one by one in the background, so the window never hangs.
The page number panel (PageNumberPanel / PageNumberDialog / PdfOptionsDialog) is also used by the main window for
"All pages as one PDF".  The maths is in printlayout.py (no Qt, tested alone).
"""
import math
from collections import OrderedDict
from concurrent.futures import ThreadPoolExecutor

from PIL import Image
from PySide6.QtCore import QObject, QPointF, QRect, QRectF, QRunnable, QSize, Qt, QThreadPool, QTimer, Signal
from PySide6.QtGui import (QColor, QCursor, QFont, QIcon, QImage, QPageLayout, QPageSize, QPainter, QPen, QPixmap, QPolygonF)
from PySide6.QtPrintSupport import QPrintDialog, QPrinter, QPrinterInfo
from PySide6.QtWidgets import (QAbstractItemView, QCheckBox, QComboBox, QDialog, QFormLayout, QFrame,
                               QGridLayout, QGroupBox, QHBoxLayout, QLabel, QListWidget, QListWidgetItem, QMenu,
                               QMessageBox, QProgressBar, QPushButton, QScrollArea, QSpinBox, QToolButton, QVBoxLayout,
                               QWidget)

import printlayout as pl

MM = QPageLayout.Unit.Millimeter
ACCENT = QColor(37, 99, 235)


def pil_to_qimage(im):
    """PIL picture -> QImage (a copy, safe to hand to the window thread)."""
    if im.mode == "L":
        w, h = im.size
        return QImage(im.tobytes(), w, h, w, QImage.Format.Format_Grayscale8).copy()
    im = im.convert("RGB")
    w, h = im.size
    return QImage(im.tobytes(), w, h, 3 * w, QImage.Format.Format_RGB888).copy()


# ================================================================================================ page number settings
def load_numspec(settings):
    g = lambda k, d, t: settings.value("pagenum/" + k, d, type=t)
    return pl.NumSpec(on=g("on", False, bool), pos=g("pos", "bottom", str), align=g("align", "center", str),
                      size=g("size", 12.0, float), start=g("start", 1, int), margin=g("margin", 8.0, float),
                      bold=g("bold", False, bool), keep_own=g("keep_own", True, bool))


def save_numspec(settings, spec):
    for k, v in spec.to_dict().items():
        settings.setValue("pagenum/" + k, v)


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
        self.spStart.setRange(-9999, 99999)
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


class PageNumberDialog(QDialog):
    """Main window > More > Page numbers...  The same setting is used by the print screen and by 'All pages as one PDF'."""

    def __init__(self, settings, parent=None):
        super().__init__(parent)
        self.settings = settings
        self.setWindowTitle("Page numbers")
        self.setModal(True)
        root = QVBoxLayout(self)
        root.setSpacing(10)
        note = QLabel("The page number is printed on every page: in the print screen (Ctrl+P) and in "
                      "\"All pages as one PDF\". Pages are numbered in the order of the thumbnails.")
        note.setWordWrap(True)
        root.addWidget(note)
        self.panel = PageNumberPanel(load_numspec(settings), show_keep_own=True)
        root.addWidget(self.panel)
        row = QHBoxLayout()
        row.addStretch(1)
        ok, no = QPushButton("OK"), QPushButton("Cancel")
        ok.setProperty("accent", True)
        ok.setDefault(True)
        ok.clicked.connect(self._ok)
        no.clicked.connect(self.reject)
        row.addWidget(no)
        row.addWidget(ok)
        root.addLayout(row)
        self.setMinimumWidth(360)

    def _ok(self):
        save_numspec(self.settings, self.panel.spec())
        self.accept()


class PdfOptionsDialog(QDialog):
    """'All pages as one PDF': the page size and the page numbers in one small window."""
    MODES = (("Same shape as the picture (nothing cut or padded)", "fit"), ("A4 paper (turned to fit each picture)", "a4"),
             ("US Letter paper (turned to fit each picture)", "letter"))

    def __init__(self, settings, parent=None):
        super().__init__(parent)
        self.settings = settings
        self.setWindowTitle("All pages as one PDF")
        self.setModal(True)
        root = QVBoxLayout(self)
        root.setSpacing(10)
        root.addWidget(QLabel("PDF page size"))
        self.cbMode = QComboBox()
        for t, v in self.MODES:
            self.cbMode.addItem(t, v)
        last = settings.value("pdf_mode", "fit", type=str)
        self.cbMode.setCurrentIndex(max(0, self.cbMode.findData(last)))
        root.addWidget(self.cbMode)
        box = QGroupBox("Page numbers")
        bl = QVBoxLayout(box)
        self.panel = PageNumberPanel(load_numspec(settings), show_keep_own=False)
        bl.addWidget(self.panel)
        root.addWidget(box)
        row = QHBoxLayout()
        row.addStretch(1)
        ok, no = QPushButton("Save PDF..."), QPushButton("Cancel")
        ok.setProperty("accent", True)
        ok.setDefault(True)
        ok.clicked.connect(self._ok)
        no.clicked.connect(self.reject)
        row.addWidget(no)
        row.addWidget(ok)
        root.addLayout(row)
        self.setMinimumWidth(400)

    def _ok(self):
        self.settings.setValue("pdf_mode", self.mode())
        save_numspec(self.settings, self.spec())
        self.accept()

    def mode(self):
        return self.cbMode.currentData()

    def spec(self):
        return self.panel.spec()


# ================================================================================================ background work
class _PrepSignals(QObject):
    page = Signal(str)          # key of a page whose small copy for the screen is ready
    finished = Signal()


class _PrepJob(QRunnable):
    """Makes the small screen copy of every picture, one after the other (the window stays alive and fills in)."""

    def __init__(self, sig, pages, stop):
        super().__init__()
        self.sig, self.pages, self.stop = sig, pages, stop

    def run(self):
        try:
            for p in self.pages:
                if self.stop():
                    break
                if p.blank or p.preview is not None:
                    continue
                p.make_preview()
                self.sig.page.emit(p.key)
        finally:
            self.sig.finished.emit()


class _RenderSignals(QObject):
    ready = Signal(int, int, QImage)        # generation, sheet number, picture


class _RenderJob(QRunnable):
    def __init__(self, sig, gen, idx, sheet, opt, W, H, ppmm, mono, alive):
        super().__init__()
        self.sig, self.gen, self.idx, self.sheet, self.opt = sig, gen, idx, sheet, opt
        self.W, self.H, self.ppmm, self.mono, self.alive = W, H, ppmm, mono, alive

    def run(self):
        if not self.alive(self.gen):
            self.sig.ready.emit(self.gen, self.idx, QImage())
            return
        try:
            img = pl.render_sheet(self.sheet, self.W, self.H, self.mono, self.ppmm, self.opt, pl.Cache(2, True))
            q = pil_to_qimage(img)
        except Exception:
            q = QImage()
        self.sig.ready.emit(self.gen, self.idx, q)


class PrintDriver(QObject):
    """Sends the sheets to the printer.  A worker thread draws the next sheet while the window thread paints the one before
    (QPainter on a printer belongs to the window thread).  Cancel = abort."""
    progress = Signal(int, int)
    finished = Signal(str)               # "" = done, otherwise the reason

    def __init__(self, printer, sheets, seq, opt, mono, parent=None):
        super().__init__(parent)
        self.printer, self.sheets, self.seq, self.opt, self.mono = printer, sheets, seq, opt, mono
        self.j = 0
        self.painter = None
        self.pool = None
        self.fut = None
        self.cancelled = False
        self.cache = pl.Cache(2)

    def start(self):
        self.painter = QPainter()
        if not self.painter.begin(self.printer):
            self.painter = None
            self.finished.emit("The printer could not be started. Check that it is on and has no error.")
            return
        vp = self.painter.viewport()
        self.vp = QRect(vp)
        res = self.printer.resolution() or 300
        f = min(1.0, pl.PRINT_MAX_DPI / float(res), 7900.0 / max(1, max(vp.width(), vp.height())))
        self.rw, self.rh = max(1, int(vp.width() * f)), max(1, int(vp.height() * f))
        self.ppmm = self.rw / max(1.0, vp.width() / float(res) * 25.4)
        self.pool = ThreadPoolExecutor(max_workers=1)
        self.fut = self._submit(0)
        QTimer.singleShot(0, self._step)

    def cancel(self):
        self.cancelled = True

    def _make(self, j):
        idx = self.seq[j]
        if idx is None:                                            # blank back side
            return pl.compose(None, self.rw, self.rh, self.mono)
        return pl.render_sheet(self.sheets[idx], self.rw, self.rh, self.mono, self.ppmm, self.opt, self.cache)

    def _submit(self, j):
        return self.pool.submit(self._make, j) if j < len(self.seq) else None

    def _end(self, message):
        try:
            if message and self.printer is not None:
                self.printer.abort()
        except Exception:
            pass
        try:
            if self.painter is not None:
                self.painter.end()
        except Exception:
            pass
        self.painter = None
        if self.pool is not None:
            self.pool.shutdown(wait=False)
        self.finished.emit(message)

    def _step(self):
        if self.painter is None:
            return
        if self.cancelled:
            self._end("Cancelled.")
            return
        if not self.fut.done():
            QTimer.singleShot(30, self._step)
            return
        try:
            img = self.fut.result()
        except Exception as ex:
            self._end("Could not make the page: %s" % ex)
            return
        self.fut = self._submit(self.j + 1)                         # the next one is made while this one is painted
        try:
            if self.j > 0:
                self.printer.newPage()
            rgb = img.convert("RGB")
            data = rgb.tobytes()
            qimg = QImage(data, rgb.width, rgb.height, 3 * rgb.width, QImage.Format.Format_RGB888)
            self.painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform, True)
            self.painter.drawImage(QRect(self.vp.x(), self.vp.y(), self.vp.width(), self.vp.height()), qimg)
            del qimg, data, rgb, img
        except Exception as ex:
            self._end("Printing failed: %s" % ex)
            return
        self.j += 1
        self.progress.emit(self.j, len(self.seq))
        if self.j >= len(self.seq):
            self._end("")
            return
        QTimer.singleShot(0, self._step)


# ================================================================================================ the sheets on the screen
_CURSORS = {"nw": Qt.CursorShape.SizeFDiagCursor, "se": Qt.CursorShape.SizeFDiagCursor,
            "ne": Qt.CursorShape.SizeBDiagCursor, "sw": Qt.CursorShape.SizeBDiagCursor,
            "n": Qt.CursorShape.SizeVerCursor, "s": Qt.CursorShape.SizeVerCursor,
            "e": Qt.CursorShape.SizeHorCursor, "w": Qt.CursorShape.SizeHorCursor, "rot": Qt.CursorShape.CrossCursor}


class SheetCanvas(QWidget):
    """All sheets under each other.  Normal mode: the finished picture of each sheet (drawn in the background, only the sheets
    that are in view).  Transform mode: the pictures themselves, which can be moved, resized, turned and copied."""
    changed = Signal()               # the Transform layout was edited
    current = Signal(int)            # the sheet in use changed
    message = Signal(str)

    PAD, GAP = 20, 30
    HIT_PX = 8
    ROT_PX = 24
    IMG_BUDGET = 160 * 1024 * 1024

    def __init__(self, dlg, parent=None):
        super().__init__(parent)
        self.dlg = dlg
        self.ppm = 3.2
        self.paper = (210.0, 297.0)
        self.area = (5.0, 5.0, 200.0, 287.0)
        self.sheets, self.labels = [], []
        self.opt = pl.Options()
        self.mono = False
        self.transform = False
        self.cur = 0
        self.gen = 0
        self.imgs = OrderedDict()
        self.img_bytes = 0
        self.pending = set()
        self.qcache = OrderedDict()
        self.drag = None
        self.tag = ""
        self.tag_pos = QPointF()
        self.hover_sheet = None
        self.pool = QThreadPool(self)
        self.pool.setMaxThreadCount(1)
        self.sig = _RenderSignals()
        self.sig.ready.connect(self._ready)
        self.setMouseTracking(True)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self._relayout()

    # ---------------------------------------------------------------- set up
    def configure(self, paper, area, sheets, labels, opt, mono, transform):
        self.paper, self.area, self.sheets, self.labels = paper, area, list(sheets), list(labels)
        self.opt, self.mono, self.transform = opt, mono, transform
        self.cur = max(0, min(self.cur, len(self.sheets) - 1))
        self._reset_images()
        self._relayout()
        self.update()

    def set_zoom(self, ppm):
        ppm = max(0.6, min(14.0, ppm))
        if abs(ppm - self.ppm) < 1e-6:
            return
        self.ppm = ppm
        self._reset_images()
        self._relayout()
        self.update()

    def _reset_images(self):
        self.gen += 1
        self.imgs.clear()
        self.img_bytes = 0
        self.pending.clear()

    def _relayout(self):
        n = max(1, len(self.sheets))
        w = int(self.paper[0] * self.ppm + 2 * self.PAD)
        h = int(n * (self.paper[1] * self.ppm + self.GAP) + self.PAD)
        self.setFixedSize(max(60, w), max(60, h))

    def sheet_top(self, i):
        return i * (self.paper[1] * self.ppm + self.GAP) + self.GAP

    def _paper_rect(self, i):
        return QRectF(self.PAD, self.sheet_top(i), self.paper[0] * self.ppm, self.paper[1] * self.ppm)

    def _area_rect(self, i):
        p = self._paper_rect(i)
        ax, ay, aw, ah = self.area
        return QRectF(p.x() + ax * self.ppm, p.y() + ay * self.ppm, aw * self.ppm, ah * self.ppm)

    def sheet_at(self, pos):
        """Number of the sheet under the point (the gap above a sheet counts for that sheet), or None."""
        if not self.sheets:
            return None
        step = self.paper[1] * self.ppm + self.GAP
        i = int((pos.y() - self.GAP * 0.4) // step)
        if i < 0 or i >= len(self.sheets):
            return None
        return i if self._paper_rect(i).adjusted(0, -self.GAP * 0.4, 0, self.GAP * 0.4).contains(pos) else None

    def to_mm(self, i, pos):
        ar = self._area_rect(i)
        return (pos.x() - ar.x()) / self.ppm, (pos.y() - ar.y()) / self.ppm

    def from_mm(self, i, x, y):
        ar = self._area_rect(i)
        return QPointF(ar.x() + x * self.ppm, ar.y() + y * self.ppm)

    def can_edit(self, i):
        return (self.transform and i is not None and 0 <= i < len(self.sheets)
                and self.sheets[i].kind in ("page", "blank") and self.sheets[i].key in self.dlg.model.lay)

    def _index_of_key(self, key):
        for i, s in enumerate(self.sheets):
            if s.key == key and s.kind in ("page", "blank"):
                return i
        return None

    def scroll_to(self, i):
        self.cur = i
        self.dlg.scroll_canvas_to(self.sheet_top(i) - self.GAP)
        self.update()

    # ---------------------------------------------------------------- pictures of the sheets (normal mode)
    def _request(self, i):
        if i in self.pending or i in self.imgs or i >= len(self.sheets):
            return
        self.pending.add(i)
        rp = max(3.0, min(8.0, self.ppm * self.devicePixelRatioF()))
        W, H = max(1, int(self.area[2] * rp)), max(1, int(self.area[3] * rp))
        gen = self.gen
        job = _RenderJob(self.sig, gen, i, self.sheets[i], self.opt, W, H, W / max(1.0, self.area[2]), self.mono,
                         lambda g: g == self.gen)
        self.pool.start(job)

    def _ready(self, gen, idx, img):
        if gen != self.gen:
            return
        self.pending.discard(idx)
        if img.isNull():
            return
        self.imgs[idx] = img
        self.img_bytes += img.sizeInBytes()
        while self.img_bytes > self.IMG_BUDGET and len(self.imgs) > 1:
            k = next(iter(self.imgs))
            if k == idx:
                self.imgs.move_to_end(k)
                continue
            self.img_bytes -= self.imgs.pop(k).sizeInBytes()
        self.update()

    def refresh_sheet(self, i=None):
        """A page of the list got its small copy: draw again."""
        self._reset_images()
        self.update()

    # ---------------------------------------------------------------- pictures of the items (Transform mode)
    def _qimg(self, pk):
        if pk in self.qcache:
            self.qcache.move_to_end(pk)
            return self.qcache[pk]
        pg = self.dlg.page_by_key.get(pk)
        if pg is None or pg.preview is None:
            return None
        pv = pg.preview
        if max(pv.size) > 1000:
            pv = pv.copy()
            pv.thumbnail((1000, 1000), Image.BILINEAR)
        q = pil_to_qimage(pv)
        self.qcache[pk] = q
        while len(self.qcache) > 40:
            self.qcache.popitem(last=False)
        return q

    # ---------------------------------------------------------------- painting
    def paintEvent(self, e):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform, True)
        p.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        clip = e.rect()
        p.fillRect(clip, self.palette().window())
        hidden = set(self.drag["ids"]) if self.drag and self.drag.get("kind") == "move" and self.drag.get("moved") else set()
        for i in range(len(self.sheets)):
            pr = self._paper_rect(i)
            if pr.bottom() + self.GAP < clip.top() or pr.top() - self.GAP > clip.bottom():
                continue
            self._paint_sheet(p, i, pr, hidden)
        self._paint_overlay(p)
        p.end()

    def _paint_sheet(self, p, i, pr, hidden):
        sheet = self.sheets[i]
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QColor(0, 0, 0, 40))
        p.drawRect(pr.translated(3, 3))
        p.setBrush(QColor(255, 255, 255))
        p.setPen(QPen(QColor(150, 150, 150), 1))
        p.drawRect(pr)
        ar = self._area_rect(i)
        label = self.labels[i] if i < len(self.labels) else ""
        p.setPen(QColor(ACCENT) if i == self.cur else self.palette().windowText().color())
        f = p.font()
        f.setBold(i == self.cur)
        p.setFont(f)
        p.drawText(QPointF(pr.x(), pr.y() - 8), label)
        f.setBold(False)
        p.setFont(f)
        if self.transform and sheet.kind in ("page", "blank"):
            p.save()
            p.setClipRect(ar)
            for it in self.dlg.model.lay.get(sheet.key, []):
                if it["id"] not in hidden:
                    self._draw_item(p, i, it)
            p.restore()
            spec = self.opt.spec
            if spec is not None and spec.on and sheet.nums and sheet.nums[0] is not None:
                self._draw_number(p, ar, spec.text(sheet.nums[0]), spec)
        else:
            img = self.imgs.get(i)
            waiting = any((not g.blank) and g.preview is None and g.error is None for g in sheet.pages)
            if img is not None and not waiting:
                p.drawImage(ar, img)
            else:
                p.setPen(QColor(150, 150, 150))
                p.drawText(ar, Qt.AlignmentFlag.AlignCenter, "Loading..." if waiting else "Preparing...")
                if not waiting:
                    self._request(i)
        p.setPen(QPen(QColor(120, 160, 230, 150), 1, Qt.PenStyle.DashLine))
        p.setBrush(Qt.BrushStyle.NoBrush)
        p.drawRect(ar)
        if i == self.hover_sheet and self.drag and self.drag.get("kind") == "move" and self.drag.get("moved"):
            p.setPen(QPen(ACCENT, 3))
            p.drawRect(pr)

    def _draw_number(self, p, ar, text, spec):
        f = QFont("Times New Roman")
        f.setPixelSize(max(6, int(spec.size * 25.4 / 72 * self.ppm)))
        f.setBold(spec.bold)
        p.save()
        p.setFont(f)
        p.setPen(QColor(0, 0, 0))
        fm = p.fontMetrics()
        tw, th = fm.horizontalAdvance(text), fm.ascent() * 0.72
        m = spec.margin * self.ppm
        x = ar.left() + m if spec.align == "left" else (ar.right() - m - tw if spec.align == "right" else ar.center().x() - tw / 2)
        y = ar.top() + m + th if spec.pos == "top" else ar.bottom() - m
        p.drawText(QPointF(x, y), text)
        p.restore()

    def _draw_item(self, p, i, it, dx=0.0, dy=0.0):
        img = self._qimg(it["pk"])
        ar = self._area_rect(i)
        w, h = it["w"] * self.ppm, it["h"] * self.ppm
        p.save()
        p.translate(ar.x() + (it["cx"] + dx) * self.ppm, ar.y() + (it["cy"] + dy) * self.ppm)
        p.rotate(it["rot"])
        r = QRectF(-w / 2, -h / 2, w, h)
        if img is not None:
            p.drawImage(r, img)
        else:
            p.fillRect(r, QColor(225, 225, 225))
        p.restore()

    def _poly(self, i, it, dx=0.0, dy=0.0):
        return QPolygonF([self.from_mm(i, x + dx, y + dy) for x, y in pl.item_corners(it)])

    def _paint_overlay(self, p):
        model = self.dlg.model
        if not self.transform:
            return
        d = self.drag if (self.drag and self.drag.get("kind") == "move" and self.drag.get("moved")) else None
        if d:                                            # the lifted pictures float above everything
            p.setOpacity(0.88)
            for iid in d["ids"]:
                key, it = model.find(iid)
                si = d["origin"][iid][0]
                if it is not None:
                    self._draw_item(p, si, it)
                    p.setOpacity(1.0)
                    p.setPen(QPen(ACCENT, 1.5, Qt.PenStyle.DashLine))
                    p.setBrush(Qt.BrushStyle.NoBrush)
                    p.drawPolygon(self._poly(si, it))
                    p.setOpacity(0.88)
            p.setOpacity(1.0)
        sel = model.selected()
        for key, it in sel:
            si = self._index_of_key(key)
            if si is None:
                continue
            p.setPen(QPen(ACCENT, 1.6))
            p.setBrush(Qt.BrushStyle.NoBrush)
            p.drawPolygon(self._poly(si, it))
        if len(sel) == 1 and not d:
            key, it = sel[0]
            si = self._index_of_key(key)
            if si is not None:
                pts = pl.handle_points(it, self.ROT_PX / self.ppm)
                top = self.from_mm(si, *pts["n"])
                rot = self.from_mm(si, *pts["rot"])
                p.setPen(QPen(ACCENT, 1.2))
                p.drawLine(top, rot)
                p.setBrush(QColor(255, 255, 255))
                p.drawEllipse(rot, 6, 6)
                for name in pl.HANDLES:
                    c = self.from_mm(si, *pts[name])
                    p.drawRect(QRectF(c.x() - 4.5, c.y() - 4.5, 9, 9))
        if self.drag and self.drag.get("kind") == "rubber":
            r = QRectF(self.drag["a"], self.drag["b"]).normalized()
            p.setPen(QPen(ACCENT, 1, Qt.PenStyle.DashLine))
            p.setBrush(QColor(37, 99, 235, 40))
            p.drawRect(r)
        if self.tag:
            f = p.font()
            f.setBold(True)
            p.setFont(f)
            fm = p.fontMetrics()
            w = fm.horizontalAdvance(self.tag) + 14
            r = QRectF(self.tag_pos.x() + 14, self.tag_pos.y() + 14, w, fm.height() + 8)
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(QColor(30, 30, 30, 220))
            p.drawRoundedRect(r, 5, 5)
            p.setPen(QColor(255, 255, 255))
            p.drawText(r, Qt.AlignmentFlag.AlignCenter, self.tag)

    # ---------------------------------------------------------------- mouse
    def _push(self):
        self.dlg.model.push(self.dlg.extra_state())

    def _set_cur(self, i):
        if i is not None and i != self.cur:
            self.cur = i
            self.current.emit(i)
            self.update()

    def _handle_hit(self, pos):
        """(sheet number, item, handle name) of the single selected picture under the pointer, or None."""
        sel = self.dlg.model.selected()
        if len(sel) != 1:
            return None
        key, it = sel[0]
        si = self._index_of_key(key)
        if si is None:
            return None
        x, y = self.to_mm(si, pos)
        h = pl.handle_at(it, x, y, self.HIT_PX / self.ppm, self.ROT_PX / self.ppm)
        return (si, it, h) if h else None

    def mousePressEvent(self, e):
        self.setFocus()
        if e.button() != Qt.MouseButton.LeftButton:
            return
        pos = e.position()
        i = self.sheet_at(pos)
        if i is not None:
            self._set_cur(i)
        if not self.transform:
            return
        model = self.dlg.model
        mods = e.modifiers()
        ctrl = bool(mods & Qt.KeyboardModifier.ControlModifier)
        hh = self._handle_hit(pos)
        if hh:
            si, it, h = hh
            self._push()
            self.drag = {"kind": "rot" if h == "rot" else "size", "handle": h, "si": si, "id": it["id"], "orig": dict(it)}
            return
        if i is not None and self.can_edit(i):
            x, y = self.to_mm(i, pos)
            hit = model.hit(self.sheets[i].key, x, y)
            if hit is not None:
                if ctrl:
                    model.toggle(hit)
                elif hit not in model.sel:
                    model.select([hit])
                ids = [iid for iid in model.sel]
                if hit in ids:
                    origin = {}
                    for iid in ids:
                        key, it = model.find(iid)
                        origin[iid] = (self._index_of_key(key), it["cx"], it["cy"])
                    self.drag = {"kind": "move", "ids": ids, "origin": origin, "start": QPointF(pos), "moved": False,
                                 "copy": bool(mods & Qt.KeyboardModifier.AltModifier), "pushed": False}
                self.update()
                return
        if not ctrl:
            model.select([])
        self.drag = {"kind": "rubber", "a": QPointF(pos), "b": QPointF(pos), "add": ctrl, "base": list(model.sel)}
        self.update()

    def mouseMoveEvent(self, e):
        pos = e.position()
        d = self.drag
        self.tag_pos = QPointF(pos)
        if d is None:
            self._hover(pos)
            return
        model = self.dlg.model
        shift = bool(e.modifiers() & Qt.KeyboardModifier.ShiftModifier)
        if d["kind"] == "move":
            dx, dy = (pos.x() - d["start"].x()) / self.ppm, (pos.y() - d["start"].y()) / self.ppm
            if not d["moved"]:
                if math.hypot(dx, dy) * self.ppm < 4:
                    return
                d["moved"] = True
                self._push()
                if d["copy"]:                                      # Alt + drag: the copies are dragged, the originals stay
                    new = []
                    origin = {}
                    for iid in d["ids"]:
                        n = model.duplicate([iid])
                        if n:
                            new.append(n[0])
                            origin[n[0]] = (d["origin"][iid][0], d["origin"][iid][1], d["origin"][iid][2])
                    d["ids"], d["origin"] = new, origin
                    model.select(new)
            if shift:
                dx, dy = pl.snap_straight(dx, dy)
            for iid in d["ids"]:
                key, it = model.find(iid)
                if it is not None:
                    it["cx"], it["cy"] = d["origin"][iid][1] + dx, d["origin"][iid][2] + dy
            self.hover_sheet = self.sheet_at(pos)
            word = "Copy" if d["copy"] else "Move"
            self.tag = "%s  %.1f, %.1f mm" % (word, dx, dy)
            if not shift and self.hover_sheet is not None and self.hover_sheet != d["origin"][d["ids"][0]][0]:
                self.tag = "%s to page %d" % (word, self.hover_sheet + 1)
            self.update()
        elif d["kind"] in ("size", "rot"):
            key, it = model.find(d["id"])
            if it is None:
                return
            x, y = self.to_mm(d["si"], pos)
            if d["kind"] == "size":
                new = pl.resize_item(d["orig"], d["handle"], x, y, keep_aspect=not shift)
                it.update(w=new["w"], h=new["h"], cx=new["cx"], cy=new["cy"])
                self.tag = "Resize  %.1f x %.1f mm" % (it["w"], it["h"])
            else:
                it["rot"] = pl.rotation_for(d["orig"], x, y, snap=shift)
                self.tag = "Rotate  %.0f\u00b0" % it["rot"]
            model.touch(key)
            self.update()
        elif d["kind"] == "rubber":
            d["b"] = QPointF(pos)
            ids = set(d["base"]) if d["add"] else set()
            r = QRectF(d["a"], d["b"]).normalized()
            for i in range(len(self.sheets)):
                if self.can_edit(i) and self._paper_rect(i).intersects(r):
                    x0, y0 = self.to_mm(i, r.topLeft())
                    x1, y1 = self.to_mm(i, r.bottomRight())
                    ids.update(model.in_rect(self.sheets[i].key, x0, y0, x1, y1))
            model.sel = list(ids)
            self.update()

    def mouseReleaseEvent(self, e):
        d, self.drag = self.drag, None
        self.tag = ""
        self.hover_sheet = None
        if d is None:
            return
        model = self.dlg.model
        if d["kind"] == "move" and d["moved"]:
            shift = bool(e.modifiers() & Qt.KeyboardModifier.ShiftModifier)
            aw, ah = self.area[2], self.area[3]
            for iid in d["ids"]:
                key, it = model.find(iid)
                if it is None:
                    continue
                si = d["origin"][iid][0]
                center = self.from_mm(si, it["cx"], it["cy"])
                target = None if shift else self.sheet_at(center)
                if target is not None and target != si and self.can_edit(target):
                    x, y = self.to_mm(target, center)
                    model.move_to_sheet(iid, self.sheets[target].key, x, y)
                else:
                    if shift:                                       # Shift: the picture stays on its own page
                        it["cx"] = max(0.0, min(aw, it["cx"]))
                        it["cy"] = max(0.0, min(ah, it["cy"]))
                    model.touch(key)
            self.changed.emit()
        elif d["kind"] in ("size", "rot"):
            self.changed.emit()
        self.update()

    def _hover(self, pos):
        cur = Qt.CursorShape.ArrowCursor
        if self.transform:
            hh = self._handle_hit(pos)
            if hh:
                cur = _CURSORS.get(hh[2], cur)
            else:
                i = self.sheet_at(pos)
                if self.can_edit(i):
                    x, y = self.to_mm(i, pos)
                    if self.dlg.model.hit(self.sheets[i].key, x, y) is not None:
                        cur = Qt.CursorShape.SizeAllCursor
        self.setCursor(QCursor(cur))

    def wheelEvent(self, e):
        if e.modifiers() & Qt.KeyboardModifier.ControlModifier:
            self.dlg.zoom_by(1.15 if e.angleDelta().y() > 0 else 1 / 1.15)
            e.accept()
        else:
            e.ignore()

    # ---------------------------------------------------------------- keys + actions (also used by the tool buttons)
    def keyPressEvent(self, e):
        k, mods = e.key(), e.modifiers()
        ctrl = bool(mods & Qt.KeyboardModifier.ControlModifier)
        if not self.transform:
            e.ignore()
            return
        step = 5.0 if mods & Qt.KeyboardModifier.ShiftModifier else 0.5
        arrows = {Qt.Key.Key_Left: (-step, 0), Qt.Key.Key_Right: (step, 0), Qt.Key.Key_Up: (0, -step), Qt.Key.Key_Down: (0, step)}
        if k in (Qt.Key.Key_Delete, Qt.Key.Key_Backspace):
            self.do_delete()
        elif ctrl and k == Qt.Key.Key_C:
            self.do_copy()
        elif ctrl and k == Qt.Key.Key_V:
            self.do_paste()
        elif ctrl and (k == Qt.Key.Key_Y or (k == Qt.Key.Key_Z and mods & Qt.KeyboardModifier.ShiftModifier)):
            self.dlg.do_redo()
        elif ctrl and k == Qt.Key.Key_Z:
            self.dlg.do_undo()
        elif ctrl and k == Qt.Key.Key_A:
            self.do_select_all()
        elif k == Qt.Key.Key_Escape:
            self.dlg.model.select([])
            self.update()
        elif k in arrows and self.dlg.model.sel:
            self._push()
            for key, it in self.dlg.model.selected():
                it["cx"] += arrows[k][0]
                it["cy"] += arrows[k][1]
                self.dlg.model.touch(key)
            self.changed.emit()
            self.update()
        else:
            e.ignore()

    def _done(self, text=""):
        if text:
            self.message.emit(text)
        self.changed.emit()
        self.update()

    def do_delete(self):
        if not self.dlg.model.sel:
            return
        self._push()
        n = self.dlg.model.delete_selected()
        self._done("Deleted %d picture(s). Ctrl+Z brings them back." % n)

    def do_copy(self):
        n = self.dlg.model.copy_selected()
        if n:
            self.message.emit("Copied %d picture(s). Ctrl+V puts them on the sheet in use." % n)

    def do_paste(self):
        m = self.dlg.model
        if not m.clip or not self.sheets or not self.can_edit(self.cur):
            return
        self._push()
        n = len(m.paste(self.sheets[self.cur].key))
        self._done("Pasted %d picture(s) on page %d." % (n, self.cur + 1))

    def do_turn(self, deg):
        if not self.dlg.model.sel:
            return
        self._push()
        self.dlg.model.turn_selected(deg)
        self._done()

    def do_center(self):
        if not self.dlg.model.sel:
            return
        self._push()
        self.dlg.model.center_selected(self.area[2], self.area[3])
        self._done("Centered on the page.")

    def do_select_all(self):
        if self.can_edit(self.cur):
            self.dlg.model.select([it["id"] for it in self.dlg.model.lay.get(self.sheets[self.cur].key, [])])
            self.update()

    def do_reset_sheet(self):
        if not self.can_edit(self.cur):
            return
        key = self.sheets[self.cur].key
        self._push()
        self.dlg.model.lay.pop(key, None)
        self.dlg.model.edited.discard(key)
        self.dlg.model.sel = []
        self.changed.emit()
        self.dlg.rebuild_now()

    def add_picture(self, pk):
        """A picture of another page is put on the sheet in use (centre, half the width of the page)."""
        pg = self.dlg.page_by_key.get(pk)
        m = self.dlg.model
        if pg is None or pg.blank or not self.can_edit(self.cur):
            return
        self.dlg._request_load([pg])
        key = self.sheets[self.cur].key
        nw, nh = pg.natural_mm()
        aw, ah = self.area[2], self.area[3]
        w = aw * 0.5
        h = w * nh / max(1e-6, nw)
        if h > ah * 0.9:
            h = ah * 0.9
            w = h * nw / max(1e-6, nh)
        self._push()
        it = m.new_item(pk, aw / 2, ah / 2, w, h, 0.0)
        m.lay[key].append(it)
        m.touch(key)
        m.select([it["id"]])
        self._done("Added %s to page %d." % (pg.name or "the picture", self.cur + 1))


# ================================================================================================ the print screen
class PrintStudio(QDialog):
    def __init__(self, parent, settings, cards, checked_uids):
        super().__init__(parent)
        self.settings = settings
        self.setWindowTitle("Print")
        self.setWindowFlag(Qt.WindowType.WindowMaximizeButtonHint, True)
        self.setSizeGripEnabled(True)
        self.model = pl.TransformModel()
        self.all_pages = {}                         # key -> Page (also the removed blank ones, for undo)
        self.page_by_key = self.all_pages
        self.order = []
        self.checked = set()
        self._blank_n = 0
        for c in cards:
            pg = pl.Page(c.uid, c.name, loader=c.render_source())
            self.all_pages[pg.key] = pg
            self.order.append(pg)
            if c.uid in checked_uids:
                self.checked.add(c.uid)
        if not self.checked and self.order:
            self.checked.add(self.order[0].key)
        self.area = (5.0, 5.0, 200.0, 287.0)
        self.paper = (210.0, 297.0)
        self.sheets, self.pcopies = [], 1
        self.first_sheet_of = {}
        self._printer = None
        self._sizes = []
        self._duplex_modes = [QPrinter.DuplexMode.DuplexNone]
        self._driver = None
        self._closing = False
        self._busy_print = False
        self._build_ui()
        self._fill_printers()
        self._fill_list()
        self._timer = QTimer(self)
        self._timer.setSingleShot(True)
        self._timer.setInterval(120)
        self._timer.timeout.connect(self._rebuild)
        self._geometry_changed()
        screen = self.screen().availableGeometry() if self.screen() else QRect(0, 0, 1366, 768)
        self.resize(min(1380, int(screen.width() * 0.92)), min(900, int(screen.height() * 0.92)))
        self._prep_sig = _PrepSignals()
        self._prep_sig.page.connect(self._page_ready)
        self._prep_sig.finished.connect(self._prep_done)
        self._prep_pool = QThreadPool(self)
        self._prep_pool.setMaxThreadCount(1)                # one picture at a time: the PC stays calm
        self._requested = set()
        self._prep_total = 0
        self._prep_n = 0
        self._request_load([p for p in self.order if p.key in self.checked])
        self._fitted = False

    # ---------------------------------------------------------------- building the window
    def _build_ui(self):
        root = QVBoxLayout(self)
        root.setSpacing(8)
        body = QHBoxLayout()
        body.setSpacing(10)
        root.addLayout(body, 1)

        # ---- left: the pages
        left = QVBoxLayout()
        left.addWidget(QLabel("Pages  (tick = print)"))
        self.listPages = QListWidget()
        self.listPages.setIconSize(QSize(44, 56))
        self.listPages.setMinimumWidth(190)
        self.listPages.setMaximumWidth(260)
        self.listPages.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.listPages.itemChanged.connect(self._item_ticked)
        self.listPages.currentRowChanged.connect(self._row_changed)
        left.addWidget(self.listPages, 1)
        r1 = QHBoxLayout()
        for text, tip, fn in (("Tick all", "Print every page", lambda: self._tick_all(True)),
                              ("Untick all", "Print nothing yet; tick the pages you want", lambda: self._tick_all(False))):
            b = QPushButton(text)
            b.setToolTip(tip)
            b.clicked.connect(fn)
            r1.addWidget(b)
        left.addLayout(r1)
        r2 = QHBoxLayout()
        self.btnUp, self.btnDown = QToolButton(), QToolButton()
        self.btnUp.setText("\u25b2")
        self.btnDown.setText("\u25bc")
        self.btnUp.setToolTip("Move this page earlier in the printout")
        self.btnDown.setToolTip("Move this page later in the printout")
        self.btnUp.clicked.connect(lambda: self._move_row(-1))
        self.btnDown.clicked.connect(lambda: self._move_row(1))
        self.btnBlank = QPushButton("+ Blank page")
        self.btnBlank.setToolTip("Adds an empty page after the selected one (to put pictures on it with Transform, or just a page number)")
        self.btnBlank.clicked.connect(self._add_blank)
        self.btnDelBlank = QToolButton()
        self.btnDelBlank.setText("\u2715")
        self.btnDelBlank.setToolTip("Remove the selected blank page")
        self.btnDelBlank.clicked.connect(self._del_blank)
        for w in (self.btnUp, self.btnDown, self.btnBlank, self.btnDelBlank):
            r2.addWidget(w)
        left.addLayout(r2)
        body.addLayout(left)

        # ---- middle: tools + the sheets
        mid = QVBoxLayout()
        mid.setSpacing(6)
        bar = QHBoxLayout()
        bar.setSpacing(4)
        self.btnTransform = QPushButton("Transform")
        self.btnTransform.setCheckable(True)
        self.btnTransform.setToolTip("Move, resize, turn and copy the pictures on the sheets (not with several pages on one sheet)")
        self.btnTransform.toggled.connect(self._transform_toggled)
        bar.addWidget(self.btnTransform)
        self.toolButtons = []

        def tool(text, tip, fn):
            b = QToolButton()
            b.setText(text)
            b.setToolTip(tip)
            b.clicked.connect(fn)
            bar.addWidget(b)
            self.toolButtons.append(b)
            return b
        self.canvas = SheetCanvas(self)
        tool("\u21ba 90", "Turn the picture 90\u00b0 to the left", lambda: self.canvas.do_turn(-90))
        tool("\u21bb 90", "Turn the picture 90\u00b0 to the right", lambda: self.canvas.do_turn(90))
        tool("Center", "Put the picture in the middle of the page", self.canvas.do_center)
        tool("Copy", "Copy the picture (Ctrl+C). Alt + drag copies too", self.canvas.do_copy)
        tool("Paste", "Paste on the page in use (Ctrl+V)", self.canvas.do_paste)
        tool("Delete", "Delete the picture (Del)", self.canvas.do_delete)
        self.btnAddPic = QToolButton()
        self.btnAddPic.setText("Add picture \u25be")
        self.btnAddPic.setToolTip("Put another picture on the page in use")
        self.btnAddPic.setPopupMode(QToolButton.ToolButtonPopupMode.InstantPopup)
        self.menuAdd = QMenu(self)
        self.menuAdd.aboutToShow.connect(self._fill_add_menu)
        self.btnAddPic.setMenu(self.menuAdd)
        bar.addWidget(self.btnAddPic)
        self.toolButtons.append(self.btnAddPic)
        tool("Reset page", "Back to the normal look of this page", self.canvas.do_reset_sheet)
        self.btnUndo = tool("Undo", "Ctrl+Z", self.do_undo)
        self.btnRedo = tool("Redo", "Ctrl+Y", self.do_redo)
        bar.addStretch(1)
        for text, tip, fn in (("\u2212", "Smaller", lambda: self.zoom_by(1 / 1.2)), ("+", "Bigger", lambda: self.zoom_by(1.2)),
                              ("Fit width", "The sheet fills the width", self.fit_width), ("100%", "Zoom 100%", self.zoom_100)):
            b = QToolButton()
            b.setText(text)
            b.setToolTip(tip)
            b.clicked.connect(fn)
            bar.addWidget(b)
        mid.addLayout(bar)
        self.scroll = QScrollArea()
        self.scroll.setWidget(self.canvas)
        self.scroll.setWidgetResizable(False)
        self.scroll.setAlignment(Qt.AlignmentFlag.AlignHCenter | Qt.AlignmentFlag.AlignTop)
        self.scroll.setFrameShape(QFrame.Shape.StyledPanel)
        mid.addWidget(self.scroll, 1)
        self.lblHint = QLabel("")
        self.lblHint.setWordWrap(True)
        mid.addWidget(self.lblHint)
        body.addLayout(mid, 1)
        self.canvas.message.connect(self._say)
        self.canvas.changed.connect(self._items_changed)
        self.canvas.current.connect(self._sheet_in_use)

        # ---- right: settings
        side = QScrollArea()
        side.setWidgetResizable(True)
        side.setFrameShape(QFrame.Shape.NoFrame)
        side.setMinimumWidth(300)
        side.setMaximumWidth(340)
        panel = QWidget()
        pv = QVBoxLayout(panel)
        pv.setSpacing(10)
        pv.setContentsMargins(0, 0, 6, 0)

        g1 = QGroupBox("Printer")
        f1 = QFormLayout(g1)
        self.cbPrinter, self.cbPaper = QComboBox(), QComboBox()
        self.cbOrient, self.cbColor, self.cbDuplex = QComboBox(), QComboBox(), QComboBox()
        self.cbOrient.addItem("Portrait", "portrait")
        self.cbOrient.addItem("Landscape", "landscape")
        self.cbColor.addItem("Color", "color")
        self.cbColor.addItem("Black & white", "mono")
        self.spCopies = QSpinBox()
        self.spCopies.setRange(1, 99)
        self.btnProps = QPushButton("Printer properties...")
        self.btnProps.setToolTip("The printer's own settings window (trays, quality, ...)")
        self.btnProps.clicked.connect(self._printer_properties)
        for t, w in (("Printer", self.cbPrinter), ("Paper", self.cbPaper), ("Orientation", self.cbOrient),
                     ("Color", self.cbColor), ("Two-sided", self.cbDuplex), ("Copies", self.spCopies)):
            f1.addRow(t, w)
        f1.addRow(self.btnProps)
        pv.addWidget(g1)

        g2 = QGroupBox("Layout")
        f2 = QFormLayout(g2)
        self.cbScale, self.cbNup = QComboBox(), QComboBox()
        for t, v in (("Fit to paper", "fit"), ("Fill the paper (extra is cut)", "fill"), ("Actual size", "actual"),
                     ("Custom size", "custom")):
            self.cbScale.addItem(t, v)
        self.spPct = QSpinBox()
        self.spPct.setRange(10, 400)
        self.spPct.setValue(100)
        self.spPct.setSuffix(" %")
        self.chkTurn = QCheckBox("Turn pictures to fit the paper")
        self.chkTurn.setChecked(True)
        self.cbNup.addItem("1 page per sheet", 1)
        for n in pl.NUP_CHOICES:
            self.cbNup.addItem("%d pages per sheet" % n, n)
        f2.addRow("Size", self.cbScale)
        f2.addRow("Percent", self.spPct)
        f2.addRow(self.chkTurn)
        f2.addRow("On a sheet", self.cbNup)
        pv.addWidget(g2)

        g3 = QGroupBox("Page numbers")
        v3 = QVBoxLayout(g3)
        self.numpanel = PageNumberPanel(load_numspec(self.settings), show_keep_own=True)
        v3.addWidget(self.numpanel)
        pv.addWidget(g3)
        pv.addStretch(1)
        side.setWidget(panel)
        body.addWidget(side)

        # ---- bottom
        bottom = QHBoxLayout()
        self.lblStatus = QLabel("")
        self.lblStatus.setWordWrap(True)
        self.progress = QProgressBar()
        self.progress.setMaximumWidth(220)
        self.progress.hide()
        self.btnPrint = QPushButton("Print")
        self.btnPrint.setProperty("accent", True)
        self.btnPrint.setMinimumWidth(110)
        self.btnPrint.clicked.connect(self._print)
        self.btnClose = QPushButton("Close")
        self.btnClose.clicked.connect(self.reject)
        bottom.addWidget(self.lblStatus, 1)
        bottom.addWidget(self.progress)
        bottom.addWidget(self.btnClose)
        bottom.addWidget(self.btnPrint)
        root.addLayout(bottom)

        # settings that change the paper
        for w in (self.cbOrient,):
            w.currentIndexChanged.connect(self._geometry_changed)
        self.cbPrinter.currentIndexChanged.connect(self._printer_changed)
        self.cbPaper.currentIndexChanged.connect(self._geometry_changed)
        # settings that only change the look of the sheets
        self.cbColor.currentIndexChanged.connect(self._look_changed)
        self.cbDuplex.currentIndexChanged.connect(self._save_settings)
        self.spCopies.valueChanged.connect(self._look_changed)
        self.cbScale.currentIndexChanged.connect(self._scale_changed)
        self.spPct.valueChanged.connect(self._layout_changed)
        self.chkTurn.toggled.connect(self._layout_changed)
        self.cbNup.currentIndexChanged.connect(self._nup_changed)
        self.numpanel.changed.connect(self._numbers_changed)
        self._restore_settings()
        self._update_tools()

    # ---------------------------------------------------------------- settings memory
    def _restore_settings(self):
        s = self.settings
        pairs = ((self.cbOrient, "print/orient"), (self.cbColor, "print/color"), (self.cbScale, "print/scale"),
                 (self.cbNup, "print/nup"))
        for cb, key in pairs:
            cb.blockSignals(True)
            v = s.value(key, None)
            if v is not None:
                data = int(v) if key == "print/nup" else str(v)
                i = cb.findData(data)
                if i >= 0:
                    cb.setCurrentIndex(i)
            cb.blockSignals(False)
        self.spPct.blockSignals(True)
        self.spPct.setValue(s.value("print/pct", 100, type=int))
        self.spPct.blockSignals(False)
        self.chkTurn.blockSignals(True)
        self.chkTurn.setChecked(s.value("print/turn", True, type=bool))
        self.chkTurn.blockSignals(False)
        self.spPct.setEnabled(self.cbScale.currentData() == "custom")

    def _save_settings(self, *_):
        s = self.settings
        s.setValue("print/printer", self.cbPrinter.currentData() or "")
        s.setValue("print/paper", self.cbPaper.currentText())
        s.setValue("print/orient", self.cbOrient.currentData())
        s.setValue("print/color", self.cbColor.currentData())
        s.setValue("print/duplex", self.cbDuplex.currentIndex())
        s.setValue("print/scale", self.cbScale.currentData())
        s.setValue("print/pct", self.spPct.value())
        s.setValue("print/turn", self.chkTurn.isChecked())
        s.setValue("print/nup", self.cbNup.currentData())

    # ---------------------------------------------------------------- printers
    def _qprinter(self):
        if self._printer is None:
            self._printer = QPrinter(QPrinter.PrinterMode.HighResolution)
        return self._printer

    def _fill_printers(self):
        try:
            names = list(QPrinterInfo.availablePrinterNames())
            default = QPrinterInfo.defaultPrinterName()
        except Exception:
            names, default = [], ""
        last = self.settings.value("print/printer", "", type=str)
        self.cbPrinter.blockSignals(True)
        self.cbPrinter.clear()
        for n in names:
            self.cbPrinter.addItem(n, n)
        want = last if last in names else (default if default in names else (names[0] if names else ""))
        if want:
            self.cbPrinter.setCurrentIndex(self.cbPrinter.findData(want))
        self.cbPrinter.blockSignals(False)
        if not names:
            self.lblStatus.setText("No printer is installed. The preview works; install a printer (or use \"Microsoft Print to PDF\") to print.")
        self._fill_paper()

    def _info(self):
        name = self.cbPrinter.currentData()
        try:
            info = QPrinterInfo.printerInfo(name) if name else QPrinterInfo()
            return None if info.isNull() else info
        except Exception:
            return None

    def _fill_paper(self):
        info = self._info()
        sizes = []
        try:
            if info is not None:
                sizes = list(info.supportedPageSizes())
        except Exception:
            sizes = []
        if not sizes:
            sizes = [QPageSize(QPageSize.PageSizeId.A4), QPageSize(QPageSize.PageSizeId.Letter), QPageSize(QPageSize.PageSizeId.A5),
                     QPageSize(QPageSize.PageSizeId.A3), QPageSize(QPageSize.PageSizeId.Legal)]
        seen, uniq = set(), []
        for ps in sizes:
            nm = ps.name()
            if nm not in seen:
                seen.add(nm)
                uniq.append(ps)
        self._sizes = uniq
        last = self.settings.value("print/paper", "", type=str)
        default = ""
        try:
            if info is not None:
                default = info.defaultPageSize().name()
        except Exception:
            pass
        self.cbPaper.blockSignals(True)
        self.cbPaper.clear()
        for i, ps in enumerate(uniq):
            self.cbPaper.addItem(ps.name(), i)
        names = [ps.name() for ps in uniq]
        pick = last if last in names else ("A4" if "A4" in names else (default if default in names else names[0]))
        self.cbPaper.setCurrentIndex(names.index(pick))
        self.cbPaper.blockSignals(False)
        self.cbDuplex.blockSignals(True)
        self.cbDuplex.clear()
        modes = [(QPrinter.DuplexMode.DuplexNone, "Off")]
        try:
            sup = list(info.supportedDuplexModes()) if info is not None else []
        except Exception:
            sup = []
        if QPrinter.DuplexMode.DuplexLongSide in sup:
            modes.append((QPrinter.DuplexMode.DuplexLongSide, "On, long edge (like a book)"))
        if QPrinter.DuplexMode.DuplexShortSide in sup:
            modes.append((QPrinter.DuplexMode.DuplexShortSide, "On, short edge (like a notepad)"))
        self._duplex_modes = [m for m, _t in modes]          # the combo keeps the number, not the Qt enum
        for k, (_m, t) in enumerate(modes):
            self.cbDuplex.addItem(t, k)
        self.cbDuplex.setEnabled(len(modes) > 1)
        self.cbDuplex.setToolTip("" if len(modes) > 1 else "This printer does not do two-sided printing")
        i = self.settings.value("print/duplex", 0, type=int)
        self.cbDuplex.setCurrentIndex(i if 0 <= i < len(modes) else 0)
        self.cbDuplex.blockSignals(False)

    def _apply_printer(self, pr):
        name = self.cbPrinter.currentData()
        if name:
            pr.setPrinterName(name)
        i = self.cbPaper.currentData()
        if i is not None and 0 <= i < len(self._sizes):
            pr.setPageSize(self._sizes[i])
        pr.setPageOrientation(QPageLayout.Orientation.Landscape if self.cbOrient.currentData() == "landscape"
                              else QPageLayout.Orientation.Portrait)
        pr.setColorMode(QPrinter.ColorMode.GrayScale if self.cbColor.currentData() == "mono" else QPrinter.ColorMode.Color)
        k = self.cbDuplex.currentData()
        if k is not None and 0 <= k < len(self._duplex_modes):
            pr.setDuplex(self._duplex_modes[k])
        pr.setCopyCount(1)                                  # the copies are made by this window (collated, see sequence())
        pr.setDocName("Image Fixer")

    def _printer_changed(self, *_):
        self._fill_paper()
        self._geometry_changed()

    def _printer_properties(self):
        """The driver's own window.  What was changed there is read back into this screen."""
        pr = self._qprinter()
        try:
            self._apply_printer(pr)
            dlg = QPrintDialog(pr, self)
            dlg.setWindowTitle("Printer properties")
            if dlg.exec() != QDialog.DialogCode.Accepted:
                return
            i = self.cbPrinter.findData(pr.printerName())
            self.cbPrinter.blockSignals(True)
            if i >= 0:
                self.cbPrinter.setCurrentIndex(i)
            self.cbPrinter.blockSignals(False)
            self._fill_paper()
            j = self.cbPaper.findText(pr.pageLayout().pageSize().name())
            self.cbPaper.blockSignals(True)
            if j >= 0:
                self.cbPaper.setCurrentIndex(j)
            self.cbPaper.blockSignals(False)
            self.cbOrient.blockSignals(True)
            self.cbOrient.setCurrentIndex(1 if pr.pageLayout().orientation() == QPageLayout.Orientation.Landscape else 0)
            self.cbOrient.blockSignals(False)
            self.cbColor.blockSignals(True)
            self.cbColor.setCurrentIndex(1 if pr.colorMode() == QPrinter.ColorMode.GrayScale else 0)
            self.cbColor.blockSignals(False)
            if pr.duplex() in self._duplex_modes:
                self.cbDuplex.setCurrentIndex(self._duplex_modes.index(pr.duplex()))
            if pr.copyCount() > 1:
                self.spCopies.setValue(min(99, pr.copyCount()))
            self._geometry_changed()
        except Exception as ex:
            self._say("Could not open the printer window: %s" % ex)

    def _geometry_changed(self, *_):
        pr = self._qprinter()
        try:
            self._apply_printer(pr)
            lay = pr.pageLayout()
            full, area = lay.fullRect(MM), lay.paintRect(MM)
            paper = (float(full.width()), float(full.height()))
            ar = (float(area.x()), float(area.y()), float(area.width()), float(area.height()))
            if paper[0] < 20 or paper[1] < 20 or ar[2] < 10 or ar[3] < 10:
                raise ValueError("bad paper")
        except Exception:
            land = self.cbOrient.currentData() == "landscape"
            paper = (297.0, 210.0) if land else (210.0, 297.0)
            ar = (5.0, 5.0, paper[0] - 10, paper[1] - 10)
        self.paper, self.area = paper, ar
        self._save_settings()
        self._layout_changed()

    # ---------------------------------------------------------------- changes -> redraw
    def _schedule(self):
        self._timer.start()

    def _layout_changed(self, *_):
        self.model.forget_unedited()
        self.spPct.setEnabled(self.cbScale.currentData() == "custom")
        self._save_settings()
        self._schedule()

    def _scale_changed(self, *_):
        self._layout_changed()

    def _look_changed(self, *_):
        self._save_settings()
        self._schedule()

    def _numbers_changed(self):
        save_numspec(self.settings, self.numpanel.spec())
        self._schedule()

    def _nup_changed(self, *_):
        nup = pl.get_nup(self.cbNup.currentData())
        if nup > 1 and self.btnTransform.isChecked():
            self.btnTransform.setChecked(False)
        self._update_tools()
        self._layout_changed()

    def _transform_toggled(self, on):
        if on and pl.get_nup(self.cbNup.currentData()) > 1:
            self.btnTransform.blockSignals(True)
            self.btnTransform.setChecked(False)
            self.btnTransform.blockSignals(False)
            self._say("Transform works with one page per sheet. Set \"On a sheet\" to 1 first.")
            return
        self.model.sel = []
        self._update_tools()
        self._schedule()
        self._say("Transform: drag a picture to move it (Shift = straight, Alt = copy), the corners resize, the round handle turns.")

    def _update_tools(self):
        on = self.btnTransform.isChecked()
        for b in self.toolButtons:
            b.setEnabled(on)
        if on:
            self.btnUndo.setEnabled(self.model.can_undo)
            self.btnRedo.setEnabled(self.model.can_redo)
        self.btnTransform.setEnabled(pl.get_nup(self.cbNup.currentData()) == 1)
        self.lblHint.setText("Drag = move  \u00b7  Shift + drag = straight  \u00b7  Alt + drag = copy  \u00b7  corners = resize  \u00b7  "
                             "round handle = turn (Shift = 15\u00b0 steps)  \u00b7  drag onto another page to move it there  \u00b7  Del = delete"
                             if on else "")

    def _items_changed(self):
        self._update_tools()

    def _sheet_in_use(self, i):
        pass

    def rebuild_now(self):
        self._rebuild()

    def _default_items(self, page, aw, ah, mode, pct, rotate):
        if page.blank:
            return []
        w, h, rot = pl.fit_geometry(page, aw, ah, mode, pct, rotate)
        return [self.model.new_item(page.key, aw / 2.0, ah / 2.0, w, h, rot)]

    def _rebuild(self):
        self._timer.stop()
        ticked = [p for p in self.order if p.key in self.checked]
        spec = self.numpanel.spec()
        nums = pl.numbering(self.order, self.checked, spec.keep_own)
        nup = pl.get_nup(self.cbNup.currentData())
        copies = self.spCopies.value()
        mode, pct = self.cbScale.currentData(), self.spPct.value()
        rotate = self.chkTurn.isChecked()
        transform = self.btnTransform.isChecked() and nup == 1
        aw, ah = self.area[2], self.area[3]
        lay = None
        if transform:
            for p in ticked:
                if p.key not in self.model.lay:
                    self.model.lay[p.key] = self._default_items(p, aw, ah, mode, pct, rotate)
            lay = self.model.lay
        sheets, self.pcopies = pl.plan(ticked, nums, nup, copies, lay)
        self.sheets = sheets
        shown = pl.display_sheets(sheets) if nup > 1 else [(s, 1) for s in sheets]
        grid = (1, 1)
        if nup > 1 and sheets:
            grid = pl.nup_grid(nup, aw, ah, sheets[0].pages[0].aspect)
        labels, self.first_sheet_of = [], {}
        for i, (s, count) in enumerate(shown):
            if nup > 1:
                text = "Sheet %d  (%d pages)" % (i + 1, len(s.pages)) + ("   \u00d7 %d" % count if count > 1 else "")
                if self.pcopies > 1:
                    text += "   \u00d7 %d copies" % self.pcopies
            else:
                pg = s.pages[0]
                text = "Page %d" % (i + 1) + (" \u00b7 " + pg.name if pg.name and not pg.blank else (" \u00b7 blank" if pg.blank else ""))
                if copies > 1:
                    text += "   \u00d7 %d copies" % copies
            labels.append(text)
            for pg in s.pages:
                self.first_sheet_of.setdefault(pg.key, i)
        opt = pl.Options(mode, pct, rotate, spec, grid, self.all_pages)
        self.canvas.configure(self.paper, self.area, [s for s, _ in shown], labels, opt, self.cbColor.currentData() == "mono", transform)
        self._update_tools()
        self.btnPrint.setEnabled(bool(sheets) and self.cbPrinter.count() > 0 and not self._busy_print)
        if not self._busy_print and self._prep_total_left() <= 0:
            total = len(sheets) * self.pcopies
            self.lblStatus.setText("%d sheet(s) will be printed%s." % (total, "" if self.cbPrinter.count() else " (no printer installed)"))

    def _prep_total_left(self):
        return getattr(self, "_prep_total", 0) - getattr(self, "_prep_n", 0)

    # ---------------------------------------------------------------- loading the pictures
    def _page_ready(self, key):
        self._prep_n += 1
        pg = self.all_pages.get(key)
        if pg is not None:
            self.listPages.blockSignals(True)           # an icon change must not count as "ticked"
            for r in range(self.listPages.count()):
                it = self.listPages.item(r)
                if it.data(Qt.ItemDataRole.UserRole) == key:
                    self._set_icon(it, pg)
            self.listPages.blockSignals(False)
            self.canvas.qcache.pop(key, None)
            if key not in self.model.edited:                # its size is known now: the normal Transform look is made again
                self.model.lay.pop(key, None)
        self.lblStatus.setText("Loading pictures %d / %d ..." % (self._prep_n, self._prep_total))
        if self.btnTransform.isChecked() or pl.get_nup(self.cbNup.currentData()) > 1:
            self._schedule()                                 # the layout depends on the picture's shape
        else:
            self.canvas.update()                             # the sheet of this page can be drawn now

    def _request_load(self, pages):
        """Makes the small screen copy of these pictures in the background (only the ones that are not made yet)."""
        new = [p for p in pages if not p.blank and p.preview is None and p.error is None and p.key not in self._requested]
        if not new:
            return
        self._requested.update(p.key for p in new)
        self._prep_total += len(new)
        self.lblStatus.setText("Loading pictures %d / %d ..." % (self._prep_n, self._prep_total))
        self._prep_pool.start(_PrepJob(self._prep_sig, new, lambda: self._closing))

    def _prep_done(self):
        if self._closing:
            return
        bad = [self.all_pages[k].name for k in self._requested if self.all_pages[k].preview is None]
        self._rebuild()
        if bad:
            self.lblStatus.setText("Could not read: %s" % ", ".join(bad[:3]))

    # ---------------------------------------------------------------- the list of pages
    def _set_icon(self, item, pg):
        if pg.blank:
            pm = QPixmap(44, 56)
            pm.fill(QColor(255, 255, 255))
            item.setIcon(QIcon(pm))
        elif pg.preview is not None:
            th = pg.preview.copy()
            th.thumbnail((88, 112))
            item.setIcon(QIcon(QPixmap.fromImage(pil_to_qimage(th))))

    def _fill_list(self, select=None):
        self.listPages.blockSignals(True)
        self.listPages.clear()
        for n, pg in enumerate(self.order):
            it = QListWidgetItem("%d.  %s" % (n + 1, "Blank page" if pg.blank else (pg.name or "picture")))
            it.setData(Qt.ItemDataRole.UserRole, pg.key)
            it.setFlags(it.flags() | Qt.ItemFlag.ItemIsUserCheckable)
            it.setCheckState(Qt.CheckState.Checked if pg.key in self.checked else Qt.CheckState.Unchecked)
            self._set_icon(it, pg)
            self.listPages.addItem(it)
        self.listPages.blockSignals(False)
        if self.order:
            row = 0
            if select is not None:
                row = next((i for i, p in enumerate(self.order) if p.key == select), 0)
            self.listPages.setCurrentRow(row)

    def _current_key(self):
        it = self.listPages.currentItem()
        return it.data(Qt.ItemDataRole.UserRole) if it is not None else None

    def _item_ticked(self, item):
        key = item.data(Qt.ItemDataRole.UserRole)
        if item.checkState() == Qt.CheckState.Checked:
            self.checked.add(key)
            self._request_load([self.all_pages[key]])
        else:
            self.checked.discard(key)
        self._schedule()

    def _tick_all(self, on):
        self.checked = {p.key for p in self.order} if on else set()
        if on:
            self._request_load(self.order)
        self.listPages.blockSignals(True)
        for r in range(self.listPages.count()):
            self.listPages.item(r).setCheckState(Qt.CheckState.Checked if on else Qt.CheckState.Unchecked)
        self.listPages.blockSignals(False)
        self._schedule()

    def _row_changed(self, row):
        key = self._current_key()
        pg = self.all_pages.get(key)
        self.btnDelBlank.setEnabled(bool(pg and pg.blank))
        i = self.first_sheet_of.get(key)
        if i is not None and i < len(self.canvas.sheets):
            self.canvas.scroll_to(i)

    def _move_row(self, d):
        key = self._current_key()
        idx = next((i for i, p in enumerate(self.order) if p.key == key), None)
        if idx is None or not 0 <= idx + d < len(self.order):
            return
        self.order[idx], self.order[idx + d] = self.order[idx + d], self.order[idx]
        self._fill_list(select=key)
        self._schedule()

    def _add_blank(self):
        key = self._current_key()
        idx = next((i for i, p in enumerate(self.order) if p.key == key), len(self.order) - 1)
        self.model.push(self.extra_state())
        self._blank_n += 1
        pg = pl.Page("blank:%d" % self._blank_n, "Blank page", blank=True)
        self.all_pages[pg.key] = pg
        self.order.insert(idx + 1, pg)
        self.checked.add(pg.key)
        self._fill_list(select=pg.key)
        self._schedule()

    def _del_blank(self):
        key = self._current_key()
        pg = self.all_pages.get(key)
        if not pg or not pg.blank:
            return
        self.model.push(self.extra_state())
        idx = self.order.index(pg)
        self.order.remove(pg)
        self.checked.discard(key)
        self.model.lay.pop(key, None)
        self._fill_list(select=self.order[max(0, idx - 1)].key if self.order else None)
        self._schedule()

    def _fill_add_menu(self):
        self.menuAdd.clear()
        for p in self.order:
            if p.blank:
                continue
            a = self.menuAdd.addAction(p.name or "picture")
            a.triggered.connect(lambda _=False, k=p.key: self.canvas.add_picture(k))

    # ---------------------------------------------------------------- undo / redo (pictures + blank pages together)
    def extra_state(self):
        return {"order": [p.key for p in self.order], "checked": set(self.checked)}

    def _restore_extra(self, ex):
        if not ex:
            return
        self.order = [self.all_pages[k] for k in ex["order"] if k in self.all_pages]
        self.checked = set(ex["checked"])
        self._fill_list(select=self._current_key())

    def do_undo(self):
        r = self.model.undo(self.extra_state())
        if r is not None:
            self._restore_extra(r[0])
            self._rebuild()

    def do_redo(self):
        r = self.model.redo(self.extra_state())
        if r is not None:
            self._restore_extra(r[0])
            self._rebuild()

    # ---------------------------------------------------------------- zoom
    def zoom_by(self, f):
        self.canvas.set_zoom(self.canvas.ppm * f)

    def fit_width(self):
        avail = self.scroll.viewport().width() - 2 * SheetCanvas.PAD - 4
        if avail > 50:
            self.canvas.set_zoom(avail / max(1.0, self.paper[0]))

    def zoom_100(self):
        self.canvas.set_zoom(self.logicalDpiX() / 25.4)

    def scroll_canvas_to(self, y):
        self.scroll.verticalScrollBar().setValue(int(max(0, y)))

    def showEvent(self, e):
        super().showEvent(e)
        if not self._fitted:
            self._fitted = True
            QTimer.singleShot(50, self.fit_width)

    def _say(self, text):
        self.lblStatus.setText(text)

    # ---------------------------------------------------------------- printing
    def _print(self):
        if self._driver is not None:
            return
        self._rebuild()
        if not self.sheets:
            self._say("Tick at least one page to print.")
            return
        if self.cbPrinter.count() == 0:
            self._say("No printer is installed.")
            return
        bad = [p.name for p in self.order if p.key in self.checked and not p.blank and p.preview is None and p.error]
        if bad:
            QMessageBox.warning(self, "Print", "These pages could not be read and would print white:\n%s" % "\n".join(bad[:5]))
            return
        if any(p.key in self.checked and not p.blank and p.preview is None and p.error is None for p in self.order):
            self._say("Please wait a moment: the pictures are still loading.")
            return
        pr = self._qprinter()
        try:
            self._apply_printer(pr)
        except Exception as ex:
            self._say("Could not set the printer: %s" % ex)
            return
        k = self.cbDuplex.currentData()
        two = k is not None and 0 <= k < len(self._duplex_modes) and self._duplex_modes[k] in (
            QPrinter.DuplexMode.DuplexLongSide, QPrinter.DuplexMode.DuplexShortSide)
        seq = pl.sequence(len(self.sheets), self.pcopies, two)
        pages = {p.key: p for p in self.all_pages.values()}
        opt = pl.Options(self.cbScale.currentData(), self.spPct.value(), self.chkTurn.isChecked(), self.numpanel.spec(),
                         self.canvas.opt.grid, pages)
        mono = self.cbColor.currentData() == "mono"
        # the sheets keep their own list of items; a copy, so nothing can change while they are being printed
        sheets = [pl.Sheet(s.kind, s.pages, s.nums, s.key, None if s.items is None else [dict(i) for i in s.items]) for s in self.sheets]
        self._driver = PrintDriver(pr, sheets, seq, opt, mono, self)
        self._driver.progress.connect(self._print_progress)
        self._driver.finished.connect(self._print_finished)
        self._set_printing(True)
        self.progress.setRange(0, len(seq))
        self.progress.setValue(0)
        self._say("Printing %d sheet(s) to %s ..." % (len(seq), self.cbPrinter.currentText()))
        self._driver.start()

    def _set_printing(self, on):
        self._busy_print = on
        for w in (self.listPages, self.canvas, self.btnTransform, self.numpanel, self.cbPrinter, self.cbPaper, self.cbOrient,
                  self.cbColor, self.spCopies, self.cbScale, self.chkTurn, self.cbNup, self.btnProps,
                  self.btnBlank, self.btnUp, self.btnDown, self.btnDelBlank):
            w.setEnabled(not on)
        self.spPct.setEnabled((not on) and self.cbScale.currentData() == "custom")
        self.cbDuplex.setEnabled((not on) and self.cbDuplex.count() > 1)
        for b in self.toolButtons:
            b.setEnabled(not on and self.btnTransform.isChecked())
        self.progress.setVisible(on)
        self.btnPrint.setText("Cancel printing" if on else "Print")
        self.btnPrint.setEnabled(True)
        try:
            self.btnPrint.clicked.disconnect()
        except Exception:
            pass
        self.btnPrint.clicked.connect(self._cancel_print if on else self._print)
        self.btnClose.setEnabled(not on)

    def _print_progress(self, done, total):
        self.progress.setValue(done)
        self._say("Printing %d / %d ..." % (done, total))

    def _cancel_print(self):
        if self._driver is not None:
            self._driver.cancel()
            self._say("Cancelling ...")

    def _print_finished(self, message):
        n = self.progress.maximum()
        self._driver = None
        self._set_printing(False)
        self._update_tools()
        self._say(("Sent %d sheet(s) to %s." % (n, self.cbPrinter.currentText())) if not message else message)

    def reject(self):
        if self._driver is not None:
            return
        self._closing = True
        super().reject()

    def closeEvent(self, e):
        if self._driver is not None:
            e.ignore()
            return
        self._closing = True
        super().closeEvent(e)
