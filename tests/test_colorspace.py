import numpy as np
import pytest

from engine.colorspace import delta_e_2000, srgb_to_lab


def test_white_and_black():
    lab = srgb_to_lab(np.array([[1.0, 1.0, 1.0], [0.0, 0.0, 0.0]]))
    np.testing.assert_allclose(lab[0], [100, 0, 0], atol=0.05)
    np.testing.assert_allclose(lab[1], [0, 0, 0], atol=0.05)


def test_primaries():
    # Reference values for sRGB primaries under D65.
    lab = srgb_to_lab(np.array([[1.0, 0, 0], [0, 1.0, 0], [0, 0, 1.0]]))
    np.testing.assert_allclose(lab[0], [53.24, 80.09, 67.20], atol=0.1)
    np.testing.assert_allclose(lab[1], [87.73, -86.18, 83.18], atol=0.1)
    np.testing.assert_allclose(lab[2], [32.30, 79.19, -107.86], atol=0.1)


# A selection of the test pairs from Sharma, Wu & Dalal (2005).
SHARMA_PAIRS = [
    ([50.0, 2.6772, -79.7751], [50.0, 0.0, -82.7485], 2.0425),
    ([50.0, 3.1571, -77.2803], [50.0, 0.0, -82.7485], 2.8615),
    ([50.0, 2.8361, -74.0200], [50.0, 0.0, -82.7485], 3.4412),
    ([50.0, -1.3802, -84.2814], [50.0, 0.0, -82.7485], 1.0),
    ([50.0, 0.0, 0.0], [50.0, -1.0, 2.0], 2.3669),
    ([50.0, 2.49, -0.001], [50.0, -2.49, 0.0009], 7.1792),
    ([50.0, 2.5, 0.0], [73.0, 25.0, -18.0], 27.1492),
    ([60.2574, -34.0099, 36.2677], [60.4626, -34.1751, 39.4387], 1.2644),
    ([22.7233, 20.0904, -46.6940], [23.0331, 14.9730, -42.5619], 2.0373),
    ([90.8027, -2.0831, 1.4410], [91.1528, -1.6435, 0.0447], 1.4441),
    ([2.0776, 0.0795, -1.1350], [0.9033, -0.0636, -0.5514], 0.9082),
]


@pytest.mark.parametrize("lab1,lab2,expected", SHARMA_PAIRS)
def test_ciede2000_sharma(lab1, lab2, expected):
    assert delta_e_2000(lab1, lab2) == pytest.approx(expected, abs=1e-4)


def test_ciede2000_vectorized_and_symmetric():
    a = np.array([p[0] for p in SHARMA_PAIRS])
    b = np.array([p[1] for p in SHARMA_PAIRS])
    np.testing.assert_allclose(delta_e_2000(a, b), delta_e_2000(b, a), atol=1e-9)
    np.testing.assert_allclose(delta_e_2000(a, b), [p[2] for p in SHARMA_PAIRS], atol=1e-4)
