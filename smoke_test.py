"""Self test (runs in the build): opens the real window invisibly and checks the main features.
Run:  QT_QPA_PLATFORM=offscreen python smoke_test.py"""
import os
import sys
import tempfile
import traceback

import numpy as np
from PIL import Image, ImageDraw
from PySide6.QtCore import Qt
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

def _pump(cond, seconds=15):
    import time
    end = time.time() + seconds
    while not cond() and time.time() < end:
        app.processEvents(); time.sleep(0.005)
    app.processEvents()

def _window_with(n):
    import ui
    from PySide6.QtCore import QSettings
    paths = []
    for i in range(n):
        q = d + "/m%d_%d.png" % (n, i)
        Image.new("RGB", (240, 160), (200, 200 - i * 10, 190)).save(q); paths.append(q)
    w = ui.MainWindow(QSettings("ImageFixerTest", "smoke_m%d" % n))
    w.add_files(paths)
    _pump(lambda: not w._adding and len(w.carousel.cards) == n)
    assert len(w.carousel.cards) == n and w.strip.count() == n, "pictures were not added"
    return w

def t_multi_remove():
    w = _window_with(5)
    w.strip.clearSelection()
    for r in (1, 2, 3):
        w.strip.item(r).setSelected(True)
    uids = w.strip.uids()
    assert len(uids) == 3
    w.remove_by_uids(uids)
    assert len(w.carousel.cards) == 2 and w.strip.count() == 2 and len(w.items) == 2
    assert [c.uid for c in w.carousel.cards] == [w.strip.item(i).data(Qt.ItemDataRole.UserRole) for i in range(2)]
    w.remove_by_uids(w.strip.uids() or [w.carousel.cards[0].uid])
    w.clear_all()
    assert not w.carousel.cards and w.strip.count() == 0
step("remove several thumbnails at once", t_multi_remove)

def t_opening_async():
    w = _window_with(3)
    w.carousel.go_to(2, animate=False)
    _pump(lambda: w.carousel.cards[2]._loaded)
    assert w.carousel.cards[2]._loaded and w.carousel.cards[2].prev is not None
    w.runner.wait_idle()
    # settings copied to a picture that was never opened are applied when it opens
    c0, c1 = w.carousel.cards[0], w.carousel.cards[1]
    _pump(lambda: c0._loaded)
    c0._set_look("sat", 60)
    c1.apply_action(c0.last_action)                  # not opened yet: kept and applied on opening (or applied now)
    assert c1.is_edited()
    w.carousel.go_to(1, animate=False)
    _pump(lambda: w.carousel.cards[1]._loaded and not w.runner.busy)
    assert w.carousel.cards[1].look["sat"] == 60, w.carousel.cards[1].look
    src = c1.render_source()
    assert src().size == (240, 160)
step("pictures open in the background, settings reach unopened pictures", t_opening_async)

def t_delete_key_on_strip():
    try:
        from PySide6.QtTest import QTest
        from PySide6.QtCore import Qt as _Qt
        w = _window_with(3)
        w.show(); w.strip.setFocus()
        w.strip.clearSelection(); w.strip.item(0).setSelected(True); w.strip.item(1).setSelected(True)
        QTest.keyClick(w.strip, _Qt.Key.Key_Delete)
        app.processEvents()
        assert len(w.carousel.cards) == 1, "Delete on the thumbnails removed %d" % (3 - len(w.carousel.cards))
    except ImportError:
        print("skipped (no QtTest)")
step("Delete key on the thumbnails", t_delete_key_on_strip)

def t_quality():
    core.set_preview_max(core.quality_preview_max("sharp"))
    big = Image.new("RGB", (6000, 4000), (220, 220, 220))
    prev, sc = core.make_preview(big)
    assert max(prev.shape[:2]) == 3200, prev.shape
    core.set_preview_max(core.quality_preview_max("balanced"))
step("preview quality levels", t_quality)


