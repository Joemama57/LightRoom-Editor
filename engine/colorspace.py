"""sRGB -> CIELAB conversion and the CIEDE2000 color difference, in plain numpy."""

import numpy as np

# sRGB (linear) -> XYZ, D65 white point.
_RGB_TO_XYZ = np.array(
    [
        [0.4124564, 0.3575761, 0.1804375],
        [0.2126729, 0.7151522, 0.0721750],
        [0.0193339, 0.1191920, 0.9503041],
    ]
)
_D65_WHITE = np.array([0.95047, 1.0, 1.08883])


def srgb_to_linear(rgb):
    rgb = np.asarray(rgb, dtype=np.float64)
    return np.where(rgb <= 0.04045, rgb / 12.92, ((rgb + 0.055) / 1.055) ** 2.4)


def linear_to_srgb(rgb):
    rgb = np.clip(np.asarray(rgb, dtype=np.float64), 0.0, 1.0)
    return np.where(rgb <= 0.0031308, rgb * 12.92, 1.055 * rgb ** (1 / 2.4) - 0.055)


def srgb_to_lab(rgb):
    """Convert gamma-encoded sRGB in [0, 1], shape (..., 3), to CIELAB (D65)."""
    xyz = srgb_to_linear(rgb) @ _RGB_TO_XYZ.T
    t = xyz / _D65_WHITE
    eps = (6 / 29) ** 3
    f = np.where(t > eps, np.cbrt(t), t / (3 * (6 / 29) ** 2) + 4 / 29)
    L = 116 * f[..., 1] - 16
    a = 500 * (f[..., 0] - f[..., 1])
    b = 200 * (f[..., 1] - f[..., 2])
    return np.stack([L, a, b], axis=-1)



def lab_to_srgb(lab):
    """Convert CIELAB (D65), shape (..., 3), to gamma-encoded sRGB clipped to [0, 1]."""
    lab = np.asarray(lab, dtype=np.float64)
    fy = (lab[..., 0] + 16) / 116
    f = np.stack([fy + lab[..., 1] / 500, fy, fy - lab[..., 2] / 200], axis=-1)
    d = 6 / 29
    t = np.where(f > d, f**3, 3 * d**2 * (f - 4 / 29))
    rgb = (t * _D65_WHITE) @ np.linalg.inv(_RGB_TO_XYZ).T
    return linear_to_srgb(rgb)


def delta_e_2000(lab1, lab2):
    """CIEDE2000 difference between Lab colors, shape (..., 3). Sharma et al. (2005)."""
    lab1 = np.asarray(lab1, dtype=np.float64)
    lab2 = np.asarray(lab2, dtype=np.float64)
    L1, a1, b1 = lab1[..., 0], lab1[..., 1], lab1[..., 2]
    L2, a2, b2 = lab2[..., 0], lab2[..., 1], lab2[..., 2]

    C1 = np.hypot(a1, b1)
    C2 = np.hypot(a2, b2)
    C_bar7 = ((C1 + C2) / 2) ** 7
    G = 0.5 * (1 - np.sqrt(C_bar7 / (C_bar7 + 25.0**7)))
    a1p = (1 + G) * a1
    a2p = (1 + G) * a2
    C1p = np.hypot(a1p, b1)
    C2p = np.hypot(a2p, b2)
    h1p = np.degrees(np.arctan2(b1, a1p)) % 360
    h2p = np.degrees(np.arctan2(b2, a2p)) % 360

    dLp = L2 - L1
    dCp = C2p - C1p
    dhp = h2p - h1p
    dhp = np.where(dhp > 180, dhp - 360, dhp)
    dhp = np.where(dhp < -180, dhp + 360, dhp)
    dhp = np.where(C1p * C2p == 0, 0.0, dhp)
    dHp = 2 * np.sqrt(C1p * C2p) * np.sin(np.radians(dhp) / 2)

    Lp_bar = (L1 + L2) / 2
    Cp_bar = (C1p + C2p) / 2
    h_sum = h1p + h2p
    hp_bar = np.where(
        np.abs(h1p - h2p) > 180,
        np.where(h_sum < 360, (h_sum + 360) / 2, (h_sum - 360) / 2),
        h_sum / 2,
    )
    hp_bar = np.where(C1p * C2p == 0, h_sum, hp_bar)

    T = (
        1
        - 0.17 * np.cos(np.radians(hp_bar - 30))
        + 0.24 * np.cos(np.radians(2 * hp_bar))
        + 0.32 * np.cos(np.radians(3 * hp_bar + 6))
        - 0.20 * np.cos(np.radians(4 * hp_bar - 63))
    )
    d_theta = 30 * np.exp(-(((hp_bar - 275) / 25) ** 2))
    Cp_bar7 = Cp_bar**7
    R_C = 2 * np.sqrt(Cp_bar7 / (Cp_bar7 + 25.0**7))
    S_L = 1 + 0.015 * (Lp_bar - 50) ** 2 / np.sqrt(20 + (Lp_bar - 50) ** 2)
    S_C = 1 + 0.045 * Cp_bar
    S_H = 1 + 0.015 * Cp_bar * T
    R_T = -np.sin(np.radians(2 * d_theta)) * R_C

    return np.sqrt(
        (dLp / S_L) ** 2
        + (dCp / S_C) ** 2
        + (dHp / S_H) ** 2
        + R_T * (dCp / S_C) * (dHp / S_H)
    )
