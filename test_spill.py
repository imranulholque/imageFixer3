"""Test of spill.py (old undo steps on the disk).  Run:  python test_spill.py   (needs only numpy + Pillow)"""
import gc
import os
import sys
import time

import numpy as np
from PIL import Image

import spill


def wait_written():
    spill._q.join()


def folder_files():
    d = spill._dir
    return sorted(os.listdir(d)) if d and os.path.isdir(d) else []


def pic(seed, size=(1600, 1200)):
    rng = np.random.default_rng(seed)
    return Image.fromarray(rng.integers(0, 256, (size[1], size[0], 3), dtype=np.uint8))


def same(a, b):
    if isinstance(a, Image.Image):
        return a.mode == b.mode and a.size == b.size and a.tobytes() == b.tobytes()
    return a.dtype == b.dtype and a.shape == b.shape and np.array_equal(a, b)


fails = []


def check(name, cond):
    print(("PASS " if cond else "FAIL ") + name)
    if not cond:
        fails.append(name)


# ---------------------------------------------------------------- round trip is bit for bit
base, alpha = pic(1), pic(2).convert("L")
sel = np.random.default_rng(3).random((900, 1200), dtype=np.float32)
layer = {"id": 1, "name": "sig", "rgb": pic(4, (700, 500)), "a": pic(5, (700, 500)).convert("L"), "src": None, "sa": None,
         "grid": [(0, 0)], "opacity": 100, "visible": True}
small = Image.new("RGB", (40, 40), (1, 2, 3))                       # below the size limit: stays in memory
st = {"base": base, "adjs": [{"x": 1}], "look": {"b": 5}, "shadow": False, "sel": sel, "ops": 3, "shadow_src": pic(6),
      "ctx": None, "alpha": alpha, "stash": {"base": pic(7), "adjs": [], "look": {}, "sel": None, "shadow_src": None, "shadow": False},
      "layers": [layer], "label": "Erase"}
orig = {"base": base.tobytes(), "alpha": alpha.tobytes(), "sel": sel.copy(), "lrgb": layer["rgb"].tobytes(),
        "la": layer["a"].tobytes(), "stash": st["stash"]["base"].tobytes(), "ssrc": st["shadow_src"].tobytes()}

d = spill.demote(st)
check("demote keeps the original step untouched", st["base"] is base and not spill.is_demoted(st))
check("demoted step holds Refs", spill.is_demoted(d) and isinstance(d["base"], spill.Ref) and isinstance(d["sel"], spill.Ref))
check("small things stay in memory", spill._out(small) is small)
check("labels / numbers are kept", d["label"] == "Erase" and d["ops"] == 3 and d["adjs"] == [{"x": 1}])
check("layer dict is a copy (shared dict not changed)", d["layers"][0] is not layer and layer["rgb"].tobytes() == orig["lrgb"])

# ---------------------------------------------------------------- the same object comes back while it is alive
p = spill.promote(d)
check("same object while still in memory", p["base"] is base and p["sel"] is sel and p["layers"][0]["rgb"] is layer["rgb"])

# ---------------------------------------------------------------- after the originals are gone: read from disk, identical
wait_written()
del st, p, base, alpha, sel, layer
gc.collect()
r = spill.promote(d)
check("base identical after reload", r["base"].tobytes() == orig["base"] and r["base"].mode == "RGB")
check("alpha identical (mode L)", r["alpha"].tobytes() == orig["alpha"] and r["alpha"].mode == "L")
check("mask identical (float32)", np.array_equal(r["sel"], orig["sel"]) and r["sel"].dtype == np.float32)
check("layer pictures identical", r["layers"][0]["rgb"].tobytes() == orig["lrgb"] and r["layers"][0]["a"].tobytes() == orig["la"])
check("stash + shadow_src identical", r["stash"]["base"].tobytes() == orig["stash"] and r["shadow_src"].tobytes() == orig["ssrc"])
check("reloaded mask is writable", r["sel"].flags.writeable)

# ---------------------------------------------------------------- two steps sharing one picture: one file, same object back
shared = pic(8)
a, b = {"base": shared, "layers": []}, {"base": shared, "layers": []}
wait_written()
n_before = len(folder_files())
da, db = spill.demote(a), spill.demote(b)
check("shared picture has one Ref", da["base"] is db["base"])
wait_written()
n_after = len(folder_files())
check("shared picture written once (2 steps, %d files for it)" % (n_after - n_before), n_after - n_before == 1)
raw = shared.tobytes()
del a, b, shared
gc.collect()
pa, pb = spill.promote(da), spill.promote(db)
check("shared picture comes back as ONE object", pa["base"] is pb["base"] and pa["base"].tobytes() == raw)

# ---------------------------------------------------------------- memory really is released (Linux only: needs /proc)
steps = []
if os.path.exists("/proc/self/statm"):
    def rss_mb():
        with open("/proc/self/statm") as f:
            return int(f.read().split()[1]) * os.sysconf("SC_PAGE_SIZE") / 1e6


    gc.collect()
    m0 = rss_mb()
    keep = [pic(50 + i, (2000, 1500)) for i in range(10)]              # control: 10 pictures kept in memory (90 MB)
    m_keep = rss_mb() - m0
    del keep
    gc.collect()
    m1 = rss_mb()
    steps = []
    for i in range(10):
        im = pic(100 + i, (2000, 1500))
        steps.append(spill.demote({"base": im, "layers": []}))
        del im
    wait_written()
    gc.collect()
    m_spill = rss_mb() - m1
    check("control: 10 pictures kept in memory = +%.0f MB" % m_keep, m_keep > 70)
    check("same 10 pictures as old steps = +%.0f MB" % m_spill, m_spill < 30)
    check("and each can be read back exactly", all(spill.promote(s)["base"].size == (2000, 1500) for s in steps))

else:
    print("SKIP memory measurement (no /proc on this system)")
    for i in range(10):
        steps.append(spill.demote({"base": pic(100 + i, (2000, 1500)), "layers": []}))
    wait_written()

# ---------------------------------------------------------------- cost() and files are removed with the steps
n, nb = spill.cost(steps)
check("cost counts pictures and bytes", n == 10 and nb == 10 * 2000 * 1500 * 3)
files = len(folder_files())
steps.clear(); del da, db, pa, pb, d, r
gc.collect()
check("files are deleted when steps are dropped (%d -> %d)" % (files, len(folder_files())), len(folder_files()) == 0)

# ---------------------------------------------------------------- write failure: nothing is lost, disk use stops
spill._failed = False
orig_folder = spill._folder
spill._folder = lambda: "/nonexistent/dir/that/does/not/exist"
im = pic(200)
raw = im.tobytes()
dd = spill.demote({"base": im, "layers": []})
wait_written()
del im
gc.collect()
check("failed write: picture still there", spill.promote(dd)["base"].tobytes() == raw)
check("failed write: disk use is switched off", spill.can_write() is False)
spill._folder = orig_folder
spill._failed = False

# ---------------------------------------------------------------- stale folders of dead processes are cleaned
import tempfile
stale = os.path.join(tempfile.gettempdir(), spill._PREFIX + "999999_stale")
os.makedirs(stale, exist_ok=True)
spill.cleanup_old()
check("old folder of a dead program removed", not os.path.exists(stale))

print("\n%d failed" % len(fails) if fails else "\nALL PASS")
sys.exit(1 if fails else 0)
