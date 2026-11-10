"""One picture = one card.
Top: dropdown tool menus (Select / Move / Erase / Crop) + eyedropper.   Middle: the picture.
Right: tabs  Adjust | Color | Shadow | Text | Picture  (Save is in the top bar).   Every change acts ONLY inside the selection (if there is one)."""
import copy
import math
import os
import tempfile
import uuid

import numpy as np
from PIL import Image, ImageChops, ImageFilter, ImageOps
from PySide6.QtCore import QMimeData, QObject, QPointF, QRectF, Qt, QTimer, QUrl, Signal
from PySide6.QtGui import QColor, QDrag, QImage, QPixmap
from PySide6.QtWidgets import (QSplitter, QApplication, QButtonGroup, QCheckBox, QColorDialog, QFileDialog, QFrame, QHBoxLayout, QLabel, QLineEdit, QMenu,
                               QPushButton, QScrollArea, QSizePolicy, QSlider, QStackedWidget, QToolButton,
                               QVBoxLayout, QWidget)

import core
import textbox
from canvas import ImageCanvas, np_to_qimage
from compare import CompareOverlay
from flow import ElidedLabel, FlowLayout
from jobs import _log, run_background

EXPORT_DIR = os.path.join(tempfile.gettempdir(), "imagefixer_export")

# tool key -> (canvas mode, shape)
TOOL_MODE = {
    "view": (ImageCanvas.VIEW, None), "pick": (ImageCanvas.PICK, None), "paint": (ImageCanvas.PAINT, None),
    "quick": (ImageCanvas.SELECT, "quick"), "brush": (ImageCanvas.SELECT, "brush"), "deselect": (ImageCanvas.SELECT, "brush"),
    "rect": (ImageCanvas.SELECT, "rect"), "ellipse": (ImageCanvas.SELECT, "ellipse"),
    "lasso": (ImageCanvas.SELECT, "lasso"), "color": (ImageCanvas.SELECT, "color"),
    "move": (ImageCanvas.MOVESEL, None), "copy": (ImageCanvas.MOVESEL, None),
    "transform": (ImageCanvas.TRANSFORM, None),
    "crop": (ImageCanvas.CROP, None), "persp": (ImageCanvas.PERSP, None),
    "crop_circle": (ImageCanvas.CROP, None), "crop_oval": (ImageCanvas.CROP, None), "crop_round": (ImageCanvas.CROP, None),
    "erase_brush": (ImageCanvas.ERASE, "brush"), "erase_rect": (ImageCanvas.ERASE, "rect"),
    "erase_ellipse": (ImageCanvas.ERASE, "ellipse"), "erase_lasso": (ImageCanvas.ERASE, "lasso"),
}
SELECT_KEYS = ("quick", "brush", "rect", "ellipse", "lasso", "color", "deselect")
MOVE_KEYS = ("move", "copy", "transform")
ERASE_KEYS = ("erase_brush", "erase_rect", "erase_ellipse", "erase_lasso")
CROP_KEYS = ("crop", "persp", "crop_circle", "crop_oval", "crop_round")
CROP_SHAPE = {"crop_circle": "circle", "crop_oval": "oval", "crop_round": "round"}     # canvas shape of the crop box
NAMES = {"quick": "Quick select", "brush": "Brush select", "deselect": "Deselect brush", "rect": "Box", "ellipse": "Ellipse", "lasso": "Freehand",
         "color": "Color range", "move": "Move", "copy": "Copy", "transform": "Transform",
         "erase_brush": "Brush", "erase_rect": "Box", "erase_ellipse": "Ellipse", "erase_lasso": "Freehand",
         "crop": "Crop", "persp": "Straighten", "crop_circle": "Circle", "crop_oval": "Oval", "crop_round": "Rounded box"}
HINTS = {
    "paint": "Paint with the fill color (swatch on the right; also in the Color tab).  With a selection the color goes ONLY inside it, "
             "without a selection you can paint anywhere on the page.  Size / Opacity / Softness like a paint brush.",
    "view": "Hand: drag the picture.  (Hold Space and drag in any tool, or Ctrl + wheel to zoom)",
    "pick": "Click a color in the picture (ink, stamp, text): its slider appears in the Color tab.",
    "quick": "Brush over an object: the selection grows to its edges.  Shift = add,  Alt = subtract.",
    "brush": "Paint the area to select.  Shift = add,  Alt = subtract.",
    "deselect": "Paint over a part that was selected by mistake: it is taken out of the selection, but only what looks like "
                "what you paint over - the edge stays exact.  (Tolerance = how alike.  'Snap' off = plain eraser.)",
    "rect": "Drag a box.  Shift = add,  Alt = subtract.  Drag INSIDE the selection = move only the outline.",
    "ellipse": "Drag an ellipse.  Shift = add,  Alt = subtract.  Drag INSIDE the selection = move only the outline.",
    "lasso": "Draw freely around an area like with a pen. When you let go it closes by itself.  Shift = add,  Alt = subtract.  Drag INSIDE the selection = move only the outline.",
    "color": "Click a color: every place with that color is selected.  Fuzziness = how wide (move it after the click, it updates).  "
             "Shift+click = add a color,  Alt+click = take a color out.  The clicked color also becomes the fill color.",
    "move": "Drag inside the selection to move it (old place is filled from around).  Alt + drag = copy.",
    "copy": "Drag the selection: a copy is placed there, the original stays.",
    "transform": "Scale / Rotate, Skew, Distort, Perspective, Warp (buttons).  Drag inside = move (Alt + drag = place a copy, the original stays), round handle = rotate.  Keys: [ ] rotate 1°, Ctrl+[ ] 15°, arrows move.  "
                 "Enter = apply,  Esc = cancel.",
    "crop": "Drag the box or its handles, then Apply (Enter).",
    "persp": "Drag the 4 corner points onto the 4 corners of the page, then Apply (Enter).",
    "crop_circle": "Drag the box: a circle fits in its shorter side.  Apply (Enter).  A picture (signature, stamp ...) becomes transparent outside the circle.",
    "crop_oval": "Drag the box: an oval (ellipse) fits in it.  Apply (Enter).  A picture becomes transparent outside the oval.",
    "crop_round": "Drag the box: its corners will be rounded.  Apply (Enter).  A picture becomes transparent outside.",
    "erase_brush": "Paint over what you want to remove: it is filled with what surrounds it.  Alt + click = copy from there.",
    "erase_rect": "Drag a box over what you want to remove.",
    "erase_ellipse": "Drag an ellipse over what you want to remove.",
    "erase_lasso": "Draw around what you want to remove.",
}
COLOR_HINT = ("Eyedropper: click a color in the picture.\n"
              "Lower than 100%: it fades into the paper.  Higher: stronger.  'Change to' recolors it.")
LOOK_TITLES = {"bright": "Brightness", "contrast": "Contrast", "sat": "Saturation", "hue": "Hue", "warm": "Warmth",
               "vib": "Vibrance", "flat": "Even out the light", "flat_size": "Shadow size (manual)", "shlift": "Lift shadows",
               "white": "Whiten paper", "black": "Darken text",
               "sharp": "Text sharpness", "objs": "Line & shape sharpness", "detail": "Faint strokes",
               "dark": "Text darkness", "clear": "Clean specks"}
SHAPES = {"erase_brush": "brush", "erase_rect": "rect", "erase_ellipse": "ellipse", "erase_lasso": "lasso"}

# what the toast says for each kind of work (label given to _edit_base -> icon, text when finished)
ACTIONS = {
    "Copying": ("copy", "Copied"), "Moving": ("move", "Moved"), "Erasing": ("erase", "Erased"),
    "Deleting": ("delete", "Deleted"), "Filling": ("fill", "Filled"), "Painting": ("paint", "Painted"),
    "Cropping": ("crop", "Cropped"), "Cropping to the shape": ("crop", "Cropped"),
    "Rotating": ("rotate", "Rotated"), "Transforming": ("resize", "Transformed"),
    "Removing shadows": ("shadow", "Shadows removed"),
}
TOOL_TITLE = {"view": "Hand", "pick": "Eyedropper", "paint": "Color Brush", "move": "Move", "copy": "Copy",
              "transform": "Transform"}
TOOL_KEYS = {"move": "V", "rect": "M", "lasso": "L", "color": "W", "brush": "B", "erase_brush": "E", "crop": "C",
             "pick": "I", "paint": "P", "view": "H", "transform": "Ctrl+T"}


def action_for(label):
    if label in ACTIONS:
        return ACTIONS[label]
    if label.startswith("Applying"):
        return "adjust", "Applied"
    if label.startswith("Resetting"):
        return "undo", "Reset to the original"
    return "info", label


def tool_kind(key):
    if key in SELECT_KEYS:
        return "select"
    if key in ERASE_KEYS:
        return "erase"
    if key in CROP_KEYS:
        return "crop"
    return {"move": "move", "copy": "copy", "transform": "resize", "pick": "pick", "paint": "paint",
            "view": "view"}.get(key, "tool")


def tool_title(key):
    if key in TOOL_TITLE:
        return TOOL_TITLE[key]
    name = NAMES.get(key, key)
    if key in SELECT_KEYS:
        return "Select: " + name
    if key in ERASE_KEYS:
        return "Erase: " + name
    return name


def dir_words(dx, dy):
    parts = []
    if dx:
        parts.append("%d px %s" % (abs(dx), "right" if dx > 0 else "left"))
    if dy:
        parts.append("%d px %s" % (abs(dy), "down" if dy > 0 else "up"))
    return ", ".join(parts)


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




class _SideSync(QObject):
    moved = Signal(int)
    panelChanged = Signal(bool)                # the Panel button (show / hide the side panel) was pressed on one picture


_SIDE_SYNC = None


def side_sync():
    """One object all pictures listen to: drag the panel bar on one picture and the others follow."""
    global _SIDE_SYNC
    if _SIDE_SYNC is None:
        _SIDE_SYNC = _SideSync()
    return _SIDE_SYNC


