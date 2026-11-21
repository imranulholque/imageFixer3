"""Image Fixer's print screen = the PC Print screen of the PrintMerged program, shown inside an Image Fixer window.

It is the SAME page (pcprint/static/index.html) and the SAME code behind it (pcprint/server.py, pcprint/printing.py):
printer + saved printer profiles, Printer Properties, paper, orientation, colour, copies, scaling, many pages on one
sheet, page numbers, Transform, perspective crop, Save PDF.  Nothing is drawn twice in another way.

How it works: a small local web server (this PC only, 127.0.0.1) runs inside Image Fixer; this window shows its page
with Qt's web engine.  The pictures are the files Image Fixer made with all edits (see MainWindow._export_for_print).
Settings, profiles and the save folder are kept in %APPDATA%\\PCPrint (the same place as the PC Print program).
"""
import os
import threading
from concurrent.futures import Future

from PySide6.QtCore import QObject, Qt, QTimer, QUrl, Signal, Slot
from PySide6.QtWebEngineCore import QWebEnginePage, QWebEngineProfile
from PySide6.QtWebEngineWidgets import QWebEngineView
from PySide6.QtWidgets import QApplication, QDialog, QFileDialog, QMessageBox, QVBoxLayout

from numpanel import NumStore, spec_from_web, spec_to_web

PORT = 53187                      # always the same port when it is free, so the page remembers its own small settings
FILTER = "PDF and images (*.pdf *.jpg *.jpeg *.png *.webp *.bmp *.gif *.tif *.tiff)"
_state = {"server": None, "http": None, "gui": None, "profile": None, "bridge": None}


class _OnGui(QObject):
    """Lets the server's threads ask the window thread for something (a file window)."""
    _call = Signal(object)

    def __init__(self):
        super().__init__()
        self._call.connect(self._run, Qt.ConnectionType.QueuedConnection)

    @Slot(object)
    def _run(self, job):
        fn, fut = job
        try:
            fut.set_result(fn())
        except Exception as e:                    # noqa: BLE001 - handed back to the asking thread
            fut.set_exception(e)

    def ask(self, fn):
        fut = Future()
        self._call.emit((fn, fut))
        return fut.result()


class _NumBridge(QObject):
    """A page number change made on the print screen (server thread) -> the shared setting (window thread)."""
    from_page = Signal(object)

    def __init__(self):
        super().__init__()
        self.store = NumStore.get()
        self.from_page.connect(self._apply, Qt.ConnectionType.QueuedConnection)

    @Slot(object)
    def _apply(self, d):
        try:
            self.store.set_spec(spec_from_web(d, self.store.spec()), source="page")
        except Exception:
            pass


def _start_server():
    """Starts the local server once (kept until Image Fixer closes). Returns the server module."""
    if _state["server"] is not None:
        return _state["server"]
    from werkzeug.serving import make_server
    from pcprint import server
    http = None
    for port in (PORT, 0):
        try:
            http = make_server("127.0.0.1", port, server.app, threaded=True)
            break
        except OSError:
            continue
    if http is None:
        raise OSError("no free port")
    threading.Thread(target=http.serve_forever, daemon=True).start()
    _state["server"], _state["http"] = server, http
    return server


def _profile():
    """The web engine's own folder (page memory like panel width), apart from Image Fixer's other data."""
    if _state["profile"] is None:
        base = os.path.join(os.environ.get("APPDATA") or os.path.expanduser("~"), "ImageFixer", "printscreen")
        p = QWebEngineProfile("ImageFixerPrint")
        try:
            os.makedirs(base, exist_ok=True)
            p.setPersistentStoragePath(base)
            p.setCachePath(os.path.join(base, "cache"))
            p.setPersistentCookiesPolicy(QWebEngineProfile.PersistentCookiesPolicy.NoPersistentCookies)
        except Exception:
            pass
        _state["profile"] = p
    return _state["profile"]


