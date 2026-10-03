# Review nudges

## 2026-10-03 — The user found the solved portraits too contrasty
Source: run 20261003-191845 (reference `DSC01303.ARW`: Contrast 7, Whites 3, Blacks −11), `DSC02121.ARW` (solved Blacks −51, Shadows +68), `DSC02201.ARW` (solved Whites +21); user: "i feel like the contrast it too much"
Finding: Both portraits were unflagged with errors 1.55 and 1.65, and the review
passed them. Their solved Blacks and Whites sat 40 and 18 away from the
reference's. Nudged `Blacks2012=+20 Contrast2012=-5` and `Whites2012=-18
Contrast2012=-5`; errors rose to 2.71 and 1.86. The user then said of
`DSC02121.ARW` "the photo is like darken, brigten it more": it was solved to
Exposure −0.73 from the reference's +0.19 without `exposure_from_skin`, and
`Exposure2012=+0.4` (to −0.33) raised its error to 9.43. After brightening,
the user said "the colors look a bit artificial"; `Vibrance=-10
SaturationAdjustmentYellow=-10 SaturationAdjustmentRed=-8` (to Vibrance 10,
Yellow −10, Red −23 against the reference's 20, 0, −15) was applied, error 9.21.
The user then said "2201 is also darkened": `DSC02201.ARW` (solved −0.69) got
`Exposure2012=+0.4` (to −0.29), error 1.86 → 7.02.
Takeaway: In review, compare each photo's solved Blacks and Whites with the
reference's. If either is more than about 15 further toward contrast (Blacks
lower, Whites higher), look at that photo's depth at full size and not on the
contact sheet, and say so in the report even when the error is under 2. A
portrait solved more than about 0.5 EV below the reference with no
`exposure_from_skin` flag needs the same look: the low error followed the
bright backdrop and clothes, not the faces.

## 2026-10-03 — Every nudge raises the engine's error number
Source: run 20261003-141934, `IMG_1964.JPG` (1.6 → 4.1 → 4.3), `IMG_1978.JPG` (1.2 → 4.8 → 5.0), `IMG_1967.JPG` (2.2 → 2.9 → 3.9), `IMG_1961.JPG` (1.5 → 1.9 → 2.5)
Finding: The error measures neutrals and brightness against the reference. An
exposure nudge of −0.15 with contrast +8 added 2.1–3.6 to it. Hue-only
nudges on yellow and orange added another 0.2–1.0, although they change no
neutral. Over the same nudges the measured wall hue and sky lightness of
`IMG_1964.JPG` moved toward the reference (color-hsl.md).
Takeaway: Do not use the error number to judge a colour nudge; re-read the
contact sheet. In the report, give the error before and after nudging and say
that the rise comes from the nudge.

## 2026-10-03 — Nudges that touch exposure, tone or white balance become learned preferences
Source: `engine/workflow.py` `learn_from_run`; run 20261003-141934 (Exposure −0.15 on three JPEGs); run 20261003-140801 (`IMG_1961.JPG` Tint +6, Highlights −15)
Finding: At the next run the engine compares each photo's Lightroom settings
with what the match chose and folds the difference into `learning.json` →
`preference`. Only Temperature, Tint, Exposure and the four tone sliders are
learned; contrast, HSL and saturation nudges are not.
Takeaway: Keep exposure and white-balance nudges for cases that reflect a
general preference. A one-off fix for one frame's content is better made with
a look slider, which leaves the learned preference alone.

## 2026-10-03 — Tint and Highlights nudges do little on a hazy sky
Source: run 20261003-140801, `IMG_1961.JPG` (`Tint=+6 Highlights2012=-15`, error 7.7 → 7.4, sky still pale green-yellow)
Finding: The nudge was aimed at the sky's colour and moved the error by 0.3.
Takeaway: For a cast that sits in one colour family, nudge that band's hue or
saturation. Do not reach for Tint, which moves the whole frame.

## 2026-10-03 — A Tint nudge of +5 on white paint had to be partly undone
Source: run 20261003-133626, `IMG_1980.JPG` (`nudges` in `report.json`: Tint +5, then Tint −3)
Finding: The second and last nudge round on this photo was spent taking back 3
of the first round's 5.
Takeaway: On frames dominated by white paint, keep the first Tint nudge to ±3.
