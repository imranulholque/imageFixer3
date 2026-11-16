"""Scan helper: finds and drives scanners (TWAIN and WIA).  Runs as a SEPARATE little program, never inside the main window:

  * it is built twice, a 32-bit copy and a 64-bit copy.  Many office scanners (Toshiba e-Studio, old Canon / HP / Epson ...) only
    have a 32-bit TWAIN driver - that is why they only work in a 32-bit Photoshop.  A 64-bit program cannot load such a
    driver, a 32-bit program can.  The main window starts both copies and shows what each of them found.
  * a crash or a hanging driver can never take the main window down: the main window just stops this program.

Usage (the main window does this; you can also try it by hand):
    scanhelper list --out result.jsonl
    scanhelper scan --kind twain --source "TOSHIBA e-STUDIO" --dir C:\\temp --dpi 300 --mode color --size a4 --ui 0 --out result.jsonl
    scanhelper scan --kind wia   --source "{device id}" ...
Answers are written as one JSON object per line into the --out file (a windowed .exe has no console to print to).
"""
import argparse
import json
import os
import struct
import sys
import time
import traceback

BITS = struct.calcsize("P") * 8

# paper size in inches (width, height).  None = the scanner's own maximum / the size the driver chooses.
PAPER = {
    "max": None,
    "a4": (8.268, 11.693),
    "a5": (5.827, 8.268),
    "a3": (11.693, 16.535),
    "b5": (6.929, 9.843),
    "letter": (8.5, 11.0),
    "legal": (8.5, 14.0),
}

# TWAIN numbers (twain.h) - written as numbers so it does not matter how the python wrapper names them
CAP_XFERCOUNT = 0x0001
ICAP_PIXELTYPE = 0x0101
ICAP_UNITS = 0x0102
ICAP_XRESOLUTION = 0x1118
ICAP_YRESOLUTION = 0x1119
CAP_FEEDERENABLED = 0x1002
CAP_AUTOFEED = 0x1007
CAP_DUPLEXENABLED = 0x1013
TWTY_INT16, TWTY_UINT16, TWTY_BOOL, TWTY_FIXED = 1, 4, 6, 7
TWUN_INCHES = 0
PIXEL = {"bw": 0, "gray": 1, "color": 2}

_OUT = {"path": None}


def emit(obj):
    """One answer line into the --out file."""
    p = _OUT["path"]
    line = json.dumps(obj, ensure_ascii=False)
    if not p:
        try:
            print(line, flush=True)
        except Exception:
            pass
        return
    with open(p, "a", encoding="utf-8") as f:
        f.write(line + "\n")


def note(text):
    emit({"event": "note", "message": text})


