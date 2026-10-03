"""Look stage: match what the eye sees beyond white balance and exposure.

The light stage (engine/solver.py) makes each photo's neutrals and brightness
match the reference. That isn't the whole look: a reference with a deep blue
sky, punchy contrast and clean whites still looks different from a flat,
golden-hour frame with the same neutral axis. This stage measures the look
from pixels and solves small per-photo offsets on Lightroom's creative
sliders, on top of the reference's own creative settings:

- tone shape: Contrast2012 and the four Parametric tone-curve sliders,
  against L* percentiles
- overall saturation: Vibrance and Saturation, against mean chroma
- each colour family: HSL Hue / Saturation / Luminance per Lightroom band,
  against that band's mean hue, chroma and lightness. A band is only touched
  when it covers enough of both photos (a close-up with no sky never moves blue).
- split toning: the colour of shadows and highlights, only when the
  reference has none of its own

It works the same whether the reference was edited in Lightroom or is an
exported JPEG with its look baked into the pixels.
"""

from dataclasses import dataclass, field

import numpy as np

from . import skin as skin_model
from .colorspace import srgb_to_lab
from .measure import _downsample, load_image

# Lightroom's HSL bands and their centres on the HSV hue wheel (degrees).
BANDS = ["Red", "Orange", "Yellow", "Green", "Aqua", "Blue", "Purple", "Magenta"]
BAND_CENTERS = np.array([0.0, 30.0, 60.0, 120.0, 180.0, 240.0, 270.0, 300.0])
TONE_PERCENTILES = (5, 15, 30, 50, 70, 85, 95)
BAND_CHROMA = (6.0, 12.0)  # band membership fades in over this chroma range
MIN_BAND_FRACTION = 0.02  # a band must cover this much of both photos to be matched
FULL_BAND_FRACTION = 0.06  # ...and gets full weight from this much
SPLIT_CHROMA = 12.0  # near-neutral pixels used for the shadow / highlight tint
SHADOW_L = (15.0, 40.0)
HIGHLIGHT_L = (65.0, 92.0)
MIN_SPLIT_FRACTION = 0.01

TONE_KEYS = ["Contrast2012", "ParametricShadows", "ParametricDarks", "ParametricLights", "ParametricHighlights"]
SAT_KEYS = ["Vibrance", "Saturation"]
BAND_KEYS = [f"{kind}Adjustment{band}" for band in BANDS for kind in ("Hue", "Saturation", "Luminance")]
SPLIT_VARS = ["shadow_a", "shadow_b", "highlight_a", "highlight_b"]
VARIABLES = TONE_KEYS + SAT_KEYS + BAND_KEYS + SPLIT_VARS
SPLIT_KEYS = ["SplitToningShadowHue", "SplitToningShadowSaturation",
              "SplitToningHighlightHue", "SplitToningHighlightSaturation"]
# Every Lightroom key this stage may write.
LOOK_KEYS = TONE_KEYS + SAT_KEYS + BAND_KEYS + SPLIT_KEYS

LIMIT = 30.0  # max offset from the reference's value, per slider
SPLIT_LIMIT = 25.0  # max split-toning saturation
STEP_CAP = 20.0  # max change per slider per pass
REGULARIZE = 0.002  # pull toward zero offset, so sliders don't move without reason

# HSV hue -> CIELAB hue angle for the sRGB primaries and secondaries; used to
# turn a Lab tint into Lightroom's split-toning hue and back.
_HSV_TO_LAB = np.array([[0, 40], [60, 103], [120, 136], [180, 197], [240, 306], [300, 328], [360, 400]], float)


def hsv_hue_to_lab(h):
    return float(np.interp(h % 360, _HSV_TO_LAB[:, 0], _HSV_TO_LAB[:, 1]) % 360)


def lab_hue_to_hsv(h):
    h = h % 360
    if h < 40:
        h += 360
    return float(np.interp(h, _HSV_TO_LAB[:, 1], _HSV_TO_LAB[:, 0]) % 360)


def hsv_hue(rgb):
    """HSV hue in degrees for sRGB pixels, shape (N, 3)."""
    r, g, b = rgb[:, 0], rgb[:, 1], rgb[:, 2]
    mx, mn = rgb.max(axis=1), rgb.min(axis=1)
    d = np.where(mx - mn > 1e-9, mx - mn, 1.0)
    h = np.where(mx == r, (g - b) / d % 6, np.where(mx == g, (b - r) / d + 2, (r - g) / d + 4))
    return h * 60.0


