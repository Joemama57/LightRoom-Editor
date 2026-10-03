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


def _grey_and_cream():
    """Half mid-grey, a third cream fabric: the cream sits near the cast estimate."""
    img = np.full((60, 60, 3), 0.45)
    img[:20] = [0.62, 0.55, 0.40]  # cream / sand
    img[20:30] = [0.85, 0.85, 0.85]  # a bright white curtain (L* about 89)
    return img


def test_warm_content_can_be_left_out_of_the_neutrals():
    from engine.measure import measure, neutral_options
    plain = measure(_grey_and_cream(), neutral_hint=(0.0, 0.0))
    with neutral_options(no_warm=True):
        cool = measure(_grey_and_cream(), neutral_hint=(0.0, 0.0))
    assert abs(cool.b) < 1.0 and cool.b <= plain.b
    assert measure(_grey_and_cream(), neutral_hint=(0.0, 0.0)) == plain  # off again after the block


def test_bright_whites_can_count_as_neutrals():
    from engine.measure import measure, neutral_options
    plain = measure(_grey_and_cream(), neutral_hint=(0.0, 0.0))
    with neutral_options(bright=True):
        bright = measure(_grey_and_cream(), neutral_hint=(0.0, 0.0))
    assert bright.neutral_fraction > plain.neutral_fraction


def test_dark_low_colour_pixels_are_not_whites():
    """A black doorway is low in colour but isn't the frame's whites (corridor run 20261004-013517)."""
    img = np.full((64, 64, 3), 0.02)  # black doorway
    img[:, :4] = 0.8  # a sliver of white wall, under LIGHT_MIN_FRACTION
    m = measure(img)
    assert m.light_L is None and m.light_fraction < 0.1
    img[:, :32] = 0.8
    m = measure(img)
    assert m.light_L > 80 and abs(m.light_fraction - 0.5) < 0.01


def test_a_saturated_colour_is_not_clipping():
    """An orange saree has almost no blue: one channel at zero keeps its detail."""
    img = np.full((64, 64, 3), 0.5)
    img[:32] = [0.9, 0.35, 0.0]
    assert measure(img).clipped_fraction == 0.0
    img[:32] = 0.0  # black in every channel is
    assert measure(img).clipped_fraction == 0.5


def test_whites_top_reads_the_bright_end_of_the_whites():
    from engine.measure import neutral_options
    img = np.full((64, 64, 3), 0.85)  # white wall
    img[:40] = 0.35  # grey road and hair, more of the frame
    plain = measure(img).light_L
    with neutral_options(whites_top=True):
        top = measure(img).light_L
    assert top > plain + 20
