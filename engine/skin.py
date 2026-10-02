"""Skin-tone model: find skin in a rendered photo and describe a skin tone.

Built from measured skin colour data by `tools/build_skin_model.py`; see
docs/SKIN_TONES.md for the data and how the numbers were derived.
"""

import json
import math
from functools import lru_cache
from pathlib import Path

import numpy as np

MODEL_PATH = Path(__file__).resolve().parent / "data" / "skin_model.json"
# A pixel counts as skin when its membership is at least this. 0.1 keeps ruddy
# skin (hue ~39°, +1 SD redness in the measured data) while the chroma band
# still rejects saturated reds.
MIN_WEIGHT = 0.1
# Soft edge width for the chroma and lightness bands.
CHROMA_SOFT = 3.0
L_SOFT = 4.0


@lru_cache(maxsize=1)
def model():
    return json.loads(MODEL_PATH.read_text())


def _soft_band(x, lo, hi, soft):
    """1 inside [lo, hi], falling off smoothly (Gaussian) outside it."""
    below = np.clip(lo - x, 0, None)
    above = np.clip(x - hi, 0, None)
    return np.exp(-0.5 * ((below + above) / soft) ** 2)


def weights(lab):
    """Skin membership (0..1) for each Lab pixel, shape (N, 3) -> (N,)."""
    d = model()["detector"]
    L, a, b = lab[:, 0], lab[:, 1], lab[:, 2]
    hue = np.degrees(np.arctan2(b, a))
    chroma = np.hypot(a, b)
    w_hue = np.exp(-0.5 * ((hue - d["hue_center"]) / d["hue_sd"]) ** 2)
    w_hue[(hue < d["hue_range"][0]) | (hue > d["hue_range"][1])] = 0.0
    w_c = _soft_band(chroma, *d["chroma_range"], CHROMA_SOFT)
    w_c[chroma < d["chroma_range"][0] - CHROMA_SOFT] = 0.0  # near-gray: hue is meaningless
    w_L = _soft_band(L, *d["L_range"], L_SOFT)
    return w_hue * w_c * w_L


def ita(L, b):
    """Individual Typology Angle (Chardon 1991), degrees."""
    return math.degrees(math.atan2(L - 50.0, b))


def ita_class(value):
    for c in model()["ita_classes"]:
        if value > c["min"]:
            return c["name"]
    return model()["ita_classes"][-1]["name"]


def nearest_monk(L):
    """Closest Monk Skin Tone by lightness. The swatches are yellower than real
    skin (docs/SKIN_TONES.md), so only lightness is compared."""
    return min(model()["monk"], key=lambda t: abs(t["L"] - L))["tone"]


def describe(L, a, b):
    """Plain-language description of a skin tone's colour."""
    m = model()
    hue = math.degrees(math.atan2(b, a))
    center, sd = m["population"]["hue_mean"], m["population"]["hue_sd"]
    if hue < center - sd:
        hue_note = "redder / more magenta than typical skin"
    elif hue > center + sd:
        hue_note = "yellower / greener than typical skin"
    else:
        hue_note = "within the typical range for skin"
    value = ita(L, b)
    return {
        "L": round(L, 1), "a": round(a, 1), "b": round(b, 1),
        "hue": round(hue, 1), "chroma": round(math.hypot(a, b), 1),
        "ita": round(value, 1), "ita_class": ita_class(value),
        "monk_tone": nearest_monk(L),
        "hue_note": hue_note,
    }


def compare(ref, target, threshold=1.5):
    """How a target's skin differs from the reference's, in words.

    ref/target: dicts with L, a, b (e.g. from `describe`). Differences below
    `threshold` (Lab units) aren't mentioned.
    """
    words = []
    da = target["a"] - ref["a"]
    db = target["b"] - ref["b"]
    dL = target["L"] - ref["L"]
    if da > threshold:
        words.append("more magenta")
    elif da < -threshold:
        words.append("greener")
    if db > threshold:
        words.append("yellower / warmer")
    elif db < -threshold:
        words.append("bluer / cooler")
    if dL > 2 * threshold:
        words.append("brighter")
    elif dL < -2 * threshold:
        words.append("darker")
    return ", ".join(words) if words else "matches the reference"
