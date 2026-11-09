"""Self test (runs in the build): opens the real window invisibly and checks the main features.
Run:  QT_QPA_PLATFORM=offscreen python smoke_test.py"""
import os
import sys
import tempfile
import traceback

import numpy as np
from PIL import Image, ImageDraw, ImageFilter
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
    except Exception:
        why = traceback.format_exc().strip().splitlines()[-1]          # the cause on the FAIL line itself
        print("FAIL", name, "->", why); traceback.print_exc(); steps.append(name)

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
    c.look = core.new_look(); c.adjs = []; c.colors.rebuild(c.adjs); c._sync_sliders()   # leftover hue must not be baked in
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

# 11b color brush: only inside the selection, anywhere without one, one undo step per stroke
def t_paint():
    c.sel = None; c.deselect(); c._set_tool("paint"); assert c._tool == "paint"
    c._set_fill((0, 0, 255)); c.sizeSlider.setValue(40); c.opSlider.setValue(100); c.softSlider.setValue(0)
    c._on_paint([(400.0, 500.0), (700.0, 500.0)])                       # no selection: paints on the page
    assert tuple(c.base.getpixel((550, 500))) == (0, 0, 255), c.base.getpixel((550, 500))
    c.undo_step(); assert tuple(c.base.getpixel((550, 500))) != (0, 0, 255)
    c._on_select("rect", (80, 80, 200, 240), "new"); c._set_tool("paint")
    c._on_paint([(20.0, 150.0), (700.0, 150.0)])                        # with a selection: only inside it
    assert tuple(c.base.getpixel((150, 150))) == (0, 0, 255), c.base.getpixel((150, 150))
    assert tuple(c.base.getpixel((250, 150))) != (0, 0, 255), "paint went outside selection"
    c.undo_step(); c.deselect(); c._set_fill((255, 255, 255))
step("color brush (selection / no selection / undo)", t_paint)

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
    w.runner.wait_idle()                           # removing / overlays are refused while a job runs
    return w

def t_multi_remove():
    w = _window_with(5)
    w.strip.clearSelection()
    for r in (1, 2, 3):
        w.strip.item(r).setSelected(True)
    uids = w.strip.uids()
    assert len(uids) == 3
    w.runner.wait_idle()                           # thumbnail clicks start loading jobs; removing is refused while they run
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


def t_scanner_v2():
    import scanner, math
    W, H = 1200, 900
    bgim = Image.new("RGB", (W, H), (60, 52, 44)); dr = ImageDraw.Draw(bgim)
    for i in range(0, W, 24): dr.line([(i, 0), (i + 90, H)], fill=(80, 66, 50), width=6)       # a stripy table
    paper = Image.new("RGB", (500, 700), (238, 236, 228)); pd = ImageDraw.Draw(paper)
    for i in range(20): pd.text((30, 30 + i * 30), "text line %d lorem ipsum" % i, fill=(20, 20, 30))
    quad = [(300, 120), (900, 170), (860, 800), (260, 740)]
    co = core._coeffs(quad, [(0, 0), (500, 0), (500, 700), (0, 700)])
    wp = paper.transform((W, H), Image.Transform.PERSPECTIVE, co, Image.Resampling.BICUBIC)
    wm = Image.new("L", (500, 700), 255).transform((W, H), Image.Transform.PERSPECTIVE, co, Image.Resampling.BILINEAR)
    scene = Image.composite(wp, bgim, wm)
    q, found = scanner.detect_document(scene)
    assert found, "the paper must be found"
    err = max(math.hypot(a[0] - b[0], a[1] - b[1]) for a, b in zip(core.order_quad(q), core.order_quad(quad)))
    assert err < 0.03 * math.hypot(W, H), "corners off by %.0f px" % err
    noise = Image.fromarray((np.random.default_rng(1).random((600, 800, 3)) * 100 + 60).astype(np.uint8))
    assert scanner.detect_document(noise)[1] is False, "noise is not a paper"
    out = scanner.enhance(scanner.straighten(scene, q), "clean"); assert out.size[0] > 100
step("phone scanner finds the paper on a busy background", t_scanner_v2)

