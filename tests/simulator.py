"""A toy stand-in for Lightroom's base render, used to test the closed loop.

It is not Lightroom's math, but it has the same kinds of behavior the solver has
to cope with: white balance as channel gains that depend on Kelvin/Tint,
exposure in stops, and nonlinear, overlapping tone sliders, then clipping.
"""

import numpy as np

from engine.colorspace import linear_to_srgb


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
    return np.clip(v, 0.0, 1.0)


def run_loop(ref_metrics, raw, start_sliders, is_raw=True, **kwargs):
    """Drive propose -> render -> measure until the solver says done."""
    from engine.measure import measure
    from engine.solver import propose

    history = [{"sliders": start_sliders, "metrics": measure(render(raw, start_sliders)).to_dict()}]
    while True:
        p = propose(ref_metrics, history, is_raw=is_raw, **kwargs)
        if p.done:
            return p, history
        history.append({"sliders": p.sliders, "metrics": measure(render(raw, p.sliders)).to_dict()})
