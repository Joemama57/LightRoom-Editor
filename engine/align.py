"""Find where an exported, cropped copy sits inside its original.

An exported reference ("IMG_1964 copy.jpg") is often a crop of the original,
re-graded and resized. To compare them pixel by pixel, the crop box has to be
found first. Edges survive a colour grade, so both images are reduced to
high-passed grayscale and matched by normalised cross-correlation over crop
sizes and positions.
"""

import numpy as np
from PIL import Image

WORK_WIDTH = 256  # the original is searched at this width
MIN_SCORE = 0.7


def _gray(img):
    img = np.asarray(img, dtype=np.float64)
    return img[..., 0] * 0.299 + img[..., 1] * 0.587 + img[..., 2] * 0.114


def _resize(gray, w, h):
    im = Image.fromarray((np.clip(gray, 0, 1) * 255).astype(np.uint8))
    return np.asarray(im.resize((max(1, int(round(w))), max(1, int(round(h)))), Image.BILINEAR), float) / 255


def _box_blur(a, r):
    k = 2 * r + 1
    c = np.cumsum(np.cumsum(np.pad(a, ((r + 1, r), (r + 1, r)), mode="edge"), 0), 1)
    return (c[k:, k:] - c[:-k, k:] - c[k:, :-k] + c[:-k, :-k]) / (k * k)


def _highpass(gray):
    hp = gray - _box_blur(gray, 3)
    return (hp - hp.mean()) / (hp.std() + 1e-9)


def _ncc(image, template):
    """Normalised cross-correlation of template over image ('valid' positions)."""
    H, W = image.shape
    h, w = template.shape
    t = template - template.mean()
    t_norm = np.sqrt((t ** 2).sum()) + 1e-9
    shape = (H + h, W + w)
    F = np.fft.rfft2(image, shape)
    corr = np.fft.irfft2(F * np.conj(np.fft.rfft2(t, shape)), shape)[: H - h + 1, : W - w + 1]
    c1 = np.cumsum(np.cumsum(np.pad(image, ((1, 0), (1, 0))), 0), 1)
    c2 = np.cumsum(np.cumsum(np.pad(image ** 2, ((1, 0), (1, 0))), 0), 1)

    def window(c):
        return c[h:, w:] - c[:-h, w:] - c[h:, :-w] + c[:-h, :-w]

    n = h * w
    s1, s2 = window(c1), window(c2)
    var = np.maximum(s2 - s1 ** 2 / n, 1e-9)
    return corr / (t_norm * np.sqrt(var))


def locate(original, copy, min_score=MIN_SCORE):
    """Box (x, y, w, h as fractions of the original) where `copy` sits in
    `original`, and the match score; or None if it can't be found."""
    og = _gray(original)
    W0 = WORK_WIDTH
    H0 = og.shape[0] * W0 / og.shape[1]
    image = _highpass(_resize(og, W0, H0))
    cg = _gray(copy)
    aspect = cg.shape[1] / cg.shape[0]

    def score_at(frac):
        cw = frac * W0
        ch = cw / aspect
        if ch > image.shape[0] + 0.5 or cw < 24 or ch < 24:
            return -1.0, None
        t = _highpass(_resize(cg, cw, ch))
        t = t[: image.shape[0], : image.shape[1]]
        ncc = _ncc(image, t)
        y, x = np.unravel_index(np.argmax(ncc), ncc.shape)
        return float(ncc[y, x]), (x / W0, y / image.shape[0], t.shape[1] / W0, t.shape[0] / image.shape[0])

    best = (-1.0, None, None)
    for frac in np.arange(0.40, 1.0001, 0.02):
        s, box = score_at(frac)
        if s > best[0]:
            best = (s, box, frac)
    if best[1] is None:
        return None
    for frac in np.arange(best[2] - 0.02, best[2] + 0.0201, 0.004):
        if 0.3 <= frac <= 1.0:
            s, box = score_at(frac)
            if s > best[0]:
                best = (s, box, frac)
    score, box, _ = best
    if score < min_score:
        return None
    return {"box": [round(float(v), 4) for v in box], "score": round(float(score), 3)}


def crop(img, box, size=None):
    """Crop a float image to a fractional box, optionally resized to (w, h)."""
    H, W = img.shape[:2]
    x, y, w, h = box
    x0, y0 = int(round(x * W)), int(round(y * H))
    x1, y1 = min(W, int(round((x + w) * W))), min(H, int(round((y + h) * H)))
    out = img[y0:y1, x0:x1]
    if size is not None and (out.shape[1], out.shape[0]) != tuple(size):
        im = Image.fromarray((np.clip(out, 0, 1) * 255).round().astype(np.uint8))
        out = np.asarray(im.resize(tuple(size), Image.LANCZOS), float) / 255
    return out
