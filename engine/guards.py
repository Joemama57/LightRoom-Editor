"""Flags for photos the agent shouldn't silently force into the look."""

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
