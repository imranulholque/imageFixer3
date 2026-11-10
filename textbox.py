"""Text boxes (Bangla + English) for Image Fixer.

A text box is a normal picture layer (card.py: move / resize / rotate / bend / opacity / order / undo / save / print all
work), but its picture is made from the text: Qt draws it (with proper Bangla letter shaping), then it is handed to the
layer as RGB + alpha.  The layer keeps the text settings in layer["text"], so the text can be changed at any time.

  render()      text settings -> picture of the text
  new_layer() / restyle() / refit() / duplicate()   what card.py calls
  TextPanel     the side tab "Aa": text, typing mode, font, size, bold / italic / underline ..., colors, effects
"""
import io
import json
import math
import os
import sys

from PIL import Image, ImageFilter
from PySide6.QtCore import QBuffer, QByteArray, QIODevice, QRectF, QSettings, Qt, Signal
from PySide6.QtGui import (QBrush, QColor, QFont, QFontDatabase, QImage, QPainter, QPen, QTextBlockFormat,
                           QTextCharFormat, QTextCursor, QTextDocument)
from PySide6.QtWidgets import (QButtonGroup, QCheckBox, QColorDialog, QComboBox, QDoubleSpinBox, QFileDialog,
                               QHBoxLayout, QLabel, QPlainTextEdit, QPushButton, QVBoxLayout, QWidget)

import banglatext

LOGICAL = 100.0            # the text is laid out with a 100 unit font; the picture is scaled from that
A4_PT = 842.0              # size in "pt" means: as on an A4 page (12 pt = normal body text)
MAX_BITMAP = 3600          # biggest side of the picture made from the text (px)

DEFAULT_STYLE = {
    "family": "", "size": 28.0, "color": "#111111", "bold": False, "italic": False, "underline": False,
    "strike": False, "case": "normal", "align": "left", "spacing": 0, "line": 125, "wrap": 0.0, "ansi": False,
    "outline": False, "outline_color": "#ffffff", "outline_w": 6,
    "shadow": False, "shadow_color": "#000000", "shadow_blur": 8, "shadow_dist": 8, "shadow_op": 60,
    "hl": False, "hl_color": "#fff2a8",
}
STYLE_KEYS = tuple(DEFAULT_STYLE)

# ------------------------------------------------------------------------------------------------------------ fonts
BANGLA_BASIC = ["Nirmala UI", "Vrinda", "Shonar Bangla", "Kalpurush", "SolaimanLipi", "Siyam Rupali", "Noto Sans Bengali",
                "Hind Siliguri", "Lohit Bengali", "Akaash", "Mukti", "Bangla"]
BANGLA_STYLISH = ["Noto Serif Bengali", "Tiro Bangla", "Galada", "Atma", "Baloo Da 2", "Mina", "Anek Bangla", "Charukola",
                  "Li Ador Noroma UI", "Ekushey Sharifa", "Noto Sans Bengali UI", "Hind Madurai"]
ENGLISH_BASIC = ["Arial", "Segoe UI", "Calibri", "Times New Roman", "Georgia", "Verdana", "Tahoma", "Trebuchet MS",
                 "Cambria", "Consolas", "Courier New", "Candara", "Corbel"]
ENGLISH_STYLISH = ["Impact", "Comic Sans MS", "Brush Script MT", "Segoe Script", "Segoe Print", "Lucida Handwriting",
                   "Ink Free", "Gabriola", "Monotype Corsiva", "Bauhaus 93", "Algerian", "Rockwell Extra Bold", "Forte",
                   "Jokerman", "Mistral", "Papyrus", "Playbill", "Stencil", "Old English Text MT", "Vladimir Script",
                   "Freestyle Script", "Showcard Gothic", "Broadway", "Harrington", "Matura MT Script Capitals",
                   "Magneto", "Chiller", "Ravie", "Curlz MT", "Kristen ITC", "Berlin Sans FB Demi", "Edwardian Script ITC"]

_user_loaded = False
_user_fams = []


def _settings():
    return QSettings("ImageFixer", "ImageFixer")


def user_font_dirs():
    """Fonts put in one of these folders are loaded every time the program starts."""
    base = os.path.dirname(sys.executable) if getattr(sys, "frozen", False) else os.path.dirname(os.path.abspath(__file__))
    appdata = os.environ.get("APPDATA") or os.path.expanduser("~")
    return [os.path.join(base, "fonts"), os.path.join(appdata, "ImageFixer", "fonts")]


def _add_file(path):
    fid = QFontDatabase.addApplicationFont(path)
    if fid < 0:
        return []
    fams = list(QFontDatabase.applicationFontFamilies(fid))
    for f in fams:
        if f not in _user_fams:
            _user_fams.append(f)
    return fams


