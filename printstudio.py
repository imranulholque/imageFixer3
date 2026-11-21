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

from PIL import Image, ImageOps
import json
import os

from PySide6.QtCore import QObject, QPointF, QRect, QRectF, QRunnable, QSize, Qt, QThreadPool, QTimer, Signal
from PySide6.QtGui import (QColor, QCursor, QFont, QIcon, QImage, QPageLayout, QPageSize, QPainter, QPainterPath, QPen,
                           QPixmap, QPolygonF)
from PySide6.QtPrintSupport import QPrintDialog, QPrinter, QPrinterInfo
from PySide6.QtWidgets import (QAbstractItemView, QAbstractSpinBox, QApplication, QCheckBox, QComboBox, QDialog,
                               QDoubleSpinBox, QFormLayout, QFrame, QGridLayout, QGroupBox, QHBoxLayout, QInputDialog,
                               QLabel, QLineEdit, QListWidget, QListWidgetItem, QMenu, QMessageBox, QProgressBar,
                               QPushButton, QScrollArea, QSpinBox, QToolButton, QVBoxLayout, QWidget)

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
from numpanel import PageNumberPanel, load_numspec, save_numspec  # noqa: E402,F401  (moved: the main window's Numbers tab uses them too)


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
    selection = Signal()             # the selected pictures (or one of them, while dragging) changed

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
        self.sharp = True                # Sharp / Fast: only the picture on the screen, never the printout
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

    def set_sharp(self, on):
        self.sharp = bool(on)
        self.qcache.clear()
        self._reset_images()
        self.update()

    def target_sheet(self):
        """The sheet under the mouse, otherwise the sheet in use."""
        i = self.sheet_at(self.mapFromGlobal(QCursor.pos()))
        return i if i is not None else self.cur

    def _hdr_rects(self, i):
        """The two small buttons above a sheet (Transform): remove this page / add a blank page after it."""
        pr = self._paper_rect(i)
        dele = QRectF(pr.right() - 92, pr.y() - 23, 92, 18)
        add = QRectF(dele.left() - 122, pr.y() - 23, 116, 18)
        return dele, add

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
        rp = max(3.0, min(8.0, self.ppm * self.devicePixelRatioF())) if self.sharp else max(1.2, min(2.5, self.ppm * 0.6))
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
        side = 1000 if self.sharp else 450
        if max(pv.size) > side:
            pv = pv.copy()
            pv.thumbnail((side, side), Image.BILINEAR)
        q = pil_to_qimage(pv)
        self.qcache[pk] = q
        while len(self.qcache) > 40:
            self.qcache.popitem(last=False)
        return q

    # ---------------------------------------------------------------- painting
    def paintEvent(self, e):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform, self.sharp)
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
            dele, add = self._hdr_rects(i)
            p.setPen(QPen(QColor(150, 150, 150), 1))
            p.setBrush(QColor(255, 255, 255, 230))
            p.drawRoundedRect(add, 4, 4)
            p.setPen(QColor(30, 100, 40))
            p.drawText(add, Qt.AlignmentFlag.AlignCenter, "+ Blank page after")
            p.setPen(QPen(QColor(150, 150, 150), 1))
            p.drawRoundedRect(dele, 4, 4)
            p.setPen(QColor(190, 40, 40))
            p.drawText(dele, Qt.AlignmentFlag.AlignCenter, "\u2715 Remove page")
            p.setPen(self.palette().windowText().color())
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
        for si, sh in enumerate(self.sheets):                 # the two small buttons above every sheet
            if sh.kind in ("page", "blank"):
                dele, add = self._hdr_rects(si)
                if dele.contains(pos):
                    self.dlg.remove_page(sh.key)
                    return
                if add.contains(pos):
                    self.dlg.insert_blank_after(sh.key)
                    return
        hh = self._handle_hit(pos)
        if hh:
            si, it, h = hh
            self._push()
            self.drag = {"kind": "rot" if h == "rot" else "size", "handle": h, "si": si, "id": it["id"], "orig": dict(it)}
            self.selection.emit()
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
                self.selection.emit()
                self.update()
                return
        if not ctrl:
            model.select([])
        self.drag = {"kind": "rubber", "a": QPointF(pos), "b": QPointF(pos), "add": ctrl, "base": list(model.sel)}
        self.selection.emit()
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
            self.selection.emit()
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
            self.selection.emit()
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
            for si, sh in enumerate(self.sheets):
                if sh.kind in ("page", "blank") and any(r.contains(pos) for r in self._hdr_rects(si)):
                    self.setCursor(QCursor(Qt.CursorShape.PointingHandCursor))
                    return
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
        if not self.handle_key(e):
            e.ignore()

    def handle_key(self, e):
        """All the shortcuts of the print screen.  True = used.  (Also called by the window, so they work wherever the focus is.)"""
        K, KM = Qt.Key, Qt.KeyboardModifier
        k, mods = e.key(), e.modifiers()
        ctrl, shift, alt = bool(mods & KM.ControlModifier), bool(mods & KM.ShiftModifier), bool(mods & KM.AltModifier)
        if ctrl and k == K.Key_0:
            self.dlg.zoom_100()
        elif ctrl and (k == K.Key_Y or (k == K.Key_Z and shift)):
            self.dlg.do_redo()
        elif ctrl and k == K.Key_Z:
            self.dlg.do_undo()
        elif k == K.Key_R and not ctrl and not alt:          # works with Transform off too
            self.dlg.rotate_key(-90 if shift else 90)
        elif not self.transform:
            return False
        else:
            step = 10.0 if shift else 1.0
            arrows = {K.Key_Left: (-step, 0), K.Key_Right: (step, 0), K.Key_Up: (0, -step), K.Key_Down: (0, step)}
            if k in (K.Key_Delete, K.Key_Backspace):
                self.do_delete()
            elif ctrl and k == K.Key_C:
                self.do_copy()
            elif ctrl and k == K.Key_X:
                self.do_cut()
            elif ctrl and k == K.Key_V:
                self.do_paste()
            elif ctrl and k == K.Key_D:
                self.do_duplicate()
            elif ctrl and k == K.Key_A:
                self.do_select_all()
            elif not ctrl and k == K.Key_F:
                self.do_fit()
            elif not ctrl and k == K.Key_C:
                self.dlg.crop_selected()
            elif k == K.Key_Escape and self.dlg.model.sel:
                self.dlg.model.select([])
                self.selection.emit()
                self.update()
            elif k in arrows and self.dlg.model.sel:
                self._push()
                for key, it in self.dlg.model.selected():
                    it["cx"] += arrows[k][0]
                    it["cy"] += arrows[k][1]
                    self.dlg.model.touch(key)
                self.changed.emit()
                self.selection.emit()
                self.update()
            else:
                return False
        e.accept()
        return True

    def _done(self, text=""):
        if text:
            self.message.emit(text)
        self.changed.emit()
        self.selection.emit()
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

    def do_cut(self):
        if not self.dlg.model.sel:
            return
        self.do_copy()
        self.do_delete()

    def do_duplicate(self):
        m = self.dlg.model
        if not m.sel:
            return
        self._push()
        new = m.duplicate(list(m.sel), 6.0, 6.0)
        m.select(new)
        self._done("Duplicated %d picture(s)." % len(new))

    def do_fit(self):
        m = self.dlg.model
        sel = m.selected()
        if not sel:
            return
        self._push()
        by_sheet = {}
        for key, it in sel:
            by_sheet.setdefault(key, []).append(it)
        for key, items in by_sheet.items():                 # each page's pictures are fitted (together) inside the printable area
            pl.fit_group(items, self.area[2], self.area[3])
            m.touch(key)
        self._done("Fitted inside the printable area.")

    def do_paste(self):
        m = self.dlg.model
        if not m.clip:
            self.dlg.paste_clipboard_image()                 # nothing copied here: a picture from the clipboard becomes a page
            return
        i = self.target_sheet()
        if not self.sheets or not self.can_edit(i):
            return
        self._push()
        n = len(m.paste(self.sheets[i].key))
        self._done("Pasted %d picture(s) on page %d." % (n, i + 1))

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
        i = self.target_sheet()
        if self.can_edit(i):
            self.dlg.model.select_sheet(self.sheets[i].key)
            self.selection.emit()
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


