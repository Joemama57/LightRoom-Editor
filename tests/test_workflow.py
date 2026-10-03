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


@pytest.mark.parametrize("seconds", [600.0, 5 * 3600.0])
def test_a_close_up_from_the_same_shoot_keeps_the_references_exposure(tmp_path, seconds):
    # A wedding stage is lit the same for hours: a frame 5 h later is still the same shoot.
    report = run_match(_shoot(seconds), tmp_path, log=quiet)
    (p,) = report["photos"]
    assert "same_shoot" in p["flags"]
    assert abs(p["final"]["Exposure2012"] - REF_SETTINGS["Exposure2012"]) <= 0.3 + 1e-6
    for key in ("Shadows2012", "Highlights2012", "Whites2012", "Blacks2012"):
        assert p["final"][key] == REF_SETTINGS[key]
    assert not {"not_converged", "tone_limited"} & set(p["flags"])


@pytest.mark.parametrize("time, camera", [(9 * 3600.0, "Sony A7 IV"), (600.0, "Canon R6"), (None, "Sony A7 IV")])
def test_other_shoots_are_matched_on_brightness(tmp_path, time, camera):
    report = run_match(_shoot(time, camera), tmp_path, log=quiet)
    (p,) = report["photos"]
    assert "same_shoot" not in p["flags"]
    assert p["final"]["Exposure2012"] < REF_SETTINGS["Exposure2012"] - 0.3  # the brighter content is pulled down


def test_brighter_content_is_only_partly_darkened_to_the_references_histogram(tmp_path, monkeypatch):
    import engine.workflow as wf
    monkeypatch.setattr(wf, "CONTENT_KEEP", 0.0)
    full = run_match(_shoot(8 * 3600.0), tmp_path / "full", log=quiet)["photos"][0]
    monkeypatch.setattr(wf, "CONTENT_KEEP", 0.5)
    part = run_match(_shoot(8 * 3600.0), tmp_path / "part", log=quiet)["photos"][0]
    assert part["final"]["Exposure2012"] > full["final"]["Exposure2012"] + 0.15
    assert part["final"]["Exposure2012"] < REF_SETTINGS["Exposure2012"]  # still darkened somewhat


def _jpeg_ref_and_raw(target_time):
    """A JPEG reference and a raw file of a contrastier, tinted frame from the same camera."""
    scene = make_scene(seed=0)
    ref_settings = {**REF_SETTINGS, "Temperature": 0, "Tint": 0}
    ref = photo(capture(scene, 5500), "IMG_0001.JPG", fmt="JPG", settings=ref_settings)
    raw = photo(capture(scene ** 1.6, 5500, 25), "IMG_0002.DNG")
    ref["captureTime"], raw["captureTime"] = 0.0, target_time
    return FakeLightroom({"ref": ref, "raw": raw}, active="ref")


def test_a_raw_file_from_a_jpeg_references_shoot_is_held_close(tmp_path):
    report = run_match(_jpeg_ref_and_raw(600.0), tmp_path, log=quiet)
    (p,) = report["photos"]
    assert {"same_shoot", "different_file_type"} <= set(p["flags"])
    assert "tone_limited" not in p["flags"]
    for key in ("Shadows2012", "Highlights2012", "Whites2012", "Blacks2012"):
        assert abs(p["final"][key] - p["start"][key]) <= 15 + 1e-6, key
    assert abs(p["final"]["Tint"] - p["start"]["Tint"]) <= 10 + 1e-6


def test_a_raw_file_from_another_shoot_keeps_the_wide_limits(tmp_path):
    report = run_match(_jpeg_ref_and_raw(9 * 3600.0), tmp_path, log=quiet)
    (p,) = report["photos"]
    assert "same_shoot" not in p["flags"] and "different_file_type" in p["flags"]
    moved = max(abs(p["final"][k] - p["start"][k]) for k in ("Shadows2012", "Highlights2012", "Whites2012", "Blacks2012"))
    assert moved > 15 or abs(p["final"]["Tint"] - p["start"]["Tint"]) > 10  # what the held-close photo is spared