def load_user_fonts():
    """Once per run: the fonts folders and the fonts added with 'Add font...' are loaded."""
    global _user_loaded
    if _user_loaded:
        return
    _user_loaded = True
    for d in user_font_dirs():
        try:
            for name in sorted(os.listdir(d)):
                if name.lower().endswith((".ttf", ".otf", ".ttc")):
                    _add_file(os.path.join(d, name))
        except OSError:
            pass
    saved = _settings().value("text_fonts", [])
    if isinstance(saved, str):
        saved = [saved]
    for p in saved or []:
        if os.path.isfile(p):
            _add_file(p)


def add_font_file(path):
    """A font file chosen by the user: loaded now and remembered for the next start.  Returns its family names."""
    load_user_fonts()
    fams = _add_file(path)
    if fams:
        st = _settings()
        saved = st.value("text_fonts", [])
        if isinstance(saved, str):
            saved = [saved]
        saved = list(saved or [])
        if path not in saved:
            saved.append(path)
            st.setValue("text_fonts", saved)
    return fams


def font_groups():
    """[(title, [family, ...]), ...] for the font list: Bangla and English fonts that this computer really has."""
    load_user_fonts()
    fams = set(QFontDatabase.families())
    used = set()

    def pick(names):
        out = []
        for n in names:
            if n in fams and n not in used:
                used.add(n)
                out.append(n)
        return out
    groups = [("Your fonts", pick(sorted(_user_fams))),
              ("Bijoy (ANSI) fonts", pick(sorted(f for f in fams if banglatext.is_bijoy_font(f)))),
              ("Bangla - basic", pick(BANGLA_BASIC)), ("Bangla - stylish", pick(BANGLA_STYLISH)),
              ("English - basic", pick(ENGLISH_BASIC)), ("English - stylish", pick(ENGLISH_STYLISH)),
              ("All other fonts", [f for f in sorted(fams) if f not in used and not f.startswith("@")])]
    return [(t, n) for t, n in groups if n]


def default_family():
    fams = set(QFontDatabase.families())
    for n in ("Nirmala UI", "Shonar Bangla", "Vrinda", "Kalpurush", "Noto Sans Bengali", "Segoe UI", "Arial"):
        if n in fams:
            return n
    return QFont().family()


# ---------------------------------------------------------------------------------------------- remembered style
def new_style():
    """The settings of a new text box: the last look the user used (font, size, color ...)."""
    t = dict(DEFAULT_STYLE)
    try:
        saved = json.loads(_settings().value("text_style", "") or "{}")
        for k in STYLE_KEYS:
            if k in saved and type(saved[k]) == type(DEFAULT_STYLE[k]):
                t[k] = saved[k]
    except Exception:
        pass
    t["wrap"] = 0.0                         # a new box starts as wide as its text
    t["text"] = "Text"
    return t


def remember_style(t):
    try:
        _settings().setValue("text_style", json.dumps({k: t[k] for k in STYLE_KEYS if k in t}))
    except Exception:
        pass


# ------------------------------------------------------------------------------------------------------- rendering
_ALIGN = {"left": Qt.AlignmentFlag.AlignLeft, "center": Qt.AlignmentFlag.AlignHCenter,
          "right": Qt.AlignmentFlag.AlignRight, "justify": Qt.AlignmentFlag.AlignJustify}


def _font(t):
    f = QFont(t.get("family") or default_family())
    f.setPixelSize(int(LOGICAL))
    f.setBold(bool(t["bold"]))
    f.setItalic(bool(t["italic"]))
    f.setUnderline(bool(t["underline"]))
    f.setStrikeOut(bool(t["strike"]))
    f.setKerning(True)
    f.setHintingPreference(QFont.HintingPreference.PreferNoHinting)       # same layout at every picture size
    f.setStyleStrategy(QFont.StyleStrategy.PreferAntialias)
    cap = {"upper": QFont.Capitalization.AllUppercase, "lower": QFont.Capitalization.AllLowercase,
           "smallcaps": QFont.Capitalization.SmallCaps, "title": QFont.Capitalization.Capitalize}.get(t["case"])
    f.setCapitalization(cap if cap is not None else QFont.Capitalization.MixedCase)
    if t["spacing"]:
        f.setLetterSpacing(QFont.SpacingType.AbsoluteSpacing, LOGICAL * float(t["spacing"]) / 100.0)
    return f


def _doc(t, text, margin, color, width=None, outline=None):
    doc = QTextDocument()
    doc.setDocumentMargin(float(margin))
    doc.setUseDesignMetrics(True)
    doc.setDefaultFont(_font(t))
    doc.setPlainText(text)
    cur = QTextCursor(doc)
    cur.select(QTextCursor.SelectionType.Document)
    cf = QTextCharFormat()
    cf.setForeground(QBrush(QColor(color)))
    if outline is not None:
        cf.setTextOutline(QPen(QColor(outline[0]), float(outline[1]), Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap,
                               Qt.PenJoinStyle.RoundJoin))
    cur.mergeCharFormat(cf)
    bf = QTextBlockFormat()
    bf.setAlignment(_ALIGN.get(t["align"], Qt.AlignmentFlag.AlignLeft))
    bf.setLineHeight(float(t["line"]), 1)               # 1 = proportional height (percent of the normal line)
    cur.mergeBlockFormat(bf)
    if width is not None:
        doc.setTextWidth(float(width))
    return doc


