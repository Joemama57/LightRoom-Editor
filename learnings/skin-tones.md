# Skin tones

No run with people in frame has been reviewed yet. The measured skin model is
documented in `docs/SKIN_TONES.md`; this file holds only what real runs showed.

## 2026-10-03 — Skin notes on a set with no people are false readings
Source: runs 20261003-124236 to 20261003-141934 (car exteriors); `reference.skin` in run 20261003-141934 reports "dark, Monk tone 7" on a frame with no person
Finding: The engine classified the orange wall, the number plate and warm
reflections in the paint as skin. Every nudge result carried a
`skin_vs_reference` note ("greener, bluer / cooler, brighter") although nobody
is in the photos.
Takeaway: Look at the contact sheet for people before reading any skin note.
With no people, do not pass `--skin`, do not act on `skin_vs_reference`, and do
not repeat the skin notes in the report.
