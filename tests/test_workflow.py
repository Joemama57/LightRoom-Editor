import json
from pathlib import Path

import numpy as np
import pytest
from PIL import Image

from engine.settings import split, starting_corrective
from engine.workflow import parse_changes, run_match, run_nudge
from tests.fake_lightroom import FakeLightroom
from tests.simulator import capture, make_scene

LOOK = {
    "ProcessVersion": "11.0",
    "CameraProfile": "Adobe Standard",
    "ToneCurvePV2012": [0, 10, 64, 60, 192, 200, 255, 245],
    "SplitToningShadowHue": 210,
    "ColorGradeMidtoneHue": 30,
    "SaturationAdjustmentOrange": -10,
    "Contrast2012": 15,
    "PostCropVignetteAmount": -12,
}
REF_SETTINGS = {
    **LOOK,
    "WhiteBalance": "Custom",
    "Temperature": 5500,
    "Tint": 0,
    "Exposure2012": 0,
    "Shadows2012": 20,
    "Highlights2012": -30,
    "Whites2012": 0,
    "Blacks2012": 0,
    "CropTop": 0.1,
    "HasCrop": True,
    "LensProfileEnable": 1,
    "Sharpness": 40,
}


def photo(raw, name, fmt="RAW", camera="Sony A7 IV", settings=None):
    return {"raw": raw, "fileName": name, "fileFormat": fmt, "cameraModel": camera,
            "settings": settings or {"Temperature": 4000, "Tint": 5, "Exposure2012": 0, "CropTop": 0.0, "Sharpness": 25}}


@pytest.fixture
def lightroom():
    other = make_scene(seed=1)[4:, 2:]
    return FakeLightroom(
        {
            "ref": photo(capture(make_scene(seed=0), 5500), "DSC0001.ARW", settings=dict(REF_SETTINGS)),
            "tung": photo(capture(other, 3200, 0, -0.5), "DSC0002.ARW"),
            "shade": photo(capture(other, 7500, 5, 0.3), "DSC0003.ARW"),
            "under": photo(capture(other, 5500, 0, -1.5), "DSC0004.ARW"),
        },
        active="ref",
    )


def test_split_keeps_look_and_drops_per_photo_settings():
    creative, corrective = split(REF_SETTINGS)
    assert creative == LOOK
    assert corrective["Temperature"] == 5500 and corrective["Shadows2012"] == 20
    assert "CropTop" not in creative and "Sharpness" not in creative and "WhiteBalance" not in creative


def test_starting_corrective_across_file_types():
    raw_ref = {"Temperature": 5200, "Tint": 4, "Exposure2012": 0.3}
    assert starting_corrective(raw_ref, True, True)[0]["Temperature"] == 5200
    jpeg, from_camera = starting_corrective(raw_ref, True, False)
    assert jpeg["Temperature"] == 0 and jpeg["Tint"] == 0 and jpeg["Exposure2012"] == 0.3
    assert not from_camera
    assert starting_corrective({"Temperature": 10}, False, True)[0]["Temperature"] == 5500


def test_starting_white_balance_comes_from_the_camera_when_as_shot():
    raw_ref = {"Temperature": 5200, "Tint": 4, "Exposure2012": 0.3}
    start, from_camera = starting_corrective(
        raw_ref, True, True, {"WhiteBalance": "As Shot", "Temperature": 3100, "Tint": 7})
    assert from_camera and start["Temperature"] == 3100 and start["Tint"] == 7
    assert start["Exposure2012"] == 0.3  # tone still starts from the reference
    _, from_camera = starting_corrective(raw_ref, True, True, {"WhiteBalance": "Custom", "Temperature": 3100})
    assert not from_camera


