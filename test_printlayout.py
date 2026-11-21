"""Tests of printlayout.py (the print screen's maths): python test_printlayout.py"""
import math, os, sys, tempfile
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import printlayout as pl
from PIL import Image, ImageDraw

ok = fail = 0
def check(name, cond, extra=""):
    global ok, fail
    if cond: ok += 1; print("PASS", name)
    else: fail += 1; print("FAIL", name, extra)

def pic(w, h, color=(200, 30, 30)):
    im = Image.new("RGB", (w, h), color)
    d = ImageDraw.Draw(im); d.rectangle((0, 0, w - 1, h - 1), outline=(0, 0, 0), width=max(2, w // 100))
    return im

def mkpage(key, w, h, dpi=300.0, color=(200, 30, 30)):
    p = pl.Page(key, key, loader=lambda: pic(w, h, color), dpi=dpi)
    assert p.make_preview()
    return p

# ---------- scale modes (A4 = 190 x 277 mm printable at 4 px/mm)
ppmm = 4.0; W, H = int(190 * ppmm), int(277 * ppmm)
land = mkpage("land", 3000, 2000)
c = pl.compose(land.preview, W, H, False, True, "fit", ppmm, 100, land.dpi)
bb = Image.eval(c.convert("L"), lambda v: 255 if v < 250 else 0).getbbox()
check("fit: landscape turned, whole picture inside", bb is not None and bb[0] >= 0 and bb[2] <= W and bb[3] <= H and (bb[3]-bb[1]) > (bb[2]-bb[0]), bb)
c2 = pl.compose(land.preview, W, H, False, False, "fit", ppmm, 100, land.dpi)
bb2 = Image.eval(c2.convert("L"), lambda v: 255 if v < 250 else 0).getbbox()
check("fit without turning: width fills", bb2[2]-bb2[0] >= W-2, bb2)
c3 = pl.compose(land.preview, W, H, False, True, "fill", ppmm, 100, land.dpi)
check("fill: covers the whole sheet (no white corner)", c3.getpixel((2, 2)) != (255,255,255) and c3.getpixel((W-3, H-3)) != (255,255,255))
# actual size: 3000x2000 px at 300dpi = 254 x 169.3 mm.  (preview is 1800 wide -> scale must come from dpi of the FULL size)
full = land.load()
c4 = pl.compose(full, W, H, False, False, "actual", ppmm, 100, 300.0)
nonwhite = Image.eval(c4.convert("L"), lambda v: 255 if v < 250 else 0).getbbox()
check("actual: cut at sheet edge (254mm > 190mm)", nonwhite[0] == 0 and nonwhite[2] == W, nonwhite)
small = mkpage("small", 1200, 600)   # 101.6 x 50.8 mm
c5 = pl.compose(small.load(), W, H, False, False, "actual", ppmm, 100, 300.0)
bb5 = Image.eval(c5.convert("L"), lambda v: 255 if v < 250 else 0).getbbox()
check("actual: 101.6mm wide", abs((bb5[2]-bb5[0]) / ppmm - 101.6) < 1.0, (bb5[2]-bb5[0]) / ppmm)
c6 = pl.compose(small.load(), W, H, False, False, "custom", ppmm, 50, 300.0)
bb6 = Image.eval(c6.convert("L"), lambda v: 255 if v < 250 else 0).getbbox()
check("custom 50%: 50.8mm wide", abs((bb6[2]-bb6[0]) / ppmm - 50.8) < 1.0, (bb6[2]-bb6[0]) / ppmm)
check("mono output is L", pl.compose(small.load(), W, H, True).mode == "L")

# ---------- fit_geometry == compose
g = pl.fit_geometry(land, 190, 277, "fit", 100, True)
check("fit_geometry rotates landscape", g[2] == 270.0)
w_mm, h_mm, rot = g
check("fit_geometry fits inside area (rotated)", h_mm <= 190 + 1e-6 and w_mm <= 277 + 1e-6 and (abs(h_mm - 190) < 1e-6 or abs(w_mm - 277) < 1e-6), g)

# ---------- n-up
check("nup_grid 2 on A4 portrait = 1x2 or 2x1", pl.nup_grid(2, 190, 277, 210/297) in ((1, 2), (2, 1)))
check("nup_grid 4 = 2x2", pl.nup_grid(4, 190, 277, 210/297) == (2, 2))
check("nup_grid 16 sane", (lambda g: g[0]*g[1] == 16)(pl.nup_grid(16, 190, 277, 210/297)))
pg = [mkpage("p%d" % i, 1000, 1400) for i in range(5)]
sh, pc = pl.nup_plan(pg[:1], 2, 2)
check("README: 1 page, 2-up, 2 copies = page twice, printer copies 1", len(sh) == 1 and len(sh[0]) == 2 and pc == 1)
sh, pc = pl.nup_plan(pg[:1], 2, 4)
check("README: 1 page, 2-up, 4 copies = 2 sheets of 2", [len(s) for s in sh] == [2, 2] and pc == 1)
sh, pc = pl.nup_plan(pg[:5], 4, 3)
check("longer than a sheet: normal sets, copies for the printer", [len(s) for s in sh] == [4, 1] and pc == 3)
sh, pc = pl.nup_plan(pg[:3], 4, 2)
check("3 pages, 4-up, 2 copies: 1 per sheet copy fits once -> 2 sheets", [len(s) for s in sh] == [3, 3] and pc == 1, [len(s) for s in sh])
disp = pl.display_sheets(pl.plan(pg[:1], {"p0": 1}, 2, 4)[0])
check("display_sheets collapses identical sheets", len(disp) == 1 and disp[0][1] == 2, [(len(d[0].pages), d[1]) for d in disp])

spec = pl.NumSpec(on=True, size=14, start=5, bold=True)
nums = pl.numbering(pg, {"p1", "p2"}, True)
check("numbering keeps own place", nums == {"p0": 1, "p1": 2, "p2": 3, "p3": 4, "p4": 5}, nums)
nums2 = pl.numbering(pg, {"p1", "p3"}, False)
check("numbering serial among ticked", nums2 == {"p1": 1, "p3": 2}, nums2)
check("start offset text", spec.text(1) == "5" and spec.text(3) == "7")

# ---------- stamp position
for pos, align in (("bottom", "center"), ("top", "left"), ("bottom", "right"), ("top", "center")):
    im = Image.new("RGB", (800, 1000), "white")
    pl.stamp(im, "12", pl.NumSpec(on=True, pos=pos, align=align, size=20, margin=10), 4.0)
    bb = Image.eval(im.convert("L"), lambda v: 255 if v < 128 else 0).getbbox()
    cx = (bb[0] + bb[2]) / 2
    good_y = (bb[1] >= 38 and bb[1] < 60) if pos == "top" else (bb[3] <= 1000 - 38 and bb[3] > 940)
    good_x = {"center": abs(cx - 400) < 12, "left": abs(bb[0] - 40) < 8, "right": abs(bb[2] - 760) < 8}[align]
    check("stamp %s/%s at the right edge" % (pos, align), bb is not None and good_y and good_x, bb)

# ---------- sheet with number + nup render
opt = pl.Options("fit", 100, True, pl.NumSpec(on=True), pl.nup_grid(4, 190, 277, 210/297))
sheets, _ = pl.plan(pg[:4], pl.numbering(pg, {p.key for p in pg}, True), 4, 1)
img = pl.render_sheet(sheets[0], W, H, False, ppmm, opt, pl.Cache(2))
check("nup sheet size", img.size == (W, H))
single, _ = pl.plan(pg[:1], {"p0": 7}, 1, 1)
img1 = pl.render_sheet(single[0], W, H, False, ppmm, opt, pl.Cache(2, True))
dark = Image.eval(img1.crop((0, H - 80, W, H)).convert("L"), lambda v: 255 if v < 90 else 0).getbbox()
check("page number is on the printed page", dark is not None)

# ---------- Transform maths
it = {"id": 1, "pk": "a", "cx": 100.0, "cy": 100.0, "w": 60.0, "h": 40.0, "rot": 0.0}
r = pl.resize_item(it, "se", 190.0, 140.0, keep_aspect=False)     # pull se corner: nw fixed at (70, 80)
check("resize se keeps nw fixed", abs(r["cx"] - r["w"]/2 - 70) < 1e-9 and abs(r["cy"] - r["h"]/2 - 80) < 1e-9 and abs(r["w"] - 120) < 1e-9 and abs(r["h"] - 60) < 1e-9, r)
r = pl.resize_item(it, "se", 190.0, 140.0, keep_aspect=True)
check("resize keeps aspect", abs(r["w"]/r["h"] - 1.5) < 1e-9, r)
r = pl.resize_item(it, "e", 150.0, 999.0)
check("edge e changes width only", abs(r["h"] - 40) < 1e-9 and abs(r["w"] - 80) < 1e-9 and abs(r["cx"] - (70 + 40)) < 1e-9, r)
it_rot = dict(it, rot=30.0, cx=120.0, cy=90.0)
fixed_before = pl.item_corners(it_rot)[0]          # nw
tgt = pl.handle_points(it_rot)["se"]
r = pl.resize_item(it_rot, "se", tgt[0] + 10, tgt[1] + 5, keep_aspect=False)
fixed_after = pl.item_corners(r)[0]
check("resize of a turned picture keeps the opposite corner", math.hypot(fixed_before[0]-fixed_after[0], fixed_before[1]-fixed_after[1]) < 1e-9)
check("hit_item inside/outside turned item", pl.hit_item(it_rot, 120, 90) and not pl.hit_item(it_rot, 10, 10))
hp = pl.handle_points(it_rot)
check("handle_at finds turn handle", pl.handle_at(it_rot, hp["rot"][0], hp["rot"][1], 3) == "rot")
check("handle_at finds corner", pl.handle_at(it_rot, hp["ne"][0] + 0.5, hp["ne"][1], 3) == "ne")
check("rotation_for pointing right = 90", abs(pl.rotation_for(it, 200, 100) - 90) < 1e-9)
check("rotation_for snap 15", pl.rotation_for(it, 200, 80, snap=True) % 15 == 0)
dx, dy = pl.snap_straight(10, 1)
check("snap_straight sideways", abs(dy) < 1e-9 and dx > 9)
dx, dy = pl.snap_straight(8, 9)
check("snap_straight diagonal exact", abs(abs(dx) - abs(dy)) < 1e-9, (dx, dy))

# ---------- model: undo/redo, move to sheet, copy/paste, delete
m = pl.TransformModel()
m.lay["A"] = [m.new_item("a", 100, 100, 60, 40)]
m.lay["B"] = []
iid = m.lay["A"][0]["id"]
m.select([iid])
m.push(("extra0",))
m.move_to_sheet(iid, "B", 50, 60)
check("move_to_sheet", m.lay["A"] == [] and len(m.lay["B"]) == 1 and m.lay["B"][0]["cx"] == 50)
res = m.undo(("extra1",))
check("undo restores + returns extra", res == (("extra0",),) and len(m.lay["A"]) == 1 and m.lay["B"] == [] and m.sel == [iid], res)
res = m.redo(("extra0",))
check("redo", res is not None and len(m.lay["B"]) == 1 and m.lay["A"] == [])
check("undo with empty history -> None", pl.TransformModel().undo() is None)
m.select([iid]); n = m.copy_selected(); new = m.paste("A")
check("copy + paste", n == 1 and len(new) == 1 and len(m.lay["A"]) == 1 and m.lay["A"][0]["cx"] == 56.0 and m.sel == new)
dup = m.duplicate([iid], 3, 4)
check("duplicate on its own sheet", len(dup) == 1 and len(m.lay["B"]) == 2)
m.select([iid] + dup); check("delete selected", m.delete_selected() == 2 and len(m.lay["B"]) == 0)
m2 = pl.TransformModel(); m2.lay["A"] = [m2.new_item("a", 50, 50, 40, 40), m2.new_item("a", 60, 60, 40, 40)]
check("hit returns top-most", m2.hit("A", 55, 55) == m2.lay["A"][1]["id"])
check("rubber band", set(m2.in_rect("A", 0, 0, 40, 40)) == {m2.lay["A"][0]["id"], m2.lay["A"][1]["id"]})
m2.edited.add("A"); m2.lay["Z"] = []; m2.forget_unedited()
check("forget_unedited keeps edited only", "A" in m2.lay and "Z" not in m2.lay)

# ---------- transform render
items = [dict(m2.lay["A"][0], pk="p0", cx=60, cy=80, w=80, h=100, rot=0), dict(m2.lay["A"][1], pk="p1", cx=140, cy=200, w=80, h=100, rot=45)]
tsheet = pl.Sheet("page", [pg[0]], [1], key="p0", items=items)
imt = pl.render_sheet(tsheet, W, H, False, ppmm, pl.Options("fit", 100, True, pl.NumSpec(on=False), pages={"p0": pg[0], "p1": pg[1]}), pl.Cache(2, True))
check("transform render makes a sheet with ink at both pictures", imt.getpixel((int(60*ppmm), int(80*ppmm))) != (255,255,255) and imt.getpixel((int(140*ppmm), int(200*ppmm))) != (255,255,255))
check("sequence: odd sheets two-sided get blank back", pl.sequence(3, 2, True) == [0, 1, 2, None, 0, 1, 2] and pl.sequence(2, 2, False) == [0, 1, 0, 1])

# ---------- numbered PDF
def pdf_page(w, h, mode):
    PS = {"a4": (595.276, 841.89), "letter": (612.0, 792.0)}
    if mode in PS:
        pw, ph = PS[mode]
        if w > h: pw, ph = ph, pw
        sc = min(pw / w, ph / h); dw, dh = w * sc, h * sc
        return pw, ph, (pw - dw) / 2, (ph - dh) / 2, dw, dh
    long = 841.89; sc = long / max(w, h)
    return w * sc, h * sc, 0.0, 0.0, w * sc, h * sc
out = os.path.join(tempfile.mkdtemp(), "n.pdf")
srcs = [pic(900, 1200), lambda: pic(1200, 900, (30, 30, 200)), pic(900, 900)]
pl.save_pdf_numbered(srcs, out, "a4", pl.NumSpec(on=True, pos="bottom", align="right", size=16, start=10, bold=True), pdf_page=pdf_page)
import pypdfium2 as pdfium
doc = pdfium.PdfDocument(out)
check("numbered pdf has 3 pages", len(doc) == 3)
txt = [doc[i].get_textpage().get_text_range().strip() for i in range(3)]
check("numbered pdf text = 10, 11, 12", txt == ["10", "11", "12"], txt)
w0, h0 = doc[0].get_size()
check("a4 portrait / landscape pages", abs(w0 - 595.276) < 0.01 and abs(doc[1].get_size()[0] - 841.89) < 0.01, (w0, doc[1].get_size()))
bm = doc[0].render(scale=1).to_pil().convert("L")
bb = Image.eval(bm, lambda v: 255 if v < 100 else 0).crop((0, int(h0 - 60), int(w0), int(h0))).getbbox()
check("number visible near the bottom right", bb is not None and bb[2] > w0 * 0.8, bb)
out2 = os.path.join(os.path.dirname(out), "n2.pdf")
pl.save_pdf_numbered([pic(900, 1200)], out2, "fit", pl.NumSpec(on=True, pos="top", align="left", size=20), pdf_page=pdf_page)
d2 = pdfium.PdfDocument(out2); t2 = d2[0].get_textpage().get_text_range().strip()
check("top-left number is '1' and pdf opens", t2 == "1", t2)
out3 = os.path.join(os.path.dirname(out), "n3.pdf")
pl.save_pdf_numbered([pic(900, 1200)], out3, "fit", pl.NumSpec(on=False), pdf_page=pdf_page)
check("numbers off = no text", pdfium.PdfDocument(out3)[0].get_textpage().get_text_range().strip() == "")

# ---------- page range (PrintMerged: "1-5, 8, 10-12")
r, bad, over = pl.parse_range("1-3, 5; 8 10\u201312", 12)
check("range parsed", r == {1, 2, 3, 5, 8, 10, 11, 12} and not bad and not over, (r, bad, over))
r, bad, over = pl.parse_range("3-1, x7, 9-20", 10)
check("range: reversed ok, junk reported, too big flagged", r == {1, 2, 3, 9, 10} and bad == ["x7"] and over, (r, bad, over))
check("range compress", pl.compress_range([1, 2, 3, 5, 8, 9, 12]) == "1-3, 5, 8, 9, 12", pl.compress_range([1, 2, 3, 5, 8, 9, 12]))
check("range round trip", pl.parse_range(pl.compress_range([2, 3, 4, 7]), 9)[0] == {2, 3, 4, 7})

# ---------- perspective crop
src = Image.new("RGB", (400, 200), (255, 255, 255))
ImageDraw.Draw(src).rectangle((100, 50, 299, 149), fill=(10, 20, 200))
rect = pl.crop_image(src, [0.25, 0.25, 0.75, 0.25, 0.75, 0.75, 0.25, 0.75])
check("straight crop = exact pixels", rect.size == (200, 100) and rect.getpixel((5, 5)) == (10, 20, 200), rect.size)
slant = pl.crop_image(src, [0.30, 0.20, 0.80, 0.30, 0.75, 0.80, 0.25, 0.70])
check("slanted crop gives an upright picture", slant.size[0] > 150 and slant.size[1] > 80, slant.size)
check("quad_valid: full yes, bow-tie no, tiny no", pl.quad_valid(pl.full_quad())
      and not pl.quad_valid([0, 0, 1, 1, 1, 0, 0, 1]) and not pl.quad_valid([0.5, 0.5, 0.505, 0.5, 0.505, 0.505, 0.5, 0.505]))
check("quad_is_full", pl.quad_is_full(pl.full_quad()) and not pl.quad_is_full([0.1, 0, 1, 0, 1, 1, 0, 1]))
base = mkpage("b", 4000, 2000)
cp = pl.make_crop_page("b#1", base, [0.25, 0.25, 0.75, 0.25, 0.75, 0.75, 0.25, 0.75])
check("crop page size known before it is loaded", cp.size == (2000, 1000) and abs(cp.aspect - 2.0) < 1e-6, cp.size)
check("crop page loads the cut-out", cp.load().size == (2000, 1000), cp.load().size)

# ---------- page turn (R key)
tp = mkpage("t", 300, 200)
tp.turn_by(90)
check("turn_by: preview + size turned, full picture turned too", tp.size == (200, 300) and tp.preview.size == (200, 300) and tp.load().size == (200, 300), (tp.size, tp.preview.size))
tp.turn_by(-90)
check("turn back", tp.size == (300, 200) and tp.load().size == (300, 200) and tp.turn == 0)

# ---------- Fit / Center / numeric fields
m = pl.TransformModel()
a = m.new_item("p", 10, 10, 400, 200, 0); b = m.new_item("p", 50, 60, 100, 100, 45)
m.lay["p"] = [a, b]
check("fit one picture inside the area", pl.fit_group([a], 190, 277) and a["w"] <= 190.01 and abs(a["cx"] - 95) < 1e-6 and abs(a["cy"] - 138.5) < 1e-6, a)
c = m.new_item("p", 0, 0, 300, 100, 90)
pl.fit_group([c], 190, 277)
bx = pl.item_bbox(c)
check("fit a turned picture: its outline is inside the area", bx[0] >= -1e-6 and bx[2] <= 190 + 1e-6 and bx[1] >= -1e-6 and bx[3] <= 277 + 1e-6, bx)
g1 = m.new_item("p", 20, 20, 40, 40, 0); g2 = m.new_item("p", 180, 20, 40, 40, 0)
pl.fit_group([g1, g2], 100, 100)
check("group keeps its shape (same factor)", abs(g1["w"] - g2["w"]) < 1e-9 and abs((g2["cx"] - g1["cx"]) - 160 * g1["w"] / 40.0) < 1e-6)
d = m.new_item("p", 50, 50, 100, 50, 0)
m.set_fields(d, "p", w=200, lock=True)
check("W with lock keeps the shape", abs(d["w"] - 200) < 1e-9 and abs(d["h"] - 100) < 1e-9, d)
m.set_fields(d, "p", h=50, lock=False)
check("H without lock changes only H", abs(d["w"] - 200) < 1e-9 and abs(d["h"] - 50) < 1e-9, d)
m.set_fields(d, "p", x=12, y=34, rot=-90)
check("X / Y / turn", d["cx"] == 12 and d["cy"] == 34 and d["rot"] == 270 and "p" in m.edited, d)
check("units", pl.unit_text(25.4, "in") == "1" and pl.unit_text(123.45, "cm") == "12.35" and abs(pl.from_unit("1,5", "cm") - 15) < 1e-9 and pl.from_unit("x", "mm") is None)
m.select_sheet("p")
check("select all on a sheet", len(m.sel) == len(m.lay["p"]))
print("\n%d passed, %d failed" % (ok, fail))
sys.exit(1 if fail else 0)
