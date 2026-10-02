"""Build the skin-tone model from measured skin colour data.

    python3 -m tools.build_skin_model

Reads data/skin_sources.json and writes:
  engine/data/skin_model.json   the model the engine loads
  docs/SKIN_TONES.md            the analysis behind it

The model has three parts:
  * a skin detector: a band of hue, chroma and lightness that real skin falls
    in, widened for how photos render skin (camera profiles and grading add
    contrast and saturation);
  * the ITA classes (very light ... dark), to describe a measured skin tone;
  * the Monk scale swatches, to name the closest everyday tone.
"""

import json
import math
from pathlib import Path

import numpy as np

from engine.colorspace import srgb_to_lab

ROOT = Path(__file__).resolve().parent.parent
SOURCES = ROOT / "data" / "skin_sources.json"
MODEL = ROOT / "engine" / "data" / "skin_model.json"
REPORT = ROOT / "docs" / "SKIN_TONES.md"

# How far rendered photos sit from in-vivo colorimetry. These are allowances,
# not measurements: a camera profile and a grade can rotate skin hue a few
# degrees and raise its chroma well above what a spectrophotometer reads.
RENDER_HUE_SD = 5.0  # degrees, added in quadrature to the population spread
RENDER_CHROMA_LOW = 0.6  # rendered chroma can drop to this fraction (soft light, desaturated looks)
RENDER_CHROMA_HIGH = 1.7  # ... or rise to this factor (contrasty profiles, warm grades)
DETECT_SIGMAS = 2.5  # hue half-width of the detector, in effective SDs
# Below this chroma a pixel is nearly gray and its hue angle is noise, so it
# can't be told apart from skin by colour (the darkest measured group's mean
# minus 2 SD is ~9).
MIN_DETECT_CHROMA = 8.0


def hue_deg(a, b):
    return math.degrees(math.atan2(b, a))


def ita_deg(L, b):
    return math.degrees(math.atan((L - 50.0) / b))


def ita_class(ita, classes):
    for c in classes:
        if ita > c["min"]:
            return c["name"]
    return classes[-1]["name"]


def hue_sd(a, b, sa, sb):
    """Propagate a*/b* spread into hue-angle spread (degrees), first order."""
    c2 = a * a + b * b
    return math.degrees(math.sqrt((b * sa) ** 2 + (a * sb) ** 2) / c2)


