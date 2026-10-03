# Colour families (HSL, saturation, split toning)

Colour figures below are median CIELAB values of a hand-placed patch in the
run's saved previews (hue in degrees, 0 = red, 90 = yellow, 270 = blue).

## 2026-10-03 — The wall lands about 28° too yellow; −20 of hue nudge recovers 8°
Source: run 20261003-141934, `IMG_1964.JPG` (`final/`, `nudges/00_`, `nudges/05_`) against `reference.jpg`, wall patch at the right edge
Finding: The reference's wall measures hue 55° (a +19, b +26). After the match
the same wall in `IMG_1964.JPG` measured hue 83° (a +4, b +35). One round of
`HueAdjustmentYellow=-10 HueAdjustmentOrange=-10` gave 79°, a second round 75°.
Two rounds is the nudge limit, so 20° of the gap remained.
Takeaway: On this set, apply the yellow and orange hue nudge in the first round
to every frame showing the wall, and report the wall as still off. Closing the
gap needs a better grade fit (see grade-fit.md), not more nudging.

## 2026-10-03 — A pale sky was a lightness gap; the saturation nudge overshot
Source: run 20261003-141934, `IMG_1964.JPG` against `reference.jpg`, upper-sky patch
Finding: Reference sky: L 68, chroma 33. Matched `IMG_1964.JPG`: L 75, chroma
32, so the sky was 7 L too light and already as saturated as the reference.
After `SaturationAdjustmentBlue=+8 LuminanceAdjustmentBlue=-8 Contrast2012=+8
Exposure2012=-0.15` it measured L 69, chroma 38: lightness matched, chroma 5
over. Sky hue stayed at 260–261° against the reference's 246° throughout.
Takeaway: For a sky that looks washed out next to the reference, nudge
`LuminanceAdjustmentBlue` down (about −8) and leave blue saturation alone.
The remaining hue difference (about 15° toward purple) is a grade-fit residual;
`HueAdjustmentBlue` is the slider to try, and has not been tested yet.

## 2026-10-03 — Per-photo look matching pushed saturation up on frames with no sky
Source: run 20261003-133626, `look_note` and `nudges` in `report.json` for `IMG_1967.JPG`, `IMG_1968.JPG`, `IMG_1974.JPG`, `IMG_1980.JPG`
Finding: Against a blue-sky reference, the per-photo look stage set Saturation
+20 to +30 (and Vibrance +20 on `IMG_1968.JPG`) on four close-ups of paint and
tyres. Look errors still ended at 6.8–13.9, and all four were nudged back by
Saturation −8 to −10 in review. Commit a251148 replaced this stage with one
fitted grade when the original is available.
Takeaway: If a run reports per-photo `look_note` values (the fallback when the
original is missing), read the Saturation and Vibrance figures for every frame
without sky and expect to take about 10 back off.
