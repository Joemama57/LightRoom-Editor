# Exposure and tone

## 2026-10-03 — A raw close-up of white paint was solved to Exposure −0.59 and its paint came out 15 L dark
Source: run 20261003-165410 (reference `IMG_1963.JPG`), `IMG_1982.DNG` (`final/`, `nudges/00_`); brightest 15% of each preview, median CIELAB
Finding: `IMG_1982.DNG` (`same_shoot`, `different_file_type`, `not_converged`)
ended at Exposure −0.59, the lowest of the set; the ±0.3 EV hold does not
apply to a raw file under a JPEG reference. Its paint measured L 66.4, against
81.2 on the reference and 75.2–91.5 on the other 26 previews in `final/`.
`Exposure2012=+0.3` raised the brightest 15% from grey level 161 to 181 and
moved the error from 14.0 to 14.1.
Takeaway: On a raw close-up filled with white paint, compare the paint's L
with the reference before trusting a negative exposure; if it is 10 L or more
lower, nudge Exposure +0.3 once. The nudge is learned as a raw preference at
the next run (nudges.md): say so in the report.

## 2026-10-03 — With a Lightroom-edited reference, 6 of 19 `same_shoot` JPEGs sat at the ±0.3 EV edge, all close-ups or side views
Source: run 20261003-163645 (reference `IMG_1963.JPG`), `final` in `report.json`; lightness percentiles of `final/` previews against `reference.jpg`
Finding: Five JPEGs ended at Exposure −0.30 (`IMG_1967`, `IMG_1970`,
`IMG_1972`, `IMG_1974`, `IMG_1980`) and one at +0.28 (`IMG_1968`); the other 13
ended between −0.24 and +0.16. The wide frames' 95th percentile measured 77–88
against the reference's 83, so none was pulled up the way the entry below
describes for the exported reference of run 20261003-161341. No exposure nudge
was made.
Takeaway: The "take 0.3 off wide frames at +0.29" rule in the entry below
belongs to the exported reference `IMG_1964 copy.jpg`. With a reference edited
in Lightroom, check the wide frames' 95th percentile against the reference
before touching exposure; within about 5 L, leave it.

## 2026-10-03 — `same_shoot` photos end at the edge of their ±0.3 EV range; the plus side is too bright
Source: run 20261003-161341 (first run after commit 9045637), 11 JPEGs flagged `same_shoot`; `final/IMG_1964_104.jpg` cropped to `grade_fit.aligned.box` against `reference.jpg`; `nudges/00_`, `05_`, `06_`
Finding: Every `same_shoot` JPEG finished at Exposure +0.29 (7 photos) or −0.31
(4 photos), the two ends of the range around the fitted −0.01, and 5 were
`not_converged`. On the reference's own frame, `IMG_1964.JPG` at +0.29 measured
L 36/82/91 at the 25th/75th/95th percentile against the reference's 31/74/83,
and its sky L 81 against 69. `Exposure2012=-0.3` (back to the fitted light)
gave 31/76/85 and sky L 74. The three DNGs are not `same_shoot` (different file
type), so they were solved freely: `IMG_1988.DNG` and `IMG_2004.DNG` took
+0.76 and +0.77 and measured a 95th percentile of 94 and 98; `-0.4` brought
them to 89 and 96. This agrees with the entry below: the brightness target
still pulls wide frames up, the new limit only caps it at +0.3.
Takeaway: After a run with this reference, treat Exposure +0.29 on a
`same_shoot` wide frame as the limit being hit, not a solved value: measure
`IMG_1964.JPG` against the reference first, and if it is brighter, take 0.3
off the wide frames that sit at +0.29. Check raw files separately; they are
not held by the `same_shoot` limit. The four frames at −0.31 are close-ups of
white paint and were left alone.

## 2026-10-03 — The light stage put white paint 8–15 L above the reference
Source: run 20261003-155051, all 14 photos (lightness percentiles of `iter_*` and `nudges/` previews against `reference.jpg`); patches on `IMG_1964.JPG`
Finding: After the match every photo's median L sat at 52–58 against the
reference's 54, but the 95th percentile was 92–99 on the nine photos given
Exposure +0.54 to +0.79, against the reference's 83. On `IMG_1964.JPG`
(Exposure +0.55) the car measured L 91 against 83.5 and the tarmac 41 against
31, while `grade_fit/fitted.jpg` (Exposure −0.01) measured 81 and 32. The
reference is half dark tarmac and half bright sky, so its median falls in the
gap between the two; the fitted grade's median was 43 with the same 25th and
75th percentiles. Reading: the brightness target follows the median, which is
unstable on a two-tone frame (not checked in `engine/`). `Exposure2012=-0.5` on
`IMG_1964.JPG` brought the car to 83.3 and the tarmac to 32.9. `IMG_1980.JPG`,
a close-up that is mostly white paint, went the other way (Exposure −0.90,
95th percentile 72) and took +0.5.
Takeaway: With this reference, check each photo's white paint against L 83 and
its tarmac against L 31 before trusting `final_error`. Expect exposure nudges
of about −0.5 on wide frames and plus on frames filled with white paint. These
nudges are learned as preferences at the next run (nudges.md): tell the user.

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
