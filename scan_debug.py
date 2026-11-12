"""Run the page finder on one photo and show what it did - to send / look at when Auto crop is wrong.

    python scan_debug.py photo.jpg            ->  photo_debug.png  (and prints the timing + scores)

Red = the corners Auto crop would use.  Thin yellow = the next best outlines the fast finder considered.
Blue = the cropped + straightened page.
"""
import sys
import time

from PIL import Image, ImageDraw, ImageOps

import scanner


def main(path):
    img = ImageOps.exif_transpose(Image.open(path)).convert("RGB")
    print("picture: %d x %d" % img.size)
    dbg = []
    t = time.time()
    fast = None
    if scanner.cv2 is not None:
        fast = scanner._detect_fast(img, want_debug=dbg)
    t_fast = time.time() - t
    print("fast finder : %.2f s  ->  %s" % (t_fast, "nothing" if fast is None else "score %.2f, edge %.2f" % (fast[2]["score"], fast[2]["edge"])))
    t = time.time()
    quad, found, info = scanner.detect_document(img, return_score=True)
    print("Auto crop   : %.2f s  ->  found=%s  score %.2f  edge %.2f  (%s)" % (time.time() - t, found, info["score"], info["edge"], info.get("engine", "classic")))
    over = img.copy()
    d = ImageDraw.Draw(over)
    lw = max(2, max(img.size) // 400)
    for v, tag, q in dbg[1:10]:
        d.line([tuple(p) for p in q] + [tuple(q[0])], fill=(255, 220, 0), width=max(1, lw // 2))
    d.line([tuple(p) for p in quad] + [tuple(quad[0])], fill=(255, 0, 0), width=lw)
    for p in quad:
        d.ellipse((p[0] - 3 * lw, p[1] - 3 * lw, p[0] + 3 * lw, p[1] + 3 * lw), outline=(255, 0, 0), width=lw)
    page = scanner.straighten(img, quad)
    sc = over.height / float(page.height)
    page = page.resize((max(1, int(page.width * sc)), over.height))
    sheet = Image.new("RGB", (over.width + page.width + 20, over.height), (30, 30, 30))
    sheet.paste(over, (0, 0))
    sheet.paste(page, (over.width + 20, 0))
    out = path.rsplit(".", 1)[0] + "_debug.png"
    sheet.thumbnail((2400, 1400))
    try:
        sheet.save(out)
    except OSError:                                    # the photo's folder is read-only: save in the current folder instead
        out = out.replace("\\", "/").rsplit("/", 1)[-1]
        sheet.save(out)
    print("saved", out)


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print(__doc__)
    else:
        main(sys.argv[1])
