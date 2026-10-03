"""The opt-in subject-first colour options: face skin, skin-weighted error, skin white balance."""

import numpy as np
import pytest

import engine.faces as faces
from engine import subject
from engine.colorspace import delta_e_2000, lab_to_srgb
from engine.measure import Metrics, measure
from engine.solver import Options, match_error, propose, solve_error
from tests.simulator import capture, render, run_loop
from tests.test_workflow import lightroom  # noqa: F401  (fixture)

SKIN = (0.42, 0.24, 0.15)
SAND = (0.50, 0.40, 0.26)
GREY = (0.30, 0.30, 0.30)
FACE = [(40 / 96, 20 / 96, 54 / 96, 36 / 96)]  # where the "face" patch sits in `scene`
START = {"Temperature": 5500, "Tint": 0, "Exposure2012": 0, "Shadows2012": 0, "Highlights2012": 0,
         "Whites2012": 0, "Blacks2012": 0}


def scene(sand, grey=True, size=96, seed=0):
    """A linear-light frame: grey (or dark) backdrop, `sand` of it sand from the bottom, one face patch."""
    rng = np.random.default_rng(seed)
    s = np.zeros((size, size, 3))
    s[:] = GREY if grey else (0.2, 0.2, 0.21)
    s[size - int(size * sand):] = SAND
    s[20:36, 40:54] = SKIN
    s *= 1 + 0.02 * rng.standard_normal(s.shape)
    return np.clip(s, 0, None)


@pytest.fixture
def face_at_patch(monkeypatch):
    monkeypatch.setattr(faces, "detect", lambda img: FACE)
    monkeypatch.setattr(faces, "detect_file", lambda path: FACE)


def test_options_are_off_unless_a_run_turns_them_on():
    assert Options().skin_error is False and Options().skin_wb is False and not faces.active()
    with subject.use(face_skin=True, skin_error=True, skin_wb=True):
        o = Options()
        assert o.skin_error and o.skin_wb and faces.active()
    assert Options().skin_wb is False and not faces.active()


def test_face_skin_measures_the_face_not_the_sand(face_at_patch):
    img = render(capture(scene(0.8), 5500), START)
    by_colour = measure(img)
    with subject.use(face_skin=True):
        by_face = measure(img)
    face_lab = measure(img[20:36, 40:54])
    assert by_colour.skin_fraction > 4 * by_face.skin_fraction  # sand reads as skin by colour alone
    assert by_face.skin_source == "faces"
    assert float(delta_e_2000([by_face.skin_L, by_face.skin_a, by_face.skin_b],
                              [face_lab.skin_L, face_lab.skin_a, face_lab.skin_b])) < 1.0


def test_no_face_means_no_skin_term(monkeypatch):
    monkeypatch.setattr(faces, "detect", lambda img: [])
    with subject.use(face_skin=True):
        m = measure(render(capture(scene(0.8), 5500), START))
    assert m.skin_a is None and m.skin_source == "faces"


def test_without_opencv_skin_falls_back_to_colour(monkeypatch):
    monkeypatch.setattr(faces, "_detectors", lambda: None)
    img = render(capture(scene(0.8), 5500), START)
    with subject.use(face_skin=True):
        m = measure(img)
        note = faces.fallback_warning()
    assert m.skin_source is None and m.skin_a == measure(img).skin_a
    assert note and "requirements-faces.txt" in note


def test_detector_ignores_frames_without_faces():
    if not faces.available():
        pytest.skip("OpenCV with its bundled face models isn't installed (requirements-faces.txt)")
    img = render(capture(scene(0.5), 5500), START)
    assert faces.detect(img) == []


def _metrics(a, b, skin=None, source="faces"):
    L = {"p1": 5.0, "p25": 30.0, "p50": 50.0, "p75": 70.0, "p99": 95.0}
    if skin is None:
        return Metrics(a=a, b=b, L=L, clipped_fraction=0.0, neutral_fraction=0.3)
    return Metrics(a=a, b=b, L=L, clipped_fraction=0.0, neutral_fraction=0.3, skin_L=skin[0], skin_a=skin[1],
                   skin_b=skin[2], skin_fraction=0.005, skin_source=source)


