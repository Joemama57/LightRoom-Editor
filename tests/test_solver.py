import numpy as np
import pytest

import engine.solver as solver
from engine.measure import measure
from engine.solver import match_error, propose
from tests.simulator import capture, make_scene, render, run_loop

# The reference: shot in daylight, graded with a little shadow lift and highlight recovery.
REF_SLIDERS = {
    "Temperature": 5500,
    "Tint": 0,
    "Exposure2012": 0,
    "Shadows2012": 20,
    "Highlights2012": -30,
    "Whites2012": 0,
    "Blacks2012": 0,
}

# Other frames from the same shoot: same subject, slightly different framing,
# different light. (kelvin, tint, stops)
CONDITIONS = {
    "tungsten": (3200, 0, 0.0),
    "shade": (7500, 5, 0.0),
    "underexposed": (5500, 0, -1.5),
    "warm_overexposed": (4300, -8, 1.0),
    "tungsten_underexposed": (2900, 0, -1.0),
}


@pytest.fixture(scope="module")
def reference():
    return measure(render(capture(make_scene(seed=0), 5500), REF_SLIDERS))


def other_frame(kelvin, tint, stops):
    return capture(make_scene(seed=1)[4:, 2:], kelvin, tint, stops)


@pytest.mark.parametrize("name", CONDITIONS)
def test_converges_to_reference(reference, name):
    raw = other_frame(*CONDITIONS[name])
    proposal, history = run_loop(reference, raw, REF_SLIDERS)
    start_error = match_error(reference, history[0]["metrics"])
    assert start_error > 5, "pasting the reference's settings should look clearly off"
    assert proposal.done
    assert proposal.best_residual < 2.0
    assert proposal.iterations <= 6


def test_recovers_white_balance_and_exposure(reference):
    raw = other_frame(3200, 0, -1.0)
    proposal, _ = run_loop(reference, raw, REF_SLIDERS)
    assert proposal.sliders["Temperature"] == pytest.approx(3200, rel=0.06)
    assert proposal.sliders["Exposure2012"] == pytest.approx(1.0, abs=0.25)


@pytest.mark.parametrize("scale", [0.5, 2.0])
def test_tolerates_wrong_prior_sensitivities(reference, monkeypatch, scale):
    # Lightroom's real slider response is unknown; the loop must learn it.
    monkeypatch.setattr(solver, "_PRIOR_RAW", solver._PRIOR_RAW * scale)
    raw = other_frame(3200, 0, -1.0)
    proposal, _ = run_loop(reference, raw, REF_SLIDERS)
    assert proposal.best_residual < 2.0


def test_already_matching_is_done_immediately(reference):
    raw = capture(make_scene(seed=0), 5500)
    proposal, history = run_loop(reference, raw, REF_SLIDERS)
    assert proposal.done and len(history) == 1
    assert proposal.sliders == pytest.approx(REF_SLIDERS)


def test_returns_best_render_when_out_of_iterations(reference):
    raw = other_frame(3200, 0, 0.0)
    proposal, history = run_loop(reference, raw, REF_SLIDERS, max_iterations=1)
    assert len(history) == 2
    errors = [match_error(reference, h["metrics"]) for h in history]
    assert proposal.best_residual == pytest.approx(min(errors))


def test_jpeg_temperature_stays_in_offset_range(reference):
    hist = [{"sliders": dict(REF_SLIDERS, Temperature=0), "metrics": measure(np.full((8, 8, 3), [0.9, 0.5, 0.1])).to_dict()}]
    p = propose(reference, hist, is_raw=False)
    assert -100 <= p.sliders["Temperature"] <= 100


def test_sliders_clamped_to_lightroom_ranges(reference):
    dark = measure(np.full((8, 8, 3), 0.001)).to_dict()
    hist = [{"sliders": REF_SLIDERS, "metrics": dark}]
    p = propose(reference, hist, is_raw=True)
    assert -5 <= p.sliders["Exposure2012"] <= 5
    assert 2000 <= p.sliders["Temperature"] <= 50000
    for key in ("Tint", "Shadows2012", "Highlights2012", "Whites2012", "Blacks2012"):
        assert -150 <= p.sliders[key] <= 150


def test_empty_history_rejected(reference):
    with pytest.raises(ValueError):
        propose(reference, [], is_raw=True)


def test_jpeg_white_balance_and_tone_are_limited():
    from engine.solver import TONE_LIMITS, _limit_tone, wb_limits
    assert wb_limits(False, False) == (30.0, 20.0)
    start = np.zeros(7)
    x = _limit_tone(np.array([0, 0, 4.5, 90, -90, 10, 0.0]), start, TONE_LIMITS)
    assert x[2] == TONE_LIMITS[0] and x[3] == TONE_LIMITS[1] and x[4] == -TONE_LIMITS[1] and x[5] == 10
