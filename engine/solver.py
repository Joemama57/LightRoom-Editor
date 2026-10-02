"""Closed-loop solver for the corrective (scene-dependent) Lightroom sliders.

The loop is driven from outside: Lightroom renders a preview with some slider
values, the engine measures it, and `propose` returns the next slider values to
try. Everything the solver needs is in `history` (one entry per render), so the
engine stays stateless between calls.

Lightroom's slider math is not public, so the solver doesn't model it. It starts
from rough prior sensitivities (how much each slider moves each metric), then
refines them from the renders it has seen (Broyden updates) and takes damped
Newton steps toward the reference's metrics.
"""

from dataclasses import dataclass

import numpy as np

from .colorspace import delta_e_2000
from .measure import Metrics

# Lightroom develop-setting keys, in solver order.
CORRECTIVE = [
    "Temperature",
    "Tint",
    "Exposure2012",
    "Shadows2012",
    "Highlights2012",
    "Whites2012",
    "Blacks2012",
]
# Metric vector order: neutral b*, neutral a*, then L* percentiles.
METRIC_KEYS = ["b", "a", "p1", "p25", "p50", "p75", "p99"]
# Relative weight of each metric in the fit. Mid-tones and color matter most;
# the extreme percentiles depend on scene content and are matched more loosely.
METRIC_WEIGHTS = np.array([1.0, 1.0, 0.4, 0.8, 1.0, 0.8, 0.4])

# Typical step size per slider, used to put sliders on a comparable scale.
# Temperature is handled in mireds for raw files (see _to_internal).
SLIDER_SCALE = np.array([40.0, 20.0, 1.0, 50.0, 50.0, 50.0, 50.0])
# How strongly each slider is pulled back toward its starting value. White
# balance and exposure fix the light, so they move freely; the tone sliders
# are held back so they don't chase content differences between photos.
ANCHOR = np.array([0.0, 0.0, 0.0, 0.3, 0.3, 0.3, 0.3])
# Indices of the sliders that fix the light (white balance + exposure).
LIGHT = [0, 1, 2]
# Color / mid-tone error below which the tone sliders join in.
LIGHT_SETTLED = 4.0

KELVIN_RANGE = (2000.0, 50000.0)
RANGES = {
    "Tint": (-150.0, 150.0),
    "Exposure2012": (-5.0, 5.0),
    "Shadows2012": (-100.0, 100.0),
    "Highlights2012": (-100.0, 100.0),
    "Whites2012": (-100.0, 100.0),
    "Blacks2012": (-100.0, 100.0),
}
JPEG_TEMP_RANGE = (-100.0, 100.0)

# Prior d(metric)/d(slider) in internal units (rows = METRIC_KEYS, cols = CORRECTIVE).
# Temperature column is per mired for raw; the sign flips because a higher
# mired means a cooler setting. For JPEG files Temperature is a -100..100 offset.
_PRIOR_RAW = np.array(
    [
        # temp(mired) tint  exposure shadows highlights whites blacks
        [-0.20, 0.00, 0.0, 0.00, 0.00, 0.00, 0.00],  # b*
        [0.00, 0.15, 0.0, 0.00, 0.00, 0.00, 0.00],  # a*
        [0.00, 0.00, 4.0, 0.03, 0.00, 0.00, 0.08],  # p1
        [0.00, 0.00, 14.0, 0.10, 0.01, 0.00, 0.02],  # p25
        [0.00, 0.00, 17.0, 0.05, 0.02, 0.01, 0.00],  # p50
        [0.00, 0.00, 14.0, 0.01, 0.06, 0.03, 0.00],  # p75
        [0.00, 0.00, 5.0, 0.00, 0.03, 0.08, 0.00],  # p99
    ]
)
_PRIOR_JPEG = _PRIOR_RAW.copy()
_PRIOR_JPEG[0, 0] = 0.30  # per unit of the -100..100 offset


@dataclass
class Proposal:
    sliders: dict
    done: bool
    residual: float  # match error of the latest render
    best_residual: float
    iterations: int


def _to_internal(sliders, is_raw):
    x = np.array([float(sliders.get(k, 0.0)) for k in CORRECTIVE])
    if is_raw:
        x[0] = 1e6 / max(x[0], 1.0)  # Kelvin -> mired
    return x


def _to_sliders(x, is_raw):
    x = x.copy()
    out = {}
    if is_raw:
        kelvin = 1e6 / max(x[0], 1e-6)
        out["Temperature"] = float(np.clip(kelvin, *KELVIN_RANGE))
    else:
        out["Temperature"] = float(np.clip(x[0], *JPEG_TEMP_RANGE))
    for i, key in enumerate(CORRECTIVE[1:], start=1):
        out[key] = float(np.clip(x[i], *RANGES[key]))
    out["Temperature"] = round(out["Temperature"]) if is_raw else round(out["Temperature"], 1)
    out["Exposure2012"] = round(out["Exposure2012"], 2)
    for key in ("Tint", "Shadows2012", "Highlights2012", "Whites2012", "Blacks2012"):
        out[key] = round(out[key], 1)
    return out


