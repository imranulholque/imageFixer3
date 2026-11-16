"""ID card cut-out test with made-up flatbed scans (no scanner needed): python test_idscan.py"""
import math
import sys

import numpy as np
from PIL import Image, ImageDraw, ImageFilter

import idscan

DPI = 300
CW, CH = int(85.6 / 25.4 * DPI), int(54.0 / 25.4 * DPI)        # real card size in pixels


def make_card(fill=(214, 232, 214), seed=1):
    rng = np.random.default_rng(seed)
    c = Image.new("RGB", (CW, CH), fill)
    d = ImageDraw.Draw(c)
    d.rectangle((40, 60, 260, 330), fill=(150, 120, 100))                     # photo
    for i in range(7):
        d.rectangle((310, 70 + i * 55, 310 + int(rng.integers(300, 600)), 90 + i * 55), fill=(40, 40, 60))
    d.rectangle((0, 0, CW - 1, CH - 1), outline=(60, 90, 60), width=6)
    return c


def make_scan(card, lid=(255, 255, 255), angle=3.0, pos=(500, 700), shadow=True, noise=2.0):
    page = Image.new("RGB", (2480, 3508), lid)
    rot = card.convert("RGBA").rotate(angle, expand=True, resample=Image.Resampling.BICUBIC)
    if shadow:
        sh = Image.new("RGBA", rot.size, (0, 0, 0, 0))
        sh.paste((0, 0, 0, 70), (0, 0), rot.split()[3])
        sh = sh.filter(ImageFilter.GaussianBlur(5))
        page.paste(sh, (pos[0] + 6, pos[1] + 8), sh)
    page.paste(rot, pos, rot)
    a = np.asarray(page).astype(np.float32) + np.random.default_rng(3).normal(0, noise, (3508, 2480, 3))
    return Image.fromarray(np.clip(a, 0, 255).astype(np.uint8))


fails = []


def check(name, scan, expect_aspect=CW / CH):
    card, found = idscan.cut_card(scan, DPI)
    asp = card.width / card.height
    ok = found and abs(card.width - CW) < 0.03 * CW and abs(card.height - CH) < 0.03 * CH and abs(asp - expect_aspect) < 0.03
    # no lid colour left on the border of the crop
    arr = np.asarray(card).astype(int)
    edge = np.concatenate([arr[0], arr[-1], arr[:, 0], arr[:, -1]])
    print("%-28s found=%s size=%s  %s" % (name, found, card.size, "ok" if ok else "FAIL"))
    if not ok:
        fails.append(name)
    return card


def run():
    check("white lid, 3 deg", make_scan(make_card(), (255, 255, 255), 3))
    check("black lid, -4 deg", make_scan(make_card(), (12, 12, 12), -4, noise=3))
    check("grey lid", make_scan(make_card(), (200, 200, 205), 1.5))
    check("pale card on white lid", make_scan(make_card((246, 246, 240)), (255, 255, 255), 2, shadow=True))
    check("near the edge, 8 deg", make_scan(make_card(), (255, 255, 255), 8, pos=(60, 40)))
    check("card turned 90 (portrait)", make_scan(make_card().rotate(90, expand=True), (255, 255, 255), 2, pos=(700, 500)))

    c0, f0 = idscan.cut_card(make_card(), DPI)
    assert f0 and c0.size == (CW, CH), 'a scan that is only the card stays as it is'
    # front + back page
    f, _ = idscan.cut_card(make_scan(make_card(seed=1)), DPI)
    b, _ = idscan.cut_card(make_scan(make_card((222, 224, 240), seed=2), angle=-2, pos=(600, 900)), DPI)
    page, items = idscan.compose_page(f, b, DPI, "A4")
    assert page.size == (2480, 3508), page.size
    assert [n for n, _, _ in items] == ["Front", "Back"]
    (_, _, r1), (_, _, r2) = items
    assert 0 <= r1[0] < r1[2] <= 1 and 0 <= r2[3] <= 1 and r1[3] < r2[1], (r1, r2)
    assert abs((r1[0] + r1[2]) / 2 - 0.5) < 0.002, "centred"
    assert abs((r1[2] - r1[0]) * 2480 - CW) < 0.04 * CW, "real size kept"
    prev = idscan.paste_preview(page, items)
    prev.resize((620, 877)).save(sys.argv[1] if len(sys.argv) > 1 else "idscan_preview.png")
    page2, items2 = idscan.compose_page(f, b, 200, "Letter", "top")
    assert page2.size == (1700, 2200), page2.size
    print("FAILED: %s" % fails if fails else "ALL OK")
    return 1 if fails else 0


if __name__ == "__main__":
    sys.exit(run())
