"""Measure the color balance and tone signature of a rendered preview."""

from dataclasses import asdict, dataclass

import numpy as np
from PIL import Image

from . import skin as skin_model
from .colorspace import srgb_to_lab

PERCENTILES = (1, 25, 50, 75, 99)
MAX_EDGE = 512
CLIP_LOW = 0.01
CLIP_HIGH = 0.99
# Pixels used for the neutral estimate: mid-tones close to the frame's average cast.
NEUTRAL_L_RANGE = (20.0, 85.0)
NEUTRAL_RADII = (30.0, 20.0, 14.0)  # successive a*b* radii around the cast estimate
# Tighter radii when we know roughly where the neutrals should be (the reference's
# neutral axis): any remaining cast is small, and saturated content stays out.
HINT_RADII = (15.0, 12.0, 10.0)
MIN_NEUTRAL_FRACTION = 0.02


@dataclass
class Metrics:
    a: float  # neutral axis, green(-) / magenta(+)
    b: float  # neutral axis, blue(-) / yellow(+)
    L: dict  # L* percentiles, keyed "p1", "p25", ...
    clipped_fraction: float
    neutral_fraction: float
    skin_a: float = None  # membership-weighted mean Lab of skin-tone pixels (None when there are none)
    skin_b: float = None
    skin_fraction: float = 0.0
    skin_L: float = None

    def to_dict(self):
        return asdict(self)

    @classmethod
    def from_dict(cls, d):
        return cls(**d)


def load_image(path):
    """Load an image as float sRGB in [0, 1], shape (H, W, 3)."""
    with Image.open(path) as im:
        im = im.convert("RGB")
        im.thumbnail((MAX_EDGE, MAX_EDGE))
        return np.asarray(im, dtype=np.float64) / 255.0


def _downsample(img):
    h, w = img.shape[:2]
    step = max(1, int(np.ceil(max(h, w) / MAX_EDGE)))
    return img[::step, ::step]


def measure(img, neutral_hint=None):
    """Measure an sRGB image (float in [0, 1] or uint8).

    neutral_hint: (a*, b*) where this photo's neutrals are expected, normally
    the reference's neutral axis. The search for near-gray pixels starts there,
    so a frame dominated by one colour (foliage, a red wall) isn't mistaken
    for a colour cast. Ignored if no gray-ish pixels are found near it.
    """
    img = np.asarray(img)
    if img.dtype == np.uint8:
        img = img.astype(np.float64) / 255.0
    img = _downsample(img).reshape(-1, 3)

    clipped = (img < CLIP_LOW).any(axis=1) | (img > CLIP_HIGH).any(axis=1)
    lab = srgb_to_lab(img)

    # Tone percentiles use every pixel: clipping is part of the tonal look.
    L = {f"p{p}": float(v) for p, v in zip(PERCENTILES, np.percentile(lab[:, 0], PERCENTILES))}

    usable = lab[~clipped]
    if len(usable) == 0:
        usable = lab
    # Near-neutral = close to the frame's own cast, so a strong cast doesn't push
    # every pixel out of the selection. The cast is found by narrowing in on the
    # largest low-chroma cluster (median, shrinking radius), so a big saturated
    # area like foliage or a red wall drops out instead of dragging the estimate.
    midtones = (usable[:, 0] >= NEUTRAL_L_RANGE[0]) & (usable[:, 0] <= NEUTRAL_L_RANGE[1])
    cast = np.median(usable[midtones, 1:] if midtones.any() else usable[:, 1:], axis=0)
    radii = NEUTRAL_RADII
    if neutral_hint is not None:
        hint = np.asarray(neutral_hint, dtype=float)
        near = midtones & (np.hypot(usable[:, 1] - hint[0], usable[:, 2] - hint[1]) <= HINT_RADII[0])
        if near.sum() >= MIN_NEUTRAL_FRACTION * len(img):
            cast, radii = hint, HINT_RADII
    neutral = midtones
    for radius in radii:
        dist = np.hypot(usable[:, 1] - cast[0], usable[:, 2] - cast[1])
        candidate = midtones & (dist <= radius)
        if candidate.sum() < MIN_NEUTRAL_FRACTION * len(img):
            break
        neutral = candidate
        cast = np.median(usable[neutral, 1:], axis=0)
    neutral_fraction = float(neutral.sum()) / len(img)
    # Fall back to plain gray-world when the frame has almost no near-neutral pixels.
    pool = usable[neutral] if neutral_fraction >= MIN_NEUTRAL_FRACTION else usable

    # Skin: membership from the measured skin-tone model (engine/skin.py). The
    # mean is weighted by membership so pixels near the edge of the skin band
    # fade in and out smoothly between renders instead of flipping.
    w = skin_model.weights(usable)
    skin = w >= skin_model.MIN_WEIGHT
    skin_fraction = float(skin.sum()) / len(img)
    if skin.any():
        sw = w[skin] / w[skin].sum()
        skin_L, skin_a, skin_b = (float(v) for v in sw @ usable[skin])
    else:
        skin_L = skin_a = skin_b = None

    return Metrics(
        a=float(np.mean(pool[:, 1])),
        b=float(np.mean(pool[:, 2])),
        L=L,
        clipped_fraction=float(clipped.mean()),
        neutral_fraction=neutral_fraction,
        skin_a=skin_a,
        skin_b=skin_b,
        skin_fraction=skin_fraction,
        skin_L=skin_L,
    )


def measure_file(path, neutral_hint=None):
    return measure(load_image(path), neutral_hint)
