# Mixed scenes and mixed file types

## 2026-10-03 — Raw files against a Lightroom-edited JPEG reference mostly do not converge
Source: run 20261003-163645 (reference `IMG_1963.JPG`, look copied from its Lightroom settings, full light stage), 11 DNGs and 19 JPEGs
Finding: 9 of the 11 DNGs ended `not_converged` after 7 passes, with errors of
0.8–13.2 (start 4.0–20.2); the three above 11 are close-ups (`IMG_1982`,
`IMG_1983`, `IMG_1987`). Of the 19 `same_shoot` JPEGs, 3 were `not_converged`
and errors ended at 1.3–15.3, the highest on close-ups (`IMG_1980.JPG` 15.3,
`IMG_1974.JPG` 12.3, `IMG_1970.JPG` 10.4). `IMG_1988.DNG` was `tone_limited`
with Highlights, Shadows and Whites at +40 and Blacks at −40. This differs
from the entry below, where three DNGs under a fitted grade with light-only
solving ended at 1.0–2.9.
Changed by run 20261003-165410 (same selection and reference, after commit
868a48e): the 11 DNGs now carry `same_shoot` with `different_file_type`, all
ended with the four tone sliders at 0, and 9 of 11 ended at Tint +10.0 (the
cap). 4 of 11 were `not_converged` (`IMG_1981`, and the close-ups `IMG_1982`,
`IMG_1983`, `IMG_1987` at 12.2–14.0); the other 7 ended at 2.7–5.6. No frame
was `tone_limited`. The 19 JPEGs ended within 0.7 of the earlier run.
Takeaway: With a Lightroom-edited JPEG reference and raw files from its shoot,
expect the yellow label on the raw close-ups only. Name them in the report,
and suggest a graded DNG as reference for the raw files if the user wants them
tighter. A raw Tint of exactly +10.0 is the cap, not a solved value.

## 2026-10-03 — Exteriors and LED-lit interiors do not share a reference
Source: runs 20261003-122807, 20261003-123319 and 20261003-124236 (24 photos: sunset exteriors plus interior DNGs `IMG_1990`–`IMG_2001`); commit 0e6aab8
Finding: With one daylight exterior as reference, the interior DNGs started
8–27 away and ended at 2–12, with Shadows, Highlights, Whites and Blacks
driven to ±50 or beyond. The set came out inconsistent. From run 20261003-130510
on, the user selected only the 14 exteriors.
Takeaway: When the selection holds both exteriors and interiors, propose two
batches before running, each with its own graded reference.

## 2026-10-03 — Sunset frames resist a blue-sky reference
Source: run 20261003-133626, `IMG_1973.JPG` (light error 9.6 → 9.9, `look_limited`); run 20261003-140801, `IMG_1961.JPG` (7.7 → 7.4 after a Tint +6, Highlights −15 nudge) and `IMG_1973.JPG` (Exposure +1.14, `tone_limited`)
Finding: The reference is a daylight frame with a blue sky. Frames shot under a
sunset sky did not improve under per-photo look matching, hit the tone limit
under the first fitted grade, and did not respond to a white-balance nudge.
Takeaway: Do not nudge sunset frames toward a blue-sky reference. Name them in
the report as a group and recommend a batch of their own with a graded sunset
frame.

## 2026-10-03 — Raw files against a JPEG reference match well with the learned grade
Source: run 20261003-141934, `IMG_1984.DNG`, `IMG_1988.DNG`, `IMG_2004.DNG` (final errors 2.9, 1.0, 2.9; flagged `different_file_type`)
Finding: The three DNGs ended inside the same error range as the JPEGs.
`IMG_1984.DNG` ended slightly further from the reference than it started
(2.0 → 2.9) after the learned preference was added on top of an already close
start. The preference came from `learning.json` → `preference` →
`raw|iPhone 16 Pro Max|daylight`, whose reliability is in question (see
failures.md).
Takeaway: `different_file_type` alone needs no action. If a raw file's final
error is higher than its start error, say so and name the learned preference
as the likely cause.
