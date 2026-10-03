import json
import numpy as np
import pytest

from engine import look
from engine.colorspace import delta_e_2000, srgb_to_lab, srgb_to_linear
from PIL import Image

from engine.workflow import find_original, run_match
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


def scenes():
    return {
        "warm": photo(capture(sky_scene(1)[4:, 2:], 5000, 0, -0.3), "IMG_1961.JPG"),
        "cool": photo(capture(sky_scene(2)[2:, 4:], 6200, 3, 0.2), "IMG_1966.JPG"),
    }


@pytest.fixture
def pair_set():
    """An exported, edited reference plus its unedited original in the selection."""
    original = capture(sky_scene(0), 5500)
    return FakeLightroom({
        "ref": photo(baked(original, BAKED_LOOK), "IMG_1964 copy.jpg"),
        "orig": photo(original, "IMG_1964.JPG"),
        **scenes(),
    }, active="ref")


def test_grade_is_learned_from_the_original_and_shared(pair_set, tmp_path):
    report = run_match(pair_set, tmp_path, log=quiet)
    fit = report["grade_fit"]
    assert report["baked_reference"] and fit["original"] == "IMG_1964.JPG"
    assert fit["delta_e_after"] < fit["delta_e_before"] / 2 and fit["delta_e_after"] < 1.0, fit
    g = fit["look_settings"]
    # Several slider mixes give the same look; what matters is that the fitted
    # grade does to a different scene what the real edit would have done.
    for scene in (sky_scene(3)[6:, :], sky_scene(4, sky=False)):
        raw = capture(scene, 5500)
        truth = srgb_to_lab(render(raw, BAKED_LOOK).reshape(-1, 3))
        fitted = delta_e_2000(truth, srgb_to_lab(render(raw, g).reshape(-1, 3))).mean()
        unedited = delta_e_2000(truth, srgb_to_lab(render(raw, {}).reshape(-1, 3))).mean()
        assert fitted < unedited / 1.8, (fitted, unedited)
    # One look for every photo; only the light differs.
    looks = [{k: v for k, v in pair_set.photos[pid]["settings"].items() if k in look.LOOK_KEYS}
             for pid in ("orig", "warm", "cool")]
    assert looks[0] == looks[1] == looks[2] == g
    for p in report["photos"]:
        assert p["final_error"] < 2.0, p
        assert p["look_settings"] == {}  # no per-photo look stage
    assert not report["options"]["look_per_photo"]


def test_original_named_explicitly(pair_set, tmp_path):
    pair_set.photos["orig"]["fileName"] = "unrelated_name.JPG"
    report = run_match(pair_set, tmp_path, original="unrelated_name", log=quiet)
    assert report["grade_fit"]["original"] == "unrelated_name.JPG"


def test_find_original_by_name():
    photos = [{"fileName": "IMG_1964.JPG"}, {"fileName": "DSC1.ARW"}, {"fileName": "IMG_1965.JPG"}]
    assert find_original({"fileName": "IMG_1964 copy.jpg"}, photos)["fileName"] == "IMG_1964.JPG"
    assert find_original({"fileName": "IMG_1964 copy 2.jpg"}, photos)["fileName"] == "IMG_1964.JPG"
    assert find_original({"fileName": "DSC1-Edit.tif"}, photos)["fileName"] == "DSC1.ARW"
    assert find_original({"fileName": "IMG_9999 copy.jpg"}, photos) is None
    assert find_original({"fileName": "IMG_1965.JPG"}, photos[:2]) is None  # not a copy of anything


def test_without_the_original_colour_is_matched_conservatively(tmp_path):
    lr = FakeLightroom({"ref": photo(baked(capture(sky_scene(0), 5500), BAKED_LOOK), "IMG_1964 copy.jpg"),
                        **scenes()}, active="ref")
    report = run_match(lr, tmp_path, log=quiet)
    assert report["grade_fit"] is None and report["options"]["look_per_photo"]
    assert any("original" in w for w in report["warnings"])
    for p in report["photos"]:
        assert p["look_final_error"] <= p["look_start_error"] + 1e-9
        assert all(abs(v) <= 12.05 for k, v in p["look_settings"].items())
        assert not any(k in look.TONE_KEYS or k.startswith("SplitToning") for k in p["look_settings"])
        assert p["final_error"] < 2.0, p


