# Failures

Process mistakes that damaged results or the learning store. Read before every
run.

## 2026-10-03 — After a catalog change the previous run cannot be read, and stays unlearned
Source: run 20261003-173030 (catalog `27 May 2025.lrcat`) following run 20261003-165410 (iPhone set, another catalog, photo ids 17321…); log line "Couldn't read last run's photos to learn from them: get_settings failed in Lightroom: ?:0: attempt to index a nil value"
Finding: The engine asked the open catalog for the previous run's photo ids,
the plugin raised a Lua error, and the learning step returned without learning
or marking the run. Nothing wrong was learned (`corrections_learned` stayed 0,
`preferences` empty). Run 20261003-165410 has no `learned` entry, so it will be
tried again whenever a later run follows it directly.
Takeaway: After a catalog switch, expect this line and report it as "previous
run not learned from", not as a fault of the match. If the user wants that run
learned, they need to reopen its catalog and run `PY -m engine.workflow learn`.

## 2026-10-03 — A reset between runs is learned as the user's taste
Source: run 20261003-124236, `IMG_2001.DNG` (matched Shadows +50, Highlights −50, Whites −32.1, Blacks −50); `PY -m engine.learning show` → `raw|iPhone 16 Pro Max|cool light`; session 7324522b ("the engine also learned from my restore as if it were your edits")
Finding: At the start of each run the engine compares the previous run's
photos, as they are in Lightroom now, with what the match chose, and records
any difference as a preference. It cannot tell an edit from a reset, and only
rejects differences above 60 (Temperature, tone), 25 (Tint) or 1.5 EV. The
cool-light preference has 2 corrections, and its tone-slider averages are
exactly 0.3 × the change `IMG_2001.DNG` makes going from its matched values
back to zero (the weight the engine gives a second correction). That is a
reset, not an edit. That run wrote no look, so the guard added later the same
day (next entry) would not have caught it. The preference was removed when the
user reset the learning store on 2026-10-03.
Takeaway: If the user reset, restored a snapshot or synced settings since the
last run and the reference has no creative look, run with `--no-learning`.
Never restore photos yourself and then run with learning on.

## 2026-10-03 — An undone match was learned as 14 corrections
Source: run 20261003-141934 (log line "Learned from 14 photo(s) adjusted after the last run (20261003-140801)"); `engine.bridge selection` at 14:18; reports of runs 20261003-133626 and 20261003-140801; `PY -m engine.learning show` before and after (corrections 20 → 34)
Finding: At 14:18 every photo held the output of the 13:39 run (133626), not
the 14:10 run (140801): for example `IMG_1966.JPG` was at Temperature −23,
Tint −22, Exposure +0.41 with the 13:39 per-photo look on it. The 14:10 match
had been undone (Edit ▸ Undo or the "Before Match Look" snapshot), and the
engine recorded the difference on all 14 photos as the user's taste. The
resulting JPEG and raw-daylight preferences were applied to every photo of run
141934. The user confirmed they made no deliberate reset.
What changed since: `engine/workflow.py` now skips a photo when the look the
run wrote is no longer on it (`look_was_removed`; replayed on this incident it
skips 14 of 14) and logs "Not learning from N photo(s)". The user reset the
learning store on 2026-10-03, so the bad preferences are gone, along with all
slider-response samples. The photos of run 141934 still carry the offsets that
were applied to them.
Takeaway: Read the "Learned from N photo(s)" and "Not learning from N photo(s)"
lines of every run and report them. If N learned is most of the set and the
user did not say they edited the photos, report it with the before and after
of `PY -m engine.learning show` and ask.

## 2026-10-03 — Learning also reads photos that are no longer selected
Source: run 20261003-124236 (24 photos) followed by run 20261003-130510 (14 photos); `engine/workflow.py` `learn_from_run`
Finding: The learning step reads every photo of the previous run from
Lightroom, selected or not. The interior DNGs were dropped from the selection
after run 124236, and the cool-light preference appeared at the next run.
Takeaway: When the selection shrinks between runs, treat any new preference in
a light bucket that is not in the current selection as suspect and report it.

## 2026-10-03 — The agent changed engine code in the middle of a match
Source: session 7324522b; `git diff` on `engine/bridge.py` and `engine/settings.py` (45 lines, uncommitted since)
Finding: After run 20261003-122807 failed, the agent patched the bridge during
the `/match-look` task and re-ran. The patch was never committed or reported
as a code change, so later sessions found unexplained modifications. What it
fixes is in white-balance.md.
Takeaway: During a grading task, do not edit `engine/`. Stop, report the fault
with evidence and let the user decide.

## 2026-10-03 — Three runs left no report
Source: run folders 20261003-122442, 20261003-123718 and 20261003-133521 (renders present, no `report.json`); sessions 7324522b and cb3a8acb (output redirect to another session's scratchpad failed)
Finding: Three run folders hold renders but no report. A run that stops
part-way has already written settings to Lightroom, and the next run cannot
learn from it or account for it.
Takeaway: Redirect match output only to this session's own scratchpad. After
any run that exits without a summary, check the run folder for `report.json`
and tell the user that photos may have been left half-matched.
