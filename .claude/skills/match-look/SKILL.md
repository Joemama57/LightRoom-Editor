---
name: match-look
description: Match the color grade of the active Lightroom Classic photo across the other selected photos, correcting each photo's white balance, exposure and tone for its lighting, then visually review the result (skin tones included) and learn from it. Use when the user runs /match-look or asks to apply/sync/match their grade or look across selected Lightroom photos.
argument-hint: "[strength 0-100] [skin] [color-only]"
---

# /match-look

You are the agent here. The engine measures, solves the sliders and learns from every run. Your job:
1. Check the Lightroom connection.
2. Choose the options.
3. Run the match.
4. **Look at the results yourself**, fix what still looks off, and report back.

Run every command from the repository root. Use `.venv/bin/python` if it exists; otherwise use `python3`. Below, `PY` means whichever one you picked.

## Before grading: read the learnings
This is an added step. If `learnings/` is missing or has no entries, skip this section and the "After grading" section, and carry on with steps 1–5 exactly as written.

- Read `learnings/README.md`, then `learnings/failures.md` (always), then only the topic files the README's table marks as relevant to this job.
- Run `PY -m engine.learning show` and keep its output to compare against after the run. This is the only way to read `~/.matchlook/learning.json`: never edit that file directly and never run `engine.learning reset`. The normal commands (match, nudge, learn) update it as designed.
- Copy `~/.matchlook/learning.json` to `~/.matchlook/learning.json.bak` before the match.
- Apply the relevant takeaways when choosing options, reviewing and nudging. They inform your judgement inside steps 1–5; they do not replace any step or limit there.
- If an entry conflicts with what `engine.learning show` reports, or a learned value looks stale or wrong, tell the user with the evidence. Do not resolve it yourself.

## 1. Check the connection
Run `PY -m engine.bridge ping`.

If it fails, stop and tell the user:
- Lightroom Classic must be open.
- The plugin must be added and enabled: **File ▸ Plug-in Manager ▸ Add ▸ `MatchLook.lrplugin`**.
- **Library ▸ Plug-in Extras ▸ Match Look Bridge Status** should say it is running.

Then run `PY -m engine.bridge selection` and confirm:
- There is an `active` photo. This is the graded reference.
- At least one other photo is selected.

Tell the user in one line which photo is the reference and how many photos will change.

**Mixed sets.** If the selection mixes very different scenes (exteriors in daylight and interiors under coloured LED lighting, or day and night), one reference can't fit them all. Before matching, look at the thumbnails from step 3's `contact_sheet_before.jpg`, or ask. Suggest running each scene type as its own batch, with its own graded reference:
- select the exteriors and click a graded exterior, run, then
- select the interiors and click a graded interior, run.

Photos the engine marks `different_scene` already get colour-only matching and a yellow label. They are the sign that a split is needed. If nothing is active, ask them to click the graded photo so it is the most-selected one.

## 2. Choose the options
Read `$ARGUMENTS`. If it doesn't say, decide from the reference preview. You can see it after step 3; re-run with different options if you got them wrong.

