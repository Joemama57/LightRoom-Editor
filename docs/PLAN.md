# Plan: Lightroom Classic "Match Look" agent

## Context
You grade one photo by hand and want that same color and depth carried across every other selected photo, even though those photos were shot in different light (different white balance, exposure, contrast). Copy/paste of develop settings fails here: the same settings on a warmer or darker photo give a different result.

Setup: Lightroom Classic on macOS. Results are written back as **normal, editable develop settings**.

**Claude Code is the agent.** You run `/match-look` in Claude Code on your Mac. Claude Code drives Lightroom through a small bridge plugin, runs the matching engine, then looks at the results itself and fixes anything that still looks off. No API key is needed.

## Core idea: copy the look, solve the light
Split the reference's settings into two groups:

| Group | Sliders | Handling |
|---|---|---|
| **Corrective** (depends on the scene's light) | Temp, Tint, Exposure, Highlights, Shadows, Whites, Blacks | **Solved per photo** |
| **Creative** (the "look") | Profile, Tone Curve, HSL, Color Grading, Calibration, Contrast, Clarity/Texture/Dehaze, Vibrance/Saturation, Vignette, Grain, … | **Copied exactly from the reference** |
| **Per-photo** (never copied) | Crop, orientation, lens corrections, transforms, spot removal, masks / local adjustments | Left alone |

Every target gets the reference's creative look. Then its corrective sliders are tuned until its **final render** matches the reference's final render. "Matches" means the same neutral color balance (a\*/b\* in Lab) and the same L\* percentiles (p1/p25/p50/p75/p99).

Lightroom's slider math is not public, so the engine runs a **closed loop**: set sliders, render a small preview, measure it, adjust, and repeat. It learns how each slider responds as it goes (Broyden updates), and usually needs 2 to 4 passes.

## Architecture
```
 you ──/match-look──▶ Claude Code (the agent)
                        │  python3 -m engine.workflow …     (deterministic matching loop)
                        │  reads contact sheet image        (visual review, judgment)
                        │  python3 -m engine.workflow nudge (small fixes)
                        ▼
                 engine/bridge.py ──JSON files in ~/.matchlook/bridge──▶ MatchLook.lrplugin (Lua, inside Lightroom)
                                                                          selection · apply · render · snapshot · label
```

### 1. Lightroom bridge plugin: `MatchLook.lrplugin/`
- Starts with Lightroom (`LrForceInitPlugin`). A background task watches `~/.matchlook/bridge/inbox` for request files and writes replies to `outbox`.
- Supported commands:
  - `ping`
  - `get_selection`: returns the selected photos, which one is active, file format, camera and develop settings
  - `apply_settings`: applies the given settings with `applyDevelopSettings` inside `withWriteAccessDo`
  - `render`: exports a small sRGB JPEG with `LrExportSession`
  - `snapshot`: creates a develop snapshot
  - `set_label`: sets the color label
- The plugin only does what it is told. All the logic lives in Python and in Claude Code.
- Menu item **Library ▸ Plug-in Extras ▸ Match Look Bridge Status** shows whether the bridge is running.

### 2. Python engine: `engine/` (done in Stage 1)
- `measure.py`, `solver.py` and `colorspace.py`: measuring a render and running the closed-loop solver.
- `bridge.py`: file-based client for the plugin, plus a command line (`ping`, `selection`).
- `settings.py`: splits the reference's settings into creative, corrective and per-photo groups.
- `workflow.py`: the whole job:
  1. Get the selection. The reference is the active photo.
  2. Create a "Before Match Look" snapshot on each target.
  3. Apply the creative look and the starting sliders.
  4. Loop: render all targets, solve, apply.
  5. Apply the best sliders, scaled by `--strength`.
  6. Give photos that didn't match well the yellow label.
  7. Write `report.json` and `contact_sheet.jpg`.
- `workflow.py nudge`: applies relative slider changes to a photo, re-renders it and rebuilds the contact sheet. Claude Code uses it during review.
- `contact_sheet.py`: builds a labelled grid with the reference first.

### 3. `/match-look` command: `.claude/skills/match-look/SKILL.md`
1. Check that the bridge is running. If it isn't, tell the user how to start it.
2. Run the workflow and read `report.json`.
3. Open the contact sheet image and compare each photo to the reference: overall warmth, skin tones, brightness and contrast.
4. For photos that still look off, apply small, bounded nudges (at most 2 rounds) and look again.
5. Report what changed for each photo and which ones were flagged, and remind the user that they can undo with the "Before Match Look" snapshot.

## Build order
1. ✅ Engine (`measure`, `solver`, `match` command line) with tests against a simulated renderer.
2. Bridge plugin (Lua), the bridge client, the settings split, the workflow, the contact sheet, the `/match-look` skill, and `setup.sh`. Tests use a fake Lightroom built on the simulator.
3. Tune against real Lightroom renders on your Mac, including the prior slider sensitivities and the tolerance.
4. Optional: a skin-tone guard (face detection) and a "keep each photo's own exposure intent" mode.

## Known limits
- Mixed light in one frame, such as window light plus tungsten, can't be fully fixed with whole-photo sliders. These photos are flagged for manual touch-up.
- Local adjustments and masks on the reference are not copied.
- Claude Code has to run on the same Mac as Lightroom, because the bridge is a local folder.

## Verification
- `python3 -m pytest -q`: covers the colour maths against published reference values, the solver converging under tungsten, shade and under/over exposure, the command-line job loop, and the full workflow against the fake Lightroom.
- **On your Mac**:
  - `./setup.sh`, then add the plugin in **File ▸ Plug-in Manager** and run `python3 -m engine.bridge ping`.
  - Select 6 to 10 photos with the graded one active, then run `/match-look`.
  - Check the photos in Survey view and that the "Before Match Look" snapshot exists.
  - Check that the flagged photos got the yellow label.
