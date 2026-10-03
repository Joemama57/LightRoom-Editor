# Colour families (HSL, saturation, split toning)

Colour figures below are median CIELAB values of a hand-placed patch in the
run's saved previews (hue in degrees, 0 = red, 90 = yellow, 270 = blue).

## 2026-10-03 — After a refit the wall was 13° too red; blue hue −10 moved the sky 9° toward the reference
Source: run 20261003-155051, `IMG_1964.JPG` (`iter_2/`, `nudges/02_`) against `reference.jpg`, same wall and upper-sky patches as the entries below
Finding: With the refitted grade (orange hue −32) the wall measured hue 40°,
chroma 19, against the reference's 53°, chroma 36: too red, the opposite of
the entry below, whose "apply −10 yellow and orange" takeaway no longer holds
for this grade. The unedited original's wall is already at 51°.
`--grade HueAdjustmentOrange=+10 LuminanceAdjustmentOrange=+10` gave 46°; the
wall stayed 15 L darker and 16 chroma weaker than the copy's, and "right" is in
the fit's `unmatched` regions. `--grade HueAdjustmentBlue=-10` moved the upper
sky from hue 263° to 254° (reference 250°) with chroma 29 → 32 (reference 36).
Takeaway: Measure the wall's hue before choosing the sign of an orange nudge;
it depends on the fit. `HueAdjustmentBlue=-10` is a safe first-round grade
nudge for this reference's sky. The wall's brightness and saturation look like
a local edit in the copy: report it, do not chase it.

## 2026-10-03 — The wall lands about 28° too yellow; −20 of hue nudge recovers 8°
Source: run 20261003-141934, `IMG_1964.JPG` (`final/`, `nudges/00_`, `nudges/05_`) against `reference.jpg`, wall patch at the right edge
Finding: The reference's wall measures hue 55° (a +19, b +26). After the match
the same wall in `IMG_1964.JPG` measured hue 83° (a +4, b +35). One round of
`HueAdjustmentYellow=-10 HueAdjustmentOrange=-10` gave 79°, a second round 75°.
Two rounds is the nudge limit, so 20° of the gap remained.
Takeaway: This direction held only for the fit of run 20261003-141934 (orange
hue +62); the refit in run 20261003-155051 erred the other way (entry above).
With a grade whose orange hue is strongly positive, apply the yellow and orange
hue nudge in the first round to every frame showing the wall, and report the
wall as still off. Closing the
gap needs a better grade fit (see grade-fit.md), not more nudging.

## 2026-10-03 — A pale sky was a lightness gap; the saturation nudge overshot
Source: run 20261003-141934, `IMG_1964.JPG` against `reference.jpg`, upper-sky patch
Finding: Reference sky: L 68, chroma 33. Matched `IMG_1964.JPG`: L 75, chroma
32, so the sky was 7 L too light and already as saturated as the reference.
After `SaturationAdjustmentBlue=+8 LuminanceAdjustmentBlue=-8 Contrast2012=+8
Exposure2012=-0.15` it measured L 69, chroma 38: lightness matched, chroma 5
over. Sky hue stayed at 260–261° against the reference's 246° throughout.
Changed by run 20261003-161341: `LuminanceAdjustmentBlue=-8` on its own moved
the sky of `IMG_1964.JPG` and `IMG_1966.JPG` by 1 L (74 → 73, reference 69;
`nudges/07_`, `08_`), while `Exposure2012=-0.3` on the same photos moved it
from 81 to 74. The 6 L gained above came mostly from the exposure and contrast
part of that nudge.
Takeaway: For a sky that looks washed out next to the reference, fix exposure
first; `LuminanceAdjustmentBlue` −8 is worth about 1 L. Leave blue saturation
alone.
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
