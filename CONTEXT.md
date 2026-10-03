# Context for a new chat

Paste or point a new Claude chat at this file to continue the project. It covers what the project is, how the user works, the rules to follow, what was built and why, and where things stand. Last updated 2026-10-03.

## What this is
**Match Look** for Lightroom Classic on macOS: grade one photo, select the rest, and run `/match-look` in Claude Code. Every selected photo gets the same look, with its light corrected so the set looks consistent. It learns from every run. See `README.md` for the full user-facing description.

**Pieces:**
- `MatchLook.lrplugin/`: the Lightroom plugin, a file-based JSON bridge to Lightroom.
  - `Bridge.lua`: the commands (`get_selection`, `apply_settings`, `render`, `get_settings`, `snapshot`, `set_label`, `mask_adjust`).
  - `Json.lua`: the encoder and decoder.
- `engine/`: Python.
  - `workflow.py`: the orchestrator, plus the `match`, `nudge`, `learn` and `calibrate` CLI commands.
  - `solver.py`: the closed-loop slider solve, with white-balance and tone limits.
  - `look.py`: the creative look keys, `is_baked`, and the per-photo look.
  - `settings.py`: splits creative from corrective settings, gives the starting sliders, and reads the camera white balance.
  - `learning.py`: slider sensitivities and taste preferences.
  - `measure.py`: Lab metrics, including skin.
  - `skin.py`: the skin model and its descriptions.
  - `guards.py`: flags.
  - `bridge.py`: the Python side of the plugin bridge.
- `.claude/skills/match-look/SKILL.md`: the agent's procedure: run, review the contact sheets, nudge, report.
- `learnings/*.md`: notes the agent writes from real runs. Read them before each run.
- `tests/`: pytest. It uses a fake Lightroom (`tests/fake_lightroom.py`), a simulator (`tests/simulator.py`), and Lua tests via lupa.

**A run, in short:**
1. Copy the reference's creative sliders to all photos.
2. Each photo starts from its own camera white balance (raw on "As Shot") or from the reference's.
3. Solve Temp, Tint, Exposure and the tone sliders per photo by render → measure → adjust, within limits.
4. The agent reviews the contact sheets, makes at most 2 nudges per photo, and reports.
5. At the next run, edits made since are learned as preferences.

## How the user works
- **Mac setup:** the repo is at `~/LightRoom-Editor`; Python is `.venv/bin/python` (plain `python3` has no pytest there).
- **Runs** go in `~/.matchlook/runs/<time>/`: `report.json`, `contact_sheet.jpg`, `contact_sheet_before.jpg`, and `learning_before.json`.
- **Learning store:** `~/.matchlook/learning.json`. Read it with `python3 -m engine.learning show`.
- **After `git pull`:** reload the plugin in Lightroom (File ▸ Plug-in Manager ▸ Match Look ▸ Reload Plug-in).
- **Branch:** `claude/lightroom-color-grading-agent-fhkwk5`. The cloud session makes engine changes. The Mac agent runs matches and writes `learnings/`.
- **Feedback:** the user sends contact sheets (before and after), `report.json`, and the local agent's output, then says what looks wrong.

## The user's rules (follow these)
- **Git:** never commit or push without the user typing **"push"**. Decline stop-hook requests to commit.
- **Tests:** run `python3 -m pytest -q` before and after a change; all must pass. When done, show the full diff and the test results.
- **SKILL.md:** additive changes only. Never alter, reorder or remove an existing step.
- **learning.json** is engine-owned:
  - Never edit it by hand and never run `reset`.
  - Report stale or wrong values with evidence instead.
  - Normal commands (`match`, `nudge`, `learn`) may update it.
- **Learning from bad runs:** a run whose nudges or manual fixes corrected an *engine mistake* should be skipped: `python3 -m engine.workflow learn --run <run dir> --skip`. Otherwise those fixes are learned as the user's taste.
- **Writing for the user:** short and plain. They are a photographer, not a programmer.