def test_match_end_to_end(lightroom, tmp_path):
    report = run_match(lightroom, tmp_path, log=lambda m: None)

    assert report["reference"]["fileName"] == "DSC0001.ARW"
    assert {p["id"] for p in report["photos"]} == {"tung", "shade", "under"}
    for p in report["photos"]:
        assert p["start_error"] > 3
        assert p["final_error"] < 2.0, p
        assert "not_converged" not in p["flags"]

    # Look copied, per-photo settings untouched, white balance set to Custom.
    for pid in ("tung", "shade", "under"):
        s = lightroom.photos[pid]["settings"]
        for key, value in LOOK.items():
            assert s[key] == value  # edited in Lightroom: the look is copied exactly
        assert s["CropTop"] == 0.0 and s["Sharpness"] == 25
        assert s["WhiteBalance"] == "Custom"

    # Reference never modified; snapshot taken before anything changed.
    assert lightroom.photos["ref"]["settings"] == REF_SETTINGS
    assert sorted(i for i, _, _ in lightroom.snapshots) == ["shade", "tung", "under"]
    assert all(name == "Before Match Look" and s["Temperature"] == 4000 for _, name, s in lightroom.snapshots)

    assert lightroom.photos["tung"]["settings"]["Temperature"] == pytest.approx(3200, rel=0.08)
    assert lightroom.photos["under"]["settings"]["Exposure2012"] == pytest.approx(1.5, abs=0.3)
    assert lightroom.labels == {}

    saved = json.loads((tmp_path / "report.json").read_text())
    assert saved["photos"] == report["photos"]
    for sheet in ("contact_sheet.jpg", "contact_sheet_before.jpg"):
        with Image.open(tmp_path / sheet) as im:
            assert im.width > 0


def test_strength_scales_the_correction(lightroom, tmp_path):
    report = run_match(lightroom, tmp_path, strength=0.5, log=lambda m: None)
    tung = next(p for p in report["photos"] if p["id"] == "tung")
    start_mired = 1e6 / tung["start"]["Temperature"]
    final_mired = 1e6 / tung["final"]["Temperature"]
    assert final_mired == pytest.approx(start_mired + 0.5 * (1e6 / 3200 - start_mired), rel=0.08)
    assert lightroom.photos["tung"]["settings"]["Temperature"] == tung["final"]["Temperature"]


def test_unmatchable_photo_is_flagged_and_labeled(lightroom, tmp_path):
    lightroom.photos["blown"] = photo(np.full((64, 64, 3), 40.0), "DSC0005.ARW", camera="Canon R5")
    report = run_match(lightroom, tmp_path, log=lambda m: None)
    blown = next(p for p in report["photos"] if p["id"] == "blown")
    assert "mostly_clipped" in blown["flags"]
    assert "different_camera" in blown["flags"]
    assert lightroom.labels == {"blown": "yellow"}


def test_needs_active_photo_and_targets(lightroom, tmp_path):
    lightroom.active = None
    with pytest.raises(ValueError, match="active"):
        run_match(lightroom, tmp_path, log=lambda m: None)
    lightroom.active = "ref"
    lightroom.photos = {"ref": lightroom.photos["ref"]}
    with pytest.raises(ValueError, match="at least one"):
        run_match(lightroom, tmp_path, log=lambda m: None)


def test_nudge_updates_photo_and_report(lightroom, tmp_path):
    report = run_match(lightroom, tmp_path, log=lambda m: None)
    before = next(p for p in report["photos"] if p["id"] == "under")["final"]

    p = run_nudge(lightroom, tmp_path, "DSC0004", parse_changes(["Exposure2012=+0.3", "Tint=-2"]))
    assert p["final"]["Exposure2012"] == pytest.approx(before["Exposure2012"] + 0.3)
    assert lightroom.photos["under"]["settings"]["Exposure2012"] == p["final"]["Exposure2012"]

    saved = json.loads((tmp_path / "report.json").read_text())
    under = next(q for q in saved["photos"] if q["id"] == "under")
    assert under["final"] == p["final"] and under["nudges"] == [{"Exposure2012": 0.3, "Tint": -2.0}]
    assert under["preview"].endswith(".jpg") and "nudges" in under["preview"]


def test_nudge_rejects_unknown_slider():
    with pytest.raises(ValueError):
        parse_changes(["Clarity2012=+10"])
    with pytest.raises(ValueError):
        parse_changes(["SplitToningShadowHue=+10"])  # solved, not nudged
    assert parse_changes(["SaturationAdjustmentBlue=+8", "Contrast2012=-5"]) == {
        "SaturationAdjustmentBlue": 8.0, "Contrast2012": -5.0}


def test_settings_lightroom_ignores_become_warnings(lightroom, tmp_path):
    lightroom.rejected_keys = {"CameraProfile"}
    report = run_match(lightroom, tmp_path, log=lambda m: None)
    assert len(report["warnings"]) == 3  # one per target photo, not one per pass
    assert all("CameraProfile" in w for w in report["warnings"])


