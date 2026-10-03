# Evaluation: what the numbers and flags are worth

## 2026-10-03 — A low light error does not mean the look matches
Source: run 20261003-141934, `IMG_1964.JPG` (`final_error` 1.6 with the wall 28° off in hue and the sky 7 L too light, see color-hsl.md); commit 047ccf8 ("reported low errors but didn't look matched")
Finding: `final_error` covers neutrals and brightness only. A photo scored 1.6
while two of its colour families were measurably off.
Takeaway: Report `final_error` as the light match. Judge the look separately,
by eye, on contrast, sky and the main coloured surface, and say which of those
were checked.

## 2026-10-03 — For an exported reference, read the grade fit before the photo errors
Source: runs 20261003-140801 (`look_error` 5.2) and 20261003-141934 (`delta_e_after` 5.75)
Finding: In both runs the per-photo numbers described how well each photo
reached a grade that was itself several units away from the reference. The
ceiling on the whole set was the fit.
Takeaway: Put the grade-fit line at the top of the report. If
`delta_e_after` is above 3, lead with that.

## 2026-10-03 — Re-running an unconverged set gives the same result
Source: runs 20261003-130510 and 20261003-130802 (13 of 14 photos `not_converged` in both, errors 2.1–8.5)
Finding: Two consecutive full matches with the same options landed close to
each other on 12 of 14 photos (within 0.25) and left the same 13
unconverged.
Takeaway: Re-running with the same options does not help `not_converged`
photos. Look at them once, then report them without re-running.

## 2026-10-03 — Yellow labels on every photo carry no signal
Source: runs 20261003-130510, 20261003-133626 and 20261003-140801 (12 or 13 of 14 photos flagged)
Finding: When nearly every photo is flagged, the Lightroom label and the
contact-sheet outline stop singling anything out.
Takeaway: If more than half the set is flagged, pick the photos that need the
user's eye from the contact sheet and name them, with the reason for each.
