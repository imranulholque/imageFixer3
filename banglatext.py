"""Bangla text helpers for the text box (pure Python, no Qt).

 1. phonetic()        English letters -> Bangla while you type (Avro style: "ami" -> আমি, "bangla" -> বাংলা).
 2. unicode_to_bijoy  Unicode Bangla -> Bijoy (SutonnyMJ) ANSI code, for the old Bijoy fonts.
 3. bijoy_to_unicode  Bijoy ANSI code -> Unicode Bangla.
 4. is_bijoy_font     tells if a font family is one of the old Bijoy (ANSI) fonts.

The Bijoy tables cover every letter, vowel sign, reph, ra-phala, ya-phala and the common conjunct glyphs listed in
BIJOY_CONJ.  A conjunct that is not in the table is written with a visible hasant (&), which every Bijoy font has, so
nothing is lost - it only looks less joined.
"""
import re

# ===================================================================================== phonetic typing (Avro style)
# (roman, kind, value, kar)   kind: C consonant, V vowel, M mark (ং ঃ ঁ ৎ), special handled in code
_CONS = {
    "kkh": "ক্ষ", "GG": "জ্ঞ", "chh": "ছ",
    "kh": "খ", "gh": "ঘ", "Ng": "ঙ", "NG": "ঞ", "ch": "ছ", "jh": "ঝ", "Th": "ঠ", "Dh": "ঢ",
    "th": "থ", "dh": "ধ", "ph": "ফ", "bh": "ভ", "sh": "শ", "Sh": "ষ", "Rh": "\u09dd",
    "k": "ক", "g": "গ", "c": "চ", "j": "জ", "T": "ট", "D": "ড", "N": "ণ", "t": "ত", "d": "দ", "n": "ন",
    "p": "প", "f": "ফ", "b": "ব", "v": "ভ", "m": "ম", "z": "য", "r": "র", "l": "ল", "S": "শ", "s": "স",
    "h": "হ", "R": "\u09dc", "q": "ক", "x": "ক্স",
}
_VOW = {                       # roman -> (independent, sign after a consonant)
    "rri": ("ঋ", "ৃ"), "oi": ("ঐ", "ৈ"), "OI": ("ঐ", "ৈ"), "ou": ("ঔ", "ৌ"), "OU": ("ঔ", "ৌ"),
    "ii": ("ঈ", "ী"), "ee": ("ঈ", "ী"), "uu": ("ঊ", "ূ"), "oo": ("ঊ", "ূ"),
    "aa": ("আ", "া"), "a": ("আ", "া"), "i": ("ই", "ি"), "I": ("ঈ", "ী"), "u": ("উ", "ু"), "U": ("ঊ", "ূ"),
    "e": ("এ", "ে"), "O": ("ও", "ো"),
}
_MARK = {"ng": "ং", "^": "ঁ", ":": "ঃ", "t``": "ৎ"}
_BN_DIGITS = "০১২৩৪৫৬৭৮৯"
_KEYS = sorted(set(_CONS) | set(_VOW) | set(_MARK) | {"o", "y", "w", "Z", "`"}, key=lambda k: (-len(k), k))
_LETTERS = set("".join(_KEYS)) | set("abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ")


def _next_token(s, i):
    for k in _KEYS:
        if s.startswith(k, i):
            return k
    return None


def phonetic(roman, digits=True):
    """Roman letters -> Bangla (Avro style).  Capital letters matter: T D N S R O I U Ng NG Th Dh Sh Rh.
    o = the hidden 'o' sound (no sign: "kolom" -> কলম), O = ো ("kO" -> কো), ` = keep letters apart,
    ng = ং, Ng = ঙ, ^ = ঁ, : = ঃ, t`` = ৎ, . = ।, y after a consonant = ্য, w after a consonant = ্ব."""
    out = []
    prev = None                         # None / "C" (a consonant that can still take a sign or join) / "X" (closed) / "V"
    i, n = 0, len(roman)
    while i < n:
        ch = roman[i]
        if ch in "0123456789":
            out.append(_BN_DIGITS[int(ch)] if digits else ch)
            prev = None
            i += 1
            continue
        if ch == "." and roman[i:i + 2] == "..":
            out.append(".")
            prev, i = None, i + 2
            continue
        if ch == ".":
            out.append("।")
            prev, i = None, i + 1
            continue
        if ch == "$":
            out.append("৳")
            prev, i = None, i + 1
            continue
        tok = _next_token(roman, i)
        if tok is None and ch.isupper():                # an unknown capital letter: use the small one
            roman = roman[:i] + ch.lower() + roman[i + 1:]
            continue
        if tok is None:
            out.append(ch)
            prev, i = None, i + 1
            continue
        i += len(tok)
        if tok == "`":                                  # closes a consonant: the next one will not join
            prev = "X" if prev == "C" else prev
            continue
        if tok in _MARK:
            out.append(_MARK[tok])
            prev = "X"
            continue
        if tok == "o":
            if prev == "C":                             # the hidden 'o': no sign, and the cluster is closed
                prev = "X"
            else:
                out.append("ও" if prev == "V" else "অ")
                prev = "V"
            continue
        if tok in _VOW:
            ind, sign = _VOW[tok]
            if prev == "C":
                out.append(sign)
                prev = "X"
            else:
                out.append(ind)
                prev = "V"
            continue
        if tok == "y":
            if prev == "C":
                out.append("্য")
            else:
                out.append("\u09df")
                prev = "C"
            continue
        if tok == "Z":
            out.append("্য" if prev == "C" else "য")
            prev = "C"
            continue
        if tok == "w":
            if prev == "C":
                out.append("্ব")
            else:
                out.append("ও")
                prev = "V"
            continue
        c = _CONS[tok]
        if prev == "C":
            out.append("্")
        out.append(c)
        prev = "C"
    return "".join(out)


def phonetic_text(s, digits=True):
    """A whole text: every run of letters is converted, spaces / punctuation / new lines stay."""
    return "".join(phonetic(p, digits) if p and (p[0].isalnum() or p[0] in "`^:$.") and p.isascii() else p
                   for p in re.split(r"(\s+)", s))


# ============================================================================================== Bijoy <-> Unicode
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
