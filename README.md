# Match Look for Lightroom Classic

Grade one photo, select the rest, and run `/match-look` in Claude Code. Every selected photo gets the same look, with each photo's white balance, exposure and tone corrected for its own light. Shots from shade, tungsten or an underexposed frame end up looking like the one you graded. It **learns from every run**: how your Lightroom and cameras respond, and how you like your photos.

**Claude Code is the agent.** It drives Lightroom through a small plugin, runs the matching engine, looks at the results itself (skin tones included), fixes what still looks off, and reports back. No API key is needed.

## How it works
- **The look is copied** from the graded (active) photo. That includes the profile, tone curve, HSL, color grading, calibration, contrast, presence, vignette and grain.
- **The light is solved per photo:** Temp, Tint, Exposure, Highlights, Shadows, Whites and Blacks.
  - The engine renders a small preview, measures its neutral color balance and tone, adjusts the sliders, and repeats until it matches the reference.
  - It starts from each raw photo's own camera white balance and limits how far white balance can move.
  - Frames dominated by one colour, like foliage or a red wall, aren't mistaken for a colour cast.
- **A reference exported from another editor** (for example `IMG_1964 copy.jpg`) has its edit baked into the pixels, so there are no settings to copy. Select its unedited original too (`IMG_1964.JPG`; it is found by name, or use `--original`). Match Look fits Lightroom settings that turn the original into the copy: contrast, tone curve, vibrance and saturation, HSL per colour, split toning. It then applies that **same** grade to every photo and solves only each photo's light.
  - The copy can be a crop: it is lined up with the original automatically, and the two are compared pixel by pixel.
  - The fit measures how your Lightroom responds to each slider (about 30–40 renders of one photo). The result is kept in `~/.matchlook/grades/` and reused next time; `--refit` learns it again.
  - Local edits in the copy (masks, brushes) can't be reproduced by global sliders. The report's `grade_fit.delta_e_after` shows how close the fit got.
  - With a fitted grade, each photo only gets its white balance and exposure solved.
  - Without the original, see the next point.
  - `--look-strength 0.5` applies half the grade. `--no-look` skips it.