def band_weights(hue):
    """Soft membership of each HSV hue in Lightroom's 8 bands, (N,) -> (N, 8).
    Linear between neighbouring band centres, so the weights sum to 1."""
    centers = np.append(BAND_CENTERS, 360.0)
    w = np.zeros((len(hue), len(BANDS) + 1))
    idx = np.clip(np.searchsorted(centers, hue % 360, side="right") - 1, 0, len(BANDS) - 1)
    lo, hi = centers[idx], centers[idx + 1]
    t = (hue % 360 - lo) / (hi - lo)
    rows = np.arange(len(hue))
    w[rows, idx] = 1 - t
    w[rows, idx + 1] += t
    w[:, 0] += w[:, -1]  # 360 wraps to red
    return w[:, :-1]


def chroma_weight(c):
    lo, hi = BAND_CHROMA
    return np.clip((c - lo) / (hi - lo), 0.0, 1.0)


def _pixels(img):
    img = np.asarray(img)
    if img.dtype == np.uint8:
        img = img.astype(np.float64) / 255.0
    rgb = _downsample(img).reshape(-1, 3)
    return rgb, srgb_to_lab(rgb)


def pixel_masks(rgb, lab):
    """Which pixels count toward each band, the shadow / highlight tint and skin.

    Taken once from a photo's first render and reused for its later renders,
    so the statistics follow the same pixels: otherwise a pixel moving from
    "yellow" to "orange" as sliders change would make the measurements jump.
    """
    L, a, b = lab[:, 0], lab[:, 1], lab[:, 2]
    c = np.hypot(a, b)
    near_neutral = (c < SPLIT_CHROMA) & (L > 1) & (L < 99)
    sw = skin_model.weights(lab)
    return {
        "bands": band_weights(hsv_hue(rgb)) * chroma_weight(c)[:, None],
        "shadow": near_neutral & (L >= SHADOW_L[0]) & (L <= SHADOW_L[1]),
        "highlight": near_neutral & (L >= HIGHLIGHT_L[0]) & (L <= HIGHLIGHT_L[1]),
        "skin": np.where(sw >= skin_model.MIN_WEIGHT, sw, 0.0),
    }


def measure_look(img, masks=None):
    """Look statistics of an sRGB image (float in [0, 1] or uint8).

    masks: from an earlier render of the same photo (see pixel_masks); None
    takes them from this image. measure_look_with_masks also returns them."""
    return measure_look_with_masks(img, masks)[0]


def measure_look_with_masks(img, masks=None):
    rgb, lab = _pixels(img)
    L, a, b = lab[:, 0], lab[:, 1], lab[:, 2]
    c = np.hypot(a, b)
    n = len(L)
    if masks is None or len(masks["shadow"]) != n:
        masks = pixel_masks(rgb, lab)

    tone = {f"p{p}": float(v) for p, v in zip(TONE_PERCENTILES, np.percentile(L, TONE_PERCENTILES))}
    # Means, not percentiles: a frame that is half sky and half gray has a
    # bimodal chroma distribution whose median jumps between the two.
    top = c[c >= np.percentile(c, 90)]
    chroma = {"mean": float(c.mean()), "top": float(top.mean()) if len(top) else 0.0}

    bands = {}
    for k, name in enumerate(BANDS):
        wk = masks["bands"][:, k]
        total = wk.sum()
        if total <= 0:
            bands[name] = {"f": 0.0, "h": 0.0, "c": 0.0, "L": 0.0}
            continue
        h = np.degrees(np.arctan2((wk * b).sum(), (wk * a).sum())) % 360
        bands[name] = {"f": float(total / n), "h": float(h), "c": float((wk * c).sum() / total),
                       "L": float((wk * L).sum() / total)}

    split = {}
    for name in ("shadow", "highlight"):
        sel = masks[name]
        split[name] = [float(a[sel].mean()), float(b[sel].mean())] if sel.sum() >= MIN_SPLIT_FRACTION * n else None
    sw = masks["skin"]
    skin = [float((sw * a).sum() / sw.sum()), float((sw * b).sum() / sw.sum())] if sw.sum() >= 0.01 * n else None
    return {"tone": tone, "chroma": chroma, "bands": bands, "split": split, "skin": skin}, masks