def test_every_real_lightroom_key_is_classified():
    """Each key from a real LrC 15.5.1 getDevelopSettings() lands in the intended group."""
    keys = json.loads((Path(__file__).parent / "fixtures" / "lrc15_develop_keys.json").read_text())["keys"]
    creative, corrective = split({k: 0 for k in keys})
    assert set(corrective) == {"Temperature", "Tint", "Exposure2012", "Shadows2012",
                               "Highlights2012", "Whites2012", "Blacks2012"}
    for k in ("CameraProfile", "Look", "ToneCurvePV2012", "ColorGradeMidtoneHue", "PointColors",
              "SaturationAdjustmentOrange", "PostCropVignetteAmount", "GrainAmount", "Contrast2012",
              "Texture", "Dehaze", "EnableToneCurve", "ProcessVersion", "ConvertToGrayscale"):
        assert k in creative, k
    for k in ("orientation", "CropTop", "LensProfileEnable", "EnableLensCorrections", "ChromaticAberrationR",
              "PerspectiveUpright", "UprightVersion", "Sharpness", "LuminanceSmoothing", "WhiteBalance",
              "AutoExposure", "LensBlur", "VignetteAmount", "EnableTransform"):
        assert k not in creative and k not in corrective, k


# -- options, self-learning, calibration, masks ---------------------------------

from engine.learning import Learner  # noqa: E402
from engine.workflow import learn_from_run, run_calibrate  # noqa: E402


def quiet(_):
    pass


def test_camera_white_balance_start_converges_faster(lightroom, tmp_path):
    plain = run_match(lightroom, tmp_path / "a", log=quiet)
    # Same photos, but Lightroom still has the camera's As Shot white balance (a bit off, like real AWB).
    for pid, kelvin, tint in (("tung", 3200, 0), ("shade", 7500, 5), ("under", 5500, 0)):
        lightroom.photos[pid]["settings"] = {"WhiteBalance": "As Shot", "Temperature": kelvin * 1.05, "Tint": tint + 2}
    as_shot = run_match(lightroom, tmp_path / "b", log=quiet)
    assert all(p["wb_from_camera"] for p in as_shot["photos"])
    assert sum(p["iterations"] for p in as_shot["photos"]) < sum(p["iterations"] for p in plain["photos"])
    assert all(p["final_error"] < 2.0 for p in as_shot["photos"])


def test_color_only_keeps_each_photos_exposure(lightroom, tmp_path):
    lightroom.photos["under"]["settings"]["Exposure2012"] = -0.4  # a deliberately dark frame
    report = run_match(lightroom, tmp_path, color_only=True, log=quiet)
    under = next(p for p in report["photos"] if p["id"] == "under")
    assert under["final"]["Exposure2012"] == -0.4
    assert under["final"]["Shadows2012"] == 0.0  # its own value, not the reference's 20
    tung = next(p for p in report["photos"] if p["id"] == "tung")
    assert tung["final"]["Temperature"] == pytest.approx(3200, rel=0.08)
    assert report["options"]["color_only"]


def test_skin_is_described_in_the_report(tmp_path):
    def portrait(seed):
        s = make_scene(seed=seed)
        s[10:50, 30:70] = (0.42, 0.24, 0.16)
        return s

    lr = FakeLightroom({
        "ref": photo(capture(portrait(0), 5500), "P1.ARW", settings=dict(REF_SETTINGS)),
        "t": photo(capture(portrait(1)[4:, 2:], 3200), "P2.ARW"),
    }, active="ref")
    report = run_match(lr, tmp_path, skin=True, log=quiet)
    assert report["reference"]["skin"]["hue_note"] == "within the typical range for skin"
    (t,) = report["photos"]
    assert t["skin"]["ita_class"] and 1 <= t["skin"]["monk_tone"] <= 10
    assert t["skin_vs_reference"] == "matches the reference"
    assert t["final_error"] < 2.0


def test_self_learning_across_runs(lightroom, tmp_path):
    learner = Learner(tmp_path / "learning.json")
    runs = tmp_path / "runs"
    first = run_match(lightroom, runs / "1", learner=learner, log=quiet)
    assert learner.data["runs"] == 1
    assert all(p["learned_sensitivities"] is None for p in first["photos"])  # nothing learned yet

    run_match(lightroom, runs / "2", learner=learner, log=quiet)
    third = run_match(lightroom, runs / "3", learner=learner, log=quiet)
    assert all(p["learned_sensitivities"] == "raw|Sony A7 IV" for p in third["photos"])
    assert all(p["final_error"] < 2.0 for p in third["photos"])


