"""Bijoy (ANSI) support for the text box (pure Python, no Qt).

 unicode_to_bijoy  Unicode Bangla -> Bijoy (SutonnyMJ) ANSI code, so Unicode text can be shown with an old Bijoy font.
 bijoy_to_unicode  Bijoy ANSI code -> Unicode Bangla.
 is_bijoy_font     tells if a font family is one of the old Bijoy (ANSI) fonts.

The tables cover every letter, vowel sign, reph, ra-phala, ya-phala and the common conjunct glyphs in BIJOY_CONJ.  A conjunct
that is not in the table is written with a visible hasant (&), which every Bijoy font has.
"""
import re

# ============================================================================================== Bijoy <-> Unicode
_BN_DIGITS = "০১২৩৪৫৬৭৮৯"
HAS = "\u09cd"                          # hasant
ZW = "\u200c\u200d"

_B_VOW = {"অ": "A", "আ": "Av", "ই": "B", "ঈ": "C", "উ": "D", "ঊ": "E", "ঋ": "F", "এ": "G", "ঐ": "H", "ও": "I", "ঔ": "J"}
_B_CONS = {"ক": "K", "খ": "L", "গ": "M", "ঘ": "N", "ঙ": "O", "চ": "P", "ছ": "Q", "জ": "R", "ঝ": "S", "ঞ": "T",
           "ট": "U", "ঠ": "V", "ড": "W", "ঢ": "X", "ণ": "Y", "ত": "Z", "থ": "_", "দ": "`", "ধ": "a", "ন": "b",
           "প": "c", "ফ": "d", "ব": "e", "ভ": "f", "ম": "g", "য": "h", "র": "i", "ল": "j", "শ": "k", "ষ": "l",
           "স": "m", "হ": "n", "\u09dc": "o", "\u09dd": "p", "\u09df": "q"}
_B_OTHER = {"ৎ": "r", "ং": "s", "ঃ": "t", "ঁ": "u", "।": "|"}
_B_POST = {"া": "v", "ী": "x", "ু": "y", "ূ": "\u201a", "ৃ": "\u201e"}
_B_PRE = {"ি": "w", "ে": "\u2021", "ৈ": "\u02c6"}
RA_PHALA, YA_PHALA, REPH = "\u00aa", "\u00a8", "\u00a9"
BIJOY_CONJ = {                          # conjunct (Unicode) -> Bijoy glyph(s)
    "ক্ষ": "\u00b6", "ঞ্জ": "\u00c4", "ত্র": "\u00ce", "হ্ম": "\u00fe", "ন্ধ": "\u00dc",
    "স্ক": "\u00af\u2039", "স্ক্র": "\u00af\u0152",
}
_COMPOSE = (("ড\u09bc", "\u09dc"), ("ঢ\u09bc", "\u09dd"), ("য\u09bc", "\u09df"), ("\u09c7\u09be", "ো"), ("\u09c7\u09d7", "ৌ"),
            ("\u09dc", "\u09dc"), ("\u09dd", "\u09dd"), ("\u09df", "\u09df"))
_CONS_SET = set(_B_CONS)
_ZSET = set("তছ")                       # after these the Bijoy sign is the 'z' glyph (same sign, nicer position)


def _normalize(s):
    for a, b in _COMPOSE:
        s = s.replace(a, b)
    return s.translate({ord(c): None for c in ZW})


def unicode_to_bijoy(text):
    """Unicode Bangla -> Bijoy ANSI.  Letters that are not Bangla (English, numbers, punctuation) stay as they are, so
    text that is already Bijoy code passes through unchanged."""
    s = _normalize(text)
    out = []
    i, n = 0, len(s)
    while i < n:
        ch = s[i]
        if ch in _CONS_SET:
            cons = [ch]
            j = i + 1
            while j + 1 < n and s[j] == HAS and s[j + 1] in _CONS_SET:
                cons.append(s[j + 1])
                j += 2
            reph = False
            if len(cons) >= 2 and cons[0] == "র":
                reph, cons = True, cons[1:]
            cl = _cluster(cons)
            pre, post, marks = "", "", ""
            while j < n:
                c = s[j]
                if c in _B_PRE:
                    pre = _B_PRE[c]
                elif c == "ো":
                    pre, post = "\u2021", post + "v"
                elif c == "ৌ":
                    pre, post = "\u2021", post + "\u0160"
                elif c in _B_POST:
                    g = _B_POST[c]
                    if c == "ু" and cons[-1] in _ZSET:
                        g = "z"
                    post += g
                elif c in "ংঃঁ":
                    marks += _B_OTHER[c]
                else:
                    break
                j += 1
            out.append(pre + cl + (REPH if reph else "") + post + marks)
            i = j
            continue
        if ch in _B_VOW:
            out.append(_B_VOW[ch])
        elif ch in _B_OTHER:
            out.append(_B_OTHER[ch])
        elif ch in _B_POST:
            out.append(_B_POST[ch])
        elif ch in _B_PRE:
            out.append(_B_PRE[ch])
        elif ch == "ো":
            out.append("\u2021v")
        elif ch == "ৌ":
            out.append("\u2021\u0160")
        elif ch == HAS:
            out.append("&")
        elif "০" <= ch <= "৯":
            out.append(str(ord(ch) - ord("০")))
        else:
            out.append(ch)
        i += 1
    return "".join(out)


