# White balance

## 2026-10-03 — A same-shoot raw with a wrong as-shot white balance was left untouched
Source: run 20261003-175141 (reference `DSC01303.ARW`, ILCE-7M4 raw at 2662 K, Tint 0, `--skin`), `DSC01318.ARW`, `DSC02121.ARW`, `DSC02201.ARW` (`report.json`, `contact_sheet.jpg`)
Finding: `DSC01318.ARW` was taken 28 s after the reference on the same stage,
with an as-shot white balance of 5550 K, Tint 11. It carries `same_shoot` and
`wb_from_camera`, and after 6 iterations its `matched` Temperature and Tint
equal its `start` (5550, 11.0); the error stayed at 18.46 (`not_converged`) and
its skin read chroma 48.9 against 24.9 on the reference. With only one
same-shoot raw there is no shoot median, so `wb_from_shoot` cannot apply. The
two frames shot 3.6 h later (as shot 3800 K, not `same_shoot`) were solved and
both ended at exactly 2914 K, with errors 18.5 → 3.9 and 22.5 → 4.2. The gap
on `DSC01318.ARW` is about 2900 K, beyond two ±400 K nudges, so no nudge was
made.
Changed by run 20261003-180212 (same selection and reference, re-run 11 min
later with no edits in between): the earlier run had left all three photos at
white balance "Custom", so this run read `wb_from_camera` false on each, started
every photo from the reference's 2662 K, Tint 0 and solved from there.
`DSC01318.ARW` ended at 2654 K, Tint 2.8, Exposure +0.49 (the +0.3 cap), error
6.7 → 3.55, flag `same_shoot` only, skin a 13.5, b 23.2, chroma 26.9 against
10.3, 22.7, 24.9. The later frames ended at 2588 K and 2741 K (no longer the
same value) with errors 18.2 → 1.6 and 12.0 → 1.4 and no flags.
`reference_wb_offset` was null, so `wb_offset_from_reference` did not apply.
Takeaway: Before matching, compare each `same_shoot` raw's as-shot Temperature
with the reference's. If one is more than about 1500 K away and ends with
`matched` equal to `start`, do not nudge: report it, and offer a second match
on the same selection, which starts it from the reference's white balance
because the photo is no longer "As Shot" (or give the reference's Temperature
and Tint for the user to type in). Two photos ending at the same
Temperature to the kelvin is probably a limit, not a solved value: say so.

## 2026-10-03 — A single portrait against a white wall was solved 2800 K too warm
Source: run 20261003-173030 (reference `DSC00183.ARW`, ILCE-7M4 raw, `--skin`), `DSC00232.ARW` (`iter_0/`, `final/`, `nudges/00_`, `01_`); median CIELAB of a patch of the white wall
Finding: The reference's wall measured b −9.4 to −10.2. With only the look
pasted and the as-shot white balance (6750 K, Tint 26), `DSC00232.ARW`'s wall
measured b −8.5, within 1 of the reference. The match moved it to 9580 K,
Tint 37.6 and the wall to a +4.3, b +5.4 (`not_converged`, error 10.1 → 4.4).
Two nudges (−400 K and Tint −8, then −400 K) brought the wall to a +0.7,
b +3.6, still about 13 b from the reference, and raised the error to 7.1. The
frame is a single person in a yellow saree in front of an orange opening; the
other six frames, all couples, ended at 5370–6110 K.
Changed by run 20261003-174742 (same selection and reference, after commit
6978868): `DSC00232.ARW` carries `wb_from_shoot` and ended at 6487 K, Tint 21.0
(as shot 6750, 26). Its wall measured b −6.1 (left) and −7.0 (right) in
`final/`, against −9.4 on the reference, with no nudge. Its error stayed at
10.8 (`not_converged`, start 10.1), so the number does not follow the wall.
In the same run the wall behind three couple frames (`DSC00201`, `DSC00214`,
`DSC00256`, Tint solved to 20.0–23.0 from 24–30 as shot) measured a −3.7 to
−4.9, b −13.6 to −13.8, against a −1.2, b −9.4 on the reference, while their
skin read a 8.7–9.4 against 7.7. Wall and skin disagree on the Tint direction,
so no Tint nudge was made.
Takeaway: When a `same_shoot` raw ends more than about 1500 K from its as-shot
value while the rest of the set stays near theirs, measure a neutral surface in
`iter_0/` against the reference before nudging. If `iter_0` is already close,
the as-shot value was right: two ±400 K nudges cannot undo it, so report the
as-shot Temperature and Tint for the user to type in. The error number rises as
the wall gets closer (nudges.md). With `wb_from_shoot` on the photo, expect
the wall within about 3 b of the reference and a `not_converged` error near
its start value: check the wall, and leave the Temperature alone if it holds.