def _forget_files(server):
    """Empties the file list of the print screen (the list is never kept between two openings)."""
    with server.SLOCK:
        for fid in list(server.FILES):
            server.drop_file(fid)
        server.ORDER.clear()
        server.GONE.clear()
        server.bump()


class PrintScreen(QDialog):
    def __init__(self, parent, settings, server):
        super().__init__(parent, Qt.WindowType.Window | Qt.WindowType.WindowTitleHint | Qt.WindowType.WindowSystemMenuHint
                         | Qt.WindowType.WindowMinMaxButtonsHint | Qt.WindowType.WindowCloseButtonHint)
        self.settings = settings
        self.server = server
        self.store = NumStore.get()
        self.store.changed.connect(self._numbers_changed)       # changed in the main window's Numbers tab -> the page follows
        self.setWindowTitle("PC Print")
        self.setWindowIcon(parent.windowIcon())
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        self.view = QWebEngineView(self)
        self.view.setPage(QWebEnginePage(_profile(), self.view))
        self.view.setContextMenuPolicy(Qt.ContextMenuPolicy.NoContextMenu)
        lay.addWidget(self.view)
        screen = QApplication.primaryScreen().availableGeometry()
        self.resize(min(1120, screen.width()), min(820, screen.height()))      # the size of the PC Print window
        geo = settings.value("printscreen_geometry")
        if geo is not None:
            try:
                self.restoreGeometry(geo)
            except Exception:
                pass

    def _numbers_changed(self, source):
        if source != "page":
            self.server.push_num(spec_to_web(self.store.spec()))

    def done(self, r):
        try:
            self.store.changed.disconnect(self._numbers_changed)
        except Exception:
            pass
        self.server.HOOKS["num"] = None
        try:
            self.settings.setValue("printscreen_geometry", self.saveGeometry())
        except Exception:
            pass
        _forget_files(self.server)
        self.view.setUrl(QUrl("about:blank"))
        super().done(r)


def open_print_screen(win, settings, files, ticked=None):
    """Shows the PC Print screen with these picture files (all open pictures, in order, so each page keeps its own
    number). ticked = the files that start ticked (None = all). Returns False when it cannot start (the caller then
    uses the older Qt print screen)."""
    try:
        server = _start_server()
    except Exception as e:                                       # a missing library, no free port ...
        try:
            import traceback
            from app import log_error
            log_error("PC Print screen could not start:\n" + traceback.format_exc())
        except Exception:
            pass
        QMessageBox.warning(win, "Print", "The new print screen could not start (%s).\nThe older print screen opens instead." % e)
        return False
    if _state["gui"] is None:
        _state["gui"] = _OnGui()                                 # made here = lives in the window thread
    gui = _state["gui"]
    dlg = PrintScreen(win, settings, server)
    # the file windows of the page (Add files, Choose folder) are Qt windows of this program
    server.HOOKS["pick"] = lambda: list(gui.ask(lambda: QFileDialog.getOpenFileNames(dlg, "Add files", "", FILTER)[0]))
    server.HOOKS["pickdir"] = lambda: gui.ask(lambda: QFileDialog.getExistingDirectory(dlg, "Choose the save folder"))
    if _state["bridge"] is None:
        _state["bridge"] = _NumBridge()
    bridge = _state["bridge"]
    server.HOOKS["num"] = lambda d: bridge.from_page.emit(dict(d))      # the page changed a page number setting
    server.push_num(spec_to_web(NumStore.get().spec()))                  # the page starts with Image Fixer's setting
    _forget_files(server)
    server.register(files, sort=False)                           # in the order of Image Fixer's pictures
    keep = {os.path.abspath(f) for f in (files if ticked is None else ticked)}
    server.EMBED_UNTICK[:] = [fid for fid in server.ORDER if os.path.abspath(server.FILES[fid]["path"]) not in keep]
    dlg.view.setUrl(QUrl("http://127.0.0.1:%d/" % _state["http"].server_port))
    QTimer.singleShot(0, dlg.view.setFocus)
    dlg.exec()
    dlg.deleteLater()
    return True
