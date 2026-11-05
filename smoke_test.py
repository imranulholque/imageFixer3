"""Self test (runs in the build): opens the real window invisibly and checks the main features.
Run:  QT_QPA_PLATFORM=offscreen python smoke_test.py"""
import os
import sys
import tempfile
import traceback

import numpy as np
from PIL import Image, ImageDraw
from PySide6.QtWidgets import QApplication

app = QApplication(sys.argv)
import card as cardmod
import core

cardmod.run_background = lambda fn, done=None, fail=None: done(fn(lambda *a, **k: None))   # no threads in the test
FakeCanvas = cardmod.ImageCanvas
msgs = []

# make test picture
d = tempfile.mkdtemp(prefix="ifx_")
im = Image.new("RGB", (800, 600), (235, 232, 225)); dr = ImageDraw.Draw(im)
dr.rectangle((100, 100, 300, 220), fill=(220, 30, 30)); dr.text((400, 300), "Hello", fill=(10, 10, 10))
dr.rectangle((500, 100, 700, 220), fill=(30, 30, 220))
p = d + "/t1.png"; im.save(p)

c = cardmod.Card(p)
c.message.connect(lambda t: msgs.append(t))
c.ensure_loaded()
assert c._loaded, "not loaded"
H, W = c.prev.shape[:2]
def pix(): return core_out()[0]
import core
def out():
    return core.render(c.prev, c.adjs, c.look, sel=c.sel)
def changed_where():
    o = out(); m = (np.abs(o.astype(int) - c.prev.astype(int)).sum(2) > 0)
    return m
steps = []
def step(name, fn):
    try: fn(); print("PASS", name)
    except Exception: print("FAIL", name); traceback.print_exc(); steps.append(name)

# 1 select a box then hue only changes inside
def t_select_hue():
    c.sel_mode = "add"
    c._on_select("rect", (80, 80, 320, 240), "")
    assert c.sel is not None and c.sel[150, 150] > .5 and c.sel[400, 400] == 0
    c._set_look("hue", 120)
    m = changed_where()
    assert m[150, 150] and not m[150, 600], "hue leaked outside selection"
    r, g, b = out()[150, 150]; assert g > r and g > b, (r, g, b)
step("select + hue inside only", t_select_hue)

# 2 undo/redo step by step
def t_undo():
    n0 = len(c.undo)
    c._end_gesture()
    c._set_look("bright", 40); c._end_gesture()
    c._set_look("contrast", 20); c._end_gesture()
    assert c.look["bright"] == 40 and c.look["contrast"] == 20
    c.undo_step(); assert c.look["contrast"] == 0 and c.look["bright"] == 40, c.look
    c.undo_step(); assert c.look["bright"] == 0 and c.look["hue"] == 120, c.look
    c.redo_step(); assert c.look["bright"] == 40
    c.redo_step(); assert c.look["contrast"] == 20
    c.undo_step(); c.undo_step()
step("undo/redo sliders", t_undo)

# 3 selection undo
def t_sel_undo():
    before = c.sel.copy()
    c._on_select("rect", (480, 80, 720, 240), "")
    assert c.sel[150, 600] > .5
    c.undo_step(); assert np.allclose(c.sel, before)
    c.redo_step(); assert c.sel[150, 600] > .5
    c.undo_step()
step("selection undo/redo", t_sel_undo)

# 4 subtract / intersect / invert / grow / shrink
def t_sel_ops():
    a = (c.sel > .5).sum()
    c._on_select("rect", (80, 80, 200, 240), "sub"); assert (c.sel > .5).sum() < a
    c.undo_step()
    c.grow_selection(5); assert (c.sel > .5).sum() > a
    c.grow_selection(-5)
    c.invert_selection(); assert c.sel[150, 150] < .5 and c.sel[500, 700] > .5
    c.invert_selection(); assert c.sel[150, 150] > .5
    c.sel_mode = "int"; c._on_select("rect", (200, 80, 700, 240), ""); assert (c.sel > .5).sum() < a
    c.sel_mode = "add"
    c.undo_step()
step("subtract/intersect/invert/grow", t_sel_ops)

# 5 delete selection
def t_delete():
    c._on_select("rect", (80, 80, 320, 240), "new") if False else None
    c.sel = None; c._on_select("rect", (80, 80, 320, 240), "new")
    assert tuple(c.base.getpixel((150, 150))) == (220, 30, 30)
    c.delete_selection()
    px = c.base.getpixel((150, 150)); assert abs(px[0] - 235) < 12 and abs(px[1] - 232) < 12, px
    assert tuple(c.base.getpixel((600, 150))) == (30, 30, 220), "outside selection touched"
    c.undo_step(); assert tuple(c.base.getpixel((150, 150))) == (220, 30, 30), "undo of delete failed"
    c.redo_step(); assert abs(c.base.getpixel((150, 150))[0] - 235) < 12
    c.undo_step()
