"""Sharper scans.  A flatbed / ADF scan is often soft (scanner lens, driver smoothing, 300 dpi).  This cannot invent detail that
the scanner never saw, but it gives clearly crisper text, lines and faces:

  1. a light noise removal on small pictures (an ID card), so the next steps do not make the noise bigger,
  2. enlarging with Lanczos up to about 600 dpi (more pixels = sharper print and a better crop / zoom later),
  3. sharpening of the BRIGHTNESS only (no coloured fringes), with a threshold so flat paper stays clean.

Pure PIL / numpy (+ OpenCV when present): no window, can be tested without a scanner.
The best result is still a higher scan dpi (600 for an ID card)."""
from PIL import Image, ImageFilter

try:
    import cv2
    import numpy as np
except Exception:                                   # OpenCV is optional here
    cv2 = None
    np = None

OFF, SHARPEN, BEST = "off", "sharpen", "best"
TARGET_DPI = 600
MAX_SIDE = 12000
DENOISE_MAX_PIXELS = 3_000_000


def _denoise(img):
    if cv2 is None or img.width * img.height > DENOISE_MAX_PIXELS:
        return img
    try:
        a = np.asarray(img.convert("RGB"))
        out = cv2.fastNlMeansDenoisingColored(a, None, 2.5, 2.5, 5, 15)
        return Image.fromarray(out)
    except Exception:
        return img


def _sharpen_luma(img, radius, percent, threshold=2):
    y, cb, cr = img.convert("YCbCr").split()
    y = y.filter(ImageFilter.UnsharpMask(radius=radius, percent=percent, threshold=threshold))
    return Image.merge("YCbCr", (y, cb, cr)).convert("RGB")


def factor_for(size, dpi, mode):
    """How much bigger the picture gets: up to 600 dpi, at most 2x, never over MAX_SIDE."""
    if mode != BEST or not dpi:
        return 1.0
    f = min(2.0, max(1.0, TARGET_DPI / float(dpi)))
    f = min(f, MAX_SIDE / float(max(size)))
    return f if f >= 1.05 else 1.0


def enhance(img, dpi=300, mode=BEST):
    """-> (picture, effective dpi).  mode: OFF / SHARPEN (sharpen only) / BEST (denoise + enlarge + sharpen)."""
    img = img.convert("RGB")
    if mode == OFF:
        return img, dpi
    if mode == BEST:
        img = _denoise(img)
    f = factor_for(img.size, dpi, mode)
    if f > 1.0:
        img = img.resize((max(1, int(round(img.width * f))), max(1, int(round(img.height * f)))), Image.Resampling.LANCZOS)
    eff = dpi * f
    # the blur of the scanner / resize grows with the factor, so the sharpening radius grows with it
    radius = 0.8 + 0.45 * f
    percent = 70 if f == 1.0 else 95
    return _sharpen_luma(img, radius, percent), eff