def measure_look_file(path, masks=None):
    return measure_look(load_image(path), masks)


def measure_look_file_with_masks(path, masks=None):
    return measure_look_with_masks(load_image(path), masks)


# -- residual -----------------------------------------------------------------

def _wrap(deg):
    return (deg + 180.0) % 360.0 - 180.0


@dataclass
class Plan:
    """What gets matched for one photo, fixed for the whole loop so the
    residual keeps the same meaning from pass to pass."""
    tone: bool = True
    bands: list = field(default_factory=list)  # active band names
    band_weight: dict = field(default_factory=dict)
    split: list = field(default_factory=list)  # "shadow" / "highlight"
    skin: bool = False  # keep skin tones matched too
    enabled: bool = True

    def variables(self):
        if not self.enabled:
            return []
        out = (TONE_KEYS if self.tone else []) + SAT_KEYS
        for band in self.bands:
            out += [f"HueAdjustment{band}", f"SaturationAdjustment{band}", f"LuminanceAdjustment{band}"]
        for name in self.split:
            out += [f"{name}_a", f"{name}_b"]
        return out

    def to_dict(self):
        return {"tone": self.tone, "bands": self.bands, "band_weight": self.band_weight, "split": self.split,
                "skin": self.skin}


def make_plan(ref, cur, tone=True, split=True, skin=False, max_ratio=None):
    """Decide which parts of the look to match for this photo.

    max_ratio: only match a band when it covers a similar share of both photos
    (within this factor). Used when the photos show different things, so a
    band that is mostly sky in one and a sliver in the other isn't "matched"."""
    plan = Plan(tone=tone, skin=bool(skin and ref.get("skin") and cur.get("skin")))
    for name in BANDS:
        f = min(ref["bands"][name]["f"], cur["bands"][name]["f"])
        similar = max_ratio is None or max(ref["bands"][name]["f"], cur["bands"][name]["f"]) <= max_ratio * f
        if f >= MIN_BAND_FRACTION and similar:
            plan.bands.append(name)
            plan.band_weight[name] = float(min(1.0, f / FULL_BAND_FRACTION))
    if split:
        plan.split = [n for n in ("shadow", "highlight") if ref["split"][n] is not None and cur["split"][n] is not None]
    return plan


def residual(ref, cur, plan):
    """Weighted residual (current - reference) in roughly ΔE units, and its weights."""
    r, w = [], []
    if plan.tone:
        for p in TONE_PERCENTILES:
            r.append(cur["tone"][f"p{p}"] - ref["tone"][f"p{p}"])
            w.append(0.6)
    for k in ("mean", "top"):
        r.append(cur["chroma"][k] - ref["chroma"][k])
        w.append(1.0)
    for band in plan.bands:
        rb, cb = ref["bands"][band], cur["bands"][band]
        bw = plan.band_weight[band]
        # Hue as arc length at the reference's chroma, so it's in ΔE-like units.
        r += [_wrap(cb["h"] - rb["h"]) * np.pi / 180 * max(rb["c"], 5.0), cb["c"] - rb["c"], cb["L"] - rb["L"]]
        w += [bw, bw, 0.5 * bw]
    for name in plan.split:
        r += [cur["split"][name][0] - ref["split"][name][0], cur["split"][name][1] - ref["split"][name][1]]
        w += [1.0, 1.0]
    if plan.skin:
        r += [cur["skin"][0] - ref["skin"][0], cur["skin"][1] - ref["skin"][1]]
        w += [2.0, 2.0]
    return np.array(r, float), np.array(w, float)


def look_error(ref, cur, plan):
    r, w = residual(ref, cur, plan)
    return float(np.sqrt((w * r**2).sum() / w.sum())) if len(r) else 0.0


