# Exposure and tone

## 2026-10-03 — A dark night interior gets blown out by a daylight reference
Source: runs 20261003-122807, 20261003-123319 and 20261003-124236, `IMG_2010.JPG` (Exposure +4.56 to +5.0, error 47 → 29–33, `mostly_clipped`)
Finding: `IMG_2010.JPG` is a dark, blue-lit night interior. Against a daylight
exterior reference the solver pushed exposure to its ceiling three runs in a
row and the frame came out blown and clipped. Commit 0e6aab8 then limited
exposure to ±2 EV and added the `different_scene` flag.
Takeaway: Take night or LED-lit interiors out of a daylight batch before
running. If one is flagged `different_scene` or `mostly_clipped`, do not nudge
it; recommend its own batch with a graded interior as reference.

## 2026-10-03 — `--color-only` keeps the last run's brightness, not the original's
Source: run 20261003-131154, `IMG_1974.JPG` (still Exposure −1.32) and `IMG_1968.JPG` (tone sliders still at ±40)
Finding: The colour-only run reported errors of 2.0 or less on all 14 photos,
but the brightness each photo "kept" was whatever the previous full match had
written, including sliders at the ±40 limit.
Takeaway: Before a `--color-only` run on photos that were matched earlier, tell
the user the brightness comes from the previous run. If they want the original
brightness, they need to restore the earliest "Before Match Look" snapshot
first.

## 2026-10-03 — Free tone sliders run to their limits on detail crops
Source: runs 20261003-124236, 20261003-130510 and 20261003-130802, `IMG_1968.JPG` (wheel) and `IMG_1974.JPG` (wheel arch): Highlights, Whites and Blacks at ±40 to ±81, flagged `tone_limited`
Finding: When Shadows, Highlights, Whites and Blacks were solved per photo, the
close-ups with no sky hit the limit run after run and the values changed sign
between runs. With the learned grade and light-only solving (run
20261003-141934) the same photos needed only exposure (+0.81 and −0.84) and
finished at errors of 1.9 and 0.8.
Takeaway: Do not nudge tone sliders on close-ups to chase the error. If a
close-up is `tone_limited`, check it for a flat or dark look and otherwise
leave it.
