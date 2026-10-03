"""Flags for photos the agent shouldn't silently force into the look."""

import numpy as np

MOSTLY_CLIPPED = 0.4  # fraction of clipped pixels
LOW_NEUTRALS = 0.02  # fraction of near-neutral pixels used for white balance


def flags_for(metrics, proposal, tolerance):
    flags = []
    if metrics.clipped_fraction > MOSTLY_CLIPPED:
        flags.append("mostly_clipped")
    if metrics.neutral_fraction < LOW_NEUTRALS:
        flags.append("low_neutral_confidence")
    if proposal.done and proposal.best_residual >= tolerance:
        flags.append("not_converged")
    return flags



# Mid-tone brightness gap (L*) and neutral-colour gap (a*b*) that, together,
# mean a frame is a different kind of scene rather than different light.
SCENE_GAP = 20.0
CAST_GAP = 20.0


def different_scene(ref, target):
    """A dark, coloured-light frame (an LED-lit car interior, a stage) against
    a normally lit reference. Its brightness is content, not exposure, and what
    passes for "neutral" in it is really a coloured surface (orange leather),
    so forcing it to match blows it out or tints it. Both gaps are needed: an
    underexposed or tungsten frame of an ordinary scene has only one of them."""
    gap = abs(target.L["p50"] - ref.L["p50"])
    cast = float(np.hypot(target.a - ref.a, target.b - ref.b))
    return gap > SCENE_GAP and (cast > CAST_GAP or target.neutral_fraction < LOW_NEUTRALS)