def test_skin_weighted_error_counts_faces_over_background():
    ref = _metrics(0, 0, (55, 12, 20))
    face_off = _metrics(0, 0, (55, 12, 26))
    background_off = _metrics(0, 6, (55, 12, 20))
    # Against the plain skin error (faces and neutrals counted equally), a face
    # gap counts twice as much relative to a background gap.
    plain = match_error(ref, face_off, skin=True, color_only=True) / match_error(ref, background_off, skin=True,
                                                                               color_only=True)
    weighted = subject.subject_colour(ref, face_off) / subject.subject_colour(ref, background_off)
    assert weighted == pytest.approx(2 * plain, rel=0.01)
    # Without skin on both, the plain error is used.
    assert subject.subject_colour(ref, _metrics(0, 0)) is None
    opts = Options(skin=True, skin_error=True)
    assert solve_error(ref, _metrics(0, 3), opts) == match_error(ref, _metrics(0, 3), skin=True)
    # Faces at 0.5% of the frame count when they come from detected faces, not when picked by colour.
    assert subject.has_skin(face_off) and not subject.has_skin(_metrics(0, 0, (55, 12, 26), source=None))


def test_skin_rows_split_hue_from_chroma():
    ref = _metrics(0, 0, (55, 10, 20))
    more_chroma = _metrics(0, 0, (55, 12, 24))  # same hue, more chroma
    diff, rows, w = subject.skin_rows(ref, more_chroma, np.eye(2))
    assert abs(diff[0]) < 1e-9 and diff[1] > 0
    assert w[0] > w[1]  # hue first


def test_neutral_guard_shortens_a_step_that_would_push_neutrals_far():
    J = np.zeros((2, 7))
    J[0, 0] = -10.0  # b* per scaled Temperature unit
    scale = np.ones(7)
    x_cur, x_next = np.zeros(7), np.zeros(7)
    x_next[0], x_next[2] = -3.0, 0.5  # would move neutral b* by +30; exposure untouched by the guard
    out = subject.guard_neutrals(x_next, x_cur, np.array([0.0, 0.0]), np.array([0.0, 0.0]), J, scale)
    assert out[0] == pytest.approx(-subject.NEUTRAL_GUARD / 10, abs=0.01)
    assert out[2] == 0.5


def test_skin_white_balance_beats_neutrals_on_a_sand_frame(face_at_patch):
    """Reference: little sand and a grey backdrop at 5500 K. Target: mostly sand,
    no true grey, shot under 3600 K light. Neutral-based white balance takes the
    sand for grey; skin white balance gets the faces (and the light) right."""
    results = {}
    for mode, flags in {"default": {}, "skin_wb": {"face_skin": True, "skin_wb": True}}.items():
        with subject.use(**flags):
            ref = measure(render(capture(scene(0.2), 5500), START))
            raw = capture(scene(0.85, grey=False), 3600)
            p, _ = run_loop(ref, raw, START, options=Options(skin=bool(flags)))
            with subject.use(face_skin=True):
                ref_face = measure(render(capture(scene(0.2), 5500), START))
                m = measure(render(raw, p.sliders), (ref.a, ref.b))
        skin_de = float(delta_e_2000([60, ref_face.skin_a, ref_face.skin_b], [60, m.skin_a, m.skin_b]))
        results[mode] = (p.sliders, skin_de, subject.neutral_gap(ref_face, m))
    assert results["default"][1] > 5  # the sand pulled the faces off
    assert results["skin_wb"][1] < 1.5
    assert abs(1e6 / results["skin_wb"][0]["Temperature"] - 1e6 / 3600) < 20  # within 20 mired of the true light
    assert results["skin_wb"][2] <= subject.NEUTRAL_GUARD


def test_skin_white_balance_needs_faces():
    # Colour-picked skin doesn't steer white balance: the step is the plain neutral one.
    ref = _metrics(0, 0, (55, 12, 20), source=None)
    ref.skin_fraction = 0.2
    start = dict(START)
    cur = _metrics(0, 8, (55, 12, 30), source=None)
    cur.skin_fraction = 0.2
    history = [{"sliders": start, "metrics": cur.to_dict()}]
    plain = propose(ref, history, is_raw=True, options=Options(skin=True))
    skin_wb = propose(ref, history, is_raw=True, options=Options(skin=True, skin_wb=True))
    assert plain.sliders == skin_wb.sliders


def test_match_with_subject_options_end_to_end(lightroom, tmp_path, face_at_patch):  # noqa: F811
    from engine.workflow import run_match

    report = run_match(lightroom, tmp_path, skin_wb=True, skin_error=True, log=lambda m: None)
    assert report["options"]["skin"] is True
    assert report["options"]["subject"]["face_skin"] and report["options"]["subject"]["skin_wb"]
    assert len(report["photos"]) == 3
    assert not faces.active() and Options().skin_wb is False  # nothing leaks past the run
