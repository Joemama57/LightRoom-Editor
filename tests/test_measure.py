import numpy as np

from engine.measure import measure
from tests.simulator import capture, make_scene, render

NEUTRAL = {"Temperature": 5500, "Tint": 0}


def test_gray_card_is_neutral():
    img = np.full((64, 64, 3), 0.5)
    m = measure(img)
    assert abs(m.a) < 0.1 and abs(m.b) < 0.1
    assert m.clipped_fraction == 0.0


def test_warm_light_reads_yellow_and_cool_light_reads_blue():
    scene = make_scene()
    warm = measure(render(capture(scene, 3200), NEUTRAL))
    daylight = measure(render(capture(scene, 5500), NEUTRAL))
    cool = measure(render(capture(scene, 9000), NEUTRAL))
    assert warm.b > daylight.b + 10
    assert cool.b < daylight.b - 5


def test_green_light_reads_negative_a():
    scene = make_scene()
    green = measure(render(capture(scene, 5500, light_tint=30), NEUTRAL))
    daylight = measure(render(capture(scene, 5500), NEUTRAL))
    assert green.a < daylight.a - 3


def test_brighter_image_has_higher_percentiles():
    scene = make_scene()
    dark = measure(render(capture(scene, 5500, stops=-1), NEUTRAL))
    bright = measure(render(capture(scene, 5500, stops=1), NEUTRAL))
    for key in dark.L:
        assert bright.L[key] >= dark.L[key]
    assert bright.L["p50"] > dark.L["p50"] + 15


def test_clipped_pixels_ignored_for_color():
    img = np.full((64, 64, 3), 0.5)
    img[:32] = [1.0, 1.0, 0.0]  # blown-out yellow, half the frame
    m = measure(img)
    assert m.clipped_fraction == 0.5
    assert abs(m.b) < 0.1


def test_uint8_input_matches_float():
    img = (make_scene()[..., :3] * 255).clip(0, 255).astype(np.uint8)
    m_int = measure(img)
    m_float = measure(img.astype(np.float64) / 255)
    assert m_int == m_float


def test_metrics_round_trip():
    m = measure(np.full((8, 8, 3), 0.3))
    assert type(m).from_dict(m.to_dict()) == m
