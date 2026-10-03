# Skin tones

The measured skin model is documented in `docs/SKIN_TONES.md`; this file holds
only what real runs showed.

## 2026-10-03 — First set with people: skin hue held, "darker" followed the framing
Source: run 20261003-173030 (reference `DSC00183.ARW`, `--skin`, 7 ILCE-7M4 raws of one couple), `skin` in `report.json`
Finding: Reference skin measured L 59.1, a 7.7, b 15.8. The six couple frames
ended at a 7.8–8.7 and b 14.4–16.8, all "within the typical range for skin".
Three were noted "darker" (`DSC00201` L 55.0, `DSC00214` L 53.8, `DSC00256`
L 53.4); these are the closer frames, where the groom's face takes more of the
skin area, and they carry `same_shoot` with exposure within the ±0.3 cap. On
`DSC00232.ARW` (bride alone, yellow saree, henna) the reading went from L 67.6,
chroma 21.0 to L 51.6, chroma 36.1 after an 800 K cooling nudge, a change in
the wrong direction for a cooler frame.
Takeaway: With two people of different skin tone in frame, do not nudge
exposure on a "darker" note alone; compare a and b, and look at the faces. A
skin reading whose chroma jumps by 10 or more between renders of the same
photo has picked up something else (fabric, henna): disregard it.

## 2026-10-03 — Skin notes on a set with no people are false readings
Source: runs 20261003-124236 to 20261003-141934 (car exteriors); `reference.skin` in run 20261003-141934 reports "dark, Monk tone 7" on a frame with no person
Finding: The engine classified the orange wall, the number plate and warm
reflections in the paint as skin. Every nudge result carried a
`skin_vs_reference` note ("greener, bluer / cooler, brighter") although nobody
is in the photos.
Takeaway: Look at the contact sheet for people before reading any skin note.
With no people, do not pass `--skin`, do not act on `skin_vs_reference`, and do
not repeat the skin notes in the report.
