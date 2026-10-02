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
    assert starting_corrective(raw_ref, True, True)["Temperature"] == 5200
    jpeg = starting_corrective(raw_ref, True, False)
    assert jpeg["Temperature"] == 0 and jpeg["Tint"] == 0 and jpeg["Exposure2012"] == 0.3
    assert starting_corrective({"Temperature": 10}, False, True)["Temperature"] == 5500


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
            assert s[key] == value
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
        parse_changes(["Saturation=+10"])


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
