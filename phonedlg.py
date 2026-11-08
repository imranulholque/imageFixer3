"""'Add from phone' DROPDOWN under the button (not a separate window): Same Wi-Fi / Internet tabs, QR code, link + Copy,
status, folder for received files."""
import os

import segno
from PySide6.QtCore import QEvent, QPoint, Qt, QTimer, QUrl
from PySide6.QtGui import QColor, QIntValidator, QDesktopServices, QGuiApplication, QPainter, QPixmap
from PySide6.QtWidgets import (QApplication, QButtonGroup, QFileDialog, QFrame, QGraphicsDropShadowEffect, QHBoxLayout, QLabel,
                               QPushButton, QLineEdit, QStackedWidget, QVBoxLayout, QWidget)

import phone
from theme import logo_pixmap


def qr_pixmap(text, px=300):
    qr = segno.make(text, error="m")
    rows = [list(r) for r in qr.matrix]
    n = len(rows)
    border = 2
    cell = max(1, px // (n + 2 * border))
    size = cell * (n + 2 * border)
    pm = QPixmap(size, size)
    pm.fill(QColor("white"))
    p = QPainter(pm)
    p.setPen(Qt.PenStyle.NoPen)
    p.setBrush(QColor("#0b0e13"))
    for y, row in enumerate(rows):
        for x, v in enumerate(row):
            if v:
                p.drawRect((x + border) * cell, (y + border) * cell, cell, cell)
    p.end()
    return pm


def default_dir():
    d = os.path.join(os.path.expanduser("~"), "Downloads", "ImageFixer")
    return d


class _Pane(QWidget):
    """QR code on the left; title, text, link box with Copy on the right."""

    def __init__(self, title, text):
        super().__init__()
        h = QHBoxLayout(self)
        h.setContentsMargins(0, 2, 0, 0)
        h.setSpacing(14)
        self.qr = QLabel()
        self.qr.setFixedSize(150, 150)
        self.qr.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.qr.setStyleSheet("background: white; border-radius: 12px;")
        h.addWidget(self.qr, 0, Qt.AlignmentFlag.AlignTop)
        r = QVBoxLayout()
        r.setSpacing(6)
        self.title = QLabel(title)
        self.title.setStyleSheet("font-size: 14px; font-weight: 700;")
        self.text = QLabel(text)
        self.text.setObjectName("muted")
        self.text.setWordWrap(True)
        self.linkBox = QFrame()
        self.linkBox.setStyleSheet("QFrame { background: #0e1219; border: 1px solid #262c3b; border-radius: 10px; }")
        lb = QHBoxLayout(self.linkBox)
        lb.setContentsMargins(8, 4, 4, 4)
        self.lblUrl = QLabel("")
        self.lblUrl.setWordWrap(True)
        self.lblUrl.setStyleSheet("border: none; font-family: Consolas, monospace; font-size: 11px;")
        self.lblUrl.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        self.btnCopy = QPushButton("Copy")
        self.btnCopy.setProperty("accent", True)
        self.btnCopy.setMinimumHeight(26)
        lb.addWidget(self.lblUrl, 1)
        lb.addWidget(self.btnCopy)
        self.extra = QVBoxLayout()
        self.extra.setSpacing(8)
        r.addWidget(self.title)
        r.addWidget(self.text)
        r.addWidget(self.linkBox)
        r.addLayout(self.extra)
        r.addStretch(1)
        h.addLayout(r, 1)

    def show_url(self, url):
        if url:
            self.qr.setPixmap(qr_pixmap(url, 140).scaled(138, 138, Qt.AspectRatioMode.KeepAspectRatio, Qt.TransformationMode.SmoothTransformation))
            self.lblUrl.setText(url)
        else:
            self.qr.clear()
            self.lblUrl.setText("")
        self.btnCopy.setEnabled(bool(url))


class PhoneDialog(QFrame):
    """A panel that drops down under the 'Add from phone' button, inside the main window."""

    def __init__(self, win):
        super().__init__(win)
        self.win = win
        self.setObjectName("phonePanel")
        self.setStyleSheet("QFrame#phonePanel { background: #12161d; border: 1px solid #343c52; border-radius: 16px; }")
        self.setAutoFillBackground(False)
        sh = QGraphicsDropShadowEffect(self)
        sh.setBlurRadius(38)
        sh.setOffset(0, 10)
        sh.setColor(QColor(0, 0, 0, 170))
        self.setGraphicsEffect(sh)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.hide()
        self._url = None
        self._net_url = None
        root = QVBoxLayout(self)
        root.setContentsMargins(14, 10, 14, 10)
        root.setSpacing(8)

        # ---- header
        head = QHBoxLayout()
        logo = QLabel()
        logo.setPixmap(logo_pixmap(22))
        t = QLabel("Add from phone")
        t.setStyleSheet("font-size: 14px; font-weight: 700;")
        t.setToolTip("Send pictures and scans from your phone to this PC")
        head.addWidget(logo)
        head.addSpacing(6)
        head.addWidget(t)
        head.addStretch(1)
        self.dot = QLabel("●")
        self.lblRun = QLabel("Starting")
        head.addWidget(self.dot)
        head.addWidget(self.lblRun)
        self.btnClose = QPushButton("\u2715")
        self.btnClose.setFixedSize(28, 26)
        self.btnClose.setToolTip("Close (the phone stays connected)")
        self.btnClose.clicked.connect(self.close_panel)
        head.addSpacing(8)
        head.addWidget(self.btnClose)
        root.addLayout(head)

        # ---- card with tabs
        card = QFrame()
        card.setObjectName("group")
        cv = QVBoxLayout(card)
        cv.setContentsMargins(12, 8, 12, 10)
        cv.setSpacing(6)
        tabs = QHBoxLayout()
        tabs.setSpacing(0)
        self.tabGroup = QButtonGroup(self)
        self.tabGroup.setExclusive(True)
        for i, n in enumerate(("Local network", "Internet")):
            b = QPushButton(n)
            b.setCheckable(True)
            b.setProperty("tab", True)
            b.setMinimumWidth(100)
            self.tabGroup.addButton(b, i)
            tabs.addWidget(b)
        tabs.addStretch(1)
        self.tabGroup.button(0).setChecked(True)
        cv.addLayout(tabs)
        self.stack = QStackedWidget()
        cv.addWidget(self.stack)

        # tab 1: same Wi-Fi
        self.wifi = _Pane("Scan to connect",
                          "Phone and PC on the same Wi-Fi: scan the code, the phone opens a web page (no app).")
        self.stack.addWidget(self.wifi)
        prow = QHBoxLayout()
        prow.setSpacing(8)
        prow.addWidget(QLabel("Port"))
        self.spinPort = QLineEdit(str(win.settings.value("phone_port", 8765, type=int) or 8765))
        self.spinPort.setValidator(QIntValidator(1024, 65535, self))
        self.spinPort.setMaxLength(5)
        self.spinPort.setFixedSize(84, 28)
        self.spinPort.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.spinPort.setStyleSheet("QLineEdit { background: #ffffff; color: #111111; border: 2px solid #5b8cff; "
                                    "border-radius: 6px; font-size: 14px; font-weight: 700; }")
        self.spinPort.returnPressed.connect(self._set_port)
        self.spinPort.setToolTip("The door number the phone connects to. Change it if another program uses it "
                                 "or your firewall needs a fixed number. The IP address is found automatically.")
        self.btnPort = QPushButton("Set")
        prow.addWidget(self.spinPort)
        prow.addWidget(self.btnPort)
        prow.addStretch(1)
        self.wifi.extra.addLayout(prow)

        # tab 2: internet
        self.net = _Pane("Use it from anywhere",
                         "For a phone on another network. The link lives only while this tab is open. "
                         "Anyone with the link can send pictures.")
        self.stack.addWidget(self.net)
        self.btnNet = QPushButton("Retry")
        self.btnNet.setProperty("accent", True)
        self.btnNet.setMinimumHeight(26)
        self.lblNet = QLabel("")
        self.lblNet.setObjectName("muted")
        self.lblNet.setWordWrap(True)
        self.btnNet.hide()
        self.net.extra.addWidget(self.btnNet)
        self.net.extra.addWidget(self.lblNet)
        root.addWidget(card, 1)

        # ---- where the pictures go
        sv = QFrame()
        sv.setObjectName("group")
        sl = QHBoxLayout(sv)
        sl.setContentsMargins(10, 6, 10, 6)
        sl.setSpacing(6)
        col = QVBoxLayout()
        col.setSpacing(2)
        c1 = QLabel("Save received files to")
        c1.setObjectName("sub")
        self.lblDir = QLabel("")
        self.lblDir.setObjectName("muted")
        self.lblDir.setWordWrap(True)
        col.addWidget(c1)
        col.addWidget(self.lblDir)
        self.btnDir = QPushButton("Change...")
        self.btnOpen = QPushButton("Open")
        self.btnStop = QPushButton("Stop")
        self.btnStop.setProperty("danger", True)
        self.btnStop.setToolTip("Stop receiving: the phone can no longer connect")
        sl.addLayout(col, 1)
        sl.addWidget(self.btnDir)
        sl.addWidget(self.btnOpen)
        sl.addWidget(self.btnStop)
        root.addWidget(sv)

        self.lblState = QLabel("")
        self.lblState.setObjectName("sub")
        self.lblState.setWordWrap(True)
        root.addWidget(self.lblState)

        # ---- wiring
        self.tabGroup.idClicked.connect(self._tab)
        self.wifi.btnCopy.clicked.connect(lambda: self._copy(self._url))
        self.net.btnCopy.clicked.connect(lambda: self._copy(self._net_url))
        self.btnPort.clicked.connect(self._set_port)
        self.btnDir.clicked.connect(self._choose_dir)
        self.btnOpen.clicked.connect(self._open_dir)
        self.btnStop.clicked.connect(self._stop_or_start)
        self.btnNet.clicked.connect(self._net_toggle)
        self.timer = QTimer(self)
        self.timer.setInterval(1200)
        self.timer.timeout.connect(self._tick)
        self._refresh_dir()
        self._set_running(False)

    # ---------------------------------------------------------------
    def _dir(self):
        d = self.win.settings.value("phone_dir", "", type=str)
        return d or default_dir()

    def _refresh_dir(self):
        self.lblDir.setText(self._dir())

    def _choose_dir(self):
        d = QFileDialog.getExistingDirectory(self, "Where should received files be saved?", self._dir())
        if d:
            self.win.settings.setValue("phone_dir", d)
            self._refresh_dir()
            if self.win.phone is not None:
                self.win.phone.inbox = d

    def _open_dir(self):
        d = self._dir()
        os.makedirs(d, exist_ok=True)
        QDesktopServices.openUrl(QUrl.fromLocalFile(d))

    def _set_running(self, on, text=None):
        self.dot.setStyleSheet("color: %s; font-size: 14px;" % ("#30a46c" if on else "#e5484d"))
        self.lblRun.setText(text or ("Running" if on else "Stopped"))
        self.btnStop.setText("Stop" if on else "Start")
        self.btnStop.setProperty("danger", bool(on))
        self.btnStop.style().unpolish(self.btnStop)
        self.btnStop.style().polish(self.btnStop)

    def show_front(self):
        """The button: opens the panel, or closes it when it is open already."""
        if self.isVisible():
            self.close_panel()
            return
        self._start()
        self.reposition()
        self.show()
        self.raise_()
        self.setFocus()
        self.timer.start()
        QApplication.instance().installEventFilter(self)

    def reposition(self):
        w = self.win
        btn = w.btnPhone
        pw = int(max(400, min(480, w.width() - 24)))
        ph = int(max(200, min(self.sizeHint().height(), w.height() - 70)))
        self.resize(pw, ph)
        p = btn.mapTo(w, QPoint(0, btn.height() + 8))
        x = int(min(max(10, p.x()), max(10, w.width() - pw - 10)))
        self.move(x, int(p.y()))

    def close_panel(self):
        QApplication.instance().removeEventFilter(self)
        self._stop_net()           # the internet link only lives while the panel is open
        self.tabGroup.button(0).setChecked(True)
        self.stack.setCurrentIndex(0)
        self.timer.stop()          # the local connection stays open: pictures can still arrive
        self.hide()

    def eventFilter(self, obj, e):
        """Click anywhere else in the window (not on the panel, not on the button) = the dropdown closes."""
        if self.isVisible() and e.type() == QEvent.Type.MouseButtonPress:
            try:
                if obj.window() is self.win:
                    gp = e.globalPosition().toPoint()
                    on_panel = self.rect().contains(self.mapFromGlobal(gp))
                    btn = self.win.btnPhone
                    on_btn = btn.rect().contains(btn.mapFromGlobal(gp))
                    if not on_panel and not on_btn:
                        self.close_panel()
            except Exception:
                pass
        return False

    def keyPressEvent(self, e):
        if e.key() == Qt.Key.Key_Escape:
            self.close_panel()
            return
        super().keyPressEvent(e)

    def _start(self, new_token=False):
        import phone as _p
        if new_token and self.win.phone is not None:
            self.win.phone.stop()
            self.win.phone = None
            self._stop_net()
        if self.win.phone is None:
            srv = _p.PhoneServer(self._dir(), self.win)
            srv.received.connect(self.win.phone_received)
            self.win.phone = srv
        srv = self.win.phone
        srv.inbox = self._dir()
        if not srv.running():
            if not srv.start(self._port()):
                self._set_running(False, "Could not start")
                self.lblState.setText("The connection could not be started (a firewall or another program may block it).")
                return
            self.win.settings.setValue("phone_port", srv.port)
        if srv.port != self._port():
            self.spinPort.setText(str(srv.port))
        self._url = None
        self._set_running(True)
        self._tick()

    def _set_port(self):
        """Use the port typed in the box (new QR code, new secret link)."""
        want = self._port()
        self.win.settings.setValue("phone_port", want)
        if self.win.phone is not None:
            self.win.phone.stop()
            self.win.phone = None
        self._stop_net()
        self._start()
        srv = self.win.phone
        if srv is not None and srv.running() and srv.port != want:
            self.lblState.setText("Port %d is busy - using %d instead." % (want, srv.port))

    def _port(self):
        try:
            p = int(self.spinPort.text())
            if 1024 <= p <= 65535:
                return p
        except ValueError:
            pass
        return int(self.win.settings.value("phone_port", 8765, type=int) or 8765)

    def _tab(self, i):
        self.stack.setCurrentIndex(i)
        if i == 1:
            self._net_start()
        else:
            self._stop_net()

    def _stop_or_start(self):
        if self.win.phone is not None and self.win.phone.running():
            self._stop()
        else:
            self._start()
            self.timer.start()

    def _stop(self):
        self._stop_net()
        if self.win.phone is not None:
            self.win.phone.stop()
            self.win.phone = None
        self._url = None
        self.wifi.show_url(None)
        self._set_running(False)
        self.lblState.setText("Stopped. Press Start to connect again.")
        self.timer.stop()

    # ---- internet tunnel
    def _net_toggle(self):
        self._stop_net()
        self._net_start()

    def _net_start(self):
        if self.win.phone_tunnel is not None:
            return
        srv = self.win.phone
        if srv is None or not srv.running():
            self._start()
            srv = self.win.phone
        if srv is None:
            return
        self.win.phone_tunnel = phone.Tunnel(srv.port)
        self.btnNet.hide()
        self.lblNet.setText("Connecting ... (can take up to 30 seconds)")
        self.timer.start()

    def _stop_net(self):
        if self.win.phone_tunnel is not None:
            self.win.phone_tunnel.stop()
            self.win.phone_tunnel = None
        self._net_url = None
        self.net.show_url(None)
        self.btnNet.hide()
        self.lblNet.setText("")

    def _copy(self, url):
        if url:
            QGuiApplication.clipboard().setText(url)
            self.lblState.setText("Link copied.")

    def _tick(self):
        """Every second or so: the address may have changed (Wi-Fi switched, PC woke up) -> new QR code."""
        srv = self.win.phone
        if srv is None or not srv.running():
            return
        url = srv.url()
        if url != self._url:
            self._url = url
            self.wifi.show_url(url)
        tun = self.win.phone_tunnel
        if tun is not None:
            if tun.error or tun.timed_out():
                self.lblNet.setText(tun.error or "Could not open the internet link (no answer after 30 seconds).")
                self._stop_net_keep_message()
            elif tun.url:
                nu = srv.link_for(tun.url)
                if nu != self._net_url:
                    self._net_url = nu
                    self.net.show_url(nu)
                    self.lblNet.setText("Link is ready. Open it on the phone (it works on mobile data).")
        if url is None:
            self.lblState.setText("No Wi-Fi / network found. Connect this computer to the same Wi-Fi as the phone "
                                  "(or use the Internet tab).")
        elif srv.phone_online():
            self.lblState.setText("✓ Phone connected   -   pictures received: %d" % srv.total)
        else:
            self.lblState.setText("Waiting for the phone ...   (pictures received: %d)" % srv.total)

    def _stop_net_keep_message(self):
        msg = self.lblNet.text()
        self._stop_net()
        self.lblNet.setText(msg)
        self.btnNet.show()