def _shoot_off_wb(target_time):
    """A reference and a frame from its light whose camera white balance read 2000 K too warm."""
    scene = make_scene(seed=0)
    ref = photo(capture(scene, 5500), "DSC0001.ARW", settings=dict(REF_SETTINGS))
    off = photo(capture(make_scene(seed=1)[4:, 2:], 5500), "DSC0002.ARW",
                settings={"WhiteBalance": "As Shot", "Temperature": 7500, "Tint": 0})
    ref["captureTime"], off["captureTime"] = 0.0, target_time
    return FakeLightroom({"ref": ref, "off": off}, active="ref")


def _mired_move(p):
    return 1e6 / p["final"]["Temperature"] - 1e6 / p["start"]["Temperature"]


def test_a_raw_from_the_same_shoot_keeps_its_white_balance_close(tmp_path):
    report = run_match(_shoot_off_wb(600.0), tmp_path, log=quiet)
    (p,) = report["photos"]
    assert p["wb_from_camera"] and "same_shoot" in p["flags"]
    assert abs(_mired_move(p)) <= 20 + 0.5
    assert abs(p["final"]["Tint"] - p["start"]["Tint"]) <= 10 + 1e-6


def test_a_raw_from_another_shoot_can_move_its_white_balance_further(tmp_path):
    report = run_match(_shoot_off_wb(9 * 3600.0), tmp_path, log=quiet)
    (p,) = report["photos"]
    assert "same_shoot" not in p["flags"]
    assert abs(_mired_move(p)) > 20