## 2026-10-03 — Raw close-ups end at Tint +22 to +24 and the white paint reads pink
Source: run 20261003-163645 (reference `IMG_1963.JPG`, edited in Lightroom, no grade fit), `IMG_1982.DNG`, `IMG_1983.DNG`, `IMG_1987.DNG` (`iter_6/`, `final/`, `nudges/00_`–`02_`); brightest 15% of each preview, median CIELAB
Finding: The three DNG close-ups were solved to Tint +21.8, +23.6 and +24.3
(Temperature 6800–7950) and stayed `not_converged` at errors 11.7–13.2. Their
white paint measured a +4.9, +8.0 and +7.0, against +1.0 on the reference and
+3.3 to +4.4 on the JPEG close-ups of the same panels (`IMG_1967.JPG`,
`IMG_1969.JPG`, `IMG_1970.JPG`). `Tint=-6` on each lowered a by 1.2–1.4 (to
+3.7, +6.8, +5.6), left b and L unchanged and moved the error by 0.2 or less.
Changed by run 20261003-165410 (after commit 868a48e caps raw Tint at ±10 for
a JPEG reference's shoot): the same three DNGs ended at Tint +10.0, +3.7 and
+10.0, and their paint measured a +2.4, +4.6 and +3.4, against +4.0 to +4.4 on
the JPEG close-ups (`IMG_1969.JPG`, `IMG_1970.JPG`, `IMG_1980.JPG`). No Tint
nudge was needed.
Takeaway: The rule below applies only if a raw close-up ends above Tint +20,
which the cap now prevents for same-shoot sets. On a raw close-up of white paint with Tint above +20, measure the
paint's a against the JPEG close-ups; if it is 3 or more higher, nudge Tint −6
in the first round. Expect about 1.3 of a per 6 of Tint, so `IMG_1983.DNG` and
`IMG_1987.DNG` need a second round to reach the JPEGs. The nudge is learned as
a raw preference at the next run (nudges.md): say so in the report.

## 2026-10-03 — JPEG white balance must go through the incremental sliders
Source: run 20261003-122807 (10 JPEGs ended at Temperature +100, Tint ±40, all `mostly_clipped`); session 7324522b; uncommitted change in `engine/bridge.py`
Finding: Lightroom Classic 15 keeps white balance for JPEGs in
`IncrementalTemperature` / `IncrementalTint` (−100..100). Writing `Temperature`
to a JPEG was read as Kelvin and pinned it, so the solver ran every JPEG to the
limit and nothing converged. The fix (the bridge translates both ways) exists
only as an uncommitted change in `engine/bridge.py` and `engine/settings.py`.
The slider responses measured before the fix were set aside in
`~/.matchlook/learning.json.bad-20261003` and are not read by the engine.
Takeaway: If JPEGs come back at Temperature ±100 or Tint ±40, stop and check
that the bridge translation is still in the working tree before re-running.
Do not restore the `.bad` file.

## 2026-10-03 — The reference copy carries its own white-balance offset
Source: `engine.bridge selection` at the start of run 20261003-141934 (`IMG_1964 copy.jpg`: Temperature −7, Tint −15, Exposure +0.04, white balance "Custom")
Finding: The exported reference is not untouched in Lightroom: it has develop
settings on it. Every photo's solve starts from those values (each photo's
`start` in `report.json` is −7 / −15 / +0.04), and the reference preview is
rendered with them. It is not known whether the user set them or an earlier run
wrote them.
Takeaway: Before matching against an exported reference, read its settings from
the selection. If it has non-zero Temperature, Tint or Exposure, tell the user
and ask whether those are intended, because they change the look being matched.

## 2026-10-03 — With the learned grade, JPEG neutrals match at Tint −8 to −21
Source: run 20261003-141934, 11 JPEGs (`final` and `final_error` in `report.json`)
Finding: After the fitted grade, the solver settled every JPEG at a negative
Tint (−7.9 to −21.1) and a Temperature between −20 and +10, and the error on
neutrals and brightness ended at 0.8–2.6 for all of them.
Takeaway: A consistently negative Tint across the set goes with this grade and
is not a fault. Do not nudge Tint back toward zero.
