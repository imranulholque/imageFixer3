"""'Scan' window: finds the scanners (TWAIN and WIA, 32-bit and 64-bit drivers) and scans into Image Fixer.
Two ways: Normal (one picture per page) and ID card (FRONT, then BACK -> one page, each side its own layer).
The real scanning is done by scanhelper (a separate small program, a 32-bit and a 64-bit copy), see scanhelper.py."""
import json
import math
import os
import shutil
import sys
import tempfile
import time

from PySide6.QtCore import QProcess, QSize, QTimer, Qt
from PySide6.QtGui import QColor
from PySide6.QtWidgets import (QApplication, QCheckBox, QColorDialog, QComboBox, QDialog, QFormLayout, QFrame, QHBoxLayout, QLabel, QProgressBar,
                               QPushButton, QScrollArea, QSlider, QVBoxLayout, QWidget)

from icons import icon as _ico
import idpreview
import idscan
import jobs
import scanfix

SIZES = (("Auto (the scanner decides the paper size)", "max"), ("A4", "a4"), ("A5", "a5"), ("A3", "a3"), ("B5", "b5"),
         ("Letter", "letter"), ("Legal", "legal"))
MODES = (("Color", "color"), ("Gray", "gray"), ("Black and white", "bw"))
SHORTCUT = "Ctrl+Shift+N"
# about how long the scanner itself needs (seconds) when we have not scanned with these settings before
GUESS_S = {150: 8.0, 200: 10.0, 300: 14.0, 400: 20.0, 600: 34.0}
BAR_CSS = ("QProgressBar { background: #1b1e2e; border: 1px solid #343a5a; border-radius: 8px; }"
           "QProgressBar::chunk { border-radius: 7px; background: qlineargradient(x1:0, y1:0, x2:1, y2:0,"
           " stop:0 #6366f1, stop:1 #22d3ee); }")
STAGE_TEXT = {"start": "Starting the scanner program ...", "setup": "Preparing the scanner ...",
              "scanning": "Scanning - the scanner is working ...", "process": "Making the picture sharper ...",
              "ui": "Use the scanner's own window ..."}


def helper_command(bits):
    """(program, leading arguments) of the scan helper with this bitness, or None when there is none."""
    here = os.path.dirname(os.path.abspath(sys.executable if getattr(sys, "frozen", False) else __file__))
    name = "ScanHelper%d" % bits
    if getattr(sys, "frozen", False):
        for p in (os.path.join(here, "scanhelpers", name, name + ".exe"), os.path.join(here, name, name + ".exe")):
            if os.path.exists(p):
                return p, []
        return None
    script = os.path.join(here, "scanhelper.py")                 # started from the sources (python app.py)
    own = 64 if sys.maxsize > 2 ** 32 else 32
    if bits == own:
        return sys.executable, [script]
    py32 = os.environ.get("IMAGEFIXER_PY32") or (shutil.which("py") and "py")
    if bits == 32 and py32:
        return py32, (["-3-32"] if os.path.basename(py32).lower().startswith("py") else []) + [script]
    return None