def _to_pil(img):
    ba = QByteArray()
    buf = QBuffer(ba)
    buf.open(QIODevice.OpenModeFlag.WriteOnly)
    img.save(buf, "PNG")
    buf.close()
    return Image.open(io.BytesIO(bytes(ba.data()))).convert("RGBA")


def _paint(w, h, s, fn):
    img = QImage(int(w), int(h), QImage.Format.Format_ARGB32_Premultiplied)
    img.fill(Qt.GlobalColor.transparent)
    p = QPainter(img)
    p.setRenderHint(QPainter.RenderHint.Antialiasing, True)
    p.setRenderHint(QPainter.RenderHint.TextAntialiasing, True)
    p.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform, True)
    p.scale(s, s)
    fn(p)
    p.end()
    return _to_pil(img)


def u_from_size(t, page_w, page_h):
    """Page pixels per layout unit for the size (pt) of the text."""
    return max(float(t["size"]) / A4_PT * max(page_w, page_h), 1.0) / LOGICAL


def size_from_u(u, page_w, page_h):
    return u * LOGICAL / max(page_w, page_h) * A4_PT


def render(t, page_w, page_h, u=None):
    """Text settings -> (rgb PIL, alpha PIL 'L', layout width, layout height, page px per layout unit).
    The picture is sharp: it is made about twice as big as it is shown (never smaller than the layout)."""
    text = t["text"] if t["text"].strip() else " "
    if t.get("ansi"):
        text = banglatext.unicode_to_bijoy(text)
    if u is None:
        u = u_from_size(t, page_w, page_h)
    ow = float(t["outline_w"]) if t["outline"] else 0.0
    reach = (float(t["shadow_dist"]) + 2.0 * float(t["shadow_blur"])) if t["shadow"] else 0.0
    margin = 4.0 + ow + reach + (14.0 if t["hl"] else 0.0)
    wrap = float(t.get("wrap") or 0.0)
    probe = _doc(t, text, margin, t["color"])
    if wrap > 0:
        width = max(wrap * page_w / u + 2.0 * margin, 8.0 * LOGICAL / 10.0)
    else:
        width = probe.idealWidth() + 0.5
    probe.setTextWidth(width)
    sz = probe.size()
    lw, lh = max(4.0, math.ceil(sz.width())), max(4.0, math.ceil(sz.height()))
    s = min(max(1.0, u * 2.0), MAX_BITMAP / max(lw, lh))
    W, H = max(1, int(math.ceil(lw * s))), max(1, int(math.ceil(lh * s)))

    def draw_text(p):
        if ow > 0:
            _doc(t, text, margin, t["outline_color"], width, (t["outline_color"], 2.0 * ow)).drawContents(p)
        _doc(t, text, margin, t["color"], width).drawContents(p)
    txt = _paint(W, H, s, draw_text)
    out = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    if t["hl"]:
        def draw_bg(p):
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(QBrush(QColor(t["hl_color"])))
            p.drawRoundedRect(QRectF(0, 0, lw, lh), 10.0, 10.0)
        out = Image.alpha_composite(out, _paint(W, H, s, draw_bg))
    if t["shadow"]:
        d = float(t["shadow_dist"]) * s * 0.7071
        a = Image.new("L", (W, H), 0)
        a.paste(txt.split()[3], (int(round(d)), int(round(d))))
        if t["shadow_blur"] > 0:
            a = a.filter(ImageFilter.GaussianBlur(float(t["shadow_blur"]) * s * 0.5))
        k = max(0, min(100, int(t["shadow_op"]))) / 100.0
        a = a.point(lambda v, k=k: int(v * k))
        sh = Image.new("RGBA", (W, H), QColor(t["shadow_color"]).getRgb()[:3] + (255,))
        sh.putalpha(a)
        out = Image.alpha_composite(out, sh)
    out = Image.alpha_composite(out, txt)
    return out.convert("RGB"), out.split()[3], lw, lh, u


# -------------------------------------------------------------------------------------------------- layer helpers
def _name(t):
    for line in t["text"].splitlines():
        if line.strip():
            line = line.strip()
            return line if len(line) <= 24 else line[:21] + "..."
    return "Text"


def _corners_px(l, W, H):
    n, g = l["n"], l["grid"]
    q = (g[0], g[n], g[n * (n + 1)], g[-1])                        # TL TR BL BR
    return [(x * W, y * H) for x, y in q]


def _grid_from(tl, w, h, ang, W, H):
    ca, sa = math.cos(ang), math.sin(ang)
    tr = (tl[0] + ca * w, tl[1] + sa * w)
    bl = (tl[0] - sa * h, tl[1] + ca * h)
    br = (tr[0] - sa * h, tr[1] + ca * h)
    return [(p[0] / W, p[1] / H) for p in (tl, tr, bl, br)]