- **Strength:** a number from 0 to 100 becomes `--strength N/100`. The default is 1.0.
- **`--skin`** if people are in the photos, or the user says skin/portrait. This keeps skin tones consistent using a model built from measured skin colour (`docs/SKIN_TONES.md`).
- **`--color-only`** if the user wants each photo to keep its own brightness (a deliberately dark or bright frame, a mood sequence), or says "just the colour". Don't use it for ordinary sets such as exteriors in daylight: it also stops contrast from being matched.
- **`--even-shoot-tone`** when photos from the reference's own shoot came out brighter or darker than it at the same settings (an indoor ceremony on manual exposure, frames toward the stage lights or the crowd), or the user says some frames are too bright, too dark or hazy. Off by default.
- Since 2026-10-03 evening the shoot's brightness is **on by default** (the user chose it): `--even-shoot-tone` is no longer needed, and `tone_from_shoot` can appear without it. Pass **`--no-even-shoot-tone`** only when the user wants photos from the reference's shoot kept at its exposure.
- **A reference exported from another editor** (for example `IMG_1964 copy.jpg`, with its edit baked into the pixels and no Lightroom settings to copy) needs its **unedited original selected too**. The engine finds it by name (`IMG_1964.JPG`). It learns the grade by fitting Lightroom settings that turn the original into the copy, then applies that **same** grade to every photo and solves only each photo's light.
  - If the reference's name doesn't make the original obvious, pass `--original FILE_NAME`.
  - If the summary has a warning that the original wasn't found, tell the user to select the original too and re-run. Without it, only a small, conservative per-photo colour match is possible.
  - The copy may be a crop of the original: it is lined up automatically. The fit renders the original about 30–40 times (a minute or two) to measure how this Lightroom responds. The result is kept, so later runs with the same reference reuse it instantly. `--refit` learns it again, for example after the user re-edits the copy.
  - With a learned grade, each photo only gets its white balance and exposure solved. The grade sets the tone.
  - `--look-strength 0.5` applies half of the learned grade. `--no-look` skips it.
  - **If the user edited the original in Lightroom** and exported the copy from it, the exact settings beat a fitted grade. Tell the user how to use them instead:
    1. On the original, open Develop ▸ History and click the last step of their own edit, before the first Match Look change, or click its oldest "Before Match Look" snapshot.
    2. Check that it looks like the copy, then select the original as the active photo with the others (not the copy) and re-run.

    The engine doesn't copy masks. If the edit used AI masks (sky, subject), suggest Lightroom's Sync Settings ▸ Masking from the original afterwards: Lightroom detects the masks again on each photo.
    When the summary warns that the reference has local adjustments, read `reference.local_corrections` in the report: each lists its masks and the local sliders it moves (for example a sky mask with LocalExposure2012 -0.6). Tell the user these didn't carry over, and for a sky or subject mask offer the matching review nudge (`--mask sky Exposure2012=...`) on the photos that need it.
- **A reference from outside the catalog** (someone else's photo, a downloaded or shared image, or any reference with no Lightroom edits whose original isn't selected): the engine reads one grade from its pixels and puts it on every photo. This replaces the small per-photo colour match as the default for this case.
  - The summary has a warning saying so, and a `style_grade` block. That warning is expected here; only ask for the original if the reference is the user's own export.
  - The grade covers the tone curve's ends (faded or crushed blacks, soft highlights), contrast, saturation, the tint of shadows and highlights, and the colours both the reference and the photos show. Each photo's white balance and exposure are solved first, and the grade keeps each photo's mid-tone brightness.
  - It works best when the reference shows a similar kind of scene (a portrait for portraits, a landscape for landscapes). A very different scene can carry its content into the grade.
  - `--look-strength 0.5` applies half of the grade. `--look-per-photo` goes back to the old small per-photo colour match.
- **Learning is on by default.** Each run:
  - first learns from any edits the user made to the previous run's photos;
  - starts from the slider response learned on this camera;
  - applies learned preferences.

  Add `--no-learning` only if the user asks.

If the user has never run Match Look before (`PY -m engine.learning show` reports 0 runs and no sensitivity samples), offer calibration. It takes about 30 s per photo and puts every photo back exactly as it was afterwards. Run it only if they agree: `PY -m engine.workflow calibrate` on 3–5 selected photos in different light. Afterwards, ask them to reselect the reference as the active photo.