def _resynced_beach(target_time):
    """A raw from the reference's light, matched before (no longer As Shot), whose
    frame is mostly sand: warm, low-chroma content its own solve takes for a cast."""
    scene = make_scene(seed=1)[4:, 2:].copy()
    scene[scene.shape[0] // 3:] = [0.42, 0.36, 0.27]
    ref = photo(capture(make_scene(seed=0), 5500), "DSC0001.ARW", settings=dict(REF_SETTINGS))
    sand = photo(capture(scene, 5500), "DSC0002.ARW",
                 settings={"WhiteBalance": "Custom", "Temperature": 4545, "Tint": 20, "Exposure2012": 0.5})
    ref["captureTime"], sand["captureTime"] = 0.0, target_time
    return FakeLightroom({"ref": ref, "sand": sand}, active="ref")


def test_a_rematched_raw_from_the_shoot_keeps_the_references_white_balance(tmp_path):
    report = run_match(_resynced_beach(1800.0), tmp_path, skin=True, hold_shoot_wb=True, log=quiet)
    (p,) = report["photos"]
    assert not p["wb_from_camera"]
    assert {"same_shoot", "wb_from_reference"} <= set(p["flags"])
    assert "not_converged" not in p["flags"]  # the colour left over is the sand, not a failed match
    assert p["final"]["Temperature"] == REF_SETTINGS["Temperature"]
    assert p["final"]["Tint"] == REF_SETTINGS["Tint"]


def test_without_the_option_a_rematched_raw_from_the_shoot_is_solved_as_before(tmp_path):
    report = run_match(_resynced_beach(1800.0), tmp_path, log=quiet)
    (p,) = report["photos"]
    assert "wb_from_reference" not in p["flags"] and abs(_mired_move(p)) > 5


def test_a_rematched_raw_from_another_shoot_still_solves_its_white_balance(tmp_path):
    report = run_match(_resynced_beach(9 * 3600.0), tmp_path, hold_shoot_wb=True, log=quiet)
    (p,) = report["photos"]
    assert "wb_from_reference" not in p["flags"]
    assert abs(_mired_move(p)) > 5  # the sand pulls it: what the held photo is spared


def _shoot_state(moves):
    """Same-shoot raws that started at 6000 K / Tint 20 and moved (mired, tint)."""
    state = {}
    for n, (mired, tint) in enumerate(moves):
        start = {"Temperature": 6000.0, "Tint": 20.0, "Exposure2012": 0.0, "Shadows2012": 0.0,
                 "Highlights2012": 0.0, "Whites2012": 0.0, "Blacks2012": 0.0}
        matched = dict(start, Temperature=round(1e6 / (1e6 / 6000 + mired)), Tint=20.0 + tint)
        state[str(n)] = {"photo": {"fileName": f"DSC{n}.ARW"}, "start": start, "matched": matched,
                         "shoot_wb": True, "extra_flags": []}
    return state


def test_a_white_balance_outlier_in_a_shoot_gets_the_shoots_move():
    from engine.workflow import _pull_shoot_outliers
    state = _shoot_state([(8, -8), (14, 0), (19, -2), (6, -7), (-6, -2), (-7, -4), (-44, 12)])
    before = {k: dict(s["matched"]) for k, s in state.items()}
    _pull_shoot_outliers(state, quiet)
    outlier = state["6"]
    assert outlier["extra_flags"] == ["wb_from_shoot"]
    assert 1e6 / outlier["matched"]["Temperature"] - 1e6 / 6000 == pytest.approx(6, abs=0.5)  # the median move
    assert outlier["matched"]["Tint"] == pytest.approx(20 - 2)
    for k in "012345":
        assert state[k]["matched"] == before[k] and not state[k]["extra_flags"]


def test_too_few_shoot_raws_have_no_median_to_go_by():
    from engine.workflow import _pull_shoot_outliers
    state = _shoot_state([(8, 0), (-44, 12)])
    _pull_shoot_outliers(state, quiet)
    assert not any(s["extra_flags"] for s in state.values())


def test_learning_ignores_photos_of_another_catalog(lightroom, tmp_path):
    learner = Learner(tmp_path / "learning.json")
    run_match(lightroom, tmp_path / "run", learner=learner, log=quiet)
    for p in lightroom.photos.values():  # the same ids are other photos in the open catalog
        p["fileName"] = "OTHER_" + p["fileName"]
        p["settings"]["Tint"] = p["settings"].get("Tint", 0) + 6
    messages = []
    assert learn_from_run(lightroom, tmp_path / "run", learner, messages.append) == 0
    assert learner.data["preference"] == {}
    assert "learned" not in json.loads((tmp_path / "run" / "report.json").read_text())  # learnable later from its own catalog
    assert any("aren't in the open catalog" in m for m in messages)


def _stage(ref_kelvin, frames):
    """A reference under 3000 K stage light, graded at ref_kelvin, and frames
    {id: (seconds after the reference, camera As Shot Kelvin)} under the same light."""
    ref = photo(capture(make_scene(seed=0), 3000), "DSC1303.ARW",
                settings={**REF_SETTINGS, "Temperature": ref_kelvin})
    ref["captureTime"] = 0.0
    photos = {"ref": ref}
    for n, (pid, (seconds, as_shot)) in enumerate(frames.items()):
        p = photo(capture(make_scene(seed=n + 1)[4:, 2:], 3000), f"DSC13{n + 10}.ARW",
                  settings={"WhiteBalance": "As Shot", "Temperature": as_shot, "Tint": 0})
        p["captureTime"] = seconds
        photos[pid] = p
    return FakeLightroom(photos, active="ref")


def _mired(kelvin):
    return 1e6 / kelvin


def test_the_references_own_white_balance_choice_is_carried_to_its_shoot(tmp_path):
    report = run_match(_stage(3000, {"mate": (30.0, 5500)}), tmp_path, log=quiet)
    assert report["reference_wb_offset"]["from"] == "DSC1310.ARW"
    (p,) = report["photos"]
    assert "wb_offset_from_reference" in p["flags"]
    assert abs(_mired(p["start"]["Temperature"]) - _mired(3000)) < 1  # its camera reading plus the reference's offset
    assert abs(_mired(p["final"]["Temperature"]) - _mired(3000)) <= 20


def test_a_later_photo_can_reach_the_references_white_balance(tmp_path):
    report = run_match(_stage(3000, {"mate": (30.0, 5500), "later": (8 * 3600.0, 5500)}), tmp_path, log=quiet)
    later = next(p for p in report["photos"] if p["id"] == "later")
    assert "wb_offset_from_reference" not in later["flags"]
    assert later["start"]["Temperature"] == 5500  # its own camera reading
    assert _mired(later["final"]["Temperature"]) - _mired(5500) > 80  # past the usual limit, toward 3000 K


def test_a_reference_near_its_camera_reading_carries_nothing(tmp_path):
    report = run_match(_stage(5500, {"mate": (30.0, 5600)}), tmp_path, log=quiet)
    assert report["reference_wb_offset"] is None
    (p,) = report["photos"]
    assert p["start"]["Temperature"] == 5600 and "wb_offset_from_reference" not in p["flags"]


def test_without_a_photo_from_the_references_minutes_nothing_is_carried(tmp_path):
    report = run_match(_stage(3000, {"later": (8 * 3600.0, 5500)}), tmp_path, log=quiet)
    assert report["reference_wb_offset"] is None
    (p,) = report["photos"]
    assert abs(_mired(p["final"]["Temperature"]) - _mired(5500)) <= 80 + 0.5  # the usual limit


def _portrait(seed, bright=False):
    """A frame with a face-sized skin patch; `bright`: bright clothes and backdrop around it."""
    s = make_scene(seed=seed)
    if bright:
        s = np.clip(s * 2.2, 0, 0.9)
    s[10:50, 30:70] = (0.42, 0.24, 0.16)
    return s


def _portraits(bright=True, target_time=None):
    ref = photo(capture(_portrait(0), 5500), "P1.ARW", settings=dict(REF_SETTINGS))
    t = photo(capture(_portrait(1, bright)[4:, 2:], 5500), "P2.ARW")
    if target_time is not None:
        ref["captureTime"], t["captureTime"] = 0.0, target_time
    return FakeLightroom({"ref": ref, "t": t}, active="ref")


def test_a_bright_portrait_gets_its_exposure_from_the_faces(tmp_path):
    plain = run_match(_portraits(), tmp_path / "plain", skin=False, log=quiet)["photos"][0]
    report = run_match(_portraits(), tmp_path / "skin", skin=True, log=quiet)
    (p,) = report["photos"]
    ref_L = report["reference"]["skin"]["L"]
    assert "exposure_from_skin" not in plain["flags"]
    assert "exposure_from_skin" in p["flags"]
    assert p["final"]["Exposure2012"] > plain["final"]["Exposure2012"]  # brighter than the histogram match
    assert p["skin_exposure"]["skin_L_after"] > p["skin_exposure"]["skin_L_before"]
    assert abs(p["skin"]["L"] - ref_L) < abs(p["skin_exposure"]["skin_L_before"] - ref_L)
    assert p["matched"]["Exposure2012"] == p["final"]["Exposure2012"]  # not learned as a preference later


def test_a_portrait_from_the_references_shoot_keeps_its_exposure(tmp_path):
    report = run_match(_portraits(target_time=600.0), tmp_path, skin=True, log=quiet)
    (p,) = report["photos"]
    assert "same_shoot" in p["flags"] and "exposure_from_skin" not in p["flags"]


def test_a_skin_exposure_that_doesnt_help_is_put_back(tmp_path):
    lr = _portraits()
    real_render = lr.render

    def render(items, size=1024):
        if any("/skin/" in i["path"] for i in items):
            # This "Lightroom" ignores the new exposure: render as before.
            for i in items:
                settings = lr.photos[i["id"]]["settings"]
                saved = settings["Exposure2012"]
                settings["Exposure2012"] = saved - 0.5  # the move here is the 0.5 cap
                real_render([i], size)
                settings["Exposure2012"] = saved
            return [i["path"] for i in items]
        return real_render(items, size)

    lr.render = render
    report = run_match(lr, tmp_path, skin=True, log=quiet)
    (p,) = report["photos"]
    assert "exposure_from_skin" not in p["flags"] and p["skin_exposure"] is None
    assert lr.photos["t"]["settings"]["Exposure2012"] == p["final"]["Exposure2012"]


def _cool_portrait(kelvin=4900):
    ref = photo(capture(_portrait(0), 5500), "P1.ARW", settings=dict(REF_SETTINGS))
    t = photo(capture(_portrait(0)[4:, 2:], kelvin), "P2.ARW")
    return FakeLightroom({"ref": ref, "t": t}, active="ref")


def _skin_gap(report):
    ref, skin = report["reference"]["skin"], report["photos"][0]["skin"]
    return float(np.hypot(skin["a"] - ref["a"], skin["b"] - ref["b"]))


def test_faces_still_off_after_the_solve_get_a_small_white_balance_move(tmp_path):
    # Tolerance 6: the solve stops at once, leaving the faces 3.9 off the reference's.
    plain = run_match(_cool_portrait(), tmp_path / "plain", skin=False, tolerance=6.0, log=quiet)
    report = run_match(_cool_portrait(), tmp_path / "skin", skin=True, tolerance=6.0, log=quiet)
    (p,) = report["photos"]
    assert "color_from_skin" in p["flags"] and "color_from_skin" not in plain["photos"][0]["flags"]
    assert _skin_gap(report) < _skin_gap(plain) - 1.0
    assert p["skin_color"]["skin_gap_after"] < p["skin_color"]["skin_gap_before"]
    assert p["skin_color"]["after"] == {"Temperature": p["final"]["Temperature"], "Tint": p["final"]["Tint"]}
    for key in ("Temperature", "Tint"):
        assert p["matched"][key] == p["final"][key]  # not learned as a preference later


def test_faces_that_already_match_keep_their_white_balance(tmp_path):
    report = run_match(_cool_portrait(5500), tmp_path, skin=True, tolerance=6.0, log=quiet)
    (p,) = report["photos"]
    assert "color_from_skin" not in p["flags"] and p["skin_color"] is None


def test_a_skin_colour_move_that_doesnt_help_is_put_back(tmp_path):
    lr = _cool_portrait()
    real_render = lr.render

    def render(items, size=1024):
        if any("/skin_color" in i["path"] for i in items):
            # This "Lightroom" renders the move wrongly: a strong green cast.
            for i in items:
                settings = lr.photos[i["id"]]["settings"]
                saved = settings["Tint"]
                settings["Tint"] = saved - 60
                real_render([i], size)
                settings["Tint"] = saved
            return [i["path"] for i in items]
        return real_render(items, size)

    lr.render = render
    report = run_match(lr, tmp_path, skin=True, tolerance=6.0, log=quiet)
    (p,) = report["photos"]
    assert "color_from_skin" not in p["flags"] and p["skin_color"] is None
    assert lr.photos["t"]["settings"]["Temperature"] == p["final"]["Temperature"]
    assert lr.photos["t"]["settings"]["Tint"] == p["final"]["Tint"]


def test_a_photo_from_the_references_shoot_keeps_its_white_balance_limits(tmp_path):
    lr = _cool_portrait(4900)
    lr.photos["ref"]["captureTime"], lr.photos["t"]["captureTime"] = 0.0, 600.0
    lr.photos["t"]["settings"] = {"Temperature": 4900, "Tint": 0, "Exposure2012": 0}  # As Shot: the camera's own
    report = run_match(lr, tmp_path, skin=True, tolerance=6.0, log=quiet)
    (p,) = report["photos"]
    assert "same_shoot" in p["flags"]
    start, final = p["start"]["Temperature"], p["final"]["Temperature"]
    assert abs(1e6 / final - 1e6 / start) <= 20.0 + 0.5 and abs(p["final"]["Tint"] - p["start"]["Tint"]) <= 10.0 + 0.1


def test_the_report_traces_every_render_of_the_solve(tmp_path):
    report = run_match(_cool_portrait(), tmp_path, skin=True, log=quiet)
    (p,) = report["photos"]
    assert len(p["trace"]) == p["iterations"] + 1
    assert p["trace"][0]["Temperature"] == p["start"]["Temperature"]
    assert {"error", "a", "b", "p50", "skin_a", "skin_b"} <= set(p["trace"][0])
    assert p["trace"][-1]["error"] <= p["trace"][0]["error"]


def _colourful(scene, k):
    gray = scene.mean(axis=-1, keepdims=True)
    return np.clip(gray + k * (scene - gray), 0, None)


def _colour_pair(k):
    """A reference with Vibrance +20 and a frame of the same light whose colours are k times stronger."""
    ref = photo(capture(make_scene(seed=0), 5500), "A.ARW", settings=dict(REF_SETTINGS, Vibrance=20, Saturation=3))
    t = photo(capture(_colourful(make_scene(seed=1)[4:, 2:], k), 5500), "B.ARW")
    return FakeLightroom({"ref": ref, "t": t}, active="ref")


def test_a_photo_much_more_colourful_than_the_reference_gets_calmer_colour(tmp_path):
    lr = _colour_pair(2.0)
    (p,) = run_match(lr, tmp_path, log=quiet)["photos"]
    chroma = p["calmed_colour"]
    assert "calmer_colour" in p["flags"]
    assert chroma["chroma_after"]["mean"] < chroma["chroma_before"]["mean"]
    assert abs(chroma["chroma_after"]["top"] - chroma["chroma_reference"]["top"]) < abs(
        chroma["chroma_before"]["top"] - chroma["chroma_reference"]["top"])
    settings = lr.photos["t"]["settings"]
    assert settings["Vibrance"] < 20 and settings["Saturation"] < 3  # the reference's own values, lowered
    assert settings["Vibrance"] >= 20 - 15 and settings["Saturation"] >= 3 - 15
    assert p["look_settings"] == {"Vibrance": settings["Vibrance"], "Saturation": settings["Saturation"]}
    assert p["look_note"].startswith("calmer colour")


@pytest.mark.parametrize("k", [1.0, 0.5])
def test_colour_that_is_not_much_stronger_than_the_reference_is_left_alone(tmp_path, k):
    lr = _colour_pair(k)
    (p,) = run_match(lr, tmp_path, log=quiet)["photos"]
    assert "calmer_colour" not in p["flags"] and p["calmed_colour"] is None
    settings = lr.photos["t"]["settings"]
    assert settings["Vibrance"] == 20 and settings["Saturation"] == 3  # never raised either


def test_colour_is_not_calmed_when_only_white_balance_is_asked_for(tmp_path):
    lr = _colour_pair(2.0)
    (p,) = run_match(lr, tmp_path, color_only=True, log=quiet)["photos"]
    assert "calmer_colour" not in p["flags"]
    assert lr.photos["t"]["settings"]["Vibrance"] == 20


def _calm_step(offsets, chroma, skin_b):
    from engine.measure import Metrics
    m = Metrics(a=0.0, b=0.0, L={"p1": 5.0, "p25": 30.0, "p50": 50.0, "p75": 70.0, "p99": 90.0}, clipped_fraction=0.0,
                neutral_fraction=0.3, skin_a=10.0, skin_b=skin_b, skin_fraction=0.2, skin_L=55.0)
    return {"offsets": offsets, "look": {"chroma": {"mean": chroma, "top": chroma * 2}}, "metrics": m}


def test_calming_colour_stops_before_it_drains_the_faces():
    import engine.workflow as wf
    history = [_calm_step({}, 32.0, 20.0), _calm_step({"Vibrance": -7.0, "Saturation": -7.0}, 28.0, 19.5),
               _calm_step({"Vibrance": -15.0, "Saturation": -15.0}, 25.0, 15.0)]
    s = {"photo": {"fileName": "A.ARW"}, "calm_history": history}
    ref_chroma = {"mean": 25.0, "top": 50.0}
    full = {"Vibrance": -15.0, "Saturation": -15.0}
    # Faces at chroma 22.4 lose 3+ units at the full cut: the half cut is used.
    assert wf._skin_safe_calm(s, full, ref_chroma, 25.0, quiet) == {"Vibrance": -7.0, "Saturation": -7.0}
    # Faces that were already more colourful than the reference's may be calmed.
    assert wf._skin_safe_calm(s, full, ref_chroma, 15.0, quiet) == full
    # When no step is safe, the reference's own colour stays.
    history[1] = _calm_step({"Vibrance": -7.0, "Saturation": -7.0}, 28.0, 16.0)
    assert wf._skin_safe_calm(s, full, ref_chroma, 25.0, quiet) == {"Vibrance": 0.0, "Saturation": 0.0}


def _magenta_faces(target_skin):
    """A reference and a frame from the same light whose faces are redder (DSC01318):
    the neutrals already match, so white balance can't fix the faces."""
    rng = np.random.default_rng(3)

    def scene(skin):
        s = make_scene(seed=0, size=256).copy()
        s[30:110, 130:240] = np.array(skin) * (1 + 0.06 * rng.standard_normal((80, 110, 1)))
        return s

    ref = photo(capture(scene([0.50, 0.33, 0.22]), 5500), "DSC01303.ARW", settings=dict(REF_SETTINGS))
    t = photo(capture(scene(target_skin), 5500), "DSC01318.ARW")
    return FakeLightroom({"ref": ref, "t": t}, active="ref")


@pytest.fixture
def sim_hue(monkeypatch):
    import engine.workflow as wf
    # The simulator's Orange hue moves skin about a quarter as far as Lightroom's is assumed to.
    monkeypatch.setattr(wf, "SKIN_HUE_GAIN", 4.0)
    monkeypatch.setattr(wf, "SKIN_HUE_STEP", 30.0)
    monkeypatch.setattr(wf, "SKIN_HUE_LIMIT", 40.0)


def _hue(p):
    return p["skin"]["hue"]


def test_redder_faces_are_turned_toward_the_references_skin_hue(tmp_path, sim_hue):
    off = run_match(_magenta_faces([0.50, 0.29, 0.22]), tmp_path / "off", skin=True, log=quiet)["photos"][0]
    lr = _magenta_faces([0.50, 0.29, 0.22])
    report = run_match(lr, tmp_path / "on", skin=True, skin_hue=True, log=quiet)
    (p,) = report["photos"]
    ref_hue = report["reference"]["skin"]["hue"]
    assert "skin_hue_from_faces" in p["flags"] and "skin_hue_from_faces" not in off["flags"]
    assert abs(ref_hue - _hue(p)) < abs(ref_hue - _hue(off)) - 2.0
    assert p["final"]["Temperature"] == off["final"]["Temperature"] and p["final"]["Tint"] == off["final"]["Tint"]
    assert lr.photos["t"]["settings"]["HueAdjustmentOrange"] == p["look_settings"]["HueAdjustmentOrange"] > 0
    assert p["skin_hue"]["after"] == pytest.approx(_hue(p), abs=0.1)


def test_faces_that_already_match_keep_the_references_orange_hue(tmp_path, sim_hue):
    lr = _magenta_faces([0.50, 0.33, 0.22])
    (p,) = run_match(lr, tmp_path, skin=True, skin_hue=True, log=quiet)["photos"]
    assert "skin_hue_from_faces" not in p["flags"] and p["skin_hue"] is None
    assert "HueAdjustmentOrange" not in lr.photos["t"]["settings"]


def test_a_hue_step_that_does_not_help_is_put_back(tmp_path, sim_hue):
    lr = _magenta_faces([0.50, 0.29, 0.22])
    lr.rejected_keys = {"HueAdjustmentOrange"}  # this "Lightroom" ignores it: the faces can't get closer
    (p,) = run_match(lr, tmp_path, skin=True, skin_hue=True, log=quiet)["photos"]
    assert "skin_hue_from_faces" not in p["flags"] and p["skin_hue"] is None


def test_the_solve_stops_instead_of_rendering_the_same_sliders_again(tmp_path):
    # Held white balance and a colour gap that is content (sand): earlier, the same
    # sliders were rendered again and again until the pass limit.
    report = run_match(_resynced_beach(1800.0), tmp_path, hold_shoot_wb=True, log=quiet)
    (p,) = report["photos"]
    rows = [tuple(t[k] for k in ("Temperature", "Tint", "Exposure2012")) for t in p["trace"]]
    assert len(rows) == len(set(rows)) and len(rows) < 7


def test_candles_and_flowers_are_not_matched_as_faces(tmp_path):
    # DSC01250: a night arch of candles and flowers, no people. Its "skin" is far more
    # colourful than the reference's faces, so it mustn't steer the white balance.
    lr = _magenta_faces([0.60, 0.20, 0.05])
    (p,) = run_match(lr, tmp_path, skin=True, log=quiet)["photos"]
    assert "skin_not_matched" in p["flags"]
    (q,) = run_match(_magenta_faces([0.50, 0.33, 0.22]), tmp_path / "faces", skin=True, log=quiet)["photos"]
    assert "skin_not_matched" not in q["flags"]


def test_big_runs_also_get_sheets_of_two_photos_next_to_the_reference(lightroom, tmp_path):
    report = run_match(lightroom, tmp_path, log=quiet)
    assert len(report["photos"]) == 3
    sheets = sorted(p.name for p in tmp_path.glob("contact_sheet_[0-9]*.jpg"))
    assert sheets == ["contact_sheet_1.jpg", "contact_sheet_2.jpg"]
    with Image.open(tmp_path / "contact_sheet_1.jpg") as im, Image.open(tmp_path / "contact_sheet.jpg") as big:
        assert im.width > big.width * 0.9 and im.height < big.height * 2  # three big tiles in one row
    assert (tmp_path / "contact_sheet.jpg").exists() and (tmp_path / "contact_sheet_before.jpg").exists()


def test_raw_tint_is_written_in_whole_numbers():
    from engine.solver import clamp
    assert clamp({"Temperature": 3889, "Tint": 7.6, "Exposure2012": 0}, is_raw=True)["Tint"] == 8.0
    assert clamp({"Temperature": 10, "Tint": 7.6, "Exposure2012": 0}, is_raw=False)["Tint"] == 7.6