## Engine rules added, and why (newest last)

**Earlier fixes:**
- **Exported reference:** a reference exported from another catalog carries no sliders, so the engine has to guess a grade from pixels, and the result was dull. Grade the reference *in the same catalog*.
  - `is_baked` (look.py) treats any real Lightroom edit as not baked: Clarity, Dehaze, Texture, colour grading, calibration, point colours, or tone curves.
- **macOS JSON:** Lua `%c` matches bytes 0x80–0x9F in a UTF-8 locale and corrupted non-ASCII text. `Json.lua` uses an explicit control-byte pattern.

**Same shoot** (same camera, same file type, `captureTime` within 3 h): `same_shoot`.
- Exposure stays within ±0.3 EV of the reference's, and the tone sliders are fixed. The camera already evened out the light.
- The photo is judged on colour only. Earlier, content-driven exposure matches made photos too dark.

**Raw next to a JPEG reference** (or the reverse), same shoot:
- Tone stays within ±15 and Tint within ±10. The two formats render differently.

**Same-shoot raw on camera white balance:**
- Held within ±20 mired / Tint ±10.
- With 3 or more of them, a photo whose move is more than 20 mired from the shoot's median move gets the median move: `wb_from_shoot`. A yellow saree or an orange wall fooled the solve once: DSC00232 went to 9580 K.

**Reference white-balance choice:**
- Lightroom forgets a raw's as-shot white balance once it's changed. The reference's camera reading is taken from the photo shot closest to it (same camera, within 10 min, still As Shot).
- If the reference was set more than 20 mired from that reading, the user chose it:
  - Photos from those minutes start from their own reading plus the same offset: `wb_offset_from_reference`.
  - Later photos may move far enough to reach the reference's value.
  - The report shows `reference_wb_offset`.
- Why: DSC01303 was graded at 2662 K while the camera said 5550 K. DSC01318, shot 28 s later, stayed orange.

**Learning after a catalog switch:**
- Photo ids are local to a catalog, so the plugin's `get_settings` looks only in the open catalog and returns file names.
- `learn_from_run` ignores photos whose file name doesn't match.
- If none are found, it logs "its photos aren't in the open catalog" and leaves the run learnable later from its own catalog.

**Skin exposure anchor** (`--skin`, not same shoot):
- When to apply:
  - Faces more than 3 L* off the reference's.
  - Skin covers at least 3% of the frame.
  - Skin chroma within 10 of the reference's.
- What it does: exposure is moved by the luminance ratio, at most ±0.5 EV. The photo is re-rendered and the change kept only if the faces got closer: `exposure_from_skin`, with `skin_exposure` in the report.
- Why: close portraits in bright clothes against bright backdrops were darkened about 1 EV to match a crowd scene's histogram.

## Where things stand
- **Latest pushed commit:** "Set a portrait's exposure from the faces…". 145 tests pass.
- **Batch 1** (wedding, Sony A7 IV, DSC00183 reference): good, except DSC00232. That one is fixed by `wb_from_shoot` and was meant to be skipped for learning.
- **Batch 2** (DSC01303 reference plus DSC01318, DSC02121 and DSC02201): colour is now good. The user said it was "losing brightness". The skin anchor was added for that.
  - **Next step:** the user re-runs batch 2 and sends the sheets. Expect DSC02121 about +0.34 EV brighter.
  - DSC02201's faces were within 3 L, so it is unchanged. If the user still finds it dark, consider lowering `SKIN_ANCHOR_DEADBAND`.
- **Open ideas, not done:**
  - A per-shoot raw-vs-JPEG tint offset: iPhone DNGs often ended at the Tint +10 cap next to a JPEG reference.
- **Uncommitted on the Mac:** the local agent's `learnings/` edits. The user commits them from the Mac when happy:
  ```
  git add learnings
  git commit -m "..."
  git push
  ```