def test_learns_your_edits_and_applies_them_next_time(lightroom, tmp_path):
    learner = Learner(tmp_path / "learning.json")
    runs = tmp_path / "runs"
    def run(n):
        # Each match first learns from your edits to the previous run's photos.
        return run_match(lightroom, runs / f"{n:02d}", learner=learner, log=quiet)

    for n in range(2):
        report = run(n)
        # After each match you warm the tungsten shot by 250 K in Lightroom.
        tung = next(p for p in report["photos"] if p["id"] == "tung")
        assert tung["learned_adjustment"] is None  # one edit isn't a habit yet
        lightroom.photos["tung"]["settings"]["Temperature"] = tung["matched"]["Temperature"] + 250

    report = run(2)  # learns from run 1's edit first (that's two), then applies it
    tung = next(p for p in report["photos"] if p["id"] == "tung")
    # Applied with less than full confidence after two edits, but clearly warmer.
    assert 100 < tung["learned_adjustment"]["Temperature"] <= 250
    assert tung["final"]["Temperature"] > tung["matched"]["Temperature"]
    # Only the warm-light photo: daylight/cool photos had no edits.
    others = [p for p in report["photos"] if p["id"] != "tung"]
    assert all(p["learned_adjustment"] is None for p in others)
    assert "warm light" in " ".join(learner.summary()["preferences"])


def test_learn_from_run_once(lightroom, tmp_path):
    learner = Learner(tmp_path / "learning.json")
    report = run_match(lightroom, tmp_path / "run", learner=learner, log=quiet)
    tung = next(p for p in report["photos"] if p["id"] == "tung")
    lightroom.photos["tung"]["settings"]["Tint"] = tung["matched"]["Tint"] + 6
    assert learn_from_run(lightroom, tmp_path / "run", learner, quiet) == 1
    assert learn_from_run(lightroom, tmp_path / "run", learner, quiet) == 0  # already learned


def test_an_undone_match_is_not_learned_as_your_taste(lightroom, tmp_path):
    before = {pid: dict(p["settings"]) for pid, p in lightroom.photos.items()}
    learner = Learner(tmp_path / "learning.json")
    run_match(lightroom, tmp_path / "run", learner=learner, log=quiet)
    for pid, settings in before.items():  # Edit > Undo, or the "Before Match Look" snapshot
        lightroom.photos[pid]["settings"] = dict(settings)
    messages = []
    assert learn_from_run(lightroom, tmp_path / "run", learner, messages.append) == 0
    assert learner.data["preference"] == {} and learner.data["corrections"] == 0
    assert "undone" in " ".join(messages)


def test_an_edit_that_keeps_the_look_is_still_learned(lightroom, tmp_path):
    learner = Learner(tmp_path / "learning.json")
    report = run_match(lightroom, tmp_path / "run", learner=learner, log=quiet)
    tung = next(p for p in report["photos"] if p["id"] == "tung")
    settings = lightroom.photos["tung"]["settings"]
    settings["Exposure2012"] = tung["matched"]["Exposure2012"] + 0.3
    settings["Contrast2012"] += 10  # one look slider changed by hand: still this run's look
    assert learn_from_run(lightroom, tmp_path / "run", learner, quiet) == 1


def test_calibrate_learns_and_restores_every_photo(lightroom, tmp_path):
    before = {pid: dict(p["settings"]) for pid, p in lightroom.photos.items()}
    learner = Learner(tmp_path / "learning.json")
    result = run_calibrate(lightroom, tmp_path / "cal", learner, log=quiet)
    assert result["photos"] == 4 and result["observations"] == 4 * 7
    for pid, settings in before.items():
        after = lightroom.photos[pid]["settings"]
        for key in ("Temperature", "Tint", "Exposure2012", "Shadows2012"):
            assert after.get(key) == settings.get(key, after.get(key)), (pid, key)
    assert learner.prior("Sony A7 IV", True)[0] is not None
    assert any(name == "Before Match Look calibration" for _, name, _ in lightroom.snapshots)