- **A reference from outside the catalog** (someone else's photo, or any picture with no Lightroom edits and no original): import it, click it as the reference and run as usual. Match Look reads one grade from its pixels (the ends of its tone curve, contrast, saturation, the tint of its shadows and highlights, the colours it shares with your photos) and puts that same grade on every photo, after matching each photo's white balance and exposure. It works best when the reference shows a similar kind of scene. `--look-strength 0.5` applies half of it; `--look-per-photo` uses the older small per-photo colour match instead.
- **Skin tones (`--skin`):** a skin model built from 14,532 measured skin colours across 8 populations keeps skin consistent and describes each photo's skin in words. See [docs/SKIN_TONES.md](docs/SKIN_TONES.md).
- **Keep each photo's brightness (`--color-only`):** matches colour only, for deliberately dark or bright frames.
- **Self-learning:**
  - Every run trains a per-camera model of how the sliders respond, so later runs need fewer passes.
  - Edits made after a match, by you in Lightroom or by Claude during review, are learned per camera and kind of light, then applied automatically once they repeat.
  - Everything is stored in `~/.matchlook/learning.json`. See it with `python3 -m engine.learning show`.
  - **Guards against learning the wrong lesson:**
    - Photos you put back to how they were (Edit ▸ Undo, or the "Before Match Look" snapshot) aren't learned from. If most of a run was undone, nothing from it is learned.
    - Runs that went badly (most photos flagged, or a poor grade fit) aren't learned from: edits to them correct the engine, they aren't your taste.
    - After a run you don't want taught, run `python3 -m engine.workflow learn --skip`.
    - Every run backs up `learning.json` to its run folder first. `python3 -m engine.learning restore --run ~/.matchlook/runs/<time>` undoes everything learned since that run started (the current file is kept as `learning.json.bak`).
    - `python3 -m engine.learning reset --preferences` forgets learned taste but keeps how your Lightroom's sliders respond. Plain `reset` forgets everything.
- **Never copied:** crop, lens corrections, transforms, spot removal, masks, sharpening and noise reduction.
- **Undo is easy:** every photo gets a **"Before Match Look"** snapshot, and everything stays a normal, editable Lightroom setting.
- **Flagged photos:** photos that couldn't be matched well get a yellow label.
- **Experimental: AI masks.** During review, Claude can fix just the subject, sky or background, for example in mixed light.

More docs:
- [docs/PLAN.md](docs/PLAN.md): the design.
- [docs/EXISTING_PROJECTS.md](docs/EXISTING_PROJECTS.md): how this compares with other Lightroom MCP projects and what was borrowed from them.
- [docs/MAC_TESTING.md](docs/MAC_TESTING.md): a checklist for the first real test.

## Setup (macOS)
1. `./setup.sh` creates `.venv` and installs `numpy` and `Pillow`.
2. In Lightroom Classic, go to **File ▸ Plug-in Manager ▸ Add** and choose `MatchLook.lrplugin` from this folder.
3. Check it: **Library ▸ Plug-in Extras ▸ Match Look Bridge Status**, or run `.venv/bin/python -m engine.bridge ping`.
4. Optional: select 3–5 photos in different light and run `.venv/bin/python -m engine.workflow calibrate`. This teaches it your Lightroom's slider response before the first match. Photos are restored afterwards.

## Use
1. In Lightroom, select the photos and click your graded photo so it's the active (most-selected) one.
2. Start Claude Code in this folder and run `/match-look`. Options: `/match-look 70` (strength), `/match-look skin`, `/match-look color-only`.
3. Claude Code reports, for each photo:
   - the match error before → after
   - any learned adjustment it applied
   - skin consistency
   - nudges it made
   - which photos need your eye

By hand:
```
.venv/bin/python -m engine.workflow match [--strength 1.0] [--skin] [--color-only] [--no-look] [--look-strength 1.0] [--original NAME] [--refit] [--look-per-photo] [--no-learning] [--skin-hue] [--hold-shoot-wb] [--even-shoot-tone]
.venv/bin/python -m engine.workflow match --skin [--face-skin] [--skin-error] [--skin-wb]   # opt-in skin options; --face-skin needs requirements-faces.txt
.venv/bin/python -m engine.workflow nudge --run ~/.matchlook/runs/<time> --photo DSC0042 Exposure2012=+0.2 SaturationAdjustmentBlue=+8
.venv/bin/python -m engine.workflow nudge --run ... --photo DSC0042 --mask subject Temperature=-10
.venv/bin/python -m engine.workflow learn [--skip] # learn now from edits made since the last run (or skip it)
.venv/bin/python -m engine.workflow calibrate
.venv/bin/python -m engine.learning show | reset [--preferences] | restore --run DIR
.venv/bin/python -m engine.replay check | rerun | answers RUN   # replay a saved run without Lightroom (engine work)
.venv/bin/python -m engine.replay bench RUN...                 # score runs against the photos you fixed by hand
```
Each run writes `report.json`, `contact_sheet_before.jpg` and `contact_sheet.jpg` to `~/.matchlook/runs/<time>/`.

## Layout
```
MatchLook.lrplugin/        Lightroom plugin: file-based bridge (~/.matchlook/bridge)
engine/                    measure, solver (light), look (colour and contrast), align (crop finder), skin model, learning, settings split, workflow, contact sheet, replay (saved runs as a benchmark)
data/skin_sources.json     measured skin colour data (with sources and licenses)
tools/build_skin_model.py  builds engine/data/skin_model.json and docs/SKIN_TONES.md from it
.claude/skills/match-look  the /match-look command for Claude Code
engine/faces.py, subject.py  opt-in face-anchored skin, skin-weighted error and skin white balance
tests/                     pytest suite (a simulated Lightroom, plus the plugin's Lua run under Lua 5.1)
```

## Development
```
pip install -r requirements-dev.txt
python3 -m pytest -q
python3 -m tools.build_skin_model    # after editing data/skin_sources.json
```

## Limits
- Mixed light within one frame (window plus tungsten) can't be fully fixed with whole-photo sliders. These photos are flagged, and the experimental AI-mask nudges can fix the subject separately.
- Frames whose *content* differs a lot from the reference (half foliage, a dark interior) get the right white balance but are flagged rather than forced to match in tone.
- Mixed sets (daylight exteriors plus LED-lit interiors) match best as separate batches, each with its own reference. Dark, coloured-light frames are detected (`different_scene`), matched for colour only and labelled. Exposure and tone never move more than ±2 EV / ±40 from where they start; JPEG white balance never more than ±30 Temperature / ±20 Tint.
- Masks and local adjustments on the reference are not copied.
- Claude Code has to run on the same Mac as Lightroom.
- Everything is tested against a simulated Lightroom. The first real-Lightroom run is [docs/MAC_TESTING.md](docs/MAC_TESTING.md).

## Credits
- Real-Lightroom findings and the develop-settings key list: [LrC_Autonomous_Gateway](https://github.com/jimjohnbeebe-jpg/LrC_Autonomous_Gateway) (MIT).
- AI-mask approach: [par4987/lightroom-mcp](https://github.com/par4987/lightroom-mcp) (MIT).
- Skin data:
  - Lu et al. 2026, *Skin Research and Technology* (CC BY 4.0)
  - Wang, Xiao, Wuerger, Cheung & Luo 2015, *Color and Imaging Conference*
  - the Monk Skin Tone Scale (CC BY 4.0)
  - Chardon et al. 1991 (ITA)