def read_events(path):
    out = []
    try:
        with open(path, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if line:
                    try:
                        out.append(json.loads(line))
                    except Exception:
                        pass
    except Exception:
        pass
    return out


def build_side(path, dpi, fix, quad, progress):
    """One scanned page -> one card side (worker thread).  quad = the 4 corners chosen by hand (Corner crop), or None =
    find the card by itself.  -> {"path", "dpi", "card", "found", "eff", "quad"}"""
    from PIL import Image
    im = Image.open(path)
    im.load()
    d = dpi
    try:                                       # the scanner may have used another dpi than asked: the file knows
        m = im.info.get("dpi")
        if m and 100 <= float(m[0]) <= 1200 and abs(float(m[0]) - dpi) > 0.05 * dpi:
            d = float(m[0])
    except Exception:
        pass
    if quad is None:
        card, ok = idscan.cut_card(im, d)
    else:
        card, ok = idscan.cut_quad(im, quad), True
    card, eff = scanfix.enhance(card, d, fix, progress)           # sharper card; eff = its real dpi now
    return {"path": path, "dpi": d, "card": card, "found": ok, "eff": eff, "quad": quad}


CARD_CSS = ("QFrame#scanCard { background: #141926; border: 1px solid #2a3042; border-radius: 12px; }"
            "QFrame#scanCard QLabel#cardCap { color: #9aa3bd; font-size: 11px; font-weight: 700; letter-spacing: 1px; }")


class _Rows(QVBoxLayout):
    """A column of widgets that understands form.addRow("", widget) - for the check boxes (no empty label column)."""
    def addRow(self, _label, field):
        if isinstance(field, QWidget):
            self.addWidget(field)
        else:
            self.addLayout(field)


def _card(caption, icon_name, vertical=False):
    """A rounded section: small icon + CAPTION, then its rows.  Returns (frame, layout to add rows to)."""
    fr = QFrame()
    fr.setObjectName("scanCard")
    fr.setStyleSheet(CARD_CSS)
    v = QVBoxLayout(fr)
    v.setContentsMargins(12, 8, 12, 10)
    v.setSpacing(6)
    head = QHBoxLayout()
    head.setSpacing(6)
    ic = QLabel()
    ic.setPixmap(_ico(icon_name, 16).pixmap(QSize(16, 16)))
    cap = QLabel(caption.upper())
    cap.setObjectName("cardCap")
    head.addWidget(ic)
    head.addWidget(cap)
    head.addStretch(1)
    v.addLayout(head)
    rows = _Rows() if vertical else QFormLayout()
    rows.setSpacing(6)
    if not vertical:
        rows.setLabelAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
    v.addLayout(rows)
    return fr, rows


class ScanDialog(QDialog):
    def __init__(self, win):
        super().__init__(win)
        self.win = win
        self.st = win.settings
        self.setWindowTitle("Scan")
        self.setModal(False)
        self.setMinimumWidth(820)
        self.work = tempfile.mkdtemp(prefix="scan_", dir=win.tmp)
        self._procs = []                  # running list jobs
        self._proc = None                 # the running scan
        self._front = None                # ID card: the FRONT picture while we wait for the BACK
        self._front_side = None           # ... and everything about it (scan file, corners) for the Corner crop
        self._bcolor = idscan.BORDER_COLOR
        self._busy = False
        self._n = 0
        self._found = []
        self._errors = []

        root = QVBoxLayout(self)
        root.setSpacing(8)
        root.setContentsMargins(14, 12, 14, 12)
        th = QHBoxLayout()
        th.setSpacing(8)
        tic = QLabel()
        tic.setPixmap(_ico("scan", 26).pixmap(QSize(26, 26)))
        title = QLabel("Scan from a scanner")
        title.setStyleSheet("font-size: 16px; font-weight: 700;")
        th.addWidget(tic)
        th.addWidget(title)
        th.addStretch(1)
        root.addLayout(th)

        fr1, form = _card("Scanner", "scan")
        fr2, form2 = _card("Paper & quality", "blank")
        fr3, form3 = _card("Options", "settings", vertical=True)
        self._idcard, form4 = _card("ID card", "border")
        row = QHBoxLayout()
        self.cmbDev = QComboBox()
        self.cmbDev.setMinimumWidth(180)
        self.btnRefresh = QPushButton("Find scanners")
        self.btnRefresh.setToolTip("Look for TWAIN and WIA scanners again")
        self.btnRefresh.setIcon(_ico("reset", 18))
        self.btnRefresh.clicked.connect(self.refresh)
        row.addWidget(self.cmbDev, 1)
        row.addWidget(self.btnRefresh)
        form.addRow("Scanner", row)
        self.lblFound = QLabel("")
        self.lblFound.setObjectName("muted")
        self.lblFound.setWordWrap(True)
        form.addRow("", self.lblFound)

        form = form2
        self.cmbMode = QComboBox()
        self.cmbMode.addItem("Normal: one picture for every page", "normal")
        self.cmbMode.addItem("ID card: front + back on one page (2 layers)", "id")
        self.cmbMode.currentIndexChanged.connect(self._mode_changed)
        form.addRow("What to scan", self.cmbMode)

        self.cmbSize = QComboBox()
        for t, v in SIZES:
            self.cmbSize.addItem(t, v)
        form.addRow("Paper size", self.cmbSize)
        r2 = QHBoxLayout()
        self.cmbDpi = QComboBox()
        for d in (150, 200, 300, 400, 600):
            self.cmbDpi.addItem("%d dpi" % d, d)
        self.cmbCol = QComboBox()
        for t, v in MODES:
            self.cmbCol.addItem(t, v)
        r2.addWidget(self.cmbDpi)
        r2.addWidget(self.cmbCol)
        form.addRow("Quality", r2)
        self.cmbFix = QComboBox()
        self.cmbFix.addItem("Sharper: clean + enlarge + sharpen (best)", scanfix.BEST)
        self.cmbFix.addItem("Sharper: sharpen only (same size)", scanfix.SHARPEN)
        self.cmbFix.addItem("Off: exactly as the scanner gave it", scanfix.OFF)
        self.cmbFix.setToolTip("Makes soft scans crisper. A higher scan dpi (600 for an ID card) gives the best result.")
        form.addRow("Sharpness", self.cmbFix)
        form = form3

        self.chkUi = QCheckBox("Show the scanner's own window (driver settings)")
        self.chkUi.setToolTip("Like Photoshop: the driver of the scanner opens its own window to choose size, preview ...")
        self.chkFeed = QCheckBox("Use the document feeder (ADF) instead of the glass")
        self.chkDuplex = QCheckBox("Feeder scans both sides (duplex)")
        self.chkDuplex.setToolTip("ID card: one scan gives front and back together")
        self.chkClose = QCheckBox("Close this window by itself when the scan is finished")
        self.chkClose.setToolTip("When the picture(s) are added to Image Fixer this window goes away. "
                                 "(It stays open while an ID card still needs its back side.)")
        self.chkClose.setChecked(True)
        self.chkFeed.toggled.connect(self._mode_changed)
        form.addRow("", self.chkUi)
        form.addRow("", self.chkFeed)
        form.addRow("", self.chkDuplex)
        form.addRow("", self.chkClose)

        self.cmbPage = QComboBox()
        for p in idscan.PAGES_MM:
            self.cmbPage.addItem(p, p)
        self.cmbPlace = QComboBox()
        self.cmbPlace.addItem("centre of the page", "center")
        self.cmbPlace.addItem("top of the page", "top")
        form = form4
        r3 = QHBoxLayout()
        r3.addWidget(self.cmbPage)
        r3.addWidget(self.cmbPlace)
        self.lblPage = QLabel("ID card page")
        form.addRow(self.lblPage, r3)
        self._r3 = (self.cmbPage, self.cmbPlace)

        # border of the card layers: colour + thickness (they can be changed later in the Border tab, too)
        self.chkBorder = QCheckBox("Border")
        self.chkBorder.setToolTip("A frame around each card (front and back). Colour and thickness can be changed any time\n"
                                  "later in the Border tab; nothing else (brightness, sharpness ...) changes it.")
        self.btnBorderColor = QPushButton()
        self.btnBorderColor.setFixedSize(40, 26)
        self.btnBorderColor.setToolTip("Border colour")
        self.btnBorderColor.clicked.connect(self._pick_border_color)
        self.sldBorder = QSlider(Qt.Orientation.Horizontal)
        self.sldBorder.setRange(1, 40)
        self.sldBorder.setToolTip("Border thickness")
        self.lblBorderW = QLabel("")
        self.lblBorderW.setMinimumWidth(24)
        self.sldBorder.valueChanged.connect(lambda v: self.lblBorderW.setText(str(v)))
        r4 = QHBoxLayout()
        r4.addWidget(self.chkBorder)
        r4.addWidget(self.btnBorderColor)
        r4.addWidget(QLabel("thickness"))
        r4.addWidget(self.sldBorder, 1)
        r4.addWidget(self.lblBorderW)
        self.lblBorder = QLabel("Card border")
        form.addRow(self.lblBorder, r4)
        self.chkBorder.toggled.connect(self._border_enable)

        # corners of the card layers: round on / off + how round (can be changed later in the Border tab, too)
        self.chkRound = QCheckBox("Round")
        self.chkRound.setToolTip("Cut the four corners of each card round, like a real card.\n"
                                 "Switch it off = square corners. How round can be changed any time later in the Border tab.")
        self.sldRound = QSlider(Qt.Orientation.Horizontal)
        self.sldRound.setRange(5, idscan.ROUND_MAX)
        self.sldRound.setToolTip("How round the corners are:  100% = a real ID card,  less = sharper corners,  more = rounder corners")
        self.lblRoundV = QLabel("")
        self.lblRoundV.setMinimumWidth(40)
        self.sldRound.valueChanged.connect(lambda v: self.lblRoundV.setText("%d%%" % v))
        self.sampleRound = idpreview.CornerSample()
        self.sldRound.valueChanged.connect(lambda _v: self._round_sample())
        r5 = QHBoxLayout()
        r5.addWidget(self.chkRound)
        self.lblRoundT = QLabel("roundness")
        r5.addWidget(self.lblRoundT)
        r5.addWidget(self.sldRound, 1)
        r5.addWidget(self.lblRoundV)
        r5.addWidget(self.sampleRound)
        self.lblRound = QLabel("Card corners")
        form.addRow(self.lblRound, r5)
        self.chkRound.toggled.connect(self._round_enable)
        self.chkPreview = QCheckBox("Preview each side (front, back) before joining")
        self.chkPreview.setToolTip("After the FRONT and again after the BACK you see how the card will look (corners + border).\n"
                                   "OK = go on, Corner crop = drag the 4 corners yourself, Scan again = scan this side again.\n"
                                   "Switch it off to put the card on the page at once.")
        form.addRow("", self.chkPreview)
        self._idrows = (self.cmbPage, self.cmbPlace, self.lblPage, self.chkBorder, self.btnBorderColor, self.sldBorder,
                        self.lblBorderW, self.lblBorder, self.chkPreview,
                        self.chkRound, self.sldRound, self.lblRoundV, self.lblRound, self.lblRoundT, self.sampleRound,
                        self._idcard)
        # the cards scroll when the screen is small; the title, the step text and the Scan / Close buttons never scroll away
        self._body = QWidget()
        bl = QHBoxLayout(self._body)                     # two columns: short enough for any laptop screen, no scrolling
        bl.setContentsMargins(0, 0, 0, 0)
        bl.setSpacing(10)
        c1, c2 = QVBoxLayout(), QVBoxLayout()
        c1.setSpacing(8)
        c2.setSpacing(8)
        c1.addWidget(fr1)
        c1.addWidget(fr2)
        c1.addStretch(1)
        c2.addWidget(fr3)
        c2.addWidget(self._idcard)
        c2.addStretch(1)
        bl.addLayout(c1, 1)
        bl.addLayout(c2, 1)
        self._scroll = QScrollArea()
        self._scroll.setWidgetResizable(True)
        self._scroll.setFrameShape(QFrame.Shape.NoFrame)
        self._scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self._scroll.setStyleSheet("QScrollArea { background: transparent; border: none; }"
                                   "QScrollArea > QWidget > QWidget { background: transparent; }")
        self._scroll.setWidget(self._body)
        self._scroll.setMinimumHeight(140)
        root.addWidget(self._scroll, 1)

        self.lblStep = QLabel("")
        self.lblStep.setWordWrap(True)
        self.lblStep.setStyleSheet("font-weight: 600; color: #a5b4fc;")
        root.addWidget(self.lblStep)
        self.lblMsg = QLabel("")
        self.lblMsg.setObjectName("muted")
        self.lblMsg.setWordWrap(True)
        root.addWidget(self.lblMsg)

        # progress: what is happening now, how far, how long
        self.pwrap = QWidget()
        pl = QVBoxLayout(self.pwrap)
        pl.setContentsMargins(0, 0, 0, 0)
        pl.setSpacing(4)
        self.pbar = QProgressBar()
        self.pbar.setRange(0, 1000)
        self.pbar.setTextVisible(False)
        self.pbar.setFixedHeight(16)
        self.pbar.setStyleSheet(BAR_CSS)
        self.lblProg = QLabel("")
        self.lblProg.setStyleSheet("font-weight: 600;")
        self.lblTime = QLabel("")
        self.lblTime.setObjectName("muted")
        row = QHBoxLayout()
        row.addWidget(self.lblProg, 1)
        row.addWidget(self.lblTime)
        pl.addWidget(self.pbar)
        pl.addLayout(row)
        self.pwrap.hide()
        root.addWidget(self.pwrap)
        self._p = None
        self._ptimer = QTimer(self)
        self._ptimer.setInterval(80)
        self._ptimer.timeout.connect(self._tick)

        btns = QHBoxLayout()
        self.btnScan = QPushButton("Scan")
        self.btnScan.setProperty("accent", True)
        self.btnScan.setDefault(True)
        self.btnScan.setToolTip("Enter")
        self.btnScan.clicked.connect(self._scan_clicked)
        self.btnAgain = QPushButton("Start the ID card again")
        self.btnAgain.clicked.connect(self._reset_id)
        self.btnClose = QPushButton("Close")
        self.btnClose.clicked.connect(self.close)
        self.btnScan.setIcon(_ico("scan", 18, normal="#ffffff"))
        self.btnScan.setMinimumHeight(36)
        self.btnAgain.setIcon(_ico("reset", 18))
        self.btnClose.setIcon(_ico("close", 18))
        btns.addWidget(self.btnScan)
        btns.addWidget(self.btnAgain)
        btns.addStretch(1)
        btns.addWidget(self.btnClose)
        root.addLayout(btns)
        self.lblKey = QLabel("Shortcut to open this window: " + SHORTCUT)
        self.lblKey.setObjectName("muted")
        root.addWidget(self.lblKey)
        self._load_settings()
        self._mode_changed()

    # ------------------------------------------------------------ progress bar
    def _time_key(self):
        return "scan/time/%s_%s_%s_%s" % ((self.cmbDev.currentData() or {}).get("kind", ""), self.cmbDpi.currentData(),
                                          self.cmbSize.currentData(), self.cmbCol.currentData())

    def _prog_start(self, out):
        try:
            est = float(self.st.value(self._time_key(), 0) or 0)
        except Exception:
            est = 0.0
        if est <= 0:
            est = GUESS_S.get(int(self.cmbDpi.currentData() or 300), 15.0)
        now = time.time()
        self._p = {"out": out, "seen": 0, "stage": "start", "ts": now, "t0": now, "est": max(2.0, est), "pages": 0,
                   "shown": 0.0, "proc": 0.0, "ptext": "", "ui": self.chkUi.isChecked(), "learned": False}
        self.pbar.setRange(0, 1000)
        self.pbar.setValue(0)
        self.pwrap.show()
        self._ptimer.start()
        self._tick()

    def _prog_stage(self, name):
        if self._p is not None and self._p["stage"] != name:
            self._p["stage"] = name
            self._p["ts"] = time.time()

    def _prog_proc(self, frac, text=""):
        if self._p is not None:
            self._p["proc"] = max(self._p["proc"], max(0.0, min(1.0, float(frac))))
            self._p["ptext"] = text or ""

    def _prog_hide(self, delay=0):
        self._ptimer.stop()
        p, self._p = self._p, None
        if delay and p is not None:
            self.pbar.setRange(0, 1000)
            self.pbar.setValue(1000)
            QTimer.singleShot(delay, lambda: self.pwrap.hide() if self._p is None else None)
        else:
            self.pwrap.hide()

    def _tick(self):
        p = self._p
        if p is None:
            return
        for ev in read_events(p["out"])[p["seen"]:]:
            p["seen"] += 1
            kind = ev.get("event")
            if kind == "stage":
                self._prog_stage(str(ev.get("stage")))
            elif kind == "page":
                p["pages"] += 1
                if not p["learned"] and p["stage"] == "scanning":       # remember how long this scanner needs
                    p["learned"] = True
                    took = time.time() - p["ts"]
                    if 1.0 < took < 600.0:
                        old = float(self.st.value(self._time_key(), 0) or 0)
                        self.st.setValue(self._time_key(), took if old <= 0 else 0.5 * old + 0.5 * took)
        now = time.time()
        dt = now - p["ts"]
        st = p["stage"]
        if st == "process":
            target = 0.80 + 0.20 * p["proc"]
        elif st == "start":
            target = 0.02 + 0.06 * (1 - math.exp(-dt / 2.0))
        elif st == "setup":
            target = 0.08 + 0.07 * (1 - math.exp(-dt / 1.5))
        else:                                                              # scanning: slows down, never stands still
            target = 0.15 + 0.60 * (1 - math.exp(-dt / (p["est"] * 0.55)))
            if p["pages"]:
                target = max(target, 0.15 + 0.60 * p["pages"] / (p["pages"] + 1.0))
        if p["ui"] and st == "scanning":
            self.pbar.setRange(0, 0)                                       # the person drives the driver window: no estimate
            text = STAGE_TEXT["ui"]
        else:
            if self.pbar.maximum() == 0:
                self.pbar.setRange(0, 1000)
            p["shown"] = max(p["shown"], p["shown"] + (target - p["shown"]) * 0.2)
            self.pbar.setValue(int(min(0.995, p["shown"]) * 1000))
            text = STAGE_TEXT.get(st, "Working ...")
        extra = []
        if st == "process" and p["ptext"]:
            extra.append(p["ptext"])
        elif p["pages"]:
            extra.append("%d page%s received" % (p["pages"], "" if p["pages"] == 1 else "s"))
        pct = "" if self.pbar.maximum() == 0 else "  %d%%" % int(min(99, p["shown"] * 100))
        self.lblProg.setText(text + pct)
        self.lblTime.setText("  |  ".join(extra + ["%d s" % int(now - p["t0"])]))

    # ------------------------------------------------------------ settings
    def _combo_set(self, cmb, key, default):
        v = self.st.value("scan/" + key, default)
        i = cmb.findData(v if cmb.itemData(0) is None or not isinstance(cmb.itemData(0), int) else int(v))
        cmb.setCurrentIndex(max(0, i))

    def _load_settings(self):
        try:
            self._combo_set(self.cmbMode, "mode", "normal")
            self._combo_set(self.cmbSize, "size", "max")
            self._combo_set(self.cmbDpi, "dpi", 600)
            self._combo_set(self.cmbFix, "fix", scanfix.BEST)
            self._combo_set(self.cmbCol, "color", "color")
            self._combo_set(self.cmbPage, "page", "A4")
            self._combo_set(self.cmbPlace, "place", "center")
            self.chkUi.setChecked(self.st.value("scan/ui", False, type=bool))
            self.chkFeed.setChecked(self.st.value("scan/feeder", False, type=bool))
            self.chkDuplex.setChecked(self.st.value("scan/duplex", False, type=bool))
            self.chkClose.setChecked(self.st.value("scan/autoclose", True, type=bool))
            self.chkPreview.setChecked(self.st.value("scan/preview", True, type=bool))
        except Exception:
            pass
        self._load_border()
        self._load_round()

    def _load_round(self):
        """The corner settings are shared with the Border tab (when no layer is selected it changes these)."""
        try:
            self.chkRound.setChecked(self.st.value("scan/round_on", True, type=bool))
            self.sldRound.setValue(max(5, min(idscan.ROUND_MAX, int(self.st.value("scan/round_pct", idscan.ROUND_PCT) or idscan.ROUND_PCT))))
        except Exception:
            pass
        self.lblRoundV.setText("%d%%" % self.sldRound.value())
        self._round_enable()

    def _round_enable(self, *_):
        self.sldRound.setEnabled(self.chkRound.isChecked())
        self._round_sample()

    def _round_sample(self):
        """The small card picture next to the slider: round corners as chosen, or square when Round is off."""
        self.sampleRound.set_pct(self._round())

    def _round(self):
        """How round the card corners are: 0 = square (switched off), else percent of a real card's corner."""
        return int(self.sldRound.value()) if self.chkRound.isChecked() else 0

    def _load_border(self):
        """The border settings are shared with the Border tab (when no layer is selected it changes these)."""
        try:
            self.chkBorder.setChecked(self.st.value("scan/border_on", True, type=bool))
            self._bcolor = str(self.st.value("scan/border_color", idscan.BORDER_COLOR, type=str) or idscan.BORDER_COLOR)
            self.sldBorder.setValue(max(1, min(40, int(self.st.value("scan/border_w", idscan.BORDER_W) or idscan.BORDER_W))))
        except Exception:
            pass
        self.lblBorderW.setText(str(self.sldBorder.value()))
        self._paint_border_color()
        self._border_enable()

    def _paint_border_color(self):
        self.btnBorderColor.setStyleSheet("background: %s; border-radius: 6px; border: 1px solid #4a5268;" % self._bcolor)

    def _border_enable(self, *_):
        on = self.chkBorder.isChecked()
        self.btnBorderColor.setEnabled(on)
        self.sldBorder.setEnabled(on)

    def _pick_border_color(self):
        c = QColorDialog.getColor(QColor(self._bcolor), self, "Border colour")
        if c.isValid():
            self._bcolor = c.name()
            self._paint_border_color()

    def _border(self):
        return idscan.make_border(self.chkBorder.isChecked(), self._bcolor, self.sldBorder.value())

    def _save_settings(self):
        for key, cmb in (("mode", self.cmbMode), ("size", self.cmbSize), ("dpi", self.cmbDpi), ("fix", self.cmbFix), ("color", self.cmbCol),
                         ("page", self.cmbPage), ("place", self.cmbPlace)):
            self.st.setValue("scan/" + key, cmb.currentData())
        self.st.setValue("scan/ui", self.chkUi.isChecked())
        self.st.setValue("scan/feeder", self.chkFeed.isChecked())
        self.st.setValue("scan/duplex", self.chkDuplex.isChecked())
        self.st.setValue("scan/autoclose", self.chkClose.isChecked())
        self.st.setValue("scan/preview", self.chkPreview.isChecked())
        self.st.setValue("scan/border_on", self.chkBorder.isChecked())
        self.st.setValue("scan/border_color", self._bcolor)
        self.st.setValue("scan/border_w", int(self.sldBorder.value()))
        self.st.setValue("scan/round_on", self.chkRound.isChecked())
        self.st.setValue("scan/round_pct", int(self.sldRound.value()))
        d = self.cmbDev.currentData()
        if d:
            self.st.setValue("scan/device", d["kind"] + "|" + d["id"])

    # ------------------------------------------------------------ show
    def show_front(self):
        first = not self.isVisible()
        if first:
            self._load_border()               # the Border tab may have changed the shared settings meanwhile
        self.show()
        self._fit_screen()
        self.raise_()
        self.activateWindow()
        if first and self.cmbDev.count() == 0 and not self._procs:
            self.refresh()

    def closeEvent(self, e):
        self._save_settings()
        self._ptimer.stop()
        if self._proc is not None:
            self._cancel()
        super().closeEvent(e)

    def _auto_close(self, delay=900):
        """The work is completely finished (pictures are in Image Fixer): close this window after a short moment, so the
        message can still be read.  Never while a new scan / job is running."""
        if self.chkClose.isChecked():
            QTimer.singleShot(delay, self._close_if_idle)

    def _close_if_idle(self):
        if self.isVisible() and not self._busy and self._proc is None and self._p is None:
            self.close()

    # ------------------------------------------------------------ finding scanners
    def refresh(self):
        if self._busy or self._procs:
            return
        self.cmbDev.clear()
        self._found, self._errors = [], []
        self.lblFound.setText("Looking for scanners ...")
        self.btnRefresh.setEnabled(False)
        self.btnScan.setEnabled(False)
        wia_done = False
        for bits in (64, 32):
            cmd = helper_command(bits)
            if cmd is None:
                continue
            out = os.path.join(self.work, "list%d_%d.jsonl" % (bits, int(time.time() * 1000) % 100000))
            p = QProcess(self)
            args = cmd[1] + ["list", "--out", out, "--wia", "0" if wia_done else "1"]
            wia_done = True
            p.finished.connect(lambda code, status, p=p, out=out, bits=bits: self._list_done(p, out, bits))
            p.errorOccurred.connect(lambda err, p=p, out=out, bits=bits: self._list_failed(p, out, bits))
            p.start(cmd[0], args)
            self._procs.append(p)
        if not self._procs:
            self._finish_list(["The scan helper program was not found (it is built together with Image Fixer)."])

    def _list_done(self, p, out, bits):
        if p not in self._procs:
            return
        self._procs.remove(p)
        got = False
        for ev in read_events(out):
            if ev.get("event") == "list":
                got = True
                self._found.extend(ev.get("sources", []))
                self._errors.extend(ev.get("errors", []))
            elif ev.get("event") == "error":
                got = True
                self._errors.append("%d-bit helper: %s" % (bits, ev.get("message", "")))
        if not got:
            self._errors.append("%d-bit helper gave no answer" % bits)
        p.deleteLater()
        if not self._procs:
            self._finish_list(None)

    def _list_failed(self, p, out, bits):
        if p in self._procs and p.state() == QProcess.ProcessState.NotRunning:
            self._procs.remove(p)
            self._errors.append("%d-bit helper could not start" % bits)
            p.deleteLater()
            if not self._procs:
                self._finish_list(None)

    def _finish_list(self, errors):
        if errors:
            self._errors.extend(errors)
        want = self.st.value("scan/device", "", type=str)
        sel = 0
        self.cmbDev.clear()
        for s in self._found:
            tag = "WIA" if s["kind"] == "wia" else "TWAIN %d-bit" % s.get("bits", 0)
            self.cmbDev.addItem("%s   [%s]" % (s["name"], tag), s)
            if want == s["kind"] + "|" + s["id"]:
                sel = self.cmbDev.count() - 1
        if self._found:
            self.cmbDev.setCurrentIndex(sel)
        n = len(self._found)
        txt = ("%d scanner%s found." % (n, "" if n == 1 else "s")) if n else \
            "No scanner found. Switch it on, connect it, install its driver, then press 'Find scanners'."
        if self._errors:
            txt += "  Notes: " + "  |  ".join(self._errors)
        self.lblFound.setText(txt)
        self.btnRefresh.setEnabled(True)
        self.btnScan.setEnabled(n > 0)

    # ------------------------------------------------------------ mode
    def _id_mode(self):
        return self.cmbMode.currentData() == "id"

    def _mode_changed(self, *_):
        idm = self._id_mode()
        for w in self._idrows:
            w.setVisible(idm)
        self.chkDuplex.setVisible(self.chkFeed.isChecked())
        self.btnAgain.setVisible(idm and self._front is not None)
        self._update_step()
        self._fit_screen()

    def _fit_screen(self):
        """Open at the height the content needs, but never taller / wider than the screen this window is on."""
        scr = (self.screen() if self.isVisible() else None) or self.win.screen() or QApplication.primaryScreen()
        ag = scr.availableGeometry()
        maxh, maxw = int(ag.height() * 0.92), int(ag.width() * 0.92)
        self.setMinimumWidth(min(820, maxw))
        self.setMaximumSize(maxw, maxh)
        fixed = self.sizeHint().height() - self._scroll.sizeHint().height()
        want = fixed + self._body.sizeHint().height() + 6
        w = min(max(self.minimumWidth(), self.width() if self.isVisible() else 860), maxw)
        self.resize(w, min(want, maxh))
        fg = self.frameGeometry()                       # keep the whole window inside the screen
        x = min(max(fg.x(), ag.x()), max(ag.x(), ag.right() - fg.width()))
        y = min(max(fg.y(), ag.y()), max(ag.y(), ag.bottom() - fg.height()))
        self.move(x, y)

    def _both_in_one(self):
        return self._id_mode() and self.chkFeed.isChecked() and self.chkDuplex.isChecked()

    def _update_step(self):
        if self._busy:
            return
        if not self._id_mode():
            self.lblStep.setText("")
            self.btnScan.setText("Scan")
        elif self._both_in_one():
            self.lblStep.setText("Put the card in the feeder. One scan gives the front and the back.")
            self.btnScan.setText("Scan front + back")
        elif self._front is None:
            self.lblStep.setText("Step 1 of 2: put the FRONT of the card on the glass.")
            self.btnScan.setText("Scan FRONT")
        else:
            self.lblStep.setText("Front is done. Step 2 of 2: turn the card over - put the BACK on the glass.")
            self.btnScan.setText("Scan BACK")
        self.btnAgain.setVisible(self._id_mode() and self._front is not None)

    def _reset_id(self):
        self._front = None
        self._front_side = None
        self.lblMsg.setText("")
        self._update_step()

    def keyPressEvent(self, e):
        if e.key() in (Qt.Key.Key_Return, Qt.Key.Key_Enter) and self.btnScan.isEnabled():
            self._scan_clicked()
            return
        super().keyPressEvent(e)

    # ------------------------------------------------------------ scanning
    def _busy_set(self, busy):
        self._busy = busy
        for w in (self.cmbDev, self.btnRefresh, self.cmbMode, self.cmbSize, self.cmbDpi, self.cmbCol, self.chkUi,
                  self.chkFeed, self.chkDuplex, self.chkClose, self.cmbPage, self.cmbPlace, self.btnAgain,
                  self.chkBorder, self.chkRound, self.chkPreview, self.cmbFix):
            w.setEnabled(not busy)
        if not busy:
            self._border_enable()
            self._round_enable()
        else:
            self.sldRound.setEnabled(False)
            self.btnBorderColor.setEnabled(False)
            self.sldBorder.setEnabled(False)
        if busy:
            self.btnScan.setText("Cancel")
            self.btnScan.setEnabled(True)
        else:
            self.btnScan.setEnabled(self.cmbDev.count() > 0)
            self._update_step()

    def _scan_clicked(self):
        if self._busy:
            self._cancel()
            return
        dev = self.cmbDev.currentData()
        if not dev:
            self.lblMsg.setText("No scanner selected. Press 'Find scanners'.")
            return
        cmd = helper_command(int(dev.get("bits", 64)))
        if cmd is None:
            self.lblMsg.setText("The %d-bit scan helper was not found." % int(dev.get("bits", 64)))
            return
        self._save_settings()
        both = self._both_in_one()
        feeder = self.chkFeed.isChecked()
        pages = 2 if both else (50 if (feeder and not self._id_mode()) else 1)
        self._n += 1
        out = os.path.join(self.work, "scan%d.jsonl" % self._n)
        shots = os.path.join(self.work, "p%d" % self._n)
        args = cmd[1] + ["scan", "--kind", dev["kind"], "--source", dev["id"], "--dir", shots, "--out", out,
                         "--dpi", str(self.cmbDpi.currentData()), "--mode", self.cmbCol.currentData(),
                         "--size", self.cmbSize.currentData(), "--ui", "1" if self.chkUi.isChecked() else "0",
                         "--feeder", "1" if feeder else "0", "--duplex", "1" if (feeder and self.chkDuplex.isChecked()) else "0",
                         "--max-pages", str(pages)]
        p = QProcess(self)
        p.finished.connect(lambda code, status, p=p, out=out: self._scan_done(p, out))
        p.errorOccurred.connect(lambda err, p=p, out=out: self._scan_failed(p, out))
        self._proc = p
        self._cancelled = False
        self._busy_set(True)
        self.lblMsg.setText("(if the scanner's own window opens, use it; this window waits)" if self.chkUi.isChecked() else "")
        self._prog_start(out)
        p.start(cmd[0], args)

    def _cancel(self):
        p = self._proc
        self._cancelled = True
        if p is not None:
            try:
                p.kill()
            except Exception:
                pass

    def _scan_failed(self, p, out):
        if p is self._proc and p.state() == QProcess.ProcessState.NotRunning and not os.path.exists(out):
            self._proc = None
            p.deleteLater()
            self._busy_set(False)
            self._prog_hide()
            self.lblMsg.setText("The scan helper could not start.")

    def _scan_done(self, p, out):
        if p is not self._proc:
            return
        self._proc = None
        p.deleteLater()
        events = read_events(out)
        pages = [e["path"] for e in events if e.get("event") == "page" and os.path.exists(e.get("path", ""))]
        err = next((e for e in events if e.get("event") == "error"), None)
        notes = [e["message"] for e in events if e.get("event") == "note"]
        self._busy_set(False)
        if self._cancelled and not pages:
            self._prog_hide()
            self.lblMsg.setText("Cancelled.")
            return
        if not pages:
            self._prog_hide()
            if any(e.get("event") == "cancelled" for e in events):
                self.lblMsg.setText("Cancelled in the scanner window.")
                return
            msg = (err or {}).get("message") or "The scanner gave no picture."
            self.lblMsg.setText("Scan failed: %s" % msg)
            if err and err.get("trace"):
                try:
                    with open(os.path.join(os.path.dirname(self.work), "scan_error.log"), "a", encoding="utf-8") as f:
                        f.write(err["trace"] + "\n" + "-" * 60 + "\n")
                except Exception:
                    pass
            return
        if notes:
            self.lblMsg.setText("Done. Notes: " + "; ".join(notes[:4]))
        if self._p is not None:
            self._p["pages"] = len(pages)
            self._prog_stage("process")
            self._p["shown"] = max(self._p["shown"], 0.78)
        self._got_pages(pages)

    # ------------------------------------------------------------ what to do with the pictures
    def _got_pages(self, pages):
        dpi = int(self.cmbDpi.currentData())
        fix = self.cmbFix.currentData()
        if not self._id_mode():
            self._to_png(pages, dpi, fix)
            return
        self.lblMsg.setText("Cutting the card out ...")
        both = self._both_in_one()
        want_back = self._front_side is not None

        def work(progress):
            sides = []
            for i, p in enumerate(pages):
                progress(i / len(pages), "cutting the card out")
                sides.append(build_side(p, dpi, fix, None,
                                        lambda f, t="", i=i: progress((i + 0.3 + 0.7 * f) / len(pages), t)))
            return sides

        def done(sides):
            if both and len(sides) >= 2:
                self._review(sides[:2], "both")
            elif both:
                self._front_side = sides[0]
                self._front = sides[0]["card"]
                self._prog_hide(600)        # the work is finished: stop the bar / timer
                self.lblMsg.setText("The scanner gave only 1 page. Scan the BACK now.")
                self._update_step()
            elif not want_back:
                self._review([sides[0]], "front")
            else:
                self._review([self._front_side, sides[0]], "back")
        jobs.run_background(work, done, lambda m: self._fail(m), self._prog_proc)

    # ------------------------------------------------------------ preview / corner crop / OK
    def _review(self, sides, stage):
        """stage: "front" (only the front is done), "back" (front kept + the back just scanned), "both" (duplex)."""
        self._prog_hide(600)                      # the work is finished: stop the bar / timer
        if not self.chkPreview.isChecked():       # preview switched off: everything is accepted at once
            self._review_ok(sides, stage)
            return
        self._show_preview(sides, stage)

    def _show_preview(self, sides, stage):
        names = ["Front"] if stage == "front" else ["Front", "Back"]
        image = idscan.preview_image([(s["card"], s["found"]) for s in sides], self._border(), round_pct=self._round())
        lost = [n for n, s in zip(names, sides) if not s["found"]]
        text = {"front": "FRONT: this is how it will look on the page. OK, then you scan the BACK.",
                "back": "FRONT and BACK: this is how they will look. OK puts them together on one page.",
                "both": "FRONT and BACK: this is how they will look. OK puts them together on one page."}[stage]
        if lost:
            text += "\n(The card was not found in the scan of: %s. Use Corner crop to mark it.)" % ", ".join(lost)
        dlg = idpreview.PreviewDialog(self, "ID card preview", text, image, names,
                                      cards=[(s["card"], s["found"]) for s in sides],
                                      border={"on": self.chkBorder.isChecked(), "color": self._bcolor, "width": int(self.sldBorder.value())},
                                      rnd={"on": self.chkRound.isChecked(), "pct": int(self.sldRound.value())})
        dlg.exec()
        self._take_look(dlg)                         # border / corners changed in the preview are used for the page, too
        act = dlg.action
        if act == "ok":
            self._review_ok(sides, stage)
        elif isinstance(act, tuple):
            self._crop_side(sides, stage, act[1])
        else:
            self._review_again(stage)

    def _take_look(self, dlg):
        """Border and corner settings that were changed in the preview window become the settings of this window."""
        try:
            self.chkBorder.setChecked(bool(dlg.border["on"]))
            self._bcolor = str(dlg.border["color"])
            self.sldBorder.setValue(int(dlg.border["width"]))
            self.lblBorderW.setText(str(self.sldBorder.value()))
            self._paint_border_color()
            self._border_enable()
            self.chkRound.setChecked(bool(dlg.rnd["on"]))
            self.sldRound.setValue(int(dlg.rnd["pct"]))
            self._round_enable()
        except Exception:
            pass

    def _crop_side(self, sides, stage, i):
        """Corner crop: the person drags the 4 corners onto the card; the side is cut again from the original scan."""
        from PIL import Image
        s = sides[i]
        try:
            im = Image.open(s["path"])
            im.load()
            quad = s.get("quad")
            if quad is None:
                quad, _ = idscan.initial_quad(im, s["dpi"])
            dlg = idpreview.CornerDialog(self, im, [tuple(p) for p in quad],
                                         "Corner crop: " + ("Front" if i == 0 else "Back"))
            if dlg.exec() != QDialog.DialogCode.Accepted:
                self._show_preview(sides, stage)
                return
        except Exception as ex:
            self.lblMsg.setText("Corner crop failed: %s" % ex)
            self._show_preview(sides, stage)
            return
        q = dlg.quad()
        fix = self.cmbFix.currentData()
        self.lblMsg.setText("Cutting the card with your corners ...")

        def work(progress):
            return build_side(s["path"], s["dpi"], fix, q, progress)

        def done(ns):
            sides[i] = ns
            if i == 0 and stage == "back":                # the kept front changed
                self._front_side, self._front = ns, ns["card"]
            self.lblMsg.setText("")
            self._show_preview(sides, stage)
        jobs.run_background(work, done, lambda m: self._fail(m), self._prog_proc)

    def _review_again(self, stage):
        if stage == "back":                                # the front stays, only the back is scanned again
            self.lblMsg.setText("Scan the BACK again.")
        else:
            self._front = None
            self._front_side = None
            self.lblMsg.setText("Scan the FRONT again." if stage == "front" else "Scan the card again.")
        self._update_step()
        self._mode_changed()

    def _review_ok(self, sides, stage):
        if stage == "front":
            self._front_side = sides[0]
            self._front = sides[0]["card"]
            self.lblMsg.setText("Front scanned%s." % ("" if sides[0]["found"] else " (the card was not found, the whole scan is used)"))
            self._update_step()
            self._mode_changed()
            return
        dpi = int(round(max(s["eff"] for s in sides)))           # the page is made at the dpi of the (enlarged) cards
        page, place = self.cmbPage.currentData(), self.cmbPlace.currentData()
        self._front = None
        self._front_side = None
        self._finish_id(sides[0]["card"], sides[1]["card"], dpi, page, place, all(s["found"] for s in sides))

    def _fail(self, msg):
        self._prog_hide()
        self.lblMsg.setText("Could not read the scan: %s" % (msg.splitlines()[-1] if msg else ""))
        self._update_step()

    def _finish_id(self, front, back, dpi, page, place, all_found):
        border = self._border()                                  # colour + thickness chosen now (changeable later: Border tab)
        rpct = self._round()                                     # corner roundness chosen now (0 = square; changeable later: Border tab)

        def work(progress):
            progress(0.9, "putting the page together")
            pg, items = idscan.compose_page(front, back, dpi, page, place, rounded=all_found and rpct > 0, round_pct=rpct)
            path = os.path.join(self.work, "idcard_%s.png" % time.strftime("%H%M%S"))
            pg.save(path, compress_level=1)
            return path, items

        def done(r):
            path, items = r
            self.win.scan_id_page(path, items, border, rpct)
            self._prog_hide(1200)
            self.lblMsg.setText("ID card page is ready (front and back are two layers)." +
                                ("" if all_found else "  Note: a card was not found in a scan, the whole scan was used."))
            self._update_step()
            self._mode_changed()
            self._auto_close()
        jobs.run_background(work, done, lambda m: self._fail(m), self._prog_proc)

    def _to_png(self, pages, dpi=300, fix=None):
        fix = fix or scanfix.OFF

        def work(progress):
            from PIL import Image
            outs = []
            for i, p in enumerate(pages):
                progress(i / len(pages), "page %d / %d" % (i + 1, len(pages)))
                im = Image.open(p)
                im.load()
                im, _ = scanfix.enhance(im, dpi, fix, lambda f, t="", i=i: progress((i + f) / len(pages),
                                                                                    "page %d / %d" % (i + 1, len(pages))))
                q = os.path.join(self.work, "scan_%s_%d.png" % (time.strftime("%H%M%S"), i + 1))
                im.convert("RGB").save(q, compress_level=1)   # temporary file: fast saving matters more than size
                outs.append(q)
            return outs

        def done(outs):
            self.win.scan_received(outs)
            self._prog_hide(1200)
            self.lblMsg.setText("%d page%s added." % (len(outs), "" if len(outs) == 1 else "s"))
            self._auto_close()
        jobs.run_background(work, done, lambda m: self._fail(m), self._prog_proc)