# ================================================================================================ perspective crop
class CropView(QWidget):
    """The whole picture with 4 corner handles (like Photoshop's Perspective Crop).  The corners are numbers 0..1 of the whole
    picture (TL, TR, BR, BL).  Corner = moves that corner, edge handle = moves the whole edge, inside = moves the shape.
    Magnets: the picture's border and the straight line of the next corner (hold Alt to switch them off).
    Arrow keys move the chosen corner (1 pixel of the original, Shift = 10)."""
    HIT = 11
    SNAP_PX = 8

    def __init__(self, img, real_size, quad, parent=None):
        super().__init__(parent)
        self.img = img                                  # QImage (the screen copy)
        self.rw, self.rh = max(1, real_size[0]), max(1, real_size[1])
        self.q = list(quad)
        self.sel = -1
        self.drag = None
        self.setMouseTracking(True)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.setMinimumSize(620, 460)

    # ---- geometry
    def _rect(self):
        m = 22
        avail = QRectF(m, m, max(10, self.width() - 2 * m), max(10, self.height() - 2 * m))
        ar = self.img.width() / float(max(1, self.img.height()))
        w, h = avail.width(), avail.width() / ar
        if h > avail.height():
            w, h = avail.height() * ar, avail.height()
        return QRectF(avail.center().x() - w / 2, avail.center().y() - h / 2, w, h)

    def _pt(self, i, q=None):
        q = q or self.q
        r = self._rect()
        return QPointF(r.x() + q[2 * i] * r.width(), r.y() + q[2 * i + 1] * r.height())

    def _mid(self, i):
        a, b = self._pt(i), self._pt((i + 1) % 4)
        return QPointF((a.x() + b.x()) / 2, (a.y() + b.y()) / 2)

    def poly(self):
        return QPolygonF([self._pt(i) for i in range(4)])

    def quad(self):
        return list(self.q)

    def set_quad(self, q):
        self.q = list(q)
        self.sel = -1
        self.update()

    # ---- painting
    def paintEvent(self, e):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        p.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform, True)
        p.fillRect(self.rect(), QColor(40, 40, 44))
        r = self._rect()
        p.drawImage(r, self.img)
        path = QPainterPath()
        path.addRect(r)
        inner = QPainterPath()
        inner.addPolygon(self.poly())
        inner.closeSubpath()
        p.fillPath(path.subtracted(inner), QColor(0, 0, 0, 125))
        p.setPen(QPen(ACCENT, 2))
        p.setBrush(Qt.BrushStyle.NoBrush)
        p.drawPolygon(self.poly())
        for i in range(4):
            c = self._mid(i)
            p.setPen(QPen(ACCENT, 1.5))
            p.setBrush(QColor(255, 255, 255))
            p.drawEllipse(c, 5, 5)
        for i in range(4):
            c = self._pt(i)
            p.setPen(QPen(ACCENT, 2))
            p.setBrush(ACCENT if i == self.sel else QColor(255, 255, 255))
            p.drawRect(QRectF(c.x() - 6, c.y() - 6, 12, 12))
        d = self.drag
        if d and d["kind"] == "corner":
            self._loupe(p, d["i"])
        p.end()

    def _loupe(self, p, i):
        """A 3x magnifier next to the corner that is being moved."""
        c = self._pt(i)
        S, B = 40, 126
        sx = self.q[2 * i] * self.img.width() - S / 2
        sy = self.q[2 * i + 1] * self.img.height() - S / 2
        bx = c.x() + 26 if c.x() + 26 + B < self.width() else c.x() - 26 - B
        by = c.y() + 26 if c.y() + 26 + B < self.height() else c.y() - 26 - B
        box = QRectF(bx, by, B, B)
        p.save()
        p.setClipRect(box)
        p.fillRect(box, QColor(255, 255, 255))
        p.drawImage(box, self.img, QRectF(sx, sy, S, S))
        p.restore()
        p.setPen(QPen(ACCENT, 2))
        p.setBrush(Qt.BrushStyle.NoBrush)
        p.drawRect(box)
        p.setPen(QPen(QColor(255, 0, 0, 200), 1))
        p.drawLine(QPointF(box.center().x() - 8, box.center().y()), QPointF(box.center().x() + 8, box.center().y()))
        p.drawLine(QPointF(box.center().x(), box.center().y() - 8), QPointF(box.center().x(), box.center().y() + 8))

    # ---- mouse
    def mousePressEvent(self, e):
        self.setFocus()
        if e.button() != Qt.MouseButton.LeftButton:
            return
        pos = e.position()
        for i in range(4):
            c = self._pt(i)
            if abs(c.x() - pos.x()) <= self.HIT and abs(c.y() - pos.y()) <= self.HIT:
                self.sel = i
                self.drag = {"kind": "corner", "i": i, "start": QPointF(pos), "q0": list(self.q)}
                self.update()
                return
        for i in range(4):
            c = self._mid(i)
            if abs(c.x() - pos.x()) <= self.HIT and abs(c.y() - pos.y()) <= self.HIT:
                self.drag = {"kind": "edge", "i": i, "start": QPointF(pos), "q0": list(self.q)}
                return
        if self.poly().containsPoint(pos, Qt.FillRule.OddEvenFill):
            self.drag = {"kind": "all", "start": QPointF(pos), "q0": list(self.q)}

    def mouseMoveEvent(self, e):
        pos = e.position()
        d = self.drag
        if d is None:
            cur = Qt.CursorShape.ArrowCursor
            if any(abs(self._pt(i).x() - pos.x()) <= self.HIT and abs(self._pt(i).y() - pos.y()) <= self.HIT for i in range(4)):
                cur = Qt.CursorShape.CrossCursor
            elif self.poly().containsPoint(pos, Qt.FillRule.OddEvenFill):
                cur = Qt.CursorShape.SizeAllCursor
            self.setCursor(QCursor(cur))
            return
        r = self._rect()
        dx, dy = (pos.x() - d["start"].x()) / r.width(), (pos.y() - d["start"].y()) / r.height()
        q = list(d["q0"])
        free = bool(e.modifiers() & Qt.KeyboardModifier.AltModifier)
        if d["kind"] == "corner":
            i = d["i"]
            x, y = min(1.0, max(0.0, q[2 * i] + dx)), min(1.0, max(0.0, q[2 * i + 1] + dy))
            if not free:
                x, y = self._magnets(i, x, y, q, r)
            q[2 * i], q[2 * i + 1] = x, y
        else:
            idx = (d["i"], (d["i"] + 1) % 4) if d["kind"] == "edge" else (0, 1, 2, 3)
            xs, ys = [q[2 * k] for k in idx], [q[2 * k + 1] for k in idx]
            dx = max(-min(xs), min(1.0 - max(xs), dx))                 # the whole shape stays on the picture
            dy = max(-min(ys), min(1.0 - max(ys), dy))
            for k in idx:
                q[2 * k] += dx
                q[2 * k + 1] += dy
        if pl.quad_valid(q):
            self.q = q
            self.update()

    def _magnets(self, i, x, y, q, r):
        tx, ty = self.SNAP_PX / r.width(), self.SNAP_PX / r.height()
        for v in (0.0, 1.0):
            if abs(x - v) <= tx:
                x = v
            if abs(y - v) <= ty:
                y = v
        for j in ((i + 1) % 4, (i + 3) % 4):                         # in line with the next corner (straight edge)
            if abs(x - q[2 * j]) <= tx:
                x = q[2 * j]
            if abs(y - q[2 * j + 1]) <= ty:
                y = q[2 * j + 1]
        return x, y

    def mouseReleaseEvent(self, e):
        self.drag = None
        self.update()

    def keyPressEvent(self, e):
        K = Qt.Key
        if self.sel >= 0 and e.key() in (K.Key_Left, K.Key_Right, K.Key_Up, K.Key_Down):
            st = 10.0 if e.modifiers() & Qt.KeyboardModifier.ShiftModifier else 1.0
            q, i = list(self.q), self.sel
            if e.key() == K.Key_Left:
                q[2 * i] -= st / self.rw
            elif e.key() == K.Key_Right:
                q[2 * i] += st / self.rw
            elif e.key() == K.Key_Up:
                q[2 * i + 1] -= st / self.rh
            else:
                q[2 * i + 1] += st / self.rh
            q[2 * i], q[2 * i + 1] = min(1.0, max(0.0, q[2 * i])), min(1.0, max(0.0, q[2 * i + 1]))
            if pl.quad_valid(q):
                self.q = q
                self.update()
            e.accept()
            return
        super().keyPressEvent(e)


