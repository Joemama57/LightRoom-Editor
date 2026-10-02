---
name: match-look
description: Match the color grade of the active Lightroom Classic photo across the other selected photos, correcting each photo's white balance, exposure and tone for its lighting, then visually review the result. Use when the user runs /match-look or asks to apply/sync/match their grade or look across selected Lightroom photos.
argument-hint: "[strength 0-100]"
---

# /match-look

You are the agent here. The engine does the measuring and slider solving; you check the Lightroom connection, run it, **look at the results yourself**, fix what still looks off, and report back.

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

Tell the user in one line which photo is the reference and how many photos will change. If nothing is active, ask them to click the graded photo so it is the most-selected one.

## 2. Match
Run `PY -m engine.workflow match --strength S`. S comes from `$ARGUMENTS`: if a number from 0 to 100 is given, divide it by 100. Otherwise use 1.0.

This writes the creative look and solved sliders into Lightroom. It takes a "Before Match Look" snapshot on every target first. It prints a JSON summary that includes the `run` folder, both contact sheets, and the following for each photo:
- `start_error`: how far off the photo was with the look only pasted
- `final_error`: how far off it is after matching (under 2 is a good match)
- `flags`

## 3. Review with your own eyes
Read both images with the Read tool:
- `contact_sheet_before.jpg` shows the look pasted, with no correction.
- `contact_sheet.jpg` shows the final result. The reference is the first tile.

For each photo, compare it against the reference. Look at:
- **overall warmth and tint**
- **skin tones** (the most important, if people are present)
- **brightness of the main subject**
- **contrast and depth** (how deep the shadows are, how bright the highlights are)

Ignore differences that come from the content itself, such as a different background or a bright sky in one frame. The goal is the same look, not identical pixels.

For any photo that clearly doesn't match, nudge it:
```
PY -m engine.workflow nudge --run RUN --photo FILE_NAME Temperature=+150 Tint=-3 Exposure2012=+0.15
```
Rules for nudging:
- **Values are relative.** For raw files, Temperature is in Kelvin (warmer = +). For JPEGs it is an offset from −100 to 100.
- **Keep nudges small:**
  - Temperature: at most ±400 K on raw, or ±10 on JPEG
  - Tint: at most ±8
  - Exposure2012: at most ±0.5
  - Shadows2012, Highlights2012, Whites2012, Blacks2012: at most ±20
- **Re-read `contact_sheet.jpg` after each round.** Do at most 2 rounds per photo.
- If a photo still looks wrong after that, leave it and report it.
- Photos whose flags say the lighting is mixed (`not_converged`, `low_neutral_confidence`) or blown out (`mostly_clipped`) are often better left for the user than forced.

Never change photos outside this run. Never change anything except the corrective sliders above. The creative look always stays exactly the reference's.

## 4. Report
Keep the report short:
- **A table:** one row per photo with the file name, error before → after, any nudge you made and why (for example "skin looked green, Tint +4"), and flags.
- **Flagged photos:** which photos have the yellow label, and why each needs the user's eye.
- **How to undo:** the "Before Match Look" snapshot on each photo (Develop ▸ Snapshots), or Edit ▸ Undo. If the summary has a snapshot `warning`, say that undo is through the History panel instead.
- **Where the files are:** the path of the final contact sheet.
