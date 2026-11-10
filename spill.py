"""Old undo / redo steps live on the disk, not in memory.

Every big picture or mask of an old step is written once, as RAW pixels (no compression, no quality loss, fast),
into a temporary folder.  The step keeps only a tiny reference.  Undo / redo reads the pixels back: the result is
bit for bit the same picture, so saving, copying and printing are exactly as sharp as before.

Pictures are never changed in place by the program (every edit makes a new picture), so one file per picture is enough,
and a picture used by many steps is written only once.
"""
import atexit
import os
import queue
import shutil
import tempfile
import threading
import uuid
import weakref

import numpy as np
from PIL import Image

MIN_BYTES = 256 * 1024                  # smaller things stay in memory (not worth a file)
_PREFIX = "ImageFixer_undo_"

_lock = threading.Lock()
_dir = None
_q = queue.Queue()
_worker = None
_registry = weakref.WeakValueDictionary()      # id(picture) -> Ref   (so one picture is written only once)
_failed = False                                # a write failed (disk full ...): stop using the disk for this session
_space = {"t": 0.0, "ok": True}
MIN_FREE_BYTES = 2 * 1024 ** 3                 # do not fill the disk: keep at least 2 GB free
MAX_PICTURES = 40                              # different big pictures one picture's history may keep (RAM + disk)
MAX_BYTES = 3 * 1024 ** 3                      # ... and at most this much (raw pixels) per picture


def can_write():
    """True while old steps may be moved to the disk (enough free space, no earlier write error)."""
    import time
    if _failed:
        return False
    now = time.time()
    if now - _space["t"] > 20.0:
        _space["t"] = now
        try:
            _space["ok"] = shutil.disk_usage(tempfile.gettempdir()).free > MIN_FREE_BYTES
        except Exception:
            _space["ok"] = False
    return _space["ok"]


def _folder():
    global _dir
    with _lock:
        if _dir is None or not os.path.isdir(_dir):
            _dir = tempfile.mkdtemp(prefix=_PREFIX + str(os.getpid()) + "_")
        return _dir


def cleanup_old():
    """Start-up: remove folders left behind by a program that crashed or was killed."""
    root = tempfile.gettempdir()
    try:
        names = os.listdir(root)
    except Exception:
        return
    for n in names:
        if not n.startswith(_PREFIX):
            continue
        try:
            pid = int(n[len(_PREFIX):].split("_")[0])
        except Exception:
            continue
        if pid == os.getpid() or _alive(pid):
            continue
        shutil.rmtree(os.path.join(root, n), ignore_errors=True)


def _alive(pid):
    try:
        if os.name == "nt":
            import ctypes
            h = ctypes.windll.kernel32.OpenProcess(0x1000, False, pid)      # PROCESS_QUERY_LIMITED_INFORMATION
            if not h:
                return False
            ctypes.windll.kernel32.CloseHandle(h)
            return True
        os.kill(pid, 0)
        return True
    except PermissionError:
        return True
    except Exception:
        return False


def _exit_cleanup():
    global _dir
    if _dir:
        shutil.rmtree(_dir, ignore_errors=True)
        _dir = None


atexit.register(_exit_cleanup)


def _size_of(obj):
    if isinstance(obj, Image.Image):
        return obj.width * obj.height * max(1, len(obj.getbands()))
    if isinstance(obj, np.ndarray):
        return obj.nbytes
    return 0


def _writer():
    while True:
        ref = _q.get()
        try:
            ref._write()
        except Exception:
            global _failed
            _failed = True                        # could not write: the picture stays in memory (strong ref kept)
        finally:
            del ref                               # do not keep the last picture alive while waiting for the next one
            _q.task_done()


def _start():
    global _worker
    with _lock:
        if _worker is None:
            _worker = threading.Thread(target=_writer, name="spill-writer", daemon=True)
            _worker.start()


