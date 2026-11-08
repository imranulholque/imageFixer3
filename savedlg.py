"""Save all: replace the original files, or save into a folder (chosen here, remembered)."""
import os

from PySide6.QtWidgets import (QButtonGroup, QCheckBox, QComboBox, QDialog, QFileDialog, QHBoxLayout, QLabel, QLineEdit,
                               QPushButton, QRadioButton, QVBoxLayout)


class SaveAllDialog(QDialog):
    def __init__(self, parent, n_total, n_replace, folder, last_mode="replace"):
        super().__init__(parent)
        self.setWindowTitle("Save all")
        self.setMinimumWidth(520)
        n_other = n_total - n_replace
        v = QVBoxLayout(self)
        v.setSpacing(10)
        v.addWidget(QLabel("<b>%d edited picture%s</b>" % (n_total, "" if n_total == 1 else "s")))
        self.rbReplace = QRadioButton("Replace the original files  (%d)" % n_replace)
        self.rbFolder = QRadioButton("Save into a folder")
        self.rbReplace.setEnabled(n_replace > 0)
        grp = QButtonGroup(self)
        grp.addButton(self.rbReplace)
        grp.addButton(self.rbFolder)
        v.addWidget(self.rbReplace)
        if n_other:
            note = QLabel("   %d picture%s came from the phone / clipboard (no original file): %s go to the folder below."
                          % (n_other, "" if n_other == 1 else "s", "it goes" if n_other == 1 else "they"))
            note.setObjectName("muted")
            note.setWordWrap(True)
            v.addWidget(note)
        v.addWidget(self.rbFolder)
        row = QHBoxLayout()
        self.edDir = QLineEdit(folder or "")
        self.edDir.setPlaceholderText("Choose a folder ...")
        self.btnBrowse = QPushButton("Browse...")
        self.btnBrowse.clicked.connect(self._browse)
        row.addWidget(self.edDir, 1)
        row.addWidget(self.btnBrowse)
        v.addLayout(row)
        frow = QHBoxLayout()
        frow.addWidget(QLabel("Format"))
        self.cbFmt = QComboBox()
        self.cbFmt.addItem("Same as the original (JPG or PNG)", "same")
        self.cbFmt.addItem("JPG", "jpg")
        self.cbFmt.addItem("PNG", "png")
        frow.addWidget(self.cbFmt, 1)
        v.addLayout(frow)
        self.chkRemember = QCheckBox("Remember this folder for every save")
        self.chkRemember.setChecked(True)
        v.addWidget(self.chkRemember)
        btns = QHBoxLayout()
        btns.addStretch(1)
        ok = QPushButton("Save")
        ok.setProperty("accent", True)
        ok.setDefault(True)
        cancel = QPushButton("Cancel")
        ok.clicked.connect(self._accept)
        cancel.clicked.connect(self.reject)
        btns.addWidget(cancel)
        btns.addWidget(ok)
        v.addLayout(btns)
        self._n_other = n_other
        if n_replace > 0 and last_mode != "folder":
            self.rbReplace.setChecked(True)
        else:
            self.rbFolder.setChecked(True)
        grp.buttonClicked.connect(lambda _b: self._sync())
        self._sync()

    def _sync(self):
        need_dir = self.rbFolder.isChecked() or self._n_other > 0
        for w in (self.edDir, self.btnBrowse, self.chkRemember):
            w.setEnabled(need_dir)
        self.cbFmt.setEnabled(self.rbFolder.isChecked())

    def _browse(self):
        d = QFileDialog.getExistingDirectory(self, "Choose the folder", self.edDir.text() or os.path.expanduser("~"))
        if d:
            self.edDir.setText(d)

    def _accept(self):
        need_dir = self.rbFolder.isChecked() or self._n_other > 0
        d = self.edDir.text().strip()
        if need_dir:
            if not d:
                self._browse()
                d = self.edDir.text().strip()
            if not d:
                return
            try:
                os.makedirs(d, exist_ok=True)
            except OSError:
                self.edDir.setFocus()
                return
        self.accept()

    def result_values(self):
        return {"mode": "replace" if self.rbReplace.isChecked() else "folder", "folder": self.edDir.text().strip(),
                "fmt": self.cbFmt.currentData(), "remember": self.chkRemember.isChecked()}
