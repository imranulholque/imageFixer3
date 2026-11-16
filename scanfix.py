"""Sharper scans.  A flatbed / ADF scan is often soft (scanner lens, driver smoothing, 300 dpi).  This cannot invent detail that
the scanner never saw, but it gives clearly crisper text, lines and faces:

  1. a light noise removal on small pictures (an ID card), so the next steps do not make the noise bigger,
  2. enlarging with Lanczos up to about 600 dpi (more pixels = sharper print and a better crop / zoom later),
  3. sharpening of the BRIGHTNESS only (no coloured fringes), with a threshold so flat paper stays clean.

Pure PIL / numpy (+ OpenCV when present): no window, can be tested without a scanner.
Speed: with OpenCV the enlarging and the sharpening run on all CPU cores (about 5 - 10 times faster than PIL), and a whole
A4 page is not enlarged to 35 million pixels any more (MAX_PIXELS): that was the slow part ("Making the picture sharper").
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
DENOISE_MAX_PIXELS = 2_000_000
MAX_PIXELS = 16_000_000            # the enlarged picture never has more pixels than this (A4 at about 400 dpi)


def _denoise(img):
    if cv2 is None or img.width * img.height > DENOISE_MAX_PIXELS:
        return img
    try:
        a = np.asarray(img.convert("RGB"))
        out = cv2.fastNlMeansDenoisingColored(a, None, 2.5, 2.5, 5, 15)
        return Image.fromarray(out)
    except Exception:
        return img


def _sharpen_luma_cv(img, radius, percent, threshold=2):
    """Same as _sharpen_luma (unsharp mask on the brightness only, flat areas below `threshold` stay untouched), but OpenCV."""
    ycc = cv2.cvtColor(np.asarray(img.convert("RGB")), cv2.COLOR_RGB2YCrCb)
    y = ycc[:, :, 0]
    blur = cv2.GaussianBlur(y, (0, 0), radius)
    k = percent / 100.0
    sharp = cv2.addWeighted(y, 1.0 + k, blur, -k, 0)            # y + k * (y - blur), saturated to 0..255
    flat = cv2.absdiff(y, blur) < threshold
    ycc[:, :, 0] = np.where(flat, y, sharp)
    return Image.fromarray(cv2.cvtColor(ycc, cv2.COLOR_YCrCb2RGB))


def _resize(img, size):
    if cv2 is not None:
        try:
            out = cv2.resize(np.asarray(img), size, interpolation=cv2.INTER_LANCZOS4)
            return Image.fromarray(out)
        except Exception:
            pass
    return img.resize(size, Image.Resampling.LANCZOS)


def _sharpen_luma(img, radius, percent, threshold=2):
    if cv2 is not None:
        try:
            return _sharpen_luma_cv(img, radius, percent, threshold)
        except Exception:
            pass
    y, cb, cr = img.convert("YCbCr").split()
    y = y.filter(ImageFilter.UnsharpMask(radius=radius, percent=percent, threshold=threshold))
    return Image.merge("YCbCr", (y, cb, cr)).convert("RGB")


def factor_for(size, dpi, mode):
    """How much bigger the picture gets: up to 600 dpi, at most 2x, never over MAX_SIDE / MAX_PIXELS."""
    if mode != BEST or not dpi:
        return 1.0
    f = min(2.0, max(1.0, TARGET_DPI / float(dpi)))
    f = min(f, MAX_SIDE / float(max(size)))
    f = min(f, (MAX_PIXELS / float(max(1, size[0] * size[1]))) ** 0.5)
    return f if f >= 1.05 else 1.0


def enhance(img, dpi=300, mode=BEST, progress=None):
    """-> (picture, effective dpi).  mode: OFF / SHARPEN (sharpen only) / BEST (denoise + enlarge + sharpen).
    progress(fraction 0..1, text) is called between the steps (it is only for a progress bar)."""
    def tell(f, t=""):
        if progress is not None:
            try:
                progress(f, t)
            except Exception:
                pass
    img = img.convert("RGB")
    if mode == OFF:
        return img, dpi
    if mode == BEST:
        tell(0.02, "cleaning")
        img = _denoise(img)
    f = factor_for(img.size, dpi, mode)
    if f > 1.0:
        tell(0.30, "enlarging")
        img = _resize(img, (max(1, int(round(img.width * f))), max(1, int(round(img.height * f)))))
    eff = dpi * f
    # the blur of the scanner / resize grows with the factor, so the sharpening radius grows with it
    radius = 0.8 + 0.45 * f
    percent = 70 if f == 1.0 else 95
    tell(0.65, "sharpening")
    out = _sharpen_luma(img, radius, percent)
    tell(1.0, "")
    return out, eff
