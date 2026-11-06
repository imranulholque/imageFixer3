"""One picture = one card.
Top: dropdown tool menus (Select / Move / Erase / Crop) + eyedropper.   Middle: the picture.
Right: tabs  Adjust | Shadow | Text | Save.   Every change acts ONLY inside the selection (if there is one)."""
import copy
import math
import os
import tempfile
import uuid

import numpy as np
from PIL import Image
from PySide6.QtCore import QMimeData, Qt, QTimer, QUrl, Signal
from PySide6.QtGui import QColor, QDrag, QImage, QPixmap
from PySide6.QtWidgets import (QApplication, QButtonGroup, QCheckBox, QColorDialog, QFrame, QHBoxLayout, QLabel, QMenu,
                               QPushButton, QScrollArea, QSizePolicy, QSlider, QStackedWidget, QToolButton,
                               QVBoxLayout, QWidget)

import core
from canvas import ImageCanvas, np_to_qimage
from jobs import _log, run_background

EXPORT_DIR = os.path.join(tempfile.gettempdir(), "imagefixer_export")

# tool key -> (canvas mode, shape)
TOOL_MODE = {
    "view": (ImageCanvas.VIEW, None), "pick": (ImageCanvas.PICK, None),
    "quick": (ImageCanvas.SELECT, "quick"), "brush": (ImageCanvas.SELECT, "brush"),
    "rect": (ImageCanvas.SELECT, "rect"), "ellipse": (ImageCanvas.SELECT, "ellipse"),
    "lasso": (ImageCanvas.SELECT, "lasso"), "color": (ImageCanvas.SELECT, "color"),
    "move": (ImageCanvas.MOVESEL, None), "copy": (ImageCanvas.MOVESEL, None),
    "transform": (ImageCanvas.TRANSFORM, None),
    "crop": (ImageCanvas.CROP, None), "persp": (ImageCanvas.PERSP, None),
    "erase_brush": (ImageCanvas.ERASE, "brush"), "erase_rect": (ImageCanvas.ERASE, "rect"),
    "erase_ellipse": (ImageCanvas.ERASE, "ellipse"), "erase_lasso": (ImageCanvas.ERASE, "lasso"),
}
SELECT_KEYS = ("quick", "brush", "rect", "ellipse", "lasso", "color")
MOVE_KEYS = ("move", "copy", "transform")
ERASE_KEYS = ("erase_brush", "erase_rect", "erase_ellipse", "erase_lasso")
CROP_KEYS = ("crop", "persp")
NAMES = {"quick": "Quick select", "brush": "Brush select", "rect": "Box", "ellipse": "Ellipse", "lasso": "Freehand",
         "color": "Color range", "move": "Move", "copy": "Copy", "transform": "Transform",
         "erase_brush": "Brush", "erase_rect": "Box", "erase_ellipse": "Ellipse", "erase_lasso": "Freehand",
         "crop": "Crop", "persp": "Straighten"}
HINTS = {
    "view": "Hand: drag the picture.  (Hold Space and drag in any tool, or Ctrl + wheel to zoom)",
    "pick": "Click a color in the picture (ink, stamp, text): its slider appears in the Adjust tab.",
    "quick": "Brush over an object: the selection grows to its edges.  Shift = add,  Alt = subtract.",
    "brush": "Paint the area to select.  Shift = add,  Alt = subtract.",
    "rect": "Drag a box.  Shift = add,  Alt = subtract.",
    "ellipse": "Drag an ellipse.  Shift = add,  Alt = subtract.",
    "lasso": "Draw freely around an area like with a pen. When you let go it closes by itself.  Shift = add,  Alt = subtract.",
    "color": "Click a color: every place with that color is selected (Tolerance = how wide).",
    "move": "Drag inside the selection to move it (old place is filled from around).  Alt + drag = copy.",
    "copy": "Drag the selection: a copy is placed there, the original stays.",
    "transform": "Drag inside = move.  Square handles = resize (corner keeps shape, Shift = free).  Round handle on top (or drag outside) = rotate.  "
                 "Enter = apply,  Esc = cancel.",
    "crop": "Drag the box or its handles, then Apply (Enter).",
    "persp": "Drag the 4 corner points onto the 4 corners of the page, then Apply (Enter).",
    "erase_brush": "Paint over what you want to remove: it is filled with what surrounds it.  Alt + click = copy from there.",
    "erase_rect": "Drag a box over what you want to remove.",
    "erase_ellipse": "Drag an ellipse over what you want to remove.",
    "erase_lasso": "Draw around what you want to remove.",
}
COLOR_HINT = ("Eyedropper: click a color in the picture.\n"
              "Lower than 100%: it fades into the paper.  Higher: stronger.  'Change to' recolors it.")
LOOK_TITLES = {"bright": "Brightness", "contrast": "Contrast", "sat": "Saturation", "hue": "Hue", "warm": "Warmth",
               "sharp": "Text sharpness", "objs": "Line & shape sharpness", "detail": "Faint strokes",
               "dark": "Text darkness", "clear": "Clean specks"}
SHAPES = {"erase_brush": "brush", "erase_rect": "rect", "erase_ellipse": "ellipse", "erase_lasso": "lasso"}


class NoWheelSlider(QSlider):
    def wheelEvent(self, e):
        e.ignore()


class SliderRow(QWidget):
    """title + value + slider (+ small captions under it)."""
    changed = Signal(int)

    def __init__(self, title, lo, hi, value, caps=None, tip="", unit="%", parent=None):
        super().__init__(parent)
        self._default = value
        self._unit = unit
        v = QVBoxLayout(self)
        v.setContentsMargins(0, 0, 0, 0)
        v.setSpacing(4)
        top = QHBoxLayout()
        top.setSpacing(8)
        self.title = QLabel(title)
        self.title.setObjectName("sub")
        self.val = QLabel("%d%s" % (value, unit))
        self.val.setObjectName("muted")
        self.val.setMinimumWidth(46)
        self.val.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        self.btnReset = QPushButton("↺")
        self.btnReset.setProperty("flat", True)
        self.btnReset.setToolTip("Back to %d%s" % (value, unit))
        self.btnReset.setFixedWidth(30)
        top.addWidget(self.title, 1)
        top.addWidget(self.val)
        top.addWidget(self.btnReset)
        v.addLayout(top)
        self.sl = NoWheelSlider(Qt.Orientation.Horizontal)
        self.sl.setRange(lo, hi)
        self.sl.setValue(value)
        self.sl.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        if tip:
            self.sl.setToolTip(tip)
            self.title.setToolTip(tip)
        v.addWidget(self.sl)
        if caps:
            cap = QHBoxLayout()
            for i, text in enumerate(caps):
                lab = QLabel(text)
                lab.setObjectName("hint")
                lab.setStyleSheet("font-size: 10px;")
                if i == 1:
                    lab.setAlignment(Qt.AlignmentFlag.AlignCenter)
                elif i == 2:
                    lab.setAlignment(Qt.AlignmentFlag.AlignRight)
                cap.addWidget(lab, 1)
            v.addLayout(cap)
        self.sl.valueChanged.connect(self._on)
        self.btnReset.clicked.connect(lambda: self.sl.setValue(self._default))

    def _on(self, v):
        self.val.setText("%d%s" % (v, self._unit))
        self.changed.emit(int(v))

    def value(self):
        return self.sl.value()

    def set_value(self, v):
        self.sl.blockSignals(True)
        self.sl.setValue(int(v))
        self.sl.blockSignals(False)
        self.val.setText("%d%s" % (int(v), self._unit))


