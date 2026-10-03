"""Face-anchored skin (opt-in, `match --face-skin`).

The colour-only skin model (engine/skin.py) also picks up sand, dry grass,
gold fabric and cream outfits: on the beach reference DSC00138 a quarter of
the frame read as "skin". Here skin is measured only inside faces:

1. find faces with OpenCV's bundled Haar detector (optional dependency,
   `pip install -r requirements-faces.txt`);
2. keep a box only when its middle looks like skin of some kind (drops
   flowers, white chairs and other false finds);
3. sample the colour of the middle of each face, and keep the pixels of the
   (slightly enlarged) box that are close to that sample in a*b* (Mahalanobis
   distance), so hair, eyes, teeth, jewellery and the background drop out.

No face found means no skin term for that render. Without OpenCV the engine
falls back to the colour-only skin model and says so once.
"""

from contextlib import contextmanager
from functools import lru_cache

import numpy as np
from PIL import Image

from .colorspace import srgb_to_lab

DETECT_EDGE = 1024  # long edge the detector looks at (small faces need the pixels)
MIN_FACE = 0.03  # smallest face, as a fraction of the image's short edge
MAX_FACE = 0.75  # a close portrait's face spans most of the short edge; whole bodies are bigger
CORE = (0.5, 0.6)  # middle of the box (width, height) used to sample the face's colour
EXPAND = 1.15  # skin pixels are taken from the box enlarged by this
MAX_DISTANCE = 3.0  # Mahalanobis distance in a*b* beyond which a pixel isn't this face's skin
MIN_SKIN_LIKE = 0.35  # share of the box middle that must look like some skin tone
MIN_CORE_PIXELS = 12
NMS_OVERLAP = 0.3

_state = {"on": False, "warned": False}


@contextmanager
def enabled(on=True):
    """Measure skin from faces (when on) for everything measured inside the block."""
    before = _state["on"]
    _state["on"], _state["warned"] = bool(on), False
    try:
        yield
    finally:
        _state["on"] = before


def active():
    return _state["on"]


@lru_cache(maxsize=1)
def _detectors():
    try:
        import cv2
    except ImportError:
        return None
    if not hasattr(cv2, "CascadeClassifier") or not hasattr(cv2, "data"):
        return None  # OpenCV 5 dropped the bundled Haar models
    out = []
    for name in ("haarcascade_frontalface_alt2.xml", "haarcascade_profileface.xml"):
        c = cv2.CascadeClassifier(cv2.data.haarcascades + name)
        if not c.empty():
            out.append(c)
    return (cv2, out) if out else None


def available():
    return _detectors() is not None


def fallback_warning():
    """A one-time note when faces were asked for but OpenCV isn't installed."""
    if _state["on"] and not available() and not _state["warned"]:
        _state["warned"] = True
        return ("Face skin was asked for but OpenCV isn't installed, so skin is found by colour as before. "
                "Install it with: pip install -r requirements-faces.txt")
    return None


def _skin_like(lab):
    """Broad 'could be some skin tone under some light' test (any skin, any cast within reason)."""
    L, a, b = lab[..., 0], lab[..., 1], lab[..., 2]
    hue = np.degrees(np.arctan2(b, a))
    chroma = np.hypot(a, b)
    return (L > 12) & (L < 95) & (hue > 0) & (hue < 110) & (chroma > 5) & (chroma < 80)


def _core(box, shape):
    h, w = shape[:2]
    x0, y0, x1, y1 = box
    cx, cy = (x0 + x1) / 2 * w, (y0 + y1) / 2 * h
    hw, hh = (x1 - x0) * w * CORE[0] / 2, (y1 - y0) * h * CORE[1] / 2
    return (slice(max(int(cy - hh), 0), max(int(cy + hh), 1)), slice(max(int(cx - hw), 0), max(int(cx + hw), 1)))


def _overlap(a, b):
    ix = max(0.0, min(a[2], b[2]) - max(a[0], b[0]))
    iy = max(0.0, min(a[3], b[3]) - max(a[1], b[1]))
    inter = ix * iy
    smaller = min((a[2] - a[0]) * (a[3] - a[1]), (b[2] - b[0]) * (b[3] - b[1]))
    return inter / smaller if smaller > 0 else 0.0