def test_no_look_keeps_light_only(pair_set, tmp_path):
    report = run_match(pair_set, tmp_path, look=False, log=quiet)
    assert report["grade_fit"] is None
    for p in report["photos"]:
        assert p["look_settings"] == {} and p["look_final_error"] is None
        assert not any(k in pair_set.photos[p["id"]]["settings"] for k in look.LOOK_KEYS)


def test_band_missing_from_a_photo_is_left_alone(tmp_path):
    closeup = sky_scene(1, sky=False)
    bluish = closeup[..., 2] > closeup[..., 0] * 1.5
    closeup[bluish] = closeup[bluish].mean(axis=-1, keepdims=True)  # no sky, no blue anything
    lr = FakeLightroom({
        "ref": photo(baked(capture(sky_scene(0), 5500), BAKED_LOOK), "ref copy.jpg"),
        "closeup": photo(capture(closeup, 5200), "closeup.JPG"),
    }, active="ref")
    report = run_match(lr, tmp_path, log=quiet)
    (p,) = report["photos"]
    assert not any(k.endswith("Blue") for k in p["look_settings"])


def test_reference_edited_in_lightroom_is_not_baked():
    assert look.is_baked({"ProcessVersion": "11.0", "CameraProfile": "Adobe Standard"})
    assert not look.is_baked({"SaturationAdjustmentBlue": 10})
    assert not look.is_baked({"ToneCurvePV2012": [0, 10, 128, 140, 255, 255]})


def test_contact_sheet_notes_stay_short_and_skip_skin_unless_asked():
    from engine.workflow import _subtitle, _tiles
    text = _subtitle("error 9.9", ["not_converged", "look_limited", "different_file_type", "tone_limited"])
    assert len(text) <= 58 and text.endswith("more")
    report = {"options": {"skin": False}, "reference": {"preview": "r.jpg", "fileName": "r.jpg"},
              "photos": [{"fileName": "a.jpg", "preview": "a.jpg", "start_preview": "a0.jpg", "start_error": 3.0,
                          "final_error": 1.0, "flags": [], "skin_vs_reference": "greener"}]}
    assert "skin" not in _tiles(report, "preview")[1]["subtitle"]
    report["options"]["skin"] = True
    assert "skin greener" in _tiles(report, "preview")[1]["subtitle"]


STRONG_LOOK = dict(BAKED_LOOK, SaturationAdjustmentOrange=-30, LuminanceAdjustmentYellow=-20, ParametricShadows=-25,
                   SplitToningHighlightHue=50, SplitToningHighlightSaturation=15)


def cropped_pair(look_settings=STRONG_LOOK):
    """The original, and an exported copy that is a graded 4:5 crop of it, resized."""
    original = capture(sky_scene(0, size=160), 5500)
    graded = render(original, look_settings)
    H, W = graded.shape[:2]
    w = int(0.7 * W)
    crop = graded[int(0.05 * H):int(0.05 * H) + int(w / 0.8), int(0.2 * W):int(0.2 * W) + w]
    crop = np.asarray(Image.fromarray((crop * 255).round().astype(np.uint8)).resize((96, 120), Image.LANCZOS),
                      float) / 255
    return original, srgb_to_linear(crop)


@pytest.fixture
def cropped_set():
    original, copy = cropped_pair()
    return FakeLightroom({"ref": photo(copy, "IMG_1964 copy.jpg"), "orig": photo(original, "IMG_1964.JPG"),
                          **scenes()}, active="ref")


def test_grade_fit_through_a_crop(cropped_set, tmp_path):
    report = run_match(cropped_set, tmp_path, log=quiet)
    fit = report["grade_fit"]
    assert fit["aligned"]["score"] > 0.8
    assert fit["delta_e_after"] < fit["delta_e_before"] * 0.65 and fit["look_error"] < 0.6, fit
    g = fit["look_settings"]
    assert g["SaturationAdjustmentOrange"] == pytest.approx(-30, abs=10)
    assert g["LuminanceAdjustmentYellow"] == pytest.approx(-20, abs=10)
    # The grade does to another scene what the real edit would have done.
    raw = capture(sky_scene(3)[6:, :], 5500)
    truth = srgb_to_lab(render(raw, STRONG_LOOK).reshape(-1, 3))
    fitted = delta_e_2000(truth, srgb_to_lab(render(raw, g).reshape(-1, 3))).mean()
    unedited = delta_e_2000(truth, srgb_to_lab(render(raw, {}).reshape(-1, 3))).mean()
    assert fitted < unedited / 2, (fitted, unedited)