def new_layer(lid, t, page_size, center=(0.5, 0.5)):
    """A new text box layer, centered on the page."""
    W, H = page_size
    rgb, a, lw, lh, u = render(t, W, H)
    wn, hn = u * lw / W, u * lh / H
    if wn > 0.9:                                                   # a very long text: shown smaller to fit the page
        k = 0.9 / wn
        wn, hn = wn * k, hn * k
        t = dict(t, size=t["size"] * k)
        rgb, a, lw, lh, u = render(t, W, H)
        wn, hn = u * lw / W, u * lh / H
    x0, y0 = center[0] - wn / 2, center[1] - hn / 2
    return {"id": lid, "name": _name(t), "rgb": rgb, "a": a, "src": None, "sa": None, "bg": {},
            "grid": [(x0, y0), (x0 + wn, y0), (x0, y0 + hn), (x0 + wn, y0 + hn)], "n": 1, "opacity": 100,
            "visible": True, "text": dict(t), "tl": (lw, lh)}


def restyle(l, patch, page_size):
    """The text box with changed settings (text, font, size ...).  It stays where it is: the corner that matches the
    alignment (left / middle / right of the top edge) keeps its place, the turn is kept, the size shown stays the same
    unless 'size' is changed."""
    W, H = page_size
    t = dict(l["text"])
    t.update(patch)
    (x0, y0), (x1, y1) = _corners_px(l, W, H)[:2]
    old_w = math.hypot(x1 - x0, y1 - y0)
    lw0 = l["tl"][0]
    cur_u = old_w / lw0 if old_w > 1e-3 and lw0 > 0 else None
    if "size" in patch or cur_u is None:
        u = u_from_size(t, W, H)
    else:
        u = cur_u
    t["size"] = size_from_u(u, W, H)
    rgb, a, lw, lh, u = render(t, W, H, u)
    ang = math.atan2(y1 - y0, x1 - x0)
    ax = {"center": 0.5, "right": 1.0}.get(t["align"], 0.0)
    anchor = (x0 + ax * (x1 - x0), y0 + ax * (y1 - y0))
    nw, nh = u * lw, u * lh
    tl = (anchor[0] - math.cos(ang) * ax * nw, anchor[1] - math.sin(ang) * ax * nw)
    return dict(l, name=_name(t), rgb=rgb, a=a, text=t, tl=(lw, lh), n=1, grid=_grid_from(tl, nw, nh, ang, W, H))


def refit(l, page_size):
    """The box was resized with the mouse: the font size follows (and the picture of the text is made sharp again)."""
    W, H = page_size
    t = l["text"]
    lw, lh = l["tl"]
    (x0, y0), (x1, y1) = _corners_px(l, W, H)[:2]
    new_w = math.hypot(x1 - x0, y1 - y0)
    u_old = u_from_size(t, W, H)
    u_new = new_w / lw if lw > 0 else u_old
    if abs(u_new / u_old - 1.0) < 0.015 or u_new <= 0:
        return l
    t2 = dict(t)
    t2["size"] = min(900.0, max(1.0, size_from_u(u_new, W, H)))
    if t2.get("wrap"):
        t2["wrap"] = t["wrap"] * u_new / u_old
    rgb, a, lw2, lh2, _ = render(t2, W, H, u_new)
    return dict(l, rgb=rgb, a=a, text=t2, tl=(lw2, lh2))


def duplicate(l, new_id, offset=0.03):
    """A copy of a picture / text box layer (the pixels are shared: they are never changed in place)."""
    g = [(x + offset, y + offset) for x, y in l["grid"]]
    nl = dict(l, id=new_id, grid=g)
    if l.get("text") is not None:
        nl["text"] = dict(l["text"])
    return nl


# --------------------------------------------------------------------------------------------------------- widgets
class _Swatch(QPushButton):
    colorChanged = Signal(str)

    def __init__(self, color="#000000", tip="", parent=None):
        super().__init__(parent)
        self._c = color
        self.setFixedSize(36, 26)
        self.setToolTip(tip)
        self._look()
        self.clicked.connect(self._pick)

    def color(self):
        return self._c

    def set_color(self, c):
        self._c = c
        self._look()

    def _look(self):
        self.setStyleSheet("QPushButton { background: %s; border: 1px solid #6b7391; border-radius: 6px; }" % self._c)

    def _pick(self):
        c = QColorDialog.getColor(QColor(self._c), self.window(), "Choose a color")
        if c.isValid():
            self._c = c.name()
            self._look()
            self.colorChanged.emit(self._c)