class AdjRow(QFrame):
    """swatch + name + slider (100% = original, 0% = removed, 200% = stronger / darker)."""
    changed = Signal()
    removed = Signal(str)
    activated = Signal(str)
    deactivated = Signal()
    selectRequested = Signal(str)

    def __init__(self, adj, removable=True, parent=None):
        super().__init__(parent)
        self.setObjectName("adjRow")
        self.adj = adj
        self._down = False
        v = QVBoxLayout(self)
        v.setContentsMargins(12, 10, 12, 10)
        v.setSpacing(7)
        top = QHBoxLayout()
        top.setSpacing(9)
        sw = QFrame()
        sw.setFixedSize(24, 24)
        r, g, b = adj["rgb"]
        sw.setStyleSheet("background: rgb(%d,%d,%d); border-radius: 7px; border: 1px solid #4a5268;" % (r, g, b))
        self.name = QLabel(adj["label"])
        self.name.setObjectName("sub")
        self.val = QLabel("%d%%" % adj["k"])
        self.val.setObjectName("muted")
        self.val.setMinimumWidth(40)
        self.val.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        self.btnReset = QPushButton("↺")
        self.btnReset.setProperty("flat", True)
        self.btnReset.setToolTip("Back to 100%")
        self.btnReset.setFixedWidth(30)
        top.addWidget(sw)
        top.addWidget(self.name, 1)
        top.addWidget(self.val)
        top.addWidget(self.btnReset)
        if removable:
            self.btnDel = QPushButton("✕")
            self.btnDel.setProperty("flat", True)
            self.btnDel.setToolTip("Remove this color")
            self.btnDel.setFixedWidth(30)
            self.btnDel.clicked.connect(lambda: self.removed.emit(self.adj["id"]))
            top.addWidget(self.btnDel)
        v.addLayout(top)

        self.sl = NoWheelSlider(Qt.Orientation.Horizontal)
        self.sl.setRange(0, 200)
        self.sl.setValue(int(adj["k"]))
        self.sl.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.sl.setToolTip("0 = this color fades into the paper around it.\n"
                           "100 = unchanged.   200 = " + ("stronger and clearer." if adj["type"] == "color" else "darker."))
        self.sl.valueChanged.connect(self._on_k)
        self.sl.sliderPressed.connect(self._press)
        self.sl.sliderReleased.connect(self._release)
        v.addWidget(self.sl)
        cap = QHBoxLayout()
        for i, text in enumerate(("removed", "original", "stronger" if adj["type"] == "color" else "darker")):
            lab = QLabel(text)
            lab.setObjectName("hint")
            lab.setStyleSheet("font-size: 10px;")
            if i == 1:
                lab.setAlignment(Qt.AlignmentFlag.AlignCenter)
            elif i == 2:
                lab.setAlignment(Qt.AlignmentFlag.AlignRight)
            cap.addWidget(lab, 1)
        v.addLayout(cap)

        self.rs = None
        if adj["type"] == "color":
            rr = QHBoxLayout()
            lab = QLabel("Range")
            lab.setObjectName("hint")
            self.rs = NoWheelSlider(Qt.Orientation.Horizontal)
            self.rs.setRange(8, 90)
            self.rs.setValue(int(adj["tol"]))
            self.rs.setFocusPolicy(Qt.FocusPolicy.NoFocus)
            self.rs.setToolTip("How wide the color match is. Raise it to also catch lighter / blurry edges.")
            self.rs.valueChanged.connect(self._on_tol)
            self.rs.sliderPressed.connect(self._press)
            self.rs.sliderReleased.connect(self._release)
            rr.addWidget(lab)
            rr.addWidget(self.rs, 1)
            v.addLayout(rr)
        sr = QHBoxLayout()
        self.btnSelect = QPushButton("Select this color" if adj["type"] == "color" else "Select this shade")
        self.btnSelect.setToolTip("Make every place with this color the selection. Then Delete, Fill, Hue ... act only there.")
        self.btnSelect.clicked.connect(lambda: self.selectRequested.emit(self.adj["id"]))
        sr.addWidget(self.btnSelect)
        v.addLayout(sr)
        if adj["type"] == "color":
            to = QHBoxLayout()
            to.setSpacing(8)
            self.btnTo = QPushButton("Change to...")
            self.btnTo.setToolTip("Turn this color into another one (red text -> green ...). Shading stays.")
            self.swTo = QFrame()
            self.swTo.setFixedSize(22, 22)
            self.btnToClear = QPushButton("\u2715")
            self.btnToClear.setProperty("flat", True)
            self.btnToClear.setFixedWidth(30)
            self.btnToClear.setToolTip("Do not change the color")
            to.addWidget(self.btnTo)
            to.addWidget(self.swTo)
            to.addStretch(1)
            to.addWidget(self.btnToClear)
            v.addLayout(to)
            self.btnTo.clicked.connect(self._pick_to)
            self.btnToClear.clicked.connect(self._clear_to)
            self._paint_to()
        self.btnReset.clicked.connect(lambda: self.sl.setValue(100))

    def _paint_to(self):
        to = self.adj.get("to")
        if to:
            self.swTo.setStyleSheet("background: rgb(%d,%d,%d); border-radius: 6px; border: 1px solid #4a5268;" % tuple(to))
        else:
            self.swTo.setStyleSheet("background: transparent; border-radius: 6px; border: 1px dashed #4a5268;")
        self.btnToClear.setVisible(bool(to))

    def _pick_to(self):
        from PySide6.QtGui import QColor as _QC
        cur = self.adj.get("to") or self.adj["rgb"]
        c = QColorDialog.getColor(_QC(*cur), self, "Change this color to")
        if c.isValid():
            self.adj["to"] = [c.red(), c.green(), c.blue()]
            self._paint_to()
            self.changed.emit()

    def _clear_to(self):
        self.adj["to"] = None
        self._paint_to()
        self.changed.emit()

    def _on_k(self, v):
        self.adj["k"] = int(v)
        self.val.setText("%d%%" % v)
        self.changed.emit()

    def _on_tol(self, v):
        self.adj["tol"] = float(v)
        self.changed.emit()

    def _press(self):
        self._down = True
        self.activated.emit(self.adj["id"])

    def _release(self):
        self._down = False
        if not self.underMouse():
            self.deactivated.emit()

    def enterEvent(self, e):
        self.activated.emit(self.adj["id"])
        super().enterEvent(e)

    def leaveEvent(self, e):
        if not self._down:
            self.deactivated.emit()
        super().leaveEvent(e)

    def set_highlight(self, on):
        self.setProperty("hl", bool(on))
        self.style().unpolish(self)
        self.style().polish(self)


