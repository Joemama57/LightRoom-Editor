# White balance

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
