# Review nudges

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