class CropDialog(QDialog):
    """Pull the 4 corners onto the area you want; Apply cuts it out straight (from the original picture, lossless)."""

    def __init__(self, parent, base_page, quad, had_crop, settings):
        super().__init__(parent)
        self.setWindowTitle("Perspective crop")
        self.settings = settings
        self.removed = False
        v = QVBoxLayout(self)
        tip = QLabel("Pull each corner onto a corner of the area you want (a skewed photo of a paper, a sign, a screen ...).  "
                     "Edge handle = moves a whole edge  \u00b7  inside = moves the shape  \u00b7  Alt = magnets off  \u00b7  "
                     "arrow keys move the chosen corner (Shift = 10 px)  \u00b7  Enter = apply  \u00b7  Esc = cancel")
        tip.setWordWrap(True)
        v.addWidget(tip)
        size = base_page.size or base_page.preview.size
        self.view = CropView(pil_to_qimage(base_page.preview), size, quad)
        v.addWidget(self.view, 1)
        self.chkFit = QCheckBox("Fit the cut-out inside the printable area afterwards")
        self.chkFit.setChecked(settings.value("print/cropfit", False, type=bool))
        v.addWidget(self.chkFit)
        row = QHBoxLayout()
        bReset = QPushButton("Reset corners")
        bReset.clicked.connect(lambda: self.view.set_quad([0.03, 0.03, 0.97, 0.03, 0.97, 0.97, 0.03, 0.97]))
        row.addWidget(bReset)
        self.bRemove = QPushButton("Remove crop")
        self.bRemove.setToolTip("Back to the whole picture")
        self.bRemove.setVisible(bool(had_crop))
        self.bRemove.clicked.connect(self._remove)
        row.addWidget(self.bRemove)
        row.addStretch(1)
        bCancel = QPushButton("Cancel")
        bCancel.clicked.connect(self.reject)
        bOk = QPushButton("Apply")
        bOk.setDefault(True)
        bOk.setProperty("accent", True)
        bOk.clicked.connect(self._ok)
        row.addWidget(bCancel)
        row.addWidget(bOk)
        v.addLayout(row)
        self.resize(900, 700)

    def _ok(self):
        self.settings.setValue("print/cropfit", self.chkFit.isChecked())
        self.accept()

    def _remove(self):
        self.removed = True
        self.settings.setValue("print/cropfit", self.chkFit.isChecked())
        self.accept()

    def quad(self):
        return self.view.quad()

    def fit_after(self):
        return self.chkFit.isChecked()


