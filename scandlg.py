"""'Scan' window: finds the scanners (TWAIN and WIA, 32-bit and 64-bit drivers) and scans into Image Fixer.
Two ways: Normal (one picture per page) and ID card (FRONT, then BACK -> one page, each side its own layer).
The real scanning is done by scanhelper (a separate small program, a 32-bit and a 64-bit copy), see scanhelper.py."""
import json
import os
import shutil
import sys
import tempfile
import time

from PySide6.QtCore import QProcess, QTimer, Qt
from PySide6.QtWidgets import (QCheckBox, QComboBox, QDialog, QFormLayout, QHBoxLayout, QLabel, QPushButton, QVBoxLayout)

import idscan
import jobs

SIZES = (("Auto (the scanner decides the paper size)", "max"), ("A4", "a4"), ("A5", "a5"), ("A3", "a3"), ("B5", "b5"),
         ("Letter", "letter"), ("Legal", "legal"))
MODES = (("Color", "color"), ("Gray", "gray"), ("Black and white", "bw"))
SHORTCUT = "Ctrl+Shift+N"


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


class ScanDialog(QDialog):
    def __init__(self, win):
        super().__init__(win)
        self.win = win
        self.st = win.settings
        self.setWindowTitle("Scan")
        self.setModal(False)
        self.setMinimumWidth(500)
        self.work = tempfile.mkdtemp(prefix="scan_", dir=win.tmp)
        self._procs = []                  # running list jobs
        self._proc = None                 # the running scan
        self._front = None                # ID card: the FRONT picture while we wait for the BACK
        self._busy = False
        self._n = 0
        self._found = []
        self._errors = []

        root = QVBoxLayout(self)
        root.setSpacing(10)
        title = QLabel("Scan from a scanner")
        title.setStyleSheet("font-size: 15px; font-weight: 700;")
        root.addWidget(title)

        form = QFormLayout()
        form.setSpacing(8)
        row = QHBoxLayout()
        self.cmbDev = QComboBox()
        self.cmbDev.setMinimumWidth(300)
        self.btnRefresh = QPushButton("Find scanners")
        self.btnRefresh.setToolTip("Look for TWAIN and WIA scanners again")
        self.btnRefresh.clicked.connect(self.refresh)
        row.addWidget(self.cmbDev, 1)
        row.addWidget(self.btnRefresh)
        form.addRow("Scanner", row)
        self.lblFound = QLabel("")
        self.lblFound.setObjectName("muted")
        self.lblFound.setWordWrap(True)
        form.addRow("", self.lblFound)

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

        self.chkUi = QCheckBox("Show the scanner's own window (driver settings)")
        self.chkUi.setToolTip("Like Photoshop: the driver of the scanner opens its own window to choose size, preview ...")
        self.chkFeed = QCheckBox("Use the document feeder (ADF) instead of the glass")
        self.chkDuplex = QCheckBox("Feeder scans both sides (duplex)")
        self.chkDuplex.setToolTip("ID card: one scan gives front and back together")
        self.chkFeed.toggled.connect(self._mode_changed)
        form.addRow("", self.chkUi)
        form.addRow("", self.chkFeed)
        form.addRow("", self.chkDuplex)

        self.cmbPage = QComboBox()
        for p in idscan.PAGES_MM:
            self.cmbPage.addItem(p, p)
        self.cmbPlace = QComboBox()
        self.cmbPlace.addItem("centre of the page", "center")
        self.cmbPlace.addItem("top of the page", "top")
        r3 = QHBoxLayout()
        r3.addWidget(self.cmbPage)
        r3.addWidget(self.cmbPlace)
        self.lblPage = QLabel("ID card page")
        form.addRow(self.lblPage, r3)
        self._r3 = (self.cmbPage, self.cmbPlace)
        root.addLayout(form)

        self.lblStep = QLabel("")
        self.lblStep.setWordWrap(True)
        self.lblStep.setStyleSheet("font-weight: 600; color: #a5b4fc;")
        root.addWidget(self.lblStep)
        self.lblMsg = QLabel("")
        self.lblMsg.setObjectName("muted")
        self.lblMsg.setWordWrap(True)
        root.addWidget(self.lblMsg)

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

    # ------------------------------------------------------------ settings
    def _combo_set(self, cmb, key, default):
        v = self.st.value("scan/" + key, default)
        i = cmb.findData(v if cmb.itemData(0) is None or not isinstance(cmb.itemData(0), int) else int(v))
        cmb.setCurrentIndex(max(0, i))

    def _load_settings(self):
        try:
            self._combo_set(self.cmbMode, "mode", "normal")
            self._combo_set(self.cmbSize, "size", "max")
            self._combo_set(self.cmbDpi, "dpi", 300)
            self._combo_set(self.cmbCol, "color", "color")
            self._combo_set(self.cmbPage, "page", "A4")
            self._combo_set(self.cmbPlace, "place", "center")
            self.chkUi.setChecked(self.st.value("scan/ui", False, type=bool))
            self.chkFeed.setChecked(self.st.value("scan/feeder", False, type=bool))
            self.chkDuplex.setChecked(self.st.value("scan/duplex", False, type=bool))
        except Exception:
            pass

    def _save_settings(self):
        for key, cmb in (("mode", self.cmbMode), ("size", self.cmbSize), ("dpi", self.cmbDpi), ("color", self.cmbCol),
                         ("page", self.cmbPage), ("place", self.cmbPlace)):
            self.st.setValue("scan/" + key, cmb.currentData())
        self.st.setValue("scan/ui", self.chkUi.isChecked())
        self.st.setValue("scan/feeder", self.chkFeed.isChecked())
        self.st.setValue("scan/duplex", self.chkDuplex.isChecked())
        d = self.cmbDev.currentData()
        if d:
            self.st.setValue("scan/device", d["kind"] + "|" + d["id"])

    # ------------------------------------------------------------ show
    def show_front(self):
        first = not self.isVisible()
        self.show()
        self.raise_()
        self.activateWindow()
        if first and self.cmbDev.count() == 0 and not self._procs:
            self.refresh()

    def closeEvent(self, e):
        self._save_settings()
        if self._proc is not None:
            self._cancel()
        super().closeEvent(e)

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
        for w in (self.cmbPage, self.cmbPlace, self.lblPage):
            w.setVisible(idm)
        self.chkDuplex.setVisible(self.chkFeed.isChecked())
        self.btnAgain.setVisible(idm and self._front is not None)
        self._update_step()
        self.adjustSize()

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
                  self.chkFeed, self.chkDuplex, self.cmbPage, self.cmbPlace, self.btnAgain):
            w.setEnabled(not busy)
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
        self.lblMsg.setText("Scanning ... (if the scanner's window opened, use it; this window waits)")
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
            self.lblMsg.setText("Cancelled.")
            return
        if not pages:
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
        self._got_pages(pages)

    # ------------------------------------------------------------ what to do with the pictures
    def _got_pages(self, pages):
        dpi = int(self.cmbDpi.currentData())
        if not self._id_mode():
            self._to_png(pages)
            return
        self.lblMsg.setText("Cutting the card out ...")
        front, back = self._front, None
        both = self._both_in_one()
        page, place = self.cmbPage.currentData(), self.cmbPlace.currentData()
        want_back = front is not None

        def work(progress):
            from PIL import Image
            imgs = [Image.open(p) for p in pages]
            for im in imgs:
                im.load()
            cards = [idscan.cut_card(im, dpi) for im in imgs]
            return cards

        def done(cards):
            nonlocal front, back
            found = [f for _, f in cards]
            if both and len(cards) >= 2:
                front, back = cards[0][0], cards[1][0]
            elif both:
                front = cards[0][0]
                self._front = front
                self.lblMsg.setText("The scanner gave only 1 page. Scan the BACK now.")
                self._update_step()
                return
            elif not want_back:
                self._front = cards[0][0]
                self.lblMsg.setText("Front scanned%s." % ("" if found[0] else " (the card was not found, the whole scan is used)"))
                self._update_step()
                self._mode_changed()
                return
            else:
                back = cards[0][0]
            self._front = None
            self._finish_id(front, back, dpi, page, place, all(found))
        jobs.run_background(work, done, lambda m: self._fail(m))

    def _fail(self, msg):
        self.lblMsg.setText("Could not read the scan: %s" % (msg.splitlines()[-1] if msg else ""))
        self._update_step()

    def _finish_id(self, front, back, dpi, page, place, all_found):
        def work(progress):
            pg, items = idscan.compose_page(front, back, dpi, page, place)
            path = os.path.join(self.work, "idcard_%s.png" % time.strftime("%H%M%S"))
            pg.save(path)
            return path, items

        def done(r):
            path, items = r
            self.win.scan_id_page(path, items)
            self.lblMsg.setText("ID card page is ready (front and back are two layers)." +
                                ("" if all_found else "  Note: a card was not found in a scan, the whole scan was used."))
            self._update_step()
            self._mode_changed()
        jobs.run_background(work, done, lambda m: self._fail(m))

    def _to_png(self, pages):
        def work(progress):
            from PIL import Image
            outs = []
            for i, p in enumerate(pages):
                im = Image.open(p)
                im.load()
                q = os.path.join(self.work, "scan_%s_%d.png" % (time.strftime("%H%M%S"), i + 1))
                im.convert("RGB").save(q)
                outs.append(q)
            return outs

        def done(outs):
            self.win.scan_received(outs)
            self.lblMsg.setText("%d page%s added." % (len(outs), "" if len(outs) == 1 else "s"))
        jobs.run_background(work, done, lambda m: self._fail(m))