def test_with_a_grade_each_photo_only_gets_white_balance_and_exposure(cropped_set, tmp_path):
    report = run_match(cropped_set, tmp_path, log=quiet)
    assert report["options"]["light_only"]
    for p in report["photos"]:
        for key in ("Shadows2012", "Highlights2012", "Whites2012", "Blacks2012"):
            assert p["final"][key] == 0.0  # the reference's own values
        assert p["final_error"] < 2.0 and "not_converged" not in p["flags"], p


def test_fitted_grade_is_kept_and_reused(cropped_set, tmp_path):
    grades = tmp_path / "grades"
    first = run_match(cropped_set, tmp_path / "a", grades_dir=grades, log=quiet)
    renders = cropped_set.renders
    second = run_match(cropped_set, tmp_path / "b", grades_dir=grades, log=quiet)
    assert second["grade_fit"]["cached"]
    assert second["grade_fit"]["look_settings"] == first["grade_fit"]["look_settings"]
    assert cropped_set.renders - renders < first["grade_fit"]["renders"]  # no fit renders the second time
    third = run_match(cropped_set, tmp_path / "c", grades_dir=grades, refit=True, log=quiet)
    assert not third["grade_fit"].get("cached")


def test_an_approved_grade_correction_goes_to_every_photo_and_is_kept(cropped_set, tmp_path):
    from engine.workflow import run_nudge
    grades = tmp_path / "grades"
    report = run_match(cropped_set, tmp_path / "a", grades_dir=grades, log=quiet)
    before = report["grade_fit"]["look_settings"].get("HueAdjustmentOrange", 0.0)
    run_nudge(cropped_set, tmp_path / "a", "all", {"HueAdjustmentOrange": -10.0, "Contrast2012": 5.0},
              grade=True, grades_dir=grades)
    for pid in ("orig", "warm", "cool"):
        assert cropped_set.photos[pid]["settings"]["HueAdjustmentOrange"] == before - 10
    again = run_match(cropped_set, tmp_path / "b", grades_dir=grades, log=quiet)
    assert again["grade_fit"]["cached"]
    assert again["grade_fit"]["look_settings"]["HueAdjustmentOrange"] == before - 10
    refit = run_match(cropped_set, tmp_path / "c", grades_dir=grades, refit=True, log=quiet)
    (cache,) = grades.glob("*.json")
    assert "corrections" not in json.loads(cache.read_text())
    assert not refit["grade_fit"].get("cached")


def test_grade_nudge_takes_only_look_sliders(cropped_set, tmp_path):
    from engine.workflow import run_nudge
    run_match(cropped_set, tmp_path, log=quiet)
    with pytest.raises(ValueError):
        run_nudge(cropped_set, tmp_path, "all", {"Exposure2012": 0.2}, grade=True)


def test_a_brushed_edit_in_the_copy_is_found_and_outvoted(tmp_path):
    original = capture(sky_scene(0, size=160), 5500)
    graded = render(original, STRONG_LOOK)
    W = graded.shape[1]
    graded[:, : W // 3] = np.clip(graded[:, : W // 3] * 1.35 + 0.05, 0, 1)  # brightened by hand on the left
    lr = FakeLightroom({"ref": photo(srgb_to_linear(graded), "IMG_1964 copy.jpg"),
                        "orig": photo(original, "IMG_1964.JPG"), **scenes()}, active="ref")
    report = run_match(lr, tmp_path, log=quiet)
    fit = report["grade_fit"]
    assert "left" in fit["unmatched"]
    assert any("local edits" in w for w in report["warnings"])
    g = fit["look_settings"]
    assert g["SaturationAdjustmentOrange"] == pytest.approx(-30, abs=10)  # the real global edit survives
    assert all(abs(g.get(k, 0)) <= 60 for k in look.BAND_KEYS)