def _cluster(cons):
    """List of consonants joined with hasants -> Bijoy glyphs (conjunct glyph, ra-phala, ya-phala or visible hasant)."""
    parts = []
    k = 0
    while k < len(cons):
        done = False
        for L in (3, 2):
            if k + L <= len(cons):
                key = HAS.join(cons[k:k + L])
                if key in BIJOY_CONJ:
                    parts.append(BIJOY_CONJ[key])
                    k += L
                    done = True
                    break
        if done:
            continue
        c = cons[k]
        if k > 0 and c == "র":
            parts.append(RA_PHALA)
        elif k > 0 and c == "য":
            parts.append(YA_PHALA)
        elif k > 0:
            parts.append("&" + _B_CONS[c])
        else:
            parts.append(_B_CONS[c])
        k += 1
    return "".join(parts)


# ---- Bijoy -> Unicode
_U_CONS = {v: k for k, v in _B_CONS.items()}
_U_CONS.update({"\u00b6": "ক্ষ", "\u00c4": "ঞ্জ", "\u00ce": "ত্র", "\u00fe": "হ্ম", "\u00dc": "ন্ধ"})
_U_CONS2 = {"\u00af\u2039": "স্ক", "\u00af\u0152": "স্ক্র"}
_U_VOW = {v: k for k, v in _B_VOW.items() if len(v) == 1}
_U_POST = {"v": "া", "x": "ী", "y": "ু", "z": "ু", "\u201a": "ূ", "\u201e": "ৃ", "\u2026": "ৃ"}
_U_PRE = {"w": "ি", "\u2021": "ে", "\u2020": "ে", "\u02c6": "ৈ"}
_U_MARK = {"s": "ং", "t": "ঃ", "u": "ঁ", "r": "ৎ", "|": "।"}


def _read_cons(s, i):
    """A consonant glyph (or conjunct glyph) at position i -> (unicode, length) or None."""
    two = s[i:i + 2]
    if two in _U_CONS2:
        return _U_CONS2[two], 2
    c = s[i:i + 1]
    if c in _U_CONS:
        return _U_CONS[c], 1
    return None


def bijoy_to_unicode(text):
    """Bijoy ANSI code (what a Bijoy keyboard types) -> Unicode Bangla.  Needs Bangla text: English words typed as
    they are would be changed too, so convert only Bijoy text."""
    s = text
    out = []
    pre = []                            # e-kar / i-kar typed before the consonant: they go after the whole cluster
    i, n = 0, len(s)
    while i < n:
        ch = s[i]
        if ch in _U_PRE:
            pre.append(_U_PRE[ch])
            i += 1
            continue
        rc = _read_cons(s, i)
        if rc is not None:
            uni, ln = rc
            i += ln
            reph = False
            cl = uni
            while i < n:
                if s[i] == "&":
                    nx = _read_cons(s, i + 1)
                    if nx is None:
                        break
                    cl += HAS + nx[0]
                    i += 1 + nx[1]
                elif s[i] == RA_PHALA:
                    cl += HAS + "র"
                    i += 1
                elif s[i] == YA_PHALA:
                    cl += HAS + "য"
                    i += 1
                elif s[i] == REPH:
                    reph = True
                    i += 1
                else:
                    break
            res = ("র" + HAS if reph else "") + cl
            if pre:
                if "ে" in pre and i < n and s[i] == "v":
                    res += "ো"
                    i += 1
                    pre = [p for p in pre if p != "ে"]
                elif "ে" in pre and i < n and s[i] == "\u0160":
                    res += "ৌ"
                    i += 1
                    pre = [p for p in pre if p != "ে"]
                res += "".join(pre)
                pre = []
            out.append(res)
            continue
        if pre:
            out.append("".join(pre))
            pre = []
        if ch == "A" and s[i + 1:i + 2] == "v":
            out.append("আ")
            i += 2
            continue
        if ch in _U_VOW:
            out.append(_U_VOW[ch])
        elif ch in _U_POST:
            out.append(_U_POST[ch])
        elif ch in _U_MARK:
            out.append(_U_MARK[ch])
        elif ch == "\u0160":
            out.append("ৗ")
        elif ch == "&":
            out.append(HAS)
        elif ch in "0123456789":
            out.append(_BN_DIGITS[int(ch)])
        elif ch == REPH:
            out.append("র" + HAS)
        else:
            out.append(ch)
        i += 1
    if pre:
        out.append("".join(pre))
    return "".join(out)


