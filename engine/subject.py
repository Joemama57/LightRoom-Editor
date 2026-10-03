"""Subject-first colour options for a match. All three are opt-in and off by default.

- face_skin (`--face-skin`): skin is measured inside detected faces only
  (engine/faces.py), not from every skin-coloured pixel.
- skin_error (`--skin-error`): the solver judges each render with a
  skin-weighted CIEDE2000 error: the faces count SKIN_WEIGHT times as much as
  the neutral background, and skin is compared at its own lightness.
- skin_wb (`--skin-wb`, turns on face_skin): white balance is solved toward the reference's skin
  hue (first) and chroma (second) instead of toward "neutral" pixels, which on
  a wedding are often sand, dry grass or cream fabric. The neutrals stay in
  as a weak second target and a guard: a step may not move them more than
  NEUTRAL_GUARD (a*b* units) from the reference's, or further than they
  already were.

The solver reads the options through `current()`; `use(...)` sets them for one
run, so code that doesn't ask for them behaves exactly as before.
"""

from contextlib import contextmanager
from dataclasses import asdict, dataclass

import numpy as np

from . import faces
from .colorspace import delta_e_2000

SKIN_WEIGHT = 4.0  # faces vs neutral background in the skin-weighted error
BACKGROUND_WEIGHT = 1.0
# Skin white balance: weight of the hue and chroma parts of the skin gap, and
# of the neutral rows, which stay in as a weak second target.
HUE_WEIGHT = 1.0
CHROMA_WEIGHT = 0.15
NEUTRAL_WEIGHT = 0.1
# Neutrals may sit this far (a*b*) from the reference's before a skin step is cut back.
# Loose on purpose: on the beach run the "neutrals" (sand, dry grass) of a
# correctly balanced frame read b* +12.8, so a tight guard would undo the point.
NEUTRAL_GUARD = 10.0
# Skin measured inside faces is trusted at a much smaller share of the frame
# than colour-picked skin (faces are often 0.5-3% of a wedding frame).
FACE_MIN_FRACTION = 0.001
COLOUR_MIN_FRACTION = 0.01


@dataclass(frozen=True)
class SubjectOptions:
    face_skin: bool = False
    skin_error: bool = False
    skin_wb: bool = False

    def report(self):
        return asdict(self)


_current = [SubjectOptions()]


def current():
    return _current[-1]


@contextmanager
def use(face_skin=False, skin_error=False, skin_wb=False):
    """Turn the options on for everything inside the block (one run)."""
    _current.append(SubjectOptions(bool(face_skin), bool(skin_error), bool(skin_wb)))
    try:
        with faces.enabled(face_skin):
            yield current()
    finally:
        _current.pop()


def _get(m, key):
    return m.get(key) if isinstance(m, dict) else getattr(m, key, None)


def has_skin(m):
    """Enough skin to steer by: faces at FACE_MIN_FRACTION, colour-picked skin at COLOUR_MIN_FRACTION."""
    if _get(m, "skin_a") is None:
        return False
    floor = FACE_MIN_FRACTION if _get(m, "skin_source") == "faces" else COLOUR_MIN_FRACTION
    return (_get(m, "skin_fraction") or 0.0) >= floor


def _skin_lab(m):
    L = _get(m, "skin_L")
    return [60.0 if L is None else L, _get(m, "skin_a"), _get(m, "skin_b")]


def neutral_gap(ref, target):
    """a*b* distance between the two photos' neutral axes."""
    return float(np.hypot(_get(target, "a") - _get(ref, "a"), _get(target, "b") - _get(ref, "b")))


def subject_colour(ref, target, guard=False):
    """Skin-weighted CIEDE2000 colour error, or None when either photo has no skin.

    Faces count SKIN_WEIGHT times the neutral axis; skin is compared at its own
    lightness, so darker faces count too. With `guard`, neutrals drifting more
    than NEUTRAL_GUARD from the reference's add a penalty (skin white balance).
    """
    if not (has_skin(ref) and has_skin(target)):
        return None
    neutral = float(delta_e_2000([50.0, _get(ref, "a"), _get(ref, "b")], [50.0, _get(target, "a"), _get(target, "b")]))
    skin = float(delta_e_2000(_skin_lab(ref), _skin_lab(target)))
    total = SKIN_WEIGHT + BACKGROUND_WEIGHT
    colour = float(np.sqrt((SKIN_WEIGHT * skin**2 + BACKGROUND_WEIGHT * neutral**2) / total))
    if guard:
        colour = float(np.hypot(colour, max(neutral_gap(ref, target) - NEUTRAL_GUARD, 0.0)))
    return colour


def skin_jacobian(xs, history, base, scale):
    """How skin b*, a* answer the sliders: `base` (the prior neutral rows, as
    skin answers white balance much as neutrals do) refined by a Broyden update
    for every pair of renders that both measured skin. Kept apart from the
    neutral rows, which jump when the neutral pixels change (sand vs shade)."""
    J = np.array(base, dtype=float)
    prev = None
    for x, m in zip(xs, history):
        if not has_skin(m):
            continue
        v = np.array([_get(m, "skin_b"), _get(m, "skin_a")])
        if prev is not None:
            du = (x - prev[0]) / scale
            if du @ du > 1e-9:
                J = J + np.outer(v - prev[1] - J @ du, du) / (du @ du)
        prev = (x, v)
    return J


def skin_rows(ref, target, J):
    """Skin white-balance residual and sensitivities, or None without skin on both.

    Returns (diff, rows, weights): the target's skin gap split into its hue part
    (across the reference's skin hue) and chroma part (along it), with the
    matching rows of J, the skin's own (b*, a*) sensitivities (skin_jacobian).
    """
    if not (has_skin(ref) and has_skin(target)):
        return None
    ra, rb = _get(ref, "skin_a"), _get(ref, "skin_b")
    h = np.arctan2(rb, ra)
    # Unit vectors in (b*, a*) order, the solver's metric order.
    across = np.array([np.cos(h), -np.sin(h)])  # hue direction
    along = np.array([np.sin(h), np.cos(h)])  # chroma direction
    P = np.vstack([across, along])
    gap = np.array([_get(target, "skin_b") - rb, _get(target, "skin_a") - ra])
    return P @ gap, P @ J, np.array([HUE_WEIGHT, CHROMA_WEIGHT])


def guard_neutrals(x_next, x_cur, neutral_now, neutral_ref, J, scale, wb=(0, 1)):
    """Shorten the white-balance part of a step whose predicted neutrals would
    land further than NEUTRAL_GUARD (or than they are now, if further) from the reference's.

    neutral_now / neutral_ref: (b*, a*) of the latest render and the reference.
    J: sensitivities per scaled slider unit; scale: SLIDER_SCALE.
    """
    wb = list(wb)
    allowed = max(NEUTRAL_GUARD, float(np.hypot(*(np.asarray(neutral_now) - neutral_ref))))

    def predicted(t):
        x = x_cur.copy()
        x[wb] = x_cur[wb] + t * (x_next[wb] - x_cur[wb])
        moved = J[[0, 1]] @ ((x - x_cur) / scale)
        return float(np.hypot(*(np.asarray(neutral_now) + moved - neutral_ref)))

    if predicted(1.0) <= allowed + 1e-9:
        return x_next
    lo, hi = 0.0, 1.0
    for _ in range(30):
        mid = (lo + hi) / 2
        lo, hi = (mid, hi) if predicted(mid) <= allowed else (lo, mid)
    out = x_next.copy()
    out[wb] = x_cur[wb] + lo * (x_next[wb] - x_cur[wb])
    return out
