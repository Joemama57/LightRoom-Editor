# Testing Match Look on your Mac

Everything here has been tested against a simulated Lightroom: 94 automated tests, including the plugin's Lua running under Lua 5.1. The steps below are the first run against **real** Lightroom Classic. Work through them in order. If a step fails, stop and paste the error into Claude Code; most problems will be in steps 2 and 4.

Use a **test catalog** or a copy of a few photos for the first session. Everything can be undone, but there's no reason to risk real work.

## 1. Install (5 min)
- [ ] Clone the repo and check out branch `claude/lightroom-color-grading-agent-fhkwk5`.
- [ ] Run `./setup.sh`. It should end with "Engine installed in .venv".
- [ ] Run `.venv/bin/python -m pytest -q`. This needs `.venv/bin/pip install -r requirements-dev.txt` first. Everything should pass on the Mac too.

## 2. Connect Lightroom (5 min)
- [ ] In Lightroom: **File ▸ Plug-in Manager ▸ Add**, choose the `MatchLook.lrplugin` folder, then **Done**.
- [ ] **Library ▸ Plug-in Extras ▸ Match Look Bridge Status** says "running". If it says "not running", click **Start**.
- [ ] In Terminal: `.venv/bin/python -m engine.bridge ping`. It should print the plugin name and your catalog path.
- [ ] Select 2–3 photos and run `.venv/bin/python -m engine.bridge selection`. Check that:
  - `active` is the photo you clicked last;
  - `fileFormat` is `RAW` for raw files;
  - `settings` has `Temperature`, `Exposure2012`, `Look`, …

## 3. Prepare a test set
- [ ] Pick one photo and grade it fully. Use a clear look: tone curve, colour grading, HSL.
- [ ] Pick 5–8 more photos from **different light**: shade, tungsten or indoor, golden hour, one underexposed, and ideally one with people. Leave their white balance on **As Shot**.

## 4. Calibrate (optional, ~3 min)
This teaches the engine how *your* Lightroom responds, before the first match.
- [ ] Select 3–5 photos in different light and run `.venv/bin/python -m engine.workflow calibrate`.
- [ ] Afterwards, every photo must look **exactly** as before. A "Before Match Look calibration" snapshot exists as a backup.
- [ ] `.venv/bin/python -m engine.learning show` now shows sensitivity samples.

## 5. First match through Claude Code
- [ ] Select the set, then click the graded photo so it's the active one.
- [ ] Start Claude Code in the repo folder and run `/match-look`. Add `skin` if people are in the set.
- [ ] Check in Lightroom:
  - [ ] The look (curve, colour grading, HSL) was copied exactly. Crop, lens corrections and masks were not.
  - [ ] The photos look consistent in **Survey view** (N).
  - [ ] Each target has a **"Before Match Look"** snapshot, and **Edit ▸ Undo** steps back through "Match Look" History entries.
  - [ ] Badly matched photos got the **yellow label**.
- [ ] Note how many passes it took (the `iterations` column) and the before → after errors.
- [ ] Open the run's `contact_sheet_before.jpg` and `contact_sheet.jpg` from `~/.matchlook/runs/<time>/`.

## 6. Self-learning
- [ ] Adjust one matched photo by hand in Lightroom, for example warm it by +200 K.
- [ ] Run `/match-look` again on a similar set. It should report that it learned from your edit.
- [ ] Repeat once more with the same kind of edit. On the next run, that kind of photo (same camera and light) gets a `learned_adjustment`.
- [ ] Check that the number of passes goes down over runs (`iterations`).
- [ ] `.venv/bin/python -m engine.learning reset` clears everything if you want to start over.

## 7. Experimental: AI masks
- [ ] On a photo with a person in mixed light, ask Claude Code to fix just the subject. Or run:
  `.venv/bin/python -m engine.workflow nudge --run <run folder> --photo <file> --mask subject Temperature=-10`
- [ ] Lightroom switches to Develop and a mask named **"Match Look subject"** appears in the Masks panel with the change.
- [ ] Your selection is restored afterwards.
- [ ] If it fails, note your Lightroom version and the error message. AI masking from plug-ins varies by version.

## What to send back
For any failure, send:
- the command
- its full output
- your Lightroom Classic version (Help ▸ System Info)
- `~/.matchlook/runs/<time>/report.json`, if one was written

For a successful run, the before/after errors, iteration counts and contact sheets are enough to tune the defaults.