def test_nudge_inside_a_mask(lightroom, tmp_path):
    run_match(lightroom, tmp_path, log=quiet)
    p = run_nudge(lightroom, tmp_path, "DSC0002", parse_changes(["Temperature=-10", "Exposure2012=+0.2"], allow_mask=True),
                  mask="subject")
    assert p["masks"]["subject"] == {"LocalTemperature": -10.0, "LocalExposure2012": 0.2}
    p = run_nudge(lightroom, tmp_path, "DSC0002", parse_changes(["Temperature=-5"], allow_mask=True), mask="subject")
    assert p["masks"]["subject"]["LocalTemperature"] == -15.0  # relative changes add up
    assert [c[1] for c in lightroom.mask_calls] == ["subject", "subject"]
    (mask,) = lightroom.photos["tung"]["settings"]["MaskGroupBasedCorrections"]
    assert mask["LocalTemperature"] == pytest.approx(-0.15)
    with pytest.raises(ValueError):
        run_nudge(lightroom, tmp_path, "DSC0002", {"Clarity2012": 10.0})  # not a slider Match Look moves


def test_learning_skips_photos_that_are_gone(lightroom, tmp_path):
    learner = Learner(tmp_path / "learning.json")
    run_match(lightroom, tmp_path / "runs" / "1", learner=learner, log=quiet)
    del lightroom.photos["shade"]  # deleted from the catalog since
    assert learn_from_run(lightroom, tmp_path / "runs" / "1", learner, quiet) == 0
    assert json.loads((tmp_path / "runs" / "1" / "report.json").read_text())["learned"]