Three opt-in skin options (added; leave them off unless the user asks for them or a run's faces keep coming out off):
- `--face-skin`: skin is measured only inside detected faces, not from every skin-coloured pixel (sand, gold, cream outfits). A photo with no face found gets no skin term. It needs OpenCV (`PY -m pip install -r requirements-faces.txt`); without it the run warns once and falls back to colour-picked skin.
- `--skin-error`: the solve judges each render with a skin-weighted CIEDE2000 error (faces count 4x the background).
- `--skin-wb`: white balance is solved toward the reference's skin hue (from faces) instead of the neutrals; neutrals may drift at most 10 a*b* from the reference's. It turns on `--face-skin`. All three turn on `--skin`; the report lists them under `options.subject`.

## 3. Match
Run `PY -m engine.workflow match [options]`.

It writes the look and the solved sliders into Lightroom. It takes a "Before Match Look" snapshot on every target first. It prints a JSON summary with:
- the `run` folder and both contact sheets
- `reference_skin`
- for each photo:
  - `start_error`: how far off it was with the look only pasted
  - `final_error`: how far off its light (neutrals and brightness) is after matching (under 2 is a good match)
- `grade_fit` (only for an exported reference):
  - `original`
  - `aligned`: where the copy sits in the original, with a match score
  - `delta_e_before` → `delta_e_after`: the pixel difference between the original and the reference, before and after fitting. Under about 3 means the grade was recovered well. Above about 6 usually means the copy has local edits (masks, brushes, sky replacement) that global sliders can't reproduce: tell the user.
  - `note`: the biggest parts of the grade, for example "blue sat +20, contrast +15"
  - `cached`: the grade was reused from an earlier run
  - `at_limits`: grade sliders that ended at their bound
  - `unmatched`: regions still far off after fitting (for example "top" or "left"). These were probably edited locally in the copy (a mask or brush), so global sliders can't follow them: tell the user, and suggest an AI-mask nudge (`--mask sky`) if that region matters
- `look_start_error` → `look_final_error` and `look_note` per photo: only when the conservative per-photo colour match ran
- `style_grade` (only for a reference from outside the catalog):
  - `look_settings`: the one grade put on every photo
  - `note`: its biggest parts, for example "saturation -24, contrast -20"
  - `error_before` → `error_after`: how far the photos' look was from the reference's, before and after, averaged over the set. `look_start_error` → `look_final_error` per photo are the same measure.
  - `limited`: a slider ended at its bound, usually because the reference's content differs from the photos'
  - `flags`
  - `learned_adjustment`: a learned preference that was applied, if any
  - `skin_vs_reference`

Each photo's full details are in `RUN/report.json`. That includes its skin description: ITA class, nearest Monk tone, hue note.

## 4. Review with your own eyes
Read both images with the Read tool:
- `contact_sheet_before.jpg` shows the look pasted, with no correction.
- `contact_sheet.jpg` shows the final result. The reference is the first tile.

For each photo, compare it against the reference. Look at:
- **overall warmth and tint**
- **skin tones**, the most important if people are present. Use `skin_vs_reference` and `skin.hue_note` from the report as a second opinion, but trust what you see.
- **brightness of the main subject**
- **contrast and depth**
- **colour families**: the sky's blue, paint and other key colours, and how saturated the whole frame is

Ignore differences that come from the content itself (a different background, a bright sky in one frame). The goal is the same look, not identical pixels.

For any photo that clearly doesn't match, **nudge the whole photo:**
```
PY -m engine.workflow nudge --run RUN --photo FILE_NAME Temperature=+150 Tint=-3 Exposure2012=+0.15
```
- **Values are relative.** For raw files, Temperature is in Kelvin (warmer = +). For JPEGs it is an offset from −100 to 100.
- **Keep nudges small:**
  - Temperature: at most ±400 K on raw, or ±10 on JPEG
  - Tint: at most ±8
  - Exposure2012: at most ±0.5
  - Shadows2012, Highlights2012, Whites2012, Blacks2012: at most ±20
- **Look sliders can be nudged too** (relative, at most ±10 per nudge):
  - `Contrast2012`
  - `ParametricShadows`, `ParametricDarks`, `ParametricLights`, `ParametricHighlights`
  - `Vibrance`, `Saturation`
  - per colour: `HueAdjustment<Band>`, `SaturationAdjustment<Band>`, `LuminanceAdjustment<Band>`, where the band is Red, Orange, Yellow, Green, Aqua, Blue, Purple or Magenta

  For example, a sky that is still too pale: `SaturationAdjustmentBlue=+8`.

**Experimental: nudge only part of the photo.** Use this for mixed light, for example a person lit by a window in a tungsten-lit room. Only use it when a whole-photo nudge can't fix it, because fixing the subject would ruin the background or the other way round:
```
PY -m engine.workflow nudge --run RUN --photo FILE_NAME --mask subject Temperature=-10 Exposure2012=+0.2
```
- `--mask` takes `subject`, `sky`, `background` or `people`.
- Inside a mask, Temperature and Tint are −100..100 local sliders, and Saturation is also allowed. Keep these changes at ±15 or less.
- It switches Lightroom to the Develop module and creates an AI mask the first time, which takes a few seconds. It then puts the user's selection back.
- If it fails, say so and leave that photo for the user. Lightroom versions differ in AI-mask support.

**Limits on reviewing:**
- Re-read `contact_sheet.jpg` after each round. Do at most 2 rounds per photo.
- If a photo still looks wrong after that, leave it and report it.
- `different_scene`: a dark, coloured-light frame against a normally lit reference. Only its colour was matched; its brightness was left alone. Don't nudge it toward the reference; recommend matching it in a batch with a similar reference instead.
- `tone_limited`: exposure or tone hit the safety limit (±2 EV, ±40 on the tone sliders) and still doesn't match, usually a content difference. Check it doesn't look too dark or flat before nudging.
- Photos flagged `not_converged` with a high error often differ in **content**, not light: half the frame is foliage, or a dark interior. Look before nudging; usually they're fine or need the user's eye.
- `look_limited` (per-photo colour match only): a colour slider hit its limit and the photo still differs, usually because the content differs. Check it before nudging.
- **With a reference from outside the catalog:** if the grade looks too strong or carries the reference's content (for example a whole set turned teal because the reference was mostly sea), say so and suggest a re-run with `--look-strength 0.5`. If one part of the grade is off on every photo, use one `nudge --photo all --grade` command rather than per-photo nudges.
- Never change photos outside this run. Don't change other creative settings such as profile, grain or vignette: those stay exactly the reference's.
- `same_shoot`: the photo comes from the reference's own shoot (same camera and file type, taken within 3 hours). It keeps the reference's exposure and tone, with at most ±0.3 EV of correction, because the camera already evened out the light. Its `final_error` is high when its content differs (a close-up, more sky): that's expected.
  - Don't nudge its exposure by comparing one object's brightness (for example white paint L*) with the reference: different framing changes those numbers.
  - Only nudge its exposure if it clearly looks darker or brighter than the reference on the contact sheet, by at most ±0.3, and say why.
  - A raw file from a JPEG reference's shoot (or the other way round) carries both `same_shoot` and `different_file_type`. Its light is solved, but its tone sliders stay within ±15 and its Tint within ±10, because the two formats render differently. Judge its white paint by eye, and nudge Tint by at most ±6.
  - A raw `same_shoot` photo whose white balance came from the camera keeps it within ±20 mired and Tint ±10, because the light was the same.
  - `wb_from_shoot`: with 3 or more raws from the shoot, this photo's own white-balance solve moved far from the rest (it followed its content, such as a yellow saree or an orange wall), so it got the shoot's median move instead. Check a neutral surface (a white wall) against the reference before nudging, and nudge Temperature only if that surface is off.
  - `wb_from_reference` (only with `--hold-shoot-wb`): a raw from the reference's shoot that is no longer on As Shot (matched or synced before) has no camera reading, so it keeps the reference's own Temperature and Tint exactly, the ones the user chose for this light. Its own solve would follow sand, grass or cream outfits instead. Judge it next to the reference; if a photo really was shot in other light (deep shade, sunset), nudge its Temperature and say why.
  - `wb_offset_from_reference`: the reference's own white balance was set far from what its camera recorded (read off a photo shot within 10 minutes of it, see `reference_wb_offset` in the report). That choice was carried to this photo: it starts from its own camera reading plus the same offset. Photos from other light keep their own reading but may move far enough to reach the reference's value.
  - A later photo from the shoot with exactly the same camera white balance (a camera set to a fixed Kelvin for the whole ceremony) gets the reference's offset too, however much later it was shot.
- If two or more photos end at exactly the same Temperature, say it is probably a limit, not a solved value.
- `exposure_from_skin` (with `--skin`): matching the frame's overall brightness followed its content (bright clothes, a bright backdrop), so its exposure was set from the faces instead; `skin_exposure` in the report gives the before and after. Judge its brightness on the faces, not the backdrop, and don't nudge its exposure back down unless the faces look brighter than the reference's.
- `color_from_skin` (with `--skin`): after the solve the faces were still more than 2 off the reference's skin colour (the report's `skin_vs_reference` says "more magenta", "bluer / cooler" and so on), so its Temperature and Tint were moved a little to bring them closer, and kept only because they did; `skin_color` in the report gives the before and after. Judge it on the faces, and check a white surface before nudging its white balance back. The report's `trace` lists every render of the solve (sliders, neutral a*/b*, skin a*/b*, error): read it to see why a photo stalled, for example a Tint that never moved.
- `skin_hue_from_faces` (only with `--skin --skin-hue`): the faces were more than 3° redder (more magenta) than the reference's while the white-balance pass couldn't help without turning the rest of the frame green, so Orange hue was raised a little (at most +15) and kept only because the faces got closer; `skin_hue` in the report gives the hue before, after and the reference's. Check warm decor (gold, orange flowers) on that photo: Orange hue moves those too.
- `skin_not_matched` (with `--skin`): what read as skin in this photo was far more (or less) colourful than the reference's faces, like candles and flowers at night, so it wasn't matched on skin. Its skin notes in the report are not about faces; don't act on them.
- With `--face-skin` (or `--skin-wb`), the skin numbers in the report and `trace` come from faces only, so they differ from earlier runs' colour-picked skin; compare them within a run, not with older runs. A photo whose faces weren't found has no skin numbers.
- `tone_from_shoot` (only with `--even-shoot-tone`): a photo from the reference's shoot was moved toward the reference's mid-tone brightness (keeping about a third of the gap as content), and if it was a bright frame that had turned hazy, its Shadows lift was halved. Faces had the last word, and each change was kept only because the frame got closer; `shoot_tone` in the report gives the exposure, Shadows, mid-tones and p25..p75 spread before and after, and the reference's. Judge it on the faces next to the reference.
  - When both photos have enough white and grey (clothes, garlands, walls), `shoot_tone.judged_on` is `whites`: brightness was matched on those, not on the whole frame, because gold walls or coloured drapes make a frame read bright while its whites look dull. Judge it on the white clothes next to the reference's.