class Ref:
    """Stands in for one picture / mask.  get() gives the real thing back (the very same object while it is still
    alive in memory, otherwise a lossless copy read from the file)."""

    def __init__(self, obj):
        self._strong = obj                        # kept until the file is written
        self._weak = weakref.ref(obj)
        self._path = None
        self._info = None
        self._mutex = threading.Lock()
        self.nbytes = _size_of(obj)
        if isinstance(obj, Image.Image):
            self._info = ("pil", obj.mode, obj.size)
        else:
            self._info = ("np", str(obj.dtype), obj.shape)

    def is_of(self, obj):
        """Is this the Ref of that very picture?  (no file is read)"""
        return self._strong is obj or self._weak() is obj

    # ---- writing (worker thread)
    def _write(self):
        obj = self._strong
        if obj is None:
            return
        path = os.path.join(_folder(), uuid.uuid4().hex + ".raw")
        tmp = path + ".part"
        with open(tmp, "wb") as f:
            if self._info[0] == "pil":
                f.write(obj.tobytes())
            else:
                f.write(np.ascontiguousarray(obj).tobytes())
        os.replace(tmp, path)
        with self._mutex:
            self._path = path
            self._strong = None                   # memory is free as soon as nobody else uses the picture

    # ---- reading
    def get(self):
        with self._mutex:
            obj = self._strong if self._strong is not None else self._weak()
            if obj is not None:
                return obj
            path, info = self._path, self._info
        if path is None:
            raise RuntimeError("undo picture lost")
        with open(path, "rb") as f:
            data = f.read()
        if info[0] == "pil":
            obj = Image.frombytes(info[1], info[2], data)
        else:
            obj = np.frombuffer(data, dtype=np.dtype(info[1])).reshape(info[2]).copy()
        with self._mutex:
            alive = self._weak()
            if alive is not None:                 # someone else read it back at the same moment: share that one
                return alive
            self._weak = weakref.ref(obj)
        _registry[id(obj)] = self
        return obj

    def __del__(self):
        p = getattr(self, "_path", None)
        if p:
            try:
                os.remove(p)
            except Exception:
                pass


def _ref_for(obj):
    """One Ref per picture (so a picture shared by many steps is written once)."""
    r = _registry.get(id(obj))
    if r is not None and r.is_of(obj):
        return r
    r = Ref(obj)
    _registry[id(obj)] = r
    _start()
    _q.put(r)
    return r


def _out(obj):
    """obj -> Ref when it is big, else obj itself."""
    if isinstance(obj, Ref) or obj is None:
        return obj
    if isinstance(obj, Image.Image) and obj.mode not in ("RGB", "RGBA", "L"):
        return obj
    if isinstance(obj, np.ndarray) and (obj.dtype.hasobject or obj.dtype.names):
        return obj
    if isinstance(obj, (Image.Image, np.ndarray)) and _size_of(obj) >= MIN_BYTES:
        return _ref_for(obj)
    return obj


def _in(obj):
    return obj.get() if isinstance(obj, Ref) else obj


_LAYER_KEYS = ("rgb", "a", "src", "sa")


def demote(st):
    """A copy of the undo step with its big pictures / masks replaced by Refs (the step itself is not changed)."""
    new = dict(st)
    new["_spilled"] = True
    for k in ("base", "shadow_src", "alpha", "sel"):
        if k in new:
            new[k] = _out(new[k])
    if new.get("layers"):
        new["layers"] = [dict(l, **{k: _out(l[k]) for k in _LAYER_KEYS if l.get(k) is not None}) for l in new["layers"]]
    stash = new.get("stash")
    if stash:
        new["stash"] = dict(stash, **{k: _out(stash[k]) for k in ("base", "shadow_src", "sel") if stash.get(k) is not None})
    return new


def promote(st):
    """The undo step with real pictures again (a copy; Refs are read back from the disk)."""
    new = dict(st)
    new.pop("_spilled", None)
    for k in ("base", "shadow_src", "alpha", "sel"):
        if k in new:
            new[k] = _in(new[k])
    if new.get("layers"):
        new["layers"] = [dict(l, **{k: _in(l[k]) for k in _LAYER_KEYS if isinstance(l.get(k), Ref)}) for l in new["layers"]]
    stash = new.get("stash")
    if stash:
        new["stash"] = dict(stash, **{k: _in(stash[k]) for k in ("base", "shadow_src", "sel") if isinstance(stash.get(k), Ref)})
    return new


def is_demoted(st):
    return bool(st.get("_spilled"))


def cost(states):
    """(how many different big pictures, how many bytes of raw pixels) a list of steps holds, in memory or on disk."""
    seen = {}
    for st in states:
        for x in [st.get("base"), st.get("shadow_src")] + [l.get("rgb") for l in st.get("layers", []) or []]:
            if x is not None:
                seen[id(x)] = x
    n = len(seen)
    b = sum(x.nbytes if isinstance(x, Ref) else _size_of(x) for x in seen.values())
    return n, b