def led_interior(size=96):
    """A dark car interior: black trim, orange leather and blue ambient LEDs, almost nothing neutral."""
    scene = np.full((size, size, 3), 0.006)
    scene[size // 2:, :] = (0.12, 0.035, 0.012)  # orange leather
    scene[size // 4:size // 4 + 6, :] = (0.02, 0.03, 0.6)  # LED strip
    scene[:size // 6, size // 3:] = (0.04, 0.03, 0.25)  # blue glow on the roof
    return scene


def test_dark_led_interior_is_not_blown_out(lightroom, tmp_path):
    lightroom.photos["led"] = photo(capture(led_interior(), 5500), "DSC0006.ARW")
    report = run_match(lightroom, tmp_path, log=lambda m: None)
    led = next(p for p in report["photos"] if p["id"] == "led")
    assert "different_scene" in led["flags"]
    # Colour matched, brightness left as pasted: no +5 EV blow-out.
    assert led["final"]["Exposure2012"] == led["start"]["Exposure2012"]
    assert lightroom.labels.get("led") == "yellow"
    # The ordinary frames are still matched normally.
    for p in report["photos"]:
        if p["id"] != "led":
            assert "different_scene" not in p["flags"] and p["final_error"] < 2.0


# -- guards against learning the wrong lesson -----------------------------------------

def undo(lightroom, report):
    """What Edit > Undo or the "Before Match Look" snapshot does: every photo back as it was."""
    for _, name, settings in lightroom.snapshots:
        if name == "Before Match Look":
            lightroom.photos[_]["settings"] = dict(settings)


def test_an_undone_run_teaches_nothing(lightroom, tmp_path):
    learner = Learner(tmp_path / "learning.json")
    report = run_match(lightroom, tmp_path / "run", learner=learner, log=quiet)
    undo(lightroom, report)
    assert learn_from_run(lightroom, tmp_path / "run", learner, quiet) == 0
    assert learner.data["preference"] == {}
    saved = json.loads((tmp_path / "run" / "report.json").read_text())
    assert saved["learned"].startswith("skipped: undone")


def test_one_photo_undone_the_rest_still_learned(lightroom, tmp_path):
    learner = Learner(tmp_path / "learning.json")
    report = run_match(lightroom, tmp_path / "run", learner=learner, log=quiet)
    before = {i: s for i, n, s in lightroom.snapshots}
    lightroom.photos["shade"]["settings"] = dict(before["shade"])  # this one put back
    tung = next(p for p in report["photos"] if p["id"] == "tung")
    lightroom.photos["tung"]["settings"]["Tint"] = tung["matched"]["Tint"] + 6  # a real edit
    assert learn_from_run(lightroom, tmp_path / "run", learner, quiet) == 1
    assert len(learner.data["preference"]) == 1


def test_repeating_the_same_edit_after_a_rerun_is_not_an_undo(lightroom, tmp_path):
    learner = Learner(tmp_path / "learning.json")
    for n in range(2):
        report = run_match(lightroom, tmp_path / f"{n}", learner=learner, log=quiet)
        tung = next(p for p in report["photos"] if p["id"] == "tung")
        lightroom.photos["tung"]["settings"]["Temperature"] = tung["matched"]["Temperature"] + 250
    assert learn_from_run(lightroom, tmp_path / "1", learner, quiet) == 1


def test_a_bad_run_teaches_no_taste(lightroom, tmp_path):
    learner = Learner(tmp_path / "learning.json")
    run_match(lightroom, tmp_path / "run", learner=learner, log=quiet)
    path = tmp_path / "run" / "report.json"
    report = json.loads(path.read_text())
    for p in report["photos"]:
        p["flags"] = ["not_converged"]  # a run that went badly
    path.write_text(json.dumps(report))
    lightroom.photos["tung"]["settings"]["Tint"] += 6
    assert learn_from_run(lightroom, tmp_path / "run", learner, quiet) == 0
    assert learner.data["preference"] == {}
    assert json.loads(path.read_text())["learned"] == "skipped: 3 of 3 photos were flagged"


def test_learning_is_backed_up_and_can_be_restored(lightroom, tmp_path):
    learner = Learner(tmp_path / "learning.json")
    run_match(lightroom, tmp_path / "a", learner=learner, log=quiet)
    first = json.loads((tmp_path / "learning.json").read_text())
    run_match(lightroom, tmp_path / "b", learner=learner, log=quiet)
    backup = tmp_path / "b" / "learning_before.json"
    assert json.loads(backup.read_text()) == first
    learner.restore(backup)
    assert json.loads((tmp_path / "learning.json").read_text()) == first
    assert (tmp_path / "learning.json.bak").exists()
    assert learner.data["runs"] == first["runs"]


def test_learn_skip_marks_the_run_and_learns_nothing(lightroom, tmp_path, monkeypatch):
    import engine.workflow as wf
    runs = tmp_path / "runs"
    monkeypatch.setattr(wf, "RUNS_DIR", runs)
    monkeypatch.setenv("MATCHLOOK_HOME", str(tmp_path))
    report = run_match(lightroom, runs / "01", log=quiet)
    tung = next(p for p in report["photos"] if p["id"] == "tung")
    lightroom.photos["tung"]["settings"]["Tint"] = tung["matched"]["Tint"] + 6
    monkeypatch.setattr(wf, "Bridge", lambda: lightroom)
    assert wf.main(["learn", "--skip"]) == 0
    assert json.loads((runs / "01" / "report.json").read_text())["learned"] == "skipped: by user"
    assert not (tmp_path / "learning.json").exists()


def _shoot(target_time, camera="Sony A7 IV"):
    """A reference and a close-up of something brighter, under the same light."""
    scene = make_scene(seed=0)
    ref = photo(capture(scene, 5500), "DSC0001.ARW", settings=dict(REF_SETTINGS))
    close = photo(capture(np.clip(scene * 1.8, 0, 0.9), 5500), "DSC0002.ARW", camera=camera)
    ref["captureTime"], close["captureTime"] = 0.0, target_time
    return FakeLightroom({"ref": ref, "close": close}, active="ref")


def test_a_close_up_from_the_same_shoot_keeps_the_references_exposure(tmp_path):
    report = run_match(_shoot(600.0), tmp_path, log=quiet)
    (p,) = report["photos"]
    assert "same_shoot" in p["flags"]
    assert abs(p["final"]["Exposure2012"] - REF_SETTINGS["Exposure2012"]) <= 0.3 + 1e-6
    for key in ("Shadows2012", "Highlights2012", "Whites2012", "Blacks2012"):
        assert p["final"][key] == REF_SETTINGS[key]
    assert not {"not_converged", "tone_limited"} & set(p["flags"])


@pytest.mark.parametrize("time, camera", [(5 * 3600.0, "Sony A7 IV"), (600.0, "Canon R6"), (None, "Sony A7 IV")])
def test_other_shoots_are_matched_on_brightness(tmp_path, time, camera):
    report = run_match(_shoot(time, camera), tmp_path, log=quiet)
    (p,) = report["photos"]
    assert "same_shoot" not in p["flags"]
    assert p["final"]["Exposure2012"] < REF_SETTINGS["Exposure2012"] - 0.3  # the brighter content is pulled down
