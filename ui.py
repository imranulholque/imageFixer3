"""Main window: header, slide-able cards, thumbnail strip, tray, drag & drop, saving."""
import os
import tempfile
import time

from PIL import Image, ImageOps
from PySide6.QtCore import (QEasingCurve, QEvent, QItemSelectionModel, QObject, QRect, QRunnable, QSettings, QSize, Qt, QThreadPool, QTimer,
                            QUrl, QVariantAnimation, Signal)
from PySide6.QtCore import QMimeData
from PySide6.QtGui import (QAction, QActionGroup, QColor, QDesktopServices, QDrag, QFontMetrics, QIcon, QImage,
                           QKeySequence, QPainter, QPixmap, QShortcut, QTransform)
from PySide6.QtWidgets import (QAbstractItemView, QApplication, QCheckBox, QDialog, QFileDialog, QFrame, QHBoxLayout, QInputDialog, QLabel,
                               QListView, QListWidget, QListWidgetItem, QMainWindow, QMenu, QMessageBox, QProgressBar,
                               QPushButton, QSizePolicy, QStackedLayout, QSystemTrayIcon, QVBoxLayout, QWidget)

import core
import updater
import winutil
from card import EXPORT_DIR, Card
from savedlg import SaveAllDialog
from jobs import JobRunner
from updatedlg import UpdateManager
from theme import logo_pixmap, make_icon, scaled_qss

FILTER = "Pictures and PDF (%s);;All files (*)" % " ".join("*" + e for e in sorted(core.IMG_EXT | {core.PDF_EXT}))


# --------------------------------------------------------------- thumbnails
class ThumbSignals(QObject):
    done = Signal(str, QImage)


class ThumbJob(QRunnable):
    def __init__(self, uid, path, signals):
        super().__init__()
        self.uid, self.path, self.signals = uid, path, signals

    def run(self):
        try:
            im = Image.open(self.path)
            try:
                im.draft("RGB", (400, 400))
            except Exception:
                pass
            im = ImageOps.exif_transpose(im)
            if im.mode in ("RGBA", "LA", "P"):
                rgba = im.convert("RGBA")
                bg = Image.new("RGB", rgba.size, (255, 255, 255))
                bg.paste(rgba, mask=rgba.split()[3])
                im = bg
            im = im.convert("RGB")
            im.thumbnail((180, 180))
            w, h = im.size
            q = QImage(im.tobytes(), w, h, w * 3, QImage.Format.Format_RGB888).copy()
            self.signals.done.emit(self.uid, q)
        except Exception:
            pass


class Strip(QListWidget):
    """Thumbnails, selected like files in Explorer: click, Ctrl+click, Shift+click, drag a box, Ctrl+A.
    Delete removes the selected pictures, Ctrl+C copies them, dragging them out gives the files (with their edits)
    to other programs."""
    removeRequested = Signal(list)      # uids of the selected pictures
    copyRequested = Signal(list)

    def __init__(self):
        super().__init__()
        self.provider = None            # provider(list of uids) -> QMimeData
        self.preparer = None            # preparer(uid): make the edited file in the background
        self._press = None
        self._press_item = None
        self.setMouseTracking(True)
        self.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        self.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.customContextMenuRequested.connect(self._context_menu)
        self.itemEntered.connect(lambda it: self.preparer(it.data(Qt.ItemDataRole.UserRole)) if self.preparer else None)

    def uids(self):
        """Selected pictures, in the order of the strip."""
        return [self.item(r).data(Qt.ItemDataRole.UserRole) for r in range(self.count()) if self.item(r).isSelected()]

    # ---- keys.  The window has its own Delete / Ctrl+A / Ctrl+C (for the picture): while the thumbnails have the
    # focus (after a click on them) these keys belong to the thumbnails.
    def event(self, e):
        if e.type() == QEvent.Type.ShortcutOverride:
            k = e.key()
            ctrl = bool(e.modifiers() & Qt.KeyboardModifier.ControlModifier)
            if k in (Qt.Key.Key_Delete, Qt.Key.Key_Backspace) or (ctrl and k in (Qt.Key.Key_A, Qt.Key.Key_C)):
                e.accept()
                return True
        return super().event(e)

    def keyPressEvent(self, e):
        k = e.key()
        ctrl = bool(e.modifiers() & Qt.KeyboardModifier.ControlModifier)
        if k in (Qt.Key.Key_Delete, Qt.Key.Key_Backspace):
            u = self.uids()
            if u:
                self.removeRequested.emit(u)
            e.accept()
            return
        if ctrl and k == Qt.Key.Key_A:
            self.selectAll()
            e.accept()
            return
        if ctrl and k == Qt.Key.Key_C:
            u = self.uids()
            if u:
                self.copyRequested.emit(u)
            e.accept()
            return
        super().keyPressEvent(e)

    def _context_menu(self, pos):
        it = self.itemAt(pos)
        if it is not None and not it.isSelected():
            self.clearSelection()
            it.setSelected(True)
        u = self.uids()
        n = len(u)
        what = "this picture" if n == 1 else "%d pictures" % n
        m = QMenu(self)
        a_rm = m.addAction("Remove %s from the list      Del" % what)
        a_cp = m.addAction("Copy %s      Ctrl+C" % what)
        a_rm.setEnabled(n > 0)
        a_cp.setEnabled(n > 0)
        m.addSeparator()
        a_all = m.addAction("Select all      Ctrl+A")
        act = m.exec(self.viewport().mapToGlobal(pos))
        if act is a_rm:
            self.removeRequested.emit(u)
        elif act is a_cp:
            self.copyRequested.emit(u)
        elif act is a_all:
            self.selectAll()

    # ---- mouse: Qt does the selection; we start the drag ourselves
    def mousePressEvent(self, e):
        self._press = e.position().toPoint() if e.button() == Qt.MouseButton.LeftButton else None
        self._press_item = self.itemAt(e.position().toPoint())
        super().mousePressEvent(e)
        if self._press is not None and self.preparer is not None:
            for u in self.uids():                       # the edited files are made now, so a drag can start at once
                self.preparer(u)
            if self._press_item is not None:
                self.preparer(self._press_item.data(Qt.ItemDataRole.UserRole))

    def mouseMoveEvent(self, e):
        """Our own drag start (Qt's list drag does not start in this layout): press a thumbnail, move a little."""
        if (self._press is not None and self._press_item is not None and (e.buttons() & Qt.MouseButton.LeftButton)
                and (e.position().toPoint() - self._press).manhattanLength() >= QApplication.startDragDistance()):
            it = self._press_item
            self._press = None
            self._press_item = None
            self._drag_items(it)
            return
        super().mouseMoveEvent(e)

    def mouseReleaseEvent(self, e):
        self._press = None
        self._press_item = None
        super().mouseReleaseEvent(e)

    def _drag_pixmap(self, it, n):
        ic = it.icon()
        pm = ic.pixmap(96, 96) if not ic.isNull() else QPixmap()
        if pm.isNull():
            pm = QPixmap(96, 96)
            pm.fill(QColor("#222836"))
        if n > 1:                                       # a badge with the number of pictures that are dragged
            pm = pm.copy()
            p = QPainter(pm)
            p.setRenderHint(QPainter.RenderHint.Antialiasing, True)
            r = QRect(max(0, pm.width() - 36), 0, 36, 26)
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(QColor("#6366f1"))
            p.drawRoundedRect(r, 13, 13)
            p.setPen(QColor("white"))
            p.drawText(r, int(Qt.AlignmentFlag.AlignCenter), str(n))
            p.end()
        return pm

    def _drag_items(self, it):
        """Always COPY: a move would take the user's original file away.  Dragging one of several selected
        thumbnails takes all of them; dragging an unselected one takes only that one."""
        if self.provider is None:
            return
        uids = self.uids() if it.isSelected() else [it.data(Qt.ItemDataRole.UserRole)]
        try:
            md = self.provider(uids)
        except Exception:
            md = None
        if md is None:
            return
        d = QDrag(self)
        d.setMimeData(md)
        pm = self._drag_pixmap(it, len(uids))
        d.setPixmap(pm)
        d.setHotSpot(pm.rect().center())
        d.exec(Qt.DropAction.CopyAction, Qt.DropAction.CopyAction)

    def startDrag(self, supportedActions):
        pass

    def mimeData(self, items):
        if self.provider is not None and items:
            try:
                md = self.provider([i.data(Qt.ItemDataRole.UserRole) for i in items])
            except Exception:
                md = None
            if md is not None:
                return md
        return super().mimeData(items)


