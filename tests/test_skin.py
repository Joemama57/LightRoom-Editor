import json
import math

import numpy as np
import pytest

from engine import skin
from engine.colorspace import srgb_to_lab
from engine.measure import measure

SOURCES = json.loads(open("data/skin_sources.json").read())


def lab_of_hex(hx):
    return srgb_to_lab(np.array([int(hx[i : i + 2], 16) / 255 for i in (1, 3, 5)]))


def test_model_is_built_from_the_sources():
    m = skin.model()
    assert m["population"]["n_measurements"] == sum(g["n"] for g in SOURCES["populations"]["groups"]) == 14532
    assert len(m["monk"]) == 10 and len(m["groups"]) == 8
    lo, hi = m["detector"]["hue_range"]
    # Every measured group's mean hue, and the facial range from Wang et al., sit inside the detector.
    assert all(lo < g["h"] < hi for g in m["groups"])
    assert lo < SOURCES["facial_hue"]["hue_range_across_locations_deg"][0]
    assert hi > SOURCES["facial_hue"]["hue_range_across_locations_deg"][1]


@pytest.mark.parametrize("group", SOURCES["populations"]["groups"], ids=lambda g: g["code"])
def test_every_measured_population_is_detected_as_skin(group):
    lab = np.array([[group["L"][0], group["a"][0], group["b"][0]]])
    assert skin.weights(lab)[0] > 0.8


def test_detection_holds_across_each_population_spread():
    # +-1 SD in every direction stays detected; that's most real skin.
    for g in SOURCES["populations"]["groups"]:
        for dL in (-1, 1):
            for da in (-1, 1):
                for db in (-1, 1):
                    lab = np.array([[g["L"][0] + dL * g["L"][1], g["a"][0] + da * g["a"][1],
                                     g["b"][0] + db * g["b"][1]]])
                    assert skin.weights(lab)[0] >= skin.MIN_WEIGHT, (g["code"], dL, da, db)


@pytest.mark.parametrize("name,lab", [
    ("neutral gray", [50, 0, 0]),
    ("sky blue", [70, -5, -30]),
    ("foliage green", [45, -35, 30]),
    ("saturated red", [45, 65, 45]),
    ("deep shadow", [8, 2, 3]),
    ("lemon yellow", [85, -10, 75]),
])
def test_non_skin_colors_are_rejected(name, lab):
    assert skin.weights(np.array([lab], dtype=float))[0] < skin.MIN_WEIGHT


def test_ita_classes_follow_chardon():
    assert skin.ita_class(skin.ita(70, 12)) == "very light"
    assert skin.ita_class(skin.ita(39.6, 14.4)) == "dark"
    assert skin.ita_class(skin.ita(52.7, 17.9)) == "brown"
    assert math.isclose(skin.ita(60, 10), math.degrees(math.atan(1.0)))


def test_monk_tone_by_lightness():
    for t in skin.model()["monk"]:
        assert skin.nearest_monk(t["L"]) == t["tone"]


def test_describe_and_compare_in_words():
    ref = skin.describe(60, 11, 16)
    assert ref["hue_note"] == "within the typical range for skin"
    assert skin.describe(60, 20, 8)["hue_note"].startswith("redder")
    assert skin.describe(60, 4, 24)["hue_note"].startswith("yellower")
    assert skin.compare(ref, skin.describe(60, 11, 16)) == "matches the reference"
    assert skin.compare(ref, skin.describe(60, 8, 16)) == "greener"
    assert skin.compare(ref, skin.describe(52, 11, 20)) == "yellower / warmer, darker"


def test_measure_finds_a_face_and_ignores_a_gray_frame():
    img = np.full((64, 64, 3), 0.5)
    assert measure(img).skin_a is None
    face = np.full((64, 64, 3), 0.5)
    face[16:48, 16:48] = [0.80, 0.60, 0.48]  # a typical light skin tone in sRGB
    m = measure(face)
    assert m.skin_fraction == pytest.approx(0.25, abs=0.02)
    assert skin.describe(m.skin_L, m.skin_a, m.skin_b)["hue_note"] == "within the typical range for skin"