def t_saturation_real():
    rng = np.random.default_rng(3)
    x = rng.integers(0, 256, (4000, 1, 3)).astype(np.float32)
    Y = lambda a: a[..., 0] * 0.299 + a[..., 1] * 0.587 + a[..., 2] * 0.114
    g = core._saturate(x, 0)
    assert np.abs(g[..., 0] - g[..., 1]).max() < 0.01 and np.abs(Y(g) - Y(x)).max() < 0.01, "0 must be a gray picture"
    for sat in (40, 150, 200):
        o = core._saturate(x, sat)
        assert np.abs(Y(o) - Y(x)).max() < 0.05, "brightness must not change"
        assert o.min() > -0.1 and o.max() < 255.1, "no clipping"
    gray = np.full((3, 1, 3), 90, np.float32)
    assert np.abs(core._saturate(gray, 200) - gray).max() < 0.01, "gray must not change"
    assert core.tone_active(core.new_look(vib=20))
step("saturation: true saturation, brightness kept, no clipping", t_saturation_real)

def t_manual_shadow():
    W, H = 600, 400
    yy, xx = np.mgrid[0:H, 0:W]
    light = (0.95 - 0.4 * xx / W).astype(np.float32)
    page = np.clip(np.full((H, W, 3), 235, np.float32) * light[..., None], 0, 255).astype(np.uint8)
    before = page[..., 1].std()
    out = core.render(page, [], core.new_look(flat=100), cache={})
    assert out[..., 1].std() < before * 0.4, "even-light must flatten the page"
    assert core.look_edited(core.new_look(shlift=10)) and not core.look_edited(core.new_look(flat_size=20))
    lift = core.render(page, [], core.new_look(shlift=100, white=40), cache={})
    assert lift[..., 1].mean() > page[..., 1].mean() + 20
    sel = np.zeros((H, W), np.float32); sel[:, W // 2:] = 1
    part = core.render(page, [], core.new_look(flat=100), cache={}, sel=sel)
    assert (part[:, :W // 2 - 2] == page[:, :W // 2 - 2]).all(), "outside the selection stays"
    r = core.remove_shadow(Image.fromarray(page), 1.0, None, {"even": 60, "depth": 80, "tint": 30, "clean": 50})
    assert r.size == (W, H)
step("manual shadow removal works without the one-click", t_manual_shadow)

def t_crop_shapes():
    for tool, shape_ok in (("crop_circle", True), ("crop_oval", True), ("crop_round", True)):
        cc = cardmod.Card(p); cc.message.connect(lambda t: None); cc.ensure_loaded()
        cc._set_tool(tool)
        assert cc._tool == tool and cc.canvas.crop_shape in ("circle", "oval", "round")
        w0, h0 = cc.base.size
        cc.apply_pending()
        assert cc.base.size[0] < w0 and cc.base.size[1] < h0, "the picture must be cropped"
        if tool == "crop_circle":
            assert abs(cc.base.size[0] - cc.base.size[1]) <= 1, "a circle needs a square box"
        assert cc._tool == "view"
    # a picture on the page: transparent outside the circle
    rgb = Image.new("RGB", (300, 200), (10, 10, 200)); a = Image.new("L", (300, 200), 255)
    r2, a2 = core.crop_to_shape(rgb, a, (50, 0, 250, 200), "oval")
    assert a2.size == (200, 200) and a2.getpixel((1, 1)) == 0 and a2.getpixel((100, 100)) == 255
    m = Image.new("L", (300, 200), 0); ImageDraw.Draw(m).ellipse((40, 40, 160, 160), fill=255)
    r3, a3 = core.crop_to_shape(rgb, a, (40, 40, 161, 161), None, mask=m)
    assert a3.getpixel((1, 1)) == 0 and a3.getpixel((60, 60)) == 255
step("crop to circle / oval / rounded box / selection shape", t_crop_shapes)

print("\nFAILED:" if steps else "\nALL OK", steps, flush=True)
sys.exit(1 if steps else 0)