class TextEdit(QPlainTextEdit):
    """The text.  In phonetic mode English letters become Bangla while you type (Avro style): the word you are typing
    is converted again after every key, so "ami" shows আমি, and a space / punctuation closes the word."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.phonetic = False
        self._buf = ""
        self._start = -1
        self._len = 0
        self._busy = False
        self.cursorPositionChanged.connect(self._moved)

    def _reset(self):
        self._buf, self._start, self._len = "", -1, 0

    def _moved(self):
        if self._busy:
            return
        c = self.textCursor()
        if self._start >= 0 and not c.hasSelection() and c.position() == self._start + self._len:
            return
        self._reset()

    def _show(self):
        self._busy = True
        try:
            cur = self.textCursor()
            if cur.hasSelection():
                cur.removeSelectedText()
                self._reset_keep_buf()
            if self._start < 0:
                self._start = cur.position()
            txt = banglatext.phonetic(self._buf)
            cur.beginEditBlock()
            cur.setPosition(self._start)
            cur.setPosition(self._start + self._len, QTextCursor.MoveMode.KeepAnchor)
            cur.insertText(txt)
            cur.endEditBlock()
            self._len = len(txt)
            self.setTextCursor(cur)
            if not self._buf:
                self._reset()
        finally:
            self._busy = False

    def _reset_keep_buf(self):
        self._start, self._len = -1, 0

    def keyPressEvent(self, e):
        if self.phonetic and not (e.modifiers() & (Qt.KeyboardModifier.ControlModifier | Qt.KeyboardModifier.AltModifier
                                                   | Qt.KeyboardModifier.MetaModifier)):
            t = e.text()
            if e.key() == Qt.Key.Key_Backspace and self._buf and not self.textCursor().hasSelection():
                self._buf = self._buf[:-1]
                self._show()
                return
            if len(t) == 1 and t.isascii() and t.isprintable() and (t.isalnum() or t in "`^:.$"):
                if self.textCursor().hasSelection():
                    self._reset()
                self._buf += t
                self._show()
                return
            self._reset()
        super().keyPressEvent(e)

    def focusOutEvent(self, e):
        self._reset()
        super().focusOutEvent(e)


class TextPanel(QWidget):
    """The "Aa" tab.  It never changes a layer itself: it emits 'changed' with the settings that changed."""
    changed = Signal(dict)
    opacityChanged = Signal(int)
    newRequested = Signal()
    duplicateRequested = Signal()

    def __init__(self, SliderRow, group_box, parent=None):
        super().__init__(parent)
        self._loading = False
        self._fonts_ready = False
        self._Slider = SliderRow
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(6)

        # ---- text
        g, gl = group_box("Text box")
        row = QHBoxLayout()
        self.btnNew = QPushButton("＋  New text box")
        self.btnNew.setProperty("accent", True)
        self.btnNew.setMinimumHeight(38)
        self.btnNew.setToolTip("Put a text box on the page.  Then: drag it to move, corner handles resize, the round handle\n"
                               "rotates, Alt + drag (or Duplicate) makes a copy, click outside it when done.")
        self.btnDup = QPushButton("⧉  Duplicate")
        self.btnDup.setToolTip("Make a copy of this text box")
        row.addWidget(self.btnNew, 1)
        row.addWidget(self.btnDup)
        gl.addLayout(row)
        self.body = QWidget()
        bl = QVBoxLayout(self.body)
        bl.setContentsMargins(0, 0, 0, 0)
        bl.setSpacing(6)
        self.edit = TextEdit()
        self.edit.setMinimumHeight(92)
        self.edit.setMaximumHeight(170)
        self.edit.setPlaceholderText("Type here ...  বাংলা / English")
        self.edit.setTabChangesFocus(True)
        self._edit_font("")
        bl.addWidget(self.edit)
        self.cmbType = QComboBox()
        self.cmbType.addItem("Normal  (Unicode keyboard, Avro Keyboard, paste)")
        self.cmbType.addItem("Phonetic typing  (English letters -> বাংলা)")
        self.cmbType.setToolTip("Normal: type with your own keyboard (Avro Keyboard, Bijoy Unicode ...) or paste.\n"
                                "Phonetic: this program turns the English letters into Bangla while you type.")
        bl.addWidget(self.cmbType)
        self.lblPhon = QLabel("Phonetic: ami = আমি, bangla = বাংলা, kolom = কলম.   o = no sign, O = ো (tOmake = তোমাকে),  "
                              "T D N S R = ট ড ণ ষ ড়,  ng = ং,  ^ = ঁ,  ` keeps letters apart,  . = ।")
        self.lblPhon.setObjectName("hint")
        self.lblPhon.setWordWrap(True)
        self.lblPhon.setVisible(False)
        bl.addWidget(self.lblPhon)
        self.btnToUni = QPushButton("Bijoy code  ->  Unicode")
        self.btnToUni.setToolTip("Text that was typed with a Bijoy keyboard (it looks like  Avwg evsjvq ) becomes real Bangla.\n"
                                 "Only the selected text, or all of it when nothing is selected.")
        bl.addWidget(self.btnToUni)
        hint = QLabel("A Bijoy (ANSI) font such as SutonnyMJ shows Bangla from either kind of text: Bijoy code is used as it is, "
                      "Unicode Bangla is converted for the font automatically.")
        hint.setObjectName("hint")
        hint.setWordWrap(True)
        bl.addWidget(hint)
        gl.addWidget(self.body)
        root.addWidget(g)

        # ---- font
        g, gl = group_box("Font")
        self.cmbFont = QComboBox()
        self.cmbFont.setMaxVisibleItems(18)
        self.cmbFont.setToolTip("Bangla and English fonts of this computer: basic and stylish ones.")
        gl.addWidget(self.cmbFont)
        r = QHBoxLayout()
        self.btnAddFont = QPushButton("＋  Add a font file...")
        self.btnAddFont.setToolTip("Choose any .ttf / .otf font (Bangla Unicode, Bijoy, English ...). It is remembered.\n"
                                   "Or copy fonts into the 'fonts' folder next to ImageFixer.exe: they load at every start.")
        self.chkAnsi = QCheckBox("Bijoy (ANSI) font")
        self.chkAnsi.setToolTip("Tick when the chosen font is an old Bijoy font (SutonnyMJ ...). Found by name; tick it by hand\n"
                                "for a Bijoy font with another name.")
        r.addWidget(self.btnAddFont, 1)
        r.addWidget(self.chkAnsi)
        gl.addLayout(r)
        r = QHBoxLayout()
        r.setSpacing(4)
        self.spSize = QDoubleSpinBox()
        self.spSize.setRange(1.0, 900.0)
        self.spSize.setDecimals(1)
        self.spSize.setSingleStep(1.0)
        self.spSize.setSuffix(" pt")
        self.spSize.setToolTip("Font size, as on an A4 page (12 pt = normal text).  Resizing the box with the mouse changes it too.")
        r.addWidget(self.spSize, 1)
        self.btnB, self.btnI, self.btnU, self.btnS = (self._style_btn(*a) for a in (
            ("B", "Bold", "font-weight: 800;"), ("I", "Italic", "font-style: italic;"),
            ("U", "Underline", "text-decoration: underline;"), ("S", "Strike through", "text-decoration: line-through;")))
        for b in (self.btnB, self.btnI, self.btnU, self.btnS):
            r.addWidget(b)
        gl.addLayout(r)
        self.cmbCase = QComboBox()
        for label, key in (("Letters as typed", "normal"), ("UPPERCASE  (capital letters)", "upper"),
                           ("lowercase  (small letters)", "lower"), ("Small caps", "smallcaps"),
                           ("Capitalize Each Word", "title")):
            self.cmbCase.addItem(label, key)
        self.cmbCase.setToolTip("Capital / small letters (English text; Bangla has no capital letters)")
        gl.addWidget(self.cmbCase)
        r = QHBoxLayout()
        r.setSpacing(4)
        self.alignGroup = QButtonGroup(self)
        self.alignGroup.setExclusive(True)
        self.alignBtns = {}
        for key, text, tip in (("left", "Left", "Align left"), ("center", "Center", "Align center"),
                               ("right", "Right", "Align right"), ("justify", "Justify", "Justify (both edges)")):
            b = QPushButton(text)
            b.setCheckable(True)
            b.setToolTip(tip)
            self.alignGroup.addButton(b)
            self.alignBtns[key] = b
            r.addWidget(b)
        gl.addLayout(r)
        root.addWidget(g)

        # ---- color + spacing
        g, gl = group_box("Color & spacing")
        r = QHBoxLayout()
        lab = QLabel("Text color")
        lab.setObjectName("sub")
        self.swColor = _Swatch("#111111", "Text color")
        r.addWidget(lab, 1)
        r.addWidget(self.swColor)
        gl.addLayout(r)
        self.rowOpacity = SliderRow("Opacity", 0, 100, 100, ("faint", "", "solid"), "How strongly the text shows on the page.")
        self.rowSpacing = SliderRow("Letter spacing", 0, 40, 0, ("tight", "", "wide"), "Space between the letters.")
        self.rowLine = SliderRow("Line spacing", 70, 300, 125, ("close", "", "far"), "Space between lines (when the text has several lines).")
        self.rowWrap = SliderRow("Box width", 0, 100, 0, ("auto", "", "full page"),
                                 "Auto = as wide as the longest line.  Otherwise the text wraps at this width (percent of the page).")
        for w in (self.rowOpacity, self.rowSpacing, self.rowLine, self.rowWrap):
            gl.addWidget(w)
        root.addWidget(g)

        # ---- effects
        g, gl = group_box("Outline, shadow, highlight", collapsed=True)
        self.chkOutline = QCheckBox("Outline")
        self.swOutline = _Swatch("#ffffff", "Outline color")
        r = QHBoxLayout()
        r.addWidget(self.chkOutline, 1)
        r.addWidget(self.swOutline)
        gl.addLayout(r)
        self.rowOutW = SliderRow("Outline width", 1, 40, 6, ("thin", "", "thick"), "", unit="")
        gl.addWidget(self.rowOutW)
        self.chkShadow = QCheckBox("Shadow")
        self.swShadow = _Swatch("#000000", "Shadow color")
        r = QHBoxLayout()
        r.addWidget(self.chkShadow, 1)
        r.addWidget(self.swShadow)
        gl.addLayout(r)
        self.rowShBlur = SliderRow("Shadow softness", 0, 40, 8, ("sharp", "", "soft"), "", unit="")
        self.rowShDist = SliderRow("Shadow distance", 0, 60, 8, ("near", "", "far"), "", unit="")
        self.rowShOp = SliderRow("Shadow strength", 0, 100, 60, ("faint", "", "dark"), "")
        for w in (self.rowShBlur, self.rowShDist, self.rowShOp):
            gl.addWidget(w)
        self.chkHl = QCheckBox("Highlight (colored box behind the text)")
        self.swHl = _Swatch("#fff2a8", "Highlight color")
        r = QHBoxLayout()
        r.addWidget(self.chkHl, 1)
        r.addWidget(self.swHl)
        gl.addLayout(r)
        root.addWidget(g)

        self.lblNone = QLabel("Add a text box with the button above, or click a text box on the page to change it.")
        self.lblNone.setObjectName("hint")
        self.lblNone.setWordWrap(True)
        root.insertWidget(1, self.lblNone)
        self._groups = [root.itemAt(i).widget() for i in range(2, root.count())]
        self._wire()
        self.set_phonetic(bool(_settings().value("text_phonetic", False, type=bool)))
        self._enable(False)

    @staticmethod
    def _style_btn(text, tip, css):
        b = QPushButton(text)
        b.setCheckable(True)
        b.setToolTip(tip)
        b.setFixedWidth(36)
        b.setStyleSheet("QPushButton { %s }" % css)
        return b

    # -------------------------------------------------------------------------------------------- signals
    def _wire(self):
        e = self._emit
        self.edit.textChanged.connect(lambda: e(text=self.edit.toPlainText()))
        self.cmbType.currentIndexChanged.connect(lambda i: self.set_phonetic(i == 1, save=True))
        self.cmbFont.currentIndexChanged.connect(self._font_picked)
        self.chkAnsi.toggled.connect(lambda on: e(ansi=bool(on)))
        self.spSize.valueChanged.connect(lambda v: e(size=float(v)))
        self.btnB.toggled.connect(lambda on: e(bold=bool(on)))
        self.btnI.toggled.connect(lambda on: e(italic=bool(on)))
        self.btnU.toggled.connect(lambda on: e(underline=bool(on)))
        self.btnS.toggled.connect(lambda on: e(strike=bool(on)))
        self.cmbCase.currentIndexChanged.connect(lambda i: e(case=self.cmbCase.itemData(i)))
        for key, b in self.alignBtns.items():
            b.clicked.connect(lambda _=False, k=key: e(align=k))
        self.swColor.colorChanged.connect(lambda c: e(color=c))
        self.rowOpacity.changed.connect(lambda v: None if self._loading else self.opacityChanged.emit(int(v)))
        self.rowSpacing.changed.connect(lambda v: e(spacing=int(v)))
        self.rowLine.changed.connect(lambda v: e(line=int(v)))
        self.rowWrap.changed.connect(lambda v: e(wrap=v / 100.0))
        self.chkOutline.toggled.connect(lambda on: e(outline=bool(on)))
        self.swOutline.colorChanged.connect(lambda c: e(outline_color=c, outline=True))
        self.rowOutW.changed.connect(lambda v: e(outline_w=int(v)))
        self.chkShadow.toggled.connect(lambda on: e(shadow=bool(on)))
        self.swShadow.colorChanged.connect(lambda c: e(shadow_color=c, shadow=True))
        self.rowShBlur.changed.connect(lambda v: e(shadow_blur=int(v)))
        self.rowShDist.changed.connect(lambda v: e(shadow_dist=int(v)))
        self.rowShOp.changed.connect(lambda v: e(shadow_op=int(v)))
        self.chkHl.toggled.connect(lambda on: e(hl=bool(on)))
        self.swHl.colorChanged.connect(lambda c: e(hl_color=c, hl=True))
        self.btnAddFont.clicked.connect(self._add_font)
        self.btnToUni.clicked.connect(self._to_unicode)
        self.btnNew.clicked.connect(self.newRequested.emit)
        self.btnDup.clicked.connect(self.duplicateRequested.emit)

    def _emit(self, **patch):
        if not self._loading:
            self.changed.emit(patch)

    def set_phonetic(self, on, save=False):
        self.edit.phonetic = bool(on)
        self.cmbType.blockSignals(True)
        self.cmbType.setCurrentIndex(1 if on else 0)
        self.cmbType.blockSignals(False)
        self.lblPhon.setVisible(bool(on))
        if save:
            _settings().setValue("text_phonetic", bool(on))
        self.edit._reset()

    def focus_text(self):
        self.edit.setFocus()
        self.edit.selectAll()

    # ------------------------------------------------------------------------------------------------ fonts
    def _fill_fonts(self):
        self.cmbFont.blockSignals(True)
        self.cmbFont.clear()
        for title, names in font_groups():
            self.cmbFont.addItem("-  " + title + "  -")
            try:
                self.cmbFont.model().item(self.cmbFont.count() - 1).setFlags(Qt.ItemFlag.NoItemFlags)
            except Exception:
                pass
            for n in names:
                self.cmbFont.addItem(n, n)
                if not banglatext.is_bijoy_font(n):
                    self.cmbFont.setItemData(self.cmbFont.count() - 1, QFont(n), Qt.ItemDataRole.FontRole)
        self.cmbFont.blockSignals(False)
        self._fonts_ready = True

    def _select_font(self, family):
        i = self.cmbFont.findData(family)
        if i < 0:
            self.cmbFont.addItem(family + "  (not installed)", family)
            i = self.cmbFont.count() - 1
        self.cmbFont.setCurrentIndex(i)

    def _font_picked(self, i):
        fam = self.cmbFont.itemData(i)
        if not fam or self._loading:
            return
        ansi = banglatext.is_bijoy_font(fam)
        self.chkAnsi.blockSignals(True)
        self.chkAnsi.setChecked(ansi)
        self.chkAnsi.blockSignals(False)
        self._edit_font(fam)
        self.changed.emit({"family": fam, "ansi": ansi})

    def _edit_font(self, fam):
        """The typing box shows the text in the chosen font (a Bijoy font shows Bijoy code as Bangla)."""
        fam = (fam or "").replace("'", "").replace('"', "")
        self.edit.setStyleSheet("QPlainTextEdit { background: #1a1f2b; border: 1px solid #30374a; border-radius: 8px; "
                                "padding: 6px; %s font-size: 16px; }" % ("font-family: '%s';" % fam if fam else ""))

    def _add_font(self):
        files, _ = QFileDialog.getOpenFileNames(self.window(), "Add a font", "", "Fonts (*.ttf *.otf *.ttc)")
        added = []
        for f in files:
            added += add_font_file(f)
        if not added:
            return
        self._fill_fonts()
        self._loading = True
        self._select_font(added[0])
        self._loading = False
        self._font_picked(self.cmbFont.currentIndex())

    def _to_unicode(self):
        cur = self.edit.textCursor()
        if cur.hasSelection():
            cur.insertText(banglatext.bijoy_to_unicode(cur.selectedText().replace("\u2029", "\n")))
        else:
            self.edit.setPlainText(banglatext.bijoy_to_unicode(self.edit.toPlainText()))

    # --------------------------------------------------------------------------------------------- loading
    def _enable(self, on):
        self.body.setEnabled(on)
        for g in self._groups:
            g.setEnabled(on)
        self.btnDup.setEnabled(on)
        self.lblNone.setVisible(not on)

    def load(self, t, opacity=100):
        """Show the settings of the selected text box (None: no text box selected)."""
        self._enable(t is not None)
        if t is None:
            return
        if not self._fonts_ready:
            self._fill_fonts()
        self._loading = True
        try:
            if self.edit.toPlainText() != t["text"]:
                self.edit.blockSignals(True)
                self.edit.setPlainText(t["text"])
                self.edit.blockSignals(False)
            fam = t.get("family") or default_family()
            if self.cmbFont.currentData() != fam:
                self._select_font(fam)
            self._edit_font(fam)
            self.chkAnsi.setChecked(bool(t["ansi"]))
            self.spSize.setValue(float(t["size"]))
            for b, k in ((self.btnB, "bold"), (self.btnI, "italic"), (self.btnU, "underline"), (self.btnS, "strike")):
                b.setChecked(bool(t[k]))
            self.cmbCase.setCurrentIndex(max(0, self.cmbCase.findData(t["case"])))
            self.alignBtns.get(t["align"], self.alignBtns["left"]).setChecked(True)
            self.swColor.set_color(t["color"])
            self.rowOpacity.set_value(int(opacity))
            self.rowSpacing.set_value(int(t["spacing"]))
            self.rowLine.set_value(int(t["line"]))
            self.rowWrap.set_value(int(round(min(1.0, float(t["wrap"])) * 100)))
            self.chkOutline.setChecked(bool(t["outline"]))
            self.swOutline.set_color(t["outline_color"])
            self.rowOutW.set_value(int(t["outline_w"]))
            self.chkShadow.setChecked(bool(t["shadow"]))
            self.swShadow.set_color(t["shadow_color"])
            self.rowShBlur.set_value(int(t["shadow_blur"]))
            self.rowShDist.set_value(int(t["shadow_dist"]))
            self.rowShOp.set_value(int(t["shadow_op"]))
            self.chkHl.setChecked(bool(t["hl"]))
            self.swHl.set_color(t["hl_color"])
        finally:
            self._loading = False
