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
- **A reference exported from another editor** (for example `IMG_1964 copy.jpg`, with its edit baked into the pixels and no Lightroom settings to copy) needs its **unedited original selected too**. The engine finds it by name (`IMG_1964.JPG`). It learns the grade by fitting Lightroom settings that turn the original into the copy, then applies that **same** grade to every photo and solves only each photo's light.
  - If the reference's name doesn't make the original obvious, pass `--original FILE_NAME`.
  - If the summary has a warning that the original wasn't found, tell the user to select the original too and re-run. Without it, only a small, conservative per-photo colour match is possible.
  - `--look-strength 0.5` applies half of the learned grade. `--no-look` skips it.
- **Learning is on by default.** Each run:
  - first learns from any edits the user made to the previous run's photos;
  - starts from the slider response learned on this camera;
  - applies learned preferences.

  Add `--no-learning` only if the user asks.

If the user has never run Match Look before (`PY -m engine.learning show` reports 0 runs and no sensitivity samples), offer calibration. It takes about 30 s per photo and puts every photo back exactly as it was afterwards. Run it only if they agree: `PY -m engine.workflow calibrate` on 3–5 selected photos in different light. Afterwards, ask them to reselect the reference as the active photo.

## 3. Match
Run `PY -m engine.workflow match [options]`.

It writes the look and the solved sliders into Lightroom. It takes a "Before Match Look" snapshot on every target first. It prints a JSON summary with:
- the `run` folder and both contact sheets
- `reference_skin`
- for each photo:
  - `start_error`: how far off it was with the look only pasted
  - `final_error`: how far off its light (neutrals and brightness) is after matching (under 2 is a good match)
- `grade_fit` (only for an exported reference): `original`, `delta_e_before` → `delta_e_after` (how far the original is from the reference before and after fitting; under 3 means the grade was recovered well), and `note` (the biggest parts of the grade, for example "blue sat +20, contrast +15")
- `look_start_error` → `look_final_error` and `look_note` per photo: only when the conservative per-photo colour match ran
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
- Never change photos outside this run. Don't change other creative settings such as profile, grain or vignette: those stay exactly the reference's.

Every nudge you make, and every edit the user makes later in Lightroom, is learned from at the next run. You don't need to run anything extra. To learn right away, for example before closing, run `PY -m engine.workflow learn`.

## 5. Report
Keep the report short:
- **A table:** one row per photo with the file name, error before → after, any learned adjustment applied, any nudge you made and why (for example "skin looked green, Tint +4"), and flags.
- **Grade**, for an exported reference: one line from `grade_fit`, for example "learned the edit from IMG_1964.JPG: off by 1.2 after fitting; blue sat +20, contrast +15".
- **Skin**, only with `--skin`: one line on how consistent it is across the set. Use the report's words (for example "DSC0042: greener than the reference, fixed").
- **Flagged photos:** which photos have the yellow label, and why each needs the user's eye.
- **What was learned:** one line, from `PY -m engine.learning show`. For example "learned from 2 edits you made last time; this camera's slider response now has 40 samples".
- **How to undo:** the "Before Match Look" snapshot on each photo (Develop ▸ Snapshots), or Edit ▸ Undo. If the summary has a snapshot `warning`, say that undo is through the History panel instead.
