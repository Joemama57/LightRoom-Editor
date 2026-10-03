"""A toy stand-in for Lightroom's base render, used to test the closed loop.

It is not Lightroom's math, but it has the same kinds of behavior the solver has
to cope with: white balance as channel gains that depend on Kelvin/Tint,
exposure in stops, and nonlinear, overlapping tone sliders, then clipping.
The creative "look" sliders (contrast, parametric curve, vibrance/saturation,
HSL bands, split toning) act in Lab, roughly the way Lightroom's do.
"""

import numpy as np

from engine.colorspace import lab_to_srgb, linear_to_srgb, srgb_to_lab
from engine import look


def illuminant_gains(kelvin, tint=0.0):
    """Relative RGB of a light source. Lower Kelvin = warmer (more red, less blue)."""
    mired_offset = 1e6 / kelvin - 1e6 / 5500.0
    r = np.exp(0.0035 * mired_offset)
    b = np.exp(-0.0050 * mired_offset)
    g = np.exp(0.004 * tint)  # a green light needs +Tint (magenta) to neutralize
    return np.array([r, g, b])


def make_scene(seed=0, size=96):
    """Linear-light scene: a gradient backdrop, gray cards and a few colored patches."""
    rng = np.random.default_rng(seed)
    y, x = np.mgrid[0:size, 0:size] / (size - 1)
    base = 0.02 + 0.6 * (0.6 * x + 0.4 * y) ** 1.8
    scene = np.stack([base * 0.95, base, base * 0.9], axis=-1)
    patches = [
        (0.18, 0.18, 0.18),
        (0.05, 0.05, 0.05),
        (0.45, 0.45, 0.45),
        (0.35, 0.12, 0.08),
        (0.10, 0.25, 0.10),
        (0.08, 0.12, 0.35),
        (0.55, 0.40, 0.25),
        (0.70, 0.65, 0.20),
    ]
    p = size // 6
    for i, color in enumerate(patches):
        row, col = divmod(i, 4)
        r0 = p // 2 + row * (p + p // 2) + size // 3
        c0 = p // 2 + col * (p + p // 3)
        scene[r0 : r0 + p, c0 : c0 + p] = color
    scene *= 1 + 0.02 * rng.standard_normal(scene.shape)
    return np.clip(scene, 0, None)


def capture(scene, light_kelvin, light_tint=0.0, stops=0.0):
    """What the camera records: scene lit by a given light, over/under exposed by `stops`."""
    return scene * illuminant_gains(light_kelvin, light_tint) * 2.0**stops


def render(raw, sliders):
    """Render a capture with Lightroom-style corrective sliders to sRGB in [0, 1]."""
    wb = 1.0 / illuminant_gains(sliders.get("Temperature", 5500.0), sliders.get("Tint", 0.0))
    lin = raw * wb * 2.0 ** sliders.get("Exposure2012", 0.0)

    v = linear_to_srgb(lin)
    blacks = sliders.get("Blacks2012", 0.0) / 100.0
    shadows = sliders.get("Shadows2012", 0.0) / 100.0
    highlights = sliders.get("Highlights2012", 0.0) / 100.0
    whites = sliders.get("Whites2012", 0.0) / 100.0
    v = v + 0.10 * blacks * (1 - v) ** 6
    v = v + 0.50 * shadows * v * (1 - v) ** 3
    v = v + 0.50 * highlights * v**3 * (1 - v)
    v = v + 0.12 * whites * v**6
    v = np.clip(v, 0.0, 1.0)
    if any(sliders.get(k) for k in look.LOOK_KEYS):
        v = _render_look(v, sliders)
    return v


PARAMETRIC_CENTERS = {"ParametricShadows": 12, "ParametricDarks": 35, "ParametricLights": 65, "ParametricHighlights": 88}


def _render_look(v, sliders):
    shape = v.shape
    rgb = v.reshape(-1, 3)
    lab = srgb_to_lab(rgb)
    L, a, b = lab[:, 0], lab[:, 1], lab[:, 2]
    c, h = np.hypot(a, b), np.degrees(np.arctan2(b, a))

    k = sliders.get("Contrast2012", 0.0) / 100
    x = (L - 50) / 50
    L = L + 0.25 * k * (L - 50) * np.clip(1 - x**2, 0, 1)
    for key, center in PARAMETRIC_CENTERS.items():
        L = L + 0.12 * sliders.get(key, 0.0) * np.exp(-0.5 * ((L - center) / 14) ** 2)

    factor = (1 + sliders.get("Vibrance", 0.0) / 100 * 0.8 * np.exp(-c / 40)) * (1 + sliders.get("Saturation", 0.0) / 100 * 0.6)
    w = look.band_weights(look.hsv_hue(rgb)) * look.chroma_weight(c)[:, None]
    hue_adj = np.array([sliders.get(f"HueAdjustment{n}", 0.0) for n in look.BANDS])
    sat_adj = np.array([sliders.get(f"SaturationAdjustment{n}", 0.0) for n in look.BANDS])
    lum_adj = np.array([sliders.get(f"LuminanceAdjustment{n}", 0.0) for n in look.BANDS])
    h = h + 0.3 * (w @ hue_adj)
    c = c * factor * (1 + 0.8 * (w @ sat_adj) / 100)
    L = L + 0.15 * (w @ lum_adj)
    a, b = c * np.cos(np.radians(h)), c * np.sin(np.radians(h))

    for name, mask in (("Shadow", np.clip((50 - L) / 35, 0, 1)), ("Highlight", np.clip((L - 50) / 35, 0, 1))):
        sat = sliders.get(f"SplitToning{name}Saturation", 0.0)
        if sat:
            angle = np.radians(look.hsv_hue_to_lab(sliders.get(f"SplitToning{name}Hue", 0.0)))
            a = a + mask * 0.12 * sat * np.cos(angle)
            b = b + mask * 0.12 * sat * np.sin(angle)
    return lab_to_srgb(np.stack([np.clip(L, 0, 100), a, b], axis=-1)).reshape(shape)


def run_loop(ref_metrics, raw, start_sliders, is_raw=True, **kwargs):
    """Drive propose -> render -> measure until the solver says done."""
    from engine.measure import measure
    from engine.solver import propose

    hint = (ref_metrics.a, ref_metrics.b)
    history = [{"sliders": start_sliders, "metrics": measure(render(raw, start_sliders), hint).to_dict()}]
    while True:
        p = propose(ref_metrics, history, is_raw=is_raw, **kwargs)
        if p.done:
            return p, history
        history.append({"sliders": p.sliders, "metrics": measure(render(raw, p.sliders), hint).to_dict()})