def build():
    src = json.loads(SOURCES.read_text())
    groups = src["populations"]["groups"]
    classes = src["ita"]["classes"]

    rows = []
    for g in groups:
        L, sL = g["L"]
        a, sa = g["a"]
        b, sb = g["b"]
        rows.append({
            "name": g["name"], "code": g["code"], "n": g["n"],
            "L": L, "sL": sL, "a": a, "sa": sa, "b": b, "sb": sb,
            "C": math.hypot(a, b), "sC": g["C"][1],
            "h": hue_deg(a, b), "sh": hue_sd(a, b, sa, sb),
            "ita": ita_deg(L, b),
        })
    for r in rows:
        r["ita_class"] = ita_class(r["ita"], classes)

    n = np.array([r["n"] for r in rows], dtype=float)
    w = n / n.sum()
    h = np.array([r["h"] for r in rows])
    sh = np.array([r["sh"] for r in rows])
    C = np.array([r["C"] for r in rows])
    sC = np.array([r["sC"] for r in rows])
    Ls = np.array([r["L"] for r in rows])
    sLs = np.array([r["sL"] for r in rows])

    # Pooled spread = within-group + between-group (law of total variance).
    h_mean = float(np.sum(w * h))
    h_sd = float(np.sqrt(np.sum(w * (sh**2 + (h - h_mean) ** 2))))
    C_mean = float(np.sum(w * C))
    C_sd = float(np.sqrt(np.sum(w * (sC**2 + (C - C_mean) ** 2))))
    L_lo = float(np.min(Ls - 3 * sLs))
    L_hi = float(np.max(Ls + 3 * sLs))

    # Cross-check against the facial measurements (hue only is reported there).
    facial = src["facial_hue"]
    face_lo, face_hi = facial["hue_range_across_locations_deg"]

    h_eff = math.hypot(h_sd, RENDER_HUE_SD)
    detector = {
        "hue_center": round(h_mean, 2),
        "hue_sd": round(h_eff, 2),
        "hue_range": [round(h_mean - DETECT_SIGMAS * h_eff, 1), round(h_mean + DETECT_SIGMAS * h_eff, 1)],
        "chroma_range": [
            round(max(MIN_DETECT_CHROMA, float(np.min(C - 3 * sC)) * RENDER_CHROMA_LOW), 1),
            round(float(np.max(C + 3 * sC)) * RENDER_CHROMA_HIGH, 1),
        ],
        # Shadows and highlights on a face reach well past the measured means.
        "L_range": [round(max(15.0, L_lo - 5), 1), round(min(92.0, L_hi + 5), 1)],
    }

    monk = []
    for i, hx in enumerate(src["monk"]["hex"], start=1):
        rgb = np.array([int(hx[j : j + 2], 16) / 255 for j in (1, 3, 5)])
        L, a, b = (float(v) for v in srgb_to_lab(rgb))
        ita = ita_deg(L, b)
        monk.append({"tone": i, "hex": hx, "L": round(L, 2), "a": round(a, 2), "b": round(b, 2),
                     "C": round(math.hypot(a, b), 2), "h": round(hue_deg(a, b), 1),
                     "ita": round(ita, 1), "ita_class": ita_class(ita, classes)})

    model = {
        "built_from": str(SOURCES.relative_to(ROOT)),
        "sources": {k: src[k]["source"] for k in ("populations", "facial_hue", "monk", "ita")},
        "detector": detector,
        "population": {
            "n_measurements": int(n.sum()),
            "hue_mean": round(h_mean, 2), "hue_sd": round(h_sd, 2),
            "chroma_mean": round(C_mean, 2), "chroma_sd": round(C_sd, 2),
            "L_range_3sd": [round(L_lo, 1), round(L_hi, 1)],
            "facial_hue_range": [face_lo, face_hi],
        },
        "groups": [{k: (round(v, 2) if isinstance(v, float) else v) for k, v in r.items()} for r in rows],
        "ita_classes": classes,
        "monk": monk,
    }
    MODEL.parent.mkdir(parents=True, exist_ok=True)
    MODEL.write_text(json.dumps(model, indent=1) + "\n")
    REPORT.write_text(report(model, src))
    return model