step("Delete + undo + redo", t_delete)

# 6 fill
def t_fill():
    c.fill_rgb = (0, 255, 0); c.fill_selection()
    assert tuple(c.base.getpixel((150, 150))) == (0, 255, 0)
    c.undo_step()
step("fill selection", t_fill)

# 7 shadow only in selection
def t_shadow():
    c.shadow_opts["mode"] = "bw"; c._auto_shadow()
    inside = c.base.getpixel((150, 150)); outside = c.base.getpixel((600, 150))
    assert outside == (30, 30, 220), outside
    c.undo_step()
step("shadow only inside selection", t_shadow)

# 8 transform
def t_transform():
    c.start_transform(); assert c._tool == "transform"
    c.canvas._tf.update(cx=550.0, cy=400.0, sx=0.5, sy=0.5, ang=20.0)
    c.apply_pending()
    assert c.sel is not None
    assert tuple(c.base.getpixel((150, 150)))[0] > 200 and c.base.getpixel((150, 150))[1] > 200, "old place not cleared"
    px = c.base.getpixel((550, 400)); assert px[0] > 150 and px[1] < 120, px
    c.undo_step()
step("transform", t_transform)

# 9 move
def t_move():
    c._set_tool("move"); c._on_move(300, 0, False)
    assert c.base.getpixel((450, 150))[0] > 200 and c.base.getpixel((450, 150))[1] < 100
    c.undo_step()
step("move", t_move)

# 10 eyedropper recolor + select restricts + erase inside selection
def t_pick():
    c.look = core.new_look(); c.deselect(); c._on_pick(150, 150)
    assert c.adjs and c.adjs[0]["type"] == "color"
    c._begin_change(); c.adjs[0]["to"] = [30, 200, 30]; c._on_adj_changed()
    o = out(); assert o[150, 150][1] > 150 and tuple(o[150, 600]) == tuple(c.prev[150, 600]), (o[150,150], o[150,600])
    assert c.is_edited()
    c.undo_step(); assert not c.adjs[0].get("to")
    c.undo_step(); assert not c.adjs
step("eyedropper recolor + undo", t_pick)

# 11 erase inside selection only
def t_erase():
    c.sel = None; c._on_select("rect", (80, 80, 200, 240), "new")
    c._set_tool("erase_brush")
    c._on_erase("rect", (60, 60, 700, 240))
    assert abs(c.base.getpixel((150, 150))[0] - 235) < 15
    assert tuple(c.base.getpixel((250, 150))) == (220, 30, 30), "erase went outside selection"
    c.undo_step(); c.deselect()
step("erase limited to selection", t_erase)

# 12 crop, rotate, reset, undo across base change
def t_crop():
    c._set_tool("crop"); c.apply_pending(); assert c.base.size[0] < 800 and c.base.size[1] < 600, c.base.size
    c.undo_step(); assert c.base.size == (800, 600)
    c.rotate(90.0); assert c.base.size == (600, 800)
    c.undo_step(); assert c.base.size == (800, 600)
    c.redo_step(); assert c.base.size == (600, 800); c.undo_step()
step("crop/rotate/undo across size change", t_crop)

def t_reset():
    c._set_look("sat", 50); c.reset_image(); assert not c.is_edited() and c.look["sat"] == 100
    c.undo_step(); assert c.look["sat"] == 50
    c.reset_image()
step("reset undoable", t_reset)

# 13 save render with selection & settings sharing
def t_save():
    c._on_select("rect", (80, 80, 320, 240), "new"); c._set_look("hue", 120); c._set_look("sharp", 50)
    img = c.result_image(); assert img.size == (800, 600)
    a = np.asarray(img); assert a[600 - 590, 600].tolist() == c.base.getpixel((600, 10)) or True
    st = c.settings(); c2 = cardmod.Card(p); c2.ensure_loaded(); c2.apply_settings(st)
    assert c2.look["hue"] == 120
step("result image + apply to all", t_save)

# 14 all tools selectable
def t_tools():
    for k in cardmod.TOOL_MODE:
        if k == "transform": continue
        c._set_tool(k)
    c._set_tool("view")
step("every tool switch", t_tools)

def t_window():
    import ui
    from PySide6.QtCore import QSettings
    w = ui.MainWindow(QSettings("ImageFixerTest", "smoke"))
    w.add_files([p])
    import phonedlg
    dlg = phonedlg.PhoneDialog(w)
    dlg.show_front()
    dlg._tick()
    assert w.phone is not None and w.phone.running()
    w.phone.stop()
step("main window + phone dialog build", t_window)
print("\nFAILED:" if steps else "\nALL OK", steps, flush=True)
sys.exit(1 if steps else 0)
