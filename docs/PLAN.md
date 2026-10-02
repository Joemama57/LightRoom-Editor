# Plan: Lightroom Classic "Match Look" Agent

## Context
You grade one photo by hand and want that same color and depth carried across every other selected photo, even though those photos were shot in different light (different white balance, exposure, contrast). Copy/paste of develop settings fails here: the same settings on a warmer or darker photo give a different result. The agent has to **correct each photo to a common neutral starting point, then apply the reference's creative look on top**, so every photo ends up looking the same.

Setup: Lightroom Classic on macOS. Results are written back as **normal, editable develop settings** so you can still adjust any photo afterwards. The repo is empty, so everything below is new.

## Core idea: correct first, then apply the look
Split the reference's settings into two groups:

| Group | Sliders | How they are handled |
|---|---|---|
| **Corrective** (depends on the scene's light) | White balance (Temp/Tint), Exposure, Highlights, Shadows, Whites, Blacks | **Calculated separately for each photo** |
| **Creative** (the "look") | Profile, Tone Curve, HSL, Color Grading wheels, Calibration, Contrast, Clarity/Texture/Dehaze, Vibrance/Saturation, Vignette, Grain | **Copied exactly from the reference** |

For each target photo, the corrective sliders are tuned until the target's *neutral base render* (look turned off) matches the reference's neutral base render. In practice that means matching neutral or gray balance in Lab, plus luminance percentiles (p1/p5/p50/p95/p99). Once the inputs match, the same look produces the same output.

Lightroom's slider math is proprietary and nonlinear, so the agent does not try to predict it. It uses a **closed loop** instead: set candidate sliders, render a small preview, measure it, adjust, and repeat. This usually takes 2 to 4 passes per photo.

## Architecture
```
Lightroom Classic plugin (Lua)  ⇄  JSON + small JPEG previews in a temp folder  ⇄  Python engine
                                                                                  └─ optional Claude vision reviewer
```

### 1. Lightroom plugin: `MatchLook.lrplugin/`
- `Info.lua`: adds the menu item **Library ▸ Plug-in Extras ▸ Match Look to Reference**.
- `MatchLook.lua`:
  - The **reference is the active (most-selected) photo**. The targets are the other selected photos.
  - Reads the reference's settings with `photo:getDevelopSettings()` and splits them into the corrective and creative groups.
  - Renders previews around 1024px with `LrExportSession` into a temp directory. Each preview is rendered with the creative group neutralized, giving the "base" render.
  - Runs the engine with `LrTasks.execute("python3 engine/match.py --job job.json")`. Each loop iteration exchanges JSON (the proposed corrective sliders) and the plugin re-renders.
  - Writes the final result as corrective plus creative settings with `photo:applyDevelopSettings()` inside `catalog:withWriteAccessDo("Match Look", …)`. That is one undo step, and the plugin also creates a **"Before Match Look" snapshot** on each photo.
  - Shows a progress bar through `LrProgressScope` and supports cancelling.
- `Dialog.lua`: options for strength (0 to 100%), "keep each photo's own exposure intent", "skin-tone protect", and "AI review".

### 2. Python engine: `engine/`
- `match.py`: command-line entry point that runs the job state machine (measure, propose, converge).
- `measure.py`: estimates neutrals with a gray-world + white-patch hybrid in Lab, measures luminance percentiles and the mid-tone chroma histogram, and masks clipped pixels.
- `solver.py`: maps each measured difference to a slider change. Temp/Tint are fixed by matching the a\*/b\* neutral axis. Exposure is fixed by matching median L. Highlights/Shadows/Whites/Blacks are fixed by matching the percentiles. It uses damped Newton steps with numeric slider sensitivities learned on the first pass, and stops when ΔE00 < 2 or after 4 iterations.
- `skin.py` (optional): uses MediaPipe face detection to measure skin hue and chroma, and nudges Tint/Temp so skin matches the reference. Skin is the first thing viewers notice when a set doesn't match.
- `guards.py`: warns about mixed camera models (profile and calibration differ), JPEG versus RAW (smaller WB range), and outlier frames such as mixed lighting or extreme scenes. Outliers are flagged instead of forced.
- Dependencies: `numpy`, `opencv-python`, `colour-science`, `mediapipe` (optional). There is also a `setup.sh` that creates a venv the plugin points to.

### 3. Optional "agent" layer: Claude vision reviewer (`engine/review.py`)
- After matching, it sends the reference and target renders as a contact sheet to Claude (model `claude-opus-5-5`) with a prompt to rank how consistent each photo is and suggest small slider nudges as JSON.
- Its nudges are applied only if they lower ΔE or you approve them. Photos it can't fix are labeled with **"Needs review"**, a yellow color label.
- This is turned off unless an `ANTHROPIC_API_KEY` is set.

## Repo layout
```
MatchLook.lrplugin/  Info.lua, MatchLook.lua, Dialog.lua, Json.lua
engine/              match.py, measure.py, solver.py, skin.py, guards.py, review.py
tests/               test_measure.py, test_solver.py, fixtures/
setup.sh, README.md (install: File ▸ Plug-in Manager ▸ Add)
```

## Build order
1. Write the engine for the measuring and solving steps, with tests on synthetic images.
2. Build a minimal plugin that copies only the creative group and runs one pass of WB and exposure correction.
3. Add the closed loop with highlights, shadows, whites and blacks, plus the snapshot and undo.
4. Add the dialog options, the skin-tone guard and outlier flagging.
5. Add the optional Claude reviewer.

## Known limits (stated up front)
- **Mixed light in one frame**, such as window light plus tungsten, can't be fully fixed with global sliders. The SDK can't create AI masks, so these photos get flagged for manual touch-up.
- Local adjustments and masks on the reference are **not** copied, because they are specific to that photo's composition.

## Verification
- **Unit tests (`pytest`)**: take a graded image, apply known WB, exposure and contrast shifts synthetically, and check that the solver brings it back to ΔE00 < 2.
- **Engine on real files**: run `match.py` on exported JPEG sets and print a before/after ΔE report for each photo.
- **In Lightroom**: on a test catalog (daylight, shade, golden hour, indoor) select 6 to 10 photos with the graded one active, then run the menu item. Check that:
  - the settings are editable and the snapshot exists
  - Edit ▸ Undo reverts everything
  - the photos look consistent in Survey view
  - the outlier photos got the yellow label
