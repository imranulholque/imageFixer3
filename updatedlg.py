"""Auto update, window part: background check, notification, 'Update now?' question, download progress.
Setting 'auto_update' (Settings menu): ON = the program looks for a new build by itself, tells you, asks, then installs.
OFF = it never looks and never shows anything about updates (only the 'Check for updates now' menu item still works)."""
import threading

from PySide6.QtCore import QObject, QTimer, Qt, Signal
from PySide6.QtWidgets import QMessageBox, QProgressDialog

import updater

CHECK_EVERY_MS = 5 * 60 * 1000               # while the program stays open (GitHub allows 60 asks / hour, this is 12)
FIRST_CHECK_MS = 10 * 1000                   # a few seconds after start, so start-up stays fast


class UpdateManager(QObject):
    checked = Signal(object, str, bool)      # update info or None, error text, was it asked by the user
    progress = Signal(int, int)
    staged = Signal(str)
    failed = Signal(str)
    available_changed = Signal(object)       # update info, or None when there is nothing to offer

    def __init__(self, win):
        super().__init__(win)
        self.win = win
        self.info = None
        self._busy = False
        self._asked = set()                  # builds we already told the user about in this run
        self._toast_is_update = False
        self._dlg = None
        self._cancel = threading.Event()
        self.timer = QTimer(self)
        self.timer.timeout.connect(lambda: self.check(False))
        self.checked.connect(self._on_checked)
        self.progress.connect(self._on_progress)
        self.staged.connect(self._on_staged)
        self.failed.connect(self._on_failed)

    # ---------------------------------------------------------------- setting
    def enabled(self):
        return self.win.settings.value("auto_update", True, type=bool)

    def set_enabled(self, on):
        self.win.settings.setValue("auto_update", bool(on))
        if on:
            self.start()
        else:
            self.timer.stop()
            self.info = None
            self.available_changed.emit(None)    # nothing about updates is shown any more

    def start(self):
        """Called once after the window is built, and again when the setting is switched on."""
        if not self.enabled() or not updater.can_self_update():
            return
        self.timer.start(CHECK_EVERY_MS)
        QTimer.singleShot(FIRST_CHECK_MS, lambda: self.check(False))

    # --------------------------------------------------------------- checking
    def check(self, manual=False):
        if self._busy or (not manual and not (self.enabled() and updater.can_self_update())):
            return
        self._busy = True

        def work():
            try:
                self.checked.emit(updater.check(), "", manual)
            except Exception as e:
                self.checked.emit(None, str(e), manual)
        threading.Thread(target=work, daemon=True).start()

    def _on_checked(self, info, err, manual):
        self._busy = False
        if err:
            if manual:
                QMessageBox.warning(self.win, "Image Fixer update", err)
            return
        if info is None:
            if manual:
                QMessageBox.information(self.win, "Image Fixer update",
                                        "You have the newest version (%s)." % updater.version_text())
            return
        if not manual and not self.enabled():
            return                                   # switched off while the check was running: say nothing
        self.info = info
        self.available_changed.emit(info)
        if manual:
            self.ask(info)
        elif info["build"] not in self._asked:
            self._asked.add(info["build"])
            self._notify(info)

    def _notify(self, info):
        """Tell the user first (tray notification); if the window is open, ask right away."""
        tray = getattr(self.win, "tray", None)
        shown = self.win.isVisible() and not self.win.isMinimized()
        if tray is not None:
            try:
                tray.messageClicked.disconnect(self._toast_clicked)
            except (TypeError, RuntimeError):
                pass
            tray.messageClicked.connect(self._toast_clicked)
            self._toast_is_update = True
            tray.showMessage("Image Fixer update",
                             "A new version (build %d) is ready. %s" % (
                                 info["build"], "" if shown else "Click here to install it."),
                             tray.icon(), 15000)
        if shown or tray is None:
            self.ask(info)

    def _toast_clicked(self):
        if self._toast_is_update and self.info is not None:
            self._toast_is_update = False
            self.win.show_front()
            self.ask(self.info)

    # ---------------------------------------------------------------- asking
    def ask(self, info):
        if self._dlg is not None:
            return
        box = QMessageBox(self.win)
        box.setWindowTitle("Image Fixer update")
        box.setIcon(QMessageBox.Icon.Question)
        box.setText("A new version of Image Fixer is ready: build %d  (you have %s)." % (info["build"], updater.version_text()))
        if updater.can_self_update():
            n = sum(1 for c in self.win.carousel.cards if c.is_unsaved())
            extra = ("\n\n%d edited picture(s) are not saved yet - you will be asked about them first." % n) if n else ""
            if not updater.folder_writable():
                box.setInformativeText("The program folder is write-protected, so it cannot update itself there. "
                                       "Move the ImageFixer folder to a normal folder (for example C:\\Tools\\ImageFixer)." )
                go = None
            else:
                box.setInformativeText("Install it now? The program closes, updates itself and opens again. "
                                       "Your settings are kept." + extra)
                go = box.addButton("Update now", QMessageBox.ButtonRole.AcceptRole)
        else:
            box.setInformativeText("This copy cannot install updates by itself. Open the download page to get the new version.")
            go = box.addButton("Open download page", QMessageBox.ButtonRole.AcceptRole)
        if info.get("notes"):
            box.setDetailedText(info["notes"])
        box.addButton("Later", QMessageBox.ButtonRole.RejectRole)
        box.exec()
        if go is None or box.clickedButton() is not go:
            return
        if updater.can_self_update():
            self._download(info)
        else:
            from PySide6.QtCore import QUrl
            from PySide6.QtGui import QDesktopServices
            QDesktopServices.openUrl(QUrl(info["page"]))

    # ------------------------------------------------------------ installing
    def _download(self, info):
        self._cancel.clear()
        dlg = QProgressDialog("Downloading build %d ..." % info["build"], "Cancel", 0, 100, self.win)
        dlg.setWindowTitle("Image Fixer update")
        dlg.setWindowModality(Qt.WindowModality.WindowModal)
        dlg.setMinimumDuration(0)
        dlg.setAutoClose(False)
        dlg.setAutoReset(False)
        dlg.setValue(0)
        dlg.canceled.connect(self._cancel.set)
        self._dlg = dlg
        dlg.show()

        def work():
            try:
                self.staged.emit(updater.download_and_stage(
                    info, lambda a, b: self.progress.emit(a, b), self._cancel.is_set))
            except Exception as e:
                self.failed.emit(str(e))
        threading.Thread(target=work, daemon=True).start()

    def _on_progress(self, done, total):
        if self._dlg is not None and total > 0:
            self._dlg.setValue(min(99, int(done * 100 / total)))

    def _close_dlg(self):
        d, self._dlg = self._dlg, None
        if d is not None:
            d.close()
            d.deleteLater()

    def _on_failed(self, text):
        self._close_dlg()
        updater.log("update failed: " + text)
        if text != "cancelled":
            QMessageBox.warning(self.win, "Image Fixer update", "The update could not be installed.\n\n%s\n\n"
                                "Nothing was changed. You can try again from Settings." % text)

    def _on_staged(self, root):
        self._close_dlg()
        self.win.quit_app()                          # asks about unsaved pictures; quitting stays False if the user says No
        if not self.win.quitting:
            updater.discard()
            return
        try:
            updater.launch_installer(root)
        except Exception as e:
            updater.log("could not start the installer: %s" % e)
