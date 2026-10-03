import numpy as np
from PIL import Image

from engine import align
from tests.simulator import capture, render
from tests.test_look import BAKED_LOOK, sky_scene


def resized(img, w, h):
    return np.asarray(Image.fromarray((img * 255).round().astype(np.uint8)).resize((w, h), Image.LANCZOS), float) / 255


def test_finds_a_graded_rescaled_crop():
    orig = render(capture(sky_scene(0, size=192), 5500), {})
    graded = render(capture(sky_scene(0, size=192), 5500), BAKED_LOOK)
    H, W = orig.shape[:2]
    x0, y0, w = int(0.15 * W), int(0.1 * H), int(0.7 * W)
    h = int(w / 0.8)
    copy = resized(graded[y0:y0 + h, x0:x0 + w], 100, 125)
    found = align.locate(orig, copy)
    assert found and found["score"] > 0.8
    for got, want in zip(found["box"], (x0 / W, y0 / H, w / W, h / H)):
        assert abs(got - want) < 0.02


def test_unrelated_picture_is_not_matched():
    orig = render(capture(sky_scene(0, size=192), 5500), {})
    noise = np.random.default_rng(3).random((120, 96, 3))
    assert align.locate(orig, noise) is None


def test_crop_returns_the_box_resized():
    img = np.zeros((100, 200, 3))
    img[20:60, 50:150] = 1.0
    out = align.crop(img, [0.25, 0.2, 0.5, 0.4], size=(50, 20))
    assert out.shape == (20, 50, 3) and out.mean() > 0.99
