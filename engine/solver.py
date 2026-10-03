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
SKIN_WEIGHTS = np.array([0.5, 0.5])
# Below this fraction of skin pixels the skin guard sits out for that photo.
MIN_SKIN_FRACTION = 0.01

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
# Default (Exposure EV, other tone sliders) radius around the starting values.
TONE_LIMITS = (2.0, 40.0)

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
class Options:
    """How a match is solved.

    prior: 7x7 sensitivities (rows METRIC_KEYS, cols CORRECTIVE, internal units)
        measured by `engine.calibrate` for this file type; None uses the built-in guess.
    skin: also match skin tones (when both photos have enough skin pixels).
    color_only: solve white balance only and leave each photo's exposure and
        tone sliders as they are ("keep each photo's own exposure intent").
    """

    prior: np.ndarray = None
    skin: bool = False
    color_only: bool = False
    # How far white balance may move from the starting point: (Temperature
    # radius, Tint radius). Temperature is in mireds for raw files and in
    # offset units for JPEGs. Keeps a frame dominated by one colour (foliage,
    # a red wall) from dragging the white balance somewhere absurd.
    wb_limits: tuple = (None, 40.0)
    # How far exposure (EV) and Shadows/Highlights/Whites/Blacks may move from
    # the starting point. Matching a dark interior to a bright exterior would
    # otherwise push exposure until the frame blows out.
    tone_limits: tuple = TONE_LIMITS
    # White balance and exposure only, matched on neutrals and mid-tone
    # brightness; the tone sliders stay put. Used when a fitted grade already
    # sets the tone, so content differences don't drive the tone sliders.
    light_only: bool = False


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


def _as_metrics(m):
    return Metrics.from_dict(m) if isinstance(m, dict) else m


def _has_skin(m):
    m = _as_metrics(m)
    return m.skin_a is not None and m.skin_fraction >= MIN_SKIN_FRACTION


def _metric_vector(m):
    m = _as_metrics(m)
    return np.array([m.b, m.a] + [m.L[k] for k in METRIC_KEYS[2:]])


def match_error(ref, target, skin=False, color_only=False, light_only=False):
    """One number for how far a render is from the reference (roughly ΔE00 units).

    Combines the color difference of the neutral axis (measured at mid-gray) with
    a weighted RMS of the L* percentile differences. With `skin`, the skin-tone
    color difference is averaged in with the neutral one (when both photos have
    skin). With `color_only`, tone is ignored.
    """
    r = _as_metrics(ref)
    t = _as_metrics(target)
    color = float(delta_e_2000([50.0, r.a, r.b], [50.0, t.a, t.b]))
    if skin and _has_skin(r) and _has_skin(t):
        skin_de = float(delta_e_2000([60.0, r.skin_a, r.skin_b], [60.0, t.skin_a, t.skin_b]))
        color = float(np.sqrt((color**2 + skin_de**2) / 2))
    if color_only:
        return color
    rv, tv = _metric_vector(r), _metric_vector(t)
    if light_only:
        return float(np.hypot(color, tv[4] - rv[4]))
    w = METRIC_WEIGHTS[2:]
    tone = float(np.sqrt(np.sum(w * (tv[2:] - rv[2:]) ** 2) / np.sum(w)))
    return float(np.hypot(color, tone))


def default_prior(is_raw):
    return (_PRIOR_RAW if is_raw else _PRIOR_JPEG).copy()


def _jacobian(history_x, history_m, is_raw, prior=None):
    """Prior sensitivities refined by a Broyden update for every step seen so far."""
    base = default_prior(is_raw) if prior is None else np.asarray(prior, dtype=float)
    J = base * SLIDER_SCALE  # per scaled unit
    for i in range(1, len(history_x)):
        du = (history_x[i] - history_x[i - 1]) / SLIDER_SCALE
        dm = history_m[i] - history_m[i - 1]
        denom = du @ du
        if denom < 1e-9:
            continue
        J = J + np.outer(dm - J @ du, du) / denom
    return J