# ---------------------------------------------------------------- carousel
class Carousel(QWidget):
    """Cards side by side, each as big as the window; slides smoothly between them."""
    currentChanged = Signal(int)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.cards = []
        self._pos = 0.0
        self._idx = 0
        self._wheel = 0
        self._last_wheel = 0.0
        self._anim = QVariantAnimation(self)
        self._anim.setDuration(260)
        self._anim.setEasingCurve(QEasingCurve.Type.OutCubic)
        self._anim.valueChanged.connect(self._set_pos)

    @property
    def index(self):
        return self._idx

    def add_card(self, card):
        card.setParent(self)
        self.cards.append(card)
        card.hide()
        self._layout()

    def remove_card(self, card):
        self.remove_cards([card])

    def remove_cards(self, cards):
        """Several pictures at once: one re-layout, one signal.  The picture next to the removed place is shown."""
        gone = [c for c in cards if c in self.cards]
        if not gone:
            return
        old_idx = self._idx
        removed_before = sum(1 for c in gone if self.cards.index(c) < old_idx)
        cur_removed = self.cards[old_idx] in gone if self.cards else False
        for c in gone:
            self.cards.remove(c)
            if hasattr(c, "dispose"):
                c.dispose()
            c.hide()
            c.setParent(None)
            c.deleteLater()
        self._anim.stop()
        self._idx = old_idx - removed_before
        if cur_removed:
            pass                                         # the next picture moved into this place
        self._idx = max(0, min(self._idx, len(self.cards) - 1))
        self._pos = float(self._idx)
        self._layout()
        if self.cards:
            self.cards[self._idx].load_async(foreground=True)
        self.currentChanged.emit(self._idx)

    def current(self):
        return self.cards[self._idx] if self.cards else None

    def move_card(self, i, j):
        """Page order: the card at i goes to position j."""
        if not (0 <= i < len(self.cards) and 0 <= j < len(self.cards)) or i == j:
            return
        c = self.cards.pop(i)
        self.cards.insert(j, c)
        self._idx = j
        self._pos = float(j)
        self._anim.stop()
        self._layout()

    def go_to(self, i, animate=True):
        if not self.cards:
            return
        i = max(0, min(i, len(self.cards) - 1))
        self.cards[i].load_async(foreground=True)
        changed = i != self._idx
        self._idx = i
        self._anim.stop()
        if animate and abs(self._pos - i) > 1e-3:
            self._anim.setStartValue(float(self._pos))
            self._anim.setEndValue(float(i))
            self._anim.start()
        else:
            self._pos = float(i)
            self._layout()
        if changed:
            self.currentChanged.emit(i)
            nxt = i + 1
            if nxt < len(self.cards):                    # the next picture is opened quietly in the background
                QTimer.singleShot(600, self.cards[nxt].load_async)

    def next(self):
        self.go_to(self._idx + 1)

    def prev(self):
        self.go_to(self._idx - 1)

    def _set_pos(self, v):
        self._pos = float(v)
        self._layout()

    def _layout(self):
        w, h = self.width(), self.height()
        for i, c in enumerate(self.cards):
            off = i - self._pos
            if abs(off) < 1.0:
                c.setGeometry(int(round(off * w)), 0, w, h)
                if not c.isVisible():
                    c.show()
            elif c.isVisible():
                c.hide()

    def resizeEvent(self, e):
        super().resizeEvent(e)
        self._layout()

    def wheelEvent(self, e):
        # Shift + mouse wheel anywhere on a card slides to the next / previous picture
        if e.modifiers() & Qt.KeyboardModifier.ShiftModifier:
            d = e.angleDelta().y() or e.angleDelta().x()
            now = time.monotonic()
            if now - self._last_wheel > 0.6:
                self._wheel = 0
            self._last_wheel = now
            self._wheel += d
            if abs(self._wheel) >= 120:
                if self._wheel < 0:
                    self.next()
                else:
                    self.prev()
                self._wheel = 0
            e.accept()
        else:
            e.ignore()