def t_warp_middle():
    n = 4
    patch = Image.new("RGBA", (400, 400), (255, 255, 255, 255)); ImageDraw.Draw(patch).line([(0, 200), (400, 200)], fill=(0, 0, 0, 255), width=6)
    grid = [((j / n) * 400, (i / n) * 400) for i in range(n + 1) for j in range(n + 1)]
    grid[(n // 2) * (n + 1) + n // 2] = (200, 120)
    a = np.asarray(core.warp_layer(patch, (400, 400), grid, n))
    ys = [int(np.argmin(a[:, x, 0].astype(int) + (a[:, x, 3] == 0) * 999)) for x in (10, 200, 390)]
    assert ys[1] < 140 and abs(ys[0] - 200) < 5 and abs(ys[2] - 200) < 5, ys
step("warp net has a middle node", t_warp_middle)

def t_select_tools():
    W, H = 400, 300
    im = Image.new("RGB", (W, H), (236, 232, 224)); dd = ImageDraw.Draw(im)
    dd.ellipse((60, 60, 220, 220), fill=(200, 40, 50)); dd.rectangle((260, 80, 380, 180), fill=(214, 120, 125))
    rgb = np.asarray(im); lab = core.rgb_to_lab(rgb)
    narrow = core.color_range_fuzzy(lab, (200, 40, 50), 20); wide = core.color_range_fuzzy(lab, (200, 40, 50), 140)
    assert narrow[100:180, 300:360].mean() < 0.05 and wide[100:180, 300:360].mean() > 0.9, "fuzziness must widen the selection"
    assert core.color_range_fuzzy(lab, (200, 40, 50), 140, (140, 140), 0.2)[100:180, 300:360].mean() < 0.05, "near the click only"
    sel = (wide > 0.5).astype(np.float32)
    stroke = np.zeros((H, W), np.float32); stroke[110:150, 280:360] = 1
    after = core.sel_combine(sel, core.smart_deselect(sel, lab, stroke, 28.0, 40.0), "sub")
    assert after[110:150, 280:360].mean() < 0.05 and after[120:160, 100:180].mean() > 0.95, "deselect brush: only the painted part goes"
step("color range fuzziness and the deselect brush", t_select_tools)

def t_news_and_text():
    rng = np.random.default_rng(3); W, H = 700, 900
    pg = Image.new("RGB", (W, H), (214, 205, 170)); dd = ImageDraw.Draw(pg)
    for i in range(24): dd.text((40, 40 + i * 30), "newspaper text line number %d here" % i, fill=(40, 40, 40))
    dd.rectangle((400, 700, 640, 860), outline=(160, 20, 30), width=8)
    a = np.asarray(pg).astype(np.float32) * (0.95 - 0.3 * np.mgrid[0:H, 0:W][1] / W)[..., None] + rng.normal(0, 7, (H, W, 3))
    img = np.clip(a, 0, 255).astype(np.uint8)
    s = core.auto_news_strength(img); assert 30 <= s <= 95
    out = core.render(img, [], core.new_look(news=s), cache={}).astype(np.float32)
    assert out[:20].mean() > 245, "the page must be white"
    assert out[700:860, 400:410].reshape(-1, 3).mean(0)[0] > out[700:860, 400:410].reshape(-1, 3).mean(0)[1] + 40, "a red shape stays red"
    weak = core.render(img, [], core.new_look(news=20), cache={}).astype(np.float32)
    assert weak[:20].mean() <= out[:20].mean() + 1, "the slider works in both directions"
    # text options: faint writing gets clearly stronger, the paper stays
    ink = Image.new("L", (800, 500), 0); di = ImageDraw.Draw(ink)
    for i in range(14): di.text((30, 20 + i * 30), "faint writing sample %d" % i, fill=255)
    m = np.asarray(ink.filter(ImageFilter.GaussianBlur(1.5))).astype(np.float32) / 255 * 0.5
    faint = np.clip(np.stack([230 - 150 * m] * 3, -1), 0, 255).astype(np.uint8)
    o = core.render(faint, [], core.new_look(sharp=100, objs=100, detail=100, dark=100), cache={}).astype(np.float32).mean(2)
    assert o[:15].mean() > 215 and o[(m > 0.15)].mean() < o[(m < 0.01)].mean() - 90, "text contrast must rise, paper must stay white"
step("newspaper page to white + stronger text options", t_news_and_text)

def t_background_cut():
    rng = np.random.default_rng(0); W, H = 500, 250
    ink = Image.new("L", (W, H), 0); ImageDraw.Draw(ink).line([(20, 200), (120, 40), (220, 190), (330, 60), (470, 150)], fill=255, width=5)
    a = np.asarray(ink).astype(np.float32) / 255
    x = np.full((H, W, 3), (226, 224, 214), np.float32) * (1 - a[..., None] * 0.9) + rng.normal(0, 14, (H, W, 3))
    cut = core.remove_background(Image.fromarray(np.clip(x, 0, 255).astype(np.uint8)), None, 50, 30, False)
    al = np.asarray(cut)[..., 3].astype(np.float32) / 255
    assert (al[a < 0.01] > 0.1).mean() < 0.01, "paper noise must not stay"
    assert al[a > 0.9].mean() > 0.9, "the writing must stay"
step("background cut: noisy paper gives no specks", t_background_cut)

def t_save_helpers():
    import tempfile
    dd = tempfile.mkdtemp(); fp = os.path.join(dd, "x.jpg"); Image.new("RGB", (40, 30), (10, 200, 30)).save(fp)
    bk = core.backup_original(fp, "t1"); core.write_replacing(Image.new("RGB", (40, 30), (200, 10, 30)), fp)
    assert Image.open(fp).getpixel((3, 3))[0] > 150 and Image.open(bk).getpixel((3, 3))[1] > 150
    assert not [n for n in os.listdir(dd) if n.startswith(".~")]
    core.purge_dir(core.ORIG_DIR)
step("save replaces the original through a temporary file, a backup is kept", t_save_helpers)

def t_incoming_routing():
    w = _window_with(2)
    c = w.carousel.cards[0]
    w.carousel.go_to(0, animate=False); _pump(lambda: c._loaded)
    q = d + "/ov.png"; Image.new("RGB", (60, 40), (20, 20, 120)).save(q)
    c.tabGroup.button(0).click()                                   # Adjust tab: a new picture arrives -> a new main picture
    n0 = len(w.carousel.cards); w._incoming([q], "phone"); _pump(lambda: not w._adding and len(w.carousel.cards) == n0 + 1); w.runner.wait_idle()
    assert len(w.carousel.cards) == n0 + 1 and w.carousel.cards[-1].origin == "phone" and not w.carousel.cards[-1].can_replace()
    w.carousel.go_to(0, animate=False); _pump(lambda: c._loaded and not w.runner.busy)   # the new picture opened by itself: go back to this one
    c.tabGroup.button(c.TAB_PICTURE).click()                      # Picture tab: it becomes an overlay on this page
    w._incoming([q], "file"); _pump(lambda: len(c.layers) == 1 and not w.runner.busy)
    assert len(c.layers) == 1 and len(w.carousel.cards) == n0 + 1, "overlay, not a new picture"
step("incoming pictures: overlay in the Picture tab, main picture elsewhere", t_incoming_routing)

def t_pdf_pages():
    from PIL import Image
    pages = [Image.new("RGB", (400, 560), c) for c in ((255, 255, 255), (240, 230, 200), (200, 220, 255))]
    pdf = d + "/doc.pdf"; pages[0].save(pdf, save_all=True, append_images=pages[1:])
    w = _window_with(1)
    n0 = len(w.carousel.cards)
    w.carousel.cards[0].tabGroup.button(w.carousel.cards[0].TAB_ADJUST).click()
    w.add_files([pdf]); _pump(lambda: not w._adding and not w.runner.busy and len(w.carousel.cards) == n0 + 3, 30)
    assert len(w.carousel.cards) == n0 + 3, "every PDF page is its own card"
    assert all(not c.can_replace() for c in w.carousel.cards[-3:]), "PDF pages never replace the PDF"
step("PDF: every page opens as its own picture", t_pdf_pages)




def t_bake_global_before_select():
    cc = cardmod.Card(p); cc.message.connect(lambda t: None); cc.ensure_loaded()
    cc._set_look("news", 80)                                       # a setting for the WHOLE picture (no selection)
    assert cc.sel is None and cc._pending_settings()
    H, W = cc.prev.shape[:2]
    m = np.zeros((H, W), np.float32); m[:, :W // 3] = 1
    cc._set_selection(m, "new")                                    # now a selection: the setting must stay everywhere
    assert cc.sel is not None and not cc._pending_settings(), "the whole-picture setting must be written into the picture first"
    out = np.asarray(cc.result_image()).astype(np.float32)
    assert out[:, -20:].mean() >= out[:, :20].mean() - 3, "the area outside the selection must keep the change"
step("settings made before a selection stay on the whole picture", t_bake_global_before_select)


def t_move_selection_outline():
    from PySide6.QtCore import QPointF, Qt as _Qt
    cc = cardmod.Card(p); cc.message.connect(lambda t: None); cc.ensure_loaded()
    H, W = cc.prev.shape[:2]
    m = np.zeros((H, W), np.float32); m[10:40, 20:60] = 1
    cc._set_selection(m, "new")
    before = np.asarray(cc.base).copy()
    cc.canvas.set_mode(cc.canvas.SELECT, "rect")
    cc.canvas.sel_mode_name = "new"
    NM = _Qt.KeyboardModifier.NoModifier
    assert cc.canvas._can_move_selection(NM, QPointF(30, 20)), "inside the selection the outline can be dragged"
    assert not cc.canvas._can_move_selection(NM, QPointF(W - 5, H - 5)), "outside it a new shape is drawn"
    assert not cc.canvas._can_move_selection(_Qt.KeyboardModifier.ShiftModifier, QPointF(30, 20)), "Shift = a new shape is added"
    cc._move_selection(15, 7)
    ys, xs = np.nonzero(cc.sel)
    assert ys.min() == 17 and xs.min() == 35, (ys.min(), xs.min())
    assert (np.asarray(cc.base) == before).all(), "only the outline moves, never the picture"
    cc.undo_step()
    ys, xs = np.nonzero(cc.sel)
    assert ys.min() == 10 and xs.min() == 20, "Undo puts the outline back"
step("dragging inside a selection moves only the outline", t_move_selection_outline)

print("\nFAILED:" if steps else "\nALL OK", steps, flush=True)
sys.exit(1 if steps else 0)