def _metric_vector(m):
    if isinstance(m, dict):
        m = Metrics.from_dict(m)
    return np.array([m.b, m.a] + [m.L[k] for k in METRIC_KEYS[2:]])


def match_error(ref, target):
    """One number for how far a render is from the reference (roughly ΔE00 units).

    Combines the color difference of the neutral axis (measured at mid-gray) with
    a weighted RMS of the L* percentile differences.
    """
    r = _metric_vector(ref)
    t = _metric_vector(target)
    color = float(delta_e_2000([50.0, r[1], r[0]], [50.0, t[1], t[0]]))
    w = METRIC_WEIGHTS[2:]
    tone = float(np.sqrt(np.sum(w * (t[2:] - r[2:]) ** 2) / np.sum(w)))
    return float(np.hypot(color, tone))


def _jacobian(history_x, history_m, is_raw):
    """Prior sensitivities refined by a Broyden update for every step seen so far."""
    J = (_PRIOR_RAW if is_raw else _PRIOR_JPEG) * SLIDER_SCALE  # per scaled unit
    for i in range(1, len(history_x)):
        du = (history_x[i] - history_x[i - 1]) / SLIDER_SCALE
        dm = history_m[i] - history_m[i - 1]
        denom = du @ du
        if denom < 1e-9:
            continue
        J = J + np.outer(dm - J @ du, du) / denom
    return J


def propose(ref, history, is_raw, tolerance=2.0, max_iterations=6, anchor=1.0, damping=0.05):
    """Return the next sliders to render, or the best ones found if done.

    ref: reference Metrics (or dict).
    history: list of {"sliders": {...}, "metrics": {...}}, one per render, oldest
        first. The first entry's sliders are the starting point (usually the
        reference's corrective values).
    anchor: multiplier on ANCHOR, the pull toward the starting sliders.
    damping: Levenberg-Marquardt style step damping.
    """
    if not history:
        raise ValueError("history needs at least one render")
    target = _metric_vector(ref)
    xs = [_to_internal(h["sliders"], is_raw) for h in history]
    ms = [_metric_vector(h["metrics"]) for h in history]
    errors = [match_error(ref, h["metrics"]) for h in history]
    best = int(np.argmin(errors))

    iterations = len(history) - 1
    if errors[-1] < tolerance or iterations >= max_iterations:
        return Proposal(
            sliders=_to_sliders(xs[best], is_raw),
            done=True,
            residual=errors[-1],
            best_residual=errors[best],
            iterations=iterations,
        )

    # If the last step made things worse, back off halfway toward the best render.
    if len(errors) > 1 and errors[-1] > errors[best] * 1.05:
        x_next = (xs[-1] + xs[best]) / 2
        return Proposal(
            sliders=_to_sliders(x_next, is_raw),
            done=False,
            residual=errors[-1],
            best_residual=errors[best],
            iterations=iterations,
        )

    J = _jacobian(xs, ms, is_raw)
    diff = ms[-1] - target
    # Fix the light first (white balance + exposure against color and mid-tones);
    # only once that's close do the tone sliders get to shape the rest.
    light_error = float(np.hypot(np.hypot(diff[0], diff[1]), diff[4]))
    active = LIGHT if light_error > LIGHT_SETTLED else list(range(len(CORRECTIVE)))

    W = np.diag(np.sqrt(METRIC_WEIGHTS))
    r = W @ diff
    A = (W @ J)[:, active]
    u_cur = xs[-1] / SLIDER_SCALE
    u_start = xs[0] / SLIDER_SCALE
    reg = np.diag(damping + anchor * ANCHOR[active])
    # minimize |A du + r|^2 + damping |du|^2 + anchor_i |u_cur + du - u_start|^2
    lhs = A.T @ A + reg
    rhs = -(A.T @ r) - anchor * ANCHOR[active] * (u_cur - u_start)[active]
    du = np.zeros(len(CORRECTIVE))
    du[active] = np.linalg.solve(lhs, rhs)
    # Cap the step so one bad sensitivity estimate can't fling a slider across its range.
    du = np.clip(du, -4.0, 4.0)
    x_next = (u_cur + du) * SLIDER_SCALE

    return Proposal(
        sliders=_to_sliders(x_next, is_raw),
        done=False,
        residual=errors[-1],
        best_residual=errors[best],
        iterations=iterations,
    )