# ------------------------------------------------------------- main window
class MainWindow(QMainWindow):
    def __init__(self, settings):
        super().__init__()
        self.settings = settings
        self.quitting = False
        self.tmp = tempfile.mkdtemp(prefix="imagefixer_")
        self._paste_n = 0
        self.items = {}
        self._sync = False
        self.runner = JobRunner(self)
        self.runner.changed.connect(self._job_changed)
        self.phone = None
        self.phone_tunnel = None
        self._phone_dlg = None
        self.setWindowTitle("Image Fixer")
        self.setWindowIcon(make_icon())
        self.setAcceptDrops(True)
        scr = QApplication.primaryScreen()
        geo = scr.availableGeometry() if scr is not None else QRect(0, 0, 1366, 768)
        self._screen_long = max(geo.width(), geo.height()) * (scr.devicePixelRatio() if scr is not None else 1.0)
        self._ui_scale = 1.0
        core.purge_dir(EXPORT_DIR, older_than=6 * 3600)         # leftovers of an earlier run
        core.purge_dir(core.ORIG_DIR, older_than=6 * 3600)
        self._replace_ok = False                   # 'Save replaces the original' was confirmed in this session
        self._pdf_n = 0
        self._origin = {}                          # path -> 'phone' / 'paste' for pictures that have no file of their own
        self._add_queue = []                       # pictures waiting to get their card (added in small slices)
        self._add_total = 0
        self._add_done = 0
        self._add_first = None
        self._adding = False
        # a small screen must never be forced wider / taller than it is
        self.setMinimumSize(min(940, int(geo.width() * 0.92)), min(600, int(geo.height() * 0.88)))
        self._apply_quality(self.settings.value("quality", "auto", type=str), say=False)

        central = QWidget()
        central.setObjectName("root")
        self.setCentralWidget(central)
        v = QVBoxLayout(central)
        v.setContentsMargins(0, 0, 0, 0)
        v.setSpacing(0)
        v.addWidget(self._build_header())
        host = QWidget()
        self.stack = QStackedLayout(host)
        self.stack.addWidget(self._build_empty())
        self.stack.addWidget(self._build_work())
        v.addWidget(host, 1)

        self.thumbs = ThumbSignals()
        self.thumbs.done.connect(self._thumb_done)
        self._build_tray()
        self._build_shortcuts()
        self.updater.start()                      # auto update: looks for a new build a few seconds after start (if switched on)
        self._apply_ui_size(self.settings.value("ui_size", "auto", type=str), say=False)
        self._fit_layout()
        self._refresh_dir()
        self._refresh_counts()

    # -------------------------------------------------------------- builders
    def _build_header(self):
        h = QWidget()
        h.setObjectName("header")
        l = QHBoxLayout(h)
        l.setContentsMargins(16, 10, 16, 10)
        l.setSpacing(10)
        logo = QLabel()
        logo.setPixmap(logo_pixmap(30))
        brand = QLabel("Image Fixer")
        brand.setObjectName("brand")
        self.lblBrand = brand
        l.addWidget(logo)
        l.addWidget(brand)
        l.addSpacing(14)
        self.btnChoose = QPushButton("+  Choose files")
        self.btnChoose.setProperty("accent", True)
        self.btnChoose.clicked.connect(self.choose_files)
        self.btnSaveAll = QPushButton("Save all edited")
        self.btnSaveAll.clicked.connect(self.save_all)
        self.btnPhone = QPushButton("Add from phone")
        self.btnPhone.setToolTip("Use your phone as a wireless scanner / picture sender (QR code)")
        self.btnPhone.clicked.connect(self.open_phone)
        self.btnPdfAll = QPushButton("Save all as PDF")
        self.btnPdfAll.setToolTip("One PDF with every open picture, one page each")
        self.btnPdfAll.clicked.connect(self.save_all_pdf)
        l.addWidget(self.btnChoose)
        l.addWidget(self.btnPhone)
        l.addStretch(1)
        self.btnFolder = QPushButton("Save folder: not set")
        self.btnFolder.setMinimumWidth(60)
        self.btnFolder.setToolTip("Edited pictures are saved here. Click to change.")
        self.btnFolder.clicked.connect(self.choose_dir)
        self.btnOpenDir = QPushButton("Open")
        self.btnOpenDir.setToolTip("Open the save folder")
        self.btnOpenDir.clicked.connect(self.open_dir)
        self.btnClear = QPushButton("Clear all")
        self.btnClear.clicked.connect(self.clear_all)
        self.btnSettings = QPushButton("Settings")
        self.btnSettings.setMenu(self._build_menu())
        for b in (self.btnFolder, self.btnOpenDir, self.btnClear, self.btnSettings):
            l.addWidget(b)
        return h

    def _build_menu(self):
        m = QMenu(self)
        self.actAuto = QAction("Start with Windows (stays in tray)", self)
        self.actAuto.setCheckable(True)
        self.actAuto.setChecked(winutil.is_autostart())
        self.actAuto.toggled.connect(self._toggle_autostart)
        self.actSend = QAction("Show in right-click > Send to menu", self)
        self.actSend.setCheckable(True)
        self.actSend.setChecked(self.settings.value("sendto", True, type=bool))
        self.actSend.toggled.connect(self._toggle_sendto)
        m.addAction(self.actAuto)
        m.addAction(self.actSend)
        m.addSeparator()
        # ---- auto update (all of it lives here)
        self.updater = UpdateManager(self)
        self.actUpd = QAction("Auto update (tell me, ask, then install)", self)
        self.actUpd.setCheckable(True)
        self.actUpd.setChecked(self.updater.enabled())
        self.actUpd.setToolTip("ON: the program looks for a new version on GitHub, tells you, asks, then updates itself.\n"
                               "OFF: it never looks and never shows anything about updates.")
        self.actUpd.toggled.connect(self._toggle_update)
        self.actUpdReady = QAction("", self)
        self.actUpdReady.setVisible(False)
        self.actUpdReady.triggered.connect(lambda: self.updater.info and self.updater.ask(self.updater.info))
        self.actUpdNow = QAction("Check for updates now...", self)
        self.actUpdNow.triggered.connect(lambda: self.updater.check(True))
        self.actUpdVer = QAction("Version: " + updater.version_text(), self)
        self.actUpdVer.setEnabled(False)
        self.updater.available_changed.connect(self._update_available)
        m.addAction(self.actUpd)
        m.addAction(self.actUpdReady)
        m.addAction(self.actUpdNow)
        m.addAction(self.actUpdVer)
        m.addSeparator()
        a = QAction("Choose save folder...", self)
        a.triggered.connect(self.choose_dir)
        m.addAction(a)
        m.addSeparator()
        self._radio_menu(m, "Picture detail (sharpness on screen)", "quality", self._apply_quality,
                         (("auto", "Automatic (best for this screen)"), ("fast", "Fast  (slow computers)"),
                          ("balanced", "Balanced"), ("sharp", "Sharp"), ("max", "Maximum  (big screens)")))
        self._radio_menu(m, "Interface size", "ui_size", self._apply_ui_size,
                         (("auto", "Automatic"), ("small", "Small"), ("normal", "Normal"), ("large", "Large"),
                          ("xl", "Extra large")))
        m.addSeparator()
        q = QAction("Exit Image Fixer", self)
        q.triggered.connect(self.quit_app)
        m.addAction(q)
        return m

    def _radio_menu(self, menu, title, key, fn, choices):
        sub = menu.addMenu(title)
        grp = QActionGroup(self)
        grp.setExclusive(True)
        cur = self.settings.value(key, "auto", type=str)
        for val, text in choices:
            a = QAction(text, self)
            a.setCheckable(True)
            a.setChecked(val == cur)
            grp.addAction(a)
            a.triggered.connect(lambda _=False, v=val: (self.settings.setValue(key, v), fn(v)))
            sub.addAction(a)

    def _build_empty(self):
        w = QWidget()
        l = QVBoxLayout(w)
        l.setContentsMargins(60, 40, 60, 40)
        zone = QFrame()
        zone.setObjectName("dropzone")
        z = QVBoxLayout(zone)
        z.setAlignment(Qt.AlignmentFlag.AlignCenter)
        z.setSpacing(12)
        ico = QLabel()
        ico.setPixmap(logo_pixmap(84))
        ico.setAlignment(Qt.AlignmentFlag.AlignCenter)
        t = QLabel("Drop pictures here")
        t.setObjectName("big")
        t.setAlignment(Qt.AlignmentFlag.AlignCenter)
        s = QLabel("or press Ctrl+V to paste copied pictures\n"
                   "or select pictures in Explorer, right-click > Send to > Image Fixer")
        s.setObjectName("muted")
        s.setAlignment(Qt.AlignmentFlag.AlignCenter)
        b = QPushButton("+  Choose files")
        b.setProperty("accent", True)
        b.setMinimumHeight(40)
        b.setMinimumWidth(180)
        b.clicked.connect(self.choose_files)
        z.addWidget(ico)
        z.addWidget(t)
        z.addWidget(s)
        z.addSpacing(8)
        z.addWidget(b, 0, Qt.AlignmentFlag.AlignCenter)
        l.addWidget(zone)
        return w

    def _build_work(self):
        w = QWidget()
        l = QVBoxLayout(w)
        l.setContentsMargins(0, 0, 0, 0)
        l.setSpacing(0)
        self.carousel = Carousel()
        self.carousel.currentChanged.connect(self._current_changed)
        l.addWidget(self.carousel, 1)
        bar = QWidget()
        bar.setObjectName("bottom")
        b = QHBoxLayout(bar)
        b.setContentsMargins(12, 8, 12, 8)
        b.setSpacing(8)
        self.btnPrev = QPushButton("\u2039")
        self.btnPrev.setObjectName("nav")
        self.btnNext = QPushButton("\u203a")
        self.btnNext.setObjectName("nav")
        self.btnPrev.clicked.connect(lambda: self.carousel.prev())
        self.btnNext.clicked.connect(lambda: self.carousel.next())
        self.strip = Strip()
        self.strip.provider = self._mime_for
        self.strip.preparer = self._prepare_for
        self.strip.removeRequested.connect(self.remove_by_uids)
        self.strip.copyRequested.connect(self.copy_by_uids)
        self.strip.setDragEnabled(False)
        self.strip.setDragDropMode(QAbstractItemView.DragDropMode.NoDragDrop)
        self.strip.setDefaultDropAction(Qt.DropAction.CopyAction)
        self.strip.setObjectName("strip")
        self.strip.setViewMode(QListView.ViewMode.IconMode)
        self.strip.setFlow(QListView.Flow.LeftToRight)
        self.strip.setWrapping(False)
        self.strip.setMovement(QListView.Movement.Static)
        self.strip.setSpacing(4)
        self.strip.setHorizontalScrollMode(QAbstractItemView.ScrollMode.ScrollPerPixel)
        self.strip.setFocusPolicy(Qt.FocusPolicy.ClickFocus)     # a click on the thumbnails gives them the keyboard (Delete ...)
        self.strip.setToolTip("Click = select.  Ctrl + click / Shift + click = select several.  Del = remove, Ctrl+C = copy.\n"
                              "Drag the selected pictures out to take them to another program.")
        self.strip.itemSelectionChanged.connect(self._strip_selection)
        self._thumb_px = 64
        self._apply_thumb_size()
        self.btnPageL = QPushButton("\u21e0")
        self.btnPageR = QPushButton("\u21e2")
        for pb, d, tip in ((self.btnPageL, -1, "Move this page earlier (page order in the PDF)"),
                           (self.btnPageR, 1, "Move this page later (page order in the PDF)")):
            pb.setToolTip(tip)
            pb.setProperty("flat", True)
            pb.clicked.connect(lambda _=False, d=d: self.move_page(d))
        self.progress = QProgressBar()
        self.progress.setFixedWidth(130)
        self.progress.setTextVisible(False)
        self.progress.hide()
        self.lblCount = QLabel("")
        self.lblCount.setObjectName("muted")
        self.lblCount.setMinimumWidth(96)
        self.lblStatus = QLabel("")
        self.lblStatus.setObjectName("muted")
        self.lblStatus.setMinimumWidth(120)
        self.lblStatus.setMaximumWidth(420)
        self.lblStatus.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Preferred)
        self.lblStatus.setWordWrap(True)
        b.addWidget(self.btnPrev)
        b.addWidget(self.strip, 1)
        b.addWidget(self.btnNext)
        b.addWidget(self.btnPageL)
        b.addWidget(self.btnPageR)
        b.addWidget(self.lblCount)
        b.addWidget(self.progress)
        b.addWidget(self.lblStatus)
        l.addWidget(bar)
        return w

    def _build_tray(self):
        self.tray = None
        if not QSystemTrayIcon.isSystemTrayAvailable():
            return
        self.tray = QSystemTrayIcon(make_icon(), self)
        self.tray.setToolTip("Image Fixer")
        m = QMenu()
        o = QAction("Open Image Fixer", self)
        o.triggered.connect(self.show_front)
        m.addAction(o)
        c = QAction("Choose files...", self)
        c.triggered.connect(lambda: (self.show_front(), self.choose_files()))
        m.addAction(c)
        m.addSeparator()
        q = QAction("Exit", self)
        q.triggered.connect(self.quit_app)
        m.addAction(q)
        self._tray_menu = m
        self.tray.setContextMenu(m)
        self.tray.activated.connect(self._tray_click)
        self.tray.show()

    def _tray_click(self, reason):
        if reason in (QSystemTrayIcon.ActivationReason.Trigger, QSystemTrayIcon.ActivationReason.DoubleClick):
            if self.isVisible() and not self.isMinimized():
                self.hide()
            else:
                self.show_front()

    def _build_shortcuts(self):
        def sc(keys, fn):
            s = QShortcut(QKeySequence(keys), self)
            s.activated.connect(fn)

        def cur(fn):
            return lambda: self._with_current(fn)
        def arrow(dx, dy):
            c = self.carousel.current()
            if c is not None and c._tool == "transform":
                c.tf_nudge(dx, dy)
            elif dx < 0:
                self.carousel.prev()
            elif dx > 0:
                self.carousel.next()
        sc("Left", lambda: arrow(-1, 0))
        sc("Right", lambda: arrow(1, 0))
        sc("Up", lambda: arrow(0, -1))
        sc("Down", lambda: arrow(0, 1))
        sc("Shift+Left", lambda: arrow(-10, 0) if (self.carousel.current() is not None and self.carousel.current()._tool == "transform") else None)
        sc("Shift+Right", lambda: arrow(10, 0) if (self.carousel.current() is not None and self.carousel.current()._tool == "transform") else None)
        sc("Shift+Up", lambda: arrow(0, -10) if (self.carousel.current() is not None and self.carousel.current()._tool == "transform") else None)
        sc("Shift+Down", lambda: arrow(0, 10) if (self.carousel.current() is not None and self.carousel.current()._tool == "transform") else None)
        for keys, deg in (("[", -1), ("]", 1), ("Ctrl+[", -15), ("Ctrl+]", 15),
                          ("Ctrl+Alt+Left", -90), ("Ctrl+Alt+Right", 90)):
            sc(keys, lambda d=deg: self._with_current(lambda c: c.tf_rotate(d)))
        sc("R", lambda: self._with_current(lambda c: c.rotate_key(-90.0)))      # 90° right: the selected picture, else the page
        sc("Shift+R", lambda: self._with_current(lambda c: c.rotate_key(90.0)))   # 90° left
        sc("Ctrl+V", self.paste)
        sc("Ctrl+C", cur(lambda c: self.copy_cards([c])))
        sc("Ctrl+P", cur(lambda c: self.print_cards([c])))
        sc("Ctrl+O", self.choose_files)
        sc("Ctrl+S", cur(self.save_card))
        sc("Ctrl+Shift+S", self.save_all)
        # history
        sc("Ctrl+Z", cur(lambda c: c.undo_step()))
        sc("Ctrl+Y", cur(lambda c: c.redo_step()))
        sc("Ctrl+Shift+Z", cur(lambda c: c.redo_step()))
        # selection / editing
        sc("Ctrl+A", cur(lambda c: c.select_all()))
        sc("Ctrl+D", cur(lambda c: c.deselect()))
        sc("Ctrl+Shift+I", cur(lambda c: c.invert_selection()))
        sc("Ctrl+T", cur(lambda c: c.start_transform()))
        sc("Delete", cur(lambda c: c.delete_selection()))
        sc("Backspace", cur(lambda c: c.delete_selection()))
        sc("Return", cur(lambda c: c.apply_pending()))
        sc("Enter", cur(lambda c: c.apply_pending()))
        sc("Escape", cur(lambda c: c.cancel_pending()))
        # tools (single letters, like in a picture editor)
        for key, tool in (("V", "move"), ("M", "rect"), ("L", "lasso"), ("W", "color"), ("B", "brush"),
                          ("E", "erase_brush"), ("C", "crop"), ("I", "pick"), ("P", "paint"), ("H", "view")):
            sc(key, cur(lambda c, t=tool: c.choose_tool(t)))
        # zoom
        sc("Ctrl+0", cur(lambda c: c.canvas.fit()))
        sc("Ctrl+1", cur(lambda c: c.canvas.zoom_to(1.0 / max(c.scale, 1e-6))))
        sc("Ctrl+=", cur(lambda c: c.canvas.zoom_by(1.25)))
        sc("Ctrl++", cur(lambda c: c.canvas.zoom_by(1.25)))
        sc("Ctrl+-", cur(lambda c: c.canvas.zoom_by(0.8)))

    def _with_current(self, fn):
        c = self.carousel.current()
        if c is not None and not self.runner.busy:
            fn(c)

    def _job_changed(self, busy, frac, text):
        """A slow job is running: show progress, keep the window alive but lock the pictures."""
        self.carousel.setEnabled(not busy)
        self.progress.setVisible(busy)
        if busy:
            if frac < 0:
                self.progress.setRange(0, 0)
            else:
                self.progress.setRange(0, 1000)
                self.progress.setValue(int(frac * 1000))
            self._job_text = text
            self.lblStatus.setText(text)
        elif self.lblStatus.text() == getattr(self, "_job_text", None):
            self.lblStatus.setText("")                  # the work is finished: remove "Selecting" etc.

    def _busy(self):
        if self.runner.heavy_busy:
            self.say("Please wait until the current work is finished.")
            return True
        return False

    # ------------------------------------------------------------ layout that follows the window / screen
    def resizeEvent(self, e):
        super().resizeEvent(e)
        self._fit_layout()

    def _fit_layout(self):
        """Small window: shorter texts, thumbnails that fit the height.  Nothing is cut off or pushed out."""
        if not hasattr(self, "btnChoose"):
            return
        w, h = self.width(), self.height()
        self.lblBrand.setVisible(w >= 1050)
        self.btnOpenDir.setVisible(w >= 880)
        self.btnPhone.setText("Add from phone" if w >= 1000 else "Phone")
        self.btnChoose.setText("+  Choose files" if w >= 900 else "+  Files")
        self._refresh_dir()
        if self._phone_dlg is not None and self._phone_dlg.isVisible():
            self._phone_dlg.reposition()
        px = int(round((56 if h < 720 else 64 if h < 1000 else 80) * self._ui_scale))
        if px != self._thumb_px:
            self._thumb_px = px
            self._apply_thumb_size()

    def _thumb_hint(self):
        return QSize(self._thumb_px + 32, self._thumb_px + 26)

    def _apply_thumb_size(self):
        px = self._thumb_px
        self.strip.setIconSize(QSize(px, px))
        hint = self._thumb_hint()
        self.strip.setFixedHeight(hint.height() + 16)
        for r in range(self.strip.count()):
            self.strip.item(r).setSizeHint(hint)
        for c in getattr(self.carousel, "cards", []):
            it = self.items.get(c.uid)
            if it is not None:
                it.setText(self._label(c))

    def _apply_quality(self, level, say=True):
        """How big the working copy on the screen is.  Bigger = sharper when zoomed in (a little slower on old computers)."""
        n = core.quality_preview_max(level, self._screen_long)
        core.set_preview_max(n)
        if say:
            self.say("Picture detail set. It is used for pictures opened from now on (up to %d px on the long side)." % n)

    def _apply_ui_size(self, mode, say=True):
        """Text / button size: automatic = from the screen (small laptop screens a bit smaller, 4K a bit larger)."""
        scr = QApplication.primaryScreen()
        geo = scr.availableGeometry() if scr is not None else QRect(0, 0, 1366, 768)
        if mode == "auto":
            f = 0.92 if geo.height() < 800 else (1.2 if geo.width() >= 2500 else 1.0)
        else:
            f = {"small": 0.9, "normal": 1.0, "large": 1.15, "xl": 1.3}.get(mode, 1.0)
        self._ui_scale = f
        QApplication.instance().setStyleSheet(scaled_qss(f))
        if hasattr(self, "strip"):
            self._thumb_px = int(round((56 if self.height() < 720 else 64 if self.height() < 1000 else 80) * f))
            self._apply_thumb_size()
        if say:
            self.say("Interface size changed.")

    # ------------------------------------------------------------ show/hide
    def show_front(self):
        if self.isMinimized():
            self.showNormal()
        self.show()
        self.raise_()
        self.activateWindow()

    def closeEvent(self, e):
        if self.quitting:
            e.accept()
            return
        if self.tray is None:
            e.ignore()
            self.quit_app()
            return
        e.ignore()
        self.hide()
        if not self.settings.value("tray_tip", False, type=bool):
            self.settings.setValue("tray_tip", True)
            self.tray.showMessage("Image Fixer is still running",
                                  "It stays in the tray. Right-click the tray icon > Exit to close it.",
                                  make_icon(), 5000)

    def quit_app(self):
        n = sum(1 for c in self.carousel.cards if c.is_unsaved())
        if n:
            ans = QMessageBox.question(self, "Exit", "%d edited picture(s) are not saved. Exit anyway?" % n,
                                       QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No)
            if ans != QMessageBox.StandardButton.Yes:
                return
        self.quitting = True
        for c in list(self.carousel.cards):                    # the temporary copies (edited files for drag / copy / print) go away
            c.dispose()
        core.purge_dir(EXPORT_DIR)
        core.purge_dir(core.ORIG_DIR)
        if self.phone_tunnel is not None:
            self.phone_tunnel.stop()
        if self.phone is not None:
            self.phone.stop()
        self.settings.setValue("geometry", self.saveGeometry())
        if self.tray is not None:
            self.tray.hide()
        QApplication.quit()

    # --------------------------------------------------------------- adding
    def dragEnterEvent(self, e):
        md = e.mimeData()
        if md.hasUrls() or md.hasImage():
            e.acceptProposedAction()

    def dropEvent(self, e):
        md = e.mimeData()
        paths = [u.toLocalFile() for u in md.urls() if u.isLocalFile()] if md.hasUrls() else []
        if paths:
            self._incoming(paths, "file")
        elif self._take_pdf_data(md):
            pass
        elif md.hasImage():
            self._add_qimage(md.imageData())
        e.acceptProposedAction()

    def paste(self):
        cb = QApplication.clipboard()
        md = cb.mimeData()
        if md is None:
            return
        if md.hasUrls():
            paths = [u.toLocalFile() for u in md.urls() if u.isLocalFile()]
            if paths:
                self._incoming(paths, "file")
                return
        if self._take_pdf_data(md):
            return
        if md.hasImage():
            self._add_qimage(cb.image())
            return
        self.say("Nothing to paste. Copy picture or PDF files in Explorer first.")

    def _take_pdf_data(self, md):
        """A PDF that arrives as data (not as a file): written to a temporary file and opened like any PDF."""
        if not md.hasFormat("application/pdf"):
            return False
        data = bytes(md.data("application/pdf"))
        if not data.startswith(b"%PDF"):
            return False
        self._paste_n += 1
        p = os.path.join(self.tmp, "pasted_%s_%d.pdf" % (time.strftime("%H%M%S"), self._paste_n))
        with open(p, "wb") as f:
            f.write(data)
        self._incoming([p], "paste")
        return True

    def _add_qimage(self, img):
        if img is None or not isinstance(img, QImage) or img.isNull():
            return
        self._paste_n += 1
        p = os.path.join(self.tmp, "pasted_%s_%d.png" % (time.strftime("%H%M%S"), self._paste_n))
        if img.save(p, "PNG"):
            self._incoming([p], "paste")

    def choose_files(self):
        start = self.settings.value("last_dir", "", type=str)
        files, _ = QFileDialog.getOpenFileNames(self, "Choose pictures", start, FILTER)
        if files:
            self.settings.setValue("last_dir", os.path.dirname(files[0]))
            self.add_files(files)

    def _expand(self, paths):
        out = []
        for p in paths:
            if os.path.isdir(p):
                try:
                    for n in sorted(os.listdir(p)):
                        fp = os.path.join(p, n)
                        if os.path.isfile(fp):
                            out.append(fp)
                except OSError:
                    pass
            else:
                out.append(p)
        ok = core.IMG_EXT | {core.PDF_EXT}
        return [p for p in out if os.path.isfile(p) and os.path.splitext(p)[1].lower() in ok]

    def _open_pdfs(self, pdfs, then):
        """PDF -> pictures, like Photoshop: every page becomes its own picture (its own card).  The pages are drawn in a
        worker thread; then(list of png paths) is called when they are ready."""
        for pdf in pdfs:
            self._pdf_n += 1
            out_dir = os.path.join(self.tmp, "pdf%d" % self._pdf_n)
            name = os.path.basename(pdf)

            def work(progress, pdf=pdf, out_dir=out_dir):
                return core.pdf_to_pictures(pdf, out_dir, progress)

            def done(pages, name=name):
                self.say("%s: %d page(s) opened as pictures." % (name, len(pages)))
                then(pages)

            def fail(msg, name=name):
                QMessageBox.warning(self, "PDF", "Could not open %s:\n%s" % (name, msg.splitlines()[0]))
            self.runner.run("Opening " + name, work, done, fail, light=True)

    def add_files(self, paths):
        paths = self._expand(paths)
        pdfs = [p for p in paths if core.is_pdf(p)]
        if pdfs:
            def pages_ready(pages):
                for p in pages:
                    self._origin[p] = "pdf"
                self.add_files(pages)
            self._open_pdfs(pdfs, pages_ready)
            paths = [p for p in paths if not core.is_pdf(p)]
            if not paths:
                return
        have = {c.path for c in self.carousel.cards} | set(self._add_queue)
        files = []
        for p in paths:
            if p not in have:
                have.add(p)
                files.append(p)
        if not files:
            self.say("No new pictures found.")
            return
        self._add_queue.extend(files)
        self._add_total += len(files)
        if not self._adding:
            self._adding = True
            self._add_first = None
            self.stack.setCurrentIndex(1)
            QTimer.singleShot(0, self._add_tick)

    def _add_tick(self):
        """Makes the cards a few at a time (about 30 ms of work, then the window may repaint and react)."""
        t0 = time.monotonic()
        while self._add_queue and (time.monotonic() - t0) < 0.03:
            first = self._add_first is None
            if first:
                self._add_first = len(self.carousel.cards)
            self._add_one(self._add_queue.pop(0))
            self._add_done += 1
            if first:                                                      # the first new picture opens at once
                self.carousel.go_to(self._add_first, animate=False)
                self._current_changed(self.carousel.index)
        if self._add_total > 6:                                             # a long list: show how far it is
            self.progress.setVisible(True)
            self.progress.setRange(0, max(1, self._add_total))
            self.progress.setValue(self._add_done)
            self.lblStatus.setText("Adding pictures  %d / %d" % (self._add_done, self._add_total))
        self._refresh_counts()
        if self._add_queue:
            QTimer.singleShot(0, self._add_tick)
            return
        n = self._add_total
        self._adding = False
        self._add_total = self._add_done = 0
        if not self.runner.busy:
            self.progress.setVisible(False)
        if self._add_first is not None and self.carousel.cards:
            self._current_changed(self.carousel.index)
        self.say("Added %d picture(s)." % n)
        self._refresh_counts()

    def _add_one(self, path):
        c = Card(path)
        c.origin = self._origin.pop(path, "paste" if path.startswith(self.tmp) else "file")
        c.runner = self.runner
        c.editedChanged.connect(self._card_edited)
        c.removeRequested.connect(self.remove_card)
        c.copyRequested.connect(lambda card: self.copy_cards([card]))
        c.printRequested.connect(lambda card: self.print_cards([card]))
        c.saveRequested.connect(self.save_card)
        c.saveAsRequested.connect(self.save_card_as)
        c.saveAllRequested.connect(self.save_all)
        c.pdfAllRequested.connect(self.save_all_pdf)
        c.applyAllRequested.connect(self.apply_to_all)
        c.message.connect(self.say)
        self.carousel.add_card(c)
        it = QListWidgetItem(self._label(c))
        it.setSizeHint(self._thumb_hint())
        it.setToolTip(path + "\n(drag it out to take the picture to another program)")
        it.setData(Qt.ItemDataRole.UserRole, c.uid)
        it.setTextAlignment(Qt.AlignmentFlag.AlignHCenter)
        self._sync = True
        self.strip.addItem(it)
        self._sync = False
        self.items[c.uid] = it
        QThreadPool.globalInstance().start(ThumbJob(c.uid, path, self.thumbs))

    def _thumb_done(self, uid, img):
        it = self.items.get(uid)
        if it is not None:
            it.setIcon(QIcon(QPixmap.fromImage(img)))

    def _label(self, c):
        mark = ""
        if c.is_edited():
            mark = "\u25cf " if c.is_unsaved() else "\u2713 "
        fm = QFontMetrics(self.font())
        return mark + fm.elidedText(c.name, Qt.TextElideMode.ElideMiddle, self._thumb_px + 20)

    # ------------------------------------------------------------ navigation
    def _strip_selection(self):
        """One thumbnail selected = show that picture.  Several (Ctrl / Shift) = just a selection, nothing slides."""
        if self._sync:
            return
        rows = [self.strip.row(i) for i in self.strip.selectedItems()]
        if len(rows) == 1:
            self.carousel.go_to(rows[0])
        self._refresh_counts()

    def _current_changed(self, i):
        prev = self._sync
        self._sync = True
        if 0 <= i < self.strip.count():
            self.strip.setCurrentRow(i, QItemSelectionModel.SelectionFlag.ClearAndSelect)   # just this one is selected
            self.strip.scrollToItem(self.strip.item(i))
        self._sync = prev
        self._refresh_counts()

    def _refresh_counts(self):
        n = len(self.carousel.cards) if hasattr(self, "carousel") else 0
        i = self.carousel.index if n else 0
        sel = len(self.strip.selectedItems()) if hasattr(self, "strip") and n else 0
        if n and sel > 1:
            self.lblCount.setText("%d selected  (%d)" % (sel, n))
        else:
            self.lblCount.setText("%d / %d" % (i + 1, n) if n else "")
        self.btnPrev.setEnabled(n > 1 and i > 0)
        self.btnNext.setEnabled(n > 1 and i < n - 1)
        unsaved = sum(1 for c in self.carousel.cards if c.is_unsaved()) if n else 0
        label = "Save all edited (%d)" % unsaved if unsaved else "Save all edited"
        self.btnSaveAll.setText(label)
        self.btnSaveAll.setEnabled(unsaved > 0)
        for c in (self.carousel.cards if n else []):
            c.btnSaveAll.setText(label)
            c.btnSaveAll.setEnabled(unsaved > 0)
        self.btnClear.setEnabled(n > 0)
        self.btnPdfAll.setEnabled(n > 0)
        self.btnPageL.setEnabled(n > 1 and i > 0)
        self.btnPageR.setEnabled(n > 1 and i < n - 1)
        if n == 0:
            self.stack.setCurrentIndex(0)

    def _card_edited(self, c):
        it = self.items.get(c.uid)
        if it is not None:
            it.setText(self._label(c))
        self._refresh_counts()

    def say(self, text):
        self.lblStatus.setText(text)
        QTimer.singleShot(9000, lambda t=text: self.lblStatus.setText("") if self.lblStatus.text() == t else None)

    # ------------------------------------------------- apply to all / drag out
    def apply_to_all(self, card):
        others = [c for c in self.carousel.cards if c is not card]
        if not others:
            self.say("There is no other picture yet.")
            return
        act = card.last_action
        if act is None:
            self.say("Change a setting first (a slider, shadow removal ...). Then this button copies that one setting.")
            return
        for c in others:
            c.apply_action(act)
        self._refresh_counts()
        self.say("Last setting (%s) applied to %d other picture(s). Crop, erase and other edits were not touched."
                 % (act["label"], len(others)))

    def _card(self, uid):
        for c in self.carousel.cards:
            if c.uid == uid:
                return c
        return None

    def _prepare_for(self, uid):
        c = self._card(uid)
        if c is not None and not self.runner.busy:
            c.prepare_export()

    def _mime_for(self, uids):
        """The files of one or several pictures (with their edits) for a drag."""
        if isinstance(uids, str):
            uids = [uids]
        cards = [c for c in (self._card(u) for u in uids) if c is not None]
        if not cards:
            return None
        QApplication.setOverrideCursor(Qt.CursorShape.WaitCursor)
        try:
            urls = []
            for c in cards:
                path = c.make_mime(with_image=False)[1]       # the file was prepared in the background while pointing
                urls.append(QUrl.fromLocalFile(path))
        finally:
            QApplication.restoreOverrideCursor()
        md = QMimeData()
        md.setUrls(urls)
        return md

    # ------------------------------------------------------------ print (with all edits; nothing is saved)
    def print_cards(self, cards):
        cards = [c for c in cards if c is not None]
        if not cards or self._busy():
            return
        try:
            from PySide6.QtPrintSupport import QPrintDialog, QPrinter
        except ImportError:
            self.say("Printing is not available on this computer.")
            return
        printer = QPrinter(QPrinter.PrinterMode.HighResolution)
        dlg = QPrintDialog(printer, self)
        dlg.setWindowTitle("Print " + (cards[0].name if len(cards) == 1 else "%d pictures" % len(cards)))
        if dlg.exec() != QDialog.DialogCode.Accepted:
            return
        sources = [c.render_source() for c in cards]

        def work(progress):
            out = []
            for i, src in enumerate(sources):
                progress(i / len(sources), "%d / %d" % (i + 1, len(sources)))
                im = src().convert("RGB")
                out.append((im.width, im.height, im.tobytes()))
            return out

        def done(pages):
            p = QPainter(printer)
            for i, (w, h, data) in enumerate(pages):
                if i:
                    printer.newPage()
                img = QImage(data, w, h, 3 * w, QImage.Format.Format_RGB888)
                area = p.viewport()
                if (w > h) != (area.width() > area.height()):            # a wide picture on a tall page: turn it
                    img = img.transformed(QTransform().rotate(90))
                s = min(area.width() / img.width(), area.height() / img.height())
                tw, th = int(img.width() * s), int(img.height() * s)
                p.drawImage(QRect(area.x() + (area.width() - tw) // 2, area.y() + (area.height() - th) // 2, tw, th), img)
            p.end()
            self.say("Sent to the printer.")
        self.runner.run("Preparing the print", work, done, lambda msg: self.say("Could not print: " + msg.splitlines()[0]))

    # ------------------------------------------------------------ copy (one or many pictures)
    def copy_by_uids(self, uids):
        self.copy_cards([c for c in (self._card(u) for u in uids) if c is not None])

    def copy_cards(self, cards):
        """Ctrl+C: the pictures (with their edits) go to the clipboard.  Rendering happens in a worker thread."""
        cards = [c for c in cards if c is not None]
        if not cards:
            return
        if self._busy():
            return
        entries = []
        for c in cards:
            p = c.export_ready()
            if p is not None:
                entries.append((c, p, None, None, None, None))
            else:
                stem, ext = c.out_name()
                entries.append((c, None, c.render_source(), stem, ext, c._ver))

        def work(progress):
            os.makedirs(EXPORT_DIR, exist_ok=True)
            out = []
            for i, (c, p, src, stem, ext, ver) in enumerate(entries):
                progress(i / len(entries), "%d / %d" % (i + 1, len(entries)))
                if p is None:
                    p = core.unique_path(EXPORT_DIR, stem, ext)
                    core.save_image(src(), p)
                out.append(p)
            qimg = QImage(out[0]) if len(out) == 1 else None
            return out, qimg

        def done(r):
            paths, qimg = r
            md = QMimeData()
            md.setUrls([QUrl.fromLocalFile(p) for p in paths])
            if qimg is not None and not qimg.isNull():
                md.setImageData(qimg)
            QApplication.clipboard().setMimeData(md)
            for (c, p, src, stem, ext, ver), path in zip(entries, paths):
                if p is None and not c._dead:
                    c.set_export(ver, path)
            self.say("Copied %d picture(s). Paste them anywhere with Ctrl+V." % len(paths))
        self.runner.run("Copying %d picture(s)" % len(entries) if len(entries) > 1 else "Copying " + cards[0].name,
                        work, done, lambda msg: self.say("Could not copy: " + msg.splitlines()[0]))

    # ------------------------------------------------------------ page order
    def move_page(self, d):
        if self._busy():
            return
        i = self.carousel.index
        j = i + d
        if not self.carousel.cards or not (0 <= j < len(self.carousel.cards)):
            return
        self.carousel.move_card(i, j)
        self._sync = True
        it = self.strip.takeItem(i)
        self.strip.insertItem(j, it)
        self._sync = False
        self._current_changed(j)
        self.say("Moved to position %d." % (j + 1))

    # ----------------------------------------------------------- removing
    def remove_card(self, c):
        self.remove_cards([c])

    def remove_by_uids(self, uids):
        self.remove_cards([c for c in (self._card(u) for u in uids) if c is not None])

    def remove_cards(self, cards):
        """Removes pictures from the list (the files on the disk are never touched).  One question at most, only when
        some of them have edits that are not saved."""
        cards = [c for c in dict.fromkeys(cards) if c in self.carousel.cards]
        if not cards or self._busy():
            return
        unsaved = [c for c in cards if c.is_unsaved()]
        if unsaved:
            if len(cards) == 1:
                msg = "This picture has edits that are not saved. Remove it anyway?"
            else:
                msg = "%d of the %d pictures have edits that are not saved. Remove them anyway?" % (len(unsaved), len(cards))
            ans = QMessageBox.question(self, "Remove", msg, QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No)
            if ans != QMessageBox.StandardButton.Yes:
                return
        rows = sorted(self.carousel.cards.index(c) for c in cards)
        self._sync = True
        self.strip.setUpdatesEnabled(False)
        try:
            for r in reversed(rows):                       # from the back, so the other rows keep their number
                it = self.strip.takeItem(r)
                if it is not None:
                    self.items.pop(it.data(Qt.ItemDataRole.UserRole), None)
            self.carousel.remove_cards(cards)
        finally:
            self.strip.setUpdatesEnabled(True)
            self._sync = False
        self._current_changed(self.carousel.index)
        self.say("Removed %d picture(s)." % len(cards))

    def clear_all(self):
        if self._busy():
            return
        n = sum(1 for c in self.carousel.cards if c.is_unsaved())
        if n:
            ans = QMessageBox.question(self, "Clear all", "%d edited picture(s) are not saved. Clear anyway?" % n,
                                       QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No)
            if ans != QMessageBox.StandardButton.Yes:
                return
        self._sync = True
        self._add_queue.clear()
        self.carousel.remove_cards(list(self.carousel.cards))
        self.strip.clear()
        self.items.clear()
        self._sync = False
        self._refresh_counts()

    # -------------------------------------------------------------- saving
    def _dir(self):
        return self.settings.value("save_dir", "", type=str)

    def _refresh_dir(self):
        d = self._dir()
        room = int(max(80, min(340, self.width() * 0.2)))
        narrow = self.width() < 1100
        fm = QFontMetrics(self.font())
        if d:
            self.btnFolder.setText(("Folder: " if narrow else "Save folder: ") + fm.elidedText(d, Qt.TextElideMode.ElideMiddle, room))
            self.btnFolder.setToolTip("Edited pictures are saved in:\n" + d + "\nClick to change.")
        else:
            self.btnFolder.setText("Save folder: not set" if not narrow else "Folder: not set")
            self.btnFolder.setToolTip("Edited pictures are saved here. Click to choose the folder.")
        self.btnOpenDir.setEnabled(bool(d))

    def choose_dir(self):
        d = QFileDialog.getExistingDirectory(self, "Where should edited pictures be saved?", self._dir())
        if d:
            self.settings.setValue("save_dir", d)
            self._refresh_dir()
            self.say("Save folder: " + d)
        return d

    def open_dir(self):
        d = self._dir()
        if d and os.path.isdir(d):
            QDesktopServices.openUrl(QUrl.fromLocalFile(d))

    def _need_dir(self, why=""):
        """The remembered save folder; asked for (and remembered) when there is none yet."""
        d = self._dir()
        if d and os.path.isdir(d):
            return d
        if why:
            QMessageBox.information(self, "Save folder", why)
        return self.choose_dir() or None

    def _fail_box(self, title):
        return lambda msg: QMessageBox.warning(self, title, "Could not save:\n%s" % msg.splitlines()[0])

    # ------------------------------------------------------------ saving, like Photoshop
    #   Save        = the picture replaces its original file.  A picture that has no file of its own (phone, clipboard)
    #                 goes to the remembered save folder (asked once, then used every time until it is changed).
    #   Save as     = choose folder, name and format (JPG / PNG / PDF); that folder is remembered.
    #   Save all    = replace the originals, or a folder (choose / remembered).
    BASIC = (".jpg", ".jpeg", ".png")

    def _basic_ext(self, card):
        e = os.path.splitext(card.name)[1].lower()
        return ".jpg" if e in (".jpg", ".jpeg") else ".png"

    def _folder_path(self, card, folder, fmt="same", taken=None):
        stem = os.path.splitext(card.name)[0] + ("_edited" if card.origin == "file" else "")
        ext = self._basic_ext(card) if fmt == "same" else "." + fmt
        p = core.unique_path(folder, stem, ext)
        n = 2
        while taken is not None and p in taken:
            p = os.path.join(folder, "%s (%d)%s" % (stem, n, ext))
            n += 1
        if taken is not None:
            taken.add(p)
        return p

    def _confirm_replace(self, n):
        if not self.settings.value("confirm_replace", True, type=bool) or self._replace_ok:
            return True
        box = QMessageBox(self)
        box.setWindowTitle("Save")
        box.setIcon(QMessageBox.Icon.Question)
        box.setText("Save replaces the original file%s with the edited picture.\nUse 'Save as' to keep the original." %
                    ("" if n == 1 else "s (%d pictures)" % n))
        chk = QCheckBox("Do not ask again")
        box.setCheckBox(chk)
        box.setStandardButtons(QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No)
        box.button(QMessageBox.StandardButton.Yes).setText("Replace")
        box.button(QMessageBox.StandardButton.No).setText("Cancel")
        if box.exec() != QMessageBox.StandardButton.Yes:
            return False
        self._replace_ok = True
        if chk.isChecked():
            self.settings.setValue("confirm_replace", False)
        return True

    def _write_job(self, entries, label):
        """entries = [(card, path, replace)].  Rendering and writing happen in a worker thread; a replaced original is
        first copied to a temporary place (Reset / Compare still show it) and written through a temporary file."""
        pdf_mode = None
        if any(p.lower().endswith(".pdf") for _c, p, _r in entries):
            pdf_mode = self._pdf_mode()
            if pdf_mode is None:
                return
        jobs = [(c, p, r, c.render_source(), c.uid) for c, p, r in entries]

        def work(progress):
            ok, failed = [], []
            for i, (c, p, rep, src, uid) in enumerate(jobs):
                progress(i / len(jobs), "%d / %d" % (i + 1, len(jobs)))
                try:
                    bk = core.backup_original(p, uid) if rep else None
                    img = src()
                    if p.lower().endswith(".pdf"):
                        core.save_pdf([img], p, pdf_mode)
                    elif rep:
                        core.write_replacing(img, p)
                    else:
                        core.save_image(img, p)
                    ok.append((c, p, bk))
                except Exception as ex:
                    failed.append("%s: %s" % (c.name, ex))
            return ok, failed

        def done(r):
            ok, failed = r
            for c, p, bk in ok:
                if bk:
                    c.orig_path = c.orig_path or bk
                c.mark_saved(p)
                it = self.items.get(c.uid)
                if it is not None and bk:
                    QThreadPool.globalInstance().start(ThumbJob(c.uid, p, self.thumbs))
            if len(ok) == 1:
                self.say(("Saved (original replaced): " if jobs[0][2] else "Saved: ") + ok[0][1])
            elif ok:
                self.say("Saved %d picture(s)." % len(ok))
            if failed:
                QMessageBox.warning(self, "Save", "Some pictures could not be saved:\n" + "\n".join(failed))
        self.runner.run(label, work, done, self._fail_box("Save"))

    def save_card(self, card):
        """Save (Ctrl+S)."""
        if self._busy():
            return
        if card.can_replace():
            if self._confirm_replace(1):
                self._write_job([(card, card.path, True)], "Saving " + card.name)
            return
        d = self._need_dir("This picture came from the phone or the clipboard, so there is no original file to replace.\n\n"
                           "Choose the folder where such pictures are saved. It is remembered and used every time\n"
                           "(change it with 'Save as...' or the folder button at the top).")
        if d:
            self._write_job([(card, self._folder_path(card, d), False)], "Saving " + card.name)

    def save_card_as(self, card):
        """Save as...: folder, name and format (JPG, PNG, PDF) are chosen; the folder is remembered."""
        if self._busy():
            return
        ext = self._basic_ext(card)
        start_dir = self._dir() if (card.origin != "file" or not os.path.isdir(os.path.dirname(card.path))) else os.path.dirname(card.path)
        start = os.path.join(start_dir or os.path.expanduser("~"), os.path.splitext(card.name)[0] + ext)
        filters = "JPG picture (*.jpg);;PNG picture (*.png);;PDF document (*.pdf)"
        sel = "JPG picture (*.jpg)" if ext == ".jpg" else "PNG picture (*.png)"
        path, flt = QFileDialog.getSaveFileName(self, "Save as", start, filters, sel)
        if not path:
            return
        want = ".pdf" if "pdf" in flt.lower() else (".jpg" if "jpg" in flt.lower() else ".png")
        cur = os.path.splitext(path)[1].lower()
        if cur not in self.BASIC + (".pdf",):
            path = os.path.splitext(path)[0] + want
        self.settings.setValue("save_dir", os.path.dirname(path))
        self._refresh_dir()
        same = os.path.abspath(path) == os.path.abspath(card.path) and card.can_replace()
        self._write_job([(card, path, same)], "Saving " + os.path.basename(path))

    def save_all(self):
        """Save all: replace the originals or a folder - chosen in the dialog, remembered."""
        if self._busy():
            return
        todo = [c for c in self.carousel.cards if c.is_unsaved()]
        if not todo:
            self.say("Nothing to save: no edited picture.")
            return
        n_rep = sum(1 for c in todo if c.can_replace())
        dlg = SaveAllDialog(self, len(todo), n_rep, self._dir(), self.settings.value("saveall_mode", "replace", type=str))
        if dlg.exec() != QDialog.DialogCode.Accepted:
            return
        r = dlg.result_values()
        self.settings.setValue("saveall_mode", r["mode"])
        folder = r["folder"]
        if folder and r["remember"]:
            self.settings.setValue("save_dir", folder)
            self._refresh_dir()
        if r["mode"] == "replace" and not self._confirm_replace(n_rep):
            return
        taken, entries = set(), []
        for c in todo:
            if r["mode"] == "replace" and c.can_replace():
                entries.append((c, c.path, True))
            else:
                entries.append((c, self._folder_path(c, folder, r["fmt"] if r["mode"] == "folder" else "same", taken), False))
        self._write_job(entries, "Saving %d pictures" % len(entries))

    def _pdf_mode(self):
        items = ["Same shape as the picture (nothing cut or padded)", "A4 paper (turned to fit each picture)",
                 "US Letter paper (turned to fit each picture)"]
        modes = ["fit", "a4", "letter"]
        last = self.settings.value("pdf_mode", "fit", type=str)
        cur = modes.index(last) if last in modes else 0
        item, ok = QInputDialog.getItem(self, "PDF page size", "Page size:", items, cur, False)
        if not ok:
            return None
        mode = modes[items.index(item)]
        self.settings.setValue("pdf_mode", mode)
        return mode

    def save_all_pdf(self):
        """Every open picture = one page, in thumbnail order."""
        if self._busy():
            return
        cards = list(self.carousel.cards)
        if not cards:
            self.say("There is no picture to save.")
            return
        start = os.path.join(self._dir() or os.path.expanduser("~"), "Pages.pdf")
        path, _ = QFileDialog.getSaveFileName(self, "Save all pages as one PDF", start, "PDF document (*.pdf)")
        if not path:
            return
        if not path.lower().endswith(".pdf"):
            path += ".pdf"
        mode = self._pdf_mode()
        if mode is None:
            return
        sources = [c.render_source() for c in cards]       # made and written one by one in the worker thread

        def work(progress):
            core.save_pdf(sources, path, mode, progress=progress)
            return path

        def done(p):
            self.say("PDF saved (%d pages): %s" % (len(cards), p))
        self.runner.run("Making the PDF", work, done, self._fail_box("PDF"))

    # --------------------------------------------------------------- phone
    def open_phone(self):
        from phonedlg import PhoneDialog
        if self._phone_dlg is None:
            self._phone_dlg = PhoneDialog(self)
        self._phone_dlg.show_front()

    def phone_received(self, paths):
        self.show_front()
        n = self._incoming(paths, "phone")
        if n < 0:
            self.say("Received %d picture(s) from the phone (added as overlay pictures)." % len(paths))
        else:
            self.say("Received %d picture(s) from the phone." % len(paths))

    def _incoming(self, paths, origin):
        """Something arrives (drag, paste, phone / QR): while the PICTURE tab is open it becomes an overlay picture on the
        current page (signature, stamp ...); in any other tab it is a new main picture.  Returns -1 for overlays."""
        files = [p for p in self._expand(paths)]
        pdfs = [p for p in files if core.is_pdf(p)]
        if pdfs:
            self._open_pdfs(pdfs, lambda pages: self._incoming(pages, "pdf"))
            files = [p for p in files if not core.is_pdf(p)]
            if not files:
                return len(pdfs)
        c = self.carousel.current()
        if files and c is not None and c.picture_tab_active() and not self.runner.heavy_busy:
            for p in files:
                c.add_picture(p)
            self.say("Added %d picture(s) as overlay on this page." % len(files))
            return -1
        for p in files:
            self._origin[p] = origin
        self.add_files(files)
        return len(files)

    # ------------------------------------------------------------- settings
    def _toggle_autostart(self, on):
        winutil.set_autostart(on)
        self.say("Image Fixer will start with Windows." if on else "Image Fixer will not start with Windows.")

    def _toggle_update(self, on):
        self.updater.set_enabled(on)
        self.say("Auto update is on: you will be told when a new version is ready." if on
                 else "Auto update is off: new versions will not be shown.")

    def _update_available(self, info):
        self.actUpdReady.setVisible(info is not None)
        if info is not None:
            self.actUpdReady.setText("Update ready: build %d - install..." % info["build"])

    def _toggle_sendto(self, on):
        self.settings.setValue("sendto", on)
        winutil.set_sendto(on)
        self.say("Added to the Send to menu." if on else "Removed from the Send to menu.")