# ------------------------------------------------------------------ a window the driver windows can belong to
def make_owner():
    """A tiny always-on-top window.  Scanner driver windows open on top of it instead of hiding behind other programs."""
    if os.name != "nt":
        return 0
    try:
        import ctypes
        from ctypes import wintypes
        u = ctypes.windll.user32
        u.CreateWindowExW.restype = ctypes.c_void_p
        u.CreateWindowExW.argtypes = [wintypes.DWORD, wintypes.LPCWSTR, wintypes.LPCWSTR, wintypes.DWORD,
                                      ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int,
                                      ctypes.c_void_p, ctypes.c_void_p, ctypes.c_void_p, ctypes.c_void_p]
        u.ShowWindow.argtypes = [ctypes.c_void_p, ctypes.c_int]
        u.SetForegroundWindow.argtypes = [ctypes.c_void_p]
        WS_POPUP, WS_EX_TOPMOST, WS_EX_TOOLWINDOW = 0x80000000, 0x8, 0x80
        sw = u.GetSystemMetrics(0)
        sh = u.GetSystemMetrics(1)
        h = u.CreateWindowExW(WS_EX_TOPMOST | WS_EX_TOOLWINDOW, "STATIC", "Image Fixer scan", WS_POPUP,
                              sw // 2, sh // 2, 1, 1, None, None, None, None)
        if h:
            u.ShowWindow(h, 5)
            try:
                u.SetForegroundWindow(h)
            except Exception:
                pass
        return int(h or 0)
    except Exception:
        return 0


# ------------------------------------------------------------------ TWAIN
def _pick(obj, *names):
    """The first attribute that exists (the python TWAIN wrapper changed its method names between versions)."""
    for n in names:
        f = getattr(obj, n, None)
        if f is not None:
            return f
    raise AttributeError("none of %s" % (names,))


def twain_list():
    import twain
    names = []
    owner = make_owner()
    sm = twain.SourceManager(owner)
    try:
        lst = getattr(sm, "source_list", None)
        if lst is None:
            lst = _pick(sm, "GetSourceList")()
        names = [str(n) for n in lst]
    finally:
        try:
            sm.close()
        except Exception:
            pass
    return names


def _dib_to_file(handle, path):
    import twain
    f = getattr(twain, "dib_to_bm_file", None) or getattr(twain, "DIBToBMFile", None)
    if f is None:
        from twain import windows as tw
        f = getattr(tw, "dib_to_bm_file", None) or getattr(tw, "DIBToBMFile")
    f(handle, path)
    g = getattr(twain, "global_handle_free", None) or getattr(twain, "GlobalHandleFree", None)
    if g is None:
        try:
            from twain import windows as tw
            g = getattr(tw, "global_handle_free", None) or getattr(tw, "GlobalHandleFree", None)
        except Exception:
            g = None
    if g is not None:
        try:
            g(handle)
        except Exception:
            pass


def twain_scan(a):
    import twain
    pages = []
    owner = make_owner()
    sm = twain.SourceManager(owner)
    src = None
    try:
        open_source = _pick(sm, "open_source", "OpenSource")
        src = open_source(a.source) if a.source else open_source()
        if src is None:
            emit({"event": "cancelled"})
            return pages
        set_cap = _pick(src, "set_capability", "SetCapability")

        def cap(cid, ty, val, label):
            try:
                set_cap(cid, ty, val)
            except Exception as ex:
                note("%s not set (%s)" % (label, str(ex).splitlines()[0] if str(ex) else type(ex).__name__))

        cap(ICAP_UNITS, TWTY_UINT16, TWUN_INCHES, "units")
        if a.mode in PIXEL:
            cap(ICAP_PIXELTYPE, TWTY_UINT16, PIXEL[a.mode], "color mode")
        if a.dpi:
            cap(ICAP_XRESOLUTION, TWTY_FIXED, float(a.dpi), "resolution")
            cap(ICAP_YRESOLUTION, TWTY_FIXED, float(a.dpi), "resolution")
        # flatbed glass or document feeder: say it clearly, an office machine otherwise picks one by itself
        cap(CAP_FEEDERENABLED, TWTY_BOOL, bool(a.feeder), "feeder")
        if a.feeder:
            cap(CAP_AUTOFEED, TWTY_BOOL, True, "auto feed")
            cap(CAP_DUPLEXENABLED, TWTY_BOOL, bool(a.duplex), "both sides")
        cap(CAP_XFERCOUNT, TWTY_INT16, int(a.max_pages) if a.feeder else 1, "page count")
        size = PAPER.get(a.size)
        if size and not a.ui:
            try:
                _pick(src, "set_image_layout", "SetImageLayout")((0.0, 0.0, float(size[0]), float(size[1])))
            except Exception as ex:
                note("paper size not set (%s)" % (str(ex).splitlines()[0] if str(ex) else type(ex).__name__))

        show = bool(a.ui)
        _pick(src, "request_acquire", "RequestAcquire")(show, show)
        if show:
            _pick(src, "modal_loop", "ModalLoop")()
        xfer = _pick(src, "xfer_image_natively", "XferImageNatively")
        while True:
            handle, remaining = xfer()
            path = os.path.join(a.dir, "scan_%s_%d.bmp" % (time.strftime("%H%M%S"), len(pages) + 1))
            _dib_to_file(handle, path)
            pages.append(path)
            emit({"event": "page", "path": path})
            if remaining == 0 or len(pages) >= int(a.max_pages) or not a.feeder:
                break
    except Exception as ex:
        if pages and "DSTransferCancelled" in type(ex).__name__:
            pass
        elif "DSTransferCancelled" in type(ex).__name__ and not pages:
            emit({"event": "cancelled"})
        else:
            raise
    finally:
        if src is not None:
            try:
                src.close()
            except Exception:
                pass
        try:
            sm.close()
        except Exception:
            pass
    return pages


# ------------------------------------------------------------------ WIA
WIA_PNG = "{B96B3CAF-0728-11D3-9D7B-0000F81EF32E}"
WIA_BMP = "{B96B3CAB-0728-11D3-9D7B-0000F81EF32E}"


def _props(obj):
    out = {}
    pr = obj.Properties
    for i in range(1, pr.Count + 1):
        try:
            p = pr.Item(i)
            out[int(p.PropertyID)] = p
        except Exception:
            pass
    return out


def _wia_set(props, pid, value, label=None):
    p = props.get(pid)
    if p is None:
        return False
    try:
        try:                                              # a range property: stay inside what the scanner allows
            hi = p.SubTypeMax
            lo = p.SubTypeMin
            if p.SubType == 1 and hi:
                value = max(lo, min(int(value), int(hi)))
        except Exception:
            pass
        p.Value = value
        return True
    except Exception as ex:
        if label:
            note("%s not set (%s)" % (label, str(ex).splitlines()[0] if str(ex) else type(ex).__name__))
        return False


def wia_list():
    import win32com.client as wc
    dm = wc.Dispatch("WIA.DeviceManager")
    out = []
    infos = dm.DeviceInfos
    for i in range(1, infos.Count + 1):
        info = infos.Item(i)
        try:
            if int(info.Type) != 1:                       # 1 = scanner (not camera / video)
                continue
        except Exception:
            pass
        try:
            name = str(info.Properties.Item("Name").Value)
        except Exception:
            name = "WIA scanner %d" % i
        out.append({"name": name, "id": str(info.DeviceID)})
    return out


def _wia_save(img, a, n):
    ext = "png"
    try:
        ext = str(img.FileExtension or "png").lstrip(".")
    except Exception:
        pass
    path = os.path.join(a.dir, "scan_%s_%d.%s" % (time.strftime("%H%M%S"), n, ext))
    if os.path.exists(path):
        os.remove(path)
    img.SaveFile(path)
    return path


def wia_scan(a):
    import win32com.client as wc
    dm = wc.Dispatch("WIA.DeviceManager")
    info = None
    infos = dm.DeviceInfos
    for i in range(1, infos.Count + 1):
        if str(infos.Item(i).DeviceID) == a.source:
            info = infos.Item(i)
            break
    if info is None:
        raise RuntimeError("The scanner is not connected any more (WIA).")
    dev = info.Connect()
    pages = []
    if a.ui:
        cd = wc.Dispatch("WIA.CommonDialog")
        img = cd.ShowAcquireImage(1, 0, 0, WIA_PNG, False, True, False)
        if img is None:
            emit({"event": "cancelled"})
            return pages
        path = _wia_save(img, a, 1)
        emit({"event": "page", "path": path})
        return [path]

    item = dev.Items.Item(1)
    dprops = _props(dev)
    iprops = _props(item)
    # 3088 = document handling select: 1 feeder, 2 flatbed, +4 both sides
    handling = (1 | (4 if a.duplex else 0)) if a.feeder else 2
    _wia_set(dprops, 3088, handling)
    intent = {"color": 1, "gray": 2, "bw": 4}.get(a.mode, 1)
    _wia_set(iprops, 6146, intent)                         # current intent
    _wia_set(iprops, 4103, {"color": 3, "gray": 2, "bw": 0}.get(a.mode, 3))   # data type
    dpi = int(a.dpi or 300)
    _wia_set(iprops, 6147, dpi, "resolution")
    _wia_set(iprops, 6148, dpi)
    size = PAPER.get(a.size)
    if size:
        bw, bh = size
        try:                                                # the glass is smaller than the paper: use the glass
            if 3074 in dprops and 3075 in dprops:
                bw = min(bw, int(dprops[3074].Value) / 1000.0)
                bh = min(bh, int(dprops[3075].Value) / 1000.0)
        except Exception:
            pass
        _wia_set(iprops, 6149, 0)
        _wia_set(iprops, 6150, 0)
        _wia_set(iprops, 6151, int(bw * dpi), "paper width")
        _wia_set(iprops, 6152, int(bh * dpi), "paper height")
    n = 0
    while True:
        try:
            img = item.Transfer(WIA_PNG)
        except Exception:
            if pages:                                       # feeder empty: normal end
                break
            raise
        n += 1
        path = _wia_save(img, a, n)
        pages.append(path)
        emit({"event": "page", "path": path})
        if not a.feeder or n >= int(a.max_pages):
            break
    return pages


# ------------------------------------------------------------------ commands
def cmd_list(a):
    sources, errors = [], []
    if os.name == "nt":
        try:
            for name in twain_list():
                sources.append({"kind": "twain", "name": name, "id": name, "bits": BITS})
        except Exception as ex:
            errors.append("TWAIN %d-bit: %s" % (BITS, str(ex).splitlines()[0] if str(ex) else type(ex).__name__))
        if a.wia:
            try:
                for d in wia_list():
                    sources.append({"kind": "wia", "name": d["name"], "id": d["id"], "bits": BITS})
            except Exception as ex:
                errors.append("WIA: %s" % (str(ex).splitlines()[0] if str(ex) else type(ex).__name__))
    else:
        errors.append("Scanners need Windows.")
    emit({"event": "list", "bits": BITS, "sources": sources, "errors": errors})
    return 0


def cmd_scan(a):
    os.makedirs(a.dir, exist_ok=True)
    pages = wia_scan(a) if a.kind == "wia" else twain_scan(a)
    emit({"event": "done", "pages": len(pages), "bits": BITS})
    return 0


def main(argv=None):
    if sys.stdout is None:
        sys.stdout = open(os.devnull, "w")
    if sys.stderr is None:
        sys.stderr = open(os.devnull, "w")
    ap = argparse.ArgumentParser(prog="scanhelper")
    sub = ap.add_subparsers(dest="cmd", required=True)
    pl = sub.add_parser("list")
    pl.add_argument("--out")
    pl.add_argument("--wia", type=int, default=1)
    ps = sub.add_parser("scan")
    ps.add_argument("--out")
    ps.add_argument("--kind", choices=("twain", "wia"), required=True)
    ps.add_argument("--source", default="")
    ps.add_argument("--dir", required=True)
    ps.add_argument("--dpi", type=int, default=300)
    ps.add_argument("--mode", choices=("color", "gray", "bw"), default="color")
    ps.add_argument("--size", default="a4")
    ps.add_argument("--ui", type=int, default=0)
    ps.add_argument("--feeder", type=int, default=0)
    ps.add_argument("--duplex", type=int, default=0)
    ps.add_argument("--max-pages", dest="max_pages", type=int, default=1)
    a = ap.parse_args(argv)
    _OUT["path"] = a.out
    try:
        return cmd_list(a) if a.cmd == "list" else cmd_scan(a)
    except SystemExit:
        raise
    except BaseException as ex:                           # always an answer, never a silent crash
        emit({"event": "error", "bits": BITS, "message": "%s: %s" % (type(ex).__name__, ex), "trace": traceback.format_exc()})
        return 1


if __name__ == "__main__":
    sys.exit(main())