def propose(ref, history, is_raw, tolerance=2.0, max_iterations=6, anchor=1.0, damping=0.05, options=None):
    """Return the next sliders to render, or the best ones found if done.

    ref: reference Metrics (or dict).
    history: list of {"sliders": {...}, "metrics": {...}}, one per render, oldest
        first. The first entry's sliders are the starting point (usually the
        reference's corrective values).
    anchor: multiplier on ANCHOR, the pull toward the starting sliders.
    damping: Levenberg-Marquardt style step damping.
    options: Options (calibrated prior, skin guard, color-only).
    """
    if not history:
        raise ValueError("history needs at least one render")
    opts = options or Options()
    target = _metric_vector(ref)
    xs = [_to_internal(h["sliders"], is_raw) for h in history]
    ms = [_metric_vector(h["metrics"]) for h in history]
    errors = [match_error(ref, h["metrics"], skin=opts.skin, color_only=opts.color_only, light_only=opts.light_only)
              for h in history]
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

    J = _jacobian(xs, ms, is_raw, opts.prior)
    diff = ms[-1] - target
    weights = METRIC_WEIGHTS.copy()
    # Fix the light first (white balance + exposure against color and mid-tones);
    # only once that's close do the tone sliders (and the skin guard) join in.
    light_error = float(np.hypot(np.hypot(diff[0], diff[1]), diff[4]))
    settled = light_error <= LIGHT_SETTLED
    if opts.color_only:
        # White balance only; tone metrics don't pull on anything.
        active = [0, 1]
        weights[2:] = 0.0
        settled = float(np.hypot(diff[0], diff[1])) <= LIGHT_SETTLED
    elif opts.light_only:
        active = LIGHT
        weights[2:] = 0.0
        weights[4] = 1.0  # mid-tone brightness only
    else:
        active = list(range(len(CORRECTIVE))) if settled else LIGHT

    # Skin guard: once the light is close, also pull skin tones toward the
    # reference's. Skin pixels are picked by color, so under a strong cast the
    # selection is unreliable; that's why it waits. Skin answers white balance
    # the way the neutral axis does, so it borrows those sensitivity rows.
    if opts.skin and settled and _has_skin(ref) and _has_skin(history[-1]["metrics"]):
        r_m, t_m = _as_metrics(ref), _as_metrics(history[-1]["metrics"])
        diff = np.concatenate([diff, [t_m.skin_b - r_m.skin_b, t_m.skin_a - r_m.skin_a]])
        J = np.vstack([J, J[[0, 1]]])
        weights = np.concatenate([weights, SKIN_WEIGHTS])

    W = np.diag(np.sqrt(weights))
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
    x_next = _limit_white_balance(x_next, xs[0], opts.wb_limits)
    x_next = _limit_tone(x_next, xs[0], opts.tone_limits)

    return Proposal(
        sliders=_to_sliders(x_next, is_raw),
        done=False,
        residual=errors[-1],
        best_residual=errors[best],
        iterations=iterations,
    )


def _limit_white_balance(x, x_start, limits):
    temp_radius, tint_radius = limits
    x = x.copy()
    if temp_radius is not None:
        x[0] = np.clip(x[0], x_start[0] - temp_radius, x_start[0] + temp_radius)
    if tint_radius is not None:
        x[1] = np.clip(x[1], x_start[1] - tint_radius, x_start[1] + tint_radius)
    return x


def _limit_tone(x, x_start, limits):
    if limits is None:
        return x
    exposure_radius, tone_radius = limits
    x = x.copy()
    x[2] = np.clip(x[2], x_start[2] - exposure_radius, x_start[2] + exposure_radius)
    x[3:] = np.clip(x[3:], x_start[3:] - tone_radius, x_start[3:] + tone_radius)
    return x


def at_tone_limit(start, sliders, is_raw, limits=TONE_LIMITS):
    """True when the solved tone sliders sit on the edge of their allowed range."""
    if limits is None:
        return False
    d = np.abs(_to_internal(sliders, is_raw) - _to_internal(start, is_raw))
    return bool(d[2] >= limits[0] - 0.01 or np.any(d[3:] >= limits[1] - 0.1))


def wb_limits(is_raw, wb_from_camera):
    """Default white-balance limits. Starting from the camera's own white
    balance, a match needs only the reference's creative offset plus a small
    correction; starting from the reference's values, the light may differ a lot."""
    if wb_from_camera:
        return (80.0 if is_raw else 30.0, 30.0)
    if not is_raw:
        # A JPEG already has its white balance baked in; it needs a nudge, not
        # a full re-balance (frames full of coloured light swing wildly otherwise).
        return (30.0, 20.0)
    return (None, 40.0)


def blend(start, best, strength, is_raw):
    """Move `strength` (0..1) of the way from the starting sliders to the solved ones.

    Temperature is blended in mireds for raw files, so 50% sits halfway in how
    the shift looks rather than halfway in Kelvin.
    """
    x0 = _to_internal(start, is_raw)
    x1 = _to_internal(best, is_raw)
    return _to_sliders(x0 + strength * (x1 - x0), is_raw)


def clamp(sliders, is_raw):
    """Round and clamp sliders to Lightroom's ranges."""
    return _to_sliders(_to_internal(sliders, is_raw), is_raw)