def prior_jacobian(ref, plan):
    """Built-in guess of d(residual)/d(variable), refined from renders (Broyden)."""
    vars_ = plan.variables()
    col = {v: i for i, v in enumerate(vars_)}
    rows = []
    if plan.tone:
        rows += [("tone", p) for p in TONE_PERCENTILES]
    rows += [("chroma", "mean"), ("chroma", "top")]
    for band in plan.bands:
        rows += [("hue", band), ("chr", band), ("lum", band)]
    for name in plan.split:
        rows += [("split", name, 0), ("split", name, 1)]
    if plan.skin:
        rows += [("skin", 0), ("skin", 1)]
    J = np.zeros((len(rows), len(vars_)))
    tone_effect = {  # L* change per slider point at each percentile
        "Contrast2012": [-0.10, -0.08, -0.04, 0.0, 0.04, 0.08, 0.08],
        "ParametricShadows": [0.08, 0.10, 0.06, 0.02, 0.0, 0.0, 0.0],
        "ParametricDarks": [0.02, 0.04, 0.08, 0.08, 0.04, 0.0, 0.0],
        "ParametricLights": [0.0, 0.0, 0.04, 0.08, 0.08, 0.04, 0.02],
        "ParametricHighlights": [0.0, 0.0, 0.0, 0.02, 0.06, 0.10, 0.08],
    }
    for i, row in enumerate(rows):
        kind = row[0]
        if kind == "tone" and plan.tone:
            k = TONE_PERCENTILES.index(row[1])
            for key, effect in tone_effect.items():
                J[i, col[key]] = effect[k]
        elif kind == "chroma":
            scale = ref["chroma"][row[1]] / 100.0
            J[i, col["Vibrance"]] = (0.8 if row[1] == "mean" else 0.4) * scale
            J[i, col["Saturation"]] = 0.6 * scale
        elif kind == "hue":
            J[i, col[f"HueAdjustment{row[1]}"]] = 0.3 * np.pi / 180 * max(ref["bands"][row[1]]["c"], 5.0)
        elif kind == "chr":
            c = ref["bands"][row[1]]["c"] / 100.0
            J[i, col[f"SaturationAdjustment{row[1]}"]] = 0.8 * c
            J[i, col["Vibrance"]] = 0.4 * c
            J[i, col["Saturation"]] = 0.6 * c
        elif kind == "lum":
            J[i, col[f"LuminanceAdjustment{row[1]}"]] = 0.15
        elif kind == "split":
            J[i, col[f"{row[1]}_{'ab'[row[2]]}"]] = 0.12
        elif kind == "skin":
            # Skin sits in the orange band: its hue slider rotates skin, its
            # saturation slider scales it.
            a, b = ref["skin"]
            if "Orange" in plan.bands:
                J[i, col["HueAdjustmentOrange"]] = 0.3 * np.pi / 180 * (-b if row[1] == 0 else a)
                J[i, col["SaturationAdjustmentOrange"]] = 0.8 / 100 * (a if row[1] == 0 else b)
            v = a if row[1] == 0 else b
            J[i, col["Vibrance"]] = 0.6 / 100 * v
            J[i, col["Saturation"]] = 0.6 / 100 * v
    return J


# -- solver ----------------------------------------------------------------------

@dataclass
class LookProposal:
    offsets: dict
    done: bool
    error: float
    best_error: float
    iterations: int
    limited: bool = False


def _vector(offsets, vars_):
    return np.array([float(offsets.get(v, 0.0)) for v in vars_])


def _limit(x, vars_, limit=LIMIT, split_limit=SPLIT_LIMIT):
    x = np.clip(x, -limit, limit)
    for name in ("shadow", "highlight"):
        if f"{name}_a" in vars_:
            i, j = vars_.index(f"{name}_a"), vars_.index(f"{name}_b")
            s = np.hypot(x[i], x[j])
            if s > split_limit:
                x[[i, j]] *= split_limit / s
    return x


