"""Measure the color balance and tone signature of a rendered preview."""

from dataclasses import asdict, dataclass

import numpy as np
from PIL import Image

from .colorspace import srgb_to_lab

PERCENTILES = (1, 25, 50, 75, 99)
MAX_EDGE = 512
CLIP_LOW = 0.01
CLIP_HIGH = 0.99
# Pixels used for the neutral estimate: mid-tones close to the frame's average cast.
NEUTRAL_L_RANGE = (20.0, 85.0)
NEUTRAL_MAX_CHROMA = 20.0
MIN_NEUTRAL_FRACTION = 0.02


@dataclass
class Metrics:
    a: float  # neutral axis, green(-) / magenta(+)
    b: float  # neutral axis, blue(-) / yellow(+)
    L: dict  # L* percentiles, keyed "p1", "p25", ...
    clipped_fraction: float
    neutral_fraction: float

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


def measure(img):
    """Measure an sRGB image (float in [0, 1] or uint8)."""
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
    # Near-neutral = close to the frame's own average color, so a strong cast
    # doesn't push every pixel out of the selection.
    cast = usable[:, 1:].mean(axis=0)
    chroma = np.hypot(usable[:, 1] - cast[0], usable[:, 2] - cast[1])
    neutral = (
        (usable[:, 0] >= NEUTRAL_L_RANGE[0])
        & (usable[:, 0] <= NEUTRAL_L_RANGE[1])
        & (chroma <= NEUTRAL_MAX_CHROMA)
    )
    neutral_fraction = float(neutral.sum()) / len(img)
    # Fall back to plain gray-world when the frame has almost no near-neutral pixels.
    pool = usable[neutral] if neutral_fraction >= MIN_NEUTRAL_FRACTION else usable

    return Metrics(
        a=float(np.mean(pool[:, 1])),
        b=float(np.mean(pool[:, 2])),
        L=L,
        clipped_fraction=float(clipped.mean()),
        neutral_fraction=neutral_fraction,
    )


def measure_file(path):
    return measure(load_image(path))
