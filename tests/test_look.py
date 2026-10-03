import numpy as np
import pytest

from engine import look
from engine.colorspace import srgb_to_linear
from engine.workflow import run_match
from tests.fake_lightroom import FakeLightroom
from tests.simulator import capture, make_scene, render

BAKED_LOOK = {"Contrast2012": 18, "SaturationAdjustmentBlue": 25, "Vibrance": 12,
              "SplitToningShadowHue": 200, "SplitToningShadowSaturation": 10}


def quiet(_msg):
    pass


def sky_scene(seed=0, size=96, sky=True):
    s = make_scene(seed, size)
    if sky:
        s[: size // 3] = np.array([0.12, 0.20, 0.42]) * (1 + 0.1 * np.linspace(0, 1, size)[None, :, None])
    return s


def baked(scene_raw, settings):
    """An exported JPEG: the look is in the pixels and its develop settings are empty."""
    return srgb_to_linear(render(scene_raw, settings))


def photo(raw, name, fmt="JPG", settings=None):
    return {"raw": raw, "fileName": name, "fileFormat": fmt, "cameraModel": "iPhone",
            "settings": settings if settings is not None else {}}


def test_bands_cover_the_hue_wheel():
    w = look.band_weights(np.linspace(0, 359, 360))
    assert np.allclose(w.sum(axis=1), 1.0)
    assert w[240].argmax() == look.BANDS.index("Blue") and w[30].argmax() == look.BANDS.index("Orange")


def test_sky_is_measured_in_the_blue_band():
    m = look.measure_look(render(capture(sky_scene(0), 5500), {}))
    assert m["bands"]["Blue"]["f"] > 0.15
    plain = look.measure_look(render(capture(sky_scene(0, sky=False), 5500), {}))
    assert plain["bands"]["Blue"]["f"] < m["bands"]["Blue"]["f"] / 3


def test_split_toning_hue_conversion_round_trips():
    for h in (0, 45, 100, 200, 260, 330):
        assert look.lab_hue_to_hsv(look.hsv_hue_to_lab(h)) == pytest.approx(h, abs=0.5)


@pytest.fixture
def baked_set():
    ref_raw = baked(capture(sky_scene(0), 5500), BAKED_LOOK)
    return FakeLightroom({
        "ref": photo(ref_raw, "IMG_1964 copy.jpg"),
        "warm": photo(capture(sky_scene(1)[4:, 2:], 5000, 0, -0.3), "IMG_1961.JPG"),
        "cool": photo(capture(sky_scene(2)[2:, 4:], 6800, 3, 0.2), "IMG_1966.JPG"),
    }, active="ref")


def test_baked_reference_look_is_matched_from_pixels(baked_set, tmp_path):
    report = run_match(baked_set, tmp_path, log=quiet)
    assert report["baked_reference"]
    ref_look = look.measure_look(render(baked_set.photos["ref"]["raw"], {}))
    for p in report["photos"]:
        assert p["look_final_error"] < p["look_start_error"] / 2, p
        assert p["look_final_error"] < 1.5, p
        assert p["look_settings"].get("SaturationAdjustmentBlue", 0) > 5  # the sky got the reference's blue
        final = look.measure_look_file(p["preview"])
        assert final["bands"]["Blue"]["c"] == pytest.approx(ref_look["bands"]["Blue"]["c"], abs=2.5)
        assert "look_limited" not in p["flags"]
        assert p["look_note"]


def test_no_look_keeps_todays_behaviour(baked_set, tmp_path):
    report = run_match(baked_set, tmp_path, look=False, log=quiet)
    for p in report["photos"]:
        assert p["look_settings"] == {} and p["look_final_error"] is None
        assert not any(k in baked_set.photos[p["id"]]["settings"] for k in look.LOOK_KEYS)


def test_band_missing_from_a_photo_is_left_alone(tmp_path):
    closeup = sky_scene(1, sky=False)
    bluish = closeup[..., 2] > closeup[..., 0] * 1.5
    closeup[bluish] = closeup[bluish].mean(axis=-1, keepdims=True)  # no sky, no blue anything
    lr = FakeLightroom({
        "ref": photo(baked(capture(sky_scene(0), 5500), BAKED_LOOK), "ref.jpg"),
        "closeup": photo(capture(closeup, 5200), "closeup.JPG"),
    }, active="ref")
    report = run_match(lr, tmp_path, log=quiet)
    (p,) = report["photos"]
    assert not any(k.endswith("Blue") for k in p["look_settings"])


def test_offsets_are_limited_and_flagged(tmp_path):
    extreme = {"SaturationAdjustmentBlue": 100, "SaturationAdjustmentAqua": 100, "Saturation": 60}
    lr = FakeLightroom({
        "ref": photo(baked(capture(sky_scene(0), 5500), extreme), "ref.jpg"),
        "t": photo(capture(sky_scene(1)[4:, 2:], 5500), "t.JPG"),
    }, active="ref")
    report = run_match(lr, tmp_path, log=quiet)
    (p,) = report["photos"]
    assert all(abs(v) <= look.LIMIT + 0.05 for k, v in p["look_settings"].items() if not k.startswith("SplitToning"))
    assert "look_limited" in p["flags"]
    assert lr.labels == {"t": "yellow"}


def test_reference_edited_in_lightroom_is_not_baked():
    assert look.is_baked({"ProcessVersion": "11.0", "CameraProfile": "Adobe Standard"})
    assert not look.is_baked({"SaturationAdjustmentBlue": 10})
    assert not look.is_baked({"ToneCurvePV2012": [0, 10, 128, 140, 255, 255]})