class Card(QWidget):
    TAB_ADJUST, TAB_COLOR, TAB_SHADOW, TAB_TEXT, TAB_PICTURE, TAB_TEXTBOX = range(6)   # order of the side tabs
    SIDE_W = 350                                                                     # width of the side panel (shared, drag to change)
    NARROW_W = 720                                                                   # a narrower picture card: the panel goes UNDER the picture
    SHORT_H = 520                                                                    # a lower card: the hint line is hidden (warnings still show)
    PANEL_USER = None                                                                # None = automatic; True / False = chosen with the Panel button (all pictures)
    editedChanged = Signal(object)
    removeRequested = Signal(object)
    copyRequested = Signal(object)
    printRequested = Signal(object)
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
        self._shadow_busy = False
        self._shadow_again = False
        self.layers = []                           # pictures placed on the page (signature, stamp ...), each its own layer
        self.ctx = None                            # id of the picture layer that is being edited (None = the page)
        self.alpha = None                          # transparency of that layer ('L'); self.base is then the layer's colours
        self._stash = None                         # the page's own state while a layer is edited
        self._hide_layer = None                    # a layer that is being moved is not drawn twice
        self._place_id = None
        self.sel_layer = None
        self._layer_seq = 0
        self._bg_busy = False
        self._bg_again = False
        self.shadow_src = None                     # picture before shadow removal: lets the shadow options be changed later
        self.last_action = None                    # the last setting changed: what 'apply to all' copies
        self.keep_ink = True
        self._cr = None                            # the last Color range click (so Fuzziness can redo it)
        self._cr_timer = QTimer(self)
        self._cr_timer.setSingleShot(True)
        self._cr_timer.setInterval(70)
        self._cr_timer.timeout.connect(self._recompute_color_range)
        self._pending = None
        self._auto_back = False                    # a picture was opened only to crop it: go back to the page afterwards
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
        self._loading = False
        self.origin = "file"                      # 'file' = opened from the disk, 'phone' / 'paste' = has no file of its own to replace
        self.orig_path = None                      # backup of the original file once 'Save' has replaced it
        self._orig_q = None                        # the original picture (QImage) for the comparison, made once
        self._dead = False                         # removed from the window: late results of background work are dropped
        self._tool = "view"
        self._ver = 0
        self._saved_ver = -1
        self._view_gen = 0
        self._rendering = False
        self._again = False
        self.saved_path = None
        self._lcache = {}                          # per-layer warped patches: moving / typing in one layer re-makes only that layer
        self._base_cache = None                    # (key, refs, picture before the layers): layer work never redoes the page maths
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
        self._bg_timer = QTimer(self)              # background-removal sliders: re-cut a moment after the last move
        self._bg_timer.setSingleShot(True)
        self._bg_timer.setInterval(160)
        self._bg_timer.timeout.connect(self._bg_apply)
        self._shadow_timer = QTimer(self)          # shadow sliders: re-apply live, a moment after the last move
        self._shadow_timer.setSingleShot(True)
        self._shadow_timer.setInterval(450)
        self._shadow_timer.timeout.connect(lambda: self._auto_shadow(live=True))
        self._type_timer = QTimer(self)            # typing in a text box: make the picture of the text a moment after the last key
        self._type_timer.setSingleShot(True)
        self._type_timer.setInterval(180)
        self._type_timer.timeout.connect(self._inline_flush)
        self._vertical = None                      # panel under (True) / beside (False) the picture; None = not placed yet
        self._panel_shown = None
        self._short = False
        self._hint_warn = False
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
        lay.setContentsMargins(0, 2, 4, 2)
        lay.setSpacing(6)
        sc.setWidget(host)
        return sc, lay

    def _group_box(self, title=None, collapsed=False, collapsible=True):
        """A framed group.  With a title it is a drop-down: click the title to fold / unfold it.  Returns (frame, layout)."""
        f = QFrame()
        f.setObjectName("group")
        outer = QVBoxLayout(f)
        outer.setContentsMargins(8, 6, 8, 8)
        outer.setSpacing(4)
        body = QWidget()
        l = QVBoxLayout(body)
        l.setContentsMargins(0, 0, 0, 0)
        l.setSpacing(6)
        if title and collapsible:
            hdr = QPushButton(("\u25b8  " if collapsed else "\u25be  ") + title)
            hdr.setProperty("gh", True)
            hdr.setCursor(Qt.CursorShape.PointingHandCursor)
            hdr.setToolTip("Click to fold / unfold")

            def toggle(_=False, hdr=hdr, body=body, title=title):
                show = not body.isVisible()
                body.setVisible(show)
                hdr.setText(("\u25be  " if show else "\u25b8  ") + title)
            hdr.clicked.connect(toggle)
            outer.addWidget(hdr)
            body.setVisible(not collapsed)
            f.expand = lambda body=body, hdr=hdr, title=title: (body.setVisible(True), hdr.setText("\u25be  " + title))
        elif title:
            t = QLabel(title)
            t.setObjectName("sub")
            outer.addWidget(t)
        outer.addWidget(body)
        return f, l

    def _scope_label(self):
        box = QWidget()
        bl = QVBoxLayout(box)
        bl.setContentsMargins(0, 0, 0, 0)
        bl.setSpacing(4)
        lab = QLabel("")
        lab.setObjectName("hint")
        lab.setWordWrap(True)
        self._scope_labels.append(lab)
        btn = QPushButton("Apply to the selection")
        btn.setProperty("accent", True)
        btn.setToolTip("Makes these changes part of the picture. They stay when you deselect (undo with Ctrl+Z).\n"
                       "If you deselect without pressing this, it is applied automatically.")
        btn.clicked.connect(lambda: self._bake(None, True))
        self._bake_btns.append(btn)
        bl.addWidget(lab)
        bl.addWidget(btn)
        return box

    def _pending_settings(self):
        return core.active_adjs(self.adjs) or core.look_edited(self.look)

    def _bake(self, then=None, manual=False):
        """Settings made with a selection active are written into the picture (inside the selection) before the
        selection goes away / changes, so they never spill over to the whole picture.  Returns True when it
        started (then() is called when finished)."""
        if not self._loaded or not self._pending_settings() or (manual and self.sel is None):
            if manual:
                self._warn("Nothing to apply: select something and change a setting first.")
            return False
        # Settings made with NO selection belong to the whole picture: they are written into it now (whole picture),
        # otherwise the new selection would take them over and the rest of the picture would go back to how it was.
        whole = self.sel is None
        a0, l0, sel0, base0 = copy.deepcopy(self.adjs), dict(self.look), self.sel, self.base
        self._checkpoint()                           # one undo step brings the settings back
        self.adjs = []
        self.look = core.new_look()
        self.colors.rebuild(self.adjs)
        self._sync_sliders()

        def make(progress):
            return core.render_full(base0, a0, l0, sel0), sel0
        self._edit_base("Applying your changes to the whole picture" if whole else "Applying to the selection", make,
                        after=then, checkpoint=False,
                        msg=("Applied to the selection." if manual else
                             ("Your changes so far are part of the picture now. New changes go only to the selection."
                              if whole else None)))
        return True

    def _look_row(self, key, title, lo, hi, caps, tip, unit="%"):
        row = SliderRow(title, lo, hi, self.look[key], caps, tip, unit)
        row.changed.connect(lambda v, k=key: self._set_look(k, v))
        self.lookRows[key] = row
        return row

    # ------------------------------------------------------------------ build
    def _build(self):
        self.lookRows = {}
        self._scope_labels = []
        self._bake_btns = []
        root = QVBoxLayout(self)
        root.setContentsMargins(10, 6, 8, 4)
        root.setSpacing(4)
        self._root = root
        self._r1 = None

        # --- row 1: name + history
        r1 = QHBoxLayout()
        r1.setSpacing(8)
        self.lblName = ElidedLabel(self.name)
        self.lblName.setObjectName("title")
        self.lblInfo = self._label("")
        self.lblState = self._label("")
        self.btnUndo = QPushButton("↶ Undo")
        self.btnUndo.setToolTip("Undo one step (Ctrl+Z)")
        self.btnRedo = QPushButton("Redo ↷")
        self.btnRedo.setToolTip("Redo one step (Ctrl+Y or Ctrl+Shift+Z)")
        self.btnReset = QPushButton("Reset")
        self.btnReset.setToolTip("Back to the original picture")
        self.btnCompare = QPushButton("Compare")
        self.btnCompare.setCheckable(True)
        self.btnCompare.setToolTip("Show the original on the left and the edited picture on the right, in the picture itself.\n"
                                   "Click again to switch it off.")
        self.btnCompare.hide()                      # only a picture that was changed has something to compare
        self.btnCmpMode = QPushButton("Slider")
        self.btnCmpMode.setToolTip("Slider (drag the line)  /  Side by side")
        self.btnCmpMode.hide()
        self._cmp = None                            # the overlay while comparing
        self.btnRemove = QPushButton("Remove")
        self.btnRemove.setProperty("danger", True)
        self.btnRemove.setToolTip("Remove this picture from the list (the file on the disk is not touched)")
        self.btnPanel = QPushButton("Panel")
        self.btnPanel.setCheckable(True)
        self.btnPanel.setChecked(True)
        self.btnPanel.setToolTip("Show / hide the side panel (Adjust, Color, Shadow ...) to give the picture more room")
        self._r1 = r1
        r1.addWidget(self.lblName)
        r1.addWidget(self.lblInfo)
        r1.addWidget(self.lblState)
        r1.addStretch(1)
        for b in (self.btnPanel, self.btnCompare, self.btnCmpMode, self.btnUndo, self.btnRedo, self.btnReset, self.btnRemove):
            r1.addWidget(b)
        root.addLayout(r1)

        # --- row 2: tool menus
        r2 = FlowLayout(hspacing=6, vspacing=6)
        self.btnHand = QPushButton("Hand")
        self.btnHand.setCheckable(True)
        self.btnHand.setChecked(True)
        self.btnHand.setToolTip("Drag the picture (H).  Space + drag works in every tool.")
        self.btnPick = QPushButton("Eyedropper")
        self.btnPick.setCheckable(True)
        self.btnPick.setToolTip("Click a color in the picture to change only that color (I)")
        self.btnPaint = QPushButton("Color Brush")
        self.btnPaint.setCheckable(True)
        self.btnPaint.setToolTip("Paint with a color (P). Inside the selection only - or anywhere if nothing is selected.")
        T = self.choose_tool
        self.menuSelect = self._menu_button("Select", [
            ("Brush select\tB", lambda: T("brush")),
            ("Box\tM", lambda: T("rect")), ("Ellipse", lambda: T("ellipse")),
            ("Freehand\tL", lambda: T("lasso")), ("Color range\tW", lambda: T("color")),
            ("Deselect brush\tShift+D", lambda: T("deselect")),
            None, ("Select all\tCtrl+A", self.select_all), ("Deselect\tCtrl+D", self.deselect),
            ("Inverse\tCtrl+Shift+I", self.invert_selection)], "Ways to select a part of the picture")
        self.menuMove = self._menu_button("Move", [
            ("Move\tV", lambda: T("move")), ("Copy", lambda: T("copy")),
            ("Transform\tCtrl+T", self.start_transform)], "Move, copy or resize the selection")
        self.menuErase = self._menu_button("Erase", [
            ("Brush\tE", lambda: T("erase_brush")), ("Box", lambda: T("erase_rect")),
            ("Ellipse", lambda: T("erase_ellipse")), ("Freehand", lambda: T("erase_lasso")), None,
            ("Delete selection\tDel", self.delete_selection),
            ("Fill with color", self.fill_selection)], "Remove things; the place is filled from around it")
        self.menuCrop = self._menu_button("Crop", [
            ("Crop\tC", lambda: T("crop")), ("Straighten", lambda: T("persp")), None,
            ("To circle", lambda: T("crop_circle")), ("To oval", lambda: T("crop_oval")),
            ("To rounded box", lambda: T("crop_round")), ("To selection shape", self.crop_to_selection), None,
            ("Rotate left", lambda: self.rotate(90.0)), ("Rotate right", lambda: self.rotate(-90.0))],
            "Crop (also a picture you placed: signature, stamp ...) to a box, circle, oval, rounded box or any selected shape; "
            "straighten a photographed page; rotate")
        self.menus = {"select": self.menuSelect, "move": self.menuMove, "erase": self.menuErase, "crop": self.menuCrop}
        self.btnLayerDone = QPushButton("◀  Back to the page")
        self.btnLayerDone.setProperty("accent", True)
        self.btnLayerDone.setToolTip("You are editing one picture on its own. Click to go back to the whole page.")
        self.btnLayerDone.hide()
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
        for w in (self.menuSelect, self.menuMove, self.btnPick, self.btnPaint, self.menuErase, self.menuCrop):
            r2.addWidget(w)
        self.btnText = QPushButton("T  Text box")
        self.btnText.setToolTip("Text box (T): write Bangla / English on the page. Click it any time to move, resize, rotate, Alt + drag = copy, double click = edit.")
        r2.addWidget(self.btnText)
        r2.addSpacing(10)
        r2.addWidget(self.btnLayerDone)
        r2.addWidget(self.btnApply)
        r2.addWidget(self.btnCancel)
        r2.addStretch(1)
        r2.addWidget(self.lblZoom)
        root.addLayout(r2)

        # --- row 3: options of the chosen tool
        self.optsA = QWidget()
        a = FlowLayout(self.optsA, hspacing=8, vspacing=6)
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
        self.tfBox = QWidget()
        tfl = FlowLayout(self.tfBox, hspacing=4, vspacing=4)
        self.tfGroup = QButtonGroup(self)
        self.tfGroup.setExclusive(True)
        self.tfBtns = {}
        for key, text, tip in (("free", "Scale / Rotate", "Resize, rotate, move (corner handle keeps the shape, Shift = free)"),
                               ("skew", "Skew", "Slide a side along itself to slant the part"),
                               ("distort", "Distort", "Drag any corner or side anywhere - makes a crooked part straight"),
                               ("perspective", "Perspective", "Drag a corner: the neighbouring corners move the opposite way"),
                               ("warp", "Warp", "A net of 25 points (corners, middle of each side and the middle of the picture): pull points to bend it")):
            b = QPushButton(text)
            b.setCheckable(True)
            b.setToolTip(tip)
            b.clicked.connect(lambda _=False, k=key: self.set_tf_mode(k))
            self.tfGroup.addButton(b)
            self.tfBtns[key] = b
            tfl.addWidget(b)
        self.tfBtns["free"].setChecked(True)
        for text, tip, fn in (("⟲ 90°", "Rotate 90° left (Ctrl+Alt+Left)", lambda: self.tf_rotate(-90)),
                              ("⟳ 90°", "Rotate 90° right (Ctrl+Alt+Right)", lambda: self.tf_rotate(90)),
                              ("⇋ Flip", "Mirror left-right", lambda: self.canvas.tf_flip(True)),
                              ("⇵ Flip", "Mirror top-bottom", lambda: self.canvas.tf_flip(False))):
            b = QPushButton(text)
            b.setToolTip(tip)
            b.clicked.connect(lambda _=False, f=fn: f())
            tfl.addWidget(b)
        a.addWidget(self.tfBox)
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
        self.paintBox = QWidget()                       # Color Brush: opacity, softness, color
        pb = QHBoxLayout(self.paintBox)
        pb.setContentsMargins(0, 0, 0, 0)
        pb.setSpacing(6)
        self.opSlider = NoWheelSlider(Qt.Orientation.Horizontal)
        self.opSlider.setRange(1, 100)
        self.opSlider.setValue(100)
        self.opSlider.setFixedWidth(100)
        self.opSlider.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.opSlider.setToolTip("How strong the color is (100% = fully covers, lower = see-through)")
        self.lblOp = self._label("100%")
        self.lblOp.setMinimumWidth(40)
        self.softSlider = NoWheelSlider(Qt.Orientation.Horizontal)
        self.softSlider.setRange(0, 100)
        self.softSlider.setValue(0)
        self.softSlider.setFixedWidth(100)
        self.softSlider.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.softSlider.setToolTip("Edge of the brush: 0 = sharp, 100 = very soft")
        self.lblSoft = self._label("0%")
        self.lblSoft.setMinimumWidth(36)
        self.brushSwatch = QPushButton()
        self.brushSwatch.setFixedSize(34, 24)
        self.brushSwatch.setToolTip("Brush color. Click to choose another color.")
        for w in (self._label("Opacity"), self.opSlider, self.lblOp, self._label("Softness"), self.softSlider,
                  self.lblSoft, self._label("Color"), self.brushSwatch):
            pb.addWidget(w)
        a.addWidget(self.paintBox)
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
        self.lblTol = self._label("Tolerance")
        self.lblTolV = self._label("28")
        self.lblTolV.setMinimumWidth(30)
        self._tolv, self._fuzz = 28, 50                  # tolerance (quick / deselect) and fuzziness (color range) are kept apart
        self.chkLocal = QCheckBox("Near the click")
        self.chkLocal.setToolTip("Color range: only the colors around the place you click (not the same color far away)")
        self.localSlider = NoWheelSlider(Qt.Orientation.Horizontal)
        self.localSlider.setRange(3, 80)
        self.localSlider.setValue(22)
        self.localSlider.setFixedWidth(120)
        self.lblLocalV = self._label("22%")
        self.lblLocalV.setMinimumWidth(34)
        self.localSlider.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.localSlider.setToolTip("How far around the click (percent of the picture). The yellow circle on the picture shows it.")
        self.chkPrev = QCheckBox("Preview")
        self.chkPrev.setToolTip("Show the selection as black and white (white = selected, grey = partly, black = not selected),\n"
                                "like the preview in Photoshop's Color Range. Move Fuzziness to see what changes.")
        self.chkSnap = QCheckBox("Snap to edges")
        self.chkSnap.setChecked(True)
        self.chkSnap.setToolTip("Deselect brush: take out only what looks like what you paint over (exact edges).\nOff = a plain eraser for the selection.")
        tb.addWidget(self.lblTol)
        tb.addWidget(self.tolSlider)
        tb.addWidget(self.lblTolV)
        tb.addWidget(self.chkLocal)
        tb.addWidget(self.localSlider)
        tb.addWidget(self.lblLocalV)
        tb.addWidget(self.chkSnap)
        tb.addWidget(self.chkPrev)
        self.chkPrev.toggled.connect(lambda _on: self._sync_preview())
        self.tolSlider.valueChanged.connect(self._tol_changed)
        self.chkLocal.toggled.connect(lambda _on: self._recompute_color_range())
        self.localSlider.valueChanged.connect(lambda v: (self.lblLocalV.setText("%d%%" % v), self._recompute_color_range()))
        a.addWidget(self.tolBox)
        self.btnClone = QPushButton("Clear copy spot")
        self.btnClone.setToolTip("Go back to filling from the surroundings")
        self.btnClone.hide()
        a.addWidget(self.btnClone)
        a.addStretch(1)
        root.addWidget(self.optsA)

        self.optsB = QWidget()
        b_ = FlowLayout(self.optsB, hspacing=6, vspacing=6)
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
        self.canvas = ImageCanvas()
        self.canvas.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        self.canvas.setMinimumSize(120, 100)             # a small window must still be possible
        self.split = QSplitter(Qt.Orientation.Horizontal)          # drag the bar between picture and panel to resize
        self.split.setChildrenCollapsible(False)
        self.split.setHandleWidth(8)
        self.split.addWidget(self.canvas)
        self.split.addWidget(self._build_side())
        self.split.setStretchFactor(0, 1)
        self.split.setStretchFactor(1, 0)
        self.split.handle(1).setCursor(Qt.CursorShape.SplitHCursor)
        self.split.handle(1).setToolTip("Drag to make the panel wider or narrower")
        self.split.splitterMoved.connect(self._side_moved)
        side_sync().moved.connect(self._side_set)
        root.addWidget(self.split, 1)

        # --- wiring
        self.btnHand.clicked.connect(lambda: self.choose_tool("view"))
        self.btnPick.clicked.connect(lambda: self.choose_tool("pick"))
        self.btnPaint.clicked.connect(lambda: self.choose_tool("paint"))
        self.opSlider.valueChanged.connect(self._sync_paint)
        self.softSlider.valueChanged.connect(self._sync_paint)
        self.brushSwatch.clicked.connect(self._pick_fill)
        self.modeGroup.idClicked.connect(lambda i: self._set_sel_mode(("new", "add", "sub", "int")[i]))
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
        self.canvas.zoomChanged.connect(lambda z: self._inline_place())
        self.canvas.applyRequested.connect(self.apply_pending)
        self.canvas.cancelRequested.connect(self.cancel_pending)
        self.canvas.eraseFinished.connect(self._on_erase)
        self.canvas.paintFinished.connect(self._on_paint)
        self.canvas.sourcePicked.connect(self._on_source)
        self.canvas.selectFinished.connect(self._on_select)
        self.canvas.selectionMoved.connect(self._move_selection)
        self.canvas.sel_mode_name = self.sel_mode
        self.canvas.moveFinished.connect(self._on_move)
        self.canvas.viewClicked.connect(self._on_view_click)
        self.canvas.tfCommitted.connect(self._commit_layer_pts)
        self.canvas.tfCopyChanged.connect(self._on_tf_copy)
        self.canvas.layerOutside.connect(self._on_layer_outside)
        self.canvas.layerEditRequested.connect(self._layer_double)
        self.canvas.layerCopyRequested.connect(self._copy_layer_drag)
        self.canvas.layer_probe = self._layer_probe
        self.canvas.layerPressed.connect(self._on_layer_pressed)
        self.btnApply.clicked.connect(self.apply_pending)
        self.btnCancel.clicked.connect(self.cancel_pending)
        self.btnUndo.clicked.connect(self.undo_step)
        self.btnRedo.clicked.connect(self.redo_step)
        self.btnReset.clicked.connect(self.reset_image)
        self.btnCompare.clicked.connect(self.compare)
        self.btnCmpMode.clicked.connect(self._cmp_mode)
        self.btnRemove.clicked.connect(lambda: self.removeRequested.emit(self))
        self.btnPanel.clicked.connect(self._panel_clicked)
        side_sync().panelChanged.connect(self._on_panel_sync)       # bound method: disconnects itself when the card is deleted
        self.btnSelVis.toggled.connect(self._toggle_sel_vis)
        self.colors.changed.connect(self._on_adj_changed)
        self.colors.removeAdj.connect(self._remove_adj)
        self.colors.removeBands.connect(self._remove_bands)
        self.colors.clearAll.connect(self._clear_adjs)
        self.colors.activated.connect(self._set_focus)
        self.colors.selectAdj.connect(self._select_adj)
        self.exportReady.connect(self._on_export_ready)
        self._tool_widgets = [self.btnHand, self.btnPick, self.btnPaint, self.btnApply, self.btnCancel, self.btnSelAll,
                              self.btnDeselect, self.btnInvert, self.btnGrow, self.btnShrink, self.btnSave,
                              self.btnSaveAs, self.btnPdfAll, self.btnPrint, self.btnApplyAll, self.btnCopy, self.btnDrag,
                              self.btnShadow, self.btnFill, self.btnDelete, self.btnPickFill] \
            + list(self.menus.values()) + self.modeBtns
        self._enable_tools(False)
        self._set_tool("view")
        self._sel_label()
        self._update_buttons()

    def resizeEvent(self, e):
        super().resizeEvent(e)
        self._fit_card()

    def _fit_card(self):
        """The card follows its own size: narrow = the panel goes under the picture, low = the hint line is hidden,
        very small = the panel is hidden (the Panel button brings it back).  Nothing is cut off."""
        if not hasattr(self, "split") or not hasattr(self, "_side") or self._dead:
            return
        w, h = self.width(), self.height()
        if w < 120 or h < 120:
            return
        vertical = w < self.NARROW_W
        tiny = (vertical and h < 560) or h < 340
        short = h < self.SHORT_H
        compact = w < 640
        narrow = w < 560
        # --- text of the top row
        self.lblInfo.setVisible(not narrow)
        self.lblState.setVisible(w >= 520)
        self.btnUndo.setText("\u21b6" if compact else "\u21b6 Undo")
        self.btnRedo.setText("\u21b7" if compact else "Redo \u21b7")
        self.btnRemove.setText("\u2715" if compact else "Remove")
        self._r1.setSpacing(4 if compact else 8)
        self._root.setContentsMargins(*((6, 4, 4, 2) if compact or short else (10, 6, 8, 4)))
        self._root.setSpacing(3 if short else 4)
        self._short = short
        self._sync_hint()
        # --- panel: beside or under the picture, shown or hidden
        user = Card.PANEL_USER
        show = (not tiny) if user is None else bool(user)
        self.btnPanel.blockSignals(True)
        self.btnPanel.setChecked(show)
        self.btnPanel.blockSignals(False)
        replace = False
        if vertical != self._vertical:
            self._vertical = vertical
            self.split.setOrientation(Qt.Orientation.Vertical if vertical else Qt.Orientation.Horizontal)
            self.split.setStretchFactor(0, 3 if vertical else 1)
            self.split.setStretchFactor(1, 2 if vertical else 0)
            self._side.setMinimumWidth(0 if vertical else 290)
            self._side.setMinimumHeight(110 if vertical else 0)
            self.split.handle(1).setToolTip("Drag to make the panel %s" % ("higher or lower" if vertical else "wider or narrower"))
            replace = True
        if show != self._panel_shown:
            self._panel_shown = show
            self._side.setVisible(show)
            replace = show
        if replace or (show and not vertical and self._side.width() > w * 0.55):
            QTimer.singleShot(0, self._place_split)

    def _place_split(self):
        """Sizes of picture and panel (the panel width is the shared, remembered one)."""
        if self._dead or self._side.isHidden():
            return
        self.split.blockSignals(True)
        if self._vertical:
            total = max(1, self.split.height())
            ph = max(110, int(total * 0.42))
            self.split.setSizes([max(60, total - ph), ph])
        else:
            total = max(1, self.split.width())
            sw = max(290, min(Card.SIDE_W, int(total * 0.5)))
            self.split.setSizes([max(60, total - sw), sw])
        self.split.blockSignals(False)

    def _on_panel_sync(self, _on=False):
        if not self._dead:
            self._fit_card()

    def _panel_clicked(self, on):
        Card.PANEL_USER = bool(on)
        side_sync().panelChanged.emit(bool(on))

    def _sync_hint(self):
        self.lblHint.setVisible((not self._short) or self._hint_warn)

    def _side_moved(self, _pos=0, _idx=0):
        if self._vertical:
            return                                             # under the picture: the height is not the shared width
        w = self.split.sizes()[1]
        if w >= 200:
            Card.SIDE_W = w                                    # every picture's panel follows
            side_sync().moved.emit(w)

    def _side_set(self, w):
        if self._vertical or self._side.isHidden():
            return
        sz = self.split.sizes()
        total = sum(sz)
        if total <= 0 or abs(sz[1] - w) < 3:
            return
        self.split.blockSignals(True)
        self.split.setSizes([total - w, w])
        self.split.blockSignals(False)

    def _build_side(self):
        side = QWidget()
        side.setObjectName("panel")
        side.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        side.setMinimumWidth(290)
        side.setMaximumWidth(900)
        self._side = side
        v = QVBoxLayout(side)
        v.setContentsMargins(12, 8, 6, 8)
        v.setSpacing(6)
        tabs = QHBoxLayout()
        tabs.setSpacing(0)
        self.tabGroup = QButtonGroup(self)
        self.tabGroup.setExclusive(True)
        self.stackPages = QStackedWidget()
        names = ("Adjust", "Color", "Shadow", "Text", "Picture", "Aa")
        for i, n in enumerate(names):
            b = QPushButton(n)
            b.setCheckable(True)
            b.setProperty("tab", True)
            if n == "Color":
                b.setToolTip("Remove a stamp or mark, Color range & fill, Picked colors")
            elif n == "Aa":
                b.setToolTip("Text box: write Bangla / English text on the page (font, size, color, bold ...)")
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
        gl.addWidget(self._look_row("sat", "Saturation", 0, 200, ("gray", "original", "very vivid"),
                                    "How strong all colors are.  100 = as it is, 0 = a gray picture, 200 = twice as strong.\n"
                                    "Brightness and hue stay the same; black, white and gray never change; strong colors\n"
                                    "are eased in so they do not burn out."))
        gl.addWidget(self._look_row("vib", "Vibrance", -100, 100, ("muted", "", "punchy"),
                                    "Like saturation, but weak colors change more than colors that are already strong\n"
                                    "(skin and strong inks are protected).", ""))
        gl.addWidget(self._look_row("hue", "Hue (turn the colors)", -180, 180, ("", "", ""),
                                    "Turns every color around the color wheel. Select only the red part, then +120 "
                                    "makes red into green.", "°"))
        gl.addWidget(self._look_row("warm", "Warmth", -100, 100, ("cooler", "", "warmer"),
                                    "Cooler = bluer, warmer = more yellow / red.", ""))
        lay.addWidget(g)
        lay.addStretch(1)
        self.stackPages.addWidget(pg)

        # ---------------- Color tab: stamp removal, color range & fill, picked colors (their own tab)
        pg, lay = self._page()
        lay.addWidget(self._scope_label())
        # ---- removing a stamp / mark: its own group, easy to find
        gs, gls = self._group_box("Remove a stamp or mark")
        hs = QLabel("Select the stamp (Select menu > Color range is the quickest), then press the button. "
                    "The place is filled from what is around it.")
        hs.setObjectName("hint")
        hs.setWordWrap(True)
        gls.addWidget(hs)
        self.chkInk = QCheckBox("Keep the dark writing under the stamp")
        self.chkInk.setChecked(True)
        self.chkInk.setToolTip("A coloured stamp on top of a black signature or text: the writing is kept (drawn from what is\n"
                               "still visible) instead of leaving a white gap.  Switch it off to remove everything.")
        self.chkInk.toggled.connect(lambda on: setattr(self, "keep_ink", bool(on)))
        gls.addWidget(self.chkInk)
        self.btnRemoveStamp = QPushButton("Remove selected stamp (Del)")
        gls.addWidget(self.btnRemoveStamp)
        lay.addWidget(gs)

        # ---- color range + fill color: one thing
        g2, gl2 = self._group_box("Color range & fill")
        hc = QLabel("Choose the Color range tool and click a color: it is selected, and it becomes the fill color. "
                    "Fuzziness (how wide) is in the bar above the picture.")
        hc.setObjectName("hint")
        hc.setWordWrap(True)
        gl2.addWidget(hc)
        fr = FlowLayout(hspacing=6, vspacing=6)
        self.swatch = QPushButton()
        self.swatch.setFixedSize(40, 30)
        self.swatch.setToolTip("The fill color. Click to choose another color.")
        self.edHex = QLineEdit()
        self.edHex.setFixedWidth(84)
        self.edHex.setMaxLength(7)
        self.edHex.setToolTip("Color as #RRGGBB")
        self.btnPickFill = QPushButton("From picture")
        self.btnPickFill.setToolTip("Click a spot in the picture: its color becomes the fill color")
        self.btnPaper = QPushButton("Paper")
        self.btnPaper.setToolTip("The color of the paper (picture border)")
        self.btnWhite = QPushButton("White")
        self.btnBlack = QPushButton("Black")
        for w in (self.swatch, self.edHex, self.btnPickFill, self.btnPaper, self.btnWhite, self.btnBlack):
            fr.addWidget(w)
        fw = QWidget()
        fw.setLayout(fr)
        gl2.addWidget(fw)
        self.chkUseClick = QCheckBox("A Color range click sets the fill color")
        self.chkUseClick.setChecked(True)
        gl2.addWidget(self.chkUseClick)
        self.btnFill = QPushButton("Fill selection with this color")
        self.btnDelete = QPushButton("Delete selection  (Del)")
        self.btnDelete.setToolTip("Remove what is selected; the place is filled from its surroundings")
        gl2.addWidget(self.btnFill)
        gl2.addWidget(self.btnDelete)
        self._paint_swatch()
        lay.addWidget(g2)
        self.colors = ColorList()
        self.colors.scroll = pg
        g3, gl3 = self._group_box("Picked colors")
        self.grpPicked = g3
        gl3.addWidget(self.colors)
        lay.addWidget(g3)
        lay.addStretch(1)
        self.stackPages.addWidget(pg)
        self.btnFill.clicked.connect(self.fill_selection)
        self.btnDelete.clicked.connect(self.delete_selection)
        self.btnRemoveStamp.clicked.connect(self.delete_selection)
        self.swatch.clicked.connect(self._pick_fill)
        self.btnPickFill.clicked.connect(self._eyedrop_fill)
        self.btnWhite.clicked.connect(lambda: self._set_fill((255, 255, 255)))
        self.btnBlack.clicked.connect(lambda: self._set_fill((0, 0, 0)))
        self.btnPaper.clicked.connect(self._paper_fill)
        self.edHex.editingFinished.connect(self._hex_fill)

        # ---------------- Shadow
        pg, lay = self._page()
        lay.addWidget(self._scope_label())
        shparts = {}
        gn, gln = self._group_box("Newspaper / book photo  \u2192  white page", collapsible=False)
        hn = QLabel("For a photo of a newspaper or a book page: the text and the shapes stay as they are, the page turns white. "
                    "One click - then slide the strength down or up. The slider works before and after the click.")
        hn.setObjectName("hint")
        hn.setWordWrap(True)
        gln.addWidget(hn)
        self.btnNews = QPushButton("Make the page white  (one click)")
        self.btnNews.setProperty("accent", True)
        self.btnNews.setMinimumHeight(40)
        gln.addWidget(self.btnNews)
        gln.addWidget(self._look_row("news", "White page", 0, 100, ("off", "", "very white"),
                                     "How strongly the page is made white.  Down = more of the page and its grey tones stay,\n"
                                     "up = cleaner white.  Works with or without the one-click button.", ""))
        gln.addWidget(self._look_row("news_keep", "Protect light writing", 0, 100, ("less", "", "more"),
                                     "Up = pale writing, thin lines and light shapes are kept (a little grey may stay).\n"
                                     "Down = a cleaner page, but pale details can fade.", ""))
        shparts["white"] = gn
        self.btnNews.clicked.connect(self._news_click)
        g, gl = self._group_box("One-click shadow removal", collapsible=False)
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
                 "Small = follows small shadows (a pen, folds). Large = only wide, soft shadows."),
                ("even", "Even out the light", ("a little", "", "fully"),
                 "How much of the uneven light is evened out.  Lower keeps some of the natural light and shade."),
                ("depth", "Shadow depth", ("light only", "", "very dark too"),
                 "How dark a shadow may be and still be lifted.  Raise it for deep shadows (a hand, a fold).\\n"
                 "Lower it if dark parts of the picture (a black box) start to turn grey."),
                ("tint", "Keep paper color", ("white", "", "keep tint"),
                 "0 = the paper becomes neutral white.  Higher keeps its own color (cream, blue-ish ...)."),
                ("clean", "Clean the paper", ("off", "", "strong"),
                 "Almost-white paper (grain, faint stains, noise) becomes clean white.  Text is not touched.")):
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
        shparts["one"] = g
        g2, gl2 = self._group_box("Manual shadow removal", collapsible=False)
        info = QLabel("Works at once - no click needed.  Select a part of the picture first (Select menu) to fix only "
                      "that part, for example a shadow in one corner.  After the one-click button it fine-tunes the result.")
        info.setObjectName("hint")
        info.setWordWrap(True)
        gl2.addWidget(info)
        gl2.addWidget(self._look_row("flat", "Even out the light", 0, 100, ("off", "", "full"),
                                     "Measures how light the paper is everywhere and brightens the dark parts to match.\n"
                                     "Best for uneven light: one side darker, a window shadow, a curved page.", ""))
        gl2.addWidget(self._look_row("flat_size", "Shadow size", 0, 100, ("small", "", "wide"),
                                     "Small = follows small shadows (a finger, a fold).  Wide = only large soft shadows.\n"
                                     "Used by 'Even out the light'.", ""))
        gl2.addWidget(self._look_row("shlift", "Lift shadows", 0, 100, ("off", "", "strong"),
                                     "Dull, shaded paper gets lighter.  Real black writing stays dark.\n"
                                     "Use it for what is left after 'Even out the light'.", ""))
        gl2.addWidget(self._look_row("white", "Whiten paper", 0, 100, ("off", "", "whiter"),
                                     "Light grey paper becomes white.  Raise it until the paper is clean white.", ""))
        gl2.addWidget(self._look_row("black", "Darken text", 0, 100, ("off", "", "darker"),
                                     "Makes writing and lines darker (use it after 'Whiten paper').", ""))
        self.btnShadowReset = QPushButton("Reset manual shadow")
        self.btnShadowReset.setToolTip("Sets the five sliders above back to 'off'")
        self.btnShadowReset.clicked.connect(self._reset_manual_shadow)
        gl2.addWidget(self.btnShadowReset)
        shparts["manual"] = g2
        # the three ways side by side: pick one, only that one is shown (faster than scrolling past all of them)
        shorder = []
        seg = QHBoxLayout()
        seg.setSpacing(4)
        self.shSeg = QButtonGroup(self)
        self.shSeg.setExclusive(True)
        for k, (key, text, tip) in enumerate((("one", "One click", "Remove shadow with one click"),
                                              ("manual", "Manual", "Manual shadow removal (sliders, works at once)"),
                                              ("white", "White page", "Newspaper / book photo: make the page white"))):
            b = QPushButton(text)
            b.setCheckable(True)
            b.setToolTip(tip)
            self.shSeg.addButton(b, k)
            seg.addWidget(b, 1)
            shorder.append(shparts[key])
        self.shSeg.button(0).setChecked(True)

        def sh_page(k):
            for n, w_ in enumerate(shorder):                       # hidden groups take no room: the chosen one sits right under the buttons
                w_.setVisible(n == k)
        lay.addLayout(seg)
        for w_ in shorder:
            lay.addWidget(w_)
        self.shSeg.idClicked.connect(sh_page)
        sh_page(0)
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

        # ---------------- Picture (signature, stamp ... placed on the page)
        pg, lay = self._page()
        g, gl = self._group_box("Pictures on this page")
        self.btnAddPic = QPushButton("＋  Add picture")
        self.btnAddPic.setProperty("accent", True)
        self.btnAddPic.setMinimumHeight(38)
        self.btnAddPic.setToolTip("Put another picture on this page. Its background is removed.\n"
                                  "Every picture is its own layer: select it, then use Adjust / Shadow / Text / Select / Erase / Crop on it alone.")
        gl.addWidget(self.btnAddPic)
        self.layerBox = QVBoxLayout()
        self.layerBox.setSpacing(4)
        gl.addLayout(self.layerBox)
        self.lblLayerHint = QLabel("")
        self.lblLayerHint.setObjectName("hint")
        self.lblLayerHint.setWordWrap(True)
        gl.addWidget(self.lblLayerHint)
        lay.addWidget(g)
        g, gl = self._group_box("Background of the selected picture", collapsed=True)
        self.chkBg = QCheckBox("Remove background")
        self.chkBg.setChecked(True)
        self.chkBg.toggled.connect(lambda _=False: self._bg_changed())
        gl.addWidget(self.chkBg)
        self.bgRows = {}
        for key, title, caps, tip, val in (
                ("strength", "Remove more", ("less", "", "more"),
                 "Higher = paper that is a bit grey / dirty also disappears. Lower = keeps faint strokes.", 50),
                ("fine", "Keep faint strokes", ("less", "", "more"),
                 "Higher = pale, thin strokes stay (a little more paper noise may come with them - 'Remove specks' takes it out).", 70),
                ("specks", "Remove specks", ("off", "", "more"),
                 "Takes dust, paper grain and tiny dots out of the cut picture. Strokes are never touched.", 50),
                ("soft", "Edge softness", ("sharp", "", "soft"), "How smooth the edge of the ink / stamp is.", 30),
                ("opacity", "Opacity", ("faint", "", "solid"), "How strongly the picture shows on the page.", 100)):
            row = SliderRow(title, 0, 100, val, caps, tip)
            row.changed.connect(lambda v, k=key: self._bg_changed(k))
            self.bgRows[key] = row
            gl.addWidget(row)
        self.chkUneven = QCheckBox("Uneven / grey print paper")
        self.chkUneven.setToolTip("Turn on when the paper behind the signature / stamp is not one flat colour (shading, grey print).")
        self.chkUneven.toggled.connect(lambda _=False: self._bg_changed())
        gl.addWidget(self.chkUneven)
        gl.addWidget(self._label("Ink color"))
        trow = QHBoxLayout()
        trow.setSpacing(4)
        self.tintGroup = QButtonGroup(self)
        self.tintGroup.setExclusive(True)
        for i, (text, tip) in enumerate((("Original", "Keep the colors of the picture"), ("Black", "Make it black ink"),
                                         ("Blue", "Make it blue ink (a blue stamp / pen)"))):
            b = QPushButton(text)
            b.setCheckable(True)
            b.setToolTip(tip)
            self.tintGroup.addButton(b, i)
            trow.addWidget(b)
        self.tintGroup.button(0).setChecked(True)
        self.tintGroup.idClicked.connect(lambda _=0: self._bg_changed())
        gl.addLayout(trow)
        self.lblBgNote = QLabel("Changing the background starts the picture again from the original (edits made inside "
                                "the picture are lost).")
        self.lblBgNote.setObjectName("hint")
        self.lblBgNote.setWordWrap(True)
        gl.addWidget(self.lblBgNote)
        self.bgGroup = g
        lay.addWidget(g)
        lay.addStretch(1)
        self.stackPages.addWidget(pg)

        # ---------------- Aa: text boxes (Bangla / English)
        pg, lay = self._page()
        self.textPanel = textbox.TextPanel(SliderRow, self._group_box)
        lay.addWidget(self.textPanel)
        lay.addStretch(1)
        self.stackPages.addWidget(pg)

        # ---------------- Save / share: no tab any more - the window's top bar (Save v) calls the same actions.
        # These buttons are kept (not shown) so the existing enable / disable and signals keep working.
        self.btnSave = QPushButton("Save")
        self.btnSave.setProperty("accent", True)
        self.btnSave.setMinimumHeight(38)
        self.btnSave.setToolTip("Replaces the original file with the edited picture (Ctrl+S).\nA picture from the phone / clipboard goes to the save folder.")
        self.btnSaveAs = QPushButton("Save as...  (JPG, PNG, PDF)")
        self.btnSaveAll = QPushButton("Save all edited...")
        self.btnSaveAll.setToolTip("Every edited picture: replace the originals or save into a folder you choose (Ctrl+Shift+S)")
        self.btnPrint = QPushButton("Print...")
        self.btnPrint.setToolTip("Print this picture (Ctrl+P)")
        self.btnPrint.clicked.connect(lambda: self.printRequested.emit(self))
        self.btnPdfAll = QPushButton("All pages as one PDF")
        self.btnPdfAll.setToolTip("One PDF with every open picture, one page each, in the order of the thumbnails")
        self.btnApplyAll = QPushButton("Apply last setting to all pictures")
        self.btnApplyAll.setToolTip("Copies ONLY the setting you changed last (one slider, the shadow removal, or the picked colors) "
                                   "to the other pictures. Crop, erase, move ... are never copied.")
        self.btnCopy = QPushButton("Copy picture  (Ctrl+C)")
        self.btnCopy.setToolTip("Copy this picture (with your edits) - then paste it anywhere")
        self.btnDrag = DragHandle(self)
        v.addWidget(self.stackPages, 1)
        self.tabGroup.idClicked.connect(self.stackPages.setCurrentIndex)
        self.btnShadow.clicked.connect(self._auto_shadow)
        self.btnSave.clicked.connect(lambda: self.saveRequested.emit(self))
        self.btnSaveAs.clicked.connect(lambda: self.saveAsRequested.emit(self))
        self.btnSaveAll.clicked.connect(self.saveAllRequested.emit)
        self.btnPdfAll.clicked.connect(self.pdfAllRequested.emit)
        self.btnApplyAll.clicked.connect(lambda: self.applyAllRequested.emit(self))
        self.btnAddPic.clicked.connect(lambda: self.add_picture())
        self.textPanel.newRequested.connect(self.add_text_layer)
        self.textPanel.duplicateRequested.connect(lambda: self.duplicate_layer())
        self.textPanel.changed.connect(self._text_changed)
        self.textPanel.opacityChanged.connect(self._text_opacity)
        self.btnText.clicked.connect(self.add_text_layer)
        self.btnLayerDone.clicked.connect(lambda: self.leave_layer())
        self.btnCopy.clicked.connect(self.copy_image)
        return side

    def show_tab(self, i):
        self.tabGroup.button(i).setChecked(True)
        self.stackPages.setCurrentIndex(i)

    def _paint_swatch(self):
        r, g, b = self.fill_rgb
        self.swatch.setStyleSheet("background: rgb(%d,%d,%d); border-radius: 7px; border: 1px solid #4a5268;" % (r, g, b))
        if hasattr(self, "brushSwatch"):                 # the Color Brush uses the same color
            self.brushSwatch.setStyleSheet("background: rgb(%d,%d,%d); border-radius: 5px; border: 1px solid #4a5268;" % (r, g, b))
            self.canvas.set_paint(self.fill_rgb, self.opSlider.value() / 100.0)
        if hasattr(self, "edHex") and not self.edHex.hasFocus():
            self.edHex.setText("#%02X%02X%02X" % (r, g, b))

    def _set_fill(self, rgb):
        self.fill_rgb = tuple(int(v) for v in rgb)
        self._paint_swatch()

    def _hex_fill(self):
        t = self.edHex.text().strip().lstrip("#")
        if len(t) == 6:
            try:
                self._set_fill((int(t[0:2], 16), int(t[2:4], 16), int(t[4:6], 16)))
                return
            except ValueError:
                pass
        self._paint_swatch()

    def _paper_fill(self):
        if self._loaded and self.base is not None:
            self._set_fill(core.border_color(self.base))

    def _eyedrop_fill(self):
        """'From picture': the next click in the picture gives the fill color."""
        if not self._loaded:
            return
        self._fill_drop = True
        self.choose_tool("pick")
        self.message.emit("Click a spot in the picture: its color becomes the fill color.")

    def _tol_mode(self, key):
        """The same slider is 'Fuzziness' (0..200) for Color range and 'Tolerance' for the brushes."""
        color = key == "color"
        self.tolSlider.blockSignals(True)
        if color:
            self.tolSlider.setRange(0, 200)
            self.tolSlider.setValue(self._fuzz)
            self.lblTol.setText("Fuzziness")
            self.tolSlider.setToolTip("Color range: how far from the clicked color a color may be and still be taken (like Photoshop)")
        else:
            self.tolSlider.setRange(5, 80)
            self.tolSlider.setValue(self._tolv)
            self.lblTol.setText("Tolerance")
            self.tolSlider.setToolTip("How different a color may be to still count as the same object")
        self.tolSlider.blockSignals(False)
        self.lblTolV.setText(str(self.tolSlider.value()))
        self.chkLocal.setVisible(color)
        self.localSlider.setVisible(color and self.chkLocal.isChecked())
        self.lblLocalV.setVisible(color and self.chkLocal.isChecked())
        self._update_ring()
        self.chkSnap.setVisible(key == "deselect")
        self.chkPrev.setVisible(color)
        self._sync_preview()

    def _sync_preview(self):
        """The black / white preview only exists for the Color range tool."""
        self.canvas.set_sel_preview(self._tool == "color" and self.chkPrev.isChecked())

    def _tol_changed(self, v):
        self.lblTolV.setText(str(v))
        if self._tool == "color":
            self._fuzz = v
            self._cr_timer.start()
        else:
            self._tolv = v

    def _recompute_color_range(self):
        """Fuzziness / 'near the click' changed after a click: the same selection, redone (no new undo step)."""
        on = self._tool == "color" and self.chkLocal.isChecked()
        self.localSlider.setVisible(on)
        self.lblLocalV.setVisible(on)
        self._update_ring()
        cr = self._cr
        if cr is None or not self._loaded or self.sel is not cr["sel"]:
            return                                          # the selection was changed by something else since the click
        lab = cr["lab"]
        if self.prev is None or lab.shape[:2] != self.prev.shape[:2]:
            return
        fuzz, local = self._fuzz, (self.localSlider.value() / 100.0 if self.chkLocal.isChecked() else 0.0)
        token = cr["token"] = cr.get("token", 0) + 1
        rgb, center, base, mode = cr["rgb"], cr["center"], cr["base"], cr["mode"]

        def work(progress):
            return core.color_range_fuzzy(lab, rgb, fuzz, center, local)

        def done(mask):
            if self._dead or cr.get("token") != token or self._cr is not cr or self.sel is not cr["sel"]:
                return
            self.sel = core.sel_combine(base, mask, mode)
            self._sel_changed()
            cr["sel"] = self.sel
            self._update_ring()
        run_background(work, done, lambda msg: None)

    def _update_ring(self):
        """'Near the click' on: a yellow circle around the click shows how far it reaches (moves live with the slider)."""
        cr = getattr(self, "_cr", None)
        if not hasattr(self, "chkLocal") or not hasattr(self, "canvas"):
            return
        ok = (self._tool == "color" and self.chkLocal.isChecked() and cr is not None and self.prev is not None
              and self.sel is cr["sel"])
        if not ok:
            self.canvas.set_ring(None)
            return
        H, W = self.prev.shape[:2]
        R = max(8.0, self.localSlider.value() / 100.0 * max(H, W))
        self.canvas.set_ring((cr["center"][0], cr["center"][1], R))

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
        can_cmp = self._loaded and self.is_edited()
        self.btnCompare.setVisible(can_cmp)
        if not can_cmp and self._cmp is not None:          # back to the original: nothing left to compare
            self._compare_off()
        self.btnCmpMode.setVisible(can_cmp and self._cmp is not None)
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
        for b in self._bake_btns:
            b.setVisible(self.sel is not None)
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

    def compare(self):
        """The Compare button: on = the original shows in the picture itself (slider / side by side); click again = off."""
        if self._cmp is not None:
            self._compare_off()
            return
        if not self._loaded or not self.is_edited() or self.canvas._img is None:
            self.btnCompare.setChecked(False)
            return
        self._go_move()                                    # the hand: wheel and drag move the picture while comparing

        def show(orig):
            if self._dead or self._cmp is not None:
                return
            self._cmp = CompareOverlay(self.canvas, orig)
            self.btnCompare.setChecked(True)
            self.btnCmpMode.setText("Slider")
            self.btnCmpMode.setVisible(True)
            self._notify("compare", "Compare is on", "drag the line; wheel = zoom, drag = move; click Compare again to switch it off")
        if self._orig_q is not None:
            show(self._orig_q)
            return
        path = self.src_path()

        def work(progress):
            progress(0.2, "reading the original")
            prev, _sc = core.make_preview(core.load_image(path))
            return np_to_qimage(prev)

        def done(q):
            self._orig_q = q
            show(q)

        def fail(msg):
            self.btnCompare.setChecked(False)
            self.message.emit("Could not read the original: " + msg.splitlines()[0])
        self.btnCompare.setChecked(True)
        self._job("Preparing the comparison", work, done, fail)

    def _compare_off(self):
        if self._cmp is not None:
            self._cmp.close_overlay()
            self._cmp = None
        self.btnCompare.setChecked(False)
        self.btnCmpMode.setVisible(False)

    def _cmp_mode(self):
        if self._cmp is None:
            return
        side = self._cmp.mode == "slider"
        self._cmp.set_mode("side" if side else "slider")
        self.btnCmpMode.setText("Side by side" if side else "Slider")

    def picture_tab_active(self):
        """True while the 'Picture' tab (overlay pictures: signature, stamp ...) is the open tab."""
        return self._loaded and self.stackPages.currentIndex() == self.TAB_PICTURE

    def src_path(self):
        """The picture's original file: the saved-over file is backed up when 'Save' replaces it."""
        return getattr(self, "orig_path", None) or self.path

    def _drop_temp_files(self):
        """The temporary files of this picture (edited copy for drag / copy / print, backup of a replaced original)."""
        paths = []
        if self._export is not None:
            paths.append(self._export[1])
            self._export = None
        if self.orig_path and os.path.dirname(self.orig_path) == core.ORIG_DIR:
            paths.append(self.orig_path)
        for p in paths:
            try:
                if os.path.dirname(os.path.abspath(p)) in (os.path.abspath(EXPORT_DIR), os.path.abspath(core.ORIG_DIR)):
                    os.remove(p)
            except OSError:
                pass

    def dispose(self):
        """The picture is being removed: stop timers, ignore everything that is still being computed for it."""
        self._dead = True
        self._drop_temp_files()
        if self._cmp is not None:
            self._compare_off()
        for t in ("_timer", "_focus_timer", "_gest_timer", "_bg_timer", "_shadow_timer", "_type_timer"):
            tm = getattr(self, t, None)
            if tm is not None:
                tm.stop()
        self.base = self.prev = self._lab = self._orig_q = None
        self.undo = []
        self.redo = []
        self._cache = {}

    # ------------------------------------------------------------- loading
    def load_async(self, foreground=False):
        """Open the picture in a worker thread (reading + preview), so sliding to it never freezes the window.
        foreground = the picture the user is looking at: shows the progress bar.  Otherwise it is a quiet pre-load."""
        if self._dead or self._loaded or self._failed or self._loading:
            return
        self._loading = True
        self.canvas.set_message("Opening %s ..." % self.name)
        path, adjs, look = self.path, copy.deepcopy(self.adjs), dict(self.look)

        def work(progress):
            progress(0.15, "reading")
            base = core.load_image(path)
            progress(0.7, "preparing the view")
            return base, self._build_view(base, adjs, look, None, None)

        def done(r):
            self._loading = False
            if self._dead or self._loaded or self._failed:
                return
            base, v = r
            self.base = base
            self._loaded = True
            self._enable_tools(True)
            self._adopt_view(v, True)
            self._cur_snap = self._snap()
            self._sel_label()
            if self._pending is not None:             # settings copied from another picture while this one was not open
                st, self._pending = self._pending, None
                self._apply_now(st, rebuild=True)

        def fail(msg):
            self._loading = False
            if self._dead:
                return
            self._failed = True
            self.canvas.set_message("This picture cannot be opened\n%s" % msg.splitlines()[0])
        if foreground and self.runner is not None:
            self.runner.run("Opening " + self.name, work, done, fail, light=True)
        else:
            run_background(work, done, fail)

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
    def _build_view(base, adjs, look, sel, deco=None):
        """Everything slow about showing a picture (preview, colour maths, first render). Runs in a worker thread.
        deco(out) puts the picture layers on the page (or the chess-board behind a layer)."""
        prev, scale = core.make_preview(base)
        cache = {}
        lab = core.rgb_to_lab(prev) if core.active_adjs(adjs) else None
        out = core.render(prev, adjs, look, lab=lab, cache=cache,
                          sel=sel if (sel is not None and sel.shape == prev.shape[:2]) else None)
        if deco is not None:
            out = deco(out)
        return {"prev": prev, "scale": scale, "lab": lab, "cache": cache, "out": out, "qimg": np_to_qimage(out)}

    @staticmethod
    def _deco_from(ctx, alpha, layers, hide=None, cache=None):
        if ctx is not None and alpha is not None:
            return lambda out: core.show_alpha(out, alpha)
        if layers:
            return lambda out: np.asarray(core.composite_layers_fast(Image.fromarray(out), layers, hide, cache))
        return None

    def _deco(self):
        return self._deco_from(self.ctx, self.alpha, self.layers, self._hide_layer, self._lcache)

    @staticmethod
    def _fp(v):
        """A hashable fingerprint of settings (numbers, text, lists, dicts; anything else by identity)."""
        if v is None or isinstance(v, (int, float, str, bool)):
            return v
        if isinstance(v, (list, tuple)):
            return tuple(Card._fp(x) for x in v)
        if isinstance(v, dict):
            return tuple(sorted((str(k), Card._fp(x)) for k, x in v.items()))
        return ("id", id(v))

    def _base_key(self):
        """Everything that makes the picture BEFORE the layers.  Same key = no need to redo the colour maths."""
        return (self._view_gen, id(self.prev), id(self.base), id(self.sel), self._sel_ver, self.ctx,
                self._fp(self.look), self._fp([a for a in self.adjs if core.adj_active(a)]))

    def _adopt_view(self, v, refit=True):
        self._view_gen += 1
        self.prev, self.scale = v["prev"], v["scale"]
        self.canvas.unit = 1.0 / max(self.scale, 1e-6)          # the drag HUD shows real picture pixels
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
        qimg = v.get("qimg") or np_to_qimage(v["out"])
        if refit:
            self.canvas.set_images(qimg, self.prev)
        else:
            self.canvas.set_display(qimg, self.prev)
        self.canvas.set_selection(self.sel)
        self._hl_key = None
        self._sel_label()
        self._update_buttons()

    def _rebuild_preview(self, refit=True):
        v = self._build_view(self.base, self.adjs, self.look, self.sel, self._deco())
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
        deco = self._deco()
        return deco(out) if deco is not None else out

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
        deco = self._deco()
        bkey = None if focus is not None else self._base_key()
        bc = self._base_cache
        cached = bc[2] if (bkey is not None and bc is not None and bc[0] == bkey) else None

        def work(progress):
            lab = lab0
            if cached is not None:                       # only the layers changed: the page itself is already made
                out0 = cached
            else:
                if lab is None and (core.active_adjs(adjs) or focus is not None):
                    lab = core.rgb_to_lab(prev)
                out0 = core.render(prev, adjs, look, lab=lab, cache=cache, sel=sel)
            out = deco(out0) if deco is not None else out0
            hl = None if focus is None else core.get_mask(prev, focus, lab, cache)
            return np_to_qimage(out), hl, lab, out0

        def done(res):
            self._rendering = False
            if self._dead:
                return
            if gen == self._view_gen:
                out, hl, lab, out0 = res
                if bkey is not None:
                    self._base_cache = (bkey, (prev, sel, self.base), out0)
                if self._lab is None and lab is not None:
                    self._lab = lab
                self.canvas.set_display(out)
                self._show_highlight(focus, hl)
            if self._again:
                self._again = False
                self._render_now()

        def fail(msg):
            self._rendering = False
            if self._dead:
                return
            self.message.emit("Preview problem: " + msg.splitlines()[0])
        run_background(work, done, fail)

    def render_sync(self):
        """For the self test: draw the picture now, without a worker thread."""
        self.canvas.set_display(np_to_qimage(self._compute()))

    # --------------------------------------------------------------- state
    def is_edited(self):
        return (self.ops > 0 or bool(self.layers) or self._pending is not None or core.look_edited(self.look)
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
        self._inline_flush()                                   # text typed a moment ago is in the picture
        self.ensure_loaded()
        layers = self._layers_now()
        if self.ctx is not None and self._stash is not None:          # a layer is open: the result is still the whole page
            st = self._stash
            return (st["base"], copy.deepcopy(st["adjs"]), dict(st["look"]), st["sel"], layers)
        return (self.base, copy.deepcopy(self.adjs), dict(self.look), self.sel, layers)

    def _layers_now(self):
        """The layers as they are right now (the one being edited with its unfinished settings)."""
        if self.ctx is None:
            return list(self.layers)
        pend = None
        if core.active_adjs(self.adjs) or core.look_edited(self.look):
            pend = (copy.deepcopy(self.adjs), dict(self.look), self.sel)
        return [dict(l, rgb=self.base, a=self.alpha, pend=pend) if l["id"] == self.ctx else l for l in self.layers]

    def result_image(self):
        return core.render_full(*self.render_args())

    def render_source(self):
        """A function that makes the final picture; call it in a worker thread.  A picture that was never opened is read
        there too (with the settings copied to it), so saving many pictures does not freeze the window."""
        if self._loaded or self._failed:
            args = self.render_args()
            return lambda: core.render_full(*args)
        path, st = self.src_path(), copy.deepcopy(self._pending)

        def make():
            base = core.load_image(path)
            if st is None:
                return base
            look = core.new_look(**st["look"])
            if st.get("shadow"):
                opts = dict(core.SHADOW_DEFAULTS)
                opts.update(st.get("shadow_opts") or {})
                base = core.remove_shadow(base, opts["strength"] / 100.0, None, opts)
            return core.render_full(base, copy.deepcopy(st["adjs"]), look, None, [])
        return make

    def can_replace(self):
        """Save can replace the original file: opened from the disk (not phone / clipboard) and writable."""
        return (self.origin == "file" and os.path.isfile(self.path) and os.access(self.path, os.W_OK)
                and os.access(os.path.dirname(self.path) or ".", os.W_OK))

    def out_name(self):
        stem = os.path.splitext(self.name)[0]
        return stem + "_edited", core.out_ext(self.name)

    # ------------------------------------------------- history (undo / redo)
    def _snap(self):
        return {"base": self.base, "adjs": copy.deepcopy(self.adjs), "look": dict(self.look),
                "shadow": self._shadow_done, "sel": self.sel, "ops": self.ops, "shadow_src": self.shadow_src,
                "ctx": self.ctx, "alpha": self.alpha, "stash": self._stash, "layers": list(self.layers)}

    def _push_undo(self, st):
        self.undo.append(st)
        self.redo = []
        while len(self.undo) > 80 or len({id(x) for e in self.undo for x in [e["base"], e.get("shadow_src")] + [l["rgb"] for l in e.get("layers", [])]
                                      if x is not None}) > 12:
            self.undo.pop(0)
        self._update_buttons()

    def _checkpoint(self, label=None):
        """Call BEFORE changing anything that must be one undo step (a selection, an erase, a crop ...)."""
        if self._gest:
            self._gest_timer.stop()
            self._gest = False
        st = self._snap()
        if label:
            st["label"] = label                      # the toast of Undo / Redo says what is undone
        self._push_undo(st)

    def _begin_change(self):
        """Slider / colour row moved: the first move of a drag stores the 'before' state, the rest is one step."""
        if not self._gest:
            self._gest = True
            st = dict(self._cur_snap if self._cur_snap is not None else self._snap())
            st["label"] = "Adjustment"
            self._push_undo(st)
        self._gest_timer.start(700)

    def _end_gesture(self):
        self._gest = False
        self._cur_snap = self._snap()

    def _sync_sliders(self):
        for k, row in self.lookRows.items():
            row.set_value(self.look[k])
        for k, row in self.shadowRows.items():
            row.set_value(self.shadow_opts[k])
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
        self.ctx, self.alpha, self._stash = st.get("ctx"), st.get("alpha"), st.get("stash")
        self.layers = list(st.get("layers", []))
        if self.sel_layer is not None and not any(l["id"] == self.sel_layer for l in self.layers):
            self.sel_layer = None
        self._sel_ver += 1
        self.colors.rebuild(self.adjs)
        self._sync_sliders()
        self._refresh_layers_ui()

    def _goto(self, st):
        """Show a stored state (undo / redo)."""
        if self._tool in CROP_KEYS or self._tool == "transform":
            self._go_move()
        self._clear_clone()
        if st["base"] is self.base and st.get("ctx") == self.ctx and st.get("alpha") is self.alpha:   # same picture: only values / selection changed
            self._set_fields(st)
            self.canvas.set_selection(self.sel)
            self._hl_key = None
            self._sel_label()
            self._timer.start()
            self._touch()
            return

        def work(progress):
            return self._build_view(st["base"], st["adjs"], core.new_look(**st["look"]), st.get("sel"),
                                    self._deco_from(st.get("ctx"), st.get("alpha"), st.get("layers", [])))

        def done(v):
            self._set_fields(st)
            self._adopt_view(v, True)
            self._touch()
        self._job("Undo / redo", work, done)

    def undo_step(self):
        self._inline_end()
        if not self._loaded:
            return
        if self._tool == "transform" and self.canvas.in_transform():       # a transform is running: undo goes back inside it
            if not self.canvas.tf_undo():
                self.cancel_pending()                                        # nothing left to step back: untransform
                self.message.emit("Transform cancelled: everything is as it was before.")
            else:
                mode = self.canvas.tf_mode_name()
                if mode in self.tfBtns:
                    self.tfBtns[mode].setChecked(True)
            return
        if self._gest:
            self._gest_timer.stop()
            self._end_gesture()
        fx = self.canvas.fx
        if not self.undo:
            fx.info("undo", "Nothing to undo", "this is how it was opened")
            return
        st = self.undo.pop()
        lab = st.get("label") or "last change"
        snap = self._snap()
        snap["label"] = lab
        self.redo.append(snap)
        self._goto(st)
        fx.info("undo", "Undo", lab + (", %d more to undo" % len(self.undo) if self.undo else ", back to the start"))
        fx.pulse("undo")

    def redo_step(self):
        self._inline_end()
        if not self._loaded:
            return
        if self._gest:
            self._gest_timer.stop()
            self._end_gesture()
        fx = self.canvas.fx
        if not self.redo:
            fx.info("redo", "Nothing to redo")
            return
        st = self.redo.pop()
        lab = st.get("label") or "change"
        snap = self._snap()
        snap["label"] = lab
        self.undo.append(snap)
        self._goto(st)
        fx.info("redo", "Redo", lab + (", %d more to redo" % len(self.redo) if self.redo else ""))
        fx.pulse("redo")

    def _edit_base(self, label, make, keep_sel=True, refit=False, after=None, msg=None, shadow_src=None, merge=False,
                   checkpoint=True, fail=None, alpha_make=None):
        """Change the picture itself (erase, crop, rotate, move ...) in a worker thread, then show it.
        make(progress) -> new PIL image, or (new image, new selection)."""
        hint, self._fx_hint = (getattr(self, "_fx_hint", None) or {}), None
        fx = self.canvas.fx
        kind, title = action_for(label)
        quiet = merge                                  # a live slider drag: no toast for every little step
        if not quiet:
            fx.busy(kind, label + "\u2026")
        adjs = copy.deepcopy(self.adjs)
        look, sel_in = dict(self.look), self.sel
        ctx_in, alpha_in, layers_in, hide_in = self.ctx, self.alpha, self.layers, self._hide_layer

        def work(progress):
            res = make(progress)
            if isinstance(res, tuple):
                new, sel_out = res
            else:
                new, sel_out = res, (sel_in if keep_sel else None)
            na = alpha_make(progress) if (alpha_make is not None and ctx_in is not None) else alpha_in
            progress(0.9, "Showing")
            return new, sel_out, self._build_view(new, adjs, look, sel_out, self._deco_from(ctx_in, na, layers_in, hide_in)), na

        def done(r):
            new, sel_out, v, na = r
            if ctx_in is not None and self.ctx == ctx_in:
                self.alpha = na
            if not checkpoint:
                self.ops += 1
            elif merge and self._gest:               # a live shadow slider drag is ONE undo step
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
            if checkpoint and self.undo:
                self.undo[-1].setdefault("label", hint.get("title") or title)
            if not quiet:
                detail = hint.get("detail")
                if callable(detail):
                    detail = detail()
                fx.done(kind, hint.get("title") or title, detail or "")
                fx.pulse(kind, hint.get("rect"), hint.get("effect", "pulse"))
            if msg:
                self.message.emit(msg)

        def on_fail(m):
            fx.fail("Could not finish", (m or "").splitlines()[0] if m else "")
            if fail is not None:
                fail(m)
        self._job(label, work, done, on_fail)

    # ------------------------------------------------ picture layers (signature, stamp ...)
    BG_DEFAULT = {"strength": 50, "soft": 30, "uneven": False, "tint": "orig", "opacity": 100, "remove": True,
                  "fine": 70, "specks": 50}

    def _layer(self, lid):
        for l in self.layers:
            if l["id"] == lid:
                return l
        return None

    def _replace_layer(self, lid, **kw):
        self.layers = [dict(l, **kw) if l["id"] == lid else l for l in self.layers]

    # ------------------------------------------------ text boxes (Bangla / English)
    def _text_layer(self):
        """The selected text box layer, or None."""
        l = None if self.sel_layer is None else self._layer(self.sel_layer)
        return l if (l is not None and l.get("text") is not None and self.ctx is None) else None

    def _show_text_tab_if_text(self, lid):
        l = self._layer(lid)
        if l is not None and l.get("text") is not None:
            self.show_tab(self.TAB_TEXTBOX)

    def add_text_layer(self):
        """A new text box in the middle of the page: it is a picture layer, so it moves / resizes / rotates like one."""
        self.ensure_loaded()
        if not self._loaded:
            return
        if self.ctx is not None:
            self.leave_layer(self.add_text_layer)
            return
        self._layer_seq += 1
        nums = [int(l["name"][9:]) for l in self.layers
                if l.get("text") is not None and l["name"].startswith("Text box ") and l["name"][9:].isdigit()]
        layer = textbox.new_layer(self._layer_seq, textbox.new_style(), self.base.size,
                                  name="Text box %d" % (max(nums, default=0) + 1))
        self._checkpoint("Text box")
        self.layers = self.layers + [layer]
        self.ops += 1
        self.sel_layer = layer["id"]
        self._touch()
        self.show_tab(self.TAB_TEXTBOX)
        self.place_layer_tool(layer["id"])
        self._inline_start(layer["id"])
        self._notify("layer", "Text box added", "type on the page, click outside when done.  Click it again any time: drag = move, Alt + drag = copy, double click = edit")

    def _put_text_layer(self, nl):
        lid = nl["id"]
        self.layers = [nl if x["id"] == lid else x for x in self.layers]
        self.ops += 1
        self._touch()
        if self._tool == "transform" and self._place_id == lid:     # the box on the page follows the typing
            PH, PW = self.prev.shape[:2]
            img = self._layer_qimage(nl)
            if self._inline_lid == lid:
                img.fill(Qt.GlobalColor.transparent)               # the editor on the page shows the text meanwhile
            self.canvas.update_layer_transform(img, [(x * PW, y * PH) for x, y in nl["grid"]], nl["n"])
            self._inline_place()
        else:
            self._timer.start()
        self._refresh_layers_ui()

    def _text_changed(self, patch):
        if self._text_pending and "text" not in patch:
            self._inline_flush()                                     # typing that is still waiting: make it first
        l = self._text_layer()
        if l is None:
            return
        fw = QApplication.focusWidget()
        if "text" not in patch and self._inline_lid == l["id"]:
            # A style (color, size, font, shadow ...) was changed in the side panel while the editor on the page is open:
            # the editor can only show plain text, so close it and show the real picture of the text at once.
            self._inline_end()
            if fw is not None:
                fw.setFocus()
        if not (self._tool == "transform" and self._place_id == l["id"]):
            self.place_layer_tool(l["id"])                           # show the box again (it was put down)
            if fw is not None:
                fw.setFocus()                                        # ... but the typing stays where it was
            l = self._layer(l["id"])
        self._begin_change()                                         # a burst of typing / slider moves = one undo step
        nl = textbox.restyle(l, patch, self.base.size)
        if self._inline_lid == nl["id"] and self._inline.toPlainText() != nl["text"]["text"]:
            self._inline.blockSignals(True)                          # text typed in the side box: show it on the page too
            self._inline.setPlainText(nl["text"]["text"])
            self._inline.blockSignals(False)
        self._put_text_layer(nl)
        textbox.remember_style(nl["text"])

    def _text_opacity(self, v):
        l = self._text_layer()
        if l is None:
            return
        self._begin_change()
        self._replace_layer(l["id"], opacity=int(v))
        self.ops += 1
        self._touch()
        if self._tool == "transform" and self._place_id == l["id"]:
            self.canvas.set_tf_image(self._layer_qimage(self._layer(l["id"])))
        else:
            self._timer.start()

    def duplicate_layer(self, lid=None, offset=0.03):
        """A copy of a picture / text box, a little to the side; the copy is selected."""
        if not self._loaded:
            return
        lid = self.sel_layer if lid is None else lid
        if self.ctx is not None:
            self.leave_layer(lambda: self.duplicate_layer(lid, offset))
            return
        if self._tool == "transform" and self._place_id is not None:
            self._commit_layer_pts()
        l = None if lid is None else self._layer(lid)
        if l is None:
            return
        self._layer_seq += 1
        nl = textbox.duplicate(l, self._layer_seq, offset)
        self._checkpoint("Copy")
        i = next(k for k, x in enumerate(self.layers) if x["id"] == lid)
        self.layers = self.layers[:i + 1] + [nl] + self.layers[i + 1:]
        self.ops += 1
        self.sel_layer = nl["id"]
        self._touch()
        self.place_layer_tool(nl["id"])
        self._notify("copy", "Copy made", "drag it where you want it")

    def _copy_layer_drag(self):
        """Alt + drag on a picture / text box: a copy stays where it is, the original is dragged away."""
        lid = self._place_id
        l = None if lid is None else self._layer(lid)
        if l is None or self.ctx is not None:
            return
        self._layer_seq += 1
        nl = textbox.duplicate(l, self._layer_seq, 0.0)
        self._checkpoint("Copy")
        i = next(k for k, x in enumerate(self.layers) if x["id"] == lid)
        self.layers = self.layers[:i] + [nl] + self.layers[i:]
        self.ops += 1
        self._touch()
        self._timer.start()
        self._refresh_layers_ui()

    def _layer_double(self):
        lid = self._place_id
        if lid is not None:
            self.edit_layer(lid)

    # typing right on the page (like a Word text box)
    _inline_lid = None
    _inline = None

    def _inline_start(self, lid):
        l = self._layer(lid)
        if l is None or l.get("text") is None or self.ctx is not None:
            return
        if not (self._tool == "transform" and self._place_id == lid):
            self.place_layer_tool(lid)
        if self._inline is None:
            self._inline = textbox.InlineEdit(self.canvas)
            self._inline.textChanged.connect(self._inline_typed)
            self._inline.finished.connect(self._end_layer_session)
        self._inline_lid = lid
        ed = self._inline
        ed.blockSignals(True)
        ed.setPlainText(l["text"]["text"])
        ed.blockSignals(False)
        img = self._layer_qimage(l)
        img.fill(Qt.GlobalColor.transparent)
        PH, PW = self.prev.shape[:2]
        self.canvas.update_layer_transform(img, [(x * PW, y * PH) for x, y in l["grid"]], l["n"])
        self._inline_place()
        ed.show()
        ed.raise_()
        ed.setFocus()
        ed.selectAll()

    def _inline_place(self):
        """Put the editor exactly over the text box (same size, font and color as the text on the page)."""
        lid, ed = self._inline_lid, self._inline
        l = None if lid is None else self._layer(lid)
        if l is None or ed is None or self.ctx is not None:
            return
        PH, PW = self.prev.shape[:2]
        p = [self.canvas.to_wid(QPointF(x * PW, y * PH)) for x, y in l["grid"][:4]]    # TL TR BL BR
        w = math.hypot(p[1].x() - p[0].x(), p[1].y() - p[0].y())
        h = math.hypot(p[2].x() - p[0].x(), p[2].y() - p[0].y())
        cx, cy = sum(q.x() for q in p) / 4.0, sum(q.y() for q in p) / 4.0      # (a turned box is edited upright)
        lw = max(l["tl"][0], 1e-6)
        ed.look(l["text"], w / lw)
        ed.setGeometry(int(cx - w / 2), int(cy - h / 2), max(24, int(w) + 2), max(24, int(h) + 2))

    _text_pending = False

    def _inline_typed(self):
        """A key was typed on the page.  The picture of the text is heavy to make (font shaping, 2x bitmap, effects), so it
        is made once, a moment after the last key - not for every key (that made the program hang while typing)."""
        if self._inline_lid is None or self._inline is None:
            return
        self._text_pending = True
        self._type_timer.start()

    def _inline_flush(self):
        """Make the picture of the typed text now, if some typing is waiting for it."""
        self._type_timer.stop()
        if not self._text_pending:
            return
        self._text_pending = False
        l = self._layer(self._inline_lid) if self._inline_lid is not None and self._inline is not None else None
        if l is not None:
            self._text_changed({"text": self._inline.toPlainText()})

    def _inline_end(self):
        lid, ed = self._inline_lid, self._inline
        if lid is None:
            return
        self._inline_flush()                                              # the last keys are not lost
        self._inline_lid = None
        if ed is not None:
            ed.hide()
        l = self._layer(lid)
        if l is not None and self._tool == "transform" and self._place_id == lid:
            self.canvas.set_tf_image(self._layer_qimage(l))                # the finished picture of the text
            self.canvas.setFocus()

    def add_picture(self, path=None):
        """A picture (signature, stamp ...) becomes a new layer: background removed, then placed on the page."""
        self.ensure_loaded()
        if not self._loaded:
            return
        if self.ctx is not None:
            self.leave_layer(lambda: self.add_picture(path))
            return
        if path is None:
            path, _ = QFileDialog.getOpenFileName(self, "Add picture (signature, stamp ...)", "",
                                                  "Pictures (*.png *.jpg *.jpeg *.bmp *.webp *.tif *.tiff)")
            if not path:
                return
        bg = dict(self.BG_DEFAULT)

        def work(progress):
            im = ImageOps.exif_transpose(Image.open(path))
            rgba = im.convert("RGBA")
            a = rgba.split()[3]
            sa = a if a.getextrema()[0] < 250 else None          # a PNG that is already transparent keeps its holes
            rgb = rgba.convert("RGB")
            if max(rgb.size) > 4800:                                # only a really huge picture is made smaller
                f = 4800.0 / max(rgb.size)
                size = (max(1, int(rgb.width * f)), max(1, int(rgb.height * f)))
                rgb = rgb.resize(size, Image.Resampling.LANCZOS)
                sa = sa.resize(size, Image.Resampling.LANCZOS) if sa is not None else None
            cut = core.remove_background(rgb, sa, **bg)
            box = core.content_box(cut.split()[3])
            if box is not None:                                    # trim the empty margins
                rgb = rgb.crop(box)
                sa = sa.crop(box) if sa is not None else None
                cut = cut.crop(box)
            return rgb, sa, cut

        def done(r):
            rgb, sa, cut = r
            self._layer_seq += 1
            W, H = self.base.size
            wn = 0.35
            hn = wn * (rgb.height / rgb.width) * (W / H)
            if hn > 0.6:
                wn, hn = wn * 0.6 / hn, 0.6
            x0, y0 = 0.5 - wn / 2, 0.5 - hn / 2
            layer = {"id": self._layer_seq, "name": os.path.basename(path) or "Picture %d" % self._layer_seq,
                     "rgb": cut.convert("RGB"), "a": cut.split()[3], "src": rgb, "sa": sa, "bg": bg,
                     "grid": [(x0, y0), (x0 + wn, y0), (x0, y0 + hn), (x0 + wn, y0 + hn)], "n": 1,
                     "opacity": 100, "visible": True}
            self._checkpoint()
            self.layers = self.layers + [layer]
            self.ops += 1
            self.sel_layer = layer["id"]
            self._touch()
            self.show_tab(self.TAB_PICTURE)
            self._refresh_layers_ui()
            self.place_layer_tool(layer["id"])
            self.message.emit("Picture added. Drag it, corner handles resize, the round handle rotates. "
                              "Click outside it when done; click it again any time to change it.")
            self.canvas.fx.done("layer", "Picture added", "drag to move, corners resize, round handle rotates")
        self._job("Adding picture", work, done)

    def select_layer(self, lid):
        self.sel_layer = lid
        self._refresh_layers_ui()

    def delete_layer(self, lid):
        self._inline_end()
        if self._layer(lid) is None:
            return
        if self.ctx == lid:
            self.leave_layer(lambda: self.delete_layer(lid))
            return
        if self._tool == "transform" and self._place_id == lid:
            self._place_id = None
            self._hide_layer = None
            self._go_move()
        self._checkpoint()
        self.layers = [l for l in self.layers if l["id"] != lid]
        if self.sel_layer == lid:
            self.sel_layer = None
        self.ops += 1
        self._touch()
        self._timer.start()
        self._refresh_layers_ui()
        self.canvas.fx.done("delete", "Picture removed", "Ctrl+Z brings it back")

    def move_layer_order(self, lid, d):
        i = next((k for k, l in enumerate(self.layers) if l["id"] == lid), None)
        j = None if i is None else i + d
        if i is None or not (0 <= j < len(self.layers)):
            return
        self._checkpoint()
        ls = list(self.layers)
        ls[i], ls[j] = ls[j], ls[i]
        self.layers = ls
        self._touch()
        self._timer.start()
        self._refresh_layers_ui()

    def move_layer_to(self, lid, front):
        """Bring a layer to the very top (front=True) or send it to the bottom, just above the main picture."""
        i = next((k for k, l in enumerate(self.layers) if l["id"] == lid), None)
        if i is None or (front and i == len(self.layers) - 1) or (not front and i == 0):
            return
        self._checkpoint()
        ls = list(self.layers)
        l = ls.pop(i)
        ls = ls + [l] if front else [l] + ls
        self.layers = ls
        self._touch()
        self._timer.start()
        self._refresh_layers_ui()

    def order_selected(self, d, extreme=False):
        """Keyboard: move the selected layer up (d > 0) / down (d < 0); extreme = all the way."""
        lid = self.sel_layer
        if lid is None or self._layer(lid) is None or self.ctx is not None:
            return
        if extreme:
            self.move_layer_to(lid, d > 0)
        else:
            self.move_layer_order(lid, 1 if d > 0 else -1)

    def toggle_layer(self, lid, visible):
        self._checkpoint()
        self._replace_layer(lid, visible=bool(visible))
        self._touch()
        self._timer.start()

    def place_layer_tool(self, lid):
        """Move / resize / rotate / bend one picture on the page."""
        l = self._layer(lid)
        if l is None or not self._loaded:
            return
        if self.ctx is not None:
            self.leave_layer(lambda: self.place_layer_tool(lid))
            return
        if self._tool == "transform":
            if self._place_id is not None:
                self._commit_layer_pts()
            self._place_id = None
            self._hide_layer = None
            self._go_move()
        PH, PW = self.prev.shape[:2]
        n = l["n"]
        pts = [(x * PW, y * PH) for x, y in l["grid"]]
        qimg = self._layer_qimage(l)
        self.sel_layer = lid
        self._hide_layer = lid
        self._timer.start()
        self._set_tool("transform")
        self._place_id = lid                           # (_set_tool clears it, so it is set after)
        self.canvas.start_layer_transform(qimg, pts, n)
        self.tfBtns["warp" if n > 1 else "free"].setChecked(True)
        self._refresh_layers_ui()

    def _commit_layer_pts(self):
        """The picture was moved / resized / rotated with the mouse: it is kept at once (one undo step)."""
        lid, p = self._place_id, self.canvas.transform_params()
        if lid is None or p is None or self._layer(lid) is None:
            return
        pts, n = p
        PH, PW = self.prev.shape[:2]
        grid = [(x / PW, y / PH) for x, y in pts]
        l = self._layer(lid)
        if l["n"] == n and all(abs(a[0] - b[0]) + abs(a[1] - b[1]) < 1e-6 for a, b in zip(grid, l["grid"])):
            return
        self._checkpoint()
        self._replace_layer(lid, grid=grid, n=n)
        self.ops += 1
        self._touch()
        l2 = self._layer(lid)
        if l2.get("text") is not None and self.ctx is None:          # a text box: the font size follows the new size
            l3 = textbox.refit(l2, self.base.size)
            if l3 is not l2:
                self.layers = [l3 if x["id"] == lid else x for x in self.layers]
                self.canvas.set_tf_image(self._layer_qimage(l3))
                textbox.remember_style(l3["text"])
                self._refresh_layers_ui()

    def _end_layer_session(self):
        self._inline_end()
        self._commit_layer_pts()
        self._place_id = None
        self._hide_layer = None
        self._go_move()
        self._timer.start()

    def _layer_at(self, x, y, skip=None):
        """The picture layer on top at this preview position (or None)."""
        PH, PW = self.prev.shape[:2]
        for l in reversed(self.layers):
            if not l.get("visible", True) or l["id"] == skip:
                continue
            g, n = l["grid"], l["n"]
            quad = [g[0], g[n], g[-1], g[n * (n + 1)]]
            inside = False
            j = len(quad) - 1
            for i in range(len(quad)):
                xi, yi = quad[i][0] * PW, quad[i][1] * PH
                xj, yj = quad[j][0] * PW, quad[j][1] * PH
                if (yi > y) != (yj > y) and x < (xj - xi) * (y - yi) / ((yj - yi) or 1e-9) + xi:
                    inside = not inside
                j = i
            if inside:
                return l["id"]
        return None

    def _on_view_click(self, x, y):
        if self.ctx is not None or not self.layers or self._tool != "view":
            return
        lid = self._layer_at(x, y)
        if lid is not None:
            self.place_layer_tool(lid)
            self._show_text_tab_if_text(lid)

    def _layer_probe(self, x, y, ctrl=False):
        """Canvas asks on every press: is there a picture / text box here that should be picked up?
        Hand and Move tools: always.  Every other tool: only with Ctrl held (so selecting / erasing still works)."""
        if self.ctx is not None or not self.layers or not self._loaded or self._tool == "transform":
            return None
        if not (ctrl or self._tool in ("view", "move", "copy")):
            return None
        lid = self._layer_at(x, y)
        if lid is None:
            return None
        if self._tool in ("move", "copy") and not ctrl and self.sel is not None:      # moving selected page pixels wins there
            H, W = self.sel.shape[:2]
            PH, PW = self.prev.shape[:2]
            ix, iy = int(x * W / max(PW, 1)), int(y * H / max(PH, 1))
            if 0 <= ix < W and 0 <= iy < H and self.sel[iy, ix] > 0.5:
                return None
        return lid

    def _on_layer_pressed(self, lid, x, y, alt):
        self.place_layer_tool(lid)
        self._show_text_tab_if_text(lid)
        self.canvas.begin_layer_drag(x, y, alt)                 # drag starts right away: click = select, drag = move, Alt + drag = copy

    def _on_layer_outside(self, x, y):
        lid = self._layer_at(x, y, skip=self._place_id)
        if lid is not None:
            self.place_layer_tool(lid)                 # another picture: select that one
            self._show_text_tab_if_text(lid)
        else:
            self._end_layer_session()                  # empty place: the picture is deselected

    def _layer_qimage(self, l):
        rgba = core.layer_rgba(l, pend=False)
        f = min(1.0, 900.0 / max(rgba.size))
        if f < 1.0:
            rgba = rgba.resize((max(1, int(rgba.width * f)), max(1, int(rgba.height * f))), Image.Resampling.LANCZOS)
        arr = np.ascontiguousarray(np.asarray(rgba))
        return QImage(arr.data, arr.shape[1], arr.shape[0], arr.shape[1] * 4, QImage.Format.Format_RGBA8888).copy()

    def rotate_key(self, deg_ccw):
        """R: the selected picture turns 90° (right), with nothing selected the whole page does."""
        if self._tool == "transform" and self._place_id is not None:
            self.canvas.tf_rotate(-deg_ccw)
            self._commit_layer_pts()
        else:
            self.rotate(deg_ccw)

    def edit_layer(self, lid, then=None):
        """Work on ONE picture alone: Adjust / Shadow / Text / Select / Erase / Crop then change only this picture."""
        l = self._layer(lid)
        if l is None or not self._loaded:
            return
        if l.get("text") is not None:                  # a text box is changed in the Aa tab, not pixel by pixel
            self.place_layer_tool(lid)
            self.show_tab(self.TAB_TEXTBOX)
            self._inline_start(lid)
            return
        if self.ctx == lid:
            if then:
                then()
            return
        if self.ctx is not None:
            self.leave_layer(lambda: self.edit_layer(lid, then))
            return
        if self._tool in CROP_KEYS or self._tool == "transform":
            self._place_id = None
            self._hide_layer = None
            self._go_move()
        self._clear_clone()
        self._checkpoint()
        self._stash = {"base": self.base, "adjs": self.adjs, "look": dict(self.look), "sel": self.sel,
                       "shadow_src": self.shadow_src, "shadow": self._shadow_done}
        self.ctx, self.alpha, self.sel_layer = lid, l["a"], lid
        self.base, self.adjs, self.look, self.sel = l["rgb"], [], core.new_look(), None
        self.shadow_src, self._shadow_done = None, False
        self._view_gen += 1
        self._timer.stop()
        self.colors.rebuild(self.adjs)
        self._sync_sliders()
        self._sel_ver += 1
        deco = self._deco()
        base = self.base

        def work(progress):
            return self._build_view(base, [], core.new_look(), None, deco)

        def done(v):
            self._adopt_view(v, True)
            self._touch()
            self._refresh_layers_ui()
            if then:
                then()
            else:
                self._notify("layer", "Editing this picture alone", "all tools work on it only; click 'Back to the page' when done")
        self._job("Opening picture", work, done)

    def leave_layer(self, then=None):
        if self.ctx is None:
            if then:
                then()
            return
        lid, st = self.ctx, self._stash
        adjs, look, sel, base, alpha = copy.deepcopy(self.adjs), dict(self.look), self.sel, self.base, self.alpha
        layers0 = list(self.layers)
        self._checkpoint()
        pending = core.active_adjs(adjs) or core.look_edited(look)

        def work(progress):
            rgb = core.render_full(base, adjs, look, sel) if pending else base
            layers = [dict(l, rgb=rgb, a=alpha, pend=None) if l["id"] == lid else l for l in layers0]
            v = self._build_view(st["base"], st["adjs"], core.new_look(**st["look"]), st["sel"],
                                 self._deco_from(None, None, layers, None))
            return layers, v

        def done(r):
            layers, v = r
            self.layers = layers
            self.base, self.adjs, self.look, self.sel = st["base"], copy.deepcopy(st["adjs"]), core.new_look(**st["look"]), st["sel"]
            self.shadow_src, self._shadow_done = st["shadow_src"], st["shadow"]
            self.ctx = self.alpha = self._stash = None
            self._go_move()
            self._clear_clone()
            self.colors.rebuild(self.adjs)
            self._sync_sliders()
            self._sel_ver += 1
            self._adopt_view(v, True)
            self._touch()
            self._refresh_layers_ui()
            if then:
                then()
        self._job("Back to the page", work, done)

    # --- background of the selected picture
    def _bg_changed(self, key=None):
        lid = self.sel_layer
        l = None if lid is None else self._layer(lid)
        if l is None or self.ctx is not None or l.get("text") is not None:
            return
        self._begin_change()
        if key == "opacity":                       # no new cut needed
            self._replace_layer(lid, opacity=int(self.bgRows["opacity"].sl.value()))
            self._timer.start()
            self._touch()
            if self._place_id == lid:
                self.canvas.set_tf_image(self._layer_qimage(self._layer(lid)))
            return
        self._bg_timer.start()

    def _bg_params(self):
        tint = ("orig", "black", "blue")[max(0, self.tintGroup.checkedId())]
        return {"strength": int(self.bgRows["strength"].sl.value()), "soft": int(self.bgRows["soft"].sl.value()),
                "fine": int(self.bgRows["fine"].sl.value()), "specks": int(self.bgRows["specks"].sl.value()),
                "uneven": self.chkUneven.isChecked(), "tint": tint, "opacity": 100, "remove": self.chkBg.isChecked()}

    def _bg_apply(self):
        lid = self.sel_layer
        l = None if lid is None else self._layer(lid)
        if l is None or self.ctx is not None or l.get("text") is not None:
            return
        if self._bg_busy:
            self._bg_again = True
            return
        self._bg_busy = True
        bg = self._bg_params()
        src, sa = l["src"], l["sa"]

        def work(progress):
            return core.remove_background(src, sa, **bg)

        def done(cut):
            self._bg_busy = False
            if self._layer(lid) is not None and self.ctx is None:
                self._replace_layer(lid, rgb=cut.convert("RGB"), a=cut.split()[3], bg=bg)
                self._touch()
                self._timer.start()
                if self._place_id == lid:
                    self.canvas.set_tf_image(self._layer_qimage(self._layer(lid)))
            if self._bg_again:
                self._bg_again = False
                self._bg_timer.start()

        def fail(msg):
            self._bg_busy = False
            self.message.emit("Background problem: " + msg.splitlines()[0])
        run_background(work, done, fail)

    def _refresh_layers_ui(self):
        if not hasattr(self, "layerBox"):
            return
        while self.layerBox.count():
            it = self.layerBox.takeAt(0)
            if it.widget() is not None:
                it.widget().deleteLater()
        n_all = len(self.layers)
        if n_all:
            top = QLabel("\u25b2  TOP  (drawn over everything below)")
            top.setObjectName("muted")
            self.layerBox.addWidget(top)
        for pos, l in enumerate(reversed(self.layers)):  # top layer first
            idx = n_all - 1 - pos                        # 0 = lowest overlay (just above the main picture)
            is_text = l.get("text") is not None
            row = QFrame()
            row.setObjectName("layerRow")
            v = QVBoxLayout(row)
            v.setContentsMargins(0, 0, 0, 2)
            v.setSpacing(3)
            h1 = QHBoxLayout()
            h1.setSpacing(6)
            eye = QCheckBox()
            eye.setChecked(l.get("visible", True))
            eye.setToolTip("Show / hide this layer")
            eye.toggled.connect(lambda on, i=l["id"]: self.toggle_layer(i, on))
            nm = l["name"] if len(l["name"]) <= 24 else l["name"][:21] + "..."
            name = QPushButton(nm)
            name.setCheckable(True)
            name.setChecked(self.sel_layer == l["id"])
            name.setToolTip(l["name"] + "\nClick = select this layer (a click on it on the page works too)")
            name.setMinimumWidth(0)
            name.clicked.connect(lambda _=False, i=l["id"]: self.place_layer_tool(i))
            h1.addWidget(eye)
            h1.addWidget(name, 1)
            h2 = QHBoxLayout()
            h2.setSpacing(4)
            ed = QPushButton("Aa  Text" if is_text else "Edit")
            ed.setToolTip("Change the text, font, size, color ...  (double click on it works too)" if is_text else
                          "Work on this picture alone: Adjust, Shadow, Text, Select, Erase, Crop  (double click on it works too)")
            ed.setProperty("accent", self.ctx == l["id"])
            ed.clicked.connect(lambda _=False, i=l["id"]: self.edit_layer(i))
            h2.addWidget(ed, 1)
            for text, tip, fn in (("\u29c9", "Duplicate (copy)  -  Alt + drag on the page copies too", lambda i=l["id"]: self.duplicate_layer(i)),
                                  ("\u2715", "Remove this layer  (Delete key)", lambda i=l["id"]: self.delete_layer(i))):
                b_ = QPushButton(text)
                b_.setFixedWidth(30)
                b_.setToolTip(tip)
                b_.clicked.connect(lambda _=False, f=fn: f())
                h2.addWidget(b_)
            h3 = QHBoxLayout()                           # order: plain words, no guessing what the arrows mean
            h3.setSpacing(4)
            for text, tip, fn, off in (("\u25b2 Up", "Move this layer one step UP (over the layer above it)   Ctrl+Alt+Up",
                                        lambda i=l["id"]: self.move_layer_order(i, 1), idx >= n_all - 1),
                                       ("\u25bc Down", "Move this layer one step DOWN (under the layer below it)   Ctrl+Alt+Down",
                                        lambda i=l["id"]: self.move_layer_order(i, -1), idx <= 0),
                                       ("\u2912 Front", "Bring to the very TOP   Ctrl+Alt+Shift+Up",
                                        lambda i=l["id"]: self.move_layer_to(i, True), idx >= n_all - 1),
                                       ("\u2913 Back", "Send to the very BOTTOM (just above the main picture)   Ctrl+Alt+Shift+Down",
                                        lambda i=l["id"]: self.move_layer_to(i, False), idx <= 0)):
                b_ = QPushButton(text)
                b_.setToolTip(tip)
                b_.setEnabled(not off)
                b_.setMinimumWidth(0)
                b_.clicked.connect(lambda _=False, f=fn: f())
                h3.addWidget(b_, 1)
            v.addLayout(h1)
            v.addLayout(h2)
            v.addLayout(h3)
            self.layerBox.addWidget(row)
        if n_all:
            base_lbl = QLabel("\u25bc  Main picture  (always at the bottom)")
            base_lbl.setObjectName("muted")
            self.layerBox.addWidget(base_lbl)
        if not self.layers:
            self.lblLayerHint.setText("No pictures yet. Add a signature or a stamp: its background is removed and you can "
                                      "place it anywhere on the page.")
        elif self.ctx is not None:
            self.lblLayerHint.setText("Editing one picture alone. Everything you do now changes only this picture.")
        else:
            self.lblLayerHint.setText("Click a picture on the page to select it: drag = move, handles = resize / rotate / bend, "
                                      "R = turn 90°, Delete = remove, click outside = done.  Edit = work on it alone "
                                      "(Adjust, Shadow, Text, Select, Erase, Crop).")
        self.btnLayerDone.setVisible(self.ctx is not None)
        l = None if self.sel_layer is None else self._layer(self.sel_layer)
        self.bgGroup.setEnabled(self.ctx is None and l is not None and l.get("text") is None)
        tp = getattr(self, "textPanel", None)
        if tp is not None:
            tp.load(l["text"] if (l is not None and self.ctx is None and l.get("text") is not None) else None,
                    0 if l is None else l.get("opacity", 100))
        if l is not None and self.ctx is None and l.get("text") is None:
            bg = l.get("bg", self.BG_DEFAULT)
            for k, dflt in (("strength", 50), ("soft", 30), ("fine", 70), ("specks", 50)):
                self.bgRows[k].sl.blockSignals(True)
                self.bgRows[k].sl.setValue(int(bg.get(k, dflt)))
                self.bgRows[k].val.setText("%d%%" % int(bg.get(k, dflt)))
                self.bgRows[k].sl.blockSignals(False)
            self.bgRows["opacity"].sl.blockSignals(True)
            self.bgRows["opacity"].sl.setValue(int(l.get("opacity", 100)))
            self.bgRows["opacity"].val.setText("%d%%" % int(l.get("opacity", 100)))
            self.bgRows["opacity"].sl.blockSignals(False)
            for w, v in ((self.chkUneven, bg.get("uneven", False)), (self.chkBg, bg.get("remove", True))):
                w.blockSignals(True)
                w.setChecked(bool(v))
                w.blockSignals(False)
            i = ("orig", "black", "blue").index(bg.get("tint", "orig"))
            self.tintGroup.blockSignals(True)
            self.tintGroup.button(i).setChecked(True)
            self.tintGroup.blockSignals(False)

    # ------------------------------------------- settings shared by all pictures
    def apply_action(self, act):
        """'Apply last setting to all': copies only the LAST changed setting from another picture (never crop, erase ...)."""
        k = act["kind"]
        if not self._loaded:                          # not opened yet: remember it, it is applied when the picture opens
            st = self._pending or {"adjs": [], "look": dict(self.look), "shadow_opts": dict(self.shadow_opts), "shadow": False}
            if k == "look":
                st["look"][act["key"]] = act["value"]
            elif k == "adjs":
                st["adjs"] = copy.deepcopy(act["adjs"])
            elif k == "shadow":
                st["shadow_opts"] = dict(act["opts"])
                st["shadow"] = True
            self._pending = st
            self._touch()
            return
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
        self.canvas.fx.ring("pick", x, y)
        if getattr(self, "_fill_drop", False):               # the eyedropper of the fill color
            self._fill_drop = False
            self._set_fill(rgb)
            self._go_move()
            self._notify("pick", "Fill color  #%02X%02X%02X" % rgb, "the Color Brush and Fill use it now")
            return
        self.canvas.fx.info("pick", "Picked  #%02X%02X%02X" % rgb, "its slider is in the Color tab")
        prev, lab0 = self.prev, self._lab

        def work(progress):                          # the colour analysis of a big picture takes a moment: not in the window thread
            lab = lab0 if lab0 is not None else core.rgb_to_lab(prev)
            return core.classify_pick(lab, rgb), lab

        def done(r):
            (kind, items, L), lab = r
            if self.prev is not prev:
                return
            if self._lab is None:
                self._lab = lab
            self._after_pick(kind, items, L)
        self._job("Analysing colors", work, done)

    def _after_pick(self, kind, items, L):
        self.show_tab(self.TAB_COLOR)
        if getattr(self, "grpPicked", None) is not None:
            self.grpPicked.expand()
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
    def _set_sel_mode(self, mode):
        self.sel_mode = mode
        self.canvas.sel_mode_name = mode

    def _move_selection(self, dx, dy):
        """The selection outline was dragged with Box / Ellipse / Freehand: only the outline moves, not the picture."""
        if not self._loaded or self.sel is None:
            return
        if self._bake(lambda: self._move_selection(dx, dy)):          # settings made in the old place stay there
            return
        moved = core.shift_mask(self.sel, int(dx), int(dy))
        if float(moved.max()) <= 0.01:                    # pushed completely out of the picture: nothing is selected
            moved = None
        self._checkpoint("Selection move")
        self.sel = moved
        self._sel_changed()
        self.canvas.fx.info("move", "Outline moved", dir_words(int(round(dx / self.scale)), int(round(dy / self.scale))))
        self.canvas.fx.pulse("move", self._sel_bbox())

    def _sel_changed(self):
        self._sel_ver += 1
        self.canvas.set_selection(self.sel)
        self._update_ring()
        self._hl_key = None
        self._sel_label()
        self._timer.start()                          # every edit is limited to the selection
        self._touch()

    def _set_selection(self, mask, mode=None, after=None):
        """after(base_selection) is called once the selection is really in place (also when the settings had to be
        written into the picture first) - the Color range click uses it to remember itself for the live sliders."""
        if self._bake(lambda: self._set_selection(mask, mode, after)):
            return
        base = self.sel
        new = core.sel_combine(base, mask, mode or self.sel_mode)
        if new is None and base is None:
            if after is not None:
                after(base)
            return
        self._checkpoint("Selection")
        self.sel = new
        self._sel_changed()
        self._sel_feedback(mode or self.sel_mode, base)
        if after is not None:
            after(base)

    def select_all(self):
        if not self._loaded:
            return
        if self._bake(self.select_all):
            return
        self._checkpoint("Select all")
        self.sel = np.ones(self.prev.shape[:2], np.float32)
        self._sel_changed()
        self.canvas.fx.info("select", "Selected everything", "Ctrl+D removes the selection")
        self.canvas.fx.pulse("select")

    def deselect(self):
        if not self._loaded or self.sel is None:
            return
        if self._bake(self.deselect):
            return
        self._checkpoint("Deselect")
        self.sel = None
        self._sel_changed()
        self.canvas.fx.info("select", "Selection removed", "everything can be changed again")

    def _notify(self, kind, title, detail=""):
        """Status bar text + a toast on the picture."""
        self.message.emit(title + (". " + detail if detail else ""))
        self.canvas.fx.info(kind, title, detail)

    @staticmethod
    def _bbox_rect(points, pad=0.0):
        xs, ys = [q[0] for q in points], [q[1] for q in points]
        return QRectF(QPointF(min(xs) - pad, min(ys) - pad), QPointF(max(xs) + pad, max(ys) + pad))

    def _sel_bbox(self):
        """The selection's bounding box in preview pixels (QRectF), or None."""
        m = self.sel
        if m is None:
            return None
        cols = np.nonzero(m.max(axis=0) > 0.5)[0]
        rows = np.nonzero(m.max(axis=1) > 0.5)[0]
        if cols.size == 0 or rows.size == 0:
            return None
        return QRectF(float(cols[0]), float(rows[0]), float(cols[-1] - cols[0] + 1), float(rows[-1] - rows[0] + 1))

    def _sel_feedback(self, mode, base):
        fx = self.canvas.fx
        if self.sel is None:
            fx.info("select", "Selection cleared")
            return
        r = self._sel_bbox()
        if r is None:
            return
        eff = mode if base is not None else "new"
        title = {"add": "Added to the selection", "sub": "Taken out of the selection",
                 "int": "Selection narrowed"}.get(eff, "Selected")
        u = 1.0 / max(self.scale, 1e-6)
        share = float(np.mean(self.sel > 0.5)) * 100.0
        fx.info("select", title, "%d \u00d7 %d px, %d%% of the picture" % (round(r.width() * u), round(r.height() * u), round(share)))
        fx.pulse("select", r)

    def _warn(self, text):
        """Short message right where the user looks (the hint line), also in the status bar."""
        head, _, rest = text.partition(". ")
        self.canvas.fx.warn(head.rstrip("."), rest)
        self.message.emit(text)
        self.lblHint.setText("\u26a0  " + text)
        self._hint_warn = True
        self._sync_hint()
        QTimer.singleShot(5000, self._end_warn)

    def _end_warn(self):
        self._hint_warn = False
        self.lblHint.setText(HINTS.get(self._tool, ""))
        self._sync_hint()

    def _select_adj(self, aid):
        """'Select this color': the places of a picked color become the selection."""
        if not self._loaded:
            return
        for a in self.adjs:
            if a["id"] == aid:
                prev, lab0, cache, a0 = self.prev, self._lab, self._cache, copy.deepcopy(a)

                def work(progress):
                    lab = lab0 if lab0 is not None else core.rgb_to_lab(prev)
                    return core.get_mask(prev, a0, lab, cache), lab

                def done(r):
                    mask, lab = r
                    if self.prev is not prev:
                        return
                    if self._lab is None:
                        self._lab = lab
                    self._set_selection(mask, self.sel_mode)
                    if self.sel is None:
                        self._warn("Nothing found for this color.")
                self._job("Selecting this color", work, done)
                return

    def invert_selection(self):
        if not self._loaded:
            return
        if self._bake(self.invert_selection):
            return
        new = core.sel_invert(self.sel, self.prev.shape[:2])
        self._checkpoint("Invert selection")
        self.sel = new
        self._sel_changed()
        self.canvas.fx.info("select", "Selection inverted", "now the other part is selected")
        self.canvas.fx.pulse("select", self._sel_bbox())

    def grow_selection(self, px):
        if not self._loaded:
            return
        if self.sel is None:
            self._warn("Select something first.")
            return
        if self._bake(lambda: self.grow_selection(px)):
            return
        new = core.sel_grow(self.sel, px)
        self._checkpoint("Selection size")
        self.sel = new
        self._sel_changed()
        self.canvas.fx.info("select", "Selection %s" % ("grown" if px > 0 else "shrunk"), "%d px" % abs(px))
        self.canvas.fx.pulse("select", self._sel_bbox())

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
        if kind == "brush" and self._tool == "deselect":
            self._deselect_brush(data, size_prev)
            return
        if kind == "brush":
            self._set_selection(core.shape_mask((W, H), "brush", data, size_prev), mode)
            return
        if kind == "color":
            self.canvas.fx.ring("select", data[0], data[1])
        tol = float(self._tolv)
        prev = self.prev
        fuzz = self._fuzz
        local = self.localSlider.value() / 100.0 if self.chkLocal.isChecked() else 0.0
        base_sel = self.sel

        def work(progress):
            lab = self._lab if self._lab is not None else core.rgb_to_lab(prev)
            info = None
            if kind == "color":
                x, y = data
                x, y = min(max(x, 0), W - 1), min(max(y, 0), H - 1)
                patch = prev[max(0, y - 1):y + 2, max(0, x - 1):x + 2].reshape(-1, 3).mean(0)
                rgb = tuple(int(round(float(v))) for v in patch)
                mask = core.color_range_fuzzy(lab, rgb, fuzz, (x, y), local)
                info = (rgb, (x, y))
            else:
                stroke = core.shape_mask((W, H), "brush", data, size_prev)
                mask = core.quick_select(lab, stroke, tol)
            return mask, lab, info

        def done(r):
            mask, lab, info = r
            if self._lab is None and self.prev is prev:
                self._lab = lab
            if self.prev is prev:
                def remember(base):
                    # the click is remembered only NOW, when the selection is really in place, so that Fuzziness /
                    # 'Near the click' can redo it live (before, it was remembered too early and the sliders did nothing)
                    if info is not None:
                        self._cr = {"lab": lab, "rgb": info[0], "center": info[1], "base": base, "mode": mode,
                                    "sel": self.sel}
                        self._update_ring()
                self._set_selection(mask, mode, after=remember)
                if info is not None and getattr(self, "chkUseClick", None) is not None \
                        and self.chkUseClick.isChecked() and mode != "sub":
                    self.fill_rgb = info[0]                    # the clicked color is the fill color now
                    self._paint_swatch()
        self._job("Selecting", work, done)

    def _deselect_brush(self, data, size_prev):
        """Take a painted part out of the selection - accurately (only what looks like what was painted over)."""
        if self.sel is None:
            self.message.emit("Nothing is selected yet. Select something first, then paint over what should NOT be selected.")
            return
        H, W = self.prev.shape[:2]
        prev, sel = self.prev, self.sel
        stroke = core.shape_mask((W, H), "brush", data, size_prev)
        snap, tol = self.chkSnap.isChecked(), float(self._tolv)

        def work(progress):
            lab = self._lab if self._lab is not None else core.rgb_to_lab(prev)
            m = core.smart_deselect(sel, lab, stroke, tol, size_prev) if snap else stroke
            return m, lab

        def done(r):
            m, lab = r
            if self._lab is None and self.prev is prev:
                self._lab = lab
            if self.prev is prev:
                self._set_selection(m, "sub")
        if snap:
            self._job("Taking it out of the selection", work, done)
        else:
            self._set_selection(stroke, "sub")

    def _on_move(self, dx, dy, copy_flag):
        if self.ctx is not None:
            self._warn("Not inside a picture. Use 'Move / resize' on the picture in the Picture tab to place it.")
            return
        if not self._loaded or self.sel is None:
            self._warn("Make a selection first.")
            return
        s = self.scale
        sel, base = self.sel, self.base
        mdx, mdy = int(round(dx / s)), int(round(dy / s))
        bb = self._sel_bbox()
        words = dir_words(mdx, mdy) + ("; the original stays" if copy_flag else "")
        self._fx_hint = {"rect": bb.translated(dx, dy) if bb is not None else None, "detail": words}

        def make(progress):
            mask = self._mask_img(sel, base.size)
            progress(0.2, "")
            return core.move_region(base, mask, mdx, mdy, copy=copy_flag), core.shift_mask(sel, dx, dy)
        self._edit_base("Copying" if copy_flag else "Moving", make)

    def delete_selection(self):
        """Delete key: what is selected disappears, the place is filled from its surroundings."""
        if not self._loaded:
            return
        if self._tool == "transform" and self._place_id is not None:   # a picture is being placed: Delete removes it
            self.delete_layer(self._place_id)
            return
        if self._tool == "transform":                # Delete while transforming: the object goes away
            self._go_move()
        if self.sel is None:
            self._warn("Nothing is selected. Select something first (Select menu), or pick a color and press 'Select this color'.")
            return
        sel, base, ink = self.sel, self.base, self.keep_ink
        bb = self._sel_bbox()
        self._fx_hint = {"rect": bb, "effect": "sweep", "detail": "filled from the paper around it" if self.ctx is None else ""}
        if self.ctx is not None:                     # inside a picture layer: deleted = transparent
            alpha = self.alpha
            self._edit_base("Deleting", lambda progress: base, msg="Deleted. Ctrl+Z brings it back.",
                            alpha_make=lambda progress: self._alpha_minus(alpha, self._mask_img(sel, alpha.size)))
            return

        def make(progress):
            return core.erase_region(base, self._mask_img(sel, base.size), None, 2.0, ink)
        self._edit_base("Deleting", make, msg="Deleted. Ctrl+Z brings it back.")

    @staticmethod
    def _alpha_minus(alpha, mask):
        return ImageChops.multiply(alpha, ImageChops.invert(mask))

    def fill_selection(self):
        if not self._loaded:
            return
        if self.sel is None:
            self._warn("Nothing is selected. Select something first.")
            return
        sel, base, rgb = self.sel, self.base, self.fill_rgb
        self._fx_hint = {"rect": self._sel_bbox(), "detail": "#%02X%02X%02X" % tuple(rgb)}

        def make(progress):
            return core.fill_region(base, self._mask_img(sel, base.size), rgb)
        alpha = self.alpha
        self._edit_base("Filling", make,
                        alpha_make=lambda progress: ImageChops.lighter(alpha, self._mask_img(sel, alpha.size)) if alpha is not None else None)

    # ---------------------------------------------------------------- tools
    def choose_tool(self, key):
        """From a menu, a button or a key. Transform needs a selection, so it has its own start."""
        if key == "transform":
            self.start_transform()
        elif key in CROP_KEYS and self.ctx is None and self._place_id is not None and self._layer(self._place_id) is not None:
            # a picture (signature, stamp ...) is selected on the page: crop THAT picture, then go back to the page
            lid = self._place_id

            def start():
                self._auto_back = True                       # set only now: opening the picture resets it
                self._set_tool(key)
                self.message.emit("Cropping the selected picture. (Click outside the picture first to crop the page instead.)")
            self.edit_layer(lid, then=start)
        else:
            self._set_tool(key)

    def _go_move(self):
        self._set_tool("view")

    def _set_tool(self, key):
        if not self._loaded and key != "view":
            return
        self._inline_end()
        if key not in CROP_KEYS:
            self._auto_back = False
        if self.ctx is not None and key in ("transform", "copy"):
            self._warn("Not inside a picture. Use 'Move / resize' on the picture in the Picture tab to place it.")
            return
        if key != "transform" and self._place_id is not None:      # a picture was not placed: it stays where it was
            self._place_id = None
            self._hide_layer = None
            self._timer.start()
        mode, shape = TOOL_MODE[key]
        old_tool = self._tool
        self._tool = key
        self.canvas.copy_mode = key == "copy"
        self.canvas.set_mode(mode, shape)
        self.canvas.set_crop_shape(CROP_SHAPE.get(key, "rect"))
        if key in ERASE_KEYS:
            self.canvas.set_erase(SHAPES[key])
        self.btnHand.setChecked(key == "view")
        self.btnPick.setChecked(key == "pick")
        self.btnPaint.setChecked(key == "paint")
        for fam, keys in (("select", SELECT_KEYS), ("move", MOVE_KEYS), ("erase", ERASE_KEYS), ("crop", CROP_KEYS)):
            b = self.menus[fam]
            on = key in keys
            b.setText((b.base_text + ": " + NAMES[key] if on else b.base_text) + "  ▾")
            self._mark(b, on)
        sel_t, er = key in SELECT_KEYS, key in ERASE_KEYS
        pend = key in CROP_KEYS or key == "transform"
        self.btnApply.setVisible(pend)
        self.btnCancel.setVisible(pend)
        self.optsA.setVisible(sel_t or er or key in ("transform", "paint"))
        self.tfBox.setVisible(key == "transform")
        self.optsB.setVisible(sel_t or key in MOVE_KEYS)
        self.modeBox.setVisible(sel_t)
        self.sizeBox.setVisible(key in ("quick", "brush", "erase_brush", "deselect", "paint"))
        self.paintBox.setVisible(key == "paint")
        self.tolBox.setVisible(key in ("quick", "color", "deselect"))
        self._tol_mode(key)
        self.btnClone.setVisible(er and self.clone_src is not None)
        self._sync_brush()
        self._sync_paint()
        self.lblHint.setText(HINTS[key])
        if key != old_tool and self._loaded and self.canvas.has_image():
            first = HINTS[key].split("  ")[0]
            kb = TOOL_KEYS.get(key)
            self.canvas.fx.info(tool_kind(key), tool_title(key), first + ("   Key: " + kb if kb else ""))

    def set_tf_mode(self, k):
        if self._tool == "transform":
            self.canvas.set_tf_mode(k)
            self.tfBtns[k].setChecked(True)

    def tf_rotate(self, deg):
        if self._tool == "transform":
            self.canvas.tf_rotate(deg)

    def tf_nudge(self, dx, dy):
        if self._tool == "transform":
            self.canvas.tf_nudge(dx, dy)

    def start_transform(self):
        """Ctrl+T: resize / rotate / move the selected part (or the selected picture layer)."""
        if not self._loaded:
            return
        if self.ctx is None and self.sel is None and self.sel_layer is not None:
            self.place_layer_tool(self.sel_layer)
            return
        px = self.canvas.selection_pixels() if self.sel is not None else None
        if px is None:
            self._warn("Select something first, then press Ctrl+T.")
            return
        self._set_tool("transform")
        self.canvas.start_transform(*px)
        self.tfBtns["free"].setChecked(True)

    def apply_pending(self):
        if self._tool in CROP_KEYS:
            self._apply_crop()
        elif self._tool == "transform" and self._place_id is not None:
            self._end_layer_session()
        elif self._tool == "transform":
            self._apply_transform()

    def cancel_pending(self):
        if self._tool in ERASE_KEYS:
            self._clear_clone()
        elif self._tool in CROP_KEYS or self._tool == "transform":
            back = self._auto_back
            self._go_move()
            self.canvas.fx.info("undo", "Cancelled", "nothing was changed")
            if back and self.ctx is not None:                  # the picture was opened only for this crop
                self._auto_back = False
                self.leave_layer()

    def _sync_brush(self):
        size = self.sizeSlider.value()
        self.lblSize.setText("%d px" % size)
        self.canvas.set_brush(size * self.scale)
        self.canvas.set_quick_brush(size * self.scale)

    def _sync_paint(self, *_):
        self.lblOp.setText("%d%%" % self.opSlider.value())
        self.lblSoft.setText("%d%%" % self.softSlider.value())
        self.canvas.set_paint(self.fill_rgb, self.opSlider.value() / 100.0)

    def _on_paint(self, pts):
        """A Color Brush stroke: paint it with the fill color - inside the selection only, or anywhere without one."""
        if not self._loaded or not pts:
            return
        s = self.scale
        payload = [(x / s, y / s) for x, y in pts]
        size, rgb = self.sizeSlider.value(), self.fill_rgb
        opacity, soft = self.opSlider.value() / 100.0, self.softSlider.value()
        base, sel, alpha = self.base, self.sel, self.alpha
        if self.ctx is None:
            alpha = None
        self._fx_hint = {"rect": self._bbox_rect(pts, size * s / 2.0 + 2), "effect": "pulse",
                         "detail": "%d%% opacity, #%02X%02X%02X" % (round(opacity * 100), rgb[0], rgb[1], rgb[2])}
        self._edit_base("Painting", lambda progress: core.paint_brush(base, payload, size, rgb, opacity, soft, sel, alpha),
                        msg="Painted. Ctrl+Z undoes the stroke.")

    def _on_source(self, x, y):
        self.clone_src = (x, y)
        self.clone_off = None
        self.canvas.set_clone(self.clone_src, None)
        self.btnClone.show()
        self.canvas.fx.ring("copy", x, y)
        self._notify("copy", "Copy spot chosen", "now paint where the copy should go; Esc clears the spot")

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
        base, sel = self.base, self.sel
        ink = False        # the eraser removes EVERYTHING it covers (handwriting too); 'keep writing under a stamp' is only for stamp removal
        if kind in ("brush", "lasso"):
            er_rect = self._bbox_rect(data, size * s / 2.0 + 2 if kind == "brush" else 0.0)
        else:
            er_rect = self._bbox_rect([(data[0], data[1]), (data[2], data[3])])
        self._fx_hint = {"rect": er_rect, "effect": "sweep",
                         "detail": "%d \u00d7 %d px, filled from around" % (round(er_rect.width() / s), round(er_rect.height() / s))}

        def get_mask():
            mask = core.make_mask(base.size, kind, payload, brush=size)
            if sel is not None:                      # with a selection, erasing only works inside it
                m = np.asarray(mask, dtype=np.float32) * core.resize_mask(sel, base.size)
                mask = Image.fromarray(np.clip(m, 0, 255).astype(np.uint8))
            return mask

        def make(progress):
            feather = max(1.5, 0.08 * size) if kind == "brush" else 2.0
            return core.erase_region(base, get_mask(), offset, feather, ink)
        if self.ctx is not None:                     # inside a picture layer: erased = transparent
            alpha = self.alpha
            self._edit_base("Erasing", lambda progress: base,
                            alpha_make=lambda progress: self._alpha_minus(alpha, get_mask().filter(ImageFilter.GaussianBlur(0.8))))
            return
        self._edit_base("Erasing", make)

    def _apply_crop(self):
        if not self._loaded:
            return
        c = self.canvas
        s = self.scale
        base = self.base
        alpha = self.alpha
        inlayer = self.ctx is not None
        back = self._auto_back
        alpha_make = None
        if self._tool == "crop":
            l, t, r, b = c.crop_rect_img()
            box = (l / s, t / s, r / s, b / s)

            def make(progress):
                return core.crop_box(base, box)
            if inlayer:
                alpha_make = lambda progress: core.crop_box(alpha.convert("RGB"), box).convert("L")
        elif self._tool == "persp":
            quad = [(x / s, y / s) for x, y in c.quad_img()]

            def make(progress):
                return core.warp_quad(base, quad)
            if inlayer:                                       # the transparency is straightened the same way
                alpha_make = lambda progress: core.warp_quad(alpha.convert("RGB"), quad).convert("L")
        elif self._tool in CROP_SHAPE:                        # circle / oval / rounded box
            l, t, r, b = [v / s for v in c.crop_rect_img()]
            shape = "round" if self._tool == "crop_round" else "oval"
            if self._tool == "crop_circle":                   # a circle fits in the shorter side, centred in the box
                side = min(r - l, b - t)
                cx, cy = (l + r) / 2.0, (t + b) / 2.0
                l, t, r, b = cx - side / 2.0, cy - side / 2.0, cx + side / 2.0, cy + side / 2.0
            box = (l, t, r, b)
            cache = {}

            def shaped():
                if "v" not in cache:
                    cache["v"] = core.crop_to_shape(base, alpha if inlayer else None, box, shape)
                return cache["v"]

            def make(progress):
                return shaped()[0]
            if inlayer:
                alpha_make = lambda progress: shaped()[1]
        else:
            return

        def after():
            self._clear_clone()
            self._go_move()
            if back:                                          # a picture was opened only for this crop
                self._auto_back = False
                self.leave_layer()
        self._fx_hint = {"title": "Straightened" if self._tool == "persp" else "Cropped",
                         "detail": lambda: "%d \u00d7 %d px" % self.base.size}
        self._edit_base("Cropping", make, keep_sel=False, refit=True, after=after, alpha_make=alpha_make)

    def crop_to_selection(self):
        """Keeps only what is inside the current selection (any shape: box, ellipse, freehand, color range).
        A picture (signature, stamp ...) becomes transparent outside the shape; on the page the outside gets the paper color."""
        if not self._loaded:
            return
        if self.ctx is None and self._place_id is not None and self._layer(self._place_id) is not None:
            lid = self._place_id                              # a picture is selected: draw the shape on that picture
            self.edit_layer(lid, then=lambda: self.message.emit(
                "Now select the shape you want to keep (Select menu: Box, Ellipse, Freehand ...), then choose "
                "Crop > Crop to the selection shape again."))
            return
        if self.sel is None:
            self._warn("First select the shape you want to keep (Select menu: Box, Ellipse, Freehand, Color range), "
                       "then choose this again.")
            return
        bb = core.sel_bbox(self.sel, pad=0)
        if bb is None:
            self._warn("The selection is empty.")
            return
        y0, y1, x0, x1 = bb
        s = self.scale
        box = (x0 / s, y0 / s, x1 / s, y1 / s)
        base, alpha, inlayer = self.base, self.alpha, self.ctx is not None
        mask = self._mask_img(self.sel, base.size)
        cache = {}

        def shaped():
            if "v" not in cache:
                cache["v"] = core.crop_to_shape(base, alpha if inlayer else None, box, None, mask=mask)
            return cache["v"]
        self._fx_hint = {"detail": lambda: "%d \u00d7 %d px" % self.base.size}
        self._edit_base("Cropping to the shape", lambda progress: shaped()[0], keep_sel=False, refit=True,
                        alpha_make=(lambda progress: shaped()[1]) if inlayer else None,
                        msg="Cropped to the selected shape.")

    def _on_tf_copy(self, on):
        if on:
            self._notify("copy", "Copy mode", "the original stays where it is; Enter places the copy")

    def _apply_transform(self):
        p = self.canvas.transform_params()
        if p is None or self.sel is None or not self._loaded:
            return
        pts, n = p
        copy_flag = self.canvas.transform_copy()
        s = self.scale
        grid = [(x / s, y / s) for x, y in pts]
        sel, base = self.sel, self.base
        H, W = self.prev.shape[:2]

        def make(progress):
            mask = self._mask_img(sel, base.size)
            new, nm = core.warp_region(base, mask, grid, n, copy=copy_flag)
            nsel = core.resize_mask(np.asarray(nm, dtype=np.float32) / 255.0, (W, H))
            return new, (nsel if float(nsel.max()) > 0.02 else None)
        self._fx_hint = {"rect": self._bbox_rect([(x, y) for x, y in pts]),
                         "title": "Copy placed" if copy_flag else "Transformed",
                         "detail": "the original stays" if copy_flag else ""}
        self._edit_base("Copying" if copy_flag else "Transforming", make, after=self._go_move)

    def rotate(self, deg_ccw):
        """Turn the whole picture (the page, or the picture layer being edited) by deg_ccw; pictures placed on the
        page turn with it."""
        if not self._loaded:
            return
        if self._tool in CROP_KEYS or self._tool == "transform":
            self._place_id = None
            self._hide_layer = None
            self._go_move()
        base, alpha = self.base, self.alpha
        am = (lambda progress: core.rotate_image(alpha.convert("RGB"), deg_ccw).convert("L")) if self.ctx is not None else None
        quarter = int(round(deg_ccw / 90.0)) % 4 if abs(deg_ccw / 90.0 - round(deg_ccw / 90.0)) < 1e-6 else None
        layers_in = self.layers if (self.ctx is None and quarter) else None

        def turn(p):                                   # one quarter turn counter-clockwise in normalised page coordinates
            return (p[1], 1.0 - p[0])

        def after():
            if layers_in:
                ls = []
                for l in layers_in:
                    g = l["grid"]
                    for _ in range(quarter):
                        g = [turn(p) for p in g]
                    ls.append(dict(l, grid=g))
                self.layers = ls
                self._timer.start()
        self._fx_hint = {"detail": "%g\u00b0 %s" % (abs(deg_ccw), "to the left" if deg_ccw > 0 else "to the right")}
        self._edit_base("Rotating", lambda progress: core.rotate_image(base, deg_ccw), keep_sel=False, refit=True,
                        alpha_make=am, after=after)

    def _auto_shadow(self, live=False):
        """Shadow removal. It always starts from the picture BEFORE the first shadow removal, so the options
        (strength, whiteness ...) can be changed afterwards and the result follows - nothing is applied twice."""
        if not self._loaded:
            return
        if live and self.shadow_src is None:
            return
        if live and self._shadow_busy:               # still working: one more pass follows, with the latest values
            self._shadow_again = True
            return
        self._shadow_busy = True
        src = self.shadow_src if self.shadow_src is not None else self.base
        sel = self.sel
        opts = dict(self.shadow_opts)

        def make(progress):
            new = core.remove_shadow(src, opts["strength"] / 100.0, progress, opts)
            if sel is not None:                      # only the selected part
                new = Image.composite(new, src, self._mask_img(sel, src.size))
            return new

        def finish():
            self._shadow_busy = False
            if self._shadow_again:
                self._shadow_again = False
                self._shadow_timer.start()

        def after():
            self._shadow_done = sel is None
            finish()
        self._set_last({"kind": "shadow", "opts": opts, "label": "shadow removal"})
        self._edit_base("Removing shadows", make, after=after, shadow_src=src, merge=live, fail=lambda m: finish(),
                        msg=None if live else "Shadow removed. Move the sliders to change it, Ctrl+Z to undo.")

    MANUAL_SHADOW = ("flat", "flat_size", "shlift", "white", "black")

    def _news_click(self):
        """One click: the strength is found from the picture, then the slider can take it up or down."""
        if not self._loaded:
            return
        prev = self.prev

        def work(progress):
            return core.auto_news_strength(prev)

        def done(s):
            if self.prev is not prev:
                return
            self._set_look("news", s)
            self.lookRows["news"].set_value(s)
            self.message.emit("Page whitened (%d). Move 'White page' to take it down or up." % s)
        self._job("Making the page white", work, done)

    def _reset_manual_shadow(self):
        if not self._loaded:
            return
        self._begin_change()
        for k in self.MANUAL_SHADOW:
            self.look[k] = core.DEFAULT_LOOK[k]
        self._sync_sliders()
        self._timer.start()
        self._touch()

    def _shadow_opt(self, key, v):
        self.shadow_opts[key] = v
        if self.shadow_src is not None:              # shadow already applied: follow the slider live
            self._shadow_timer.start()

    def reset_image(self):
        if not self._loaded:
            return
        path = self.src_path()
        self._checkpoint()                           # Reset can be undone too
        self.adjs = []
        self.look = core.new_look()
        self.shadow_opts = dict(core.SHADOW_DEFAULTS)
        self._shadow_done = False
        self.shadow_src = None
        self._pending = None
        self.colors.rebuild(self.adjs)
        self._sync_sliders()
        self._clear_clone()
        self._go_move()

        def make(progress):
            progress(0.2, "reading the original")
            return core.load_image(path)

        def after():
            self.ops = 0
            self.sel = None
            self._sel_ver += 1
            self._touch()
        self._edit_base("Resetting " + self.name, make, keep_sel=False, refit=True, checkpoint=False, after=after,
                        fail=lambda msg: self.message.emit("Cannot reload: " + msg.splitlines()[0]))

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
        src_path = self.path

        def work(progress):
            os.makedirs(EXPORT_DIR, exist_ok=True)
            path = core.unique_path(EXPORT_DIR, stem, ext)
            core.save_image(core.render_full(*args), path, src=src_path)
            return path

        def done(path):
            self._exporting = False
            if self._dead:
                return
            self.set_export(ver, path)
            self.exportReady.emit()

        def fail(msg):
            self._exporting = False
            self._drag_wait = None
            if self._dead:
                return
            self.message.emit("Could not prepare the picture: " + msg.splitlines()[0])
        run_background(work, done, fail)

    def set_export(self, ver, path):
        """Remembers the edited copy made for drag / copy / print; the one of an older version is deleted."""
        old = self._export
        self._export = (ver, path)
        if old is not None and old[1] != path:
            try:
                if os.path.dirname(os.path.abspath(old[1])) == os.path.abspath(EXPORT_DIR):
                    os.remove(old[1])
            except OSError:
                pass

    def export_file(self):
        """The picture with all edits as a file (made now if it is not ready)."""
        self.ensure_loaded()
        ready = self.export_ready()
        if ready is not None:
            return ready
        os.makedirs(EXPORT_DIR, exist_ok=True)
        stem, ext = self.out_name()
        path = core.unique_path(EXPORT_DIR, stem, ext)
        core.save_image(self.result_image(), path, src=self.path)
        self.set_export(self._ver, path)
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
        """Ctrl+C / the Copy button: the window makes the file in a worker thread and fills the clipboard."""
        if not self._loaded:
            return
        self.copyRequested.emit(self)

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