- With more than 2 photos, the run folder also has `contact_sheet_1.jpg`, `contact_sheet_2.jpg`, ...: the reference next to 2 photos at a time, with bigger tiles. Judge colour (warmth, skin, saturation) on those rather than on the small tiles of the big sheet, and show the user the one that matters when you report.
- The `same_shoot` window is 6 hours, not the 3 stated above: a stage is lit the same for a whole afternoon, so a close-up shot hours later keeps the reference's exposure and tone too, instead of being darkened to the wide reference's histogram.
- `calmer_colour`: the photo ended much more colourful than the reference (a close-up full of gold and red against a wide reference), so Vibrance and Saturation were lowered, never raised, by at most 15 each; `calmed_colour` in the report gives the chroma before, after and the reference's, and `look_settings` the new values. Judge the colours against the reference's. Don't raise them back unless the photo looks dull next to it.
- `wb_held_same_reading`: a raw from the reference's shoot with exactly the camera reading of the photo shot next to the reference (a camera set to a fixed white balance) starts at the user's own white balance from the reference and stays within ±5 mired and Tint ±3 of it, however close that choice was to the camera's. Judge it next to the reference; if it really was shot in other light (outdoors, deep shade), nudge its Temperature and say why.
- A photo more colourful than the reference whose skin is not more colourful than the reference's keeps the reference's Vibrance and Saturation (log: "not in its skin"): the extra colour is a dress, walls or flowers, not too strong a grade.
- `skin_note` in the report says when the skin numbers came from skin-coloured areas rather than detected faces. Then "faces off by", `skin_vs_reference` and the skin pass can be about walls, wood or gold; check the faces by eye before acting on them.
- `final_error_with_tone`: for a same-shoot photo, the error including brightness. `final_error` is colour only for those photos, so a low `final_error` can hide a brightness gap.
- After a nudge, `error_before_nudge` keeps the run's own error and `error_note` says "after your nudge": a warmer error after the user's deliberate warm-up is not the photo getting worse.
- `wb_at_limit`: a raw from the reference's shoot whose white-balance solve ran into the shoot's limit (20 mired or 10 Tint from where it started) was following its content (sand, cream outfits, lamp-lit sheets), not its light, so it keeps the white balance it started from: its camera reading (plus the user's offset when that applies), or the reference's own for a raw no longer on As Shot. Judge it next to the reference; if it really was shot in other light, nudge its Temperature and say why.
- Calmer colour is not used when the extra colour is only saturated reds and pinks (flowers, a dress), nor on a photo from the reference's shoot unless faces were detected (`--face-skin`): in the same light with the same sliders, extra colour is the scene's. The log says which.
- `--neutral-no-warm` (experimental, off by default): leaves cream, gold, sand and dry grass out of the neutral reading, so a beach or gold-outfit frame isn't cooled for them. `--bright-neutrals` (experimental, off by default): counts bright whites up to L* 95 (a window-lit curtain) as neutrals, for mixed window and lamp light. Use them only when the user asks or to compare a rerun; say which was on.
- `--face-tone` (experimental, off by default, turns on `--face-skin`): a photo from the reference's shoot that reads as another kind of scene (a black doorway beside a close-up) but whose faces are more than 3 L* darker than the reference's is brightened toward the reference's faces, up to +0.6 EV. `--copy-masks` (experimental, off by default, slow): copies the reference's background mask (an inverted Select Subject mask) with its sliders onto every photo, dropping its Temperature, or the mask, where that makes the colour worse; the report lists it under `copied_masks`. With it on, don't also nudge the background Temperature by hand to recreate the reference's mask.
- A log line saying the previous run's photos aren't in the open catalog is expected after the user switches catalogs. It is not a fault: that run isn't learned from. To learn from it, reopen its catalog and run `PY -m engine.workflow learn --run RUN`.
- **When the user approves some photos and not others:** if the approved ones carry a look nudge the others lack, apply that same change to the whole set and to the stored grade with one command, instead of nudging photos one by one:
  ```
  PY -m engine.workflow nudge --run RUN --photo all --grade HueAdjustmentOrange=-20 HueAdjustmentYellow=-20
  ```
  `--grade` takes look sliders only. The correction is kept with the reference's grade for the next run; `--refit` drops it.

Every nudge you make, and every edit the user makes later in Lightroom, is learned from at the next run. You don't need to run anything extra. To learn right away, for example before closing, run `PY -m engine.workflow learn`.

## 5. Report
Keep the report short:
- **A table:** one row per photo with the file name, error before → after, any learned adjustment applied, any nudge you made and why (for example "skin looked green, Tint +4"), and flags.
- **Grade**, for an exported reference: one line from `grade_fit`, for example "learned the edit from IMG_1964.JPG: off by 1.2 after fitting; blue sat +20, contrast +15".
- **Grade**, for a reference from outside the catalog: one line from `style_grade`, for example "read the grade from the reference's pixels: saturation -24, contrast -20; look 3.6 → 2.5".
- **Skin**, only with `--skin`: one line on how consistent it is across the set. Use the report's words (for example "DSC0042: greener than the reference, fixed").
- **Flagged photos:** which photos have the yellow label, and why each needs the user's eye.
- **What was learned:** one line, from `PY -m engine.learning show`. For example "learned from 2 edits you made last time; this camera's slider response now has 40 samples".
- **Skipped for learning:** if the log or the report's `learned` says a run was skipped (undone, too many flags, poor grade fit), say so in one line. If the user says this run went badly, suggest `PY -m engine.workflow learn --skip` so it is never learned from.
- **How to undo:** the "Before Match Look" snapshot on each photo (Develop ▸ Snapshots), or Edit ▸ Undo. If the summary has a snapshot `warning`, say that undo is through the History panel instead.

## After grading: record what you found
This is an added step, done after the report in step 5. Skip it if `learnings/` is missing or has no entries.

- Add each new finding to the matching topic file in `learnings/`, newest first, in the README's entry format: dated, citing the run folder and photo, ending with a takeaway.
- Only record a finding backed by a measurable result (an error, a slider value, a measured colour, a flag) or by something the user said. Leave out impressions you can't tie to either.
- Don't copy values from `learning.json` into an entry. Point at the key instead (for example "see `learning.json` → `preference` → `raw|iPhone 16 Pro Max|daylight`").
- If a new result contradicts an existing entry, edit that entry and say what changed.
- If you added a topic file, add its row to the table in `learnings/README.md`.
- Add one line to the report: which entries you applied and which you added.

## Replaying a run without Lightroom
This is an added step for engine work, not for grading.
- `PY -m engine.replay check RUN` shows how closely the run's saved previews can be reproduced (leave-one-out ΔE per photo); `PY -m engine.replay rerun RUN` runs today's engine on that run's photos and writes `RUN/replay/<time>/` with its own report and contact sheets. Learning is never used or changed.
- To make a run a benchmark, the user fixes a few of its photos by hand in Lightroom after `PY -m engine.workflow learn --run RUN --skip`, then runs `PY -m engine.replay answers RUN`. `PY -m engine.replay bench RUN...` then scores the run as it ran and as today's engine re-runs it against those photos.