# =================================================================================================== fonts
_BIJOY_NAME = re.compile(r"(mj$|mj[ \-]|sutonny|ansi$|bijoy)", re.I)


def is_bijoy_font(family):
    """True for the old Bijoy (ANSI) fonts: SutonnyMJ, SutonnyOMJ, BornomalaMJ, ... (names ending in MJ)."""
    return bool(family) and bool(_BIJOY_NAME.search(family.strip()))


def has_bangla(text):
    return any("\u0980" <= c <= "\u09ff" for c in text)


# ============================================================================================ Bangla / English runs
# Like MS Word: Bangla letters use the Bangla font, English letters use an English font, automatically.  A Bijoy (ANSI)
# font draws every English letter as a Bangla glyph, so English text must never be drawn with it.
def _is_bn(c):
    return "\u0980" <= c <= "\u09ff" or c in "\u0964\u0965"          # Bangla block + danda / double danda


def _is_neutral(c):
    """Spaces, zero-width joiners and punctuation take the script of the text next to them."""
    if c in "\u200c\u200d":
        return True
    if c.isspace():
        return True
    return (not c.isalnum()) and ord(c) < 0x2c00 and not _is_bn(c)


def split_scripts(text):
    """text -> [("bn" | "lat", piece), ...] (pieces in order; joined they give the text again).
    Bangla letters are "bn"; everything else is "lat" (English, digits, ...); spaces and punctuation go with the
    text before them (or after, at the very start)."""
    kinds = []
    for c in text:
        kinds.append("n" if _is_neutral(c) else ("bn" if _is_bn(c) else "lat"))
    prev = None
    for i, k in enumerate(kinds):                    # neutral takes the script before it
        if k == "n":
            kinds[i] = prev
        else:
            prev = k
    nxt = None
    for i in range(len(kinds) - 1, -1, -1):          # ... or after it, at the start of the text
        if kinds[i] is None:
            kinds[i] = nxt
        else:
            nxt = kinds[i]
    out = []
    for c, k in zip(text, kinds):
        k = k or "lat"
        if out and out[-1][0] == k:
            out[-1][1].append(c)
        else:
            out.append([k, [c]])
    return [(k, "".join(p)) for k, p in out]


def utf16_len(s):
    """Length in UTF-16 units (the unit Qt uses for text positions)."""
    return len(s) + sum(1 for c in s if ord(c) > 0xFFFF)


def layout_runs(text, ansi, bangla_family, latin_family):
    """Text of a text box -> (text to draw, [(start, end, family), ...]) with Qt (UTF-16) positions.

    Bangla letters get bangla_family, everything else latin_family.  With ansi (Bijoy font) only the Bangla pieces are
    converted to Bijoy code, English pieces stay English.  Text without any Bangla letter is left alone when ansi is
    on: it may be typed with a Bijoy keyboard (Avwg = আমি), which the Bijoy font shows as Bangla - runs = [] then."""
    if ansi and not has_bangla(text):
        return text, []
    out, runs, pos = [], [], 0
    for kind, piece in split_scripts(text):
        if kind == "bn":
            if ansi:
                piece = unicode_to_bijoy(piece)
            fam = bangla_family
        else:
            fam = latin_family
        n = utf16_len(piece)
        out.append(piece)
        runs.append((pos, pos + n, fam))
        pos += n
    return "".join(out), runs