def report(m, src):
    p, d = m["population"], m["detector"]
    lines = [
        "# Skin tones: the data behind the skin guard",
        "",
        "_Generated by `python3 -m tools.build_skin_model` from `data/skin_sources.json`. Don't edit by hand._",
        "",
        "Match Look uses this model to:",
        "- **find skin** in a rendered photo, so it can keep skin tones consistent across the set (`--skin`);",
        "- **describe a skin tone** in the report (ITA class, nearest Monk tone, hue), so the review can say",
        "  \"skin is greener than in the reference\" instead of quoting numbers.",
        "",
        "It is about colour, not about people: it never labels or guesses anyone's ethnicity, and the",
        "populations below are only where the measurements come from.",
        "",
        f"## 1. Measured skin colour ({p['n_measurements']:,} in-vivo measurements, 8 populations)",
        "",
        "Source: " + src["populations"]["source"],
        "",
        "| Population | n | L* | a* | b* | Chroma | Hue° | ITA° | ITA class |",
        "|---|---:|---:|---:|---:|---:|---:|---:|---|",
    ]
    for g in m["groups"]:
        lines.append(
            f"| {g['name']} | {g['n']:,} | {g['L']:.1f} ± {g['sL']:.1f} | {g['a']:.1f} ± {g['sa']:.1f} | "
            f"{g['b']:.1f} ± {g['sb']:.1f} | {g['C']:.1f} | {g['h']:.1f} ± {g['sh']:.1f} | {g['ita']:.1f} | {g['ita_class']} |"
        )
    lines += [
        "",
        "**What the numbers say**",
        f"- **Hue is the constant.** Across all eight populations, skin hue sits at **{p['hue_mean']:.1f}° ± {p['hue_sd']:.1f}°**"
        f" (pooled, within plus between groups). Wang et al. (2015) found the same on faces: group means of 53–60°, and"
        f" {p['facial_hue_range'][0]}–{p['facial_hue_range'][1]}° across forehead, cheek, chin, nose and hands.",
        f"- **Chroma barely changes:** {p['chroma_mean']:.1f} ± {p['chroma_sd']:.1f}. Mean a* is 9.8–11.0 in every group.",
        f"- **Lightness is what varies:** group means from {min(g['L'] for g in m['groups']):.1f} to {max(g['L'] for g in m['groups']):.1f}"
        f" (±3 SD: {p['L_range_3sd'][0]}–{p['L_range_3sd'][1]}).",
        "- So a skin detector should be **tight on hue, loose on lightness**. A skin tone that drifts in hue (greener or",
        "  more magenta) reads as wrong on every complexion, which is why hue is what the review watches most.",
        "",
        "## 2. The Monk Skin Tone scale (10 swatches)",
        "",
        "Source: " + src["monk"]["source"],
        "",
        "| Tone | Swatch | L* | a* | b* | Chroma | Hue° | ITA° | ITA class |",
        "|---:|---|---:|---:|---:|---:|---:|---:|---|",
    ]
    for t in m["monk"]:
        lines.append(f"| {t['tone']} | `{t['hex']}` | {t['L']:.1f} | {t['a']:.1f} | {t['b']:.1f} | {t['C']:.1f} | {t['h']:.0f} | {t['ita']:.0f} | {t['ita_class']} |")
    lines += [
        "",
        "**What the numbers say**",
        f"- The Monk swatches are **mostly yellower than measured skin**: {sum(t['h'] > 65 for t in m['monk'])} of 10 have a hue above 65°,"
        f" against {min(g['h'] for g in m['groups']):.0f}–{max(g['h'] for g in m['groups']):.0f}° for the measured population means. The lightest and darkest",
        f"  tones are nearly neutral (chroma under 8). They were designed as a perceptual scale to show people, not as",
        "  colorimetric skin. So Match Look uses them **only to name** the closest tone (by lightness and ITA), never to",
        "  detect skin or judge its hue.",
        "",
        "## 3. ITA classes (Chardon et al. 1991)",
        "",
        "ITA° = atan((L* − 50) / b*). A standard dermatology measure of how light or dark skin is:",
        "",
        "| Class | ITA° |",
        "|---|---|",
    ]
    prev = 90
    for c in src["ita"]["classes"]:
        lines.append(f"| {c['name']} | {c['min']} to {prev} |")
        prev = c["min"]
    lines += [
        "",
        "## 4. The detector Match Look uses",
        "",
        "Rendered photos aren't spectrophotometer readings: profiles and grades add contrast and saturation, and",
        "faces have shadows and highlights. So the measured spread is widened:",
        "",
        f"- **Hue:** centre {d['hue_center']}°, effective SD {d['hue_sd']}° (population {p['hue_sd']}° plus {RENDER_HUE_SD}° for rendering),",
        f"  accepted range {d['hue_range'][0]}–{d['hue_range'][1]}°. Membership falls off smoothly toward the edges, so a",
        "  skin tone near the boundary doesn't flicker in and out between renders.",
        f"- **Chroma:** {d['chroma_range'][0]}–{d['chroma_range'][1]} (measured range × {RENDER_CHROMA_LOW}–{RENDER_CHROMA_HIGH}, with a floor of"
        f" {MIN_DETECT_CHROMA:g}: below that a pixel is nearly gray and its hue is noise).",
        f"- **Lightness:** {d['L_range'][0]}–{d['L_range'][1]}.",
        "",
        "Limits: wood, sand, tan leather and warm walls share skin's hue and are picked up too. That's why the skin",
        "guard only joins in after the white balance is close, and why it's weighted below the neutral axis.",
        "",
    ]
    return "\n".join(lines)


if __name__ == "__main__":
    model = build()
    print(json.dumps(model["detector"], indent=1))
    print(f"wrote {MODEL.relative_to(ROOT)} and {REPORT.relative_to(ROOT)}")
