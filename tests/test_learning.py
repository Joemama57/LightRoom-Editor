import numpy as np
import pytest

from engine.learning import Learner, light_bucket
from engine.measure import measure
from engine.solver import CORRECTIVE, SLIDER_SCALE, default_prior
from tests.simulator import capture, make_scene, render, run_loop
from engine.solver import Options

REF = {"Temperature": 5500, "Tint": 0, "Exposure2012": 0, "Shadows2012": 20,
       "Highlights2012": -30, "Whites2012": 0, "Blacks2012": 0}


@pytest.fixture
def learner(tmp_path):
    return Learner(tmp_path / "learning.json")


def simulated_runs(learner, conditions, camera="Sony A7 IV"):
    """Run the closed loop on several photos and let the learner observe them."""
    ref = measure(render(capture(make_scene(seed=0), 5500), REF))
    iterations = []
    for kelvin, tint, stops in conditions:
        raw = capture(make_scene(seed=1)[4:, 2:], kelvin, tint, stops)
        prior, _ = learner.prior(camera, True)
        p, history = run_loop(ref, raw, dict(REF), options=Options(prior=prior))
        learner.observe_history(camera, True, history)
        iterations.append(p.iterations)
    learner.end_run()
    return iterations


def test_no_prior_until_enough_observations(learner):
    assert learner.prior("Sony A7 IV", True) == (None, None)


def test_learned_sensitivities_match_what_the_renderer_does(learner):
    simulated_runs(learner, [(3200, 0, 0), (7500, 5, 0.3), (5500, 0, -1.5), (4300, -8, 1.0), (2900, 0, -1.0)])
    J, source = learner.prior("Sony A7 IV", True)
    assert source == "raw|Sony A7 IV"
    # Measure the simulator's true response to +0.5 EV around the reference.
    raw = capture(make_scene(seed=1), 5500)
    m0 = measure(render(raw, REF))
    m1 = measure(render(raw, dict(REF, Exposure2012=0.5)))
    true_dp50 = (m1.L["p50"] - m0.L["p50"]) / 0.5
    learned, guess = J[4, 2], default_prior(True)[4, 2]
    assert abs(learned - true_dp50) < abs(guess - true_dp50)


def test_learning_cuts_passes_on_later_runs(learner):
    shoot = [(3200, 0, 0), (2900, 0, -1.0), (7500, 5, 0.3), (4300, -8, 1.0), (5500, 0, -1.5)]
    first = simulated_runs(learner, shoot)
    for _ in range(3):
        later = simulated_runs(learner, shoot)
    assert sum(later) < sum(first)


def test_camera_specific_then_falls_back_to_file_type(learner):
    simulated_runs(learner, [(3200, 0, 0), (7500, 5, 0.3), (5500, 0, -1.5), (2900, 0, -1.0)], camera="Canon R5")
    _, source = learner.prior("Nikon Z8", True)
    assert source == "raw|*"  # no Nikon data yet: use everything learned for raw files
    assert learner.prior("Nikon Z8", False) == (None, None)  # nothing for JPEGs


def test_persists_and_resets(learner, tmp_path):
    simulated_runs(learner, [(3200, 0, 0), (7500, 5, 0.3), (5500, 0, -1.5), (2900, 0, -1.0)])
    learner.save()
    again = Learner(tmp_path / "learning.json")
    assert again.prior("Sony A7 IV", True)[0] is not None
    assert again.data["runs"] == 1
    again.reset()
    assert Learner(tmp_path / "learning.json").prior("Sony A7 IV", True) == (None, None)


def test_corrupt_file_starts_fresh(tmp_path):
    path = tmp_path / "learning.json"
    path.write_text("{not json")
    assert Learner(path).data["runs"] == 0


# -- preferences --------------------------------------------------------------

MATCHED = {"Temperature": 3200, "Tint": 0, "Exposure2012": 0.5, "Shadows2012": 20,
           "Highlights2012": -30, "Whites2012": 0, "Blacks2012": 0}


def warmer(sliders, kelvin=200, exposure=0.0):
    return dict(sliders, Temperature=sliders["Temperature"] + kelvin,
                Exposure2012=sliders["Exposure2012"] + exposure)


def test_preference_needs_repeats_then_applies(learner):
    b = light_bucket(MATCHED, True)
    assert b == "warm light"
    assert learner.observe_correction("Sony", True, b, MATCHED, warmer(MATCHED))
    assert learner.preference("Sony", True, b) is None  # one correction is not a habit
    learner.observe_correction("Sony", True, b, MATCHED, warmer(MATCHED))
    learner.observe_correction("Sony", True, b, MATCHED, warmer(MATCHED))
    out, applied = learner.apply_preference(MATCHED, "Sony", True, b)
    assert out["Temperature"] > MATCHED["Temperature"]
    assert 0 < applied["Temperature"] <= 200
    # Only for this camera and kind of light.
    assert learner.preference("Sony", True, "daylight") is None
    assert learner.preference("Canon", True, b) is None


def test_inconsistent_corrections_apply_less(learner):
    b = "warm light"
    for _ in range(4):
        learner.observe_correction("A", True, b, MATCHED, warmer(MATCHED, 200))
    for k in (400, -100, 300, 100):
        learner.observe_correction("B", True, b, MATCHED, warmer(MATCHED, k))
    steady = learner.apply_preference(MATCHED, "A", True, b)[1]["Temperature"]
    noisy = learner.apply_preference(MATCHED, "B", True, b)[1]
    assert noisy is None or noisy.get("Temperature", 0) < steady


def test_big_changes_and_no_changes_are_not_taste(learner):
    b = "daylight"
    m = dict(MATCHED, Temperature=5500)
    assert not learner.observe_correction("A", True, b, m, dict(m, Exposure2012=3.0))  # a different edit
    assert not learner.observe_correction("A", True, b, m, m)  # untouched, and no preference yet


def test_kept_as_is_fades_a_preference(learner):
    b = "warm light"
    for _ in range(3):
        learner.observe_correction("A", True, b, MATCHED, warmer(MATCHED, 200))
    before = learner.preference("A", True, b)[0]
    for _ in range(4):
        assert learner.observe_correction("A", True, b, MATCHED, MATCHED, allow_zero=True)
    after = learner.preference("A", True, b)[0]
    assert abs(after) < abs(before) / 2


def test_summary_is_readable(learner):
    for _ in range(3):
        learner.observe_correction("Sony", True, "warm light", MATCHED, warmer(MATCHED, 200))
    s = learner.summary()
    pref = s["preferences"]["raw|Sony|warm light"]
    assert pref["applied"] and pref["corrections"] == 3
    assert pref["average_change"]["Temperature"].startswith("warmer by")