class ColorList(QWidget):
    """Eyedropper results: one row per clicked colour (+ one group for black / gray shades)."""
    changed = Signal()
    removeAdj = Signal(str)
    removeBands = Signal()
    clearAll = Signal()
    activated = Signal(str)
    deactivated = Signal()
    selectAdj = Signal(str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.hover_id = None
        self.scroll = None
        self.lay = QVBoxLayout(self)
        self.lay.setContentsMargins(0, 0, 0, 0)
        self.lay.setSpacing(8)
        head = QHBoxLayout()
        t = QLabel("Picked colors")
        t.setObjectName("sub")
        self.btnClear = QPushButton("Clear")
        self.btnClear.setProperty("flat", True)
        self.btnClear.clicked.connect(self.clearAll.emit)
        head.addWidget(t, 1)
        head.addWidget(self.btnClear)
        self.lay.addLayout(head)
        self.hint = QLabel(COLOR_HINT)
        self.hint.setObjectName("hint")
        self.hint.setWordWrap(True)
        self.lay.addWidget(self.hint)
        self.lay.addStretch(1)
        self._rows = {}
        self._group = None
        self._group_lay = None
        self._band_rows = []
        self._refresh()

    def _refresh(self):
        empty = not self._rows and self._group is None
        self.hint.setVisible(empty)
        self.btnClear.setVisible(not empty)

    def _insert(self, w):
        self.lay.insertWidget(self.lay.count() - 1, w)

    def _see(self, w):
        sc = self.scroll
        if sc is not None:
            sc.ensureWidgetVisible(w)

    def _wire(self, row):
        row.changed.connect(self.changed.emit)
        row.activated.connect(self._act)
        row.selectRequested.connect(self.selectAdj.emit)
        row.deactivated.connect(self._deact)

    def _act(self, aid):
        self.hover_id = aid
        self.activated.emit(aid)

    def _deact(self):
        self.hover_id = None
        self.deactivated.emit()

    def add_color(self, adj):
        row = AdjRow(adj, removable=True)
        self._wire(row)
        row.removed.connect(self.removeAdj.emit)
        self._rows[adj["id"]] = row
        self._insert(row)
        self._refresh()
        self.flash(adj["id"])
        return row

    def remove_color(self, aid):
        row = self._rows.pop(aid, None)
        if row is not None:
            row.setParent(None)
            row.deleteLater()
        self._refresh()

    # black / gray shades: one group, one slider per shade the user clicked
    def _ensure_group(self):
        if self._group is not None:
            return
        g = QFrame()
        g.setObjectName("group")
        gl = QVBoxLayout(g)
        gl.setContentsMargins(10, 10, 10, 10)
        gl.setSpacing(8)
        head = QHBoxLayout()
        lab = QLabel("Black & gray shades")
        lab.setObjectName("sub")
        btn = QPushButton("✕")
        btn.setProperty("flat", True)
        btn.setToolTip("Remove these shades")
        btn.setFixedWidth(30)
        btn.clicked.connect(self.removeBands.emit)
        head.addWidget(lab, 1)
        head.addWidget(btn)
        gl.addLayout(head)
        note = QLabel("Click another shade in the picture to add its slider.")
        note.setObjectName("hint")
        note.setWordWrap(True)
        gl.addWidget(note)
        self._group = g
        self._group_lay = gl
        self._band_rows = []
        self._insert(g)

    def add_band(self, band, hl=False):
        self._ensure_group()
        row = AdjRow(band, removable=False)
        self._wire(row)
        self._group_lay.addWidget(row)
        self._band_rows.append(row)
        self._refresh()
        if hl:
            self.highlight_band(len(self._band_rows) - 1)
        return row

    def remove_bands(self):
        if self._group is not None:
            self._group.setParent(None)
            self._group.deleteLater()
        self._group = None
        self._group_lay = None
        self._band_rows = []
        self._refresh()

    def has_bands(self):
        return self._group is not None

    def highlight_band(self, i):
        for j, r in enumerate(self._band_rows):
            r.set_highlight(j == i)
        if 0 <= i < len(self._band_rows):
            self.scroll.ensureWidgetVisible(self._band_rows[i])
            QTimer.singleShot(1800, self._unhighlight_bands)

    def _unhighlight_bands(self):
        for r in self._band_rows:
            try:
                r.set_highlight(False)
            except RuntimeError:
                pass

    def flash(self, aid):
        row = self._rows.get(aid)
        if row is None:
            return
        row.set_highlight(True)
        self.scroll.ensureWidgetVisible(row)

        def off():
            try:
                row.set_highlight(False)
            except RuntimeError:
                pass
        QTimer.singleShot(1500, off)

    def rebuild(self, adjs):
        for aid in list(self._rows):
            self.remove_color(aid)
        self.remove_bands()
        self.hover_id = None
        for a in adjs:
            if a["type"] == "color":
                self.add_color(a)
        for a in adjs:
            if a["type"] == "band":
                self.add_band(a)
        self._refresh()


class DragHandle(QPushButton):
    """Press and drag from here: the picture (with your edits) goes to Explorer, WhatsApp Web, Paint ..."""

    def __init__(self, card):
        super().__init__("Drag out")
        self.card = card
        self._press = None
        self.setToolTip("Press and drag to take this picture (with your edits) to Explorer, "
                        "WhatsApp Web, Paint, a folder ...")
        self.setCursor(Qt.CursorShape.OpenHandCursor)

    def enterEvent(self, e):
        self.card.prepare_export()          # the file is made while the mouse is still on its way
        super().enterEvent(e)

    def mousePressEvent(self, e):
        self._press = e.position().toPoint()
        self.card.prepare_export()
        super().mousePressEvent(e)

    def mouseMoveEvent(self, e):
        if self._press is not None and (e.buttons() & Qt.MouseButton.LeftButton) and \
                (e.position().toPoint() - self._press).manhattanLength() >= QApplication.startDragDistance():
            self._press = None
            self.card.begin_drag(self)
            self.setDown(False)
            return
        super().mouseMoveEvent(e)

    def mouseReleaseEvent(self, e):
        self._press = None
        super().mouseReleaseEvent(e)



class Card(QWidget):
    editedChanged = Signal(object)
    removeRequested = Signal(object)
    saveRequested = Signal(object)
    saveAsRequested = Signal(object)
    saveAllRequested = Signal()
    pdfAllRequested = Signal()
    applyAllRequested = Signal(object)
    message = Signal(str)
    exportReady = Signal()

    def __init__(self, path, name=None, parent=None):
        super().__init__(parent)
        self.uid = uuid.uuid4().hex
        self.path = path
        self.name = name or os.path.basename(path)
        self.runner = None                         # set by the window: slow work shows a progress bar
        self.base = None
        self.prev = None
        self.scale = 1.0
        self.adjs = []
        self.look = core.new_look()
        self.shadow_opts = dict(core.SHADOW_DEFAULTS)
        self.fill_rgb = (255, 255, 255)
        self.sel = None                            # selection mask at preview size (or None)
        self.sel_mode = "add"
        self._sel_ver = 0
        self.undo = []
        self.redo = []
        self._cur_snap = None
        self._gest = False
        self.ops = 0
        self._shadow_done = False
        self.shadow_src = None                     # picture before shadow removal: lets the shadow options be changed later
        self.last_action = None                    # the last setting changed: what 'apply to all' copies
        self.keep_ink = True
        self._pending = None
        self._focus_id = None
        self._hl_key = None
        self.clone_src = None
        self.clone_off = None
        self._size_init = False
        self._export = None
        self._exporting = False
        self._drag_wait = None
        self._lab = None
        self._cache = {}
        self._loaded = False
        self._failed = False
        self._tool = "view"
        self._ver = 0
        self._saved_ver = -1
        self._view_gen = 0
        self._rendering = False
        self._again = False
        self.saved_path = None
        self._timer = QTimer(self)
        self._timer.setSingleShot(True)
        self._timer.setInterval(25)
        self._timer.timeout.connect(self._render_now)
        self._focus_timer = QTimer(self)           # pointing at colour rows: wait a moment before redrawing
        self._focus_timer.setSingleShot(True)
        self._focus_timer.setInterval(110)
        self._focus_timer.timeout.connect(self._render_now)
        self._gest_timer = QTimer(self)            # a slider drag = ONE undo step
        self._gest_timer.setSingleShot(True)
        self._gest_timer.setInterval(700)
        self._gest_timer.timeout.connect(self._end_gesture)
        self._shadow_timer = QTimer(self)          # shadow sliders: re-apply live, a moment after the last move
        self._shadow_timer.setSingleShot(True)
        self._shadow_timer.setInterval(450)
        self._shadow_timer.timeout.connect(lambda: self._auto_shadow(live=True))
        self._build()

    # ------------------------------------------------------------------ ui helpers
    @staticmethod
    def _label(text, obj="muted"):
        lab = QLabel(text)
        lab.setObjectName(obj)
        return lab

    @staticmethod
    def _sep():
        f = QFrame()
        f.setFrameShape(QFrame.Shape.VLine)
        f.setStyleSheet("color: #2a3042;")
        return f

    def _menu_button(self, text, entries, tip=""):
        b = QToolButton()
        b.setText(text + "  ▾")
        b.setToolTip(tip)
        b.setPopupMode(QToolButton.ToolButtonPopupMode.InstantPopup)
        b.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextOnly)
        m = QMenu(b)
        for e in entries:
            if e is None:
                m.addSeparator()
                continue
            label, cb = e
            act = m.addAction(label)
            act.triggered.connect(lambda _=False, cb=cb: cb())
        b.setMenu(m)
        b.base_text = text
        return b

    def _mark(self, w, on):
        w.setProperty("active", bool(on))
        w.style().unpolish(w)
        w.style().polish(w)

    def _page(self):
        """A scrollable tab page. Returns (scroll area, layout to fill)."""
        sc = QScrollArea()
        sc.setWidgetResizable(True)
        sc.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        host = QWidget()
        lay = QVBoxLayout(host)
        lay.setContentsMargins(0, 4, 6, 4)
        lay.setSpacing(10)
        sc.setWidget(host)
        return sc, lay

    def _group_box(self, title=None):
        f = QFrame()
        f.setObjectName("group")
        l = QVBoxLayout(f)
        l.setContentsMargins(12, 12, 12, 12)
        l.setSpacing(12)
        if title:
            t = QLabel(title)
            t.setObjectName("sub")
            l.addWidget(t)
        return f, l

    def _scope_label(self):
        lab = QLabel("")
        lab.setObjectName("hint")
        lab.setWordWrap(True)
        self._scope_labels.append(lab)
        return lab

    def _look_row(self, key, title, lo, hi, caps, tip, unit="%"):
        row = SliderRow(title, lo, hi, self.look[key], caps, tip, unit)
        row.changed.connect(lambda v, k=key: self._set_look(k, v))
        self.lookRows[key] = row
        return row

    # ------------------------------------------------------------------ build
    def _build(self):
        self.lookRows = {}
        self._scope_labels = []
        root = QVBoxLayout(self)
        root.setContentsMargins(14, 10, 14, 8)
        root.setSpacing(7)

        # --- row 1: name + history
        r1 = QHBoxLayout()
        r1.setSpacing(8)
        self.lblName = QLabel(self.name)
        self.lblName.setObjectName("title")
        self.lblInfo = self._label("")
        self.lblState = self._label("")
        self.btnUndo = QPushButton("↶ Undo")
        self.btnUndo.setToolTip("Undo one step (Ctrl+Z)")
        self.btnRedo = QPushButton("Redo ↷")
        self.btnRedo.setToolTip("Redo one step (Ctrl+Y or Ctrl+Shift+Z)")
        self.btnReset = QPushButton("Reset")
        self.btnReset.setToolTip("Back to the original picture")
        self.btnRemove = QPushButton("Remove")
        self.btnRemove.setProperty("danger", True)
        r1.addWidget(self.lblName)
        r1.addWidget(self.lblInfo)
        r1.addWidget(self.lblState)
        r1.addStretch(1)
        for b in (self.btnUndo, self.btnRedo, self.btnReset, self.btnRemove):
            r1.addWidget(b)
        root.addLayout(r1)

        # --- row 2: tool menus
        r2 = QHBoxLayout()
        r2.setSpacing(6)
        self.btnHand = QPushButton("Hand")
        self.btnHand.setCheckable(True)
        self.btnHand.setChecked(True)
        self.btnHand.setToolTip("Drag the picture (H).  Space + drag works in every tool.")
        self.btnPick = QPushButton("Eyedropper")
        self.btnPick.setCheckable(True)
        self.btnPick.setToolTip("Click a color in the picture to change only that color (I)")
        T = self.choose_tool
        self.menuSelect = self._menu_button("Select", [
            ("Brush select      B", lambda: T("brush")),
            ("Box      M", lambda: T("rect")), ("Ellipse", lambda: T("ellipse")),
            ("Freehand (draw around)      L", lambda: T("lasso")), ("Color range  (click a color)      W", lambda: T("color")),
            None, ("Select all      Ctrl+A", self.select_all), ("Deselect      Ctrl+D", self.deselect),
            ("Inverse      Ctrl+Shift+I", self.invert_selection)], "Ways to select a part of the picture")
        self.menuMove = self._menu_button("Move", [
            ("Move      V   (Alt + drag = copy)", lambda: T("move")), ("Copy", lambda: T("copy")),
            ("Transform (resize / rotate)      Ctrl+T", self.start_transform)], "Move, copy or resize the selection")
        self.menuErase = self._menu_button("Erase", [
            ("Brush      E", lambda: T("erase_brush")), ("Box", lambda: T("erase_rect")),
            ("Ellipse", lambda: T("erase_ellipse")), ("Freehand", lambda: T("erase_lasso")), None,
            ("Delete selection      Del", self.delete_selection),
            ("Fill selection with color", self.fill_selection)], "Remove things; the place is filled from around it")
        self.menuCrop = self._menu_button("Crop", [
            ("Crop      C", lambda: T("crop")), ("Straighten (4 corners)", lambda: T("persp")), None,
            ("Rotate left", lambda: self.rotate(90.0)), ("Rotate right", lambda: self.rotate(-90.0))],
            "Crop, straighten a photographed page, rotate")
        self.menus = {"select": self.menuSelect, "move": self.menuMove, "erase": self.menuErase, "crop": self.menuCrop}
        self.btnApply = QPushButton("Apply  (Enter)")
        self.btnApply.setProperty("accent", True)
        self.btnCancel = QPushButton("Cancel  (Esc)")
        self.btnApply.hide()
        self.btnCancel.hide()
        self.lblZoom = QLabel("100%")
        self.lblZoom.setObjectName("muted")
        self.lblZoom.setMinimumWidth(48)
        self.lblZoom.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        self.lblZoom.setToolTip("Zoom: Ctrl + mouse wheel,  Ctrl +/-,  Ctrl+0 = fit,  Ctrl+1 = 100%")
        r2.addWidget(self.btnHand)
        r2.addWidget(self._sep())
        for w in (self.menuSelect, self.menuMove, self.btnPick, self.menuErase, self.menuCrop):
            r2.addWidget(w)
        r2.addSpacing(10)
        r2.addWidget(self.btnApply)
        r2.addWidget(self.btnCancel)
        r2.addStretch(1)
        r2.addWidget(self.lblZoom)
        root.addLayout(r2)

        # --- row 3: options of the chosen tool
        self.optsA = QWidget()
        a = QHBoxLayout(self.optsA)
        a.setContentsMargins(0, 0, 0, 0)
        a.setSpacing(8)
        self.modeBox = QWidget()
        mb = QHBoxLayout(self.modeBox)
        mb.setContentsMargins(0, 0, 0, 0)
        mb.setSpacing(4)
        self.modeGroup = QButtonGroup(self)
        self.modeGroup.setExclusive(True)
        self.modeBtns = []
        for i, (text, tip) in enumerate((("New", "A new shape replaces the selection"),
                                         ("Add", "New shapes are added to the selection (Shift)"),
                                         ("Subtract", "New shapes are taken OUT of the selection (Alt)"),
                                         ("Intersect", "Keep only where the old and the new shape overlap"))):
            b = QPushButton(text)
            b.setCheckable(True)
            b.setToolTip(tip)
            self.modeGroup.addButton(b, i)
            self.modeBtns.append(b)
            mb.addWidget(b)
        self.modeBtns[1].setChecked(True)
        a.addWidget(self.modeBox)
        self.sizeBox = QWidget()
        sb = QHBoxLayout(self.sizeBox)
        sb.setContentsMargins(0, 0, 0, 0)
        sb.setSpacing(6)
        self.sizeSlider = NoWheelSlider(Qt.Orientation.Horizontal)
        self.sizeSlider.setRange(2, 600)
        self.sizeSlider.setValue(30)
        self.sizeSlider.setFixedWidth(150)
        self.sizeSlider.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.lblSize = self._label("30 px")
        self.lblSize.setMinimumWidth(48)
        sb.addWidget(self._label("Size"))
        sb.addWidget(self.sizeSlider)
        sb.addWidget(self.lblSize)
        a.addWidget(self.sizeBox)
        self.tolBox = QWidget()
        tb = QHBoxLayout(self.tolBox)
        tb.setContentsMargins(0, 0, 0, 0)
        tb.setSpacing(6)
        self.tolSlider = NoWheelSlider(Qt.Orientation.Horizontal)
        self.tolSlider.setRange(5, 80)
        self.tolSlider.setValue(28)
        self.tolSlider.setFixedWidth(130)
        self.tolSlider.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.tolSlider.setToolTip("How different a color may be to still count as the same object")
        tb.addWidget(self._label("Tolerance"))
        tb.addWidget(self.tolSlider)
        a.addWidget(self.tolBox)
        self.btnClone = QPushButton("Clear copy spot")
        self.btnClone.setToolTip("Go back to filling from the surroundings")
        self.btnClone.hide()
        a.addWidget(self.btnClone)
        a.addStretch(1)
        root.addWidget(self.optsA)

        self.optsB = QWidget()
        b_ = QHBoxLayout(self.optsB)
        b_.setContentsMargins(0, 0, 0, 0)
        b_.setSpacing(6)
        self.btnSelAll = QPushButton("All")
        self.btnSelAll.setToolTip("Select the whole picture (Ctrl+A)")
        self.btnDeselect = QPushButton("None")
        self.btnDeselect.setToolTip("Remove the selection (Ctrl+D)")
        self.btnInvert = QPushButton("Inverse")
        self.btnInvert.setToolTip("Select everything that is NOT selected now (Ctrl+Shift+I)")
        self.btnGrow = QPushButton("Grow +")
        self.btnGrow.setToolTip("Make the selection bigger by the amount on the right")
        self.btnShrink = QPushButton("Shrink −")
        self.btnShrink.setToolTip("Make the selection smaller by the amount on the right")
        self.growSlider = NoWheelSlider(Qt.Orientation.Horizontal)
        self.growSlider.setRange(1, 40)
        self.growSlider.setValue(4)
        self.growSlider.setFixedWidth(100)
        self.growSlider.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.lblGrow = self._label("4 px")
        self.lblGrow.setMinimumWidth(40)
        self.btnSelVis = QPushButton("Outline: on")
        self.btnSelVis.setCheckable(True)
        self.btnSelVis.setChecked(True)
        self.btnSelVis.setToolTip("Show / hide the moving dashed outline of the selection")
        self.lblSel = self._label("")
        for w in (self.btnSelAll, self.btnDeselect, self.btnInvert, self._sep(), self.btnGrow, self.btnShrink,
                  self.growSlider, self.lblGrow, self._sep(), self.btnSelVis):
            b_.addWidget(w)
        b_.addStretch(1)
        b_.addWidget(self.lblSel)
        root.addWidget(self.optsB)

        self.lblHint = QLabel(HINTS["view"])
        self.lblHint.setObjectName("hint")
        self.lblHint.setWordWrap(True)
        root.addWidget(self.lblHint)

        # --- body: picture + tabs
        body = QHBoxLayout()
        body.setSpacing(0)
        self.canvas = ImageCanvas()
        self.canvas.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        self.canvas.setMinimumSize(200, 200)
        body.addWidget(self.canvas, 1)
        body.addWidget(self._build_side())
        root.addLayout(body, 1)

        # --- wiring
        self.btnHand.clicked.connect(lambda: self.choose_tool("view"))
        self.btnPick.clicked.connect(lambda: self.choose_tool("pick"))
        self.modeGroup.idClicked.connect(lambda i: setattr(self, "sel_mode", ("new", "add", "sub", "int")[i]))
        self.btnSelAll.clicked.connect(self.select_all)
        self.btnDeselect.clicked.connect(self.deselect)
        self.btnInvert.clicked.connect(self.invert_selection)
        self.btnGrow.clicked.connect(lambda: self.grow_selection(self.growSlider.value()))
        self.btnShrink.clicked.connect(lambda: self.grow_selection(-self.growSlider.value()))
        self.growSlider.valueChanged.connect(lambda v: self.lblGrow.setText("%d px" % v))
        self.sizeSlider.valueChanged.connect(self._sync_brush)
        self.btnClone.clicked.connect(self._clear_clone)
        self.canvas.pickRequested.connect(self._on_pick)
        self.canvas.zoomChanged.connect(lambda z: self.lblZoom.setText("%d%%" % round(z * self.scale * 100)))
        self.canvas.applyRequested.connect(self.apply_pending)
        self.canvas.cancelRequested.connect(self.cancel_pending)
        self.canvas.eraseFinished.connect(self._on_erase)
        self.canvas.sourcePicked.connect(self._on_source)
        self.canvas.selectFinished.connect(self._on_select)
        self.canvas.moveFinished.connect(self._on_move)
        self.btnApply.clicked.connect(self.apply_pending)
        self.btnCancel.clicked.connect(self.cancel_pending)
        self.btnUndo.clicked.connect(self.undo_step)
        self.btnRedo.clicked.connect(self.redo_step)
        self.btnReset.clicked.connect(self.reset_image)
        self.btnRemove.clicked.connect(lambda: self.removeRequested.emit(self))
        self.btnSelVis.toggled.connect(self._toggle_sel_vis)
        self.colors.changed.connect(self._on_adj_changed)
        self.colors.removeAdj.connect(self._remove_adj)
        self.colors.removeBands.connect(self._remove_bands)
        self.colors.clearAll.connect(self._clear_adjs)
        self.colors.activated.connect(self._set_focus)
        self.colors.selectAdj.connect(self._select_adj)
        self.exportReady.connect(self._on_export_ready)
        self._tool_widgets = [self.btnHand, self.btnPick, self.btnApply, self.btnCancel, self.btnSelAll,
                              self.btnDeselect, self.btnInvert, self.btnGrow, self.btnShrink, self.btnSave,
                              self.btnSaveAs, self.btnPdfAll, self.btnApplyAll, self.btnCopy, self.btnDrag,
                              self.btnShadow, self.btnFill, self.btnDelete, self.btnPickFill] \
            + list(self.menus.values()) + self.modeBtns
        self._enable_tools(False)
        self._set_tool("view")
        self._sel_label()
        self._update_buttons()

    def _build_side(self):
        side = QWidget()
        side.setObjectName("panel")
        side.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        side.setFixedWidth(344)
        v = QVBoxLayout(side)
        v.setContentsMargins(12, 8, 6, 8)
        v.setSpacing(6)
        tabs = QHBoxLayout()
        tabs.setSpacing(0)
        self.tabGroup = QButtonGroup(self)
        self.tabGroup.setExclusive(True)
        self.stackPages = QStackedWidget()
        names = ("Adjust", "Shadow", "Text", "Save")
        for i, n in enumerate(names):
            b = QPushButton(n)
            b.setCheckable(True)
            b.setProperty("tab", True)
            self.tabGroup.addButton(b, i)
            tabs.addWidget(b, 1)
        self.tabGroup.button(0).setChecked(True)
        v.addLayout(tabs)

        # ---------------- Adjust
        pg, lay = self._page()
        lay.addWidget(self._scope_label())
        g, gl = self._group_box("Color & light")
        gl.addWidget(self._look_row("bright", "Brightness", -100, 100, ("darker", "", "brighter"),
                                    "Lighter or darker. White paper stays white-ish when you go up.", ""))
        gl.addWidget(self._look_row("contrast", "Contrast", -100, 100, ("flatter", "", "stronger"),
                                    "Difference between light and dark parts.", ""))
        gl.addWidget(self._look_row("sat", "Saturation", 0, 200, ("whiter", "original", "more vivid"),
                                    "Up: colors get stronger, white and gray never change.\n"
                                    "Down: colors get lighter, up to white."))
        gl.addWidget(self._look_row("hue", "Hue (turn the colors)", -180, 180, ("", "", ""),
                                    "Turns every color around the color wheel. Select only the red part, then +120 "
                                    "makes red into green.", "°"))
        gl.addWidget(self._look_row("warm", "Warmth", -100, 100, ("cooler", "", "warmer"),
                                    "Cooler = bluer, warmer = more yellow / red.", ""))
        lay.addWidget(g)
        g2, gl2 = self._group_box("Fill & delete")
        fr = QHBoxLayout()
        self.btnPickFill = QPushButton("Pick color...")
        self.btnPickFill.setToolTip("Choose the color used by 'Fill selection'")
        self.swatch = QFrame()
        self.swatch.setFixedSize(34, 26)
        fr.addWidget(self.swatch)
        fr.addWidget(self.btnPickFill, 1)
        gl2.addLayout(fr)
        self.btnFill = QPushButton("Fill selection with this color")
        self.btnDelete = QPushButton("Delete selection  (Del)")
        self.btnDelete.setToolTip("Remove what is selected; the place is filled from its surroundings")
        gl2.addWidget(self.btnFill)
        gl2.addWidget(self.btnDelete)
        self.chkInk = QCheckBox("Keep dark writing under a removed stamp")
        self.chkInk.setChecked(True)
        self.chkInk.setToolTip("Deleting a coloured stamp on top of a black signature: the signature is kept\n"
                               "(drawn from what is still visible) instead of leaving a white gap.")
        self.chkInk.toggled.connect(lambda on: setattr(self, "keep_ink", bool(on)))
        gl2.addWidget(self.chkInk)
        self._paint_swatch()
        lay.addWidget(g2)
        self.colors = ColorList()
        self.colors.scroll = pg
        g3, gl3 = self._group_box()
        gl3.addWidget(self.colors)
        lay.addWidget(g3)
        lay.addStretch(1)
        self.stackPages.addWidget(pg)
        self.btnFill.clicked.connect(self.fill_selection)
        self.btnDelete.clicked.connect(self.delete_selection)
        self.btnPickFill.clicked.connect(self._pick_fill)

        # ---------------- Shadow
        pg, lay = self._page()
        lay.addWidget(self._scope_label())
        g, gl = self._group_box("Shadow removal")
        self.btnShadow = QPushButton("Remove shadow  (one click)")
        self.btnShadow.setProperty("accent", True)
        self.btnShadow.setMinimumHeight(40)
        self.btnShadow.setToolTip("Turns shadows and grey paper white, like a scanner. Text and lines stay.\n"
                                  "Not happy? Undo, change the options below and click again.")
        gl.addWidget(self.btnShadow)
        self.shadowRows = {}
        for key, title, caps, tip in (
                ("strength", "Strength", ("light", "", "full"), "How strongly the paper is pushed to white."),
                ("white", "Paper whiteness", ("greyer", "", "whiter"), "How white the paper gets."),
                ("ink", "Text darkness", ("lighter", "", "darker"), "How dark the writing gets."),
                ("scale", "Shadow size", ("small", "", "large"),
                 "Small = follows small shadows (a pen, folds). Large = only wide, soft shadows.")):
            row = SliderRow(title, 0, 100, self.shadow_opts[key], caps, tip)
            row.changed.connect(lambda v, k=key: self._shadow_opt(k, int(v)))
            self.shadowRows[key] = row
            gl.addWidget(row)
        mrow = QHBoxLayout()
        mrow.setSpacing(4)
        self.modeOut = QButtonGroup(self)
        self.modeOut.setExclusive(True)
        for i, (text, tip) in enumerate((("Color", "Keep the colors"), ("Gray", "Grayscale scan"),
                                         ("Black && white", "Pure black text on white paper"))):
            b = QPushButton(text)
            b.setCheckable(True)
            b.setToolTip(tip)
            self.modeOut.addButton(b, i)
            mrow.addWidget(b)
        self.modeOut.button(0).setChecked(True)
        self.modeOut.idClicked.connect(lambda i: self._shadow_opt("mode", ("color", "gray", "bw")[i]))
        gl.addWidget(self._label("Result"))
        gl.addLayout(mrow)
        self.chkProtect = QCheckBox("Keep photos / faces untouched")
        self.chkProtect.setChecked(True)
        self.chkProtect.setToolTip("Photos (a face on an ID card ...) are found automatically and left as they are.")
        self.chkProtect.toggled.connect(lambda on: self._shadow_opt("protect", 1 if on else 0))
        gl.addWidget(self.chkProtect)
        lay.addWidget(g)
        lay.addStretch(1)
        self.stackPages.addWidget(pg)

        # ---------------- Text clarity
        pg, lay = self._page()
        lay.addWidget(self._scope_label())
        g, gl = self._group_box("Text & line clarity")
        gl.addWidget(self._look_row("sharp", "Text sharpness", 0, 100, ("off", "", "max"),
                                    "Makes small text and thin lines crisp. Shapes are never moved or bent."))
        gl.addWidget(self._look_row("objs", "Line & shape sharpness", 0, 100, ("off", "", "max"),
                                    "Makes thicker lines, boxes, drawings and objects crisp."))
        gl.addWidget(self._look_row("detail", "Faint strokes", 0, 100, ("off", "", "max"),
                                    "Makes faint writing and thin pencil lines clearer."))
        gl.addWidget(self._look_row("dark", "Text darkness", 0, 100, ("off", "", "max"),
                                    "Makes writing darker; the paper stays as it is."))
        gl.addWidget(self._look_row("clear", "Clean specks", 0, 100, ("off", "", "max"),
                                    "Removes tiny dots and grain (use little: very high can soften thin text)."))
        self.btnClarityReset = QPushButton("Reset text clarity")
        self.btnClarityReset.clicked.connect(self._reset_clarity)
        gl.addWidget(self.btnClarityReset)
        lay.addWidget(g)
        lay.addStretch(1)
        self.stackPages.addWidget(pg)

        # ---------------- Save
        pg, lay = self._page()
        g, gl = self._group_box("Save")
        self.btnSave = QPushButton("Save")
        self.btnSave.setProperty("accent", True)
        self.btnSave.setMinimumHeight(38)
        self.btnSave.setToolTip("Save this picture in the save folder (Ctrl+S)")
        self.btnSaveAs = QPushButton("Save as...  (JPG, PNG, WEBP, BMP, TIF, PDF)")
        self.btnSaveAll = QPushButton("Save all edited")
        self.btnSaveAll.setToolTip("Saves every edited picture into the save folder (Ctrl+Shift+S)")
        self.btnPdfAll = QPushButton("All pages as one PDF")
        self.btnPdfAll.setToolTip("One PDF with every open picture, one page each, in the order of the thumbnails")
        for w in (self.btnSave, self.btnSaveAs, self.btnSaveAll, self.btnPdfAll):
            gl.addWidget(w)
        lay.addWidget(g)
        g, gl = self._group_box("Share")
        self.btnApplyAll = QPushButton("Apply last setting to all pictures")
        self.btnApplyAll.setToolTip("Copies ONLY the setting you changed last (one slider, the shadow removal, or the picked colors) "
                                   "to the other pictures. Crop, erase, move ... are never copied.")
        self.btnCopy = QPushButton("Copy picture  (Ctrl+C)")
        self.btnCopy.setToolTip("Copy this picture (with your edits) - then paste it anywhere")
        self.btnDrag = DragHandle(self)
        for w in (self.btnApplyAll, self.btnCopy, self.btnDrag):
            gl.addWidget(w)
        lay.addWidget(g)
        lay.addStretch(1)
        self.stackPages.addWidget(pg)

        v.addWidget(self.stackPages, 1)
        self.tabGroup.idClicked.connect(self.stackPages.setCurrentIndex)
        self.btnShadow.clicked.connect(self._auto_shadow)
        self.btnSave.clicked.connect(lambda: self.saveRequested.emit(self))
        self.btnSaveAs.clicked.connect(lambda: self.saveAsRequested.emit(self))
        self.btnSaveAll.clicked.connect(self.saveAllRequested.emit)
        self.btnPdfAll.clicked.connect(self.pdfAllRequested.emit)
        self.btnApplyAll.clicked.connect(lambda: self.applyAllRequested.emit(self))
        self.btnCopy.clicked.connect(self.copy_image)
        return side

    def show_tab(self, i):
        self.tabGroup.button(i).setChecked(True)
        self.stackPages.setCurrentIndex(i)

    def _paint_swatch(self):
        r, g, b = self.fill_rgb
        self.swatch.setStyleSheet("background: rgb(%d,%d,%d); border-radius: 7px; border: 1px solid #4a5268;" % (r, g, b))

    def _pick_fill(self):
        c = QColorDialog.getColor(QColor(*self.fill_rgb), self, "Fill color")
        if c.isValid():
            self.fill_rgb = (c.red(), c.green(), c.blue())
            self._paint_swatch()

    def _reset_clarity(self):
        self._begin_change()
        for k in ("sharp", "objs", "detail", "dark", "clear"):
            self.look[k] = 0
            self.lookRows[k].set_value(0)
        self._on_look_done()

    def _enable_tools(self, on):
        for b in self._tool_widgets:
            b.setEnabled(on)
        for r in list(self.lookRows.values()) + list(self.shadowRows.values()):
            r.setEnabled(on)
        self.btnClarityReset.setEnabled(on)

    def _update_buttons(self):
        self.btnUndo.setEnabled(bool(self.undo))
        self.btnRedo.setEnabled(bool(self.redo))
        self.btnReset.setEnabled(self._loaded and self.is_edited())
        if self.is_edited():
            self.lblState.setText("edited" if self.is_unsaved() else "saved")
        else:
            self.lblState.setText("")

    def _toggle_sel_vis(self, on):
        self.btnSelVis.setText("Outline: on" if on else "Outline: off")
        self.canvas.set_selection_visible(on)

    def _sel_label(self):
        if self.sel is None:
            scope = "Nothing selected: changes apply to the WHOLE picture."
            short = ""
        else:
            pct = core.sel_coverage(self.sel) * 100.0
            scope = "Selection active (%.0f%% of the picture): changes apply ONLY inside it. " \
                    "Ctrl+D to deselect." % pct
            short = "Selected: %.0f%%" % pct
        for lab in self._scope_labels:
            lab.setText(scope)
        self.lblSel.setText(short)

    # ------------------------------------------------------------ slow work
    def _job(self, label, work, done, fail=None):
        """Slow work: in a worker thread with a progress bar. Without a window (tests) it simply runs."""
        def failed(msg):
            _log(msg)
            self.message.emit("Could not finish: " + msg.splitlines()[0])
            if fail is not None:
                fail(msg)
        if self.runner is None:
            try:
                res = work(lambda *a, **k: None)
            except Exception as ex:
                failed("%s" % ex)
                return
            done(res)
            return
        self.runner.run(label, work, done, failed)

    # ------------------------------------------------------------- loading
    def ensure_loaded(self):
        if self._loaded or self._failed:
            return
        QApplication.setOverrideCursor(Qt.CursorShape.WaitCursor)
        try:
            self.base = core.load_image(self.path)
            self._loaded = True
            self._enable_tools(True)
            if self._pending is not None:
                st, self._pending = self._pending, None
                self._apply_now(st, rebuild=False)
            self._rebuild_preview(refit=True)
            self._cur_snap = self._snap()
            self._sel_label()
        except Exception as ex:
            self._failed = True
            self.canvas.set_message("This picture cannot be opened\n%s" % ex)
        finally:
            QApplication.restoreOverrideCursor()

    @staticmethod
    def _build_view(base, adjs, look, sel):
        """Everything slow about showing a picture (preview, colour maths, first render). Runs in a worker thread."""
        prev, scale = core.make_preview(base)
        cache = {}
        lab = core.rgb_to_lab(prev) if core.active_adjs(adjs) else None
        out = core.render(prev, adjs, look, lab=lab, cache=cache,
                          sel=sel if (sel is not None and sel.shape == prev.shape[:2]) else None)
        return {"prev": prev, "scale": scale, "lab": lab, "cache": cache, "out": out}

    def _adopt_view(self, v, refit=True):
        self._view_gen += 1
        self.prev, self.scale = v["prev"], v["scale"]
        self._lab = v["lab"]
        self._cache = v["cache"]
        if self.sel is not None and self.sel.shape != self.prev.shape[:2]:
            self.sel = None
        w, h = self.base.size
        self.lblInfo.setText("%d × %d px" % (w, h))
        if not self._size_init:
            self._size_init = True
            size = max(8, min(200, int(round(max(w, h) / 60.0))))
            self.sizeSlider.blockSignals(True)
            self.sizeSlider.setValue(size)
            self.sizeSlider.blockSignals(False)
        self._sync_brush()
        qimg = np_to_qimage(v["out"])
        if refit:
            self.canvas.set_images(qimg, self.prev)
        else:
            self.canvas.set_display(qimg, self.prev)
        self.canvas.set_selection(self.sel)
        self._hl_key = None
        self._sel_label()
        self._update_buttons()

    def _rebuild_preview(self, refit=True):
        v = self._build_view(self.base, self.adjs, self.look, self.sel)
        self._adopt_view(v, refit)

    def _get_lab(self):
        if self._lab is None:
            self._lab = core.rgb_to_lab(self.prev)
        return self._lab

    def _focus_adj(self):
        if self._focus_id is None:
            return None
        for a in self.adjs:
            if a["id"] == self._focus_id:
                return a
        return None

    def _compute(self):
        """Render on the spot (tests, small jobs)."""
        focus = self._focus_adj()
        lab = self._get_lab() if (core.active_adjs(self.adjs) or focus is not None) else None
        out = core.render(self.prev, self.adjs, self.look, lab=lab, cache=self._cache, sel=self.sel)
        self._show_highlight(focus, None if focus is None else core.get_mask(self.prev, focus, lab, self._cache))
        return out

    def _show_highlight(self, focus, mask):
        key = None if focus is None else (focus["id"], core.adj_key(focus), self._sel_ver)
        if key == self._hl_key:
            return
        self._hl_key = key
        if mask is not None and self.sel is not None:
            mask = mask * self.sel
        self.canvas.set_highlight(mask)

    def _render_now(self):
        """Live preview in a worker thread; if more slider moves arrive meanwhile, one more pass follows."""
        if not self._loaded or self.prev is None:
            return
        if self._rendering:
            self._again = True
            return
        self._rendering = True
        self._again = False
        gen = self._view_gen
        prev, cache, lab0 = self.prev, self._cache, self._lab
        adjs = copy.deepcopy(self.adjs)
        look, sel = dict(self.look), self.sel
        focus = copy.deepcopy(self._focus_adj())

        def work(progress):
            lab = lab0
            if lab is None and (core.active_adjs(adjs) or focus is not None):
                lab = core.rgb_to_lab(prev)
            out = core.render(prev, adjs, look, lab=lab, cache=cache, sel=sel)
            hl = None if focus is None else core.get_mask(prev, focus, lab, cache)
            return out, hl, lab

        def done(res):
            self._rendering = False
            if gen == self._view_gen:
                out, hl, lab = res
                if self._lab is None and lab is not None:
                    self._lab = lab
                self.canvas.set_display(np_to_qimage(out))
                self._show_highlight(focus, hl)
            if self._again:
                self._again = False
                self._render_now()

        def fail(msg):
            self._rendering = False
            self.message.emit("Preview problem: " + msg.splitlines()[0])
        run_background(work, done, fail)

    def render_sync(self):
        """For the self test: draw the picture now, without a worker thread."""
        self.canvas.set_display(np_to_qimage(self._compute()))

    # --------------------------------------------------------------- state
    def is_edited(self):
        return (self.ops > 0 or self._pending is not None or core.look_edited(self.look)
                or bool(core.active_adjs(self.adjs)))

    def is_unsaved(self):
        return self.is_edited() and self._ver != self._saved_ver

    def _touch(self):
        self._ver += 1
        if not self._gest:
            self._cur_snap = self._snap()
        self._update_buttons()
        self.editedChanged.emit(self)

    def mark_saved(self, path):
        self._saved_ver = self._ver
        self.saved_path = path
        self._update_buttons()
        self.editedChanged.emit(self)

    def render_args(self):
        """A snapshot of everything needed to produce the final picture (safe to use in a worker thread)."""
        self.ensure_loaded()
        return (self.base, copy.deepcopy(self.adjs), dict(self.look), self.sel)

    def result_image(self):
        return core.render_full(*self.render_args())

    def out_name(self):
        stem = os.path.splitext(self.name)[0]
        return stem + "_edited", core.out_ext(self.name)

    # ------------------------------------------------- history (undo / redo)
    def _snap(self):
        return {"base": self.base, "adjs": copy.deepcopy(self.adjs), "look": dict(self.look),
                "shadow": self._shadow_done, "sel": self.sel, "ops": self.ops, "shadow_src": self.shadow_src}

    def _push_undo(self, st):
        self.undo.append(st)
        self.redo = []
        while len(self.undo) > 80 or len({id(x) for e in self.undo for x in (e["base"], e.get("shadow_src")) if x is not None}) > 8:
            self.undo.pop(0)
        self._update_buttons()

    def _checkpoint(self):
        """Call BEFORE changing anything that must be one undo step (a selection, an erase, a crop ...)."""
        if self._gest:
            self._gest_timer.stop()
            self._gest = False
        self._push_undo(self._snap())

    def _begin_change(self):
        """Slider / colour row moved: the first move of a drag stores the 'before' state, the rest is one step."""
        if not self._gest:
            self._gest = True
            self._push_undo(self._cur_snap if self._cur_snap is not None else self._snap())
        self._gest_timer.start(700)

    def _end_gesture(self):
        self._gest = False
        self._cur_snap = self._snap()

    def _sync_sliders(self):
        for k, row in self.lookRows.items():
            row.set_value(self.look[k])
        for k, row in self.shadowRows.items():
            row.set_value(self.shadow_opts[k])
        self.chkProtect.blockSignals(True)
        self.chkProtect.setChecked(bool(self.shadow_opts.get("protect", 1)))
        self.chkProtect.blockSignals(False)
        i = ("color", "gray", "bw").index(self.shadow_opts.get("mode", "color"))
        self.modeOut.button(i).setChecked(True)

    def _set_fields(self, st):
        self.base = st["base"]
        self.adjs = copy.deepcopy(st["adjs"])
        self.look = core.new_look(**st["look"])
        self._shadow_done = st["shadow"]
        self.sel = st.get("sel")
        self.ops = st["ops"]
        self.shadow_src = st.get("shadow_src")
        self._sel_ver += 1
        self.colors.rebuild(self.adjs)
        self._sync_sliders()

    def _goto(self, st):
        """Show a stored state (undo / redo)."""
        if self._tool in CROP_KEYS or self._tool == "transform":
            self._go_move()
        self._clear_clone()
        if st["base"] is self.base:                      # same picture: only values / selection changed
            self._set_fields(st)
            self.canvas.set_selection(self.sel)
            self._hl_key = None
            self._sel_label()
            self._timer.start()
            self._touch()
            return

        def work(progress):
            return self._build_view(st["base"], st["adjs"], core.new_look(**st["look"]), st.get("sel"))

        def done(v):
            self._set_fields(st)
            self._adopt_view(v, True)
            self._touch()
        self._job("Undo / redo", work, done)

    def undo_step(self):
        if not self._loaded:
            return
        if self._gest:
            self._gest_timer.stop()
            self._end_gesture()
        if not self.undo:
            return
        self.redo.append(self._snap())
        self._goto(self.undo.pop())

    def redo_step(self):
        if not self._loaded:
            return
        if self._gest:
            self._gest_timer.stop()
            self._end_gesture()
        if not self.redo:
            return
        self.undo.append(self._snap())
        self._goto(self.redo.pop())

    def _edit_base(self, label, make, keep_sel=True, refit=False, after=None, msg=None, shadow_src=None, merge=False):
        """Change the picture itself (erase, crop, rotate, move ...) in a worker thread, then show it.
        make(progress) -> new PIL image, or (new image, new selection)."""
        adjs = copy.deepcopy(self.adjs)
        look, sel_in = dict(self.look), self.sel

        def work(progress):
            res = make(progress)
            if isinstance(res, tuple):
                new, sel_out = res
            else:
                new, sel_out = res, (sel_in if keep_sel else None)
            progress(0.9, "Showing")
            return new, sel_out, self._build_view(new, adjs, look, sel_out)

        def done(r):
            new, sel_out, v = r
            if merge and self._gest:                 # a live shadow slider drag is ONE undo step
                self._gest_timer.start(2500)
            else:
                self._checkpoint()
                self.ops += 1
                if merge:
                    self._gest = True
                    self._gest_timer.start(2500)
            self.shadow_src = shadow_src
            self.base = new
            self.sel = sel_out
            self._sel_ver += 1
            self._adopt_view(v, refit)
            self._touch()
            if after is not None:
                after()
            if msg:
                self.message.emit(msg)
        self._job(label, work, done)

    # ------------------------------------------- settings shared by all pictures
    def apply_action(self, act):
        """'Apply last setting to all': copies only the LAST changed setting from another picture (never crop, erase ...)."""
        self.ensure_loaded()
        if not self._loaded:
            return
        k = act["kind"]
        if k == "look":
            self._checkpoint()
            self.look[act["key"]] = act["value"]
            self._sync_sliders()
            self._timer.start()
            self._touch()
        elif k == "adjs":
            self._checkpoint()
            self.adjs = copy.deepcopy(act["adjs"])
            self.colors.rebuild(self.adjs)
            self._timer.start()
            self._touch()
        elif k == "shadow":
            self.shadow_opts = dict(act["opts"])
            self._sync_sliders()
            self._auto_shadow()

    def settings(self):
        return {"adjs": copy.deepcopy(self.adjs), "look": dict(self.look), "shadow_opts": dict(self.shadow_opts),
                "shadow": self._shadow_done}

    def apply_settings(self, st):
        """Same colors / light / clarity / shadow as another picture (from 'Same settings for all')."""
        st = copy.deepcopy(st)
        if not self._loaded:
            self._pending = st
            self._touch()
            return
        self._apply_now(st, rebuild=True)

    def _apply_now(self, st, rebuild=True):
        base = self.base
        need_shadow = bool(st.get("shadow")) and not self._shadow_done
        opts = dict(core.SHADOW_DEFAULTS)
        opts.update(st.get("shadow_opts") or {})
        look = core.new_look(**st["look"])

        def after_state():
            self.adjs = copy.deepcopy(st["adjs"])
            self.look = dict(look)
            self.shadow_opts = dict(opts)
            self.colors.rebuild(self.adjs)
            self._sync_sliders()
        if not rebuild:                              # while loading: no preview yet, just take the values
            self._checkpoint()
            after_state()
            if need_shadow:
                self.base = core.remove_shadow(self.base, opts["strength"] / 100.0, None, opts)
                self._shadow_done = True
                self.ops += 1
            self._touch()
            return
        adjs = copy.deepcopy(st["adjs"])
        sel = self.sel

        def work(progress):
            new = core.remove_shadow(base, opts["strength"] / 100.0, progress, opts) if need_shadow else base
            return new, self._build_view(new, adjs, look, sel)

        def done(r):
            new, v = r
            self._checkpoint()
            self.base = new
            after_state()
            if need_shadow:
                self._shadow_done = True
                self.ops += 1
            self._adopt_view(v, False)
            self._touch()
        self._job("Applying settings to " + self.name, work, done)

    # -------------------------------------------------------------- look / colours
    def _set_last(self, act):
        self.last_action = act
        self.btnApplyAll.setText("Apply last setting to all  (%s)" % act["label"])

    def _on_adj_changed(self):
        self._set_last({"kind": "adjs", "adjs": copy.deepcopy(self.adjs), "label": "colors"})
        self._begin_change()
        self._timer.start()
        self._touch()

    def _set_look(self, key, v):
        self._begin_change()
        self.look[key] = int(v)
        self._set_last({"kind": "look", "key": key, "value": int(v), "label": LOOK_TITLES.get(key, key)})
        self._timer.start()
        self._touch()

    def _on_look_done(self):
        self._timer.start()
        self._touch()

    def _set_focus(self, aid):
        self._focus_id = aid
        self._focus_timer.start()

    def _clear_focus(self):
        if self._focus_id is not None:
            self._focus_id = None
            self._focus_timer.start()

    def _on_pick(self, x, y):
        if not self._loaded:
            return
        H, W = self.prev.shape[:2]
        x, y = min(max(x, 0), W - 1), min(max(y, 0), H - 1)
        patch = self.prev[max(0, y - 1):y + 2, max(0, x - 1):x + 2].reshape(-1, 3).mean(0)
        rgb = tuple(int(round(float(v))) for v in patch)
        QApplication.setOverrideCursor(Qt.CursorShape.WaitCursor)
        try:
            kind, items, L = core.classify_pick(self._get_lab(), rgb)
        finally:
            QApplication.restoreOverrideCursor()
        self.show_tab(0)
        if kind == "color":
            new = items[0]
            for a in self.adjs:
                if a["type"] == "color" and math.dist(a["target"], new["target"]) < 6.0:
                    self.colors.flash(a["id"])
                    self._set_focus(a["id"])
                    return
            self._checkpoint()
            self.adjs.append(new)
            self.colors.add_color(new)
            self._set_focus(new["id"])
        else:
            existing = [a for a in self.adjs if a["type"] == "band"]
            for i, b in enumerate(existing):
                lo = -1e9 if b["lo"] is None else b["lo"]
                hi = 1e9 if b["hi"] is None else b["hi"]
                if lo <= L < hi:
                    self.colors.highlight_band(i)
                    self._set_focus(b["id"])
                    return
            new = items[core.band_index_for(items, L)]
            for b in existing:                       # keep the shades from overlapping each other
                if b["center"] < new["center"] and b["hi"] is not None:
                    new["lo"] = b["hi"] if new["lo"] is None else max(new["lo"], b["hi"])
                elif b["center"] > new["center"] and b["lo"] is not None:
                    new["hi"] = b["lo"] if new["hi"] is None else min(new["hi"], b["lo"])
            self._checkpoint()
            self.adjs.append(new)
            self.colors.add_band(new, hl=True)
            self._set_focus(new["id"])
        self._touch()

    def _remove_adj(self, aid):
        self._checkpoint()
        self.adjs = [a for a in self.adjs if a["id"] != aid]
        self.colors.remove_color(aid)
        self._clear_focus()
        self._timer.start()
        self._touch()

    def _remove_bands(self):
        self._checkpoint()
        self.adjs = [a for a in self.adjs if a["type"] != "band"]
        self.colors.remove_bands()
        self._clear_focus()
        self._timer.start()
        self._touch()

    def _clear_adjs(self):
        self._checkpoint()
        self.adjs = []
        self.colors.rebuild(self.adjs)
        self._clear_focus()
        self._timer.start()
        self._touch()

    # ------------------------------------------------------------ selection
    def _sel_changed(self):
        self._sel_ver += 1
        self.canvas.set_selection(self.sel)
        self._hl_key = None
        self._sel_label()
        self._timer.start()                          # every edit is limited to the selection
        self._touch()

    def _set_selection(self, mask, mode=None):
        new = core.sel_combine(self.sel, mask, mode or self.sel_mode)
        if new is None and self.sel is None:
            return
        self._checkpoint()
        self.sel = new
        self._sel_changed()

    def select_all(self):
        if not self._loaded:
            return
        self._checkpoint()
        self.sel = np.ones(self.prev.shape[:2], np.float32)
        self._sel_changed()

    def deselect(self):
        if not self._loaded or self.sel is None:
            return
        self._checkpoint()
        self.sel = None
        self._sel_changed()

    def _warn(self, text):
        """Short message right where the user looks (the hint line), also in the status bar."""
        self.message.emit(text)
        self.lblHint.setText("\u26a0  " + text)
        QTimer.singleShot(5000, lambda: self.lblHint.setText(HINTS.get(self._tool, "")))

    def _select_adj(self, aid):
        """'Select this color': the places of a picked color become the selection."""
        if not self._loaded:
            return
        for a in self.adjs:
            if a["id"] == aid:
                mask = core.get_mask(self.prev, a, self._get_lab(), self._cache)
                self._set_selection(mask, self.sel_mode)
                if self.sel is None:
                    self._warn("Nothing found for this color.")
                return

    def invert_selection(self):
        if not self._loaded:
            return
        new = core.sel_invert(self.sel, self.prev.shape[:2])
        self._checkpoint()
        self.sel = new
        self._sel_changed()

    def grow_selection(self, px):
        if not self._loaded:
            return
        if self.sel is None:
            self.message.emit("Select something first.")
            return
        new = core.sel_grow(self.sel, px)
        self._checkpoint()
        self.sel = new
        self._sel_changed()

    @staticmethod
    def _mask_img(sel, size):
        """Selection (preview size) -> PIL 'L' mask at the real picture size."""
        return Image.fromarray(np.clip(core.resize_mask(sel, size) * 255, 0, 255).astype(np.uint8))

    def _on_select(self, kind, data, mod):
        if not self._loaded:
            return
        mode = mod or self.sel_mode
        H, W = self.prev.shape[:2]
        size_prev = self.sizeSlider.value() * self.scale
        if kind in ("rect", "ellipse", "lasso"):
            self._set_selection(core.shape_mask((W, H), kind, data), mode)
            return
        if kind == "brush":
            self._set_selection(core.shape_mask((W, H), "brush", data, size_prev), mode)
            return
        tol = float(self.tolSlider.value())
        prev = self.prev

        def work(progress):
            lab = self._lab if self._lab is not None else core.rgb_to_lab(prev)
            if kind == "color":
                x, y = data
                x, y = min(max(x, 0), W - 1), min(max(y, 0), H - 1)
                patch = prev[max(0, y - 1):y + 2, max(0, x - 1):x + 2].reshape(-1, 3).mean(0)
                mask = core.color_range_mask(lab, tuple(int(round(float(v))) for v in patch), tol)
            else:
                stroke = core.shape_mask((W, H), "brush", data, size_prev)
                mask = core.quick_select(lab, stroke, tol)
            return mask, lab

        def done(r):
            mask, lab = r
            if self._lab is None and self.prev is prev:
                self._lab = lab
            if self.prev is prev:
                self._set_selection(mask, mode)
        self._job("Selecting", work, done)

    def _on_move(self, dx, dy, copy_flag):
        if not self._loaded or self.sel is None:
            self.message.emit("Make a selection first.")
            return
        s = self.scale
        sel, base = self.sel, self.base
        mdx, mdy = int(round(dx / s)), int(round(dy / s))

        def make(progress):
            mask = self._mask_img(sel, base.size)
            progress(0.2, "")
            return core.move_region(base, mask, mdx, mdy, copy=copy_flag), core.shift_mask(sel, dx, dy)
        self._edit_base("Copying" if copy_flag else "Moving", make)

    def delete_selection(self):
        """Delete key: what is selected disappears, the place is filled from its surroundings."""
        if not self._loaded:
            return
        if self._tool == "transform":                # Delete while transforming: the object goes away
            self._go_move()
        if self.sel is None:
            self._warn("Nothing is selected. Select something first (Select menu), or pick a color and press 'Select this color'.")
            return
        sel, base, ink = self.sel, self.base, self.keep_ink

        def make(progress):
            return core.erase_region(base, self._mask_img(sel, base.size), None, 2.0, ink)
        self._edit_base("Deleting", make, msg="Deleted. Ctrl+Z brings it back.")

    def fill_selection(self):
        if not self._loaded:
            return
        if self.sel is None:
            self._warn("Nothing is selected. Select something first.")
            return
        sel, base, rgb = self.sel, self.base, self.fill_rgb

        def make(progress):
            return core.fill_region(base, self._mask_img(sel, base.size), rgb)
        self._edit_base("Filling", make)

    # ---------------------------------------------------------------- tools
    def choose_tool(self, key):
        """From a menu, a button or a key. Transform needs a selection, so it has its own start."""
        if key == "transform":
            self.start_transform()
        else:
            self._set_tool(key)

    def _go_move(self):
        self._set_tool("view")

    def _set_tool(self, key):
        if not self._loaded and key != "view":
            return
        mode, shape = TOOL_MODE[key]
        self._tool = key
        self.canvas.copy_mode = key == "copy"
        self.canvas.set_mode(mode, shape)
        if key in ERASE_KEYS:
            self.canvas.set_erase(SHAPES[key])
        self.btnHand.setChecked(key == "view")
        self.btnPick.setChecked(key == "pick")
        for fam, keys in (("select", SELECT_KEYS), ("move", MOVE_KEYS), ("erase", ERASE_KEYS), ("crop", CROP_KEYS)):
            b = self.menus[fam]
            on = key in keys
            b.setText((b.base_text + ": " + NAMES[key] if on else b.base_text) + "  ▾")
            self._mark(b, on)
        sel_t, er = key in SELECT_KEYS, key in ERASE_KEYS
        pend = key in CROP_KEYS or key == "transform"
        self.btnApply.setVisible(pend)
        self.btnCancel.setVisible(pend)
        self.optsA.setVisible(sel_t or er)
        self.optsB.setVisible(sel_t or key in MOVE_KEYS)
        self.modeBox.setVisible(sel_t)
        self.sizeBox.setVisible(key in ("quick", "brush", "erase_brush"))
        self.tolBox.setVisible(key in ("quick", "color"))
        self.btnClone.setVisible(er and self.clone_src is not None)
        self._sync_brush()
        self.lblHint.setText(HINTS[key])

    def start_transform(self):
        """Ctrl+T: resize / rotate / move the selected part."""
        if not self._loaded:
            return
        px = self.canvas.selection_pixels() if self.sel is not None else None
        if px is None:
            self._warn("Select something first, then press Ctrl+T.")
            return
        self._set_tool("transform")
        self.canvas.start_transform(*px)

    def apply_pending(self):
        if self._tool in CROP_KEYS:
            self._apply_crop()
        elif self._tool == "transform":
            self._apply_transform()

    def cancel_pending(self):
        if self._tool in ERASE_KEYS:
            self._clear_clone()
        elif self._tool in CROP_KEYS or self._tool == "transform":
            self._go_move()

    def _sync_brush(self):
        size = self.sizeSlider.value()
        self.lblSize.setText("%d px" % size)
        self.canvas.set_brush(size * self.scale)
        self.canvas.set_quick_brush(size * self.scale)

    def _on_source(self, x, y):
        self.clone_src = (x, y)
        self.clone_off = None
        self.canvas.set_clone(self.clone_src, None)
        self.btnClone.show()
        self.message.emit("Copy spot chosen. Now paint where the copy should go. Esc clears the spot.")

    def _clear_clone(self):
        self.clone_src = None
        self.clone_off = None
        self.canvas.set_clone(None, None)
        self.btnClone.hide()

    def _on_erase(self, kind, data):
        if not self._loaded:
            return
        s = self.scale
        if kind in ("brush", "lasso"):
            payload = [(x / s, y / s) for x, y in data]
            start = payload[0]
        else:
            x0, y0, x1, y1 = [v / s for v in data]
            payload = (x0, y0, x1, y1)
            start = (min(x0, x1), min(y0, y1))
        offset = None
        if self.clone_src is not None:
            if self.clone_off is None:
                self.clone_off = (self.clone_src[0] / s - start[0], self.clone_src[1] / s - start[1])
                self.canvas.set_clone(self.clone_src, (self.clone_off[0] * s, self.clone_off[1] * s))
            offset = self.clone_off
        size = self.sizeSlider.value()
        base, sel, ink = self.base, self.sel, self.keep_ink

        def make(progress):
            mask = core.make_mask(base.size, kind, payload, brush=size)
            if sel is not None:                      # with a selection, erasing only works inside it
                m = np.asarray(mask, dtype=np.float32) * core.resize_mask(sel, base.size)
                mask = Image.fromarray(np.clip(m, 0, 255).astype(np.uint8))
            feather = max(1.5, 0.08 * size) if kind == "brush" else 2.0
            return core.erase_region(base, mask, offset, feather, ink)
        self._edit_base("Erasing", make)

    def _apply_crop(self):
        if not self._loaded:
            return
        c = self.canvas
        s = self.scale
        base = self.base
        if self._tool == "crop":
            l, t, r, b = c.crop_rect_img()
            box = (l / s, t / s, r / s, b / s)

            def make(progress):
                return core.crop_box(base, box)
        elif self._tool == "persp":
            quad = [(x / s, y / s) for x, y in c.quad_img()]

            def make(progress):
                return core.warp_quad(base, quad)
        else:
            return

        def after():
            self._clear_clone()
            self._go_move()
        self._edit_base("Cropping", make, keep_sel=False, refit=True, after=after)

    def _apply_transform(self):
        p = self.canvas.transform_params()
        if p is None or self.sel is None or not self._loaded:
            return
        cx, cy, sx, sy, ang = p
        s = self.scale
        sel, base = self.sel, self.base
        H, W = self.prev.shape[:2]

        def make(progress):
            mask = self._mask_img(sel, base.size)
            new, nm = core.transform_region(base, mask, cx / s, cy / s, sx, sy, ang)
            nsel = core.resize_mask(np.asarray(nm, dtype=np.float32) / 255.0, (W, H))
            return new, (nsel if float(nsel.max()) > 0.02 else None)
        self._edit_base("Transforming", make, after=self._go_move)

    def rotate(self, deg_ccw):
        if not self._loaded:
            return
        base = self.base
        self._edit_base("Rotating", lambda progress: core.rotate_image(base, deg_ccw), keep_sel=False, refit=True)

    def _auto_shadow(self, live=False):
        """Shadow removal. It always starts from the picture BEFORE the first shadow removal, so the options
        (strength, whiteness ...) can be changed afterwards and the result follows - nothing is applied twice."""
        if not self._loaded:
            return
        if live and self.shadow_src is None:
            return
        src = self.shadow_src if self.shadow_src is not None else self.base
        sel = self.sel
        opts = dict(self.shadow_opts)

        def make(progress):
            new = core.remove_shadow(src, opts["strength"] / 100.0, progress, opts)
            if sel is not None:                      # only the selected part
                new = Image.composite(new, src, self._mask_img(sel, src.size))
            return new

        def after():
            self._shadow_done = sel is None
        self._set_last({"kind": "shadow", "opts": opts, "label": "shadow removal"})
        self._edit_base("Removing shadows", make, after=after, shadow_src=src, merge=live,
                        msg=None if live else "Shadow removed. Move the sliders to change it, Ctrl+Z to undo.")

    def _shadow_opt(self, key, v):
        self.shadow_opts[key] = v
        if self.shadow_src is not None:              # shadow already applied: follow the slider live
            self._shadow_timer.start()

    def reset_image(self):
        if not self._loaded:
            return
        try:
            base = core.load_image(self.path)
        except Exception as ex:
            self.message.emit("Cannot reload: %s" % ex)
            return
        self._checkpoint()                           # Reset can be undone too
        self.base = base
        self.adjs = []
        self.look = core.new_look()
        self.shadow_opts = dict(core.SHADOW_DEFAULTS)
        self.ops = 0
        self.sel = None
        self._sel_ver += 1
        self._shadow_done = False
        self.shadow_src = None
        self._pending = None
        self.colors.rebuild(self.adjs)
        self._sync_sliders()
        self._clear_clone()
        self._go_move()
        self._rebuild_preview(refit=True)
        self._touch()

    # ------------------------------------------------------ copy / drag out
    def export_ready(self):
        """Path of the picture-with-edits file if it is ready now (an untouched picture is its own file)."""
        if not self.is_edited():
            return self.path
        if self._export is not None and self._export[0] == self._ver and os.path.exists(self._export[1]):
            return self._export[1]
        return None

    def prepare_export(self):
        """Makes the edited file in the background, so a drag can start the moment the mouse moves."""
        if not self._loaded or self._exporting or self.export_ready() is not None:
            return
        self._exporting = True
        ver, args = self._ver, self.render_args()
        stem, ext = self.out_name()

        def work(progress):
            os.makedirs(EXPORT_DIR, exist_ok=True)
            path = core.unique_path(EXPORT_DIR, stem, ext)
            core.save_image(core.render_full(*args), path)
            return path

        def done(path):
            self._exporting = False
            self._export = (ver, path)
            self.exportReady.emit()

        def fail(msg):
            self._exporting = False
            self._drag_wait = None
            self.message.emit("Could not prepare the picture: " + msg.splitlines()[0])
        run_background(work, done, fail)

    def export_file(self):
        """The picture with all edits as a file (made now if it is not ready)."""
        self.ensure_loaded()
        ready = self.export_ready()
        if ready is not None:
            return ready
        os.makedirs(EXPORT_DIR, exist_ok=True)
        stem, ext = self.out_name()
        path = core.unique_path(EXPORT_DIR, stem, ext)
        core.save_image(self.result_image(), path)
        self._export = (self._ver, path)
        return path

    def make_mime(self, with_image=True):
        path = self.export_file()
        md = QMimeData()
        md.setUrls([QUrl.fromLocalFile(path)])
        if with_image:
            img = QImage(path)
            if not img.isNull():
                md.setImageData(img)
        return md, path

    def copy_image(self):
        if not self._loaded:
            return
        QApplication.setOverrideCursor(Qt.CursorShape.WaitCursor)
        try:
            md, _ = self.make_mime()
            QApplication.clipboard().setMimeData(md)
            self.message.emit("Copied. Paste it anywhere with Ctrl+V.")
        except Exception as ex:
            self.message.emit("Could not copy: %s" % ex)
        finally:
            QApplication.restoreOverrideCursor()

    def begin_drag(self, source):
        """Starts the drag. If the file is not ready yet, waits for it while the mouse button stays down."""
        if not self._loaded:
            return
        if self.export_ready() is not None:
            self.start_drag(source)
            return
        self._drag_wait = source
        self.message.emit("Preparing the picture - keep the mouse button pressed...")
        self.prepare_export()

    def _on_export_ready(self):
        src, self._drag_wait = self._drag_wait, None
        if src is None:
            return
        if QApplication.mouseButtons() & Qt.MouseButton.LeftButton:
            self.start_drag(src)
        else:
            self.message.emit("The picture is ready now - press and drag it again.")

    def start_drag(self, source):
        path = self.export_ready()
        if path is None:
            return
        md = QMimeData()
        md.setUrls([QUrl.fromLocalFile(path)])         # files only: every program accepts a dropped file
        d = QDrag(source)
        d.setMimeData(md)
        try:
            pm = QPixmap.fromImage(self.canvas._img).scaled(150, 150, Qt.AspectRatioMode.KeepAspectRatio,
                                                           Qt.TransformationMode.SmoothTransformation)
            d.setPixmap(pm)
        except Exception:
            pass
        d.exec(Qt.DropAction.CopyAction, Qt.DropAction.CopyAction)     # copy only: never moves the original file

