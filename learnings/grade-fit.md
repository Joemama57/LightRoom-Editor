# Grade fit (exported references)

## 2026-10-03 — With the original active, the copy is taken as its "original"
Source: `engine.bridge selection` before run 20261003-161341 (`active` 104 = `IMG_1964.JPG`, all sliders 0, with `IMG_1964 copy.jpg` selected); `engine/workflow.py` `find_original` and `engine/look.py` `is_baked` (read, not run)
Finding: An unedited active photo counts as a baked reference, and
`find_original` then matches `IMG_1964 copy.jpg` to it by name. The fit would
have run from the graded copy to the ungraded frame and been applied to all 14
photos. The match was not run; the user clicked the copy and the run went
ahead with `active` 6883.
Takeaway: Before matching, check that `active` in the selection is the copy
(the file with the export suffix), not its original. If it is the original,
stop and ask the user to click the copy; the bridge cannot change the active
photo.

## 2026-10-03 — A fit of 5.8 with sliders at ±80 is a partial recovery
Source: run 20261003-141934, `IMG_1964 copy.jpg` fitted from `IMG_1964.JPG` (`grade_fit/fitted.jpg`)
Finding: The fit lowered the pixel difference from 7.87 to 5.75 with an
alignment score of 0.86. Five colour sliders ended at or near their limit
(orange sat +80, orange lum +80, aqua hue −80, aqua sat −80, orange hue +62).
Applied to its own original, the grade left the wall at hue 83° against the
copy's 55° and the sky 7 L lighter (measurements in color-hsl.md).
Takeaway: When `delta_e_after` is above about 3 or the note lists sliders at
±80, open `grade_fit/fitted.jpg` next to `reference.jpg` before reviewing the
set, say in the report that the grade was only partly recovered, and name the
colour that is off.

## 2026-10-03 — The fitted grade swings between runs; do not trust its direction
Source: runs 20261003-140801, 20261003-141934 and 20261003-155051, same reference and original
Finding: The earlier fit (whole-image statistics, before commit aafd9b0) put
orange saturation and luminance at −45 and orange hue at −21. The later
pixel-by-pixel fit put orange saturation and luminance at +80 and orange hue at
+62. Opposite signs for the same pair of images, and the second still left the
wall 28° off (color-hsl.md). A third fit (`--refit`, run 20261003-155051,
8.08 → 5.22, alignment 0.86) put orange hue at −32, orange saturation at +60
and global saturation at −44, and left the wall 13° off the other way.
`unmatched` listed top left, right, left and centre.
Takeaway: Treat the orange and yellow bands of a fitted grade as unreliable for
this reference. Check the wall in every frame that shows it, measure which way
it is off before nudging (see color-hsl.md). The fitted grade is cached in
`~/.matchlook/grades/`; pass `--refit` after any engine change to the fit.

## 2026-10-03 — One learned grade plus light-only solving beats per-photo look matching
Source: runs 20261003-133626 (per-photo look), 20261003-140801 (grade, full light stage) and 20261003-141934 (grade, light only)
Finding: Per-photo look matching gave each photo different creative settings,
hit its limits on 5 of 14 photos and left light errors at 3.3–9.9. Applying one
fitted grade and solving only white balance and exposure finished in 1–4 passes
with light errors of 0.8–2.9 and no `not_converged` flags.
Takeaway: For an exported reference, always make sure the unedited original is
in the selection so the single-grade path runs. Do not fall back to the
per-photo look match unless the original cannot be found.

## 2026-10-03 — The copy is a crop of the original
Source: commit aafd9b0; run 20261003-141934 (`aligned.box` 0.066, 0.117, 0.828, 0.777)
Finding: `IMG_1964 copy.jpg` is a 4:5 crop of the 3:4 original. Before the
alignment step existed the fit silently fell back to whole-image statistics and
did not converge (look error 5.2).
Takeaway: Check `grade_fit.aligned.score` in the summary. A missing `aligned`
block or a low score means pixels were not compared; say so and treat the grade
as a rough guess.
