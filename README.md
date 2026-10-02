# Match Look for Lightroom Classic

Grade one photo, select the rest, run `/match-look` in Claude Code. Every selected photo gets the same look, and each photo's white balance, exposure and tone are corrected for its own lighting, so shots from shade, tungsten or an underexposed frame end up looking like the one you graded.

**Claude Code is the agent.** It drives Lightroom through a small plugin, runs the matching engine, then looks at the results and fixes anything that still looks off. No API key is needed.

## How it works
- **The look is copied exactly** from the graded (active) photo. That includes the profile, tone curve, HSL, color grading, calibration, contrast, presence, vignette and grain.
- **The light is solved per photo:** Temp, Tint, Exposure, Highlights, Shadows, Whites and Blacks. The engine renders a small preview, measures its neutral color balance and tone distribution, adjusts the sliders, and repeats until it matches the reference. This usually takes 2 to 4 passes.
- **Some settings are never copied:** crop, lens corrections, transforms, spot removal, masks, sharpening and noise reduction.
- **Undo is easy.** Each photo gets a **"Before Match Look"** snapshot before anything changes, and every edit stays a normal, editable Lightroom setting.
- **Photos that couldn't be matched well** get a yellow label, for example mixed lighting or blown-out frames.

See [docs/PLAN.md](docs/PLAN.md) for the design, and [docs/EXISTING_PROJECTS.md](docs/EXISTING_PROJECTS.md) for how this compares with other Lightroom MCP projects and what was borrowed from them. In particular, the findings from real Lightroom 15.5.1 tests and the develop-key list come from [LrC_Autonomous_Gateway](https://github.com/jimjohnbeebe-jpg/LrC_Autonomous_Gateway) (MIT).

## Setup (macOS)
1. `./setup.sh` creates `.venv` and installs `numpy` and `Pillow`.
2. In Lightroom Classic, go to **File ▸ Plug-in Manager ▸ Add** and choose `MatchLook.lrplugin` from this folder.
3. Check it: **Library ▸ Plug-in Extras ▸ Match Look Bridge Status**, or run `.venv/bin/python -m engine.bridge ping`.

## Use
1. In Lightroom, select the photos and click your graded photo so it's the active (most-selected) one.
2. Start Claude Code in this folder and run `/match-look`. You can pass a strength, for example `/match-look 70`.
3. Claude Code reports the before/after match error for each photo, the nudges it made, and which photos need your eye.

You can also run the steps by hand:
```
.venv/bin/python -m engine.workflow match --strength 1.0
.venv/bin/python -m engine.workflow nudge --run ~/.matchlook/runs/<time> --photo DSC0042 Exposure2012=+0.2
```
Each run writes `report.json`, `contact_sheet_before.jpg` and `contact_sheet.jpg` to `~/.matchlook/runs/<time>/`.

## Layout
```
MatchLook.lrplugin/       Lightroom plugin: file-based bridge (~/.matchlook/bridge)
engine/                   measuring, solver, settings split, workflow, contact sheet
.claude/skills/match-look the /match-look command for Claude Code
tests/                    pytest suite (a simulated Lightroom, plus the plugin's Lua run under Lua 5.1)
```

## Development
```
pip install -r requirements-dev.txt
python3 -m pytest -q
```

## Limits
- Mixed light in one frame, such as window light plus tungsten, can't be fully fixed with whole-photo sliders, so these photos are flagged. AI masks could fix this later.
- Masks and local adjustments on the reference are not copied.
- Claude Code has to run on the same Mac as Lightroom.
- The solver's starting slider sensitivities are estimates. It learns the real ones during each run, but tuning them against real Lightroom renders is the next step.