def detect(img):
    """Face boxes in an sRGB image (float in [0, 1] or uint8, HxWx3), as
    (x0, y0, x1, y1) fractions of the width and height. None without OpenCV."""
    found = _detectors()
    if found is None:
        return None
    cv2, cascades = found
    img = np.asarray(img)
    if img.dtype != np.uint8:
        img = (np.clip(img, 0, 1) * 255).astype(np.uint8)
    h, w = img.shape[:2]
    gray = cv2.equalizeHist(cv2.cvtColor(img, cv2.COLOR_RGB2GRAY))
    short = min(h, w)
    lo, hi = max(int(MIN_FACE * short), 12), int(MAX_FACE * short)
    raw = []
    for c in cascades:
        for flip in (False, True) if c is not cascades[0] else (False,):
            g = gray[:, ::-1] if flip else gray  # the profile model only knows one side
            for x, y, bw, bh in c.detectMultiScale(np.ascontiguousarray(g), 1.1, 4, minSize=(lo, lo), maxSize=(hi, hi)):
                if flip:
                    x = w - x - bw
                raw.append((x / w, y / h, (x + bw) / w, (y + bh) / h))
    lab = srgb_to_lab(img.astype(np.float64) / 255.0)
    boxes = []
    for box in sorted(raw, key=lambda b: -(b[2] - b[0]) * (b[3] - b[1])):
        if any(_overlap(box, k) > NMS_OVERLAP for k in boxes):
            continue
        core = lab[_core(box, lab.shape)]
        if core.size and _skin_like(core).mean() >= MIN_SKIN_LIKE:
            boxes.append(box)
    return boxes


def detect_file(path):
    with Image.open(path) as im:
        im = im.convert("RGB")
        im.thumbnail((DETECT_EDGE, DETECT_EDGE))
        return detect(np.asarray(im))


def weights(lab, boxes):
    """Skin membership (0..1) per pixel of a Lab image (HxWx3): this face's own
    colour, inside its (enlarged) box. All zeros when there are no boxes."""
    h, w = lab.shape[:2]
    out = np.zeros((h, w))
    for box in boxes or ():
        core = lab[_core(box, lab.shape)].reshape(-1, 3)
        # The box is already a face: any coloured, not clipped pixel of its middle
        # counts (a strong cast can push skin outside the usual skin colours).
        core = core[(core[:, 0] > 12) & (core[:, 0] < 95) & (np.hypot(core[:, 1], core[:, 2]) > 3)]
        if len(core) < MIN_CORE_PIXELS:
            continue
        ab = core[:, 1:]
        mean = np.median(ab, axis=0)
        for _ in range(2):  # robust: refit on the pixels near the face's colour
            cov = np.cov(ab.T) + np.eye(2)
            d = _mahalanobis(ab, mean, cov)
            keep = ab[d <= 2.0]
            if len(keep) < MIN_CORE_PIXELS:
                break
            ab, mean = keep, keep.mean(axis=0)
        cov = np.cov(ab.T) + np.eye(2) if len(ab) >= 3 else np.eye(2) * 4.0
        x0, y0, x1, y1 = box
        cx, cy, hw, hh = (x0 + x1) / 2, (y0 + y1) / 2, (x1 - x0) * EXPAND / 2, (y1 - y0) * EXPAND / 2
        rows = slice(max(int((cy - hh) * h), 0), min(int(np.ceil((cy + hh) * h)), h))
        cols = slice(max(int((cx - hw) * w), 0), min(int(np.ceil((cx + hw) * w)), w))
        region = lab[rows, cols]
        d = _mahalanobis(region[..., 1:].reshape(-1, 2), mean, cov).reshape(region.shape[:2])
        wt = np.exp(-0.5 * d**2) * (d <= MAX_DISTANCE) * (region[..., 0] > 8) * (region[..., 0] < 97)
        out[rows, cols] = np.maximum(out[rows, cols], wt)
    return out


def _mahalanobis(x, mean, cov):
    diff = x - mean
    return np.sqrt(np.einsum("ij,jk,ik->i", diff, np.linalg.inv(cov), diff).clip(min=0))