class HelpDialog(QDialog):
    ROWS = (("Click", "select a picture"), ("Ctrl + click", "add / remove from the selection"),
            ("Ctrl + A", "select all on the page under the mouse"), ("Drag", "move (all selected together)"),
            ("Alt + drag", "copy (all selected together)"), ("Shift + drag", "move / copy dead straight, stays on its page"),
            ("Corner handles", "resize  (Shift = free shape)"), ("Round handle", "turn  (Shift = 15\u00b0 steps)"),
            ("F  /  Fit", "fit the selected picture(s) inside the printable area"),
            ("C  /  Crop", "perspective crop of the selected picture (pull 4 corners, lossless)"),
            ("R  /  Shift + R", "turn 90\u00b0 clockwise / back"),
            ("R, nothing selected", "mouse on a page = that page  \u00b7  mouse elsewhere = all pages"),
            ("Arrow keys", "nudge 1 mm  (Shift = 10 mm)"), ("Drag to another page", "move it there  (Alt + drag = copy there)"),
            ("Ctrl + C / Ctrl + X", "copy / cut the selected pictures"),
            ("Ctrl + V", "paste on the page under the mouse (or a picture from the clipboard as a new page)"),
            ("Ctrl + D", "duplicate on the same page"), ("Ctrl + Z / Ctrl + Y", "undo / redo"),
            ("Delete", "remove the selected pictures"), ("Drop a picture file", "add it as a new page"),
            ("Ctrl + wheel / Ctrl + 0", "zoom / 100%"), ("Esc", "deselect"))

    def __init__(self, parent):
        super().__init__(parent)
        self.setWindowTitle("Print screen shortcuts")
        g = QGridLayout(self)
        g.setHorizontalSpacing(18)
        for n, (k, t) in enumerate(self.ROWS):
            a = QLabel("<b>%s</b>" % k)
            g.addWidget(a, n, 0)
            g.addWidget(QLabel(t), n, 1)
        b = QPushButton("Close")
        b.clicked.connect(self.accept)
        g.addWidget(b, len(self.ROWS), 1, Qt.AlignmentFlag.AlignRight)


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
        self._orig_order = list(self.order)              # for "Reset all"
        self._ui_ready = False                            # set when the whole window is built
        self.unit, self.lock = "mm", True                # the W / H / X / Y fields
        self._crop_pages, self._crop_n, self._img_n = {}, 0, 0
        self.setAcceptDrops(True)
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
        rr = QHBoxLayout()
        self.edRange = QLineEdit()
        self.edRange.setPlaceholderText("All pages \u00b7 e.g. 1-5, 8, 10-12")
        self.edRange.setClearButtonEnabled(True)
        self.edRange.setToolTip("Type the pages to print, for example 1-5, 8, 10-12, and press Enter.  Empty = all pages")
        self.edRange.returnPressed.connect(self._apply_range)
        bRange = QPushButton("Apply")
        bRange.clicked.connect(self._apply_range)
        rr.addWidget(self.edRange, 1)
        rr.addWidget(bRange)
        left.addLayout(rr)
        self.lblSelCount = QLabel("")
        left.addWidget(self.lblSelCount)
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
        self.btnDelBlank.setToolTip("Take the selected page out of the printout (Ctrl+Z brings it back)")
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
        self.btnTransform.setToolTip("Move, resize, turn, crop and copy the pictures on the sheets (not with several pages on one sheet)")
        self.btnTransform.toggled.connect(self._transform_toggled)
        bar.addWidget(self.btnTransform)
        self.toolButtons = []

        def tool(text, tip, fn, row=None):
            b = QToolButton()
            b.setText(text)
            b.setToolTip(tip)
            b.clicked.connect(fn)
            (row if row is not None else bar).addWidget(b)
            self.toolButtons.append(b)
            return b
        self.canvas = SheetCanvas(self)
        c = self.canvas
        tool("\u21ba 90", "Turn the picture 90\u00b0 to the left (Shift + R)", lambda: c.do_turn(-90))
        tool("\u21bb 90", "Turn the picture 90\u00b0 to the right (R)", lambda: c.do_turn(90))
        tool("Center", "Put the picture in the middle of the page", c.do_center)
        tool("Fit", "Fit the selected picture(s) inside the printable area, same shape, centred (F)", c.do_fit)
        self.btnCrop = tool("\u2702 Perspective crop", "Pull 4 corners onto the area you want; it is cut out straight, lossless (C)",
                            self.crop_selected)
        tool("Select all", "Select every picture on the page (Ctrl+A)", c.do_select_all)
        tool("Cut", "Cut the picture (Ctrl+X)", c.do_cut)
        tool("Copy", "Copy the picture (Ctrl+C). Alt + drag copies too", c.do_copy)
        tool("Paste", "Paste on the page under the mouse (Ctrl+V). With nothing copied, a picture from the clipboard becomes a page", c.do_paste)
        tool("Duplicate", "Copy on the same page (Ctrl+D)", c.do_duplicate)
        tool("Delete", "Delete the picture (Del)", c.do_delete)
        self.btnAddPic = QToolButton()
        self.btnAddPic.setText("Add picture \u25be")
        self.btnAddPic.setToolTip("Put another picture on the page in use")
        self.btnAddPic.setPopupMode(QToolButton.ToolButtonPopupMode.InstantPopup)
        self.menuAdd = QMenu(self)
        self.menuAdd.aboutToShow.connect(self._fill_add_menu)
        self.btnAddPic.setMenu(self.menuAdd)
        bar.addWidget(self.btnAddPic)
        self.toolButtons.append(self.btnAddPic)
        bar.addStretch(1)
        for text, tip, fn in (("\u2212", "Smaller", lambda: self.zoom_by(1 / 1.2)), ("+", "Bigger", lambda: self.zoom_by(1.2)),
                              ("Fit width", "The sheet fills the width", self.fit_width),
                              ("Fit page", "The whole sheet is in view", self.fit_page),
                              ("100%", "Zoom 100% (Ctrl+0)", self.zoom_100)):
            b = QToolButton()
            b.setText(text)
            b.setToolTip(tip)
            b.clicked.connect(fn)
            bar.addWidget(b)
        self.btnSharp = QToolButton()
        self.btnSharp.setCheckable(True)
        self.btnSharp.setChecked(True)
        self.btnSharp.setText("Sharp")
        self.btnSharp.setToolTip("Screen only (the printout is always full quality).\nSharp = pictures in original quality on the screen.\n"
                                 "Fast = light pictures on the screen: smoother with many or big pages.")
        self.btnSharp.toggled.connect(self._sharp_toggled)
        bar.addWidget(self.btnSharp)
        mid.addLayout(bar)

        # ---- the Transform dock: more tools + the numbers of the selected picture
        self.dock = QFrame()
        self.dock.setFrameShape(QFrame.Shape.StyledPanel)
        dv = QHBoxLayout(self.dock)
        dv.setContentsMargins(6, 3, 6, 3)
        dv.setSpacing(4)
        tool("Reset page", "Back to the normal look of the page under the mouse", c.do_reset_sheet, dv)
        tool("Reset all", "Back to the normal look of ALL pages: pictures, added / removed pages and turned pages", self.reset_all, dv)
        self.btnUndo = tool("Undo", "Ctrl+Z", self.do_undo, dv)
        self.btnRedo = tool("Redo", "Ctrl+Y", self.do_redo, dv)
        self.btnHelp = QToolButton()
        self.btnHelp.setText("?")
        self.btnHelp.setToolTip("All the shortcuts of this screen")
        self.btnHelp.clicked.connect(self.show_help)
        dv.addWidget(self.btnHelp)
        dv.addSpacing(14)
        self.lblNoSel = QLabel("Select a picture to type its size and place.")
        dv.addWidget(self.lblNoSel)
        self.fieldsBox = QWidget()
        fv = QHBoxLayout(self.fieldsBox)
        fv.setContentsMargins(0, 0, 0, 0)
        fv.setSpacing(4)

        def field(label, name):
            fv.addWidget(QLabel(label))
            sp = QDoubleSpinBox()
            sp.setButtonSymbols(QAbstractSpinBox.ButtonSymbols.NoButtons)
            sp.setKeyboardTracking(False)
            sp.setMaximumWidth(84)
            sp.editingFinished.connect(lambda n=name: self._field_edited(n))
            fv.addWidget(sp)
            return sp
        self.spW = field("W", "w")
        self.btnLock = QToolButton()
        self.btnLock.setCheckable(True)
        self.btnLock.setChecked(True)
        self.btnLock.setText("Lock")
        self.btnLock.setToolTip("Locked: typing W or H keeps the shape of the picture")
        self.btnLock.toggled.connect(lambda on: setattr(self, "lock", bool(on)))
        fv.addWidget(self.btnLock)
        self.spH = field("H", "h")
        self.spX = field("X", "x")
        self.spY = field("Y", "y")
        self.spR = field("Rot", "rot")
        self.spR.setSuffix("\u00b0")
        self.unitBtns = {}
        for u in ("mm", "cm", "in"):
            b = QToolButton()
            b.setText(u)
            b.setCheckable(True)
            b.setChecked(u == "mm")
            b.clicked.connect(lambda _=False, u=u: self._unit_changed(u))
            fv.addWidget(b)
            self.unitBtns[u] = b
        self.lblMulti = QLabel("")
        fv.addWidget(self.lblMulti)
        dv.addWidget(self.fieldsBox)
        dv.addStretch(1)
        self.fieldsBox.hide()
        self._unit_changed("mm")
        mid.addWidget(self.dock)
        self.dock.hide()
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
        self.canvas.selection.connect(self.sync_fields)
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

        g4 = QGroupBox("Presets")
        v4 = QVBoxLayout(g4)
        self.cbPreset = QComboBox()
        self.cbPreset.setToolTip("Printer, paper, orientation, colour, two-sided, copies and layout, saved under a name")
        self.cbPreset.activated.connect(self._preset_apply)
        r4 = QHBoxLayout()
        self.btnPresetSave, self.btnPresetDel = QPushButton("Save as..."), QPushButton("Delete")
        self.btnPresetSave.clicked.connect(self._preset_save)
        self.btnPresetDel.clicked.connect(self._preset_delete)
        r4.addWidget(self.btnPresetSave)
        r4.addWidget(self.btnPresetDel)
        v4.addWidget(self.cbPreset)
        v4.addLayout(r4)
        pv.addWidget(g4)
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
        self._ui_ready = True
        self._restore_settings()
        self._fill_presets()
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
        sharp = s.value("print/sharp", True, type=bool)
        self.btnSharp.blockSignals(True)
        self.btnSharp.setChecked(sharp)
        self.btnSharp.setText("Sharp" if sharp else "Fast")
        self.btnSharp.blockSignals(False)
        self.canvas.sharp = sharp

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
        if on:
            self._say("Transform: drag a picture to move it (Shift = straight, Alt = copy), the corners resize, the round handle turns.  ? = all shortcuts.")
        elif self.model.edited:
            self._say("Your Transform changes are kept for printing.  Turn Transform on and press Reset all to undo them.")

    def _update_tools(self):
        on = self.btnTransform.isChecked()
        for b in self.toolButtons:
            b.setEnabled(on)
        if on:
            self.btnUndo.setEnabled(self.model.can_undo)
            self.btnRedo.setEnabled(self.model.can_redo)
        self.btnTransform.setEnabled(pl.get_nup(self.cbNup.currentData()) == 1)
        self.dock.setVisible(on)
        if on:
            self.btnCrop.setEnabled(len(self.model.sel) == 1)
        self.lblHint.setText("Drag = move  \u00b7  Shift + drag = straight  \u00b7  Alt + drag = copy  \u00b7  corners = resize  \u00b7  "
                             "round handle = turn (Shift = 15\u00b0 steps)  \u00b7  drag onto another page to move it there  \u00b7  Del = delete"
                             if on else "")

    def _items_changed(self):
        self._update_tools()
        self.sync_fields()

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
        elif nup == 1 and self.model.edited:                       # changed in Transform: stays in use when it is switched off
            lay = {k: v for k, v in self.model.lay.items() if k in self.model.edited} or None
        self._update_count()
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
        self.btnDelBlank.setEnabled(pg is not None)
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
        self.insert_blank_after(self._current_key())

    def insert_blank_after(self, key):
        """A blank white page after the page called key (the + button above a sheet, or the list's + Blank page)."""
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
        self.remove_page(self._current_key())

    def remove_page(self, key):
        """Takes a page out of the printout completely (Ctrl+Z brings it back)."""
        pg = self.all_pages.get(key)
        if pg is None or pg not in self.order or self._busy_print:
            return
        self.model.push(self.extra_state())
        idx = self.order.index(pg)
        self.order.remove(pg)
        self.checked.discard(key)
        self.model.lay.pop(key, None)
        self.model.edited.discard(key)
        self.model.sel = []
        self._fill_list(select=self.order[max(0, idx - 1)].key if self.order else None)
        self._schedule()
        self._say("Page removed.  Ctrl+Z brings it back.")

    def _fill_add_menu(self):
        self.menuAdd.clear()
        for p in self.order:
            if p.blank:
                continue
            a = self.menuAdd.addAction(p.name or "picture")
            a.triggered.connect(lambda _=False, k=p.key: self.canvas.add_picture(k))

    # ---------------------------------------------------------------- undo / redo (pictures + blank pages together)
    def extra_state(self):
        return {"order": [p.key for p in self.order], "checked": set(self.checked),
                "turns": {k: p.turn for k, p in self.all_pages.items() if p.turn}}

    def _restore_extra(self, ex):
        if not ex:
            return
        self.order = [self.all_pages[k] for k in ex["order"] if k in self.all_pages]
        self.checked = set(ex["checked"])
        for k, p in self.all_pages.items():
            want = ex.get("turns", {}).get(k, 0)
            if p.turn != want:
                p.turn_by(want - p.turn)
                self.canvas.qcache.pop(k, None)
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

    # ---------------------------------------------------------------- page range
    def _update_count(self):
        n, total = len([p for p in self.order if p.key in self.checked]), len(self.order)
        self.lblSelCount.setText("%d of %d selected" % (n, total))
        if not self.edRange.hasFocus():
            nums = [i + 1 for i, p in enumerate(self.order) if p.key in self.checked]
            self.edRange.setText("" if n == total or n == 0 else pl.compress_range(nums))

    def _apply_range(self):
        text = self.edRange.text().strip()
        if not text:
            self._tick_all(True)
            self._update_count()
            return
        total = len(self.order)
        nums, bad, over = pl.parse_range(text, total)
        if bad:
            self._say("Cannot read: %s.  Use a format like 1-5, 8, 10-12" % ", ".join(bad))
            return
        if not nums:
            self._say("No such page.  There are %d page(s)." % total)
            return
        self.checked = {p.key for i, p in enumerate(self.order) if (i + 1) in nums}
        self._request_load([p for p in self.order if p.key in self.checked])
        self.listPages.blockSignals(True)
        for r in range(self.listPages.count()):
            k = self.listPages.item(r).data(Qt.ItemDataRole.UserRole)
            self.listPages.item(r).setCheckState(Qt.CheckState.Checked if k in self.checked else Qt.CheckState.Unchecked)
        self.listPages.blockSignals(False)
        self.edRange.clearFocus()
        self._schedule()
        self._say("Pages beyond %d were ignored." % total if over else "%d page(s) ticked." % len(nums))

    # ---------------------------------------------------------------- the numbers (W / H / X / Y / Rot)
    def _unit_changed(self, u):
        self.unit = u
        for k, b in self.unitBtns.items():
            b.blockSignals(True)
            b.setChecked(k == u)
            b.blockSignals(False)
        dec = {"mm": 1, "cm": 2, "in": 3}[u]
        f = pl.UNITS[u]
        for sp in (self.spW, self.spH, self.spX, self.spY):
            sp.setDecimals(dec)
            sp.setRange(-30000.0 / f, 30000.0 / f)
        self.spR.setDecimals(1)
        self.spR.setRange(-360.0, 360.0)
        if self._ui_ready:
            self.sync_fields()

    def sync_fields(self):
        """Shows the numbers of the selected picture (the last one selected)."""
        on = self.btnTransform.isChecked()
        sel = self.model.selected() if on else []
        self._update_tools()
        self.fieldsBox.setVisible(bool(sel))
        self.lblNoSel.setVisible(not sel)
        if not sel:
            return
        key, it = sel[-1]
        multi = len(sel) > 1
        vals = {self.spW: it["w"], self.spH: it["h"], self.spX: it["cx"], self.spY: it["cy"]}
        for sp, v in vals.items():
            if not sp.hasFocus():
                sp.blockSignals(True)
                sp.setValue(float(pl.unit_text(v, self.unit)))
                sp.blockSignals(False)
        if not self.spR.hasFocus():
            self.spR.blockSignals(True)
            self.spR.setValue(round(it["rot"] % 360.0, 1))
            self.spR.blockSignals(False)
        self.spX.setEnabled(not multi)
        self.spY.setEnabled(not multi)
        self.lblMulti.setText("%d selected" % len(sel) if multi else "")

    def _field_edited(self, name):
        if not self.btnTransform.isChecked() or self._busy_print:
            return
        sel = self.model.selected()
        if not sel:
            return
        f = pl.UNITS[self.unit]
        sp = {"w": self.spW, "h": self.spH, "x": self.spX, "y": self.spY, "rot": self.spR}[name]
        v = sp.value() if name == "rot" else sp.value() * f
        if name in ("w", "h") and v <= 0:
            return
        kw = {name: v}
        targets = sel if name not in ("x", "y") else sel[-1:]
        probe = [dict(it) for _k, it in targets]
        if not any(self.model.set_fields(pi, k, lock=self.lock, **kw) for pi, (k, _it) in zip(probe, targets)):
            return
        self.model.push(self.extra_state())
        for key, it in targets:
            self.model.set_fields(it, key, lock=self.lock, **kw)
        self._update_tools()
        self.canvas.update()
        self.sync_fields()

    # ---------------------------------------------------------------- perspective crop
    def _crop_page(self, base, q):
        sig = (base.key, tuple(round(v, 6) for v in q))
        if sig in self._crop_pages:
            return self._crop_pages[sig]
        self._crop_n += 1
        pg = pl.make_crop_page("crop:%d" % self._crop_n, base, q)
        self.all_pages[pg.key] = pg
        self._crop_pages[sig] = pg
        return pg

    def crop_selected(self):
        sel = self.model.selected()
        if len(sel) != 1:
            self._say("Select one picture, then press C (Perspective crop).")
            return
        key, it = sel[0]
        pg = self.all_pages.get(it["pk"])
        if pg is None or pg.blank:
            return
        base = pg.base if pg.base is not None else pg
        start = pg.quad if pg.base is not None else pl.full_quad()
        if base.preview is None:
            self._request_load([base])
            self._say("The picture is still loading.  Try again in a moment.")
            return
        dlg = CropDialog(self, base, start, pg.base is not None, self.settings)
        if dlg.exec() != QDialog.DialogCode.Accepted:
            return
        q = dlg.quad()
        full = dlg.removed or pl.quad_is_full(q)
        if full and pg.base is None:
            return                                                   # nothing was cropped, nothing to remove
        self.model.push(self.extra_state())
        new = base if full else self._crop_page(base, q)
        nw, nh = new.natural_mm()
        sc = min(it["w"] / nw, it["h"] / nh)                          # the cut-out fits inside the box the picture had, same centre
        if dlg.fit_after() and not full:
            aw, ah = self.area[2], self.area[3]
            if int(round(it["rot"])) % 180 == 90:
                aw, ah = ah, aw
            sc = min(aw / nw, ah / nh)
            it["cx"], it["cy"] = self.area[2] / 2.0, self.area[3] / 2.0
        it["pk"] = new.key
        it["w"], it["h"] = nw * sc, nh * sc
        self.model.touch(key)
        self._request_load([new])
        self.canvas.qcache.pop(new.key, None)
        self._schedule()
        self.canvas.update()
        self.sync_fields()
        self._say("Crop removed." if full else "Perspective crop applied.  Press C again to change the corners.")

    # ---------------------------------------------------------------- turning pages (R) / reset all
    def rotate_key(self, deg):
        """R: turns the selected pictures; with nothing selected the page under the mouse, mouse elsewhere = all pages."""
        if self._busy_print:
            return
        if self.btnTransform.isChecked() and self.model.sel:
            self.canvas.do_turn(deg)
            return
        vp = self.scroll.viewport()
        gp = QCursor.pos()
        i = self.canvas.sheet_at(self.canvas.mapFromGlobal(gp)) if vp.rect().contains(vp.mapFromGlobal(gp)) else None
        if i is not None and i < len(self.canvas.sheets):
            pages = [p for p in self.canvas.sheets[i].pages if not p.blank]
            where = "Page %d" % (i + 1)
        else:
            pages = [p for p in self.order if p.key in self.checked and not p.blank]
            where = "All pages"
        self.rotate_pages(pages, deg, where)

    def rotate_pages(self, pages, deg, where=""):
        if not pages:
            return
        self.model.push(self.extra_state())
        keys = {p.key for p in pages}
        for p in pages:
            p.turn_by(deg)
            self.canvas.qcache.pop(p.key, None)
            if p.key not in self.model.edited:
                self.model.lay.pop(p.key, None)                      # gets its normal look again, now for the turned shape
        if int(round(deg / 90.0)) % 2:
            for key, items in self.model.lay.items():
                if key in self.model.edited:
                    for it in items:
                        if it["pk"] in keys:
                            it["w"], it["h"] = it["h"], it["w"]
        self.listPages.blockSignals(True)
        for r in range(self.listPages.count()):
            k = self.listPages.item(r).data(Qt.ItemDataRole.UserRole)
            if k in keys:
                self._set_icon(self.listPages.item(r), self.all_pages[k])
        self.listPages.blockSignals(False)
        self._schedule()
        self._say("%s turned %d\u00b0 %s." % (where, abs(deg), "clockwise" if deg > 0 else "back"))

    def reset_all(self):
        """Back to the normal look of every page: pictures, added / removed pages and turned pages."""
        if self._busy_print:
            return
        self.model.push(self.extra_state())
        for p in self._orig_order:
            if p not in self.order:
                self.checked.add(p.key)                               # a removed page comes back, ticked
        self.order = list(self._orig_order)
        self.checked &= {p.key for p in self.order}
        for p in self.all_pages.values():
            if p.turn:
                p.turn_by(-p.turn)
                self.canvas.qcache.pop(p.key, None)
        self.model.lay.clear()
        self.model.edited.clear()
        self.model.sel = []
        self._fill_list(select=self._current_key())
        self._request_load([p for p in self.order if p.key in self.checked])
        self._schedule()
        self._say("Everything is back to the normal look.  Ctrl+Z undoes this.")

    def show_help(self):
        HelpDialog(self).exec()

    # ---------------------------------------------------------------- adding pictures as pages (clipboard / drop)
    def _add_page_after_current(self, page):
        key = self._current_key()
        idx = next((i for i, p in enumerate(self.order) if p.key == key), len(self.order) - 1)
        self.model.push(self.extra_state())
        self.all_pages[page.key] = page
        self.order.insert(idx + 1, page)
        self.checked.add(page.key)
        self._fill_list(select=page.key)
        self._request_load([page])
        self._schedule()

    def add_image_file(self, path):
        try:
            with Image.open(path) as im:
                dpi = im.info.get("dpi", (300, 300))[0]
        except Exception:
            self._say("Could not read %s" % os.path.basename(path))
            return False

        def loader(p=path):
            im = Image.open(p)
            im.load()
            return ImageOps.exif_transpose(im)
        self._img_n += 1
        try:
            dpi = float(dpi)
        except Exception:
            dpi = 300.0
        self._add_page_after_current(pl.Page("img:%d" % self._img_n, os.path.basename(path), loader=loader,
                                             dpi=dpi if 36 <= dpi <= 2400 else 300.0))
        return True

    def paste_clipboard_image(self):
        md = QApplication.clipboard().mimeData()
        if md is not None and md.hasUrls():
            n = sum(1 for u in md.urls() if u.isLocalFile() and self.add_image_file(u.toLocalFile()))
            if n:
                self._say("Added %d picture(s) as new page(s)." % n)
                return
        q = QApplication.clipboard().image()
        if q.isNull():
            self._say("Nothing to paste.")
            return
        q = q.convertToFormat(QImage.Format.Format_RGBA8888)
        im = Image.frombuffer("RGBA", (q.width(), q.height()), bytes(q.constBits()), "raw", "RGBA", q.bytesPerLine(), 1).copy()
        self._img_n += 1
        self._add_page_after_current(pl.Page("img:%d" % self._img_n, "Pasted picture", loader=lambda im=im: im.copy(), dpi=150.0))
        self._say("Pasted the clipboard picture as a new page.")

    IMG_EXT = (".png", ".jpg", ".jpeg", ".bmp", ".gif", ".tif", ".tiff", ".webp")

    def dragEnterEvent(self, e):
        if not self._busy_print and e.mimeData().hasUrls():
            e.acceptProposedAction()

    def dropEvent(self, e):
        n = 0
        for u in e.mimeData().urls():
            f = u.toLocalFile()
            if f and f.lower().endswith(self.IMG_EXT) and self.add_image_file(f):
                n += 1
        if n:
            self._say("Added %d picture(s) as new page(s)." % n)
            e.acceptProposedAction()

    def keyPressEvent(self, e):
        if not self._busy_print and self.canvas.handle_key(e):
            return
        super().keyPressEvent(e)

    # ---------------------------------------------------------------- Sharp / Fast, Fit page
    def _sharp_toggled(self, on):
        self.btnSharp.setText("Sharp" if on else "Fast")
        self.settings.setValue("print/sharp", bool(on))
        self.canvas.set_sharp(on)

    def fit_page(self):
        vp = self.scroll.viewport()
        aw = (vp.width() - 2 * SheetCanvas.PAD - 4) / max(1.0, self.paper[0])
        ah = (vp.height() - 2 * SheetCanvas.GAP) / max(1.0, self.paper[1])
        if aw > 0 and ah > 0:
            self.canvas.set_zoom(min(aw, ah))
            self.canvas.scroll_to(self.canvas.cur)

    # ---------------------------------------------------------------- presets
    PRESET_KEYS = ("printer", "paper", "orient", "color", "duplex", "copies", "scale", "pct", "turn", "nup")

    def _presets(self):
        try:
            d = json.loads(self.settings.value("print/presets", "{}", type=str) or "{}")
            return d if isinstance(d, dict) else {}
        except Exception:
            return {}

    def _fill_presets(self, select=""):
        self.cbPreset.blockSignals(True)
        self.cbPreset.clear()
        self.cbPreset.addItem("Presets ...", "")
        for name in sorted(self._presets(), key=str.lower):
            self.cbPreset.addItem(name, name)
        i = self.cbPreset.findData(select)
        self.cbPreset.setCurrentIndex(max(0, i))
        self.cbPreset.blockSignals(False)
        self.btnPresetDel.setEnabled(self.cbPreset.count() > 1)

    def _preset_save(self):
        name, ok = QInputDialog.getText(self, "Save preset", "Name of the preset:", text=self.cbPreset.currentData() or "")
        name = (name or "").strip()
        if not ok or not name:
            return
        d = self._presets()
        d[name] = {"printer": self.cbPrinter.currentData() or "", "paper": self.cbPaper.currentText(),
                   "orient": self.cbOrient.currentData(), "color": self.cbColor.currentData(),
                   "duplex": self.cbDuplex.currentIndex(), "copies": self.spCopies.value(),
                   "scale": self.cbScale.currentData(), "pct": self.spPct.value(), "turn": self.chkTurn.isChecked(),
                   "nup": self.cbNup.currentData()}
        self.settings.setValue("print/presets", json.dumps(d))
        self._fill_presets(name)
        self._say("Preset \"%s\" saved." % name)

    def _preset_apply(self, *_):
        name = self.cbPreset.currentData()
        d = self._presets().get(name) if name else None
        if not d:
            return
        s = self.settings
        for k in ("printer", "paper", "orient", "color", "duplex", "scale", "pct", "turn", "nup"):
            if k in d:
                s.setValue("print/" + k, d[k])
        self._fill_printers()                                          # reads printer / paper / two-sided from the settings
        self._restore_settings()
        self.spCopies.setValue(max(1, min(99, int(d.get("copies", 1) or 1))))
        self._geometry_changed()
        self._update_tools()
        self._say("Preset \"%s\" applied." % name)

    def _preset_delete(self):
        name = self.cbPreset.currentData()
        if not name:
            return
        d = self._presets()
        d.pop(name, None)
        self.settings.setValue("print/presets", json.dumps(d))
        self._fill_presets()

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
                  self.btnBlank, self.btnUp, self.btnDown, self.btnDelBlank, self.edRange, self.cbPreset, self.btnPresetSave,
                  self.btnPresetDel, self.dock):
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