def propose_look(ref, history, plan, tolerance=1.0, max_iterations=4, limit=LIMIT, split_limit=SPLIT_LIMIT):
    """Next look offsets to render, or the best found once done.

    history: [{"offsets": {...}, "look": {...}}, ...], oldest first; the first
    entry is the light-matched render with no look offsets.
    """
    vars_ = plan.variables()
    xs = [_vector(h["offsets"], vars_) for h in history]
    rs, ws = zip(*(residual(ref, h["look"], plan) for h in history))
    w = ws[0]
    errors = [float(np.sqrt((w * r**2).sum() / w.sum())) if len(r) else 0.0 for r in rs]
    best = int(np.argmin(errors))
    iterations = len(history) - 1

    def done():
        x = xs[best]
        limited = bool(np.any(np.abs(x) >= limit - 0.5)) and errors[best] >= tolerance
        return LookProposal(_offsets(x, vars_), True, errors[-1], errors[best], iterations, limited)

    if not vars_ or errors[-1] < tolerance or iterations >= max_iterations:
        return done()
    if len(errors) > 1 and errors[-1] > errors[best] * 1.02:
        # Got worse: back off halfway toward the best render.
        x_next = (xs[-1] + xs[best]) / 2
        if np.abs(x_next - xs[-1]).max() < 0.5:
            return done()
        return LookProposal(_offsets(x_next, vars_), False, errors[-1], errors[best], iterations)

    J = prior_jacobian(ref, plan)
    for i in range(1, len(xs)):  # Broyden updates from the renders so far
        dx, dr = xs[i] - xs[i - 1], rs[i] - rs[i - 1]
        denom = dx @ dx
        if denom > 1e-6:
            J = J + np.outer(dr - J @ dx, dx) / denom
    W = np.diag(w)
    lhs = J.T @ W @ J + REGULARIZE * np.eye(len(vars_)) + 1e-4 * np.diag(np.diag(J.T @ W @ J))
    rhs = -J.T @ W @ rs[-1] - REGULARIZE * xs[-1]
    dx = np.clip(np.linalg.solve(lhs, rhs), -STEP_CAP, STEP_CAP)
    x_next = _limit(xs[-1] + dx, vars_, limit, split_limit)
    if np.abs(x_next - xs[-1]).max() < 0.5:
        return done()
    return LookProposal(_offsets(x_next, vars_), False, errors[-1], errors[best], iterations)


def _offsets(x, vars_):
    # Lightroom's creative sliders are whole numbers.
    return {v: float(round(val)) for v, val in zip(vars_, x)}


# -- Lightroom settings -------------------------------------------------------

def has_split_toning(creative):
    return bool(creative.get("SplitToningShadowSaturation") or creative.get("SplitToningHighlightSaturation")
                or creative.get("ColorGradeShadowSat") or creative.get("ColorGradeHighlightSat"))


def is_baked(creative):
    """True when the reference carries no creative colour or tone edits of its
    own (typically an exported JPEG): its look is in the pixels only."""
    keys = TONE_KEYS + SAT_KEYS + BAND_KEYS + SPLIT_KEYS + [
        "ColorGradeMidtoneSat", "ColorGradeShadowSat", "ColorGradeHighlightSat", "ColorGradeGlobalSat"]
    if any(float(creative.get(k) or 0) != 0 for k in keys):
        return False
    curve = creative.get("ToneCurvePV2012")
    return not curve or list(curve) in ([0, 0, 255, 255], [0.0, 0.0, 255.0, 255.0])


def to_settings(offsets, creative, split_limit=SPLIT_LIMIT):
    """Look offsets -> Lightroom settings, added to the reference's own values."""
    out = {}
    for key, value in offsets.items():
        if key in SPLIT_VARS:
            continue
        base = float(creative.get(key) or 0.0)
        out[key] = float(round(np.clip(base + value, -100, 100)))
    for name, cap in (("shadow", "Shadow"), ("highlight", "Highlight")):
        if f"{name}_a" in offsets:
            sa, sb = offsets[f"{name}_a"], offsets[f"{name}_b"]
            sat = float(np.hypot(sa, sb))
            out[f"SplitToning{cap}Saturation"] = float(round(min(sat, split_limit)))
            out[f"SplitToning{cap}Hue"] = float(round(lab_hue_to_hsv(np.degrees(np.arctan2(sb, sa))))) % 360 if sat > 0 else 0.0
    return out


def describe(offsets):
    """Short readable note, e.g. "Blue sat +12, contrast +8"."""
    notes = []
    for key, value in sorted(offsets.items(), key=lambda kv: -abs(kv[1])):
        if abs(value) < 3 or key in SPLIT_VARS:
            continue
        name = key.replace("Adjustment", " ").replace("2012", "").replace("Parametric", "curve ")
        for band in BANDS:
            if name.endswith(" " + band):
                kind = name.split(" ")[0].lower().replace("saturation", "sat").replace("luminance", "lum")
                name = f"{band} {kind}"
        notes.append(f"{name.lower()} {value:+.0f}")
    split = [n for n in ("shadow", "highlight") if np.hypot(offsets.get(f"{n}_a", 0), offsets.get(f"{n}_b", 0)) >= 3]
    notes += [f"{n} tint" for n in split]
    return ", ".join(notes[:5])
