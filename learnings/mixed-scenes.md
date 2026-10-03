# Mixed scenes and mixed file types

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
